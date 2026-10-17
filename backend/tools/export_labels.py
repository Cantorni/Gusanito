"""Exports verified field reports for the ML side and as few-shot examples for the Claude prompt.

- ml/data/labels.csv: one row per verified report (columns in README "Labels for the ML model").
- data/fewshot/: examples.jsonl + photos of the reports where Claude and the officer agree, a store the
  analysis prompt can draw few-shot examples from later (analysis.py does not use it yet).

Usage (from backend/): .venv/bin/python tools/export_labels.py [--csv PATH] [--fewshot DIR]
Storage as in tools/run_alerts.py (SQLITE_PATH by default).
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
if not os.environ.get("DATA_STORAGE_CONNECTION_STRING") and not os.environ.get("DATA_STORAGE_ACCOUNT"):
    os.environ.setdefault("SQLITE_PATH", str(BACKEND / "data" / "gusanito.db"))

from gusanito import config  # noqa: E402
from gusanito.alerts import VERIFIED  # noqa: E402
from gusanito.geo import bbox_center  # noqa: E402
from gusanito.storage import open_repository  # noqa: E402

COLUMNS = ["report_id", "reservoir_id", "lat", "lon", "scene_id", "scene_date", "hay_plaga", "confianza",
           "estado", "label_source", "officer_label", "received_at"]


def label_row(r: dict) -> dict:
    scene = r.get("scene") or {}
    if r.get("location"):
        lat, lon = r["location"]["lat"], r["location"]["lng"]
    elif scene.get("bbox"):  # no GPS: the reservoir's centre
        lat, lon = bbox_center(scene["bbox"])
    else:
        lat = lon = None
    return {
        "report_id": r["id"], "reservoir_id": r.get("reservoirId"), "lat": lat, "lon": lon,
        "scene_id": scene.get("id"), "scene_date": scene.get("date"),
        "hay_plaga": r["analisis"]["hay_plaga"], "confianza": r["analisis"]["confianza"], "estado": r["estado"],
        "label_source": "claude", "officer_label": r.get("label"), "received_at": r["receivedAt"],
    }


def verified(reports: list[dict]) -> list[dict]:
    """Verified field reports. Demo reports are left out: their location is fixed, not where the photo was taken."""
    return [r for r in reports if r.get("estado") in VERIFIED and r.get("analisis") and not r.get("demo")]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(BACKEND.parent / "ml" / "data" / "labels.csv"))
    parser.add_argument("--fewshot", default=str(BACKEND / "data" / "fewshot"))
    args = parser.parse_args()

    repo = open_repository(config.load())
    reports = verified(repo.list_reports(limit=100_000))

    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, COLUMNS)
        writer.writeheader()
        writer.writerows(label_row(r) for r in reports)
    print(f"{len(reports)} verified reports -> {args.csv}")

    examples = 0
    out = Path(args.fewshot)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "examples.jsonl", "w", encoding="utf-8") as f:
        for r in reports:
            if r.get("agrees") is not True:  # only where the officer confirms what Claude saw
                continue
            files = []
            for i, photo in enumerate(r.get("photos") or []):
                found = repo.read_photo(photo["file"])
                if found:
                    name = f"{r['id']}-{i}.{photo['file'].rsplit('.', 1)[-1]}"
                    (out / name).write_bytes(found[0])
                    files.append(name)
            f.write(json.dumps({"report_id": r["id"], "photos": files, "officer_label": r.get("label"),
                                "notes": r.get("notes"), "analisis": r["analisis"]}, ensure_ascii=False) + "\n")
            examples += 1
    print(f"{examples} few-shot examples -> {out}")


if __name__ == "__main__":
    main()
