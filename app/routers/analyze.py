from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth import require_api_key
from app.axis import (
    check_recording_exists,
    fetch_recording_frame,
    fetch_sequence,
    fetch_snapshot,
)
from app.config import get_settings, load_actions, load_cameras
from app.vision import ImageTooLargeError, analyze

router = APIRouter(prefix="/analyze", tags=["analyze"])


class AnalyzeResponse(BaseModel):
    camera_id: str
    action: str
    mode: str
    model_used: str
    reasoning_enabled: bool
    source: Literal["live", "recording"]
    at_used: str
    result: dict
    frames_captured: int
    capture_interval_ms: Optional[int]
    timestamp: str


def _find(items: list[dict], item_id: str, kind: str) -> dict:
    for item in items:
        if item["id"] == item_id:
            return item
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{kind} '{item_id}' not found")


@router.post(
    "/{camera_id}/{action_id}",
    response_model=AnalyzeResponse,
    summary="Analyze a camera frame with the specified action",
)
async def analyze_camera(
    camera_id: str,
    action_id: str,
    mode: Literal["snapshot", "sequence"] = Query(
        "snapshot",
        description="snapshot = single frame; sequence = 4 frames @ 1 fps",
    ),
    at: Optional[datetime] = Query(
        None,
        description="UTC datetime for historical clip retrieval (ISO 8601, e.g. 2024-01-15T10:30:00Z). "
                    "Default: live snapshot.",
    ),
    resolution: Optional[str] = Query(
        None,
        description="Override camera resolution (e.g. 640x480)",
    ),
    compression: Optional[int] = Query(
        None,
        ge=0,
        le=100,
        description="Override JPEG compression 0–100",
    ),
    _: str = Depends(require_api_key),
):
    now = datetime.now(timezone.utc)

    if at is not None:
        at_utc = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at
        if at_utc > now + timedelta(seconds=60):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Parameter 'at' cannot be in the future: {at_utc.isoformat()}",
            )
    else:
        at_utc = None

    camera = _find(load_cameras(), camera_id, "Camera")
    action = _find(load_actions(), action_id, "Action")

    snap_kwargs: dict = {}
    if resolution:
        snap_kwargs["resolution"] = resolution
    if compression is not None:
        snap_kwargs["compression"] = compression

    try:
        if at_utc is not None:
            exists = await check_recording_exists(camera, at_utc)
            if not exists:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail=f"No clip found for {at_utc.isoformat()}",
                )
            frames = [await fetch_recording_frame(camera, at_utc)]
            source = "recording"
            at_label = at_utc.isoformat()
            interval_ms: Optional[int] = None
        elif mode == "sequence":
            frames = await fetch_sequence(camera, count=4, interval=1.0, **snap_kwargs)
            source = "live"
            at_label = now.isoformat()
            interval_ms = 1000
        else:
            frames = [await fetch_snapshot(camera, **snap_kwargs)]
            source = "live"
            at_label = now.isoformat()
            interval_ms = None

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Timeout on camera '{camera_id}'")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Camera HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Camera unreachable: {e}")

    prompt = action["prompt_sequence"] if mode == "sequence" else action["prompt_single"]

    try:
        result = await analyze(frames, prompt)
    except ImageTooLargeError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"LLM response not parseable as JSON: {e}",
        )

    s = get_settings()
    return AnalyzeResponse(
        camera_id=camera_id,
        action=action_id,
        mode=mode,
        model_used=s.llm_model,
        reasoning_enabled=s.llm_reasoning,
        source=source,
        at_used=at_label,
        result=result,
        frames_captured=len(frames),
        capture_interval_ms=interval_ms,
        timestamp=now.isoformat(),
    )
