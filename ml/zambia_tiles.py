"""Builds the patch table for all of Zambia, tile by tile, and scores it (landing/zambia.json).

Zambia is cut into 0.4-degree tiles on the same grid as the pilot zone; every tile touching the country outline
(landing/zambia-outline.geojson, Natural Earth 10m) is downloaded, cached in ml/data/zambia/ and turned into
patches. Patches outside the outline are dropped. A rerun only does the missing tiles, so it can be interrupted.

The pilot-zone table (ml/data/patches.csv) is part of the result. Patch ids are unique per tile.

Usage (repo root, env gusanito-ml):
    python -m ml.zambia_tiles --workers 5            # everything (hours: ~100 s per tile, ~400 tiles)
    python -m ml.zambia_tiles --workers 5 --assemble # only merge what is cached, write the table and the layer
    python -m ml.zambia_tiles --rings 2              # just the 5x5 block around the pilot zone
"""

import argparse
import json
import math
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import box, shape
from shapely.prepared import prep

from ml import features, train

ROOT = Path(__file__).resolve().parent.parent
TILES = features.DATA / "zambia"
TABLE = features.DATA / "patches_zambia.csv"
OUTLINE = ROOT / "landing" / "zambia-outline.geojson"
OUT = ROOT / "landing" / "zambia.json"
PILOT = features.DEFAULT_BBOX
STEP = 0.4


def country():
    return shape(json.loads(OUTLINE.read_text())["geometry"])


def bbox_of(i: int, j: int) -> tuple[float, float, float, float]:
    return (round(PILOT[0] + i * STEP, 3), round(PILOT[1] + j * STEP, 3), round(PILOT[2] + i * STEP, 3), round(PILOT[3] + j * STEP, 3))


def grid(rings: int | None) -> list[tuple[int, int]]:
    if rings is not None:
        cells = [(i, j) for i in range(-rings, rings + 1) for j in range(-rings, rings + 1)]
    else:
        lo, hi = country().bounds[:2], country().bounds[2:]
        i0, i1 = math.floor((lo[0] - PILOT[0]) / STEP), math.ceil((hi[0] - PILOT[0]) / STEP)
        j0, j1 = math.floor((lo[1] - PILOT[1]) / STEP), math.ceil((hi[1] - PILOT[1]) / STEP)
        zam = prep(country())
        cells = [(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1) if zam.intersects(box(*bbox_of(i, j)))]
    return [c for c in cells if c != (0, 0)]


def tile(ij: tuple[int, int]) -> str:
    i, j = ij
    out = TILES / f"{i}_{j}.csv"
    if not out.exists():
        try:
            features.build(bbox_of(i, j), prefix=f"t{i}_{j}").to_csv(out, index=False)
        except Exception as e:  # no scenes or a flaky download must not stop the others
            return f"{i},{j} failed: {e!r}"
    return f"{i},{j} ok"


def assemble(cells: list[tuple[int, int]]) -> pd.DataFrame:
    frames = [features.load()] + [features.load(TILES / f"{i}_{j}.csv") for i, j in cells if (TILES / f"{i}_{j}.csv").exists()]
    patches = pd.concat(frames, ignore_index=True)
    zam = prep(country())
    from shapely.geometry import Point
    inside = np.array([zam.contains(Point(x, y)) for x, y in zip(patches.lon, patches.lat)])
    patches = patches[inside].reset_index(drop=True)
    patches.to_csv(TABLE, index=False)
    return patches


def score(patches: pd.DataFrame, tiles: tuple[int, int]) -> None:
    model, version = train.load_current()  # the model trained on the whole-country table; it only predicts here
    version = f"v{version}"
    patches = patches.assign(prob=model.predict_proba(patches[train.FEATURES])[:, 1])
    keep = patches[patches.prob >= 0.7]
    OUT.write_text(json.dumps({"model": version, "total": len(patches), "tiles": tiles[0], "tilesTotal": tiles[1],
                               "points": [[r.patch_id, round(r.lat, 4), round(r.lon, 4), round(float(r.prob), 2), r.area_ha] for r in keep.itertuples()]},
                              separators=(",", ":")))
    print(f"{len(patches)} patches, {len(keep)} with probability >= 0.7 -> {OUT}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rings", type=int, default=None, help="only the (2n+1)x(2n+1) block around the pilot zone")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--assemble", action="store_true", help="skip downloading, merge the cached tiles")
    a = ap.parse_args()
    TILES.mkdir(parents=True, exist_ok=True)
    cells = grid(a.rings)
    todo = [c for c in cells if not (TILES / f"{c[0]}_{c[1]}.csv").exists()]
    print(f"{len(cells)} tiles, {len(todo)} to do", flush=True)
    if not a.assemble:
        with ProcessPoolExecutor(a.workers) as pool:
            for n, r in enumerate(pool.map(tile, todo), 1):
                print(f"[{n}/{len(todo)}] {r}", flush=True)
    patches = assemble(cells)
    print(f"table: {len(patches)} patches inside the outline -> {TABLE}")
    score(patches, (sum((TILES / f"{i}_{j}.csv").exists() for i, j in cells) + 1, len(cells) + 1))


if __name__ == "__main__":
    main()
