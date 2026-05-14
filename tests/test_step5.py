"""Step 5 — Parametro at UTC per clip storiche."""
import json
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from tests.conftest import READER_HDR

FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100 + b"\xff\xd9"
FAKE_RESULT = '{"number": 2, "confidence": 0.85, "describe": "Two people."}'


def _mock_deps(recording_exists=True, result_str=FAKE_RESULT):
    return [
        patch("app.routers.analyze.check_recording_exists", new=AsyncMock(return_value=recording_exists)),
        patch("app.routers.analyze.fetch_recording_frame",  new=AsyncMock(return_value=FAKE_JPEG)),
        patch("app.routers.analyze.fetch_snapshot",         new=AsyncMock(return_value=FAKE_JPEG)),
        patch("app.routers.analyze.analyze",                new=AsyncMock(return_value=json.loads(result_str))),
    ]


def _apply(patches):
    stack = ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


def test_analyze_live_no_at(client):
    """Senza at → live snapshot, source=live."""
    with _apply(_mock_deps()):
        r = client.post("/analyze/tc_kitchen/people_count", headers=READER_HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "live"
    assert "at_used" in body


def test_analyze_historical_at_found(client):
    """Con at valido e clip trovata → source=recording."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with _apply(_mock_deps(recording_exists=True)):
        r = client.post(
            "/analyze/tc_kitchen/people_count",
            params={"at": past},
            headers=READER_HDR,
        )
    assert r.status_code == 200
    assert r.json()["source"] == "recording"


def test_analyze_historical_at_not_found(client):
    """Con at valido ma nessuna clip → 404."""
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    with _apply(_mock_deps(recording_exists=False)):
        r = client.post(
            "/analyze/tc_kitchen/people_count",
            params={"at": past},
            headers=READER_HDR,
        )
    assert r.status_code == 404
    assert "No clip found" in r.json()["detail"]


def test_analyze_future_at_rejected(client):
    """at nel futuro → 422."""
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    r = client.post(
        "/analyze/tc_kitchen/people_count",
        params={"at": future},
        headers=READER_HDR,
    )
    assert r.status_code == 422


def test_analyze_at_used_in_response(client):
    """Il campo at_used deve essere presente nella response."""
    past = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    with _apply(_mock_deps(recording_exists=True)):
        r = client.post(
            "/analyze/tc_kitchen/people_count",
            params={"at": past},
            headers=READER_HDR,
        )
    assert r.status_code == 200
    assert "at_used" in r.json()


def test_frame_endpoint_accepts_at(client):
    """GET /frame/{camera_id}?at=... con clip trovata → 200 image/jpeg."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with patch("app.routers.frame.check_recording_exists", new=AsyncMock(return_value=True)), \
         patch("app.routers.frame.fetch_recording_frame",  new=AsyncMock(return_value=FAKE_JPEG)):
        r = client.get("/frame/tc_kitchen", params={"at": past}, headers=READER_HDR)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"


def test_frame_endpoint_at_not_found(client):
    """GET /frame con at ma nessuna clip → 404."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with patch("app.routers.frame.check_recording_exists", new=AsyncMock(return_value=False)):
        r = client.get("/frame/tc_kitchen", params={"at": past}, headers=READER_HDR)
    assert r.status_code == 404


def test_vapix_ts_format():
    """_vapix_ts deve produrre il formato ISO YYYY-MM-DDTHH:MM:SS.mmmZ."""
    from app.axis import _vapix_ts
    dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    assert _vapix_ts(dt) == "2024-01-15T10:30:00.000Z"
