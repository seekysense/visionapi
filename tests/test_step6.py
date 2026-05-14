"""Step 6 — Sequence Analysis Engine."""
import math
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from tests.conftest import ADMIN_HDR, READER_HDR

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9"

FAKE_CHUNK_RESULT = {
    "door_open": "yes",
    "hand_visible": "yes",
    "item_interaction": "no",
    "confidence": 0.85,
    "notes": "Door opened briefly.",
}
FAKE_FINAL_RESULT = {
    "cabinet_accessed": True,
    "item_taken": False,
    "hand_visible": True,
    "confidence": 0.88,
    "evidence": "Door seen open in batches 2-3.",
    "verdict": "SUSPICIOUS",
}


def _patches(recording_exists=True, num_frames=60, chunk_result=None, final_result=None):
    return [
        patch("app.routers.sequence.check_recording_exists", new=AsyncMock(return_value=recording_exists)),
        patch("app.routers.sequence.fetch_mjpeg_frames",     new=AsyncMock(return_value=[FAKE_JPEG] * num_frames)),
        patch("app.routers.sequence.fetch_live_sequence_frames", new=AsyncMock(return_value=[FAKE_JPEG] * num_frames)),
        patch("app.routers.sequence.analyze",                new=AsyncMock(return_value=chunk_result or FAKE_CHUNK_RESULT)),
        patch("app.routers.sequence.analyze_sequence_final", new=AsyncMock(return_value=final_result or FAKE_FINAL_RESULT)),
    ]


def _apply(patches_list):
    stack = ExitStack()
    for p in patches_list:
        stack.enter_context(p)
    return stack


# ── CRUD sequences ─────────────────────────────────────────────────────────────

def test_list_sequences(client):
    r = client.get("/sequence", headers=READER_HDR)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    ids = [s["id"] for s in r.json()]
    assert "cabinet_access_detection" in ids


def test_list_sequences_no_auth(client):
    r = client.get("/sequence")
    assert r.status_code == 401


def test_create_sequence_admin(client):
    payload = {
        "id": "step6_test_seq",
        "label": "Test Seq",
        "fps": 1.0,
        "window_before_s": 5,
        "window_after_s": 5,
        "frames_per_chunk": 4,
        "chunk_prompt": "Analyze these frames.",
        "final_prompt": "Summarize: {chunk_results}",
        "output_type": "json",
    }
    r = client.post("/sequence", json=payload, headers=ADMIN_HDR)
    assert r.status_code in (201, 409)


def test_create_sequence_reader_rejected(client):
    payload = {
        "id": "step6_reader_reject",
        "label": "X", "fps": 1.0, "window_before_s": 5, "window_after_s": 5,
        "frames_per_chunk": 4, "chunk_prompt": "x", "final_prompt": "x",
    }
    r = client.post("/sequence", json=payload, headers=READER_HDR)
    assert r.status_code == 401


def test_delete_sequence_admin(client):
    client.post(
        "/sequence",
        json={
            "id": "step6_del_seq", "label": "Del", "fps": 1.0,
            "window_before_s": 5, "window_after_s": 5, "frames_per_chunk": 4,
            "chunk_prompt": "x", "final_prompt": "x",
        },
        headers=ADMIN_HDR,
    )
    r = client.delete("/sequence/step6_del_seq", headers=ADMIN_HDR)
    assert r.status_code == 204


# ── run sequence ───────────────────────────────────────────────────────────────

def test_run_sequence_live(client):
    with _apply(_patches(num_frames=60)):
        r = client.post(
            "/sequence/tc_kitchen_cabinet/cabinet_access_detection",
            headers=READER_HDR,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "live"
    assert body["frames_collected"] == 60
    assert body["chunks_analyzed"] == math.ceil(60 / 8)
    assert "final_result" in body
    assert body["final_result"]["verdict"] in ("CLEAR", "SUSPICIOUS", "CONFIRMED_ACCESS")


def test_run_sequence_historical(client):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with _apply(_patches(recording_exists=True, num_frames=60)):
        r = client.post(
            "/sequence/tc_kitchen_cabinet/cabinet_access_detection",
            params={"at": past},
            headers=READER_HDR,
        )
    assert r.status_code == 200
    assert r.json()["source"] == "recording"


def test_run_sequence_no_recording(client):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with _apply(_patches(recording_exists=False)):
        r = client.post(
            "/sequence/tc_kitchen_cabinet/cabinet_access_detection",
            params={"at": past},
            headers=READER_HDR,
        )
    assert r.status_code == 404
    assert "No recording found" in r.json()["detail"]


def test_run_sequence_future_rejected(client):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = client.post(
        "/sequence/tc_kitchen_cabinet/cabinet_access_detection",
        params={"at": future},
        headers=READER_HDR,
    )
    assert r.status_code == 422


def test_run_sequence_chunk_results_structure(client):
    with _apply(_patches(num_frames=60)):
        r = client.post(
            "/sequence/tc_kitchen_cabinet/cabinet_access_detection",
            headers=READER_HDR,
        )
    body = r.json()
    chunks = body["chunk_results"]
    assert len(chunks) == math.ceil(60 / 8)
    for c in chunks:
        assert "chunk_index" in c
        assert "frame_start" in c
        assert "frame_end" in c
        assert "time_start" in c
        assert "result" in c


def test_run_sequence_unknown_camera(client):
    r = client.post(
        "/sequence/nonexistent_cam/cabinet_access_detection",
        headers=READER_HDR,
    )
    assert r.status_code == 404


def test_run_sequence_unknown_sequence(client):
    r = client.post(
        "/sequence/tc_kitchen_cabinet/nonexistent_seq",
        headers=READER_HDR,
    )
    assert r.status_code == 404
