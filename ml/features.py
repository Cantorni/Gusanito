"""Builds the patch table the reservoir model learns from: dry-season greenness patches from Sentinel-2
(Microsoft Planetary Computer) plus weather (Open-Meteo), cached to ml/data/patches.csv.

A candidate patch is a connected blob of pixels that is still green late in the dry season, when almost
everything else is dry: the places where the pest can survive (see docs/propuesta-gusano-cogollero.md).

Usage (from the repo root, env gusanito-ml): python -m ml.features [--bbox lon_min,lat_min,lon_max,lat_max]
"""

import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import ndimage

DATA = Path(__file__).parent / "data"
PATCHES_CSV = DATA / "patches.csv"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
# Around the demo area near Chimunsi, Petauke district, Eastern Province (one of the top maize districts).
DEFAULT_BBOX = (31.40, -14.50, 31.80, -14.10)
DRY_YEAR = 2025
RES_M = 100  # one pixel = 1 ha: coarse on purpose, the model only needs the greenness pattern
GREEN_PCT = 94  # "still green" is relative: the top 6% NDVI of the late dry season (this savanna peaks at ~0.2-0.3)
MIN_PIXELS = 2
# shortcut: with MAX_SCENES set, each season uses only its least cloudy scenes (much faster, a bit noisier greenness);
# the pilot zone and the first tiles were built with every scene. Upgrade: unset it for a final run.
MAX_SCENES = int(os.environ.get("MAX_SCENES", 0))
MIN_DROP = 0.02  # NDVI points lost from early to late dry season; less than this is evergreen
WORLDCOVER_HOST = {30: 1, 40: 1, 20: 0.5, 90: 0.5, 10: 0}  # grassland, cropland, shrub, wetland, trees


def _utm(bbox):
    """UTM zone of the bbox centre, southern hemisphere (Zambia spans zones 34-36)."""
    return f"EPSG:327{int((bbox[0] + bbox[2]) / 2 // 6 + 31):02d}"


def _load_sentinel(bbox):
    """Dry-season composites as numpy arrays, cached on disk: downloading them is the slow step."""
    cache = DATA / f"s2_{'_'.join(f'{v:.3f}' for v in bbox)}.npz"
    if cache.exists():
        return dict(np.load(cache))
    out = _download_sentinel(bbox)
    DATA.mkdir(exist_ok=True)
    np.savez_compressed(cache, **out)
    return out


def _download_sentinel(bbox):
    import planetary_computer as pc
    from odc.stac import load
    from pystac_client import Client

    catalog = Client.open(STAC, modifier=pc.sign_inplace)
    kw = dict(bands=["B04", "B08", "B11", "SCL"], bbox=bbox, crs=_utm(bbox), resolution=RES_M,
              groupby="solar_day", chunks={"x": 512, "y": 512}, resampling={"SCL": "nearest", "*": "average"})
    out = {}
    for name, window in {"early": f"{DRY_YEAR}-06-01/{DRY_YEAR}-07-31", "late": f"{DRY_YEAR}-09-01/{DRY_YEAR}-10-31"}.items():
        items = list(catalog.search(collections=["sentinel-2-l2a"], bbox=bbox, datetime=window,
                                    query={"eo:cloud_cover": {"lt": 30}}).items())
        if MAX_SCENES:
            items = sorted(items, key=lambda i: i.properties["eo:cloud_cover"])[:MAX_SCENES]
        print(f"{name} dry season: {len(items)} Sentinel-2 scenes")
        ds = load(items, **kw)
        valid = ds.SCL.isin([4, 5, 6])  # vegetation, bare soil, water: not cloud, shadow or snow
        red, nir, swir = (ds[b].astype("float32").where(valid) / 10000 for b in ("B04", "B08", "B11"))
        out[name] = {"ndvi": ((nir - red) / (nir + red)).median("time", skipna=True).compute(),
                     "ndmi": ((nir - swir) / (nir + swir)).median("time", skipna=True).compute()}
    template = out["late"]["ndvi"]
    wc_items = list(catalog.search(collections=["esa-worldcover"], bbox=bbox).items())
    wc = load(wc_items, bands=["map"], like=template.to_dataset(name="grid"), chunks={})["map"].isel(time=0).compute()
    return {"ndvi_early": out["early"]["ndvi"].values, "ndvi_late": template.values, "ndmi_late": out["late"]["ndmi"].values,
            "worldcover": wc.values, "x": template.x.values, "y": template.y.values}


def _weather(bbox):
    """Dry-season weather at a coarse grid; every patch takes the nearest point."""
    lons, lats = np.linspace(bbox[0], bbox[2], 3), np.linspace(bbox[1], bbox[3], 3)
    rows = []
    for lat in lats:
        for lon in lons:
            url = ("https://archive-api.open-meteo.com/v1/archive?"
                   f"latitude={lat:.3f}&longitude={lon:.3f}&start_date={DRY_YEAR}-08-01&end_date={DRY_YEAR}-10-31"
                   "&daily=temperature_2m_mean,temperature_2m_max,precipitation_sum,et0_fao_evapotranspiration&timezone=UTC")
            for attempt in range(5):  # Open-Meteo throttles bursts: wait and retry
                try:
                    d = json.load(urllib.request.urlopen(url, timeout=60))["daily"]
                    break
                except (ValueError, OSError):
                    if attempt == 4:
                        raise
                    time.sleep(15 * (attempt + 1))
            rows.append({"lat": lat, "lon": lon, "temp_mean": np.mean(d["temperature_2m_mean"]),
                         "temp_max": np.mean(d["temperature_2m_max"]), "rain_mm": np.sum(d["precipitation_sum"]),
                         "et0_mm": np.sum(d["et0_fao_evapotranspiration"])})
    return pd.DataFrame(rows)


def candidates(df: pd.DataFrame) -> pd.DataFrame:
    """Drops patches that are green all year, which says nothing about the pest: tree cover, water, built-up or
    bare ground as the dominant land cover (host_cover 0), and patches whose greenness barely drops between early
    and late dry season. shortcut: a flat NDVI also removes a permanently irrigated field; WorldCover 2021 is old."""
    return df[(df.host_cover > 0) & (df.ndvi_drop >= MIN_DROP)].reset_index(drop=True)


def load(path=PATCHES_CSV) -> pd.DataFrame:
    return candidates(pd.read_csv(path))


def build(bbox=DEFAULT_BBOX, prefix: str = "zm") -> pd.DataFrame:
    from rasterio.warp import transform

    s2 = _load_sentinel(bbox)
    crs = _utm(bbox)
    ndvi_l, ndvi_e, ndmi_l, wc = s2["ndvi_late"], s2["ndvi_early"], s2["ndmi_late"], s2["worldcover"]
    print(f"late-dry NDVI percentiles 50/90/99: {np.nanpercentile(ndvi_l, [50, 90, 99]).round(2)}")
    green = ndvi_l > np.nanpercentile(ndvi_l, GREEN_PCT)
    labels, n = ndimage.label(green)
    print(f"{n} green blobs, keeping those of at least {MIN_PIXELS} ha")
    weather = _weather(bbox)
    xs, ys = s2["x"], s2["y"]

    rows = []
    for blob in range(1, n + 1):
        mask = labels == blob
        size = int(mask.sum())
        if size < MIN_PIXELS:
            continue
        yy, xx = np.nonzero(mask)
        x_c, y_c = xs[xx].mean(), ys[yy].mean()
        lon, lat = (v[0] for v in transform(crs, "EPSG:4326", [x_c], [y_c]))
        ring = ndimage.binary_dilation(mask, iterations=3) & ~mask  # the surroundings, to tell an oasis from a green landscape
        classes, counts = np.unique(wc[mask], return_counts=True)
        w = weather.iloc[((weather.lat - lat) ** 2 + (weather.lon - lon) ** 2).argmin()]
        rows.append({
            "patch_id": f"{prefix}-{blob:05d}", "lat": round(lat, 5), "lon": round(lon, 5), "area_ha": size,
            "ndvi_late": float(np.nanmean(ndvi_l[mask])), "ndvi_early": float(np.nanmean(ndvi_e[mask])),
            "ndmi_late": float(np.nanmean(ndmi_l[mask])),
            "ndvi_ring": float(np.nanmean(ndvi_l[ring])) if ring.any() else np.nan,
            "host_cover": WORLDCOVER_HOST.get(int(classes[counts.argmax()]), 0),
            "temp_mean": w.temp_mean, "temp_max": w.temp_max, "rain_mm": w.rain_mm, "et0_mm": w.et0_mm,
            "scene_window": f"{DRY_YEAR}-09-01/{DRY_YEAR}-10-31",
        })
    df = pd.DataFrame(rows)
    df["ndvi_drop"] = df.ndvi_early - df.ndvi_late
    df["oasis"] = df.ndvi_late - df.ndvi_ring
    return candidates(df.dropna())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", default=",".join(map(str, DEFAULT_BBOX)))
    args = ap.parse_args()
    DATA.mkdir(exist_ok=True)
    table = build(tuple(float(v) for v in args.bbox.split(",")))
    table.to_csv(PATCHES_CSV, index=False)
    print(f"{len(table)} patches -> {PATCHES_CSV}")
