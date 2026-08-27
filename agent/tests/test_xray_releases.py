import asyncio
import hashlib
import io
import stat
import zipfile
from unittest.mock import AsyncMock

import httpx
import pytest
from open_node_agent import xray_releases as releases
from open_node_agent.client import Agent
from open_node_agent.runtime import RuntimeFailure


def archive_bytes(version="v26.3.27", *, name="xray", mode=stat.S_IFREG | 0o700):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        info = zipfile.ZipInfo(name)
        info.external_attr = mode << 16
        archive.writestr(info, f"#!/bin/sh\necho 'Xray {version[1:]} fixture'\n".encode())
    return content.getvalue()


def spec_for(data, version="v26.3.27"):
    return releases.ReleaseSpec(version=version, sha256=hashlib.sha256(data).hexdigest())


def cache(config, tmp_path, version="v26.3.27"):
    data = archive_bytes(version)
    spec = spec_for(data, version)
    root = config.state_dir / "xray-releases" / spec.sha256
    root.mkdir(parents=True, mode=0o700)
    archive = tmp_path / (version + ".zip")
    archive.write_bytes(data)
    releases.extract_release(archive, root, spec)
    return spec


def mock_download(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(
        releases.httpx,
        "AsyncClient",
        lambda **kwargs: client(
            **kwargs,
            transport=httpx.MockTransport(handler),
        ),
    )


def test_release_requests_require_supported_architecture_and_checksum(monkeypatch):
    monkeypatch.setattr(releases.platform, "system", lambda: "Linux")
    monkeypatch.setattr(releases.platform, "machine", lambda: "x86_64")
    assert (
        releases.InstallRequest().release().sha256 == releases.PINNED_RELEASES["v26.3.27"]["x86_64"]
    )
    with pytest.raises(RuntimeFailure, match="explicit"):
        releases.InstallRequest(version="v26.4.1").release()
    with pytest.raises(ValueError):
        releases.InstallRequest(version="../../outside")
    with pytest.raises(ValueError):
        releases.InstallRequest(version="v26.3.27", start="true")
    monkeypatch.setattr(releases.platform, "machine", lambda: "unsupported")
    with pytest.raises(RuntimeFailure, match="amd64 and arm64"):
        releases.InstallRequest().release()


@pytest.mark.parametrize(
    "name,mode",
    [
        ("../xray", stat.S_IFREG | 0o700),
        ("/xray", stat.S_IFREG | 0o700),
        ("xray", stat.S_IFLNK | 0o777),
        ("xray", stat.S_IFIFO | 0o600),
        ("unexpected", stat.S_IFREG | 0o700),
    ],
)
def test_archive_rejects_unowned_paths_and_special_files(tmp_path, name, mode):
    data = archive_bytes(name=name, mode=mode)
    archive = tmp_path / "input.zip"
    archive.write_bytes(data)
    target = tmp_path / "release"
    target.mkdir()
    with pytest.raises(RuntimeFailure):
        releases.extract_release(archive, target, spec_for(data))
    assert not (tmp_path / "xray").exists()


def test_archive_rejects_duplicate_names_and_size_limits(tmp_path, monkeypatch):
    archive = tmp_path / "input.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("xray", "first")
        with pytest.warns(UserWarning, match="Duplicate"):
            output.writestr("xray", "second")
    target = tmp_path / "release"
    target.mkdir()
    with pytest.raises(RuntimeFailure):
        releases.extract_release(archive, target, spec_for(archive.read_bytes()))
    (target / "xray").unlink()
    monkeypatch.setattr(releases, "MAX_FILE_BYTES", 2)
    with pytest.raises(RuntimeFailure):
        releases.extract_release(archive, target, spec_for(archive.read_bytes()))


async def test_download_checks_digest_and_redirect_hosts(tmp_path, monkeypatch):
    data = archive_bytes()
    spec = spec_for(data)
    mock_download(monkeypatch, lambda request: httpx.Response(200, content=data))
    await releases.download_release(spec, tmp_path / "valid.zip")
    assert (tmp_path / "valid.zip").read_bytes() == data
    with pytest.raises(RuntimeFailure, match="SHA-256"):
        await releases.download_release(
            releases.ReleaseSpec(version=spec.version, sha256="0" * 64),
            tmp_path / "bad.zip",
        )


@pytest.mark.parametrize(
    "destination",
    [
        "http://github.com/archive",
        "https://example.invalid/archive",
        "https://github.com:444/archive",
    ],
)
async def test_download_rejects_untrusted_redirect(tmp_path, monkeypatch, destination):
    mock_download(
        monkeypatch, lambda request: httpx.Response(302, headers={"Location": destination})
    )
    with pytest.raises(RuntimeFailure, match="official release hosts"):
        await releases.download_release(spec_for(archive_bytes()), tmp_path / "input.zip")
    assert not (tmp_path / "input.zip").exists()


async def test_download_is_size_bounded(tmp_path, monkeypatch):
    data = archive_bytes()
    monkeypatch.setattr(releases, "MAX_ARCHIVE_BYTES", 8)
    mock_download(monkeypatch, lambda request: httpx.Response(200, content=data))
    with pytest.raises(RuntimeFailure, match="download limit"):
        await releases.download_release(spec_for(data), tmp_path / "input.zip")


async def test_prepare_verifies_version_and_reuses_a_verified_cache(config, tmp_path, monkeypatch):
    data = archive_bytes()
    spec = spec_for(data)
    mock_download(monkeypatch, lambda request: httpx.Response(200, content=data))
    agent = Agent(config)
    try:
        binary = await agent.operations.releases.prepare(spec)
        assert binary == releases.release_binary(config.state_dir, spec)
        assert binary.stat().st_mode & 0o777 == 0o700
        assert not list(binary.parent.glob("*.zip"))
        assert not list(binary.parent.parent.glob(".download-*"))
        monkeypatch.setattr(
            releases,
            "download_release",
            AsyncMock(side_effect=AssertionError("cache must be reused")),
        )
        assert await agent.operations.releases.prepare(spec) == binary
        binary.write_bytes(b"tampered")
        with pytest.raises(RuntimeFailure, match="integrity"):
            await agent.operations.releases.prepare(spec)
    finally:
        await agent.close()


async def test_prepare_refuses_mismatched_version_without_selecting_it(config, monkeypatch):
    data = archive_bytes("v26.2.6")
    spec = spec_for(data, "v26.3.27")
    mock_download(monkeypatch, lambda request: httpx.Response(200, content=data))
    agent = Agent(config)
    try:
        with pytest.raises(RuntimeFailure, match="requested version"):
            await agent.operations.releases.prepare(spec)
        assert not (config.state_dir / "xray-release.json").exists()
        assert not list((config.state_dir / "xray-releases").iterdir())
    finally:
        await agent.close()


async def test_stopped_upgrade_and_rollback_persist_across_restart(config, tmp_path):
    agent = Agent(config)
    first = cache(config, tmp_path, "v26.2.6")
    second = cache(config, tmp_path)
    original = config.xray_config.read_bytes()
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    try:
        for spec in (first, second):
            result = await agent.operations.releases.install(spec.model_dump())
            assert result["installed"] and not result["running"]
            assert result["release"] == spec.model_dump()
        assert config.xray_config.read_bytes() == original
    finally:
        await agent.close()
    restarted = Agent(config)
    restarted.runtime.validate = AsyncMock(return_value=(True, "valid"))
    try:
        assert restarted.runtime.binary.parent.name == second.sha256
        result = await restarted.operations.releases.rollback()
        assert result["release"] == first.model_dump() and not result["running"]
        assert not restarted.journal.desired_running(True)
        removed = await restarted.operations.releases.remove()
        assert not removed["enabled"] and not removed["running"]
        assert config.xray_config.read_bytes() == original
        with pytest.raises(RuntimeFailure, match="removed"):
            await restarted.runtime.start()
    finally:
        await restarted.close()


async def test_failed_candidate_validation_preserves_selection_and_config(config, tmp_path):
    agent = Agent(config)
    spec = cache(config, tmp_path)
    original = config.xray_config.read_bytes()
    agent.runtime.validate = AsyncMock(return_value=(False, "unsupported configuration"))
    try:
        with pytest.raises(RuntimeFailure, match="rejected"):
            await agent.operations.releases.install(spec.model_dump())
        assert config.xray_config.read_bytes() == original
        assert not (config.state_dir / "xray-release.json").exists()
        assert agent.runtime.binary == config.xray_binary
    finally:
        await agent.close()


async def test_failed_start_restores_original_binary_and_running_intent(config, tmp_path):
    agent = Agent(config)
    spec = cache(config, tmp_path)
    agent.journal.set_desired_running(True)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    agent.runtime.running = AsyncMock(return_value=True)
    agent.runtime.stop = AsyncMock()
    agent.runtime.start = AsyncMock(side_effect=[RuntimeFailure("startup failed"), None])
    try:
        with pytest.raises(RuntimeFailure, match="startup failed"):
            await agent.operations.releases.install(spec.model_dump())
        assert agent.runtime.binary == config.xray_binary
        assert agent.journal.desired_running(False)
        assert agent.runtime.start.await_count == 2
        assert not (config.state_dir / "xray-release-transaction.json").exists()
    finally:
        await agent.close()


async def test_cancellation_waits_for_runtime_recovery(config, tmp_path):
    agent = Agent(config)
    spec = cache(config, tmp_path)
    entered = asyncio.Event()
    agent.journal.set_desired_running(True)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    agent.runtime.running = AsyncMock(return_value=True)
    agent.runtime.stop = AsyncMock()

    async def start():
        if agent.runtime.binary != config.xray_binary:
            entered.set()
            await asyncio.Event().wait()

    agent.runtime.start = AsyncMock(side_effect=start)
    try:
        task = asyncio.create_task(agent.operations.releases.install(spec.model_dump()))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert agent.runtime.binary == config.xray_binary
        assert agent.journal.desired_running(False)
        assert not (config.state_dir / "xray-release-transaction.json").exists()
    finally:
        await agent.close()


async def test_interrupted_switch_restores_files_and_intents_before_startup(config, tmp_path):
    agent = Agent(config)
    spec = cache(config, tmp_path)
    original = config.xray_config.read_bytes()
    state = releases.ReleaseState(current=releases.Selection(release=spec))
    agent.operations.releases.transaction.begin(
        {
            config.state_dir / "xray-release.json": state.model_dump_json().encode(),
            config.xray_config: b'{"inbounds":[]}',
        },
        intents={"xray": False, "nginx": False},
    )
    agent.journal.set_desired_running(True)
    await agent.close()
    restarted = Agent(config)
    try:
        assert restarted.runtime.binary == config.xray_binary
        assert config.xray_config.read_bytes() == original
        assert not restarted.journal.desired_running(True)
        assert not (config.state_dir / "xray-release-transaction.json").exists()
    finally:
        await restarted.close()


async def test_external_systemd_runtime_is_not_replaced(config):
    config.runtime_mode = "systemd"
    agent = Agent(config)
    try:
        with pytest.raises(RuntimeFailure, match="managed runtime"):
            await agent.operations.releases.install({})
        assert not (config.state_dir / "xray-release.json").exists()
    finally:
        await agent.close()


async def test_same_release_keeps_previous_selection_without_restarting(config, tmp_path):
    agent = Agent(config)
    first = cache(config, tmp_path, "v26.2.6")
    second = cache(config, tmp_path)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    try:
        for spec in (first, second):
            await agent.operations.releases.install(spec.model_dump())
        agent.runtime.stop = AsyncMock()
        await agent.operations.releases.install(second.model_dump())
        agent.runtime.stop.assert_not_awaited()
        assert releases.read_state(config.state_dir).previous.release == first
    finally:
        await agent.close()


async def test_first_install_initializes_only_a_missing_configuration(config, tmp_path):
    config.xray_config.unlink()
    agent = Agent(config)
    spec = cache(config, tmp_path)
    agent.runtime.validate = AsyncMock(return_value=(True, "valid"))
    try:
        await agent.operations.releases.install(spec.model_dump())
        assert agent.runtime.read() == {
            "inbounds": [],
            "outbounds": [{"protocol": "freedom", "tag": "direct"}],
        }
        assert config.xray_config.stat().st_mode & 0o777 == 0o600
    finally:
        await agent.close()


async def test_unresolved_transaction_cannot_be_hidden_by_reinstalling_same_release(
    config, tmp_path
):
    agent = Agent(config)
    spec = cache(config, tmp_path)
    selection = releases.ReleaseState(current=releases.Selection(release=spec))
    agent.operations.releases.transaction.begin(
        {
            config.state_dir / "xray-release.json": selection.model_dump_json().encode(),
        },
        intents={"xray": False, "nginx": False},
    )
    try:
        with pytest.raises(RuntimeFailure, match="requires recovery"):
            await agent.operations.releases.install(spec.model_dump())
        assert (config.state_dir / "xray-release-transaction.json").exists()
    finally:
        agent.operations.releases.transaction.recover()
        await agent.close()


async def test_remove_does_not_require_a_valid_configuration(config):
    agent = Agent(config)
    config.xray_config.write_bytes(b"damaged configuration")
    agent.runtime.stop = AsyncMock()
    try:
        result = await agent.operations.releases.remove()
        assert not result["enabled"]
        agent.runtime.stop.assert_awaited_once()
        assert config.xray_config.read_bytes() == b"damaged configuration"
    finally:
        await agent.close()
