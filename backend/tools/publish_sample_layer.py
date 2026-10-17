"""Publishes a sample reservoirs layer, so the PWA has patches to show until the satellite pipeline publishes
the real one. Five patches in rural farmland around Chimunsi and Matula villages (Eastern Province, Zambia,
between Petauke and Sinda), the demo area: see README "Run the demo locally".

Usage (from backend/):
    .venv/bin/python tools/publish_sample_layer.py              # local SQLite, data/gusanito.db
    .venv/bin/python tools/publish_sample_layer.py --azurite    # Azurite, from local.settings.json
"""

import argparse
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from gusanito.config import Config  # noqa: E402
from gusanito.storage import open_repository  # noqa: E402

DEMO_CENTER = (-14.30, 31.60)  # lat, lon; keep in sync with START in frontend/index.html
# Synthetic: stands for the Sentinel-2 scene the model ran on.
SCENE = {"escena": "demo-S2-T36L-20260905", "fecha": "2026-09-05"}


def square(feature_id, lat, lon, probability, side=0.004):
    ring = [[lon, lat], [lon + side, lat], [lon + side, lat + side], [lon, lat + side], [lon, lat]]
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"probabilidad": probability, **SCENE},
    }


def point(feature_id, lat, lon, probability):
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"probabilidad": probability, **SCENE},
    }


LAYER = {
    "type": "FeatureCollection",
    "features": [
        square("chimunsi-0007", -14.300, 31.598, 0.86),
        square("chimunsi-0012", -14.284, 31.568, 0.62),
        point("chimunsi-0020", -14.322, 31.627, 0.83),
        square("chimunsi-0031", -14.268, 31.642, 0.45),
        point("chimunsi-0044", -14.343, 31.556, 0.71),
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(BACKEND / "data" / "gusanito.db"))
    parser.add_argument("--azurite", action="store_true", help="publish to Azurite instead of SQLite")
    args = parser.parse_args()
    if args.azurite:
        settings = json.loads((BACKEND / "local.settings.json").read_text())["Values"]
        config = Config(settings["DATA_STORAGE_CONNECTION_STRING"], None, "", "", 0.7, None)
    else:
        config = Config(None, None, "", "", 0.7, None, sqlite_path=args.db)
    open_repository(config).write_reservoirs_layer(LAYER)
    print(f"published {len(LAYER['features'])} sample reservoirs ({'Azurite' if args.azurite else args.db})")


if __name__ == "__main__":
    main()
