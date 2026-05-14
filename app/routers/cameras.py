from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.auth import require_admin_key, require_api_key
from app.config import load_cameras, save_cameras

router = APIRouter(prefix="/cameras", tags=["cameras"])


class CameraOut(BaseModel):
    id: str
    label: str
    base_url: str
    channel: int
    resolution: str
    compression: int
    rotation: int
    has_roi: bool
    recording_diskid: Optional[str] = None


class CameraIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    label: str
    base_url: str
    channel: int = 1
    resolution: str = "1280x720"
    compression: int = 30
    rotation: int = 0
    recording_diskid: Optional[str] = None
    roi: Optional[list[list[int]]] = None


def _to_out(c: dict) -> CameraOut:
    return CameraOut(
        id=c["id"],
        label=c["label"],
        base_url=c["base_url"],
        channel=c.get("channel", 1),
        resolution=c.get("resolution", "1280x720"),
        compression=c.get("compression", 30),
        rotation=c.get("rotation", 0),
        has_roi=bool(c.get("roi") and len(c["roi"]) >= 3),
        recording_diskid=c.get("recording_diskid"),
    )


def _to_entry(body: CameraIn) -> dict:
    return {k: v for k, v in body.model_dump().items() if v is not None}


@router.get("", response_model=list[CameraOut], summary="List configured cameras")
async def list_cameras(_: str = Depends(require_api_key)):
    return [_to_out(c) for c in load_cameras()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=CameraOut,
    summary="Add new camera",
)
async def create_camera(body: CameraIn, _: str = Depends(require_admin_key)):
    cameras = load_cameras()
    if any(c["id"] == body.id for c in cameras):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Camera '{body.id}' already exists")
    entry = _to_entry(body)
    cameras.append(entry)
    save_cameras(cameras)
    return _to_out(entry)


@router.put(
    "/{camera_id}",
    response_model=CameraOut,
    summary="Update existing camera",
)
async def update_camera(camera_id: str, body: CameraIn, _: str = Depends(require_admin_key)):
    cameras = load_cameras()
    for i, c in enumerate(cameras):
        if c["id"] == camera_id:
            entry = _to_entry(body)
            cameras[i] = entry
            save_cameras(cameras)
            return _to_out(entry)
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Camera '{camera_id}' not found")


@router.delete("/{camera_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove camera")
async def delete_camera(camera_id: str, _: str = Depends(require_admin_key)):
    cameras = load_cameras()
    new_list = [c for c in cameras if c["id"] != camera_id]
    if len(new_list) == len(cameras):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Camera '{camera_id}' not found")
    save_cameras(new_list)
