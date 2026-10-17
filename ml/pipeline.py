"""The continuous-improvement loop, end to end (also the entry point of the Azure ML job).

  python -m ml.pipeline                 build the patch table (cached), train on all labels, score, publish
  python -m ml.pipeline --curve         replay it with growing batches of verified labels: v1, v2, ... each
                                        retrained on more field data, promoted only if the holdout AUC rises
  python -m ml.pipeline --publish       also load the layer into the local app database (backend/data)
"""

import argparse
import subprocess
from pathlib import Path

import pandas as pd

from ml import features, labels, score, train

BACKEND = Path(__file__).resolve().parent.parent / "backend"


def publish() -> None:
    """The app's own tool does the loading, from the backend virtualenv (it has the Azure/SQLite code)."""
    subprocess.run([str(BACKEND / ".venv" / "bin" / "python"), "tools/publish_layer.py", str(score.OUT)], cwd=BACKEND, check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--curve", action="store_true")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="rebuild the patch table from Sentinel-2/weather")
    args = ap.parse_args()

    if args.rebuild or not features.PATCHES_CSV.exists():
        features.DATA.mkdir(exist_ok=True)
        features.build().to_csv(features.PATCHES_CSV, index=False)
    patches = pd.read_csv(features.PATCHES_CSV)
    verified = labels.simulated(patches)  # in a fixed random order: the first n are nested batches
    field = labels.field(patches)
    print(f"{len(patches)} patches, {len(field)} labels from the field app")

    if args.curve:
        for old in train.MODELS.glob("v*"):
            for f in old.iterdir():
                f.unlink()
            old.rmdir()
        (train.MODELS / "current.json").unlink(missing_ok=True)
        n_train = int((~patches.patch_id.map(train.is_holdout)).sum())
        sizes = [n for n in (20, 40, 80, 160, 320, 640, 1280) if n < n_train] + [n_train]
        for n in sizes:
            batch = pd.concat([verified[~verified.patch_id.map(train.is_holdout)].head(n), field]).drop_duplicates("patch_id", keep="last")
            r = train.train(patches, batch, verified)
            print(f"v{r['version']}: {r['n_train']:>5} verified patches -> AUC {r['auc']:.3f}  Brier {r['brier']:.3f}  "
                  f"{'promoted' if r['promoted'] else 'kept the previous model'}")
    else:
        batch = pd.concat([verified, field]).drop_duplicates("patch_id", keep="last")
        r = train.train(patches, batch, verified)
        print(f"v{r['version']}: AUC {r['auc']:.3f} {'promoted' if r['promoted'] else 'not promoted'}")

    doc = score.write(patches)
    print(f"layer: {len(doc['features'])} probable reservoirs -> {score.OUT}")
    if args.publish:
        publish()


if __name__ == "__main__":
    main()
