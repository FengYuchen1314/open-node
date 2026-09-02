import sys
from collections import Counter

import pytest
from conftest import authenticated_client
from fastapi.testclient import TestClient
from open_node.services.camouflage_pools import (
    CamouflagePoolError,
    catalog,
    get_pool,
    validate_pool_id,
)


def test_catalog_has_three_disjoint_verified_pools_for_every_region():
    value = catalog()
    counts = Counter(pool.region for pool in value.pools)
    assert counts == {
        "los-angeles": 3,
        "san-jose": 3,
        "tokyo": 3,
        "singapore": 3,
        "germany": 3,
        "united-kingdom": 3,
        "netherlands": 3,
    }
    assert len({pool.id for pool in value.pools}) == 21
    assert len({pool.server_name for pool in value.pools}) == 21
    assert all(pool.target == f"{pool.server_name}:443" for pool in value.pools)
    assert all(
        pool.tls_version == "TLSv1.3"
        and pool.alpn == "h2"
        and pool.cloudflare is False
        and pool.gfw_verdict == "not_blocked"
        for pool in value.pools
    )


def test_catalog_lookup_fails_closed():
    assert get_pool("tokyo-sony").server_name == "www.sony.jp"
    assert validate_pool_id("tokyo-sony") == "tokyo-sony"
    with pytest.raises(CamouflagePoolError, match="required"):
        validate_pool_id(None)
    with pytest.raises(CamouflagePoolError, match="Unknown"):
        validate_pool_id("does-not-exist")


@pytest.mark.skipif(sys.platform == "win32", reason="application lock uses fcntl")
def test_catalog_api_requires_admin_and_filters_region(tmp_path):
    from open_node.core.config import Settings
    from open_node.main import create_app

    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'camouflage.db'}"))
    anonymous = TestClient(app, base_url="https://testserver")
    client = authenticated_client(app)
    assert anonymous.get("/api/v1/camouflage-pools").status_code == 401
    response = client.get("/api/v1/camouflage-pools?region=singapore")
    assert response.status_code == 200
    body = response.json()
    assert body["license_required"] is False
    assert len(body["pools"]) == 3
    assert {pool["region"] for pool in body["pools"]} == {"singapore"}
    assert client.get("/api/v1/camouflage-pools?region=moon").status_code == 422
