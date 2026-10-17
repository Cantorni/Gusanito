"""Loads a reservoirs GeoJSON (for example ml/out/reservoirs/current.geojson from the ML pipeline) into the
app's storage.

Usage (from backend/):
    .venv/bin/python tools/publish_layer.py ../ml/out/reservoirs/current.geojson    # local SQLite
    DATA_STORAGE_CONNECTION_STRING=... .venv/bin/python tools/publish_layer.py FILE      # Azure (terraform output storage_connection_string)

Storage as in tools/run_alerts.py: SQLITE_PATH (default data/gusanito.db), or DATA_STORAGE_CONNECTION_STRING /
DATA_STORAGE_ACCOUNT.
"""

import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
if not os.environ.get("DATA_STORAGE_CONNECTION_STRING") and not os.environ.get("DATA_STORAGE_ACCOUNT"):
    os.environ.setdefault("SQLITE_PATH", str(BACKEND / "data" / "gusanito.db"))

from gusanito import config  # noqa: E402
from gusanito.storage import open_repository  # noqa: E402

layer = json.loads(Path(sys.argv[1]).read_text())
open_repository(config.load()).write_reservoirs_layer(layer)
print(f"published {len(layer['features'])} reservoirs")
