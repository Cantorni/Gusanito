"""Modelo híbrido: imagen + variables -> probabilidad de gusano cogollero.

No descarga nada: recibe los datos ya listos.

Entradas:
- bandas:     arreglo 4x64x64 (B02, B03, B04, B08 de Sentinel-2 L2A), reflectancia x10000
              (en escenas con processing baseline >= 4.0, ya restado el offset de 1000)
- scl:        arreglo 64x64, banda SCL de Sentinel-2 (máscara de nubes)
- worldcover: arreglo 64x64, clases de ESA WorldCover 2021
- clima:      dict con las 8 variables de los 30 días previos:
              gd30, tmed30, tmax30, tmin30, lluvia30, dias_sin_lluvia, hum_suelo30, hum_suelo_max30

Uso como función:
    from modelo_hibrido import ModeloHibrido
    m = ModeloHibrido()
    p = m.predecir(bandas, scl, worldcover, clima)

Uso por consola:
    python modelo_hibrido.py recorte.npz clima.json
    (recorte.npz con arreglos "bandas", "scl", "wc"; clima.json con las 8 variables)
"""
import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from torchgeo.models import ResNet18_Weights, resnet18

RAIZ = Path(__file__).resolve().parents[1]
INVALIDOS_SCL = [0, 1, 3, 8, 9, 10]
WC = {10: "wc_arboles", 20: "wc_matorral", 30: "wc_pastizal", 40: "wc_cultivo", 50: "wc_urbano",
      60: "wc_suelo_desnudo", 80: "wc_agua", 90: "wc_humedal"}
CLIMA = ["gd30", "tmed30", "tmax30", "tmin30", "lluvia30", "dias_sin_lluvia", "hum_suelo30", "hum_suelo_max30"]


class ModeloHibrido:
    def __init__(self, ruta=RAIZ / "modelo_hibrido" / "hibrido_final.joblib"):
        paq = joblib.load(ruta)
        self.modelo, self.pca, self.variables = paq["modelo"], paq["pca"], paq["variables"]
        self.red = resnet18(weights=ResNet18_Weights.SENTINEL2_RGB_MOCO)
        self.red.fc = torch.nn.Identity()
        self.red.eval()

    def variables_de_imagen(self, bandas, scl, worldcover):
        b = np.asarray(bandas, dtype=np.float32)
        if b.shape != (4, 64, 64):
            raise ValueError(f"bandas debe ser 4x64x64, llegó {b.shape}")
        inval = np.isin(scl, INVALIDOS_SCL) | (b.sum(0) == 0)
        nd = (b[3] - b[2]) / np.maximum(b[3] + b[2], 1)
        v = nd[~inval] if (~inval).any() else nd.ravel()
        f = {"ndvi_medio": float(v.mean()), "ndvi_p90": float(np.percentile(v, 90)),
             "pct_ndvi_05": float((v >= 0.5).mean())}
        for k, n in WC.items():
            f[n] = float((np.asarray(worldcover) == k).mean())
        x = F.interpolate(torch.tensor(b[[2, 1, 0]][None]) / 10000, size=(224, 224),
                          mode="bilinear", align_corners=False)
        with torch.no_grad():
            emb = self.red(x).numpy()
        for i, val in enumerate(self.pca.transform(emb)[0]):
            f[f"emb_pca{i}"] = float(val)
        return f, float(inval.mean())

    def predecir(self, bandas, scl, worldcover, clima):
        faltan = [c for c in CLIMA if c not in clima]
        if faltan:
            raise ValueError(f"faltan variables de clima: {faltan}")
        f, pct_nubes = self.variables_de_imagen(bandas, scl, worldcover)
        f.update({c: float(clima[c]) for c in CLIMA})
        p = float(self.modelo.predict_proba(pd.DataFrame([f])[self.variables])[0, 1])
        return {"prob_gusano": round(p, 4), "pct_nubes_recorte": round(pct_nubes * 100, 1)}


if __name__ == "__main__":
    z = np.load(sys.argv[1])
    clima = json.loads(Path(sys.argv[2]).read_text())
    print(json.dumps(ModeloHibrido().predecir(z["bandas"], z["scl"], z["wc"], clima), ensure_ascii=False))
