# ML: reservoir model

Predicts, for each patch that is still green late in the dry season, the probability that it shelters fall
armyworm. Inputs: Sentinel-2 greenness/moisture (Planetary Computer) and dry-season weather (Open-Meteo).
There is no image model: Claude reads the officers' photos and its verdict becomes the label.

```
python -m ml.features            Sentinel-2 + weather -> ml/data/patches.csv (cached; minutes the first time)
python -m ml.pipeline --curve    v1..vN trained on growing verified batches; a model is promoted only if its holdout AUC rises
python -m ml.pipeline --publish  train on everything, score, load the layer into the local app database
```

The loop with the app: `backend/tools/export_labels.py` writes `ml/data/labels.csv` from the verified reports;
`ml/labels.py` matches each report to its nearest patch; the next pipeline run retrains with them and publishes
`current.geojson`, which the PWA draws. Until real reports exist, labels are simulated (`label_source=simulada`).

Run in Azure ML: `ml/azureml/job.yml` (see the header of the file).
Env: `~/miniconda3/envs/gusanito-ml` (python 3.12, scikit-learn, odc-stac, pystac-client).
