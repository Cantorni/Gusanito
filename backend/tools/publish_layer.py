"""Loads a reservoirs GeoJSON (for example ml/out/reservoirs/current.geojson from the ML pipeline) into the
local app database.

Usage (from backend/): .venv/bin/python tools/publish_layer.py ../ml/out/reservoirs/current.geojson
"""

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from gusanito.config import Config  # noqa: E402
from gusanito.storage import open_repository  # noqa: E402

layer = json.loads(Path(sys.argv[1]).read_text())
open_repository(Config(None, None, "", "", 0.7, None, sqlite_path=str(BACKEND / "data" / "gusanito.db"))).write_reservoirs_layer(layer)
print(f"published {len(layer['features'])} reservoirs")
