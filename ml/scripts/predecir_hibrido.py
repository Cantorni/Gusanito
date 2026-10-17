"""Probabilidad de gusano cogollero en un punto y fecha, con el modelo híbrido final.

Uso: python predecir_hibrido.py LON LAT FECHA      (FECHA = AAAA-MM-DD; clima de Open-Meteo
                                                  disponible con ~5 días de atraso)
Descarga: recorte Sentinel-2 (±15 días, <30% nubes), WorldCover 2021 y clima de 30 días.
"""
import datetime as dt
import importlib
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pystac_client
import torch
import torch.nn.functional as F
from rasterio.enums import Resampling
from shapely.geometry import box, shape

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))
d = importlib.import_module("02_descarga")
clima = importlib.import_module("h02_clima")
h5 = importlib.import_module("h05_hibrido_preentrenado")
INVALIDOS_SCL = [0, 1, 3, 8, 9, 10]


def main():
    lon, lat, fecha = float(sys.argv[1]), float(sys.argv[2]), dt.date.fromisoformat(sys.argv[3])
    cat = pystac_client.Client.open(d.STAC, timeout=60)
    caja = box(lon - 0.004, lat - 0.004, lon + 0.004, lat + 0.004)
    items = cat.search(collections=["sentinel-2-l2a"], intersects=caja.__geo_interface__,
                       datetime=f"{fecha - dt.timedelta(days=15)}/{fecha + dt.timedelta(days=15)}",
                       query={"eo:cloud_cover": {"lt": 30}},
                       sortby=[{"field": "eo:cloud_cover", "direction": "asc"}], max_items=10).item_collection()
    it = next((i for i in items if shape(i.geometry).contains(caja)), None)
    if it is None:
        raise SystemExit("Sin escena Sentinel-2 despejada en ±15 días")
    epsg = it.properties.get("proj:epsg") or int(str(it.properties["proj:code"]).split(":")[1])
    tr, _ = d.grilla_recorte(lon, lat, epsg)
    b = np.stack([d.leer_banda(it.assets[x].href, tr) for x in d.BANDAS]).astype(np.int32)
    if float(it.properties.get("s2:processing_baseline", "0")) >= 4.0:
        b = np.clip(b - 1000, 0, None)
    b = b.astype(np.float32)
    scl = d.leer_reproyectado(it.assets["SCL"].href, epsg, tr, Resampling.nearest)
    wc = np.zeros((d.TAM, d.TAM), np.uint8)
    for w in cat.search(collections=["esa-worldcover"], intersects=caja.__geo_interface__,
                        datetime="2021-01-01/2021-12-31").item_collection():
        wc = np.where(wc == 0, d.leer_reproyectado(w.assets["map"].href, epsg, tr, Resampling.nearest), wc)

    inval = np.isin(scl, INVALIDOS_SCL) | (b.sum(0) == 0)
    nd = (b[3] - b[2]) / np.maximum(b[3] + b[2], 1)
    v = nd[~inval] if (~inval).any() else nd.ravel()
    f = {"ndvi_medio": float(v.mean()), "ndvi_p90": float(np.percentile(v, 90)), "pct_ndvi_05": float((v >= 0.5).mean())}
    for k, n in {10: "wc_arboles", 20: "wc_matorral", 30: "wc_pastizal", 40: "wc_cultivo", 50: "wc_urbano",
                 60: "wc_suelo_desnudo", 80: "wc_agua", 90: "wc_humedal"}.items():
        f[n] = float((wc == k).mean())
    serie = clima.pedir(lat, lon, fecha - dt.timedelta(days=clima.VENTANA), fecha)
    f.update(clima.variables(serie, fecha))

    red = h5.red_preentrenada()
    x = F.interpolate(torch.tensor(b[[2, 1, 0]][None]) / 10000, size=(224, 224), mode="bilinear", align_corners=False)
    with torch.no_grad():
        e = red(x).numpy()
    paq = joblib.load(RAIZ / "modelo_hibrido" / "hibrido_final.joblib")
    for i, val in enumerate(paq["pca"].transform(e)[0]):
        f[f"emb_pca{i}"] = float(val)
    p = float(paq["modelo"].predict_proba(pd.DataFrame([f])[paq["variables"]])[0, 1])
    print(json.dumps({"lon": lon, "lat": lat, "fecha": str(fecha), "escena": it.id,
                      "pct_nubes_recorte": round(float(inval.mean()) * 100, 1),
                      "prob_gusano": round(p, 3), "variables": {k: round(v, 3) for k, v in f.items()
                                                                if not k.startswith("emb_")}},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
