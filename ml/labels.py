"""Labels for the reservoir model.

simulated: stand-in for field verification. A hidden rule says how likely a green patch is to be a real
reservoir, and a coin toss decides the 'verified' answer. Replace with real ones as they arrive:
backend/tools/export_labels.py writes ml/data/labels.csv (Claude's verdict on the officers' photos).
"""

from pathlib import Path

import numpy as np
import pandas as pd

LABELS_CSV = Path(__file__).parent / "data" / "labels.csv"
MATCH_M = 150  # a field report belongs to the patch whose centre is this close


def _truth(df: pd.DataFrame) -> np.ndarray:
    """Hidden ground truth (what the pest 'really' prefers); the model never sees this function."""
    z = lambda c: (df[c] - df[c].mean()) / (df[c].std() or 1)  # dry-season NDVI is low everywhere: use relative values
    warm = np.exp(-(((df.temp_mean - 25.0) / 5.0) ** 2))  # armyworm develops best around 25 C
    logit = (-1.2 + 1.1 * z("ndvi_late") + 0.7 * z("ndmi_late") + 0.8 * z("oasis") + 0.6 * z("ndvi_drop")
             + 0.9 * df.host_cover + 1.0 * warm + 0.5 * np.log(df.area_ha))
    return 1 / (1 + np.exp(-logit))


def simulated(patches: pd.DataFrame, seed: int = 7) -> pd.DataFrame:
    """One verified answer per patch, in a fixed random order so that 'the first n' are nested batches."""
    rng = np.random.default_rng(seed)
    out = patches[["patch_id"]].copy()
    out["hay_plaga"] = rng.random(len(out)) < _truth(patches).to_numpy()
    out["label_source"] = "simulada"
    return out.iloc[rng.permutation(len(out))].reset_index(drop=True)


def field(patches: pd.DataFrame) -> pd.DataFrame:
    """Real verified reports from the app (ml/data/labels.csv), matched by reservoir id, else to the nearest patch."""
    if not LABELS_CSV.exists():
        return pd.DataFrame(columns=["patch_id", "hay_plaga", "label_source"])
    reports = pd.read_csv(LABELS_CSV).dropna(subset=["hay_plaga"])
    rows = []
    known = set(patches.patch_id)
    for r in reports.itertuples():
        if r.reservoir_id in known:  # the app's reservoir ids are the model's patch ids
            rows.append({"patch_id": r.reservoir_id, "hay_plaga": bool(r.hay_plaga), "label_source": "campo"})
            continue
        dy = (patches.lat - r.lat) * 111_000
        dx = (patches.lon - r.lon) * 111_000 * np.cos(np.radians(r.lat))
        d = np.hypot(dx, dy)
        if d.min() <= MATCH_M:
            rows.append({"patch_id": patches.patch_id[d.argmin()], "hay_plaga": bool(r.hay_plaga), "label_source": "campo"})
    return pd.DataFrame(rows, columns=["patch_id", "hay_plaga", "label_source"])
