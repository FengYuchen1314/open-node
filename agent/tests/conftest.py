import json

import pytest
from open_node_agent.config import AgentConfig


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "xray.json"
    path.write_text(
        json.dumps({"inbounds": [], "outbounds": [{"tag": "direct", "protocol": "freedom"}]})
    )
    path.chmod(0o600)
    return AgentConfig(
        master_url="https://control.example",
        token="test-node-token",
        state_dir=tmp_path / "state",
        xray_config=path,
        auto_start=False,
    )
