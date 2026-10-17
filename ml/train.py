"""Trains the reservoir model and keeps a versioned registry in ml/models (v1, v2, ...).

A new model is promoted only if it beats the current one on a fixed holdout of patches that never take
part in training: this is the 'model improves with field data' loop. Env: gusanito-ml.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import brier_score_loss, roc_auc_score

MODELS = Path(__file__).parent / "models"
FEATURES = ["area_ha", "ndvi_late", "ndvi_early", "ndvi_drop", "ndmi_late", "ndvi_ring", "oasis", "host_cover",
            "temp_mean", "temp_max", "rain_mm", "et0_mm"]


def is_holdout(patch_id: str) -> bool:
    """Stable 20% of the patches, chosen by hash so it never changes between runs."""
    return int(hashlib.md5(patch_id.encode()).hexdigest(), 16) % 5 == 0


def current_version() -> dict | None:
    f = MODELS / "current.json"
    return json.loads(f.read_text()) if f.exists() else None


def train(patches: pd.DataFrame, labels: pd.DataFrame, holdout_labels: pd.DataFrame, promote: bool = True) -> dict:
    """labels: the verified answers to learn from. holdout_labels: answers for the held-out patches."""
    data = patches.merge(labels, on="patch_id")
    data = data[~data.patch_id.map(is_holdout)]
    test = patches.merge(holdout_labels, on="patch_id")
    test = test[test.patch_id.map(is_holdout)]
    model = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=150, random_state=0)
    model.fit(data[FEATURES], data.hay_plaga.astype(int))
    p = model.predict_proba(test[FEATURES])[:, 1]
    metrics = {"n_train": int(len(data)), "n_test": int(len(test)), "positives_train": int(data.hay_plaga.astype(int).sum()),
               "auc": round(float(roc_auc_score(test.hay_plaga.astype(int), p)), 4),
               "brier": round(float(brier_score_loss(test.hay_plaga.astype(int), p)), 4),
               "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
               "label_sources": data.label_source.value_counts().to_dict()}
    best = current_version()
    metrics["promoted"] = bool(promote and (best is None or metrics["auc"] > best["auc"]))
    version = 1 + max([int(d.name[1:]) for d in MODELS.glob("v*") if d.name[1:].isdigit()], default=0)
    out = MODELS / f"v{version}"
    out.mkdir(parents=True)
    joblib.dump(model, out / "model.joblib")
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    if metrics["promoted"]:
        (MODELS / "current.json").write_text(json.dumps({"version": version, "auc": metrics["auc"]}))
    return {"version": version} | metrics


def load_current():
    cur = current_version()
    if cur is None:
        raise SystemExit("no model yet: run python -m ml.pipeline first")
    return joblib.load(MODELS / f"v{cur['version']}" / "model.joblib"), cur["version"]
