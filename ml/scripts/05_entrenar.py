"""Paso 5: entrenamiento de la CNN "zona verde sí/no".

- Entrada: recorte 4x64x64 (B02, B03, B04, B08), reflectancia /10000, normalizado con
  media y desviación del set de entrenamiento.
- Línea base sin entrenar: umbral sobre el NDVI medio del recorte, elegido en train.
- CNN chica, aumento con rotaciones y espejos, early stopping con validación.

Salidas: modelo/modelo.pt, modelo/metricas.json, modelo/predicciones_test.csv
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn

RAIZ = Path(__file__).resolve().parents[1]
DATOS = RAIZ / "datos"
MODELO = RAIZ / "modelo"
SEMILLA = 2026
torch.manual_seed(SEMILLA)
np.random.seed(SEMILLA)


class CNN(nn.Module):
    def __init__(self):
        super().__init__()

        def bloque(i, o):
            return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                                 nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                                 nn.MaxPool2d(2))

        self.f = nn.Sequential(bloque(4, 16), bloque(16, 32), bloque(32, 64), bloque(64, 64))
        self.c = nn.Sequential(nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Dropout(0.3), nn.Linear(64, 1))

    def forward(self, x):
        return self.c(self.f(x)).squeeze(1)


def cargar(df):
    X = np.stack([np.load(RAIZ / a)["bandas"].astype(np.float32) / 10000 for a in df.archivo])
    return X, df.etiqueta.values.astype(np.float32)


def aumentar(x):
    k = np.random.randint(4)
    x = torch.rot90(x, k, dims=(2, 3))
    if np.random.rand() < 0.5:
        x = torch.flip(x, dims=(3,))
    return x


def metricas(y, p):
    y, p = np.asarray(y).astype(int), np.asarray(p).astype(int)
    tp, tn = int(((p == 1) & (y == 1)).sum()), int(((p == 0) & (y == 0)).sum())
    fp, fn = int(((p == 1) & (y == 0)).sum()), int(((p == 0) & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"n": len(y), "exactitud": round((tp + tn) / len(y), 4), "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(2 * prec * rec / (prec + rec), 4) if prec + rec else 0.0,
            "matriz_confusion": {"VP": tp, "VN": tn, "FP": fp, "FN": fn}}


def main():
    MODELO.mkdir(exist_ok=True)
    df = pd.read_csv(DATOS / "dataset.csv")
    tr, va, te = (df[df.conjunto == c].reset_index(drop=True) for c in ["train", "val", "test"])
    Xtr, ytr = cargar(tr)
    Xva, yva = cargar(va)
    Xte, yte = cargar(te)
    media = Xtr.mean(axis=(0, 2, 3), keepdims=True)
    desv = Xtr.std(axis=(0, 2, 3), keepdims=True) + 1e-6
    norm = lambda X: torch.tensor((X - media) / desv)  # noqa: E731

    # Línea base: umbral sobre NDVI medio elegido en train
    umbrales = np.arange(0.0, 0.8, 0.01)
    acc = [((tr.ndvi_medio >= u).astype(int) == tr.etiqueta).mean() for u in umbrales]
    u_best = float(umbrales[int(np.argmax(acc))])
    base_te = (te.ndvi_medio >= u_best).astype(int)

    disp = "mps" if torch.backends.mps.is_available() else "cpu"
    modelo = CNN().to(disp)
    opt = torch.optim.Adam(modelo.parameters(), lr=1e-3, weight_decay=1e-4)
    perdida = nn.BCEWithLogitsLoss()
    Ttr, Tva, Tte = norm(Xtr), norm(Xva), norm(Xte)
    ytr_t = torch.tensor(ytr)

    def predecir(T):
        modelo.eval()
        with torch.no_grad():
            return torch.cat([torch.sigmoid(modelo(T[i:i + 256].to(disp))).cpu()
                              for i in range(0, len(T), 256)]).numpy()

    mejor, paciencia, historia, t0 = 1e9, 0, [], time.time()
    for ep in range(1, 41):
        modelo.train()
        idx = torch.randperm(len(Ttr))
        tot = 0.0
        for i in range(0, len(idx), 64):
            b = idx[i:i + 64]
            xb = torch.stack([aumentar(x.unsqueeze(0)).squeeze(0) for x in Ttr[b]]).to(disp)
            opt.zero_grad()
            l = perdida(modelo(xb), ytr_t[b].to(disp))
            l.backward()
            opt.step()
            tot += l.item() * len(b)
        pva = predecir(Tva)
        lva = float(nn.functional.binary_cross_entropy(torch.tensor(pva).clamp(1e-6, 1 - 1e-6), torch.tensor(yva)))
        accva = float(((pva >= 0.5) == yva).mean())
        historia.append({"epoca": ep, "loss_train": round(tot / len(Ttr), 4), "loss_val": round(lva, 4), "exactitud_val": round(accva, 4)})
        print(historia[-1], flush=True)
        if lva < mejor - 1e-4:
            mejor, paciencia = lva, 0
            torch.save({"estado": modelo.state_dict(), "media": media, "desv": desv}, MODELO / "modelo.pt")
        else:
            paciencia += 1
            if paciencia >= 6:
                break

    ck = torch.load(MODELO / "modelo.pt", weights_only=False)
    modelo.load_state_dict(ck["estado"])
    pte = predecir(Tte)
    te["prob_verde"] = pte.round(4)
    te["prediccion"] = (pte >= 0.5).astype(int)
    te["base_ndvi_medio"] = base_te
    te.to_csv(MODELO / "predicciones_test.csv", index=False)

    res = {
        "dispositivo": disp, "epocas": len(historia), "segundos": round(time.time() - t0),
        "n": {"train": len(tr), "val": len(va), "test": len(te)},
        "cnn_test": metricas(yte, te.prediccion),
        "cnn_test_por_estacion": {e: metricas(g.etiqueta, g.prediccion) for e, g in te.groupby("estacion")},
        "linea_base": {"regla": f"NDVI medio del recorte >= {u_best:.2f} (umbral elegido en train)",
                       "test": metricas(yte, base_te)},
        "historia": historia,
    }
    (MODELO / "metricas.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k != "historia"}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
