import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_production_container_disables_uvicorn_access_log():
    dockerfile = (ROOT / "Dockerfile").read_text()
    command_line = next(line for line in dockerfile.splitlines() if line.startswith("CMD ["))
    command = json.loads(command_line.removeprefix("CMD "))

    assert command[:2] == ["uvicorn", "open_node.main:app"]
    assert "--no-access-log" in command


def test_nginx_example_disables_access_logs_for_redirect_and_tls_servers():
    nginx = (ROOT / "deploy/nginx.conf.example").read_text()
    servers = nginx.split("server {")[1:]

    assert len(servers) == 2
    assert all("access_log off;" in server for server in servers)
    assert all("error_log /var/log/nginx/open-node-error.log crit;" in server for server in servers)
    tls_server = servers[1]
    assert 'location ~ "^/x(?:/|$)"' in tls_server
    assert 'location ~ "^/api/v1/subscribe/(?![A-Za-z0-9_-]{43}$)"' in tls_server


def test_compose_bounds_local_container_logs():
    compose = yaml.safe_load((ROOT / "deploy/compose.yaml").read_text())
    logging = compose["services"]["open-node"]["logging"]

    assert logging == {
        "driver": "local",
        "options": {"max-size": "10m", "max-file": "5"},
    }
