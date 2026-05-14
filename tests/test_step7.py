"""Step 7 — Verifica che il codice sia importabile e le dipendenze siano ok."""


def test_imports_fastapi():
    import fastapi
    assert fastapi.__version__


def test_imports_uvicorn():
    import uvicorn
    assert uvicorn.__version__


def test_imports_httpx():
    import httpx
    assert httpx.__version__


def test_imports_openai():
    import openai
    assert openai.__version__


def test_imports_pydantic():
    import pydantic
    assert pydantic.__version__


def test_imports_pil():
    from PIL import Image
    assert "jpeg" in [f.lower() for f in Image.registered_extensions().values()]


def test_imports_yaml():
    import yaml
    assert yaml.__version__


def test_app_starts():
    """Verifica che l'app FastAPI si crei senza errori."""
    from app.main import app
    assert app is not None
    assert app.title == "VisionAPISmart"


def test_health_route_exists():
    """Verifica che /health sia registrato."""
    from app.main import app
    routes = [r.path for r in app.routes]
    assert "/health" in routes
