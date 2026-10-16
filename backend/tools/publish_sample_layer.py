"""Publishes a sample reservoirs layer to the local storage (Azurite), so the PWA has patches to show
until the satellite pipeline publishes the real one. Three patches near Lusaka.

Usage (from backend/, with Azurite running): .venv/bin/python tools/publish_sample_layer.py
"""

import json
from pathlib import Path

from azure.storage.blob import BlobServiceClient

LAYERS_CONTAINER = "layers"
RESERVOIRS_BLOB = "reservoirs/current.geojson"


def square(feature_id, lon, lat, probability, side=0.004):
    ring = [[lon, lat], [lon + side, lat], [lon + side, lat + side], [lon, lat + side], [lon, lat]]
    return {
        "type": "Feature",
        "id": feature_id,
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"probabilidad": probability, "fecha": "2026-09-10"},
    }


LAYER = {
    "type": "FeatureCollection",
    "features": [
        square("lusaka-0007", 28.283, -15.418, 0.81),
        square("lusaka-0012", 28.295, -15.410, 0.46),
        {
            "type": "Feature",
            "id": "lusaka-0020",
            "geometry": {"type": "Point", "coordinates": [28.27, -15.425]},
            "properties": {"probabilidad": 0.63, "fecha": "2026-09-10"},
        },
    ],
}


def main() -> None:
    settings = json.loads((Path(__file__).resolve().parent.parent / "local.settings.json").read_text())
    blobs = BlobServiceClient.from_connection_string(settings["Values"]["DATA_STORAGE_CONNECTION_STRING"])
    container = blobs.get_container_client(LAYERS_CONTAINER)
    if not container.exists():
        container.create_container()
    container.upload_blob(RESERVOIRS_BLOB, json.dumps(LAYER), overwrite=True)
    print(f"published {len(LAYER['features'])} sample reservoirs to {LAYERS_CONTAINER}/{RESERVOIRS_BLOB}")


if __name__ == "__main__":
    main()
