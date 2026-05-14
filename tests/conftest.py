import os

# Override env PRIMA di qualsiasi import da app/
# (altrimenti lru_cache su get_settings si calcola con valori reali o mancanti)
os.environ.update({
    "API_PASSWORD":       "test-reader-key",
    "API_ADMIN_PASSWORD": "test-admin-key",
    "LLM_BASE_URL":       "http://fake-llm/v1",
    "LLM_API_KEY":        "fake-key",
    "LLM_MODEL":          "test-model",
    "LLM_REASONING":      "false",
    "LLM_TIMEOUT":        "10",
    "LLM_CONTEXT_WINDOW": "4096",
    "AXIS_DEFAULT_USER":  "testuser",
    "AXIS_DEFAULT_PASS":  "testpass",
})

from app.config import get_settings
get_settings.cache_clear()

from fastapi.testclient import TestClient
from app.main import app


def pytest_configure(config):
    """Assicura cache pulita ad ogni sessione pytest."""
    get_settings.cache_clear()


import pytest

@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


READER_HDR = {"X-API-Key":   "test-reader-key"}
ADMIN_HDR  = {"X-Admin-Key": "test-admin-key"}
BAD_HDR    = {"X-API-Key":   "wrong"}
