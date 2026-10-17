# Los dos modelos de ML: qué son y por qué conviven

Hay dos modelos en el repo. Este documento explica en qué se diferencian y qué decidimos, para que nadie tenga que reconstruirlo desde el código.

| | Modelo local de Zambia | Modelo híbrido (África oriental) |
|---|---|---|
| Dónde está | `ml/*.py` (`features`, `labels`, `train`, `score`, `pipeline`); guía en `ml/README-reservoir.md` | `ml/scripts/`, `ml/modelo_hibrido/`; guía en `ml/README.md` |
| Qué predice | Probabilidad de que un **parche verde de la estación seca** sea un reservorio del gusano | Probabilidad de gusano en un **recorte de 640 m × 640 m** de una fecha concreta |
| Entradas | Verdor y humedad de Sentinel-2 de la estación seca, cobertura, clima de Open-Meteo, área del parche (12 variables tabulares) | Imagen Sentinel-2 (4 bandas, 64×64) más 8 variables de clima de los últimos 30 días; ResNet18 preentrenada (torchgeo) reducida con PCA, más gradient boosting |
| Zona de datos | Bbox `31.40,-14.50 → 31.80,-14.10` (Chimunsi / Petauke, Eastern Province, Zambia): 1.055 parches (tras el filtro de verde permanente) | Registros FAMEWS de Etiopía, Ruanda, Tanzania, Uganda, Burundi y Kenia |
| Etiquetas | El veredicto de Claude sobre las fotos de los extensionistas. Mientras no haya fotos reales, **simuladas** (`label_source=simulada`) | Monitoreo de campo y trampas de la FAO (FAMEWS, Niassy et al. 2021): 2.182 registros, 61,8 % positivos |
| Métrica publicada | AUC 0,84 → 0,89 al crecer de 41 a 966 parches verificados; **medida sobre etiquetas simuladas**, no vale como puntería real | AUC 0,73 ± 0,05 con validación cruzada espacial (clima solo 0,66; imagen sola 0,70) |
| Dependencias | scikit-learn, odc-stac, pystac-client (entorno `gusanito-ml`) | Además `torch` y `torchgeo` (varios GB) |
| Mejora con el tiempo | Sí: se reentrena con las fotos verificadas y solo se publica una versión si mejora en un conjunto reservado | Reentrenable con los scripts `h01`–`h05`, pero no está conectado al flujo de la app |

## Lo que sabemos de cómo generaliza el híbrido

Su propio README lo dice: **no se transfiere a países que no ha visto**. Al entrenarlo con cinco países y probarlo en el sexto, el AUC queda cerca del azar en 4 de 6 (`ml/modelo_hibrido/auc_dejando_un_pais_fuera.csv`: Burundi 0,48, Ruanda 0,56, Tanzania 0,41, Uganda 0,48; solo Kenia, 0,89, y Etiopía, 0,65, rinden). Además sus etiquetas vienen de maíz en campaña, no de reservorios de la estación seca.

## La decisión

- **El modelo de Zambia es el local.** Es el que dibuja la capa de reservorios de la demo y el que se reentrena con las fotos verificadas por los extensionistas.
- **El híbrido se queda en el repo como evidencia**, no como motor: probamos un modelo entrenado en varios países de África oriental y vimos que no basta para un país nuevo. Eso justifica que el sistema aprenda de datos locales.
- **No los hemos comparado cabeza a cabeza en Zambia** porque no existen etiquetas reales de Zambia: hoy hay 4 filas de demo en `ml/data/labels.csv`. Cualquier AUC calculado contra etiquetas simuladas sería circular. Comparar en serio exige fotos verificadas, y el híbrido necesita instalar `torch` y descargar imágenes y clima.

## Qué falta para poder decidir con datos

1. Reunir fotos verificadas de Zambia (el flujo de la app ya exporta `ml/data/labels.csv`).
2. Puntuar con ambos modelos los mismos puntos y compararlos con esas etiquetas reales.
3. Si el híbrido aporta algo, usar su probabilidad como una variable más del modelo local, no como reemplazo.

## Filtro de parches siempre verdes

Un parche que está verde todo el año no dice nada sobre el gusano, así que `ml/features.py::candidates` lo descarta antes de entrenar y de puntuar:
- cobertura dominante de **árboles, agua, construido o suelo desnudo** (WorldCover 2021, `host_cover == 0`);
- o un NDVI que **casi no baja** entre la estación seca temprana y la tardía (caída menor de 0,02).

Efecto: en la zona piloto de 1.257 parches quedan 1.055; en el bloque de 25 celdas de la landing, de 25.007 quedan 12.257. Límite conocido: un campo regado todo el año también tiene el NDVI plano y se descarta, aunque podría ser un refugio real; y WorldCover es de 2021.

## Aviso sobre la demo

La landing (`landing/`, servida en `/landing/` por `frontend/dev-server.js`, datos de `python -m ml.demo_timeline`) dibuja **solo predicciones reales** del modelo local: cada día lo reentrena con las parcelas verificadas hasta entonces y puntúa los 1.055 parches. Se muestran en tres tonos de verde según la probabilidad (70-80 %, 80-90 %, 90 % o más) y no hay zonas "confirmadas" en el mapa: las visitas solo se usan para reentrenar.

Lo **sintético** son los datos de prueba: si un parche visitado tiene gusano de verdad (la regla oculta de `ml/labels.py` más una tirada), los agricultores y el plan de visitas. Como esa regla usa las mismas variables que el modelo, que acierte es casi circular: sirve para ver el flujo y comprobar que los niveles ordenan bien el riesgo, no para afirmar puntería en campo.
