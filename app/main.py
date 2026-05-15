from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.config import get_settings
from app.routers import actions, analyze, cameras, frame, sequence
from app.telemetry import setup_phoenix

app = FastAPI(
    title="VisionAPISmart",
    description=(
        "Semantic API layer for Axis camera systems. "
        "Acquires frames via VAPIX and analyzes them with an AI vision model.\n\n"
        "**Authentication:**\n"
        "- `X-API-Key` — read access (analyze, frame, catalog listing)\n"
        "- `X-Admin-Key` — admin access (CRUD on cameras, actions, sequences)"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

@app.on_event("startup")
async def _startup() -> None:
    s = get_settings()
    setup_phoenix(s.phoenix_url, s.phoenix_api_key)


app.include_router(cameras.router)
app.include_router(actions.router)
app.include_router(analyze.router)
app.include_router(frame.router)
app.include_router(sequence.router)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    schema.setdefault("components", {})
    schema["components"]["securitySchemes"] = {
        "ReaderKey": {
            "type": "apiKey",
            "in": "header",
            "name": "X-API-Key",
            "description": "Read key — access to analyze, frame, catalog listing",
        },
        "AdminKey": {
            "type": "apiKey",
            "in": "header",
            "name": "X-Admin-Key",
            "description": "Admin key — CRUD on cameras, actions, sequences",
        },
    }

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/health", tags=["system"], summary="Health check", response_model=dict)
async def health():
    return {"status": "ok", "version": app.version}
