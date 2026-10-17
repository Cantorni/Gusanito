"""Híbrido, paso 2: variables de clima de los 30 días previos a cada registro (Open-Meteo).

Fuente: Open-Meteo Historical Weather API (reanálisis ERA5/ERA5-Land), la misma que se usa
en operación con ~5 días de atraso. Gratis para uso no comercial.

Variables por registro (ventana de 30 días que termina el día del registro):
- gd30: grados-día sobre 12,6 °C (umbral de desarrollo, Du Plessis et al. 2020)
- tmed30, tmax30, tmin30: temperatura media, máxima media y mínima media (°C)
- lluvia30: lluvia acumulada (mm); dias_sin_lluvia: días desde el último día con >= 1 mm (máx 30)
- hum_suelo30, hum_suelo_max30: humedad del suelo 0-7 cm, media y máximo diario (m³/m³)

Uso: python h02_clima.py --entrada datos_hibrido/registros.csv --salida datos_hibrido/clima.csv
     (sirve igual para los recortes de Kasungu: requiere columnas id, lat, lon, fecha)
"""
import argparse
import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

RAIZ = Path(__file__).resolve().parents[1]
URL = "https://archive-api.open-meteo.com/v1/archive"
T_BASE = 12.6
VENTANA = 30


def pedir(lat, lon, inicio, fin):
    params = {
        "latitude": round(lat, 4), "longitude": round(lon, 4),
        "start_date": inicio.isoformat(), "end_date": fin.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,soil_moisture_0_to_7cm_mean",
        "timezone": "UTC",
    }
    for intento in range(6):
        r = requests.get(URL, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(20 * (intento + 1))
            continue
        r.raise_for_status()
        j = r.json()
        dia = pd.DataFrame(j["daily"]).assign(time=lambda x: pd.to_datetime(x.time).dt.date)
        return dia.rename(columns={"soil_moisture_0_to_7cm_mean": "hum"}).set_index("time")
    raise RuntimeError("Open-Meteo: demasiados 429")


def variables(serie, fecha):
    v = serie.loc[fecha - dt.timedelta(days=VENTANA - 1): fecha]
    tmed = (v.temperature_2m_max + v.temperature_2m_min) / 2
    lluvia = v.precipitation_sum.fillna(0)
    dias_lluvia = np.where(lluvia.values >= 1)[0]
    return {
        "gd30": round(float(np.clip(tmed - T_BASE, 0, None).sum()), 1),
        "tmed30": round(float(tmed.mean()), 2),
        "tmax30": round(float(v.temperature_2m_max.mean()), 2),
        "tmin30": round(float(v.temperature_2m_min.mean()), 2),
        "lluvia30": round(float(lluvia.sum()), 1),
        "dias_sin_lluvia": int(len(v) - 1 - dias_lluvia[-1]) if len(dias_lluvia) else VENTANA,
        "hum_suelo30": round(float(v.hum.mean()), 4),
        "hum_suelo_max30": round(float(v.hum.max()), 4),
    }


def por_lugar(grupo):
    fechas = sorted(grupo.fecha.unique())
    serie = pedir(grupo.lat.iloc[0], grupo.lon.iloc[0],
                  fechas[0] - dt.timedelta(days=VENTANA), fechas[-1])
    return [{"id": r.id, **variables(serie, r.fecha)} for r in grupo.itertuples()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrada", required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--hilos", type=int, default=4)
    a = ap.parse_args()

    df = pd.read_csv(RAIZ / a.entrada)
    df["fecha"] = pd.to_datetime(df.fecha).dt.date
    salida = RAIZ / a.salida
    hechos = set(pd.read_csv(salida).id) if salida.exists() else set()
    df = df[~df.id.isin(hechos)]
    # Mismo lugar (~1 km, la grilla de Open-Meteo es ~9 km) = una sola consulta
    df["lugar"] = df.lat.round(2).astype(str) + "_" + df.lon.round(2).astype(str)
    grupos = [g for _, g in df.groupby("lugar")]
    print(f"registros pendientes={len(df)} consultas={len(grupos)}", flush=True)

    filas, errores = [], 0
    with ThreadPoolExecutor(a.hilos) as ex:
        for i, fut in enumerate([ex.submit(por_lugar, g) for g in grupos], 1):
            try:
                filas += fut.result()
            except Exception as e:  # noqa: BLE001
                errores += 1
                print("error:", e, flush=True)
            if i % 100 == 0:
                print(f"{i}/{len(grupos)}", flush=True)
                pd.DataFrame(filas).to_csv(salida, mode="a", header=not salida.exists(), index=False)
                filas = []
    if filas:
        pd.DataFrame(filas).to_csv(salida, mode="a", header=not salida.exists(), index=False)
    print(f"listo. errores={errores}")


if __name__ == "__main__":
    main()
