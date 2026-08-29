"""Verify custom links, authenticated edits and unchanged real forwarding on the VPS."""

import argparse
import importlib.util
import json
import os
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import pyotp
from playwright.sync_api import expect, sync_playwright

SPEC = importlib.util.spec_from_file_location(
    "link_accounts", Path(__file__).with_name("smoke-subscriber-account.py")
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
    accounts.setup(work, fixture, args, client, endpoint, ca)
    node = client.get("/api/v1/nodes").raise_for_status().json()["nodes"][0]
    base = "/api/v1/servers/" + node["server_id"]
    pid = native.command(client, base, "limiter/status")["pid"]

    def token(username="alice"):
        return (
            client.get("/api/v1/user-subscription-token", params={"username": username})
            .raise_for_status()
            .json()["subscription"]
        )

    def set_code(code, username="alice"):
        return (
            client.put(
                "/api/v1/user-subscription-short-code",
                params={"username": username},
                json={
                    "custom_short_code": code,
                    "expected_revision": token(username)["revision"],
                },
            )
            .raise_for_status()
            .json()["subscription"]
        )

    original = {username: token(username) for username in ("alice", "bob")}
    configs = {
        username: client.get(value["subscription_url"], params={"format": "xray"})
        .raise_for_status()
        .json()
        for username, value in original.items()
    }
    credentials = (
        client.get("/api/v1/users/alice/credentials").raise_for_status().json()
    )
    password = secrets.token_urlsafe(24)
    login = (
        client.get("/api/v1/subscriber-accounts", params={"username": "alice"})
        .raise_for_status()
        .json()
    )
    client.put(
        "/api/v1/subscriber-accounts",
        params={"username": "alice"},
        json={"expected_revision": login["revision"], "new_password": password},
    ).raise_for_status()

    with native.echo_server(work) as (echo, _), sync_playwright() as playwright:

        def forward(config):
            with (
                servers.exported_client(work, args.xray, config) as socks,
                native.connect(socks, echo) as connection,
            ):
                native.transfer(connection, 4096)

        forward(configs["alice"])
        browser = playwright.chromium.launch()
        admin_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        portal_context = browser.new_context(
            viewport={"width": 1440, "height": 1000}, accept_downloads=True
        )
        errors, requests = [], []
        try:
            admin_context.add_cookies(
                [
                    {
                        "name": item.name,
                        "value": item.value,
                        "url": backend,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                    for item in client.cookies.jar
                ]
            )
            page, portal = admin_context.new_page(), portal_context.new_page()
            for target in (page, portal):
                target.on("pageerror", lambda error: errors.append(str(error)))
                target.on("request", lambda request: requests.append(request.url))
            page.goto(backend + "/subscriptions")
            admin_context.grant_permissions(
                ["clipboard-read", "clipboard-write"], origin=backend
            )
            page.get_by_role("button", name="Share", exact=True).click()
            temporary_dialog = page.get_by_role("dialog")
            temporary_dialog.get_by_label("Label", exact=True).fill(
                "Smoke temporary link"
            )
            temporary_dialog.get_by_label("Downloads", exact=True).fill("2")
            accounts.capture(page, args.output, "temporary-link-create")
            with page.expect_response(
                lambda response: (
                    response.url.endswith("/api/v1/temporary-subscriptions")
                    and response.request.method == "POST"
                )
            ) as temporary_response:
                temporary_dialog.get_by_role(
                    "button", name="Create", exact=True
                ).click()
            assert temporary_response.value.status == 201, (
                temporary_response.value.text()
            )
            temporary = temporary_response.value.json()
            expect(
                temporary_dialog.get_by_label("Temporary URL", exact=True)
            ).to_have_value(temporary["subscription_url"])
            temporary_dialog.get_by_role("button", name="Close", exact=True).click()

            temporary_path = urlsplit(temporary["subscription_url"]).path
            temporary_xray = client.get(
                temporary_path, params={"format": "xray"}
            ).raise_for_status()
            assert "subscription-userinfo" not in temporary_xray.headers
            forward(temporary_xray.json())
            client.get(temporary_path, params={"format": "uri-list"}).raise_for_status()
            assert client.get(temporary_path).status_code == 404
            page.reload()
            temporary_item = page.locator(".catalog-item").filter(
                has_text="Smoke temporary link"
            )
            expect(temporary_item).to_contain_text("2/2 downloads")
            expect(temporary_item).to_contain_text("exhausted")
            temporary_item.get_by_role(
                "button", name="Copy temporary link Smoke temporary link", exact=True
            ).click()
            assert (
                page.evaluate("navigator.clipboard.readText()")
                == temporary["subscription_url"]
            )
            accounts.capture(page, args.output, "temporary-link-exhausted")
            with page.expect_response(
                lambda response: (
                    response.url.endswith(
                        "/api/v1/temporary-subscriptions/" + temporary["id"]
                    )
                    and response.request.method == "DELETE"
                )
            ) as revoked_response:
                temporary_item.get_by_role(
                    "button",
                    name="Revoke temporary link Smoke temporary link",
                    exact=True,
                ).click()
            assert revoked_response.value.status == 200, revoked_response.value.text()
            expect(temporary_item).to_have_count(0)
            print(
                "PASS temporary link browser lifecycle, access limit and real forwarding",
                flush=True,
            )

            page.get_by_role(
                "button", name="Edit short code for alice", exact=True
            ).click()
            dialog = page.get_by_role("dialog")
            field = dialog.get_by_label("Custom short code", exact=True)
            expect(field).to_have_value("")
            for invalid in ("Admin", "bad/code", "a"):
                field.fill(invalid)
                expect(
                    dialog.get_by_role("button", name="Save", exact=True)
                ).to_be_disabled()
            external = set_code("external-code")

            def save(target, expected=200, own=False):
                suffix = (
                    "/account/subscription-short-code"
                    if own
                    else "/users/alice/subscription-short-code"
                )
                with target.expect_response(
                    lambda response: (
                        response.url.endswith(suffix)
                        and response.request.method == "PUT"
                    )
                ) as response:
                    target.get_by_role("dialog").get_by_role(
                        "button", name="Save", exact=True
                    ).click()
                assert response.value.status == expected, response.value.text()
                if expected == 200:
                    expect(
                        target.get_by_role("dialog").get_by_text(
                            "Short code saved", exact=True
                        )
                    ).to_be_visible()
                return response.value

            field.fill("Alpha_Link-2")
            save(page, 409)
            expect(
                dialog.get_by_text(
                    "Subscription links changed; reload before saving", exact=True
                )
            ).to_be_visible()
            dialog.get_by_role(
                "button", name="Reload subscription links", exact=True
            ).click()
            expect(field).to_have_value("external-code")
            field.fill("Alpha_Link-2")
            save(page)
            custom = token()
            assert custom["token"] == original["alice"]["token"]
            assert custom["generated_short_code"] == original["alice"]["short_code"]
            assert client.get(external["short_url"]).status_code == 404
            for format in ("clash", "sing-box", "xray", "uri-list", "base64"):
                expected = client.get(
                    custom["subscription_url"], params={"format": format}
                ).raise_for_status()
                for url in (custom["short_url"], original["alice"]["short_url"]):
                    response = client.get(
                        url, params={"format": format}
                    ).raise_for_status()
                    assert response.content == expected.content
                    assert (
                        response.headers["subscription-userinfo"]
                        == expected.headers["subscription-userinfo"]
                    )
            accounts.capture(page, args.output, "admin-short-code")
            dialog.get_by_role("button", name="Close", exact=True).click()
            forward(
                client.get(custom["short_url"], params={"format": "xray"})
                .raise_for_status()
                .json()
            )
            print(
                "PASS administrator edits, revision conflict and all subscription formats",
                flush=True,
            )

            accounts.sign_in(portal, backend, password)
            expect(
                portal.get_by_role("heading", name="Alice", exact=True)
            ).to_be_visible()
            csrf = portal_context.request.get(
                backend + "/api/v1/account/session"
            ).json()["csrf_token"]
            setup = portal_context.request.post(
                backend + "/api/v1/account/totp/setup",
                data={"password": password},
                headers={"X-CSRF-Token": csrf},
            )
            assert setup.status == 200, setup.status
            confirmed = portal_context.request.post(
                backend + "/api/v1/account/totp/confirm",
                data={"code": pyotp.TOTP(setup.json()["secret"]).now()},
                headers={"X-CSRF-Token": csrf},
            )
            assert confirmed.status == 200, confirmed.status
            recovery = confirmed.json()["recovery_codes"]
            portal.get_by_role(
                "button", name="Edit subscription short code", exact=True
            ).click()
            own = portal.get_by_role("dialog")
            own.get_by_label("Custom short code", exact=True).fill("Reader_Link")
            own.get_by_label("Current password", exact=True).fill("wrong-password")
            own.get_by_label("Authenticator or recovery code", exact=True).fill(
                recovery[0]
            )
            save(portal, 400, own=True)
            expect(own.get_by_label("Current password", exact=True)).to_have_value("")
            own.get_by_label("Current password", exact=True).fill(password)
            own.get_by_label("Authenticator or recovery code", exact=True).fill(
                recovery[0]
            )
            save(portal, own=True)
            current = token()
            assert current["short_code"] == "Reader_Link"
            assert client.get(custom["short_url"]).status_code == 404
            accounts.capture(portal, args.output, "subscriber-short-code")
            own.get_by_role("button", name="Close", exact=True).click()
            portal.get_by_role("button", name="Short", exact=True).click()
            portal.locator(".account-link-controls .v-select .v-field").click()
            portal.get_by_role("option", name="Xray", exact=True).click()
            expect(portal.get_by_label("Subscription URL", exact=True)).to_have_value(
                current["short_url"] + "?format=xray"
            )
            with portal.expect_download() as download:
                portal.get_by_role(
                    "link", name="Download subscription", exact=True
                ).click()
            path = Path(download.value.path())
            downloaded = json.loads(path.read_text())
            assert downloaded == configs["alice"]
            forward(downloaded)
            accounts.capture(portal, args.output, "short-link-download")
            print(
                "PASS subscriber password/second-factor proof and real short-link download",
                flush=True,
            )

            page.get_by_role(
                "button", name="Edit short code for alice", exact=True
            ).click()
            expect(field).to_have_value("Reader_Link")
            field.fill("")
            save(page)
            cleared = token()
            assert cleared["custom_short_code"] is None
            assert cleared["short_url"] == original["alice"]["short_url"]
            assert client.get(current["short_url"]).status_code == 404
            dialog.get_by_role("button", name="Close", exact=True).click()
            set_code("Other_Link", "bob")
            set_code("Before_Reset")
            portal.get_by_role(
                "button", name="Edit subscription short code", exact=True
            ).click()
            own.get_by_label("Custom short code", exact=True).fill("OTHER_LINK")
            own.get_by_label("Current password", exact=True).fill(password)
            own.get_by_label("Authenticator or recovery code", exact=True).fill(
                recovery[1]
            )
            save(portal, 409, own=True)
            expect(
                own.get_by_text("This short code is unavailable", exact=True)
            ).to_be_visible()
            own.get_by_role("button", name="Cancel", exact=True).click()
            before_reset = token()
            portal.get_by_role("tab", name="Security", exact=True).click()
            portal.get_by_role("button", name="Reset links", exact=True).click()
            reset = portal.get_by_role("dialog")
            reset.get_by_label("Current password", exact=True).fill(password)
            reset.get_by_label("Authenticator or recovery code", exact=True).fill(
                recovery[1]
            )
            with portal.expect_response(
                lambda response: response.url.endswith(
                    "/account/subscription-token/reset"
                )
            ) as response:
                reset.get_by_role("button", name="Confirm", exact=True).click()
            assert response.value.status == 200, response.value.text()
            fresh = token()
            assert fresh["custom_short_code"] is None
            for url in (
                original["alice"]["subscription_url"],
                original["alice"]["short_url"],
                before_reset["short_url"],
            ):
                assert client.get(url).status_code == 404
            assert (
                client.get(fresh["short_url"], params={"format": "xray"})
                .raise_for_status()
                .json()
                == configs["alice"]
            )
            forward(configs["alice"])
            forward(configs["bob"])
            assert native.command(client, base, "limiter/status")["pid"] == pid
            assert (
                client.get("/api/v1/users/alice/credentials").raise_for_status().json()
                == credentials
            )
            assert original["bob"]["token"] == token("bob")["token"]
            with sqlite3.connect(work / "backend.db") as db:
                assert db.execute("PRAGMA foreign_key_check").fetchall() == []
            assert not errors, errors
            assert all(url.startswith((backend, "data:")) for url in requests)
            print(
                "PASS clear, collision, complete link reset and unchanged Xray/credentials/Bob forwarding",
                flush=True,
            )
        finally:
            admin_context.close()
            portal_context.close()
            browser.close()


def run(args):
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["OPEN_NODE_FRONTEND_DIR"] = str(ROOT / "frontend/dist")
    os.environ["OPEN_NODE_SUBSCRIBER_TOTP_KEY"] = (
        accounts.Fernet.generate_key().decode()
    )

    def callback(work, fixture, wheel, stock, client, backend, echo):
        with lifecycle.gateway(work, args.nginx, backend) as (endpoint, ca, _):
            exercise(work, fixture, args, client, backend, endpoint, ca)

    service.exercise = callback
    service.run(args.wheel, args.xray_archive)
    print("PASS custom subscription links end-to-end " + args.transport, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("xray", "wheel", "nginx", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    parser.add_argument(
        "--transport", choices=["websocket", "http"], default="websocket"
    )
    run(parser.parse_args())
