"""Verify an actual public Agent release using the default host-approved source."""

import argparse
import importlib.util
from pathlib import Path
from uuid import uuid4

SPEC = importlib.util.spec_from_file_location(
    "public_agent_lifecycle", Path(__file__).with_name("smoke-agent-lifecycle.py")
)
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)
service, runtime = lifecycle.service, lifecycle.runtime


def run(wheel, nginx, archive):
    spec = importlib.util.spec_from_file_location(
        "public_release_host", lifecycle.ROOT / "agent/app/open_node_agent/service.py"
    )
    info = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(info)
    release = info.wheel_info(wheel)

    def exercise(work, first, wheel, xray, client, backend, echo):
        with lifecycle.gateway(work, nginx, backend) as (endpoint, ca, _):
            for mode in ("websocket", "http"):
                directory = work / mode
                directory.mkdir()
                fixture = first if mode == "websocket" else service.Fixture(work)
                try:
                    created = (
                        client.post(
                            "/api/v1/servers", json={"name": "published-agent-" + mode}
                        )
                        .raise_for_status()
                        .json()
                    )
                    base = "/api/v1/servers/" + created["server"]["id"]
                    config, xray_config = (
                        directory / "agent.json",
                        directory / "xray.json",
                    )
                    port, user = runtime.free_port(), str(uuid4())
                    runtime.write_private(
                        config,
                        {
                            "master_url": endpoint,
                            "ca_file": str(ca),
                            "token": created["agent_token"],
                            "connection_mode": mode,
                            "heartbeat_seconds": 1,
                            "telemetry_seconds": 1,
                            "poll_seconds": 0.2,
                        },
                    )
                    runtime.write_private(
                        xray_config,
                        {
                            "log": {"loglevel": "warning"},
                            "inbounds": [
                                {
                                    "tag": "vless",
                                    "listen": "127.0.0.1",
                                    "port": port,
                                    "protocol": "vless",
                                    "settings": {
                                        "decryption": "none",
                                        "clients": [{"id": user}],
                                    },
                                }
                            ],
                            "outbounds": [{"protocol": "freedom", "tag": "direct"}],
                        },
                    )
                    previous = service.variant_wheel(wheel, directory, "good")
                    fixture.cli(
                        "install",
                        "--wheel",
                        previous,
                        "--config",
                        config,
                        "--xray-config",
                        xray_config,
                        "--xray",
                        xray,
                    )
                    original = fixture.record()["current"]
                    assert original != release["id"]
                    fixture.cli("enable-remote")
                    status = lifecycle.wait_command(
                        client, base, lifecycle.queue(client, base, "lifecycle")
                    )
                    assert status["result_body"]["release_base_url"] == (
                        "https://github.com/FengYuchen1314/open-node/releases/download"
                    )
                    result = lifecycle.wait_command(
                        client,
                        base,
                        lifecycle.queue(
                            client,
                            base,
                            "upgrade",
                            {
                                "version": release["version"],
                                "sha256": release["sha256"],
                            },
                        ),
                    )
                    assert result["result_body"]["current"] == release
                    assert (
                        fixture.record()["current"] == release["id"] and fixture.ready()
                    )
                    with runtime.proxy_client(directory, xray, port, user) as socks:
                        runtime.poll(
                            "published Agent forwards VLESS traffic",
                            lambda: runtime.forwards(socks, echo),
                        )
                    lifecycle.wait_command(
                        client,
                        base,
                        lifecycle.queue(
                            client,
                            base,
                            "rollback",
                            {"confirm": True},
                        ),
                    )
                    assert fixture.record()["current"] == original and fixture.ready()
                    print(
                        "PASS "
                        + mode
                        + " actual GitHub release download, pin and rollback",
                        flush=True,
                    )
                finally:
                    fixture.cleanup()

    service.exercise = exercise
    service.run(wheel, archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--nginx", type=Path, required=True)
    parser.add_argument("--xray-archive", type=Path)
    args = parser.parse_args()
    run(
        args.wheel.resolve(),
        args.nginx.resolve(),
        args.xray_archive.resolve() if args.xray_archive else None,
    )
