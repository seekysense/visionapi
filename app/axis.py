from __future__ import annotations

import asyncio
import base64
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree

import httpx
from PIL import Image, ImageDraw

from app.config import get_settings

_SNAPSHOT_PATH       = "/axis-cgi/jpg/image.cgi"
_RECORD_LIST_PATH    = "/axis-cgi/record/list.cgi"
_RECORD_EXPORT_PATH  = "/axis-cgi/record/export/exportrecording.cgi"
_TIMEOUT             = 5.0
_RECORD_TIMEOUT      = 30.0
_MP4_EXPORT_TIMEOUT  = 120.0


def _snapshot_url(
    base_url: str,
    channel: int,
    resolution: str,
    compression: int,
    rotation: int,
) -> str:
    url = (
        f"{base_url}{_SNAPSHOT_PATH}"
        f"?camera={channel}&resolution={resolution}&compression={compression}"
    )
    if rotation:
        url += f"&rotation={rotation}"
    return url


def apply_roi(image_bytes: bytes, roi_points: list[list[int]]) -> bytes:
    """Mask pixels outside the ROI polygon and crop to its bounding box."""
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    flat = [(p[0], p[1]) for p in roi_points]

    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).polygon(flat, fill=255)

    black = Image.new("RGB", img.size, (0, 0, 0))
    result = Image.composite(img, black, mask)

    xs, ys = zip(*flat)
    result = result.crop((min(xs), min(ys), max(xs), max(ys)))

    buf = BytesIO()
    result.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def resize_image(image_bytes: bytes, scale: float) -> bytes:
    img = Image.open(BytesIO(image_bytes))
    new_w = max(1, int(img.width * scale))
    new_h = max(1, int(img.height * scale))
    img = img.resize((new_w, new_h), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def to_b64(image_bytes: bytes) -> str:
    return base64.b64encode(image_bytes).decode()


async def fetch_snapshot(
    camera: dict,
    resolution: Optional[str] = None,
    compression: Optional[int] = None,
) -> bytes:
    s = get_settings()
    url = _snapshot_url(
        base_url=camera["base_url"],
        channel=camera.get("channel", 1),
        resolution=resolution or camera.get("resolution", "1280x720"),
        compression=compression if compression is not None else camera.get("compression", 30),
        rotation=camera.get("rotation", 0),
    )
    user = camera.get("username") or s.axis_default_user
    pwd = camera.get("password") or s.axis_default_pass

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(url, auth=httpx.DigestAuth(user, pwd))
        resp.raise_for_status()
        data = resp.content

    roi = camera.get("roi")
    if roi and len(roi) >= 3:
        data = apply_roi(data, roi)

    return data


def _vapix_ts(dt: datetime) -> str:
    """Converte datetime UTC in formato VAPIX ISO: 2024-01-15T10:30:00.000Z"""
    utc = dt.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_recordings_xml(xml_bytes: bytes) -> list[dict]:
    """Parses VAPIX record/list.cgi XML into a list of recording dicts."""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return []
    recordings = []
    for rec in root.iter("recording"):
        rec_id = rec.get("recordingid")
        disk_id = rec.get("diskid")
        if rec_id and disk_id:
            recordings.append({
                "id": rec_id,
                "disk_id": disk_id,
                "start": rec.get("starttime", ""),
                "stop": rec.get("stoptime", ""),
            })
    return recordings


async def _find_recording_for_time(camera: dict, at: datetime) -> Optional[dict]:
    """Returns the first recording that covers `at` (±30s window), or None."""
    s = get_settings()
    window = timedelta(seconds=30)
    user = camera.get("username") or s.axis_default_user
    pwd = camera.get("password") or s.axis_default_pass

    params = {
        "starttime": _vapix_ts(at - window),
        "stoptime": _vapix_ts(at + window),
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{camera['base_url']}{_RECORD_LIST_PATH}",
            params=params,
            auth=httpx.DigestAuth(user, pwd),
        )
        resp.raise_for_status()

    recordings = _parse_recordings_xml(resp.content)
    return recordings[0] if recordings else None


async def _download_mp4(
    camera: dict,
    recording_id: str,
    disk_id: str,
    start: datetime,
    stop: datetime,
    dest: Path,
) -> None:
    """Streams an MP4 export from AXIS exportrecording.cgi to dest."""
    s = get_settings()
    user = camera.get("username") or s.axis_default_user
    pwd = camera.get("password") or s.axis_default_pass

    params = {
        "schemaversion": "1",
        "recordingid": recording_id,
        "diskid": disk_id,
        "exportformat": "mp4",
        "starttime": _vapix_ts(start),
        "stoptime": _vapix_ts(stop),
    }
    async with httpx.AsyncClient(timeout=_MP4_EXPORT_TIMEOUT) as client:
        async with client.stream(
            "GET",
            f"{camera['base_url']}{_RECORD_EXPORT_PATH}",
            params=params,
            auth=httpx.DigestAuth(user, pwd),
        ) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)


def _run_ffmpeg_extract(
    mp4_path: Path,
    duration_s: float,
    target_fps: float,
    output_dir: Path,
    target_resolution: Optional[str] = None,
) -> list[Path]:
    """Extracts frames from an MP4 at target_fps using ffmpeg (system binary).

    target_resolution (e.g. "1280x720") scales the output so ROI coordinates
    defined for the camera's snapshot resolution remain valid on recording frames.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found in PATH — install with: apt-get install -y ffmpeg"
        )

    vf = f"fps={target_fps}"
    if target_resolution:
        w, h = target_resolution.split("x")
        vf += f",scale={w}:{h}"

    pattern = str(output_dir / "frame_%04d.jpg")
    cmd = [
        ffmpeg, "-y",
        "-t", str(duration_s),
        "-i", str(mp4_path),
        "-vf", vf,
        "-q:v", "2",
        pattern,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr.decode(errors='replace')}")

    return sorted(output_dir.glob("frame_*.jpg"))


async def check_recording_exists(camera: dict, at: datetime) -> bool:
    """Verifica se esiste almeno una registrazione nell'intervallo ±30s intorno ad `at`."""
    rec = await _find_recording_for_time(camera, at)
    return rec is not None


async def fetch_recording_frame(camera: dict, at: datetime) -> bytes:
    """Trova la registrazione che copre `at`, scarica il clip MP4 ed estrae il frame centrale."""
    rec = await _find_recording_for_time(camera, at)
    if not rec:
        raise ValueError(f"No recording found for {_vapix_ts(at)}")

    window = timedelta(seconds=3)
    start = at - window
    stop = at + window
    duration_s = (stop - start).total_seconds()

    cam_resolution = camera.get("resolution", "1280x720")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        mp4_file = tmp_path / "clip.mp4"
        await _download_mp4(camera, rec["id"], rec["disk_id"], start, stop, mp4_file)

        frame_paths = _run_ffmpeg_extract(mp4_file, duration_s, 1.0, tmp_path, cam_resolution)
        if not frame_paths:
            raise ValueError(f"No frames extracted from recording at {_vapix_ts(at)}")

        data = frame_paths[0].read_bytes()

    roi = camera.get("roi")
    if roi and len(roi) >= 3:
        data = apply_roi(data, roi)
    return data


async def fetch_sequence(
    camera: dict,
    count: int = 4,
    interval: float = 1.0,
    **kwargs,
) -> list[bytes]:
    frames: list[bytes] = []
    for i in range(count):
        if i > 0:
            await asyncio.sleep(interval)
        frames.append(await fetch_snapshot(camera, **kwargs))
    return frames


async def fetch_mjpeg_frames(
    camera: dict,
    at: datetime,
    window_before_s: int,
    window_after_s: int,
    target_fps: float,
) -> list[bytes]:
    """Scarica il clip MP4 dalla registrazione ed estrae frame a target_fps con ffmpeg."""
    rec = await _find_recording_for_time(camera, at)
    if not rec:
        raise ValueError(f"No recording found for {_vapix_ts(at)}")

    start = at - timedelta(seconds=window_before_s)
    stop = at + timedelta(seconds=window_after_s)
    total_duration_s = window_before_s + window_after_s
    cam_resolution = camera.get("resolution", "1280x720")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        mp4_file = tmp_path / "clip.mp4"
        await _download_mp4(camera, rec["id"], rec["disk_id"], start, stop, mp4_file)

        frame_paths = _run_ffmpeg_extract(
            mp4_file, total_duration_s, target_fps, tmp_path, cam_resolution
        )
        if not frame_paths:
            raise ValueError(
                f"No frames extracted from recording {_vapix_ts(start)}–{_vapix_ts(stop)}"
            )

        frames = [p.read_bytes() for p in frame_paths]

    roi = camera.get("roi")
    if roi and len(roi) >= 3:
        frames = [apply_roi(f, roi) for f in frames]

    return frames


async def fetch_live_sequence_frames(
    camera: dict,
    window_before_s: int,
    window_after_s: int,
    target_fps: float,
    **snap_kwargs,
) -> list[bytes]:
    """Acquisisce frame live per (window_before_s + window_after_s) secondi a target_fps."""
    total_duration_s = window_before_s + window_after_s
    total_frames = int(total_duration_s * target_fps)
    interval_s = 1.0 / target_fps

    frames: list[bytes] = []
    for i in range(total_frames):
        if i > 0:
            await asyncio.sleep(interval_s)
        frames.append(await fetch_snapshot(camera, **snap_kwargs))

    return frames
