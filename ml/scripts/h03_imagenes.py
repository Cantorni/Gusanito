"""Híbrido, paso 3: recorte Sentinel-2 de 64x64 (640 m) por registro de FAMEWS.

Para cada registro busca la escena L2A con menos nubes (<30% de escena) dentro de ±15 días
de la fecha, que cubra el recorte completo. Reusa la lectura por ventana de 02_descarga.py.
Guarda bandas (B02, B03, B04, B08), SCL y WorldCover en datos_hibrido/recortes/<id>.npz
y la metadata en datos_hibrido/imagenes.jsonl (reanudable).
"""
import argparse
import datetime as dt
import importlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import pystac_client
from rasterio.enums import Resampling
from shapely.geometry import box, shape

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))
d = importlib.import_module("02_descarga")
DH = RAIZ / "datos_hibrido"
REC = DH / "recortes"
META = DH / "imagenes.jsonl"
lock = threading.Lock()
cat = pystac_client.Client.open(d.STAC, timeout=60)
WC_ITEMS = []


def procesar(r):
    f = dt.date.fromisoformat(str(r.fecha))
    caja = box(r.lon - 0.004, r.lat - 0.004, r.lon + 0.004, r.lat + 0.004)
    items = cat.search(
        collections=["sentinel-2-l2a"], intersects=caja.__geo_interface__,
        datetime=f"{f - dt.timedelta(days=15)}/{f + dt.timedelta(days=15)}",
        query={"eo:cloud_cover": {"lt": 30}}, sortby=[{"field": "eo:cloud_cover", "direction": "asc"}],
        max_items=10,
    ).item_collection()
    it = next((i for i in items if shape(i.geometry).contains(caja)), None)
    if it is None:
        return {"id": r.id, "sin_escena": True}
    epsg = it.properties.get("proj:epsg") or int(str(it.properties["proj:code"]).split(":")[1])
    tr, _ = d.grilla_recorte(r.lon, r.lat, epsg)
    b = np.stack([d.leer_banda(it.assets[x].href, tr) for x in d.BANDAS]).astype(np.int32)
    if float(it.properties.get("s2:processing_baseline", "0")) >= 4.0:
        b = np.clip(b - 1000, 0, None)
    scl = d.leer_reproyectado(it.assets["SCL"].href, epsg, tr, Resampling.nearest).astype(np.uint8)
    wc = np.zeros((d.TAM, d.TAM), np.uint8)
    for geom, href in WC_ITEMS:
        if geom.intersects(caja):
            parte = d.leer_reproyectado(href, epsg, tr, Resampling.nearest)
            wc = np.where(wc == 0, parte, wc)
    np.savez_compressed(REC / f"{r.id}.npz", bandas=b.astype(np.uint16), scl=scl, wc=wc)
    return {"id": r.id, "sin_escena": False, "escena": it.id,
            "fecha_escena": it.datetime.date().isoformat(), "nubes_escena": it.properties["eo:cloud_cover"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hilos", type=int, default=32)
    a = ap.parse_args()
    REC.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(DH / "registros.csv")
    # Una sola búsqueda de WorldCover 2021 para toda la extensión de los registros
    bb = box(df.lon.min() - 0.1, df.lat.min() - 0.1, df.lon.max() + 0.1, df.lat.max() + 0.1)
    WC_ITEMS.extend((shape(w.geometry), w.assets["map"].href) for w in cat.search(
        collections=["esa-worldcover"], intersects=bb.__geo_interface__,
        datetime="2021-01-01/2021-12-31").item_collection())
    print(f"teselas worldcover={len(WC_ITEMS)}", flush=True)
    hechos = {json.loads(l)["id"] for l in META.read_text().splitlines()} if META.exists() else set()
    pend = [r for r in df.itertuples() if r.id not in hechos]
    print(f"pendientes={len(pend)}", flush=True)
    t0, ok, err = time.time(), 0, 0
    with ThreadPoolExecutor(a.hilos) as ex, META.open("a") as fo:
        futs = {ex.submit(procesar, r): r.id for r in pend}
        for i, fu in enumerate(as_completed(futs), 1):
            try:
                fila = fu.result()
                with lock:
                    fo.write(json.dumps(fila) + "\n")
                    fo.flush()
                ok += 1
            except Exception as e:  # noqa: BLE001
                err += 1
                with lock:
                    (DH / "errores_imagenes.log").open("a").write(f"{futs[fu]}\t{e!r}\n")
            if i % 50 == 0 or i == len(pend):
                print(f"{i}/{len(pend)} ok={ok} err={err} {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
