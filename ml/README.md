# ml: fall armyworm probability from a satellite image + climate

A hybrid model that takes a Sentinel-2 image of a 640 m × 640 m patch and the climate of the previous 30 days, and returns the probability that fall armyworm is present there. The satellite pipeline uses it to fill `probabilidad` in the reservoirs layer (`layers/reservoirs/current.geojson`, see the main README).

```
scripts/modelo_hibrido.py      The model: image + variables in, probability out. Downloads nothing
scripts/predecir_hibrido.py    Shortcut for testing: lon, lat, date in; downloads the inputs and calls the model
scripts/h01..h05_*.py          Training pipeline (labels, climate, images, training, evaluation)
modelo_hibrido/                Trained model (hibrido_final.joblib) and evaluation results
ejemplo/                       One input (image + climate) to try the model
```

## Input

| Input | Shape | Source |
|---|---|---|
| `bandas` | 4 × 64 × 64, uint16 | Sentinel-2 L2A bands B02, B03, B04, B08 at 10 m, reflectance × 10000. Scenes with `s2:processing_baseline` >= 4.0 need the 1000 offset subtracted. Planetary Computer, collection `sentinel-2-l2a` |
| `scl` | 64 × 64 | Sentinel-2 SCL band of the same scene, resampled to 10 m (nearest). Used to drop cloud pixels |
| `worldcover` | 64 × 64 | ESA WorldCover 2021 classes on the same grid. Planetary Computer, collection `esa-worldcover` |
| `clima` | 8 numbers | Last 30 days up to the image date, from Open-Meteo (Historical Weather API, ERA5): `gd30` (degree-days above 12.6 °C), `tmed30`, `tmax30`, `tmin30` (°C), `lluvia30` (mm), `dias_sin_lluvia`, `hum_suelo30`, `hum_suelo_max30` (soil moisture 0-7 cm, m³/m³) |

One scene, one date: the clearest Sentinel-2 scene (< 30% clouds) within ±15 days. Open-Meteo is free for non-commercial use only.

## Output

```json
{"prob_gusano": 0.1716, "pct_nubes_recorte": 0.0}
```

## Use

```sh
cd ml
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt

# The model, with the example input (an Ethiopia record with no armyworm)
.venv/bin/python scripts/modelo_hibrido.py ejemplo/recorte_h00000.npz ejemplo/clima_h00000.json

# Shortcut: point and date, downloads everything (the date must be >= 5 days old for Open-Meteo)
.venv/bin/python scripts/predecir_hibrido.py 28.283 -15.418 2026-09-15
```

From Python:

```python
from modelo_hibrido import ModeloHibrido
modelo = ModeloHibrido()                     # load once
modelo.predecir(bandas, scl, worldcover, clima)
```

The first load downloads the pretrained ResNet18 weights (42 MB) to the torch cache.

## How it works

1. Drops cloud pixels with `scl`.
2. Verdor (NDVI) from red and near infrared: mean, 90th percentile, share of pixels with NDVI >= 0.5.
3. Land cover shares from WorldCover: trees, shrub, grass, crop, built-up, bare, water, wetland.
4. Red, green and blue go through a ResNet18 pretrained on Sentinel-2 (SSL4EO-S12, MoCo, via torchgeo), reduced to 16 numbers with PCA.
5. Those 27 image variables plus the 8 climate variables go into a gradient boosting classifier.

## Training data and results

- **Labels:** FAO FAMEWS field scouting and trap records (Niassy et al. 2021, PLOS ONE; [Zenodo 4737393](https://zenodo.org/records/4737393)), June 2018 to April 2019, in Ethiopia, Rwanda, Tanzania, Uganda, Burundi and Kenya. 1 if the scouting found any infested plant or the trap confirmed moths. 2,182 records after removing the ones with no clear scene, 61.8% positive.
- **Evaluation:** 5-fold spatial cross-validation (whole 0.1° ≈ 11 km blocks held out).

| Model | AUC (mean ± sd) |
|---|---|
| Climate only | 0.66 ± 0.06 |
| Image only | 0.70 ± 0.06 |
| **Hybrid** | **0.73 ± 0.05** |

Most important variables: 30-day mean maximum temperature, 30-day rainfall, then two image components (`modelo_hibrido/importancia_variables.csv`).

## Limits

- **It does not transfer to a country it has not seen.** Trained on five countries and tested on the sixth, AUC is near chance in 4 of 6 (`modelo_hibrido/auc_dejando_un_pais_fuera.csv`). For Zambia it needs local labels: the officers' verified photos are those labels, and the model should be retrained with them each season.
- Labels come from maize fields during the season, not from dry-season reservoirs.
- WorldCover is from 2021.

## Retrain

The training scripts write their data next to them (`datos_hibrido/`, `datos_externos/`, ignored by git). In order:

```sh
# FAMEWS xlsx from Zenodo 4737393 into datos_externos/dryad/famews.xlsx, then:
.venv/bin/python scripts/h01_etiquetas_famews.py
.venv/bin/python scripts/h02_clima.py --entrada datos_hibrido/registros.csv --salida datos_hibrido/clima.csv
.venv/bin/python scripts/h03_imagenes.py --hilos 32
.venv/bin/python scripts/h04_entrenar_hibrido.py      # also builds modelo_hibrido/dataset_hibrido.csv
.venv/bin/python scripts/h05_hibrido_preentrenado.py  # spatial CV + hibrido_final.joblib
```

`hibrido_final.joblib` must be loaded with the scikit-learn version in `requirements.txt`.
