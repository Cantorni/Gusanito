# Diagrama de despliegue

Sale de `infra/main.tf` y `infra/ml.tf`. Para verlo: pega el bloque en [mermaid.live](https://mermaid.live) o ábrelo en un visor de Markdown con Mermaid (GitHub lo dibuja). Mermaid no tiene nodos 3D de UML, así que cada nodo lleva su estereotipo («device», «execution environment», «artifact») en el texto y las zonas van en color: azul para el grupo de recursos de Azure, verde para el entrenamiento, rojo para servicios externos.

```mermaid
flowchart LR
  subgraph CLI["«device»  Teléfono del extensionista"]
    direction TB
    PWA["«execution environment»<br/>Navegador (Chrome / Safari)<br/><br/>«artifact» PWA Gusanito<br/>Service Worker + IndexedDB<br/>mapa offline zambia.pmtiles"]
  end

  subgraph OPS["«device»  Portátil del operador"]
    OP["«execution environment»<br/>Terraform + Python<br/><br/>«artifact» infra/ · deploy.sh<br/>seed_demo · publish_layer<br/>run_alerts"]
  end

  subgraph FARM["«device»  Teléfono del agricultor"]
    SMSIN["«artifact»<br/>SMS con enlace offline"]
  end

  subgraph AZ["«cloud»  Azure · grupo de recursos (infra/main.tf)"]
    direction LR

    SWA["«node» Static Web App · Free<br/><br/>«artifact» PWA y receiver.html"]

    subgraph FN["«node» Function App · Flex Consumption (FC1) · Linux"]
      direction TB
      PY["«execution environment»<br/>Python 3.13 · 2 GB<br/>escala a cero<br/><br/>«artifact» API HTTP<br/>«artifact» analyze_report (cola)<br/>«artifact» motor de alertas"]
    end

    subgraph ST["«node» Storage Account · Standard LRS"]
      direction TB
      BLOB[("Blob<br/>deployment · layers<br/>current.geojson")]
      TBL[("Tables<br/>reports · subscribers · outbox")]
      QUE[["Queue<br/>analysis-requests"]]
    end

    KV["«node» Key Vault<br/>secreto ANTHROPIC_API_KEY"]
    ID["«node» Identidad administrada<br/>Blob Data Owner · Queue y Table Data Contributor"]
    OBS["«node» Application Insights<br/>+ Log Analytics (30 días)"]

    subgraph ML["«node» Azure Machine Learning · opcional (enable_ml = true)"]
      direction TB
      WS["Workspace"]
      CL2["«execution environment»<br/>Cluster CPU Standard_DS3_v2<br/>0 a 2 nodos<br/><br/>«artifact» ml.pipeline<br/>entrena y publica la capa"]
    end
  end

  subgraph EXT["«external»  Servicios externos"]
    direction TB
    CLAUDE["Anthropic API<br/>Claude analiza la foto"]
    SMS["Proveedor de SMS<br/>Twilio (o emisor simulado)"]
    PC["Planetary Computer<br/>Sentinel-2"]
    OM["Open-Meteo<br/>clima"]
  end

  OP -->|"az login: carga datos y capa"| ST
  OP -.->|"terraform apply · deploy.sh"| AZ
  PWA -->|"HTTPS"| SWA
  PWA -->|"HTTPS: POST /reports, GET /reservoirs"| PY
  PY --> TBL
  PY --> QUE
  QUE -->|"dispara analyze_report"| PY
  PY --> BLOB
  PY -->|"foto"| CLAUDE
  PY -->|"referencia al secreto"| KV
  PY -.->|"autenticación"| ID
  ID -.-> ST
  PY -->|"alerta"| SMS
  SMS -.->|"SMS"| SMSIN
  PY -.-> OBS
  WS --- CL2
  CL2 -->|"lee etiquetas, publica current.geojson"| BLOB
  CL2 --> PC
  CL2 --> OM

  classDef zone fill:#cfe2ff,stroke:#7a9cc6,color:#111
  classDef train fill:#d9f2dc,stroke:#6aa874,color:#111
  classDef ext fill:#ffd6d6,stroke:#d08a8a,color:#111
  classDef box fill:#ffffff,stroke:#555,color:#111
  class AZ zone
  class ML train
  style ML stroke-dasharray: 6 4
  class EXT ext
  class OP,PWA,SMSIN,SWA,PY,BLOB,TBL,QUE,KV,ID,OBS,WS,CL2,CLAUDE,SMS,PC,OM box
```

## Qué sale de cada archivo de Terraform

| Elemento | Recurso Terraform |
|---|---|
| Static Web App (Free) | `azurerm_static_web_app.pwa` |
| Function App (FC1, Python 3.13, 2 GB) | `azurerm_service_plan.functions`, `azurerm_function_app_flex_consumption.api` |
| Storage Account (LRS) y su contenedor `deployment` | `azurerm_storage_account.main`, `azurerm_storage_container.deployment` |
| Identidad administrada y roles de almacenamiento | `azurerm_user_assigned_identity.functions`, `azurerm_role_assignment.storage` |
| Key Vault con la clave de Anthropic | `azurerm_key_vault.main` |
| Application Insights y Log Analytics | `azurerm_application_insights.main`, `azurerm_log_analytics_workspace.main` |
| Azure ML y cluster que escala a cero, **solo con `enable_ml = true`** (por defecto apagado) | `azurerm_machine_learning_workspace.main`, `azurerm_machine_learning_compute_cluster.cpu`, rol *Storage Blob Data Contributor* para ambos (`infra/ml.tf`) |
| Portátil del operador con acceso a datos (`seed_demo.py`, `publish_layer.py`, `run_alerts.py`) | `azurerm_role_assignment.storage_deployer`: *Blob* y *Table Data Contributor* para quien ejecuta Terraform |

## Lo que no sale de Terraform

Lo puse en el diagrama porque el despliegue real depende de ello, pero Terraform no lo crea:
- Las **tablas** `reports`, `subscribers` y `outbox`, la **cola** `analysis-requests` y el contenedor `layers` los crea el código al arrancar.
- El **proveedor de SMS** es simulado por defecto (`SMS_SENDER` sin definir). Twilio es opcional y no está en `infra/`.
- El **secreto** `ANTHROPIC_API_KEY` en Key Vault se carga a mano (lo dice el README).
- Los datos de **Planetary Computer** y **Open-Meteo** los usa el job de entrenamiento, no la API.
