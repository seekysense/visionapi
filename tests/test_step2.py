"""Step 2 — LLM singolo modello + reasoning flag."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import READER_HDR


FAKE_LLM_RESULT = '{"number": 3, "confidence": 0.9, "describe": "Three people in coats."}'


def _mock_analyze(result_str: str):
    """Patch vision.analyze a livello di router per evitare chiamate reali."""
    mock = AsyncMock(return_value=json.loads(result_str))
    return patch("app.routers.analyze.analyze", mock)


def _mock_snapshot():
    """Patch axis.fetch_snapshot per evitare chiamate alla telecamera."""
    return patch(
        "app.routers.analyze.fetch_snapshot",
        new=AsyncMock(return_value=b"FAKEJPEG"),
    )


def test_analyze_response_has_model_used(client):
    with _mock_snapshot(), _mock_analyze(FAKE_LLM_RESULT):
        r = client.post(
            "/analyze/tc_kitchen/people_count",
            headers=READER_HDR,
        )
    assert r.status_code == 200
    body = r.json()
    assert "model_used" in body
    assert body["model_used"] != ""


def test_analyze_response_has_reasoning_enabled(client):
    with _mock_snapshot(), _mock_analyze(FAKE_LLM_RESULT):
        r = client.post("/analyze/tc_kitchen/people_count", headers=READER_HDR)
    assert r.status_code == 200
    assert "reasoning_enabled" in r.json()


def test_analyze_response_no_model_query_param(client):
    """Il parametro ?model= viene ignorato; model_used viene sempre da Settings."""
    with _mock_snapshot(), _mock_analyze(FAKE_LLM_RESULT):
        r = client.post(
            "/analyze/tc_kitchen/people_count?model=fast",
            headers=READER_HDR,
        )
    assert r.status_code == 200


def test_analyze_unknown_camera(client):
    r = client.post("/analyze/nonexistent_cam/people_count", headers=READER_HDR)
    assert r.status_code == 404


def test_analyze_unknown_action(client):
    with _mock_snapshot():
        r = client.post("/analyze/tc_kitchen/nonexistent_action", headers=READER_HDR)
    assert r.status_code == 404


def test_analyze_llm_invalid_json_returns_422(client):
    with _mock_snapshot():
        with patch(
            "app.routers.analyze.analyze",
            new=AsyncMock(side_effect=ValueError("LLM returned garbage")),
        ):
            r = client.post("/analyze/tc_kitchen/people_count", headers=READER_HDR)
    assert r.status_code == 422


def test_config_has_llm_model():
    """Verifica che Settings carichi llm_model e non fast_model/reasoning_model."""
    from app.config import get_settings
    s = get_settings()
    assert hasattr(s, "llm_model")
    assert hasattr(s, "llm_reasoning")
    assert not hasattr(s, "fast_model"), "fast_model deve essere rimosso"
    assert not hasattr(s, "reasoning_model"), "reasoning_model deve essere rimosso"
