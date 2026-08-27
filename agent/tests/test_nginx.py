import asyncio
from datetime import UTC, datetime, timedelta
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
from open_node_agent.runtime import RuntimeFailure, atomic_write


def certificate(domain="localhost", *, expired=False, not_yet_valid=False):
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
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(domain)]), critical=False)
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
