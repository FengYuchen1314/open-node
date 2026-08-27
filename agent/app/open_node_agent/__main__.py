import argparse
import asyncio
import contextlib
import logging
import os
import signal
from pathlib import Path

from open_node_agent import __version__
from open_node_agent.client import Agent
from open_node_agent.config import load_config
from open_node_agent.runtime import RuntimeFailure, run_command
from open_node_agent.xray_releases import read_state, selected_binary


async def run(config):
    agent = Agent(config)
    task = asyncio.create_task(agent.run())
    loop = asyncio.get_running_loop()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        loop.add_signal_handler(sig, task.cancel)
    try:
        with contextlib.suppress(asyncio.CancelledError):
            await task
    finally:
        await agent.close()


def main():
    parser = argparse.ArgumentParser(description="Open Node Agent")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, default=Path("/etc/open-node-agent/config.yaml"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--check-runtime", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        config = load_config(args.config)
        if args.check_runtime:
            if (config.state_dir / "xray-release-transaction.json").exists():
                raise RuntimeFailure("Xray release switch is unresolved; wait for Agent recovery")
            if not read_state(config.state_dir).current.enabled:
                print("Xray is removed; no runtime process is required")
                return
            code, output = asyncio.run(
                run_command(
                    str(selected_binary(config)),
                    "run",
                    "-test",
                    "-config",
                    str(config.xray_config),
                )
            )
            if code:
                raise RuntimeFailure("Selected Xray configuration check failed: " + output[-2000:])
            print("Selected Xray runtime and configuration are valid")
            return
        if args.check:
            print("Agent configuration is valid")
            return
        asyncio.run(run(config))
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Agent stopped: {exc}\n")


if __name__ == "__main__":
    main()
