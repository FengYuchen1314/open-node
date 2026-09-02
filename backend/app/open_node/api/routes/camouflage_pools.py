from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from open_node.domain.camouflage import CamouflagePoolCatalogRead
from open_node.services.camouflage_pools import CamouflagePoolError, catalog, list_pools

router = APIRouter(prefix="/camouflage-pools", tags=["camouflage-pools"])


@router.get("", response_model=CamouflagePoolCatalogRead)
def get_camouflage_pools(
    region: Annotated[str | None, Query(min_length=2, max_length=32)] = None,
) -> CamouflagePoolCatalogRead:
    try:
        pools = list_pools(region)
    except CamouflagePoolError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    value = catalog()
    return value.model_copy(update={"pools": pools})
