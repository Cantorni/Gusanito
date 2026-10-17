"""Híbrido, paso 4: imagen + clima -> probabilidad de gusano cogollero.

Solo usa entradas disponibles en operación (nada que dependa de la encuesta FAMEWS):
- Imagen: recorte Sentinel-2 4x64x64 (B02, B03, B04, B08).
- Tabla: clima Open-Meteo 30 días + NDVI del recorte + composición WorldCover.

Compara tres modelos en el mismo set de prueba:
  1. solo tabla (gradient boosting), 2. solo imagen (CNN), 3. híbrido (CNN + red densa).
División espacial: bloques de 0,1° (~11 km) completos a train/val/test (70/15/15).

Salidas: modelo_hibrido/{hibrido.pt, metricas.json, predicciones_test.csv, dataset_hibrido.csv}
"""
import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))
CNN = importlib.import_module("05_entrenar").CNN
metricas_base = importlib.import_module("05_entrenar").metricas
DH, SAL = RAIZ / "datos_hibrido", RAIZ / "modelo_hibrido"
SEMILLA = 2026
MAX_INVALIDOS = 0.30
INVALIDOS_SCL = [0, 1, 3, 8, 9, 10]
WC = {10: "wc_arboles", 20: "wc_matorral", 30: "wc_pastizal", 40: "wc_cultivo", 50: "wc_urbano",
      60: "wc_suelo_desnudo", 80: "wc_agua", 90: "wc_humedal"}
CLIMA = ["gd30", "tmed30", "tmax30", "tmin30", "lluvia30", "dias_sin_lluvia", "hum_suelo30", "hum_suelo_max30"]
torch.manual_seed(SEMILLA)
np.random.seed(SEMILLA)


def armar():
    reg = pd.read_csv(DH / "registros.csv")
    cli = pd.read_csv(DH / "clima.csv").drop_duplicates("id")
    img = pd.DataFrame([json.loads(l) for l in (DH / "imagenes.jsonl").read_text().splitlines()])
    img = img[img.sin_escena == False].drop(columns="sin_escena")  # noqa: E712
    df = reg.merge(cli, on="id").merge(img, on="id")
    filas, X = [], []
    for r in df.itertuples():
        z = np.load(DH / "recortes" / f"{r.id}.npz")
        b = z["bandas"].astype(np.float32)
        inval = np.isin(z["scl"], INVALIDOS_SCL) | (b.sum(0) == 0)
        if inval.mean() > MAX_INVALIDOS:
            continue
        nd = (b[3] - b[2]) / np.maximum(b[3] + b[2], 1)
        v = nd[~inval]
        f = {"id": r.id, "pct_invalido": round(float(inval.mean()), 3),
             "ndvi_medio": float(v.mean()), "ndvi_p90": float(np.percentile(v, 90)),
             "pct_ndvi_05": float((v >= 0.5).mean())}
        for k, n in WC.items():
            f[n] = float((z["wc"] == k).mean())
        filas.append(f)
        X.append(b / 10000)
    feats = pd.DataFrame(filas)
    df = df.merge(feats, on="id")
    return df.reset_index(drop=True), np.stack(X)


def dividir(df):
    bloque = (df.lat // 0.1).astype(int).astype(str) + "_" + (df.lon // 0.1).astype(int).astype(str)
    bl = bloque.unique()
    np.random.default_rng(SEMILLA).shuffle(bl)
    cnt, acc, asig = bloque.value_counts(), 0, {}
    for b in bl:
        fr = acc / len(df)
        asig[b] = "train" if fr < 0.70 else ("val" if fr < 0.85 else "test")
        acc += cnt[b]
    return bloque.map(asig).values


class Hibrido(nn.Module):
    def __init__(self, n_tab, usar_img=True, usar_tab=True):
        super().__init__()
        self.usar_img, self.usar_tab = usar_img, usar_tab
        dim = 0
        if usar_img:
            self.cnn = CNN().f
            self.pool = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Flatten())
            dim += 64
        if usar_tab:
            self.mlp = nn.Sequential(nn.Linear(n_tab, 64), nn.ReLU(), nn.Dropout(0.2), nn.Linear(64, 32), nn.ReLU())
            dim += 32
        self.cabeza = nn.Sequential(nn.Dropout(0.3), nn.Linear(dim, 32), nn.ReLU(), nn.Linear(32, 1))

    def forward(self, img, tab):
        partes = []
        if self.usar_img:
            partes.append(self.pool(self.cnn(img)))
        if self.usar_tab:
            partes.append(self.mlp(tab))
        return self.cabeza(torch.cat(partes, 1)).squeeze(1)


def aumentar(x):
    x = torch.rot90(x, int(np.random.randint(4)), dims=(2, 3))
    return torch.flip(x, dims=(3,)) if np.random.rand() < 0.5 else x


def entrenar_red(nombre, I, T, y, idx, usar_img, usar_tab, disp):
    tr, va = idx["train"], idx["val"]
    m = Hibrido(T.shape[1], usar_img, usar_tab).to(disp)
    opt = torch.optim.Adam(m.parameters(), lr=1e-3, weight_decay=1e-3)
    pos = float((y[tr] == 0).sum() / max((y[tr] == 1).sum(), 1))
    lossf = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pos, device=disp))
    yt = torch.tensor(y, dtype=torch.float32)

    def pred(ix):
        m.eval()
        with torch.no_grad():
            return torch.cat([torch.sigmoid(m(I[ix[i:i + 256]].to(disp), T[ix[i:i + 256]].to(disp))).cpu()
                              for i in range(0, len(ix), 256)]).numpy()

    mejor, pac, hist, ruta = -1, 0, [], SAL / f"{nombre}.pt"
    for ep in range(1, 61):
        m.train()
        perm = tr[torch.randperm(len(tr)).numpy()]
        for i in range(0, len(perm), 32):
            b = perm[i:i + 32]
            xi = torch.stack([aumentar(x.unsqueeze(0)).squeeze(0) for x in I[b]]).to(disp)
            opt.zero_grad()
            lossf(m(xi, T[b].to(disp)), yt[b].to(disp)).backward()
            opt.step()
        auc = roc_auc_score(y[va], pred(va))
        hist.append({"epoca": ep, "auc_val": round(float(auc), 4)})
        if auc > mejor + 1e-3:
            mejor, pac = auc, 0
            torch.save(m.state_dict(), ruta)
        else:
            pac += 1
            if pac >= 8:
                break
    m.load_state_dict(torch.load(ruta))
    print(f"{nombre}: épocas={len(hist)} mejor AUC val={mejor:.3f}", flush=True)
    return pred(idx["test"]), hist


def resumen(y, p):
    out = metricas_base(y, (p >= 0.5).astype(int))
    out["auc"] = round(float(roc_auc_score(y, p)), 4) if len(set(y)) > 1 else None
    return out


def main():
    SAL.mkdir(exist_ok=True)
    df, X = armar()
    df["conjunto"] = dividir(df)
    tab_cols = CLIMA + ["ndvi_medio", "ndvi_p90", "pct_ndvi_05"] + list(WC.values())
    df.to_csv(SAL / "dataset_hibrido.csv", index=False)
    y = df.etiqueta.values.astype(int)
    idx = {c: np.where(df.conjunto.values == c)[0] for c in ["train", "val", "test"]}
    print({c: (len(v), round(float(y[v].mean()), 3)) for c, v in idx.items()}, flush=True)

    # Normalización con estadísticas de train
    mi = X[idx["train"]].mean(axis=(0, 2, 3), keepdims=True)
    si = X[idx["train"]].std(axis=(0, 2, 3), keepdims=True) + 1e-6
    Tn = df[tab_cols].values.astype(np.float32)
    mt, st = Tn[idx["train"]].mean(0), Tn[idx["train"]].std(0) + 1e-6
    I = torch.tensor((X - mi) / si, dtype=torch.float32)
    T = torch.tensor(np.nan_to_num((Tn - mt) / st), dtype=torch.float32)
    disp = "mps" if torch.backends.mps.is_available() else "cpu"

    # 1. Solo tabla
    gb = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_leaf_nodes=15,
                                        class_weight="balanced", random_state=SEMILLA)
    gb.fit(df[tab_cols].values[idx["train"]], y[idx["train"]])
    p_tab = gb.predict_proba(df[tab_cols].values[idx["test"]])[:, 1]
    # 2. Solo imagen, 3. Híbrido
    p_img, h_img = entrenar_red("solo_imagen", I, T, y, idx, True, False, disp)
    p_hib, h_hib = entrenar_red("hibrido", I, T, y, idx, True, True, disp)

    te = df.iloc[idx["test"]].copy()
    te["p_tabla"], te["p_imagen"], te["p_hibrido"] = p_tab.round(4), p_img.round(4), p_hib.round(4)
    te.to_csv(SAL / "predicciones_test.csv", index=False)
    yt = y[idx["test"]]
    res = {
        "n": {c: int(len(v)) for c, v in idx.items()},
        "prevalencia_test": round(float(yt.mean()), 4),
        "linea_base_clase_mayoritaria": round(float(max(yt.mean(), 1 - yt.mean())), 4),
        "test": {"solo_tabla": resumen(yt, p_tab), "solo_imagen": resumen(yt, p_img), "hibrido": resumen(yt, p_hib)},
        "test_hibrido_por_pais": {p: resumen(g.etiqueta.values, g.p_hibrido.values) for p, g in te.groupby("pais")},
        "variables_tabla": tab_cols,
        "historia": {"solo_imagen": h_img, "hibrido": h_hib},
    }
    torch.save({"estado": torch.load(SAL / "hibrido.pt"), "media_img": mi, "desv_img": si,
                "media_tab": mt, "desv_tab": st, "variables_tabla": tab_cols}, SAL / "hibrido.pt")
    (SAL / "metricas.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k != "historia"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
