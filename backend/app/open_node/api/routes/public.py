from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from open_node.api.dependencies import get_inventory_store
from open_node.domain.probe import ProbePayload, ProbeSeriesResponse
from open_node.services.inventory import InventoryStore, ProbeNotFoundError

router = APIRouter(prefix="/public", tags=["public"])


@router.get(
    "/probe-servers",
    response_model=ProbePayload,
    response_model_exclude_none=True,
)
def public_probe_servers(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
) -> ProbePayload:
    return store.public_probe_payload()


@router.get(
    "/probe-series",
    response_model=ProbeSeriesResponse,
    response_model_exclude_none=True,
)
def public_probe_series(
    store: Annotated[InventoryStore, Depends(get_inventory_store)],
    server: Annotated[int, Query(ge=0)],
    metric: str = "ping",
    range_name: Annotated[str, Query(alias="range")] = "1h",
    target: str = "__avg__",
    all_targets: Annotated[bool, Query(alias="all")] = False,
) -> ProbeSeriesResponse | JSONResponse:
    try:
        return store.public_probe_series(
            server_index=server,
            metric=metric,
            range_name=range_name,
            target=target,
            all_targets=all_targets,
        )
    except ProbeNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"success": False, "license_required": False},
        )
