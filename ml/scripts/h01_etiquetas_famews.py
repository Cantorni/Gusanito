"""Híbrido, paso 1: etiquetas reales de gusano cogollero desde FAMEWS (Niassy et al. 2021).

Fuente: Zenodo 4737393 (espejo del Dryad doi:10.5061/dryad.9kd51c5gg), hoja "EA_data".
Etiqueta:
- Revisión de campo (scouting): 1 si scoutingPercentageFAW > 0; 0 si = 0.
- Trampa: 1 si trapsConfirmedFAW > 0; 0 si = 0.
Un registro puede tener ambas; si cualquiera es 1, la etiqueta es 1.
Se agrupan registros del mismo lugar (~100 m) y mismo día.

Salida: datos_hibrido/registros.csv
"""
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
ENTRADA = RAIZ / "datos_externos" / "dryad" / "famews.xlsx"
SALIDA = RAIZ / "datos_hibrido" / "registros.csv"


def main():
    d = pd.read_excel(ENTRADA, sheet_name=0)
    d["fecha"] = pd.to_datetime(d["date"], errors="coerce", utc=True).dt.date
    d = d.dropna(subset=["fecha", "latitude", "longitude"])

    sc = d.scoutingPercentageFAW
    tr = d.trapsConfirmedFAW
    d["et_scouting"] = np.where(sc.notna(), (sc > 0).astype(float), np.nan)
    d["et_trampa"] = np.where(tr.notna(), (tr > 0).astype(float), np.nan)
    d = d[d.et_scouting.notna() | d.et_trampa.notna()].copy()
    d["etiqueta"] = d[["et_scouting", "et_trampa"]].max(axis=1).astype(int)

    d["lat_r"], d["lon_r"] = d.latitude.round(3), d.longitude.round(3)
    g = d.groupby(["lat_r", "lon_r", "fecha"], as_index=False).agg(
        lat=("latitude", "mean"), lon=("longitude", "mean"), pais=("country", "first"),
        etiqueta=("etiqueta", "max"), pct_faw=("scoutingPercentageFAW", "max"),
        trampa_faw=("trapsConfirmedFAW", "max"), riego=("cropIrrigation", "first"),
        cultivo=("cropMain", "first"), etapa=("cropStage", "first"),
        fuente=("scouting", lambda s: "scouting" if s.any() else "trampa"),
    )
    g = g.drop(columns=["lat_r", "lon_r"]).sort_values(["fecha", "pais"]).reset_index(drop=True)
    g.insert(0, "id", [f"h{i:05d}" for i in range(len(g))])
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    g.to_csv(SALIDA, index=False)
    print(f"registros={len(g)} positivos={int(g.etiqueta.sum())} negativos={int((g.etiqueta == 0).sum())}")
    print(g.groupby(["pais", "etiqueta"]).size().unstack(fill_value=0).to_string())
    print(g.groupby("fuente").etiqueta.agg(["count", "mean"]).round(3).to_string())


if __name__ == "__main__":
    main()
