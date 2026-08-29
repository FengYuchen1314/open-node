"""Claim one invitation and forward traffic through its real Agent/Xray access."""

import argparse
import hashlib
import importlib.util
import os
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

SPEC = importlib.util.spec_from_file_location(
    "invitation_accounts", Path(__file__).with_name("smoke-subscriber-account.py")
)
accounts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(accounts)
native, runtime, service, lifecycle, servers = (
    accounts.native,
    accounts.runtime,
    accounts.service,
    accounts.lifecycle,
    accounts.servers,
)
ROOT = Path(__file__).resolve().parents[2]


def exercise(work, fixture, args, client, backend, endpoint, ca):
    plan = accounts.setup(work, fixture, args, client, endpoint, ca)
    node = client.get("/api/v1/nodes").raise_for_status().json()["nodes"][0]
    base = "/api/v1/servers/" + node["server_id"]
    created = (
        client.post(
            "/api/v1/registration-invitations",
            json={"plan_id": plan["id"], "expires_minutes": 60},
        )
        .raise_for_status()
        .json()
    )
    token = parse_qs(urlsplit(created["registration_url"]).fragment)["invite"][0]
    username = "invited-smoke"
    password = secrets.token_urlsafe(24)

    with httpx.Client(base_url=backend, trust_env=False, timeout=10) as subscriber:
        claimed = subscriber.post(
            "/api/v1/account/register",
            headers={"X-Open-Node-Client": "browser"},
            json={
                "token": token,
                "username": username,
                "password": password,
                "display_name": "Invited Smoke",
            },
        )
        claimed.raise_for_status()
        body = claimed.json()
        assert body["user"]["role"] == "user"
        assert body["user"]["current_plan_id"] == plan["id"]
        assert body["plan"]["id"] == plan["id"]
        assert len(body["commands"]) == 1
        accounts.users.wait_access(client, username)

        login = subscriber.post(
            "/api/v1/account/login",
            headers={"X-Open-Node-Client": "browser"},
            json={"username": username, "password": password},
        )
        login.raise_for_status()
        assert login.json()["authenticated"] is True
        reused = subscriber.post(
            "/api/v1/account/register",
            headers={"X-Open-Node-Client": "browser"},
            json={
                "token": token,
                "username": "second-claim",
                "password": secrets.token_urlsafe(24),
            },
        )
        assert reused.status_code == 404
        assert reused.json() == {"detail": "Invitation unavailable"}

    subscription = (
        client.post(f"/api/v1/users/{username}/subscription-token")
        .raise_for_status()
        .json()["subscription"]
    )
    config = (
        client.get(subscription["subscription_url"], params={"format": "xray"})
        .raise_for_status()
        .json()
    )
    with (
        native.echo_server(work) as (echo, _),
        servers.exported_client(work, args.xray, config) as socks,
        native.connect(socks, echo) as connection,
    ):
        native.transfer(connection, 32768)

    invitations = (
        client.get("/api/v1/registration-invitations").raise_for_status().json()
    )
    current = next(
        item
        for item in invitations["invitations"]
        if item["id"] == created["invitation"]["id"]
    )
    assert current["status"] == "used" and current["used_by"] == username
    assert token not in str(invitations)
    with sqlite3.connect(work / "backend.db") as database:
        stored_hash, used_by = database.execute(
            "SELECT token_hash, used_by FROM registration_invitations WHERE id = ?",
            (created["invitation"]["id"],),
        ).fetchone()
        password_hash = database.execute(
            "SELECT password_hash FROM subscriber_accounts WHERE username = ?",
            (username,),
        ).fetchone()[0]
        assert stored_hash == hashlib.sha256(token.encode()).hexdigest()
        assert used_by == username and token not in stored_hash
        assert password_hash.startswith("$argon2id$") and password not in password_hash
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    assert native.command(client, base, "limiter/status")["pid"] > 0
    print(
        "PASS invitation claim, Agent apply and real Xray forwarding",
        flush=True,
    )


def run(args):
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")

    def callback(work, fixture, wheel, stock, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, backend, endpoint, ca)

    service.exercise = callback
    service.run(args.wheel, args.xray_archive)
    print("PASS registration invitation end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
