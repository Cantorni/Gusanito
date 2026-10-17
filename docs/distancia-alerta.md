# Distancia de alerta: campo de maíz y reservorio de gusano cogollero

**Decisión:** alerta preventiva a los campos a **10 km o menos** de un reservorio confirmado, en tres bandas:

| Banda | Distancia | Reservorio confirmado | Solo probable (modelo ≥ 0,8) | Plazo en el SMS |
|---|---|---|---|---|
| Inmediata | ≤ 2 km | `high` | `medium` | revisar en 3 días |
| Cercana | 2–5 km | `medium` | `low` | 7 días |
| Vigilancia | 5–10 km | `low` | sin SMS | 14 días |

Un parche revisado en terreno sin larvas (`hospedero_sin_senales`, `descartado`) no genera alertas, aunque el modelo le dé una probabilidad alta. Todo es configurable: `ALERT_IMMEDIATE_KM`, `ALERT_NEAR_KM`, `ALERT_RADIUS_KM`, `ALERT_MIN_PROBABILITY`, `ALERT_DEDUP_DAYS` (`backend/gusanito/alerts.py`).

## Qué se sabe

- **El vuelo de una noche alcanza decenas de km.** En molino de vuelo, la mediana fue de unos 64 km en cinco noches (≈ 2,7 km/h); el revisado de Nagoshi et al. cita medianas de 21–38 km y un máximo de 70 km por día, con un límite de unas 12 h de vuelo sostenido. Con viento, la migración puede superar los 100 km por noche e incluso 500 km antes de la puesta. ([J. Integrative Agriculture 2021, molino de vuelo](https://doaj.org/article/b2a1dd81cd1645a69d15c81cbbb026f2); [Nagoshi et al. 2022](https://pmc.ncbi.nlm.nih.gov/articles/PMC8816908); [SA Grain 2019](https://sagrainmag.co.za/2019/09/02/managing-the-local-risk-of-fall-armyworm-infestation-in-maize/))
- **Pero en África la migración es más limitada de lo esperado.** Nagoshi et al. (2022, 22 países) encuentran diferencias genéticas persistentes este-oeste: "la magnitud y extensión de la migración natural en el continente es más limitada de lo esperado"; la plaga persiste localmente. Es la premisa del proyecto: el riesgo empieza cerca del reservorio.
- **Matiz en contra:** polillas de Ghana en molino de vuelo (Gao et al. 2025) siguen siendo migratorias: 36 % del total y más del 60 % en la primera generación de laboratorio, como una población de campo china. Pueden hacer movimientos largos sin dirección fija cuando el hábitat empeora. La cercanía a un reservorio explica la primera ola local, no toda la infestación de la temporada. ([Insect Science 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12905470/))
- **El movimiento cotidiano en el maizal es de cientos de metros.** En marcaje y recaptura en maíz (Brasil), las distancias máximas de recaptura fueron 806 m (machos) y 608 m (hembras); Brasil usa 800 m como distancia máxima entre refugio y maíz Bt para esta especie. ([Vilarinho et al. 2011, Florida Entomologist 94:480](https://wiki.pestinfo.org/wiki/Florida_Entomologist_(2011)_94,_480-488))
- **Las larvas casi no se mueven entre plantas** (≤ 1,5 m en ensayos de campo en India): la plaga llega a un campo nuevo por las polillas, no por las larvas. ([Indian J. Entomology](https://indianentomology.org/index.php/ije/article/view/1516))
- **Práctica de alerta existente:** PRISE (CABI, con ZARI en Zambia) envía SMS por zona según modelos de desarrollo de la larva y clima, no por distancia a una fuente; FAMEWS (FAO) registra exploraciones y trampas sin un radio de alerta. Ninguno publica un radio. ([PRISE, CABI](https://preventionweb.net/news/cabi-led-study-creates-first-forecasting-models-targeting-fall-armyworm-larval-stages-africa); [FAMEWS](https://www.fao.org/fall-armyworm/monitoring-tools/famews-mobile-app/en))

## Qué es supuesto

- **No encontramos datos africanos de cómo cae la infestación con la distancia a una fuente** (un gradiente en km). Las bandas son un juicio, no una medición:
  - 2 km: los campos vecinos, unas pocas veces la distancia de recaptura de 0,8 km; es el ejemplo de la propuesta ("reservorio confirmado a 2 km de tu campo").
  - 5 km: lo que una polilla cubre en 1–2 h de vuelo a ≈ 2,7 km/h sin viento; una noche corta de dispersión local.
  - 10 km: un margen de vigilancia que cubre la dispersión local de varias noches sin llegar a la escala migratoria (decenas a cientos de km), donde la cercanía a un reservorio ya no dice mucho y el SMS sería ruido.
- Un radio mayor avisaría a casi todos los agricultores de una zona maicera densa; uno menor perdería la dispersión de más de una noche. La dirección del viento dominante no se usa todavía.
- **Cómo calibrarlo:** comparar los reportes de campo (`ml/data/labels.csv`, `tools/export_labels.py`) de campos a distintas distancias de reservorios confirmados en la primera generación de la temporada, y ajustar las bandas con el agrónomo.
