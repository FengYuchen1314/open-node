"""Deliver a completed host operation after dropping to the Agent account."""

import argparse
import json
import os
import sqlite3
import ssl
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

LIMIT = 32 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def record_result(config, job, *, acknowledged=False):
    path = Path(config["state_dir"]) / "commands.sqlite"
    if not path.is_file():
        return
    with sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=5) as database:
        database.execute(
            "UPDATE commands SET result=? WHERE request_id=? AND fingerprint=? AND result IS NULL",
            (json.dumps(job["result"]), job["request_id"], job["fingerprint"]),
        )
        if acknowledged:
            database.execute(
                "UPDATE commands SET acknowledged=1 WHERE request_id=? AND fingerprint=?",
                (job["request_id"], job["fingerprint"]),
            )


def deliver(config_path, job):
    if os.geteuid() == 0:
        raise ValueError("Lifecycle reports must run as the Agent account")
    with config_path.open("rb") as source:
        raw = source.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Agent configuration is too large")
    config = json.loads(raw)
    base = config["master_url"].rstrip("/")
    parts = urlsplit(base)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or (parts.scheme == "http" and config.get("allow_insecure_http") is not True)
    ):
        raise ValueError("Invalid controller endpoint")
    result = job["result"]
    if result["request_id"] != job["request_id"]:
        raise ValueError("Lifecycle result identity mismatch")
    record_result(config, job)
    context = ssl.create_default_context(cafile=config.get("ca_file"))
    client = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        NoRedirect(),
    )
    payload = {
        "token": config["token"],
        "status": result["status"],
        "body": result.get("body"),
        "error": result.get("error"),
    }
    request = urllib.request.Request(
        base
        + "/api/v1/agents/commands/by-request/"
        + quote(job["request_id"], safe="")
        + "/result",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with client.open(request, timeout=10) as response:
        raw = response.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("Controller reply is too large")
    command = json.loads(raw)["command"]
    expected = "failed" if result["status"] >= 400 or result.get("error") else "succeeded"
    if command["request_id"] != job["request_id"] or command["status"] != expected:
        raise ValueError("Controller has a conflicting command outcome")
    record_result(config, job, acknowledged=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("Lifecycle report is too large")
        deliver(args.config, json.loads(raw))
    except Exception as error:
        print("Lifecycle report was not acknowledged: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1) from None
    print(json.dumps({"delivered": True}))


if __name__ == "__main__":
    main()
