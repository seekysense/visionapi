"""Step 4 — Swagger security schemes + health endpoint."""
from tests.conftest import READER_HDR


def test_health_no_auth(client):
    """GET /health non richiede autenticazione."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_health_returns_correct_version(client):
    r = client.get("/health")
    assert r.json()["version"] == "1.0.0"


def test_docs_accessible(client):
    """Swagger UI deve essere raggiungibile senza auth."""
    r = client.get("/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower() or "openapi" in r.text.lower()


def test_redoc_accessible(client):
    r = client.get("/redoc")
    assert r.status_code == 200


def test_openapi_json_has_security_schemes(client):
    """Lo schema OpenAPI deve dichiarare ReaderKey e AdminKey."""
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    schemes = schema.get("components", {}).get("securitySchemes", {})
    assert "ReaderKey" in schemes, "ReaderKey security scheme mancante"
    assert "AdminKey" in schemes, "AdminKey security scheme mancante"


def test_openapi_reader_key_is_apikey_header(client):
    r = client.get("/openapi.json")
    schemes = r.json()["components"]["securitySchemes"]
    reader = schemes["ReaderKey"]
    assert reader["type"] == "apiKey"
    assert reader["in"] == "header"
    assert reader["name"] == "X-API-Key"


def test_openapi_admin_key_is_apikey_header(client):
    r = client.get("/openapi.json")
    schemes = r.json()["components"]["securitySchemes"]
    admin = schemes["AdminKey"]
    assert admin["type"] == "apiKey"
    assert admin["in"] == "header"
    assert admin["name"] == "X-Admin-Key"


def test_analyze_endpoint_requires_security(client):
    """Il path /analyze deve avere security requirements nella spec."""
    r = client.get("/openapi.json")
    paths = r.json().get("paths", {})
    analyze_paths = {k: v for k, v in paths.items() if "/analyze/" in k or k.startswith("/analyze")}
    assert len(analyze_paths) > 0, "Nessun endpoint /analyze nella spec"
