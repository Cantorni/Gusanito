"""Prepares an environment for the presentation: the Zambia reservoirs layer from the model
(layers/zambia-reservoirs.geojson) and synthetic farmers subscribed to alerts around the demo location at
Chimunsi, so confirming a reservoir from the PWA produces SMS in the receiver page's "SMS enviados" view.
Safe to run again: the layer is replaced and each farmer is stored by phone.

Usage (from backend/):
    .venv/bin/python tools/seed_demo.py                                     # local SQLite, data/gusanito.db
    DATA_STORAGE_CONNECTION_STRING=... .venv/bin/python tools/seed_demo.py      # Azure (terraform output storage_connection_string)

Storage as in tools/run_alerts.py. The phone numbers are made up and nothing is sent: on Azure the SMS sender
is simulated (SMS_SENDER is not set).
"""

import argparse
import json
import math
import os
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tools"))
if not os.environ.get("DATA_STORAGE_CONNECTION_STRING") and not os.environ.get("DATA_STORAGE_ACCOUNT"):
    os.environ.setdefault("SQLITE_PATH", str(BACKEND / "data" / "gusanito.db"))

from publish_sample_layer import DEMO_CENTER  # noqa: E402

from gusanito import config  # noqa: E402
from gusanito.storage import open_repository  # noqa: E402


LAYER_FILE = BACKEND.parent / "layers" / "zambia-reservoirs.geojson"


def demo_layer() -> dict:
    return json.loads(LAYER_FILE.read_text())


def farmers(n: int, when: datetime) -> list[dict]:
    """The same n farmers every run, spread over a ~12 km radius disc around the demo centre."""
    rng = random.Random(7)
    lat0, lon0 = DEMO_CENTER
    out = []
    for i in range(n):
        # Uniform in the disc: 0.11 deg latitude is ~12 km.
        r, a = 0.11 * rng.random() ** 0.5, rng.uniform(0, 2 * math.pi)
        lat, lng = round(lat0 + r * math.sin(a), 5), round(lon0 + r * math.cos(a), 5)
        out.append({
            "phone": f"+26097100{i:04d}", "location": {"lat": lat, "lng": lng, "accuracy": None},
            "language": rng.choice(["ny", "ny", "en", "es"]), "createdAt": when.isoformat(),
            "updatedAt": when.isoformat(), "optOut": i == 3,  # one farmer unsubscribed: never alerted
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--farmers", type=int, default=20)
    args = parser.parse_args()
    repo = open_repository(config.load())
    layer = demo_layer()
    repo.write_reservoirs_layer(layer)
    for farmer in farmers(args.farmers, datetime.now(UTC)):
        repo.save_subscriber(farmer)
    print(f"{len(layer['features'])} reservoirs and {args.farmers} farmers")


if __name__ == "__main__":
    main()
