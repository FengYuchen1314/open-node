import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import psutil
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from open_node_agent.certificates import hostname, validate_pair
from open_node_agent.client import Agent
from open_node_agent.host_files import guarded_path
from open_node_agent.nginx import directive
from open_node_agent.runtime import RuntimeFailure, atomic_write


def certificate(domain="localhost", *, expired=False, not_yet_valid=False, ip_san=False):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, domain)])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now + timedelta(days=1) if not_yet_valid else now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=-1 if expired else 3))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ip_address(domain)) if ip_san else x509.DNSName(domain)]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


@pytest.fixture
async def agent(config):
    instance = Agent(config.model_copy(update={"nginx_binary": Path("/operator/nginx")}))
    instance.operations.nginx.validate = AsyncMock()
    yield instance
    await instance.close()


async def test_status_reports_the_owned_nginx_version(agent, monkeypatch):
    command = AsyncMock(return_value=(0, "nginx version: nginx/1.29.1\n"))
    monkeypatch.setattr("open_node_agent.nginx.run_command", command)
    status = await agent.operations.nginx.status()
    assert status["version"] == "nginx version: nginx/1.29.1"
    command.assert_awaited_once_with("/operator/nginx", "-v", timeout=5)

    command.side_effect = TimeoutError
    assert (await agent.operations.nginx.status())["version"] is None


def test_certificate_validity_and_name_matching():
    cert, key = certificate()
    assert validate_pair("LOCALHOST.", cert, key)["domain"] == "localhost"
    wildcard, wildcard_key = certificate("*.example.com")
    assert validate_pair("a.example.com", wildcard, wildcard_key)["expires_at"]
    for domain in ("example.com", "a.b.example.com", "other.example.net"):
        with pytest.raises(RuntimeFailure, match="SAN"):
            validate_pair(domain, wildcard, wildcard_key)
    with pytest.raises(RuntimeFailure, match="do not match"):
        validate_pair("localhost", cert, certificate()[1])
    for pair in (certificate(expired=True), certificate(not_yet_valid=True)):
        with pytest.raises(RuntimeFailure, match="expired or not yet valid"):
            validate_pair("localhost", *pair)
    with pytest.raises(RuntimeFailure, match="Invalid certificate"):
        validate_pair("localhost", "invalid-secret-certificate", "invalid-secret-key")


@pytest.mark.parametrize("domain", ["192.0.2.20", "2001:db8::20"])
async def test_ip_san_certificate_deployment_keeps_exact_matching_and_owned_paths(agent, domain):
    cert, key = certificate(domain, ip_san=True)
    expanded = str(ip_address(domain).exploded)
    assert validate_pair(expanded, cert, key)["domain"] == domain
    with pytest.raises(RuntimeFailure, match="SAN"):
        validate_pair(str(ip_address(domain) + 1), cert, key)
    with pytest.raises(RuntimeFailure, match="SAN"):
        validate_pair(domain, *certificate(domain))
    if ":" in domain:
        with pytest.raises(RuntimeFailure, match="Invalid domain"):
            hostname(domain)  # Site hostname parsing has not been widened.
        with pytest.raises(RuntimeFailure, match="Invalid domain"):
            validate_pair(domain + "%eth0", cert, key)
    nginx = agent.operations.nginx
    await seed(nginx)
    response = await nginx.handle(
        "POST",
        "/api/child/cert/deploy",
        {
            "domain": domain,
            "cert_pem": cert,
            "key_pem": key,
            "cert_path": "private-ip.pem",
            "key_path": "private-ip.key",
            "reload": "none",
        },
        {},
    )
    assert response["success"] and key not in str(response)
    assert nginx.cert_path("private-ip.pem").read_text() == cert
    assert nginx.cert_path("private-ip.key").read_text() == key
    assert nginx.cert_path("private-ip.key").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "domain", ["../outside", "*.example.com", "a;root /", "a/b", "-bad", "a..b"]
)
def test_unsafe_domain_names(domain):
    with pytest.raises(RuntimeFailure, match="Invalid domain"):
        hostname(domain)


async def test_paths_reject_escape_symlinks_hardlinks_and_token_files(agent, tmp_path):
    nginx = agent.operations.nginx
    for value in ("../agent.json", "/etc/nginx/nginx.conf", "agent.json", "../nginx-evil/a.conf"):
        with pytest.raises(RuntimeFailure):
            nginx.config_path(value)
    for value in ("../../agent.json", "../nginx/nginx.conf", "nginx.conf", "/etc/cron.d/task"):
        with pytest.raises(RuntimeFailure):
            nginx.cert_path(value)
    nginx.prepare()
    outside = tmp_path / "outside"
    outside.mkdir()
    (nginx.root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeFailure, match="Symlink"):
        nginx.config_path("linked/site.conf")
    (nginx.root / "linked").unlink()
    atomic_write(nginx.main, nginx.default_main())
    (nginx.root / "hard.conf").hardlink_to(nginx.main)
    with pytest.raises(RuntimeFailure, match="Hard-linked"):
        nginx.config_path("hard.conf")
    (nginx.root / "hard.conf").unlink()
    with pytest.raises(RuntimeFailure, match="nginx_site_roots"):
        nginx.site_path(str(tmp_path))
    (nginx.html / "index.html").symlink_to(agent.config.xray_config)
    with pytest.raises(RuntimeFailure, match="Symlink"):
        guarded_path(nginx.html, "index.html")


async def seed(nginx):
    await nginx.apply(
        {
            nginx.main: nginx.default_main(),
            nginx.root / "servers/local.conf": b"server { listen 18080; }\n",
        }
    )


async def test_config_parser_includes_and_owned_master_controls(agent):
    nginx = agent.operations.nginx
    await seed(nginx)
    original = nginx.main.read_bytes()
    effective = nginx.effective.read_bytes()
    assert b"listen 18080" in effective
    assert str(nginx.state / "nginx.pid").encode() in effective
    for text in (
        "events {} http {",
        "include ../external.conf;",
        "include nginx.conf;",
        "daemon on; events {} http {}",
        "events {} http { server { root /etc; } }",
    ):
        with pytest.raises(RuntimeFailure):
            await nginx.apply({nginx.main: text.encode()})
        assert nginx.main.read_bytes() == original
        assert nginx.effective.read_bytes() == effective
        assert not nginx.transaction.record.exists()


async def test_config_validation_failure_restores_every_file(agent):
    nginx = agent.operations.nginx
    await seed(nginx)
    old_main, old_effective = nginx.main.read_bytes(), nginx.effective.read_bytes()
    nginx.validate.side_effect = RuntimeFailure("fixture config failure")
    new_file = nginx.config_path("servers/new.conf")
    with pytest.raises(RuntimeFailure):
        await nginx.apply(
            {
                nginx.main: b"events {} http { include servers/*.conf; }",
                new_file: b"server { listen 18081; unknown_directive yes; }",
            }
        )
    assert nginx.main.read_bytes() == old_main
    assert nginx.effective.read_bytes() == old_effective
    assert not new_file.exists()


async def test_interrupted_file_transaction_recovers_before_runtime_start(config):
    agent = Agent(config)
    nginx = agent.operations.nginx
    cert = nginx.cert_path("localhost.pem")
    key = nginx.cert_path("localhost.key")
    atomic_write(cert, b"old certificate")
    nginx.transaction.begin({cert: b"new certificate", key: b"new private key"})
    assert cert.read_bytes() == b"new certificate"
    await agent.close()
    restarted = Agent(config)
    try:
        assert cert.read_bytes() == b"old certificate"
        assert not key.exists()
        assert not restarted.operations.nginx.transaction.record.exists()
        assert not await restarted.runtime.running()
    finally:
        await restarted.close()


async def test_certificate_rotation_rolls_back_both_services(agent):
    nginx = agent.operations.nginx
    await seed(nginx)
    cert, key = certificate()
    body = {
        "domain": "localhost",
        "cert_pem": cert,
        "key_pem": key,
        "cert_path": "localhost.pem",
        "key_path": "localhost.key",
        "reload": "none",
    }
    response = await nginx.handle("POST", "/api/child/cert/deploy", body, {})
    assert response["success"]
    assert key not in str(response)
    assert nginx.cert_path("localhost.key").stat().st_mode & 0o777 == 0o600
    nginx.running = AsyncMock(return_value=True)
    nginx.reload = AsyncMock()
    agent.runtime.running = AsyncMock(return_value=True)
    agent.runtime.validate = AsyncMock(return_value=(True, ""))
    agent.runtime.restart = AsyncMock(side_effect=[RuntimeFailure("restart failed"), None])
    new_cert, new_key = certificate()
    with pytest.raises(RuntimeFailure, match="restart failed"):
        await nginx.handle(
            "POST",
            "/api/child/cert/deploy",
            {**body, "cert_pem": new_cert, "key_pem": new_key, "reload": "both"},
            {},
        )
    assert nginx.cert_path("localhost.pem").read_text() == cert
    assert nginx.cert_path("localhost.key").read_text() == key
    assert nginx.reload.await_count == 2
    assert agent.runtime.restart.await_count == 2


async def test_cancelled_reload_restores_configuration_and_awaits_recovery(agent):
    nginx = agent.operations.nginx
    await seed(nginx)
    nginx.running = AsyncMock(return_value=True)
    old = nginx.main.read_bytes()
    called = 0

    async def reload():
        nonlocal called
        called += 1
        if called == 1:
            await asyncio.sleep(60)

    nginx.reload = reload
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.1):
            await nginx.apply({nginx.main: b"events {} http {}"}, activate=True)
    assert nginx.main.read_bytes() == old
    assert called == 2
    assert not nginx.transaction.record.exists()


async def test_stream_port_cleanup_is_exact_and_preserves_other_blocks(agent):
    nginx = agent.operations.nginx
    file = nginx.config_path("stream_servers/test.conf")
    await nginx.apply(
        {
            nginx.main: b"events {} stream { include stream_servers/*.conf; }",
            file: (
                b"server { listen 443; proxy_pass 127.0.0.1:19000; }\n"
                b"server { listen [::]:443 ssl; proxy_pass 127.0.0.1:19001; }\n"
                b"server { listen 4430; proxy_pass 127.0.0.1:19002; }\n"
            ),
        }
    )
    result = await nginx.handle("POST", "/api/child/nginx/clear-stream-port", {"port": 443}, {})
    assert result["removed"] == 2
    assert len(nginx.parse(file)) == 1
    assert b"4430" in file.read_bytes()
    assert b"19002" in file.read_bytes()


async def test_service_intent_is_separate_and_survives_agent_restart(config):
    agent = Agent(config)
    agent.journal.set_desired_running(True)
    agent.journal.set_desired_running(False, "nginx")
    await agent.close()
    restarted = Agent(config)
    try:
        assert restarted.journal.desired_running(False)
        assert not restarted.journal.desired_running(True, "nginx")
        restarted.journal.set_desired_running(True, "nginx")
        restarted.runtime.running = AsyncMock(return_value=True)
        assert not (await restarted.health_report())["runtime_ready"]
        restarted.operations.nginx.running = AsyncMock(return_value=True)
        assert (await restarted.health_report())["runtime_ready"]
    finally:
        await restarted.close()


async def test_dead_master_cleanup_and_pid_reuse_guard(agent, monkeypatch):
    nginx = agent.operations.nginx
    kill = Mock()
    monkeypatch.setattr("open_node_agent.nginx.os.killpg", kill)
    nginx.process = SimpleNamespace(pid=987654321, returncode=-9)
    nginx.process_started = 100
    monkeypatch.setattr(
        "open_node_agent.nginx.psutil.Process",
        Mock(return_value=SimpleNamespace(create_time=lambda: 200)),
    )
    nginx.kill_owned_group()
    kill.assert_not_called()
    monkeypatch.setattr(
        "open_node_agent.nginx.psutil.Process", Mock(side_effect=psutil.NoSuchProcess(987654321))
    )
    await nginx.stop()
    kill.assert_called_once()
    assert nginx.process is None


def tunnel_body(agent):
    nginx = agent.operations.nginx
    cert, key = certificate()
    atomic_write(nginx.cert_path("localhost.pem"), cert.encode())
    atomic_write(nginx.cert_path("localhost.key"), key.encode())
    current = json.dumps(agent.runtime.read(), sort_keys=True, separators=(",", ":"))
    return {
        "domain": "localhost",
        "cert_name": "localhost",
        "expected_xray_sha256": hashlib.sha256(current.encode()).hexdigest(),
        "nginx_http": [
            directive(
                "map",
                "$http_upgrade",
                "$open_node_connection_upgrade",
                block=[
                    directive("default", "upgrade"),
                    directive("", "close"),
                ],
            ),
            directive("include", "servers/*.conf"),
        ],
        "domain_config": f"server {{ listen 18081; root {nginx.html}; }}",
        "xray_config": {"inbounds": [], "outbounds": [{"protocol": "blackhole"}]},
    }


async def test_native_tunnel_initializes_owned_files_and_persists_start_intent(agent):
    nginx = agent.operations.nginx
    body = tunnel_body(agent)
    nginx.start = AsyncMock()
    agent.runtime.restart = AsyncMock()
    agent.runtime.validate = AsyncMock(return_value=(True, ""))
    result = await nginx.deploy_tunnel(body)
    assert result["success"] and not result["restart_required"]
    assert agent.runtime.read() == body["xray_config"]
    assert (nginx.html / "index.html").read_bytes() == b"Open Node\n"
    assert agent.journal.desired_running(False)
    assert agent.journal.desired_running(False, "nginx")
    nginx.start.assert_awaited_once()
    agent.runtime.restart.assert_awaited_once()


async def test_native_tunnel_preserves_main_and_merges_maps_idempotently(agent):
    nginx = agent.operations.nginx
    await seed(nginx)
    body = tunnel_body(agent)
    nginx.start = AsyncMock()
    agent.runtime.validate = AsyncMock(return_value=(True, ""))
    body["restart_xray"] = False
    first = await nginx.deploy_tunnel(body)
    main = nginx.main.read_bytes()
    assert first["restart_required"]
    body["expected_xray_sha256"] = tunnel_body(agent)["expected_xray_sha256"]
    await nginx.deploy_tunnel(body)
    assert main == nginx.main.read_bytes()
    assert b"listen 18080" in nginx.effective.read_bytes()
    assert main.count(b"include servers/*.conf") == 1
    assert main.count(b"map $http_upgrade") == 1
    assert not agent.journal.desired_running(agent.config.auto_start)
    body["nginx_http"][0]["block"][0]["args"] = ["conflict"]
    with pytest.raises(RuntimeFailure, match="map conflicts"):
        await nginx.deploy_tunnel(body)
    assert main == nginx.main.read_bytes()


@pytest.mark.parametrize("running", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_native_tunnel_failed_activation_restores_files_and_service_intents(
    agent, running, cancel
):
    nginx = agent.operations.nginx
    await seed(nginx)
    body = tunnel_body(agent)
    old_xray, old_main = agent.config.xray_config.read_bytes(), nginx.main.read_bytes()
    agent.journal.set_desired_running(running)
    agent.journal.set_desired_running(running, "nginx")
    nginx.running = AsyncMock(return_value=running)
    agent.runtime.running = AsyncMock(return_value=running)
    nginx.reload = AsyncMock()
    nginx.start = AsyncMock()
    nginx.stop = AsyncMock()
    agent.runtime.validate = AsyncMock(return_value=(True, ""))
    agent.runtime.restart = AsyncMock(
        side_effect=asyncio.CancelledError() if cancel else RuntimeFailure("failed start")
    )
    agent.runtime.start = AsyncMock()
    agent.runtime.stop = AsyncMock()
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeFailure):
        await nginx.deploy_tunnel(body)
    assert agent.config.xray_config.read_bytes() == old_xray
    assert nginx.main.read_bytes() == old_main
    assert not nginx.config_path("servers/localhost.conf").exists()
    assert agent.journal.desired_running(not running) == running
    assert agent.journal.desired_running(not running, "nginx") == running
    assert agent.runtime.start.await_count == int(running)
    assert nginx.start.await_count == 1
    nginx.stop.assert_awaited_once()
    agent.runtime.stop.assert_awaited_once()
    assert not nginx.transaction.record.exists()


async def test_native_tunnel_rejects_stale_snapshot_and_invalid_flags_without_writes(agent):
    nginx = agent.operations.nginx
    body = tunnel_body(agent)
    original = agent.config.xray_config.read_bytes()
    with pytest.raises(RuntimeFailure, match="refresh its snapshot"):
        await nginx.deploy_tunnel({**body, "expected_xray_sha256": "0" * 64})
    with pytest.raises(ValueError, match="bool_type"):
        await nginx.deploy_tunnel({**body, "restart_xray": "false"})
    agent.config.stats_address = "127.0.0.1:12345"
    with pytest.raises(RuntimeFailure, match="stats_address"):
        await nginx.deploy_tunnel(body)
    assert agent.config.xray_config.read_bytes() == original
    assert not nginx.main.exists()


@pytest.mark.parametrize("running", [True, False])
async def test_crashed_coupled_transaction_recovers_files_and_intent_before_start(config, running):
    instance = Agent(config)
    nginx = instance.operations.nginx
    original = config.xray_config.read_bytes()
    nginx.transaction.begin(
        {config.xray_config: b"interrupted", nginx.main: b"invalid"},
        intents={"xray": running, "nginx": running},
    )
    instance.journal.set_desired_running(not running)
    instance.journal.set_desired_running(not running, "nginx")
    await instance.close()
    recovered = Agent(config)
    try:
        assert config.xray_config.read_bytes() == original and not nginx.main.exists()
        assert recovered.journal.desired_running(not running) == running
        assert recovered.journal.desired_running(not running, "nginx") == running
    finally:
        await recovered.close()


async def test_corrupt_intent_metadata_is_rejected_before_file_recovery(agent):
    nginx = agent.operations.nginx
    original = agent.config.xray_config.read_bytes()
    record = nginx.transaction.record
    for intents in (None, {"xray": False}, {"xray": True, "nginx": "false"}):
        atomic_write(
            record,
            json.dumps(
                {
                    "schema": 1,
                    "files": {str(agent.config.xray_config): None},
                    "intents": intents,
                }
            ).encode(),
        )
        with pytest.raises(RuntimeFailure, match="intent undo"):
            nginx.transaction.recover()
        assert agent.config.xray_config.read_bytes() == original
    record.unlink()


async def test_stats_auto_discovers_only_explicit_loopback_api(agent):
    for endpoint in (
        "127.0.0.1:12345",
        "[::1]:12345",
        "0.0.0.0:12345",
        "example.com:12345",
        "127.0.0.1:0",
    ):
        atomic_write(
            agent.config.xray_config,
            json.dumps(
                {
                    "api": {"listen": endpoint, "services": ["StatsService"]},
                }
            ).encode(),
        )
        assert agent.runtime.stats_endpoint() == (
            endpoint if endpoint in {"127.0.0.1:12345", "[::1]:12345"} else None
        )
    agent.config.stats_address = "operator-address:1234"
    assert agent.runtime.stats_endpoint() == "operator-address:1234"


async def test_dynamic_content_and_certificate_paths_cannot_escape_owned_roots(agent):
    nginx = agent.operations.nginx
    with pytest.raises(RuntimeFailure, match="Dynamic"):
        nginx.site_path(str(nginx.html / "$uri"))
    with pytest.raises(RuntimeFailure, match="Dynamic"):
        nginx.cert_path("$ssl_server_name.pem")


def shared_ingress_body(*, website=False):
    body = {
        "listen_port": 443,
        "listen_ipv6": True,
        "routes": [
            {
                "node_id": "11111111-1111-4111-8111-111111111111",
                "profile": "vless-reality-vision",
                "sni": "vision.example.com",
                "upstream_address": "127.0.0.1",
                "upstream_port": 62041,
            },
            {
                "node_id": "22222222-2222-4222-8222-222222222222",
                "profile": "vless-xhttp-reality-xmux",
                "sni": "xhttp.example.com",
                "upstream_address": "::1",
                "upstream_port": 62042,
            },
            {
                "node_id": "33333333-3333-4333-8333-333333333333",
                "profile": "anytls-shadowtls",
                "sni": "anytls.example.com",
                "upstream_address": "127.0.0.1",
                "upstream_port": 62043,
            },
        ],
        "website": None,
    }
    if website:
        body["website"] = {
            "sni": "www.example.com",
            "upstream_url": "https://example.com/app",
            "tls_address": "127.0.0.1",
            "tls_port": 62044,
            "certificate_name": "www.example.com",
            "redirect_http": True,
        }
    return body


def shared_ingress_deployment(configuration, revision=1):
    return {"revision": revision, "configuration": configuration}


async def test_shared_ingress_compiles_three_passthrough_profiles_and_website(agent):
    nginx = agent.operations.nginx
    cert, key = certificate("www.example.com")
    atomic_write(nginx.cert_path("www.example.com.pem"), cert.encode())
    atomic_write(nginx.cert_path("www.example.com.key"), key.encode())
    nginx.start = AsyncMock()

    body = shared_ingress_body(website=True)
    result = await nginx.handle(
        "PUT",
        "/api/child/nginx/shared-ingress",
        shared_ingress_deployment(body),
        {},
    )
    assert result["success"] and result["configuration"] == body
    stream = nginx.shared_ingress_stream.read_text()
    assert stream.count("vision.example.com") == 1
    assert stream.count("xhttp.example.com") == 1
    assert stream.count("anytls.example.com") == 1
    assert "www.example.com" in stream
    assert "ssl_preread on;" in stream
    assert "proxy_pass $open_node_shared_ingress_upstream;" in stream
    assert "listen 0.0.0.0:443;" in stream
    assert "listen 0.0.0.0:443 ssl" not in stream
    assert "127.0.0.1:62041" in stream and "[::1]:62042" in stream

    website = nginx.shared_ingress_website.read_text()
    assert "listen 127.0.0.1:62044 ssl;" in website
    assert "proxy_pass https://example.com/app;" in website
    assert "proxy_ssl_verify on;" in website
    assert "return 308 https://$host$request_uri;" in website
    assert "include stream_servers/*.conf;" in nginx.main.read_text()
    assert "$open_node_shared_connection_upgrade" in nginx.main.read_text()
    assert nginx.shared_ingress_state()["configuration"] == body
    assert nginx.shared_ingress_state()["revision"] == 1

    # Reapplying the same declaration is intentionally idempotent.
    await nginx.handle(
        "PUT",
        "/api/child/nginx/shared-ingress",
        shared_ingress_deployment(body),
        {},
    )
    assert nginx.main.read_text().count("$open_node_shared_connection_upgrade") == 1

    deleted = await nginx.handle("DELETE", "/api/child/nginx/shared-ingress", {"revision": 2}, {})
    assert deleted["success"] and deleted["configuration"] is None
    assert not nginx.shared_ingress_stream.exists()
    assert not nginx.shared_ingress_website.exists()
    assert nginx.shared_ingress_state()["configuration"] is None
    assert nginx.shared_ingress_state()["revision"] == 2
    with pytest.raises(RuntimeFailure, match="stale"):
        await nginx.handle(
            "PUT",
            "/api/child/nginx/shared-ingress",
            shared_ingress_deployment(body),
            {},
        )

    # Leave one compiled candidate available to real-Nginx smoke jobs.
    await nginx.handle(
        "PUT",
        "/api/child/nginx/shared-ingress",
        shared_ingress_deployment(body, revision=3),
        {},
    )


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda body: body.update({"listen_port": 444}), "literal_error"),
        (
            lambda body: body["routes"][0].update({"upstream_address": "0.0.0.0"}),
            "literal_error",
        ),
        (
            lambda body: body["routes"][0].update({"upstream_port": 443}),
            "greater_than_equal",
        ),
        (
            lambda body: body["routes"][1].update({"sni": body["routes"][0]["sni"]}),
            "duplicate SNI",
        ),
        (
            lambda body: body["routes"][1].update(
                {"upstream_port": body["routes"][0]["upstream_port"]}
            ),
            "duplicate internal port",
        ),
    ],
)
async def test_shared_ingress_rejects_unsafe_or_ambiguous_routes_without_writes(
    agent, mutate, message
):
    nginx = agent.operations.nginx
    body = shared_ingress_body()
    mutate(body)
    with pytest.raises(ValueError, match=message):
        await nginx.handle(
            "PUT",
            "/api/child/nginx/shared-ingress",
            shared_ingress_deployment(body),
            {},
        )
    assert not nginx.shared_ingress_stream.exists()
    assert not nginx.shared_ingress_declaration.exists()


async def test_shared_ingress_rejects_url_injection_and_competing_443(agent):
    nginx = agent.operations.nginx
    injected = shared_ingress_body(website=True)
    injected["website"]["upstream_url"] = "https://example.net/;include=/etc/passwd"
    with pytest.raises(ValueError, match="unsafe characters"):
        await nginx.handle(
            "PUT",
            "/api/child/nginx/shared-ingress",
            shared_ingress_deployment(injected),
            {},
        )

    await nginx.apply(
        {
            nginx.main: b"events {} stream { include stream_servers/*.conf; }",
            nginx.config_path("stream_servers/existing.conf"): (
                b"server { listen 443; proxy_pass 127.0.0.1:62090; }\n"
            ),
        }
    )
    with pytest.raises(RuntimeFailure, match="already declared"):
        await nginx.handle(
            "PUT",
            "/api/child/nginx/shared-ingress",
            shared_ingress_deployment(shared_ingress_body()),
            {},
        )
    assert not nginx.shared_ingress_stream.exists()


async def test_shared_ingress_activation_failure_rolls_back_all_managed_files(agent):
    nginx = agent.operations.nginx
    nginx.start = AsyncMock()
    original = shared_ingress_body()
    await nginx.handle(
        "PUT",
        "/api/child/nginx/shared-ingress",
        shared_ingress_deployment(original),
        {},
    )
    old_main = nginx.main.read_bytes()
    old_stream = nginx.shared_ingress_stream.read_bytes()
    old_state = nginx.shared_ingress_declaration.read_bytes()

    replacement = shared_ingress_body()
    replacement["routes"][0]["sni"] = "replacement.example.com"
    replacement["routes"][0]["upstream_port"] = 62141
    nginx.running = AsyncMock(return_value=True)
    nginx.reload = AsyncMock(side_effect=[RuntimeFailure("reload failed"), None])
    with pytest.raises(RuntimeFailure, match="reload failed"):
        await nginx.handle(
            "PUT",
            "/api/child/nginx/shared-ingress",
            shared_ingress_deployment(replacement, revision=2),
            {},
        )
    assert nginx.main.read_bytes() == old_main
    assert nginx.shared_ingress_stream.read_bytes() == old_stream
    assert nginx.shared_ingress_declaration.read_bytes() == old_state
    assert nginx.reload.await_count == 2
    assert not nginx.transaction.record.exists()
