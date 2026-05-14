from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse

from app.auth import require_api_key
from app.axis import check_recording_exists, fetch_recording_frame, fetch_snapshot
from app.config import load_cameras

router = APIRouter(prefix="/frame", tags=["frame"])


async def _safe_snapshot(camera: dict, **kwargs) -> tuple[str, bytes | Exception]:
    """Return (camera_id, jpeg_bytes) or (camera_id, exception) without raising."""
    try:
        data = await fetch_snapshot(camera, **kwargs)
        return camera["id"], data
    except Exception as exc:
        return camera["id"], exc


@router.get(
    "/{camera_id}",
    responses={200: {"content": {"image/jpeg": {}}}},
    summary="Scarica un frame da una telecamera",
)
async def get_frame(
    camera_id: str,
    at: Optional[datetime] = Query(
        None,
        description="UTC datetime per frame storico (ISO 8601). Default: snapshot live.",
    ),
    resolution: Optional[str] = Query(None),
    compression: Optional[int] = Query(None, ge=0, le=100),
    _: str = Depends(require_api_key),
):
    cameras = load_cameras()
    camera = next((c for c in cameras if c["id"] == camera_id), None)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Camera '{camera_id}' not found")

    kwargs: dict = {}
    if resolution:
        kwargs["resolution"] = resolution
    if compression is not None:
        kwargs["compression"] = compression

    try:
        if at is not None:
            at_utc = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at
            exists = await check_recording_exists(camera, at_utc)
            if not exists:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail=f"No clip found for {at_utc.isoformat()}",
                )
            data = await fetch_recording_frame(camera, at_utc)
        else:
            data = await fetch_snapshot(camera, **kwargs)
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Timeout on camera '{camera_id}'")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Camera HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Camera unreachable: {e}")

    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Content-Disposition": f'attachment; filename="{camera_id}.jpg"'},
    )


@router.get(
    "",
    responses={200: {"content": {"application/zip": {}}}},
    summary="Scarica un frame da tutte le telecamere (ZIP)",
)
async def get_all_frames(
    resolution: Optional[str] = Query(None),
    compression: Optional[int] = Query(None, ge=0, le=100),
    _: str = Depends(require_api_key),
):
    cameras = load_cameras()
    if not cameras:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No cameras configured")

    kwargs: dict = {}
    if resolution:
        kwargs["resolution"] = resolution
    if compression is not None:
        kwargs["compression"] = compression

    # Fetch all cameras concurrently; failed cameras are captured as exceptions.
    results: list[tuple[str, bytes | Exception]] = await asyncio.gather(
        *[_safe_snapshot(c, **kwargs) for c in cameras]
    )

    buf = io.BytesIO()
    errors: list[str] = []

    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for cam_id, outcome in results:
            if isinstance(outcome, Exception):
                errors.append(f"{cam_id}: {outcome}")
            else:
                zf.writestr(f"{cam_id}.jpg", outcome)

        if errors:
            zf.writestr("errors.txt", "\n".join(errors))

    buf.seek(0)

    if not any(not isinstance(outcome, Exception) for _, outcome in results):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="All cameras failed: " + "; ".join(errors))

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="frames.zip"'},
    )
