"""Exercise atomic owned Xray/Nginx tunnel deployment over both Agent transports."""

import argparse
import base64
import http.client
import importlib.util
import json
import os
import socket
import sqlite3
import ssl
import subprocess
import threading
from contextlib import ExitStack
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "nginx_smoke", Path(__file__).with_name("smoke-nginx.py")
)
nginx_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nginx_smoke)
runtime = nginx_smoke.runtime


def tls_get(port, domain, cert):
    context = ssl.create_default_context(cadata=cert)
    with (
        socket.create_connection(("127.0.0.1", port), timeout=3) as connection,
        context.wrap_socket(connection, server_hostname=domain) as secure,
    ):
        secure.sendall(
            f"GET / HTTP/1.1\r\nHost: {domain}\r\nConnection: close\r\n\r\n".encode()
        )
        response = http.client.HTTPResponse(secure)
        response.begin()
        assert response.status == 200
        return response.read()


def exercise(work, fixture, wheel, nginx, module, xray, client, url, echo_port, mode):
    created = client.post(
        "/api/v1/servers", json={"name": "tunnel-" + mode, "domain": "localhost"}
    ).json()
    base = f"/api/v1/servers/{created['server']['id']}"
    source, original = work / (mode + "-agent.json"), work / (mode + "-xray.json")
    runtime.write_private(
        source,
        {
            "master_url": url,
            "token": created["agent_token"],
            "allow_insecure_http": True,
            "connection_mode": mode,
            "heartbeat_seconds": 1,
            "telemetry_seconds": 1,
            "poll_seconds": 0.2,
            "nginx_binary": str(nginx),
            "nginx_modules": [str(module)],
        },
    )
    runtime.write_private(
        original,
        {"inbounds": [], "outbounds": [{"tag": "direct", "protocol": "freedom"}]},
    )
    fixture.cli(
        "install",
        "--wheel",
        wheel,
        "--config",
        source,
        "--xray-config",
        original,
        "--xray",
        xray,
    )
    assert fixture.properties()["User"] != "root"

    def result(command_id, expected="succeeded"):
        def read():
            return next(
                item
                for item in client.get(base + "/commands").json()["commands"]
                if item["id"] == command_id
            )

        completed = runtime.poll(
            mode + " tunnel command",
            read,
            lambda item: item["status"] in {"succeeded", "failed", "skipped"},
            timeout=45,
        )
        assert completed["status"] == expected, completed
        return completed

    def raw(path, body=None, method="POST", expected="succeeded"):
        response = client.post(
            base + "/commands",
            json={
                "path": "/api/child/" + path,
                "method": method,
                "body": body,
                "timeout_ms": 30000,
            },
        )
        response.raise_for_status()
        return result(response.json()["command"]["id"], expected)

    owned_xray = fixture.root / "config/xray.json"

    def snapshot_ready():
        response = client.get(
            base + "/xray/config-snapshots/recovery?with_config=true"
        ).json()
        current = response.get("current")
        return current and json.loads(current["config"]) == json.loads(
            owned_xray.read_text()
        )

    raw("scan")
    scan = client.get(base + "/scan/latest").json()["scan"]["nginx"]
    assert scan["tunnel_deploy"] == 1 and scan["available"] and not scan["installed"]
    raw("xray/config", method="GET")
    runtime.poll("fresh owned Xray snapshot", snapshot_ready)
    cert, key, _ = nginx_smoke.certificate()
    raw(
        "cert/deploy",
        {
            "domain": "localhost",
            "cert_pem": cert,
            "key_pem": key,
            "cert_path": "localhost.pem",
            "key_path": "localhost.key",
            "reload": "none",
        },
    )
    root = Path(scan["config_path"]).parent
    main, site = root / "nginx.conf", root / "servers/localhost.conf"
    record = fixture.root / "state/host-transaction.json"
    ports = set()
    while len(ports) < 5:
        ports.add(runtime.free_port())
    public, internal, api, metrics, fallback = ports
    payload = {
        "domain": "localhost",
        "listen_address": "127.0.0.1",
        "listen_port": public,
        "nginx_port": internal,
        "api_port": api,
        "metrics_port": metrics,
        "forward_port": fallback,
        "queue_agent_commands": True,
        "queue_scan_after_apply": True,
        "force": True,
        "command_timeout_ms": 30000,
    }

    def deploy(changes=None, expected="succeeded"):
        response = client.post(
            base + "/xray/runtime/tunnel-deploy", json={**payload, **(changes or {})}
        )
        response.raise_for_status()
        data = response.json()
        assert data["runtime_profile"] == "open-node" and len(data["commands"]) == 1
        completed = result(data["commands"][0]["id"], expected)
        result(
            data["scan_command"]["id"],
            "succeeded" if expected == "succeeded" else "skipped",
        )
        if expected == "succeeded":
            runtime.poll(
                "compound deployment refreshes current Xray snapshot", snapshot_ready
            )
        return data, completed

    with ExitStack() as stack:
        fallback_cert, fallback_key, _ = nginx_smoke.certificate("other.localhost")
        cert_file, key_file = work / "fallback.pem", work / "fallback.key"
        cert_file.write_text(fallback_cert)
        key_file.write_text(fallback_key)
        key_file.chmod(0o600)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file, key_file)
        tls_server = runtime.ThreadingHTTPServer(
            ("127.0.0.1", fallback), runtime.EchoHandler
        )
        tls_server.socket = context.wrap_socket(tls_server.socket, server_side=True)
        thread = threading.Thread(target=tls_server.serve_forever, daemon=True)
        thread.start()
        stack.callback(tls_server.server_close)
        stack.callback(thread.join, 5)
        stack.callback(tls_server.shutdown)

        data, completed = deploy()
        assert not completed["result_body"]["restart_required"]
        assert tls_get(public, "localhost", cert) == b"Open Node\n"
        assert (
            tls_get(public, "other.localhost", fallback_cert) == runtime.RESPONSE_BODY
        )
        print(
            f"PASS {mode} fresh TLS SNI tunnel: owned static site and fixed fallback bytes",
            flush=True,
        )

        def traffic_reported():
            latest = client.get(base + "/telemetry/latest").json().get("latest")
            if not latest:
                return False
            counters = (
                (latest.get("stats") or {}).get("inbound", {}).get("tunnel-in", {})
            )
            return counters.get("uplink", 0) > 0 and counters.get("downlink", 0) > 0

        runtime.poll(
            "native API listener exports real traffic statistics", traffic_reported
        )

        # A valid but stale queued template must not overwrite a later local edit.
        old_request = data["command_previews"][0]["body"]
        raw("tunnel/deploy", old_request, expected="failed")
        assert tls_get(public, "localhost", cert) == b"Open Node\n"

        files = {path: path.read_bytes() for path in (main, site, owned_xray)}
        for port_name in ("nginx_port", "listen_port"):
            with socket.socket() as occupied:
                occupied.bind(("127.0.0.1", 0))
                occupied.listen()
                deploy({port_name: occupied.getsockname()[1]}, expected="failed")
                assert all(path.read_bytes() == value for path, value in files.items())
                assert not record.exists()
                assert tls_get(public, "localhost", cert) == b"Open Node\n"
                assert (
                    tls_get(public, "other.localhost", fallback_cert)
                    == runtime.RESPONSE_BODY
                )
        print(
            f"PASS {mode} Nginx and Xray port conflicts roll back both live services",
            flush=True,
        )

        # Move a listener from the owned Nginx stream context to Xray, retaining neighbors.
        stream_port, retained = runtime.free_port(), runtime.free_port()
        stream = root / "stream_servers/relay.conf"
        raw(
            "nginx/config-files",
            {
                "path": "stream_servers/relay.conf",
                "content": (
                    f"server {{ listen 127.0.0.1:{stream_port}; proxy_pass 127.0.0.1:{echo_port}; }}\n"
                    f"server {{ listen 127.0.0.1:{retained}; proxy_pass 127.0.0.1:{echo_port}; }}\n"
                ),
            },
        )
        raw(
            "nginx/config",
            {
                "config": main.read_text()
                + "\nstream { include stream_servers/*.conf; }\n"
            },
        )
        raw("services/control", {"service": "nginx", "action": "reload"})
        assert nginx_smoke.request(stream_port, runtime.RESPONSE_BODY)
        assert nginx_smoke.request(retained, runtime.RESPONSE_BODY)
        payload["listen_port"] = stream_port
        deploy()
        public = stream_port
        assert tls_get(public, "localhost", cert) == b"Open Node\n"
        assert (
            str(retained) in stream.read_text()
            and str(stream_port) not in stream.read_text()
        )
        assert nginx_smoke.request(retained, runtime.RESPONSE_BODY)
        deploy({"site_type": "proxy", "site_value": f"http://127.0.0.1:{echo_port}"})
        assert tls_get(public, "localhost", cert) == runtime.RESPONSE_BODY
        assert (
            tls_get(public, "other.localhost", fallback_cert) == runtime.RESPONSE_BODY
        )
        print(
            f"PASS {mode} stream listener handover preserves neighbors; real TLS reverse proxy",
            flush=True,
        )

        # Recover a durable multi-file undo record before starting either child process.
        for desired in (True, False):
            for service in ("nginx", "xray"):
                raw(
                    "services/control",
                    {"service": service, "action": "start" if desired else "stop"},
                )
            subprocess.run(["systemctl", "stop", fixture.unit], check=True, timeout=20)
            files = {
                path: path.read_bytes() for path in (main, site, owned_xray, stream)
            }
            runtime.write_private(
                record,
                {
                    "schema": 1,
                    "files": {
                        str(path): base64.b64encode(value).decode()
                        for path, value in files.items()
                    },
                    "intents": {"xray": desired, "nginx": desired},
                },
            )
            owner = main.stat()
            os.chown(record, owner.st_uid, owner.st_gid)
            for path in files:
                path.write_text("interrupted candidate")
            with sqlite3.connect(fixture.root / "state/commands.sqlite") as db:
                db.execute(
                    "UPDATE settings SET value=? WHERE key IN ('runtime_running','nginx_running')",
                    ("false" if desired else "true",),
                )
            subprocess.run(["systemctl", "start", fixture.unit], check=True, timeout=20)
            runtime.poll(
                "coupled recovery restores service intent", fixture.ready, timeout=40
            )
            assert all(path.read_bytes() == value for path, value in files.items())
            assert not record.exists()
            assert (
                runtime.port_open(public) == desired
                and runtime.port_open(internal) == desired
            )
            if desired:
                assert tls_get(public, "localhost", cert) == runtime.RESPONSE_BODY
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            deploy({"listen_port": occupied.getsockname()[1]}, expected="failed")
            assert not runtime.port_open(public) and not runtime.port_open(internal)
        print(
            f"PASS {mode} crash recovery and failed cold deployment preserve stopped intent",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--nginx-stream-module", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    args = parser.parse_args()
    nginx_smoke.run(
        args.wheel.resolve(),
        args.nginx.resolve(),
        args.nginx_stream_module.resolve(),
        args.xray_archive.resolve() if args.xray_archive else None,
        exercise_fn=exercise,
    )
    print("PASS atomic owned tunnel smoke", flush=True)
