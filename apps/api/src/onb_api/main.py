"""ASGI entry point: `uvicorn onb_api.main:app`.

Telemetry starts first, so the FastAPI instrumentation reaches the app (F-4: the instrumentor swaps
the `FastAPI` class). Export settings come from the standard `OTEL_*` environment.
"""

from onb_telemetry import init_telemetry

from onb_api.app import create_app
from onb_api.health import SERVICE_NAME

telemetry = init_telemetry(SERVICE_NAME)
app = create_app()
