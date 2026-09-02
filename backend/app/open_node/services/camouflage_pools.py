import json
import re
from functools import lru_cache
from importlib.resources import files

from pydantic import TypeAdapter, ValidationError

from open_node.domain.camouflage import CamouflagePoolCatalogRead, CamouflagePoolRead

EXPECTED_REGIONS = {
    "los-angeles",
    "san-jose",
    "tokyo",
    "singapore",
    "germany",
    "united-kingdom",
    "netherlands",
}
HOSTNAME = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z"
)


class CamouflagePoolError(ValueError):
    pass


@lru_cache(maxsize=1)
def catalog() -> CamouflagePoolCatalogRead:
    resource = files("open_node.resources").joinpath("camouflage-pools.json")
    try:
        raw = json.loads(resource.read_text(encoding="utf-8"))
        value = TypeAdapter(CamouflagePoolCatalogRead).validate_python(raw, strict=True)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError("The camouflage pool catalog is invalid") from exc
    identifiers: set[str] = set()
    names: set[str] = set()
    counts = {region: 0 for region in EXPECTED_REGIONS}
    for pool in value.pools:
        if pool.id in identifiers or pool.server_name in names:
            raise RuntimeError("Camouflage pool identifiers and server names must be unique")
        if not HOSTNAME.fullmatch(pool.server_name):
            raise RuntimeError("Camouflage pool server names must be canonical hostnames")
        if pool.target != f"{pool.server_name}:443":
            raise RuntimeError("Camouflage pool targets must use their server name on TCP 443")
        identifiers.add(pool.id)
        names.add(pool.server_name)
        counts[pool.region] += 1
    if set(counts) != EXPECTED_REGIONS or any(count < 3 for count in counts.values()):
        raise RuntimeError("Every supported region requires at least three camouflage pools")
    return value


def list_pools(region: str | None = None) -> list[CamouflagePoolRead]:
    if region is not None and region not in EXPECTED_REGIONS:
        raise CamouflagePoolError("Unsupported camouflage pool region")
    return [pool for pool in catalog().pools if region is None or pool.region == region]


def get_pool(pool_id: str) -> CamouflagePoolRead:
    for pool in catalog().pools:
        if pool.id == pool_id:
            return pool
    raise CamouflagePoolError("Unknown camouflage pool")


def validate_pool_id(pool_id: str | None) -> str:
    if not pool_id:
        raise CamouflagePoolError("A camouflage pool is required for this protocol profile")
    return get_pool(pool_id).id
