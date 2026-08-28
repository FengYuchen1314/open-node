import base64
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from open_node_agent.journal import CommandJournal
from open_node_agent.runtime import RuntimeFailure, XrayRuntime, atomic_write
from open_node_agent.systemd_runtime import Binding, ConfigLayout, config_layout
from open_node_agent.xray_takeover import EMPTY, XrayTakeover


@pytest.fixture
def takeover(config):
    config.runtime_mode = "systemd"
    config.allow_xray_takeover = True
    extra = config.xray_config.with_name("extra.json")
    atomic_write(extra, b'{"inbounds": [{"tag": "overlay", "password": "fixture-password"}]}\n')
    runtime = XrayRuntime(config)
    journal = CommandJournal(config.state_dir)
    adapter = runtime.systemd
    layout = ConfigLayout(
        (str(config.xray_binary), "run", "-c", str(config.xray_config), "-c", str(extra)),
        (config.xray_config, extra),
        None,
    )
    binding = Binding(True, {}, "/", layout, "a" * 64)
    adapter.inspect = AsyncMock(side_effect=lambda **kwargs: replace(binding))
    adapter.layout = layout
    operation = XrayTakeover(runtime, journal)
    merged = {
        "inbounds": [{"tag": "overlay", "password": "fixture-password"}],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
    }

    async def native(binding, args, *, dump=False):
        return merged if dump else None

    async def control(action):
        binding.running = action == "start"

    operation.native = AsyncMock(side_effect=native)
    operation.control = AsyncMock(side_effect=control)
    yield operation, binding, extra, merged
    runtime.log_handler.close()
    journal.close()


async def test_preview_is_secret_free_and_does_not_write_or_stop(takeover):
    operation, binding, _, _ = takeover
    before = operation.snapshot(binding)
    result = await operation.handle({"preview": True})
    assert result["preview"] and result["merged_files"] == 1
    assert len(result["source_sha256"]) == 64
    assert "fixture-password" not in json.dumps(result)
    assert operation.snapshot(binding) == before
    operation.control.assert_not_awaited()
    assert not operation.path.exists()


@pytest.mark.parametrize(
    "body", [{}, {"confirm": False}, {"confirm": "yes"}, {"preview": 1}, {"path": "/other"}]
)
async def test_takeover_requires_explicit_confirmation(takeover, body):
    operation, _, _, _ = takeover
    with pytest.raises(RuntimeFailure):
        await operation.handle(body)
    operation.native.assert_not_awaited()


async def test_native_merge_writes_primary_neutralizes_fragments_and_keeps_backup(takeover):
    operation, binding, extra, merged = takeover
    before = operation.snapshot(binding)
    preview = await operation.handle({"preview": True})
    result = await operation.handle({"confirm": True, "expected_sha256": preview["source_sha256"]})
    assert result["restarted"] and result["last_phase"] == "complete"
    assert json.loads(operation.config.xray_config.read_bytes()) == merged
    assert extra.read_bytes() == EMPTY
    assert [item.args[0] for item in operation.control.await_args_list] == ["stop", "start"]
    saved = operation.load()
    assert {name: base64.b64decode(raw) for name, raw in saved["files"].items()} == before
    backup = operation.config.state_dir / "xray-takeover-backups" / (saved["id"] + ".json")
    assert backup.stat().st_mode & 0o777 == 0o600
    assert not operation.adapter.pending_takeover
    operation.control.reset_mock()
    assert (await operation.handle({"confirm": True}))["unchanged"]
    operation.control.assert_not_awaited()


async def test_stopped_source_stays_stopped_and_remembers_intent(takeover):
    operation, binding, _, _ = takeover
    binding.running = False
    operation.journal.set_desired_running(True)
    result = await operation.handle({"confirm": True})
    assert not result["restarted"]
    assert not operation.journal.desired_running(True)
    operation.control.assert_not_awaited()


async def test_stale_preview_and_native_failure_leave_sources_untouched(takeover):
    operation, binding, _, _ = takeover
    before = operation.snapshot(binding)
    with pytest.raises(RuntimeFailure, match="stale"):
        await operation.handle({"confirm": True, "expected_sha256": "0" * 64})
    operation.native.side_effect = RuntimeFailure("Invalid original config")
    with pytest.raises(RuntimeFailure, match="Invalid original"):
        await operation.handle({"confirm": True})
    assert operation.snapshot(binding) == before
    operation.control.assert_not_awaited()
    assert not operation.path.exists()


async def test_failed_start_restores_exact_sources_and_runtime(takeover):
    operation, binding, _, _ = takeover
    before = operation.snapshot(binding)
    failed = False

    async def control(action):
        nonlocal failed
        if action == "start" and not failed:
            failed = True
            raise RuntimeFailure("Startup rejected")
        binding.running = action == "start"

    operation.control.side_effect = control
    with pytest.raises(RuntimeFailure, match="Startup rejected"):
        await operation.handle({"confirm": True})
    assert operation.snapshot(binding) == before
    assert binding.running
    assert operation.state["phase"] == "rolled_back"


async def test_changed_inputs_during_preview_are_rejected(takeover):
    operation, _, extra, merged = takeover

    async def native(binding, args, *, dump=False):
        atomic_write(extra, b'{"independent": true}')
        return merged if dump else None

    operation.native.side_effect = native
    with pytest.raises(RuntimeFailure, match="changed during takeover"):
        await operation.handle({"confirm": True})
    assert extra.read_bytes() == b'{"independent": true}'
    operation.control.assert_not_awaited()


@pytest.mark.parametrize("phase", ["prepared", "stopping", "writing", "activating", "restoring"])
async def test_persisted_incomplete_takeover_recovers_after_agent_restart(takeover, phase):
    operation, binding, _, _ = takeover
    before = operation.snapshot(binding)
    await operation.handle({"confirm": True})
    operation.save(phase)
    fresh = XrayTakeover(operation.runtime, operation.journal)
    fresh.native, fresh.control = operation.native, operation.control
    assert fresh.adapter.pending_takeover
    await fresh.recover()
    assert fresh.snapshot(binding) == before
    assert fresh.state["phase"] == "rolled_back"
    assert not fresh.adapter.pending_takeover


async def test_recovery_never_overwrites_independent_changes(takeover):
    operation, binding, extra, _ = takeover
    await operation.handle({"confirm": True})
    operation.save("writing")
    atomic_write(extra, b'{"independently_changed": true}')
    before = operation.snapshot(binding)
    operation.control.reset_mock()
    await operation.recover()
    assert operation.adapter.pending_takeover
    assert operation.snapshot(binding) == before
    operation.control.assert_not_awaited()


async def test_preview_never_triggers_pending_recovery(takeover):
    operation, binding, _, _ = takeover
    await operation.handle({"confirm": True})
    operation.save("writing")
    before = operation.snapshot(binding)
    journal = operation.path.read_bytes()
    operation.control.reset_mock()
    with pytest.raises(RuntimeFailure, match="recovery is pending"):
        await operation.handle({"preview": True})
    assert operation.snapshot(binding) == before
    assert operation.path.read_bytes() == journal
    operation.control.assert_not_awaited()


@pytest.mark.parametrize("phase", ["complete", "rolled_back"])
async def test_terminal_backup_precedes_active_journal_commit(takeover, monkeypatch, phase):
    operation, _, _, _ = takeover
    await operation.handle({"confirm": True})
    operation.save("writing")
    backup = (
        operation.config.state_dir / "xray-takeover-backups" / (operation.state["id"] + ".json")
    )
    backup.unlink()

    def fail_commit(path, raw):
        if path == operation.path:
            assert json.loads(backup.read_bytes())["phase"] == phase
            raise OSError("Interrupted before terminal journal commit")
        atomic_write(path, raw)

    monkeypatch.setattr("open_node_agent.xray_takeover.atomic_write", fail_commit)
    with pytest.raises(OSError, match="Interrupted"):
        operation.save(phase)
    fresh = XrayTakeover(operation.runtime, operation.journal)
    assert fresh.state["phase"] == "writing"
    assert fresh.adapter.pending_takeover


async def test_oversized_consolidated_config_is_rejected_before_writes(takeover):
    operation, binding, _, merged = takeover
    merged["large"] = "x" * (2 * 1024 * 1024)
    before = operation.snapshot(binding)
    with pytest.raises(RuntimeFailure, match="configuration exceeds"):
        await operation.handle({"confirm": True})
    assert operation.snapshot(binding) == before
    operation.control.assert_not_awaited()
    assert not operation.path.exists()


async def test_native_round_trip_mismatch_is_rejected(takeover):
    operation, _, _, _ = takeover
    operation.native.side_effect = [{"inbounds": []}, None, None, {"outbounds": []}]
    with pytest.raises(RuntimeFailure, match="round-trip"):
        await operation.handle({"confirm": True})
    operation.control.assert_not_awaited()


def test_layout_preserves_cli_order_then_sorted_directory(config):
    directory = config.xray_config.parent / "fragments"
    directory.mkdir()
    for name in ("30_tail.jsonc", "10_first.json", "ignored.txt", "UPPER.JSON"):
        (directory / name).write_text("{}")
    argv = [
        str(config.xray_binary),
        "run",
        "--config=" + str(config.xray_config),
        "-confdir",
        str(directory),
    ]
    layout = config_layout(argv, config.xray_binary, config.xray_config, allow_multiple=True)
    assert layout.files == (
        config.xray_config,
        directory / "10_first.json",
        directory / "30_tail.jsonc",
    )
    assert layout.argv == tuple(argv)
    assert (directory / "ignored.txt").exists()


def test_confdir_only_requires_existing_selected_target(config):
    directory = config.xray_config.parent
    layout = config_layout(
        [str(config.xray_binary), "run", "-confdir", str(directory)],
        config.xray_binary,
        config.xray_config,
        allow_multiple=True,
    )
    assert layout.files == (config.xray_config,)


def test_default_single_file_keeps_explicit_json_with_non_json_filename(config):
    target = config.xray_config.with_name("xray.conf")
    argv = [str(config.xray_binary), "run", "-config", str(target), "-format", "json"]
    assert config_layout(argv, config.xray_binary, target).files == (target,)


@pytest.mark.parametrize("option", ["-c", "--config", "-confdir"])
def test_multifile_requires_host_opt_in(config, option):
    with pytest.raises(RuntimeFailure):
        config_layout(
            [
                str(config.xray_binary),
                "run",
                "-c",
                str(config.xray_config),
                option,
                str(config.xray_config),
            ],
            config.xray_binary,
            config.xray_config,
        )


def test_default_multi_layout_guard_blocks_unmerged_fragments(takeover):
    operation, _, extra, _ = takeover
    with pytest.raises(RuntimeFailure, match="active config fragments"):
        operation.adapter.read_config()
    atomic_write(extra, EMPTY)
    assert operation.adapter.read_config()
    operation.adapter.pending_takeover = True
    with pytest.raises(RuntimeFailure, match="recovery is pending"):
        operation.adapter.read_config()


async def test_jsonc_target_is_normalized_instead_of_returning_false_noop(takeover):
    operation, _, extra, merged = takeover
    atomic_write(extra, EMPTY)
    atomic_write(operation.config.xray_config, b"// Native JSONC\n{}\n")
    with pytest.raises(RuntimeFailure, match="native JSON consolidation"):
        operation.adapter.read_config()
    result = await operation.handle({"confirm": True})
    assert not result.get("unchanged")
    assert json.loads(operation.adapter.read_config()) == merged


@pytest.mark.parametrize("kind", ["symlink", "hardlink"])
async def test_unsafe_source_file_is_rejected_before_merge(takeover, kind):
    operation, _, extra, _ = takeover
    extra.unlink()
    if kind == "symlink":
        extra.symlink_to(operation.config.xray_config)
    else:
        extra.hardlink_to(operation.config.xray_config)
    with pytest.raises(RuntimeFailure):
        await operation.handle({"confirm": True})
    operation.native.assert_not_awaited()
    operation.control.assert_not_awaited()


async def test_source_size_is_bounded_before_native_process(takeover):
    operation, _, extra, _ = takeover
    atomic_write(extra, b" " * (2 * 1024 * 1024 + 1))
    with pytest.raises(RuntimeFailure, match="exceeds"):
        await operation.handle({"confirm": True})
    operation.native.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [("id", "../outside"), ("running", "false"), ("identity", "bad"), ("target", "/elsewhere")],
)
async def test_invalid_durable_metadata_is_rejected(takeover, field, value):
    operation, _, _, _ = takeover
    await operation.handle({"confirm": True})
    state = operation.load()
    state[field] = value
    atomic_write(operation.path, json.dumps(state).encode())
    with pytest.raises(RuntimeFailure, match="journal does not match"):
        XrayTakeover(operation.runtime, operation.journal)
