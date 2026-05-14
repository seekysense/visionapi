from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from app.routers import actions, analyze, cameras, frame, sequence

app = FastAPI(
    title="VisionAPISmart",
    description=(
        "Semantic API layer for Axis camera systems. "
        "Acquires snapshots via VAPIX and analyzes them with an AI vision model.\n\n"
        "**Authentication:**\n"
        "- `X-API-Key` — read-only access (analyze, frame, catalogs)\n"
        "- `X-Admin-Key` — admin access (CRUD on cameras, actions, sequences)"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

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

    schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schema["components"]["securitySchemes"]["ReaderKey"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "Chiave di lettura — accesso a analyze, frame, GET cataloghi",
    }
    schema["components"]["securitySchemes"]["AdminKey"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-Admin-Key",
        "description": "Chiave amministratore — CRUD su cameras, actions, sequences",
    }

    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/health", tags=["system"], summary="Health check", response_model=dict)
async def health():
    return {"status": "ok", "version": app.version}
