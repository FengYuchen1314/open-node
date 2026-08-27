"""Exercise the PowerShell launcher over an isolated real loopback SSH service."""

import argparse
import os
import shlex
import socket
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory


def run(args, *, cwd=None, env=None, check=True):
    return subprocess.run(
        list(map(str, args)),
        cwd=cwd,
        env=env,
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
    )


def commit(root, value):
    (root / "payload.txt").write_text(value)
    run(["git", "add", "."], cwd=root)
    run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            value,
        ],
        cwd=root,
    )
    return run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()


def smoke(pwsh):
    if os.geteuid() != 0:
        raise ValueError(
            "This disposable SSH fixture requires root on the designated VPS"
        )
    launcher = Path(__file__).with_name("sync-and-test.ps1").resolve()
    with (
        TemporaryDirectory(prefix="open-node-sync-ssh-", dir="/root") as work_name,
        TemporaryDirectory(prefix="sync-fixture-", dir="/opt/open-node") as target_name,
    ):
        work, target = Path(work_name), Path(target_name)
        work.chmod(0o700)
        author, origin = work / "author's checkout", work / "origin's repo.git"
        branch = "codex/sync'check"
        run(["git", "init", "--bare", "--initial-branch", branch, origin])
        run(["git", "clone", origin, author])
        scripts = author / "scripts/vps"
        scripts.mkdir(parents=True)
        (author / ".gitignore").write_text("bootstrap-marker\ntested-revision\n")
        (scripts / "bootstrap-debian.sh").write_text(
            '#!/bin/bash\nset -eu\ncd "$(dirname "$0")/../.."\ntouch bootstrap-marker\n'
        )
        (scripts / "run-tests.sh").write_text(
            '#!/bin/bash\nset -eu\ncd "$(dirname "$0")/../.."\ngit rev-parse HEAD > tested-revision\n'
        )
        first = commit(author, "first")
        for name in ("host-key", "client-key"):
            run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", work / name])
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        server_config = work / "sshd.conf"
        server_config.write_text(
            f"Port {port}\nListenAddress 127.0.0.1\nHostKey {work / 'host-key'}\n"
            f"PidFile {work / 'sshd.pid'}\nAuthorizedKeysFile {work / 'client-key.pub'}\n"
            "PasswordAuthentication no\nKbdInteractiveAuthentication no\nUsePAM no\n"
            "PermitRootLogin prohibit-password\nAllowUsers root\nLogLevel VERBOSE\n"
        )
        host_public = (work / "host-key.pub").read_text().split()
        known_hosts = work / "known-hosts"
        known_hosts.write_text(f"[127.0.0.1]:{port} {' '.join(host_public[:2])}\n")
        client_config = work / "ssh.conf"
        client_config.write_text(
            f"Host *\n  Port {port}\n  IdentityFile {work / 'client-key'}\n"
            f"  UserKnownHostsFile {known_hosts}\n  StrictHostKeyChecking yes\n"
            "  IdentitiesOnly yes\n  BatchMode yes\n  ConnectTimeout 5\n"
        )
        shim_dir = work / "bin"
        shim_dir.mkdir()
        shim = shim_dir / "ssh"
        shim.write_text(
            f'#!/bin/sh\nexec /usr/bin/ssh -F {shlex.quote(str(client_config))} "$@"\n'
        )
        shim.chmod(0o700)
        env = {**os.environ, "PATH": f"{shim_dir}:{os.environ['PATH']}"}
        run(["/usr/sbin/sshd", "-t", "-f", server_config])
        with (work / "sshd.log").open("w+") as log:
            server = subprocess.Popen(
                ["/usr/sbin/sshd", "-D", "-e", "-f", str(server_config)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                for _ in range(50):
                    if server.poll() is not None:
                        raise RuntimeError("Disposable SSH service exited")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise TimeoutError("Disposable SSH service did not become ready")
                command = [
                    pwsh,
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                    launcher,
                    "-HostName",
                    "root@127.0.0.1",
                    "-RepoUrl",
                    origin,
                    "-Branch",
                    branch,
                    "-RemoteDir",
                    target,
                ]
                run([*command, "-SkipBootstrap"], cwd=author, env=env)
                assert (target / "tested-revision").read_text().strip() == first
                assert not (target / "bootstrap-marker").exists()
                second = commit(author, "second")
                run(command, cwd=author, env=env)
                assert (target / "tested-revision").read_text().strip() == second
                assert (target / "bootstrap-marker").exists()
                (target / "payload.txt").write_text("operator edit")
                commit(author, "third")
                refused = run(
                    [*command, "-SkipBootstrap"], cwd=author, env=env, check=False
                )
                assert refused.returncode != 0 and "local changes" in refused.stderr
                assert (target / "payload.txt").read_text() == "operator edit"
                assert (target / "tested-revision").read_text().strip() == second
                assert (
                    run(["git", "rev-parse", "HEAD"], cwd=target).stdout.strip()
                    == second
                )
                print(
                    "PASS PowerShell, real SSH key auth, quoted branch/URL and exact commit",
                    flush=True,
                )
                print(
                    "PASS bootstrap switch, fast-forward update and dirty checkout preservation",
                    flush=True,
                )
            except BaseException:
                log.flush()
                log.seek(0)
                print(log.read())
                raise
            finally:
                server.terminate()
                try:
                    server.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pwsh", type=Path, required=True)
    args = parser.parse_args()
    try:
        smoke(args.pwsh.resolve())
    except subprocess.CalledProcessError as exc:
        print(exc.stdout)
        print(exc.stderr)
        raise
