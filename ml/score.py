"""Scores every patch with the current model and writes the reservoirs layer the PWA reads."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from ml.train import FEATURES, load_current

OUT = Path(__file__).parent / "out" / "reservoirs" / "current.geojson"


def layer(patches: pd.DataFrame, min_prob: float = 0.7) -> dict:
    model, version = load_current()
    patches = patches.assign(prob=model.predict_proba(patches[FEATURES])[:, 1])
    features = []
    for r in patches[patches.prob >= min_prob].itertuples():
        half = np.sqrt(r.area_ha * 1e4) / 2  # metres, a square of the patch's area
        dlat = half / 111_000
        dlon = half / (111_000 * np.cos(np.radians(r.lat)))
        ring = [[r.lon - dlon, r.lat - dlat], [r.lon + dlon, r.lat - dlat], [r.lon + dlon, r.lat + dlat],
                [r.lon - dlon, r.lat + dlat], [r.lon - dlon, r.lat - dlat]]
        features.append({"type": "Feature", "id": r.patch_id, "geometry": {"type": "Polygon", "coordinates": [[ring]][0]},
                         "properties": {"probabilidad": round(float(r.prob), 3), "fecha": r.scene_window.split("/")[1],
                                        "escena": f"S2-{r.scene_window}", "modelo": f"v{version}"}})
    return {"type": "FeatureCollection", "features": features}


def write(patches: pd.DataFrame) -> dict:
    doc = layer(patches)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc))
    return doc
