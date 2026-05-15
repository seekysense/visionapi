from __future__ import annotations

from opentelemetry import trace

_TRACER_NAME = "VisionAPISmart"
_tracer: trace.Tracer | None = None


def setup_phoenix(phoenix_url: str, phoenix_api_key: str) -> None:
    if not phoenix_url:
        return

    try:
        from phoenix.otel import register
        from openinference.instrumentation.openai import OpenAIInstrumentor
    except ImportError:
        print("[telemetry] arize-phoenix-otel not installed — tracing disabled")
        return

    try:
        headers = {"api_key": phoenix_api_key} if phoenix_api_key else None
        register(
            project_name="VisionAPISmart",
            endpoint=f"{phoenix_url.rstrip('/')}/v1/traces",
            headers=headers,
        )
        OpenAIInstrumentor().instrument()

        global _tracer
        _tracer = trace.get_tracer(_TRACER_NAME)
        print(f"[telemetry] Phoenix tracing active → {phoenix_url}")
    except Exception as e:
        print(f"[telemetry] Phoenix setup failed ({e}) — tracing disabled")


def get_tracer() -> trace.Tracer | None:
    return _tracer
