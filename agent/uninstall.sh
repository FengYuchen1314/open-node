#!/usr/bin/env bash
set -euo pipefail

readonly SAFE_PATH="/usr/sbin:/usr/bin:/sbin:/bin"
readonly BOOTSTRAP_BASE="/var/lib/open-node-agent-bootstrap"
PATH="$SAFE_PATH"
export PATH
umask 077

die() {
  printf 'Agent uninstall refused: %s\n' "$1" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: sudo bash agent/uninstall.sh [--root ABSOLUTE_PATH --unit SERVICE_NAME]

Without --root, the script discovers installer-owned Open Node Agent roots
directly below /opt and /var/lib. Multiple installations require an interactive
selection. The final [Y/n] prompt defaults to a full local purge; answer n to
remove only the Agent service and packages while retaining recovery data.
EOF
}

[[ "${EUID}" -eq 0 ]] || die "run this script as root"
[[ -t 0 && -t 1 && -t 2 ]] || die "an interactive TTY on stdin, stdout, and stderr is required"
[[ -x /usr/bin/python3 ]] || die "/usr/bin/python3 is required"
[[ -x /usr/bin/mktemp ]] || die "/usr/bin/mktemp is required"

requested_root=""
requested_unit=""
while (($#)); do
  case "$1" in
    --root)
      (($# >= 2)) || die "--root requires a value"
      requested_root="$2"
      shift 2
      ;;
    --unit)
      (($# >= 2)) || die "--unit requires a value"
      requested_unit="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument; run with --help"
      ;;
  esac
done
[[ -z "$requested_unit" || -n "$requested_root" ]] \
  || die "--unit may only be used together with --root"

policy_file="$(/usr/bin/mktemp --tmpdir open-node-agent-uninstall.XXXXXXXX.py)" \
  || die "could not create a private policy helper"

cleanup_policy() {
  if [[ -n "${policy_file:-}" && -e "$policy_file" && ! -L "$policy_file" ]]; then
    /usr/bin/python3 -I -c \
      'import os,sys; os.unlink(sys.argv[1])' "$policy_file" 2>/dev/null || true
  fi
}
trap cleanup_policy EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

cat >"$policy_file" <<'PY'
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
from pathlib import Path
from uuid import UUID

ROOT_UID = 0
BOOTSTRAP_BASE = Path("/var/lib/open-node-agent-bootstrap")
UNIT_PATTERN = re.compile(r"open-node-agent(?:-[a-z0-9][a-z0-9-]{0,15})?\.service")
ROOT_PATTERN = re.compile(r"/[a-zA-Z0-9_./-]+")
JOB_PATTERN = re.compile(r"([0-9a-f]{32})-([0-9a-f]{16})")
HEX_32 = re.compile(r"[0-9a-f]{32}")
HEX_64 = re.compile(r"[0-9a-f]{64}")
MAX_JSON = 1024 * 1024
MAX_HELPER = 64 * 1024 * 1024


class PolicyError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise PolicyError(message)


def read_regular(
    path: Path,
    *,
    limit: int,
    private: bool = False,
    root_owned: bool = True,
) -> bytes:
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise PolicyError(f"required file is missing: {path}") from None
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and (not root_owned or info.st_uid == ROOT_UID)
        and not (info.st_mode & 0o022)
        and (not private or stat.S_IMODE(info.st_mode) == 0o600),
        f"unsafe file ownership, mode, links, or type: {path}",
    )
    with path.open("rb") as source:
        data = source.read(limit + 1)
    require(len(data) <= limit, f"file exceeds its size boundary: {path}")
    return data


def parse_json(path: Path, *, private: bool = False) -> dict:
    try:
        value = json.loads(read_regular(path, limit=MAX_JSON, private=private))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise PolicyError(f"invalid JSON document: {path}") from None
    require(isinstance(value, dict), f"JSON document must be an object: {path}")
    return value


def validate_root_shape(root: Path) -> None:
    require(
        root.is_absolute()
        and len(root.parts) >= 3
        and ROOT_PATTERN.fullmatch(str(root)) is not None
        and ".." not in root.parts
        and root != Path("/opt/open-node")
        and any(
            root != base and root.is_relative_to(base)
            for base in map(Path, ("/opt", "/var/lib", "/tmp"))
        ),
        "installation root is outside the host deployment policy",
    )
    for component in (root, *root.parents):
        require(not component.is_symlink(), f"symlink path component refused: {component}")


def installation(root_text: str, expected_unit: str | None = None) -> dict:
    root = Path(root_text)
    validate_root_shape(root)
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        raise PolicyError("installation root does not exist") from None
    require(
        stat.S_ISDIR(root_info.st_mode)
        and root_info.st_uid == ROOT_UID
        and not (root_info.st_mode & 0o022),
        "installation root must be a root-owned, non-writable directory",
    )
    manifest_path = root / "installation.json"
    record = parse_json(manifest_path)
    unit = record.get("unit")
    require(
        record.get("schema") in {1, 2}
        and record.get("root") == str(root)
        and isinstance(unit, str)
        and UNIT_PATTERN.fullmatch(unit) is not None
        and record.get("user") == unit.removesuffix(".service")
        and isinstance(record.get("installation_id"), str)
        and HEX_32.fullmatch(record["installation_id"]) is not None
        and record.get("status")
        in {"preparing", "installed", "failed", "removing", "removed"},
        "installation manifest identity is invalid",
    )
    if expected_unit:
        require(unit == expected_unit, "--unit does not match the installation manifest")
    return {
        "root": str(root),
        "unit": unit,
        "status": record["status"],
        "installation_id": record["installation_id"],
        "manifest_sha256": hashlib.sha256(read_regular(manifest_path, limit=MAX_JSON)).hexdigest(),
    }


def discover(root_text: str | None, expected_unit: str | None) -> list[dict]:
    if root_text:
        return [installation(root_text, expected_unit)]
    results = []
    for base in map(Path, ("/opt", "/var/lib")):
        if not base.is_dir() or base.is_symlink():
            continue
        for candidate in sorted(base.iterdir(), key=lambda item: item.name):
            if candidate.name != "open-node-agent" and not candidate.name.startswith(
                "open-node-agent-"
            ):
                continue
            manifest = candidate / "installation.json"
            if manifest.exists() or manifest.is_symlink():
                results.append(installation(str(candidate)))
    identities = {(item["root"], item["unit"]) for item in results}
    require(len(identities) == len(results), "duplicate installation identity discovered")
    return results


def unit_suffix(unit: str) -> str | None:
    match = re.fullmatch(r"open-node-agent-([0-9a-f]{12})\.service", unit)
    return match.group(1) if match else None


def safe_job_base() -> bool:
    if not BOOTSTRAP_BASE.exists() and not BOOTSTRAP_BASE.is_symlink():
        return False
    try:
        info = BOOTSTRAP_BASE.lstat()
    except FileNotFoundError:
        return False
    require(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == ROOT_UID
        and stat.S_IMODE(info.st_mode) == 0o700,
        "bootstrap job base is not a root-owned mode-0700 directory",
    )
    return True


def request_for_job(job: Path, *, related_suffix: str | None) -> dict | None:
    name = JOB_PATTERN.fullmatch(job.name)
    if name is None:
        return None
    try:
        info = job.lstat()
        require(
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == ROOT_UID
            and stat.S_IMODE(info.st_mode) == 0o700,
            "bootstrap job directory must be root-owned mode 0700 without links",
        )
        request = parse_json(job / "request.json", private=True)
    except PolicyError:
        if related_suffix and job.name.startswith(related_suffix):
            raise PolicyError("a potentially related bootstrap job has unsafe metadata") from None
        return None
    required = {
        "schema_version",
        "server_id",
        "control_url",
        "ticket_sha256",
        "root",
        "unit",
        "ca_sha256",
        "claim_nonce",
    }
    require(set(request) == required, "bootstrap request fields changed")
    try:
        server_hex = UUID(request["server_id"]).hex
    except (ValueError, AttributeError, TypeError):
        raise PolicyError("bootstrap request server identity is invalid") from None
    require(
        request["schema_version"] == 1
        and server_hex == name.group(1)
        and isinstance(request["ticket_sha256"], str)
        and HEX_64.fullmatch(request["ticket_sha256"]) is not None
        and request["ticket_sha256"].startswith(name.group(2))
        and isinstance(request["root"], str)
        and isinstance(request["unit"], str),
        "bootstrap request identity is invalid",
    )
    return request


def matching_jobs(root: str, unit: str) -> list[tuple[Path, dict]]:
    if not safe_job_base():
        return []
    suffix = unit_suffix(unit)
    matches = []
    with os.scandir(BOOTSTRAP_BASE) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            job = BOOTSTRAP_BASE / entry.name
            request = request_for_job(job, related_suffix=suffix)
            if request is not None and request["root"] == root and request["unit"] == unit:
                matches.append((job, request))
    return matches


def verified_job_helper(job: Path, root: str, unit: str, installation_id: str) -> Path:
    success = parse_json(job / "success.json", private=True)
    require(
        success.get("schema_version") == 1
        and success.get("root") == root
        and success.get("unit") == unit
        and success.get("installation_id") == installation_id,
        "bootstrap success identity does not match the selected installation",
    )
    manifest = parse_json(job / "manifest.json", private=True)
    try:
        bootstrap = manifest["agent"]["bootstrap"]
        filename = bootstrap["filename"]
        expected_sha = bootstrap["sha256"]
    except (KeyError, TypeError):
        raise PolicyError("bootstrap manifest is incomplete") from None
    require(
        isinstance(filename, str)
        and re.fullmatch(r"open-node-agent-bootstrap-[a-zA-Z0-9_.+-]+\.tar\.gz", filename)
        and isinstance(expected_sha, str)
        and HEX_64.fullmatch(expected_sha) is not None,
        "bootstrap artifact identity is invalid",
    )
    archive = job / filename
    archive_data = read_regular(archive, limit=MAX_HELPER, private=True)
    require(hashlib.sha256(archive_data).hexdigest() == expected_sha, "bootstrap archive changed")
    helper = job / "bootstrap" / "service.py"
    helper_data = read_regular(helper, limit=MAX_HELPER, private=True)
    try:
        with tarfile.open(archive, mode="r:gz") as source:
            members = [member for member in source.getmembers() if member.name == "service.py"]
            require(len(members) == 1 and members[0].isreg(), "bootstrap service member is invalid")
            stream = source.extractfile(members[0])
            require(stream is not None, "bootstrap service member is missing")
            archived_helper = stream.read(MAX_HELPER + 1)
    except (tarfile.TarError, OSError, EOFError):
        raise PolicyError("bootstrap archive cannot be verified") from None
    require(
        len(archived_helper) <= MAX_HELPER and archived_helper == helper_data,
        "saved bootstrap service.py differs from the verified archive",
    )
    return helper


def inspect_jobs(root: str, unit: str, installation_id: str) -> None:
    for job, _request in matching_jobs(root, unit):
        print("JOB\t" + str(job))
        try:
            helper = verified_job_helper(job, root, unit, installation_id)
        except PolicyError:
            continue
        print("HELPER\t" + str(helper))


def checkout_helper(path_text: str) -> None:
    path = Path(path_text)
    data = read_regular(path, limit=MAX_HELPER, root_owned=False)
    require(data.startswith(b'"""Root-only systemd deployment CLI.'), "checkout helper identity is invalid")
    print(path)


def purge_jobs(root: str, unit: str) -> None:
    jobs = [job for job, _request in matching_jobs(root, unit)]
    require(
        getattr(shutil.rmtree, "avoids_symlink_attacks", False),
        "this Python runtime cannot safely remove private job trees",
    )
    for job in jobs:
        # Re-read the exact request immediately before deletion. rmtree's fd-based
        # implementation does not follow a swapped directory or child symlink.
        request = request_for_job(job, related_suffix=unit_suffix(unit))
        require(
            request is not None and request["root"] == root and request["unit"] == unit,
            "bootstrap job identity changed before purge",
        )
        shutil.rmtree(job)
    if jobs:
        descriptor = os.open(BOOTSTRAP_BASE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    print(len(jobs))


def main() -> None:
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    discover_parser = actions.add_parser("discover")
    discover_parser.add_argument("--root")
    discover_parser.add_argument("--unit")
    validate_parser = actions.add_parser("validate")
    validate_parser.add_argument("--root", required=True)
    validate_parser.add_argument("--unit", required=True)
    jobs_parser = actions.add_parser("jobs")
    jobs_parser.add_argument("--root", required=True)
    jobs_parser.add_argument("--unit", required=True)
    jobs_parser.add_argument("--installation-id", required=True)
    checkout_parser = actions.add_parser("checkout-helper")
    checkout_parser.add_argument("path")
    purge_parser = actions.add_parser("purge-jobs")
    purge_parser.add_argument("--root", required=True)
    purge_parser.add_argument("--unit", required=True)
    args = parser.parse_args()
    if args.action == "discover":
        for item in discover(args.root, args.unit):
            print(
                "\t".join(
                    item[key]
                    for key in (
                        "root",
                        "unit",
                        "status",
                        "installation_id",
                        "manifest_sha256",
                    )
                )
            )
    elif args.action == "validate":
        item = installation(args.root, args.unit)
        print(
            "\t".join(
                item[key]
                for key in (
                    "root", "unit", "status", "installation_id", "manifest_sha256"
                )
            )
        )
    elif args.action == "jobs":
        inspect_jobs(args.root, args.unit, args.installation_id)
    elif args.action == "checkout-helper":
        checkout_helper(args.path)
    else:
        purge_jobs(args.root, args.unit)


try:
    main()
except (PolicyError, OSError, ValueError, KeyError) as error:
    print("policy check failed: " + str(error), file=sys.stderr)
    raise SystemExit(1) from None
PY
chmod 0600 "$policy_file"

discover_arguments=(discover)
if [[ -n "$requested_root" ]]; then
  discover_arguments+=(--root "$requested_root")
fi
if [[ -n "$requested_unit" ]]; then
  discover_arguments+=(--unit "$requested_unit")
fi
installations_output="$(/usr/bin/python3 -I "$policy_file" "${discover_arguments[@]}")" \
  || die "installed Agent identity validation failed"
[[ -n "$installations_output" ]] \
  || die "no installer-owned Agent was found; specify its exact --root and --unit"
mapfile -t installations <<<"$installations_output"

selected_index=0
if ((${#installations[@]} > 1)); then
  printf 'Installed Open Node Agents:\n'
  for index in "${!installations[@]}"; do
    IFS=$'\t' read -r item_root item_unit item_status _ <<<"${installations[$index]}"
    printf '  %d) %s  %s  [%s]\n' "$((index + 1))" "$item_unit" "$item_root" "$item_status"
  done
  printf 'Select exactly one installation [1-%d]: ' "${#installations[@]}"
  IFS= read -r selection
  [[ "$selection" =~ ^[1-9][0-9]*$ ]] \
    || die "selection must be a displayed number"
  ((selection >= 1 && selection <= ${#installations[@]})) \
    || die "selection is outside the displayed range"
  selected_index=$((selection - 1))
fi

selected="${installations[$selected_index]}"
IFS=$'\t' read -r selected_root selected_unit selected_status installation_id manifest_sha \
  <<<"$selected"
[[ -n "$selected_root" && -n "$selected_unit" && -n "$installation_id" && -n "$manifest_sha" ]] \
  || die "selected installation identity is incomplete"

script_directory="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
sibling_helper="$script_directory/app/open_node_agent/service.py"
checkout_root="$(dirname -- "$script_directory")"
trusted_helper=""
if [[ "$(basename -- "$script_directory")" == "agent" \
  && -e "$checkout_root/.git" && ! -L "$checkout_root/.git" \
  && -f "$sibling_helper" && ! -L "$sibling_helper" ]]; then
  trusted_helper="$(/usr/bin/python3 -I "$policy_file" checkout-helper "$sibling_helper")" \
    || die "checkout sibling service.py failed its policy check"
fi

jobs_output="$(/usr/bin/python3 -I "$policy_file" jobs \
  --root "$selected_root" --unit "$selected_unit" --installation-id "$installation_id")" \
  || die "bootstrap job identity validation failed"
matching_jobs=()
saved_helpers=()
if [[ -n "$jobs_output" ]]; then
  while IFS=$'\t' read -r kind path; do
    case "$kind" in
      JOB) matching_jobs+=("$path") ;;
      HELPER) saved_helpers+=("$path") ;;
      *) die "unexpected bootstrap job inspection output" ;;
    esac
  done <<<"$jobs_output"
fi
if [[ -z "$trusted_helper" ]]; then
  ((${#saved_helpers[@]} == 1)) \
    || die "run this script from a trusted checkout or restore exactly one verified bootstrap helper"
  trusted_helper="${saved_helpers[0]}"
fi

printf '\nSelected Agent:\n'
printf '  Unit: %s\n' "$selected_unit"
printf '  Root: %s\n' "$selected_root"
printf '  State: %s\n' "$selected_status"
printf '\nDefault PURGE removes the service, Agent packages, retained config/state/runtime,\n'
printf 'the dedicated service account, and %d exactly matched private bootstrap job(s).\n' \
  "${#matching_jobs[@]}"
printf 'Answer n to remove the service/packages but preserve all recovery data and jobs.\n'
printf '是否彻底清除以上数据？[Y/n] '
IFS= read -r answer
case "$answer" in
  ''|y|Y|yes|YES|Yes) purge=1 ;;
  n|N|no|NO|No) purge=0 ;;
  *) die "answer must be y or n" ;;
esac

# Revalidate all mutable identities after the operator has reviewed the scope.
revalidated="$(/usr/bin/python3 -I "$policy_file" validate \
  --root "$selected_root" --unit "$selected_unit")" \
  || die "installation identity changed while awaiting confirmation"
[[ "$revalidated" == "$selected" ]] \
  || die "installation manifest changed while awaiting confirmation"
if [[ "$trusted_helper" == "$sibling_helper" ]]; then
  /usr/bin/python3 -I "$policy_file" checkout-helper "$trusted_helper" >/dev/null \
    || die "checkout helper changed while awaiting confirmation"
else
  refreshed_jobs="$(/usr/bin/python3 -I "$policy_file" jobs \
    --root "$selected_root" --unit "$selected_unit" --installation-id "$installation_id")" \
    || die "saved bootstrap helper changed while awaiting confirmation"
  printf '%s\n' "$refreshed_jobs" | /usr/bin/grep -Fqx $'HELPER\t'"$trusted_helper" \
    || die "saved bootstrap helper is no longer verified"
fi

command=(/usr/bin/python3 -I "$trusted_helper" --root "$selected_root" --unit "$selected_unit" uninstall)
if ((purge)); then
  command+=(--purge)
fi
"${command[@]}"

if ((purge)); then
  [[ ! -e "$selected_root" && ! -L "$selected_root" ]] \
    || die "host deployment reported success but the purged installation root remains"
  purged_jobs="$(/usr/bin/python3 -I "$policy_file" purge-jobs \
    --root "$selected_root" --unit "$selected_unit")" \
    || die "Agent was purged, but an exactly matched private bootstrap job needs manual review"
  printf 'Local Agent purge completed; removed %s matched private bootstrap job(s).\n' "$purged_jobs"
else
  printf 'Agent service/packages removed; recovery data and private bootstrap jobs were preserved.\n'
fi
printf 'The host cannot revoke the control-plane server token. Disable or delete this server\n'
printf 'record in the panel before considering the node fully decommissioned.\n'
