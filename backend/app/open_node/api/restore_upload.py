"""Shared bounded raw-body receiver for authenticated and first-run restore uploads."""

import sys

from fastapi import Request

from open_node.domain.restore import BrowserRestoreError
from open_node.services.backup_encryption import MAX_ENCRYPTED_ARCHIVE_BYTES
from open_node.services.backup_runtime import run_in_backup_threadpool


async def receive_restore_upload(request: Request, owner: str):
    media = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    length = request.headers.get("content-length", "")
    if (
        media != "application/octet-stream"
        or request.headers.get("content-encoding") not in (None, "identity")
        or not length.isascii() or not length.isdigit() or length.startswith("0")
    ):
        raise BrowserRestoreError("restore_upload_invalid", 415)
    expected = int(length)
    if not 22 <= expected <= MAX_ENCRYPTED_ARCHIVE_BYTES:
        raise BrowserRestoreError("restore_upload_invalid", 413)
    writer = request.app.state.browser_restore.writer(owner, expected)
    entered = False
    try:
        await run_in_backup_threadpool(writer.__enter__)
        entered = True
        async for chunk in request.stream():
            if chunk:
                await run_in_backup_threadpool(writer.write, bytes(chunk))
        return await run_in_backup_threadpool(writer.finish)
    finally:
        if entered:
            await run_in_backup_threadpool(writer.__exit__, *sys.exc_info())
