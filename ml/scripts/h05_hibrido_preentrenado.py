"""Híbrido v2: características de imagen de una red preentrenada + clima, con validación cruzada espacial.

Imagen: ResNet18 preentrenada con SSL4EO-S12 (MoCo, Sentinel-2 RGB; Wang et al. 2022,
arXiv:2211.07044) vía torchgeo. Cada recorte (B04, B03, B02) se escala a 224x224 y se
normaliza /10000 (transform de torchgeo); se extrae el vector de 512 y se reduce con PCA (16).

Se comparan con validación cruzada espacial de 5 partes (bloques de 0,1° ~ 11 km, GroupKFold):
  - clima:   8 variables Open-Meteo de 30 días
  - imagen:  NDVI + WorldCover + PCA de la red preentrenada
  - hibrido: todo lo anterior
Modelo: HistGradientBoosting con pesos balanceados. PCA se ajusta solo con el train de cada parte.

Salidas en modelo_hibrido/: embeddings.npy, cv_metricas.json, cv_predicciones.csv,
importancia_variables.csv, hibrido_final.joblib (entrenado con todos los datos)
"""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F
from sklearn.decomposition import PCA
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold
from torchgeo.models import ResNet18_Weights, resnet18

RAIZ = Path(__file__).resolve().parents[1]
DH, SAL = RAIZ / "datos_hibrido", RAIZ / "modelo_hibrido"
SEMILLA = 2026
N_PCA = 16
CLIMA = ["gd30", "tmed30", "tmax30", "tmin30", "lluvia30", "dias_sin_lluvia", "hum_suelo30", "hum_suelo_max30"]
IMG_FIJAS = ["ndvi_medio", "ndvi_p90", "pct_ndvi_05", "wc_arboles", "wc_matorral", "wc_pastizal",
             "wc_cultivo", "wc_urbano", "wc_suelo_desnudo", "wc_agua", "wc_humedal"]
PCA_COLS = [f"emb_pca{i}" for i in range(N_PCA)]


def red_preentrenada():
    m = resnet18(weights=ResNet18_Weights.SENTINEL2_RGB_MOCO)
    m.fc = torch.nn.Identity()
    return m.eval()


def embeddings(df):
    ruta = SAL / "embeddings.npy"
    if ruta.exists():
        e = np.load(ruta)
        if len(e) == len(df):
            return e
    disp = "mps" if torch.backends.mps.is_available() else "cpu"
    m = red_preentrenada().to(disp)
    salida = []
    with torch.no_grad():
        for i in range(0, len(df), 64):
            lote = [np.load(DH / "recortes" / f"{rid}.npz")["bandas"][[2, 1, 0]].astype(np.float32)
                    for rid in df.id.iloc[i:i + 64]]
            x = torch.tensor(np.stack(lote)) / 10000
            x = F.interpolate(x, size=(224, 224), mode="bilinear", align_corners=False)
            salida.append(m(x.to(disp)).cpu().numpy())
    e = np.concatenate(salida)
    np.save(ruta, e)
    return e


def gbm():
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                          l2_regularization=1.0, class_weight="balanced", random_state=SEMILLA)


def main():
    df = pd.read_csv(SAL / "dataset_hibrido.csv")  # ya filtrado por nubes (<=30% inválido) en h04
    E = embeddings(df)
    y = df.etiqueta.values
    grupos = ((df.lat // 0.1).astype(int).astype(str) + "_" + (df.lon // 0.1).astype(int).astype(str)).values
    conjuntos = {"clima": CLIMA, "imagen": IMG_FIJAS + PCA_COLS, "hibrido": CLIMA + IMG_FIJAS + PCA_COLS}

    oof = {k: np.zeros(len(df)) for k in conjuntos}
    por_parte = {k: [] for k in conjuntos}
    importancias = []
    for parte, (tr, te) in enumerate(GroupKFold(n_splits=5).split(df, y, grupos)):
        pca = PCA(N_PCA, random_state=SEMILLA).fit(E[tr])
        Z = pd.DataFrame(pca.transform(E), columns=PCA_COLS)
        X = pd.concat([df[CLIMA + IMG_FIJAS].reset_index(drop=True), Z], axis=1)
        for k, cols in conjuntos.items():
            m = gbm().fit(X.iloc[tr][cols], y[tr])
            p = m.predict_proba(X.iloc[te][cols])[:, 1]
            oof[k][te] = p
            por_parte[k].append(roc_auc_score(y[te], p))
            if k == "hibrido":
                pi = permutation_importance(m, X.iloc[te][cols], y[te], scoring="roc_auc",
                                            n_repeats=5, random_state=SEMILLA)
                importancias.append(pd.Series(pi.importances_mean, index=cols))
        print(f"parte {parte + 1}: " + ", ".join(f"{k}={por_parte[k][-1]:.3f}" for k in conjuntos), flush=True)

    def resumen(p):
        pred = (p >= 0.5).astype(int)
        vp, vn = int(((pred == 1) & (y == 1)).sum()), int(((pred == 0) & (y == 0)).sum())
        fp, fn = int(((pred == 1) & (y == 0)).sum()), int(((pred == 0) & (y == 1)).sum())
        return {"auc_oof": round(float(roc_auc_score(y, p)), 4), "exactitud": round((vp + vn) / len(y), 4),
                "precision": round(vp / max(vp + fp, 1), 4), "recall": round(vp / max(vp + fn, 1), 4),
                "matriz_confusion": {"VP": vp, "VN": vn, "FP": fp, "FN": fn}}

    res = {
        "n": len(df), "prevalencia": round(float(y.mean()), 4), "bloques_espaciales": int(len(set(grupos))),
        "linea_base": {"auc": 0.5, "exactitud_clase_mayoritaria": round(float(max(y.mean(), 1 - y.mean())), 4)},
        "modelos": {k: {"auc_por_parte": [round(float(a), 4) for a in por_parte[k]],
                        "auc_media": round(float(np.mean(por_parte[k])), 4),
                        "auc_desv": round(float(np.std(por_parte[k])), 4), **resumen(oof[k])} for k in conjuntos},
    }
    (SAL / "cv_metricas.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    pd.DataFrame({"id": df.id, "pais": df.pais, "etiqueta": y, **{f"p_{k}": oof[k].round(4) for k in conjuntos}}) \
        .to_csv(SAL / "cv_predicciones.csv", index=False)
    imp = pd.concat(importancias, axis=1).mean(axis=1).sort_values(ascending=False)
    imp.rename("caida_auc_al_permutar").to_csv(SAL / "importancia_variables.csv")

    # Modelo final con todos los datos (para predecir)
    pca = PCA(N_PCA, random_state=SEMILLA).fit(E)
    Xf = pd.concat([df[CLIMA + IMG_FIJAS].reset_index(drop=True),
                    pd.DataFrame(pca.transform(E), columns=PCA_COLS)], axis=1)
    final = gbm().fit(Xf[conjuntos["hibrido"]], y)
    joblib.dump({"modelo": final, "pca": pca, "variables": conjuntos["hibrido"]}, SAL / "hibrido_final.joblib")

    print(json.dumps({k: {x: v[x] for x in ["auc_media", "auc_desv", "auc_oof", "exactitud", "recall"]}
                      for k, v in res["modelos"].items()}, indent=2))
    print(imp.round(4).head(12).to_string())


if __name__ == "__main__":
    main()
