from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.auth import require_admin_key, require_api_key
from app.axis import (
    check_recording_exists,
    fetch_live_sequence_frames,
    fetch_mjpeg_frames,
)
from app.config import get_settings, load_cameras, load_sequences, save_sequences
from app.vision import ImageTooLargeError, analyze, analyze_sequence_final

router = APIRouter(prefix="/sequence", tags=["sequence"])


# ── Pydantic models ────────────────────────────────────────────────────────────

class SequenceOut(BaseModel):
    id: str
    label: str
    fps: float
    window_before_s: int
    window_after_s: int
    frames_per_chunk: int
    output_type: str


class SequenceIn(BaseModel):
    id: str
    label: str
    fps: float = 2.0
    window_before_s: int = 15
    window_after_s: int = 15
    frames_per_chunk: int = 8
    chunk_prompt: str
    final_prompt: str
    output_type: str = "json"
    output_schema: Optional[dict] = None


class ChunkResult(BaseModel):
    chunk_index: int
    frame_start: int
    frame_end: int
    time_start: str
    time_end: str
    result: dict


class SequenceResponse(BaseModel):
    camera_id: str
    sequence_id: str
    at: str
    source: str
    window: dict
    frames_collected: int
    chunks_analyzed: int
    chunk_results: list[ChunkResult]
    final_result: dict
    model_used: str
    processing_time_ms: int
    timestamp: str


# ── helpers ────────────────────────────────────────────────────────────────────

def _find_camera(camera_id: str) -> dict:
    for c in load_cameras():
        if c["id"] == camera_id:
            return c
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Camera '{camera_id}' not found")


def _find_sequence_cfg(sequence_id: str) -> dict:
    for s in load_sequences():
        if s["id"] == sequence_id:
            return s
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Sequence '{sequence_id}' not found")


def _seq_out(entry: dict) -> SequenceOut:
    return SequenceOut(
        id=entry["id"],
        label=entry["label"],
        fps=entry.get("fps", 2.0),
        window_before_s=entry.get("window_before_s", 15),
        window_after_s=entry.get("window_after_s", 15),
        frames_per_chunk=entry.get("frames_per_chunk", 8),
        output_type=entry.get("output_type", "json"),
    )


# ── CRUD sequences ─────────────────────────────────────────────────────────────

@router.get("", response_model=list[SequenceOut], summary="List configured sequences")
async def list_sequences(_: str = Depends(require_api_key)):
    return [_seq_out(s) for s in load_sequences()]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SequenceOut,
    summary="Create new sequence",
)
async def create_sequence(body: SequenceIn, _: str = Depends(require_admin_key)):
    sequences = load_sequences()
    if any(s["id"] == body.id for s in sequences):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Sequence '{body.id}' already exists")
    entry = {k: v for k, v in body.model_dump().items() if v is not None}
    sequences.append(entry)
    save_sequences(sequences)
    return _seq_out(entry)


@router.put("/{sequence_id}", response_model=SequenceOut, summary="Update existing sequence")
async def update_sequence(sequence_id: str, body: SequenceIn, _: str = Depends(require_admin_key)):
    sequences = load_sequences()
    for i, s in enumerate(sequences):
        if s["id"] == sequence_id:
            entry = {k: v for k, v in body.model_dump().items() if v is not None}
            sequences[i] = entry
            save_sequences(sequences)
            return _seq_out(entry)
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Sequence '{sequence_id}' not found")


@router.delete("/{sequence_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete sequence")
async def delete_sequence(sequence_id: str, _: str = Depends(require_admin_key)):
    sequences = load_sequences()
    new_list = [s for s in sequences if s["id"] != sequence_id]
    if len(new_list) == len(sequences):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Sequence '{sequence_id}' not found")
    save_sequences(new_list)


# ── execution endpoint ─────────────────────────────────────────────────────────

@router.post(
    "/{camera_id}/{sequence_id}",
    response_model=SequenceResponse,
    summary="Run sequence analysis on a camera",
)
async def run_sequence(
    camera_id: str,
    sequence_id: str,
    at: Optional[datetime] = Query(
        None,
        description="UTC datetime of the target moment (ISO 8601). "
                    "If omitted: live capture over the full time window.",
    ),
    resolution: Optional[str] = Query(None),
    compression: Optional[int] = Query(None, ge=0, le=100),
    _: str = Depends(require_api_key),
):
    t_start = time.monotonic()
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

    camera   = _find_camera(camera_id)
    sequence = _find_sequence_cfg(sequence_id)

    fps              = sequence["fps"]
    window_before_s  = sequence["window_before_s"]
    window_after_s   = sequence["window_after_s"]
    frames_per_chunk = sequence["frames_per_chunk"]
    chunk_prompt     = sequence["chunk_prompt"]
    final_prompt     = sequence["final_prompt"]

    snap_kwargs: dict = {}
    if resolution:
        snap_kwargs["resolution"] = resolution
    if compression is not None:
        snap_kwargs["compression"] = compression

    # ── FASE 1: raccolta frame ─────────────────────────────────────────────
    try:
        if at_utc is not None:
            exists = await check_recording_exists(camera, at_utc)
            if not exists:
                raise HTTPException(
                    status.HTTP_404_NOT_FOUND,
                    detail=f"No recording found for {at_utc.isoformat()}",
                )
            frames = await fetch_mjpeg_frames(
                camera, at_utc, window_before_s, window_after_s, fps
            )
            source   = "recording"
            at_label = at_utc.isoformat()
        else:
            frames = await fetch_live_sequence_frames(
                camera, window_before_s, window_after_s, fps, **snap_kwargs
            )
            source   = "live"
            at_label = now.isoformat()

    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Timeout on camera '{camera_id}'")
    except httpx.HTTPStatusError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Camera HTTP {e.response.status_code}")
    except (httpx.RequestError, ValueError) as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Camera error: {e}")

    if len(frames) < frames_per_chunk:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Too few frames collected ({len(frames)} < {frames_per_chunk})",
        )

    # ── FASE 2: chunk analysis ─────────────────────────────────────────────
    chunks           = [frames[i:i + frames_per_chunk] for i in range(0, len(frames), frames_per_chunk)]
    total_chunks     = len(chunks)
    frame_interval_s = 1.0 / fps
    chunk_duration_s = frames_per_chunk * frame_interval_s

    if at_utc:
        base_ts = at_utc - timedelta(seconds=window_before_s)
    else:
        base_ts = now - timedelta(seconds=window_before_s + window_after_s)

    chunk_results_raw: list[dict] = []
    chunk_results_out: list[ChunkResult] = []

    for idx, chunk_frames in enumerate(chunks):
        frame_start   = idx * frames_per_chunk
        frame_end     = frame_start + len(chunk_frames) - 1
        t_chunk_start = base_ts + timedelta(seconds=frame_start * frame_interval_s)
        t_chunk_end   = base_ts + timedelta(seconds=frame_end   * frame_interval_s)

        contextual_prompt = (
            chunk_prompt
            .replace("{chunk_index}", str(idx + 1))
            .replace("{total_chunks}", str(total_chunks))
        )

        try:
            result = await analyze(chunk_frames, contextual_prompt)
        except ImageTooLargeError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
        except ValueError as e:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"LLM parse error on chunk {idx}: {e}",
            )

        chunk_results_raw.append(result)
        chunk_results_out.append(ChunkResult(
            chunk_index=idx,
            frame_start=frame_start,
            frame_end=frame_end,
            time_start=t_chunk_start.isoformat(),
            time_end=t_chunk_end.isoformat(),
            result=result,
        ))

    # ── FASE 3: sintesi finale ─────────────────────────────────────────────
    try:
        final_result = await analyze_sequence_final(
            chunk_results_raw, final_prompt, batch_duration_s=chunk_duration_s
        )
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Final synthesis parse error: {e}",
        )

    s = get_settings()
    processing_ms = int((time.monotonic() - t_start) * 1000)

    return SequenceResponse(
        camera_id=camera_id,
        sequence_id=sequence_id,
        at=at_label,
        source=source,
        window={"before_s": window_before_s, "after_s": window_after_s},
        frames_collected=len(frames),
        chunks_analyzed=total_chunks,
        chunk_results=chunk_results_out,
        final_result=final_result,
        model_used=s.llm_model,
        processing_time_ms=processing_ms,
        timestamp=now.isoformat(),
    )
