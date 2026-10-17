"""Paso 2: descarga de recortes 64x64 (640 m) de Sentinel-2 L2A + SCL + WorldCover.

- Consulta una sola vez el catálogo STAC (Planetary Computer) para el área y las fechas.
- Sortea puntos al azar dentro del área; para cada uno elige una escena de la
  estación sorteada (70% seca, 30% lluviosa) que cubra el recorte completo.
- Lee solo la ventana del recorte (no descarga escenas completas).
- Guarda cada recorte en datos/recortes/<id>.npz y su metadata en datos/crudo.jsonl.
  Se puede reanudar: los ids ya guardados se saltan.

Uso: python 02_descarga.py --n 50          (piloto)
     python 02_descarga.py --n 9000        (completo; n = intentos totales acumulados)
"""
import argparse
import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import geopandas as gpd
import numpy as np
import planetary_computer
import pystac_client
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from shapely.geometry import Point, box
from shapely.ops import transform as shp_transform

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("GDAL_HTTP_MULTIPLEX", "YES")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "4")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "2")
os.environ.setdefault("VSI_CACHE", "TRUE")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "30")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "10")

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / "datos"
RECORTES = DATOS / "recortes"
CRUDO = DATOS / "crudo.jsonl"
CACHE_ITEMS = DATOS / "items_s2.json"

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
TAM = 64  # píxeles
RES = 10  # metros
BANDAS = ["B02", "B03", "B04", "B08"]
INVALIDOS_SCL = {0, 1, 3, 8, 9, 10}
FECHAS = {
    "seca": "2024-05-01/2024-10-31",
    "lluviosa": "2024-01-01/2024-03-31",
}
PROB_SECA = 0.7
SEMILLA = 2026

lock = threading.Lock()


def cargar_items(catalogo, area_geom):
    if CACHE_ITEMS.exists():
        return json.loads(CACHE_ITEMS.read_text())
    items = {}
    for estacion, rango in FECHAS.items():
        res = catalogo.search(
            collections=["sentinel-2-l2a"],
            intersects=area_geom.__geo_interface__,
            datetime=rango,
            query={"eo:cloud_cover": {"lt": 20}},
        ).item_collection()
        items[estacion] = [
            {
                "id": it.id,
                "fecha": it.datetime.date().isoformat(),
                "geom": it.geometry,
                "epsg": it.properties["proj:epsg"] if "proj:epsg" in it.properties
                else int(str(it.properties["proj:code"]).split(":")[1]),
                "baseline": float(it.properties.get("s2:processing_baseline", "0")),
                "nubes": it.properties["eo:cloud_cover"],
                "hrefs": {b: it.assets[b].href for b in BANDAS + ["SCL"]},
            }
            for it in res
        ]
    CACHE_ITEMS.write_text(json.dumps(items))
    return items


def cargar_worldcover(catalogo, area_geom):
    res = catalogo.search(
        collections=["esa-worldcover"],
        intersects=area_geom.__geo_interface__,
        datetime="2021-01-01/2021-12-31",
    ).item_collection()
    return [{"geom": it.geometry, "href": it.assets["map"].href} for it in res]


def grilla_recorte(lon, lat, epsg):
    """Transform y bounds de un recorte de 64x64 a 10 m alineado a la grilla de 10 m."""
    x, y = Transformer.from_crs(4326, epsg, always_xy=True).transform(lon, lat)
    mitad = TAM * RES / 2
    x0 = np.floor((x - mitad) / RES) * RES
    y1 = np.floor((y + mitad) / RES) * RES
    tr = from_origin(x0, y1, RES, RES)
    return tr, (x0, y1 - TAM * RES, x0 + TAM * RES, y1)


def leer_reproyectado(href, epsg, tr, resampling):
    with rasterio.open(planetary_computer.sign(href)) as src:
        with WarpedVRT(src, crs=f"EPSG:{epsg}", transform=tr, width=TAM, height=TAM,
                       resampling=resampling) as vrt:
            return vrt.read(1)


def leer_banda(href, tr):
    with rasterio.open(planetary_computer.sign(href)) as src:
        ventana = rasterio.windows.from_bounds(
            tr.c, tr.f - TAM * RES, tr.c + TAM * RES, tr.f, transform=src.transform
        ).round_offsets().round_lengths()
        return src.read(1, window=ventana, boundless=True, fill_value=0,
                        out_shape=(TAM, TAM))


def procesar(tarea, wc_items):
    rid, lon, lat, estacion, item = tarea["id"], tarea["lon"], tarea["lat"], tarea["estacion"], tarea["item"]
    epsg = item["epsg"]
    tr, bounds = grilla_recorte(lon, lat, epsg)

    bandas = np.stack([leer_banda(item["hrefs"][b], tr) for b in BANDAS]).astype(np.int32)
    if item["baseline"] >= 4.0:  # desde 2022 los L2A traen offset de +1000
        bandas = np.clip(bandas - 1000, 0, None)
    bandas = bandas.astype(np.uint16)
    scl = leer_reproyectado(item["hrefs"]["SCL"], epsg, tr, Resampling.nearest).astype(np.uint8)

    caja_ll = shp_transform(Transformer.from_crs(epsg, 4326, always_xy=True).transform, box(*bounds))
    wc = np.zeros((TAM, TAM), np.uint8)
    for w in wc_items:
        from shapely.geometry import shape
        if shape(w["geom"]).intersects(caja_ll):
            parte = leer_reproyectado(w["href"], epsg, tr, Resampling.nearest)
            wc = np.where(wc == 0, parte, wc)

    np.savez_compressed(RECORTES / f"{rid}.npz", bandas=bandas, scl=scl, wc=wc)
    return {
        "id": rid, "lon": lon, "lat": lat, "estacion": estacion,
        "fecha": item["fecha"], "id_escena": item["id"], "epsg": epsg,
        "nubes_escena": item["nubes"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True, help="intentos totales acumulados")
    ap.add_argument("--hilos", type=int, default=16)
    args = ap.parse_args()

    RECORTES.mkdir(parents=True, exist_ok=True)
    area = gpd.read_file(DATOS / "area.geojson").union_all()
    catalogo = pystac_client.Client.open(STAC)
    items = cargar_items(catalogo, area)
    wc_items = cargar_worldcover(catalogo, area)
    print(f"escenas: seca={len(items['seca'])} lluviosa={len(items['lluviosa'])}; worldcover={len(wc_items)}")

    from shapely.geometry import shape
    geoms = {e: [shape(i["geom"]) for i in items[e]] for e in items}

    # Tareas deterministas: el intento k siempre es el mismo punto/escena (permite reanudar)
    rng = random.Random(SEMILLA)
    minx, miny, maxx, maxy = area.bounds
    tareas = []
    while len(tareas) < args.n:
        lon, lat = rng.uniform(minx, maxx), rng.uniform(miny, maxy)
        estacion = "seca" if rng.random() < PROB_SECA else "lluviosa"
        idx_candidatos = list(range(len(items[estacion])))
        rng.shuffle(idx_candidatos)
        if not area.contains(Point(lon, lat)):
            continue
        # recorte completo dentro de la huella de la escena (~0,004° de margen)
        caja = box(lon - 0.004, lat - 0.004, lon + 0.004, lat + 0.004)
        elegido = next((items[estacion][i] for i in idx_candidatos if geoms[estacion][i].contains(caja)), None)
        if elegido is None:
            continue
        tareas.append({"id": f"r{len(tareas):05d}", "lon": round(lon, 5), "lat": round(lat, 5),
                       "estacion": estacion, "item": elegido})

    hechos = set()
    if CRUDO.exists():
        hechos = {json.loads(l)["id"] for l in CRUDO.read_text().splitlines() if l.strip()}
    pendientes = [t for t in tareas if t["id"] not in hechos]
    print(f"tareas={len(tareas)} ya hechas={len(hechos)} pendientes={len(pendientes)}")

    t0, ok, errores = time.time(), 0, 0
    with ThreadPoolExecutor(args.hilos) as ex, CRUDO.open("a") as f:
        futuros = {ex.submit(procesar, t, wc_items): t for t in pendientes}
        for i, fut in enumerate(as_completed(futuros), 1):
            try:
                fila = fut.result()
                with lock:
                    f.write(json.dumps(fila) + "\n")
                    f.flush()
                ok += 1
            except Exception as e:  # noqa: BLE001
                errores += 1
                with lock:
                    (DATOS / "errores.log").open("a").write(f"{futuros[fut]['id']}\t{e!r}\n")
            if i % 50 == 0 or i == len(pendientes):
                dt = time.time() - t0
                print(f"{i}/{len(pendientes)} ok={ok} err={errores} {dt:.0f}s ({dt / i:.2f}s/recorte)", flush=True)
    print(json.dumps({"ok": ok, "errores": errores, "segundos": round(time.time() - t0)}))


if __name__ == "__main__":
    main()
