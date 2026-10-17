"""Day-by-day replay for the landing page (landing/data.json): what the model predicts as field data arrives.

The predictions are real: every day a model is retrained on the verified patches so far (same features and
learner as ml/train.py) and scores all 1,257 real patches. What is synthetic is the test data: whether a visited
patch really has larvae (the hidden rule of ml/labels.py plus a coin toss), the farmers, and the visit plan.
Confirmed visits never appear on the map; they only become training labels. The SMS come from the real alert
engine (backend/gusanito/alerts.py), fed with that day's predictions only.

Two teams make the same number of visits per day against the SAME outcomes: one goes where the model ranks
highest, the other at random.

Usage (repo root, env gusanito-ml): python -m ml.demo_timeline
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from gusanito import alerts  # noqa: E402
from ml import features, labels, train  # noqa: E402

OUT = ROOT / "landing" / "data.json"
DAYS, VISITS_PER_DAY, FARMERS, INITIAL = 14, 12, 30, 100
DAY0 = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)


def fit(patches: pd.DataFrame, labelled: dict[str, bool]):
    data = patches[patches.patch_id.isin(labelled) & ~patches.patch_id.map(train.is_holdout)]
    y = data.patch_id.map(labelled).astype(int)
    model = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=150, random_state=0)
    if y.nunique() < 2:  # not enough variety to learn from yet: flat prior
        return lambda df: np.full(len(df), float(y.mean()) if len(y) else 0.5)
    model.fit(data[train.FEATURES], y)
    return lambda df: model.predict_proba(df[train.FEATURES])[:, 1]


def main() -> None:
    patches = features.load()
    rng = np.random.default_rng(11)
    patches["real"] = rng.random(len(patches)) < labels._truth(patches).to_numpy()  # hidden outcome of a visit
    real = dict(zip(patches.patch_id, patches.real))
    hold = patches[patches.patch_id.map(train.is_holdout)]

    # Synthetic farmers living near a patch (within ~13 km), Chilean numbers: the demo runs in Santiago.
    anchors = patches.sample(FARMERS, random_state=5, replace=True)
    farmers = [{"phone": f"+5698{i:07d}", "language": ["ny", "ny", "en", "es"][i % 4], "optOut": False,
                "location": {"lat": round(a.lat + rng.normal(0, .12), 5), "lng": round(a.lon + rng.normal(0, .12), 5)}}
               for i, a in enumerate(anchors.itertuples())]

    start = list(rng.choice(patches.patch_id, INITIAL, replace=False))  # the first verified photos
    labelled = {pid: bool(real[pid]) for pid in start}  # what the model learns from (guided team's findings)
    visited = set(start)
    random_queue = [p for p in rng.permutation(patches.patch_id) if p not in visited]
    outbox: list[dict] = []
    settings = alerts.Settings(pwa_url="https://gusanito.app", min_probability=0.9, per_farmer=True)
    found = {"guided": 0, "random": 0}
    seen = {"guided": 0, "random": 0}
    timeline = []
    for day in range(DAYS + 1):
        now = DAY0 + timedelta(days=day)
        predict = fit(patches, labelled)
        patches["prob"] = predict(patches)
        auc = float(roc_auc_score(hold.patch_id.map(real).astype(int), patches.set_index("patch_id").loc[hold.patch_id, "prob"]))
        if day > 0:
            for pid in list(patches[~patches.patch_id.isin(visited)].nlargest(VISITS_PER_DAY, "prob").patch_id):
                visited.add(pid); labelled[pid] = bool(real[pid]); seen["guided"] += 1; found["guided"] += int(real[pid])
            for pid in [random_queue.pop(0) for _ in range(VISITS_PER_DAY)]:
                seen["random"] += 1; found["random"] += int(real[pid])
            # Tomorrow's model learns from today's reports; today's map still shows the model they were planned with.
            patches["prob"] = fit(patches, labelled)(patches)
            auc = float(roc_auc_score(hold.patch_id.map(real).astype(int), patches.set_index("patch_id").loc[hold.patch_id, "prob"]))
        reservoirs = [alerts.Reservoir(r.patch_id, r.lat, r.lon, float(r.prob), False, None)
                      for r in patches[patches.prob >= 0.7].itertuples()]
        sent = alerts.plan(reservoirs, farmers, outbox, settings)
        for a in sent:
            outbox.append({"phone": a.phone, "reservoirId": a.reservoir_id, "reservoirIds": a.reservoir_ids,
                           "riskLevel": a.risk_level, "status": "enviado"})
        # Test data check: of the patches the model puts in each confidence tier, how many really have larvae.
        tiers = [{"n": int(m.sum()), "rate": round(float(patches.real[m].mean()), 3) if m.any() else None}
                 for m in ((patches.prob >= lo) & (patches.prob < hi) for lo, hi in ((.7, .8), (.8, .9), (.9, 1.01)))]
        timeline.append({
            "day": day, "labelled": len(labelled), "auc": round(auc, 3), "tiers": tiers,
            "p": [round(float(x), 2) for x in patches.prob],
            "found": dict(found), "seen": dict(seen),
            "sms": [{"phone": a.phone[-4:], "lang": a.language, "level": a.risk_level, "km": a.distance_km,
                     "dir": a.direction, "reservoir": a.reservoir_id, "text": a.sms} for a in sent],
        })

    curve = []  # the registry of ml/pipeline.py --curve: the model retrained on growing batches of verified patches
    for d in sorted(train.MODELS.glob("v*"), key=lambda d: int(d.name[1:])):
        m = json.loads((d / "metrics.json").read_text())
        curve.append({"version": d.name, "n": m["n_train"], "auc": m["auc"], "promoted": m["promoted"]})
    last = patches[patches.prob >= 0.7]
    doc = {
        "synthetic": True, "start": DAY0.date().isoformat(),
        "patches": [[r.patch_id, round(r.lat, 5), round(r.lon, 5), r.area_ha] for r in patches.itertuples()],
        "farmers": [[f["phone"][-4:], f["location"]["lat"], f["location"]["lng"], f["language"]] for f in farmers],
        "timeline": timeline, "curve": curve,
        "baseRate": round(float(patches.real.mean()), 3),
        "layerRate": round(float(last.real.mean()), 3), "layerSize": len(last),
    }
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"{OUT}  patches={len(patches)} base rate={doc['baseRate']} final layer={len(last)} rate={doc['layerRate']}")
    print("labelled:", [t["labelled"] for t in timeline])
    print("auc:", [t["auc"] for t in timeline])
    print("layer size:", [sum(x >= .7 for x in t["p"]) for t in timeline])
    print(f"found/visit: guided {found['guided']}/{seen['guided']}  random {found['random']}/{seen['random']}")
    print("SMS per day:", [len(t["sms"]) for t in timeline])


if __name__ == "__main__":
    main()
