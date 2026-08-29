from fastapi import APIRouter, HTTPException, Request, Response

from open_node.domain.legacy_mmwx import (
    LegacyMMWXImportPreview,
    LegacyMMWXImportRequest,
    LegacyMMWXImportResponse,
    LegacyMMWXPreviewRequest,
)
from open_node.services.legacy_mmwx import LegacyMMWXMigration, LegacyMMWXMigrationError

router = APIRouter(prefix="/migrations/mmwx", tags=["MMWX migration"])


def migration(request):
    return LegacyMMWXMigration(request.app.state.inventory, request.app.state.subscriber_auth)


@router.post("/identities/preview", response_model=LegacyMMWXImportPreview)
def preview_identities(payload: LegacyMMWXPreviewRequest, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return migration(request).preview(payload)


@router.post("/identities/import", response_model=LegacyMMWXImportResponse)
def import_identities(payload: LegacyMMWXImportRequest, request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return migration(request).apply(payload)
    except LegacyMMWXMigrationError as exc:
        raise HTTPException(409, str(exc)) from exc
