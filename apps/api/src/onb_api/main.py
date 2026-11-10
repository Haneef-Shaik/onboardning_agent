"""ASGI entry point: `uvicorn onb_api.main:app`.

Telemetry starts first, so the FastAPI instrumentation reaches the app (F-4: the instrumentor swaps
the `FastAPI` class). Export settings come from the standard `OTEL_*` environment.

Startup fails (`ConfigError`) when `JWT_SECRET`, `MONGO_URI` or `MONGO_DATABASE` is missing (PRD §7.8).
"""

import os

from onb_data import connect
from onb_telemetry import init_telemetry

from onb_api.app import create_app
from onb_api.auth.mongo_users import MongoUserStore
from onb_api.auth.settings import AuthSettings, require_env
from onb_api.health import SERVICE_NAME

auth = AuthSettings.from_env()
mongo_uri = require_env(os.environ, "MONGO_URI")
mongo_database = require_env(os.environ, "MONGO_DATABASE")
# Telemetry before the Mongo client: PyMongo applies the tracing listener only to clients made after it.
telemetry = init_telemetry(SERVICE_NAME)
store = connect(mongo_uri, mongo_database)
app = create_app(auth=auth, users=MongoUserStore(store))
