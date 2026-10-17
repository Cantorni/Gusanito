"""Replays the alert demo end to end on the local SQLite DB: satellite layer, synthetic farmers, field reports,
alert engine, outbox. Nothing is sent: every SMS goes to the outbox (table + data/outbox.jsonl).

Usage (from backend/):
    .venv/bin/python tools/simulate.py --reset            # every step, one after the other
    .venv/bin/python tools/simulate.py --reset --pause    # waits for Enter between steps (presenter mode)
    .venv/bin/python tools/simulate.py --reset --step 0   # then --step 1, --step 2 ... one per slide

Uses the same DB as tools/local_api.py, so the PWA (frontend/dev-server.js) shows the result.
"""

import argparse
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tools"))

from publish_sample_layer import DEMO_CENTER, LAYER  # noqa: E402
from seed_demo import farmers  # noqa: E402

from gusanito import alerts  # noqa: E402
from gusanito.geo import bbox_center, distance_km, geometry_bbox  # noqa: E402
from gusanito.storage import REPORT_FIELDS, SqliteRepository  # noqa: E402

DAY0 = datetime(2026, 9, 15, 7, 0, tzinfo=UTC)
ANALYSIS = {
    True: {"hay_plaga": True, "confianza": 0.92, "calidad_fotos": "buena", "tipo_vegetacion": "maiz",
           "es_hospedero": "si", "senales_gusano": ["hojas_ventana", "larva_visible"], "severidad": "media"},
    False: {"hay_plaga": False, "confianza": 0.88, "calidad_fotos": "buena", "tipo_vegetacion": "maiz",
            "es_hospedero": "si", "senales_gusano": [], "severidad": "ninguna"},
}

# (day, title, reservoir to report on or None, larvae found)
STEPS = [
    (0, "Dia 0: el satelite publica 5 reservorios probables. El motor avisa a quien vive muy cerca de los de alta probabilidad",
     None, None),
    (1, "Dia 1: un extensionista encuentra larvas en chimunsi-0007 (reservorio_confirmado)", "chimunsi-0007", True),
    (2, "Dia 2: otro parche, chimunsi-0012, se revisa y no tiene larvas: nadie recibe SMS", "chimunsi-0012", False),
    (3, "Dia 3: el motor corre de nuevo (tarea diaria): nadie recibe el mismo aviso dos veces", None, None),
    (5, "Dia 5: chimunsi-0044 tambien se confirma: avisos a los campos cercanos", "chimunsi-0044", True),
]


def seed(repo: SqliteRepository, n: int) -> None:
    repo.write_reservoirs_layer(LAYER)
    for farmer in farmers(n, DAY0):
        repo.save_subscriber(farmer)


def report(repo: SqliteRepository, reservoir_id: str, pest: bool, when: datetime) -> dict:
    feature = next(f for f in LAYER["features"] if f["id"] == reservoir_id)
    box = geometry_bbox(feature["geometry"])
    lat, lon = (round(v, 5) for v in bbox_center(box))
    r = dict.fromkeys(REPORT_FIELDS) | {
        "id": str(uuid.uuid4()), "reservoirId": reservoir_id, "label": "armyworm" if pest else "not_armyworm",
        "location": {"lat": lat, "lng": lon, "accuracy": 6}, "capturedAt": when.isoformat(),
        "photos": [], "receivedAt": when.isoformat(), "analyzedAt": when.isoformat(),
        "estado": "reservorio_confirmado" if pest else "hospedero_sin_senales",
        "analisis": ANALYSIS[pest], "agrees": True, "model": "simulado",
        "scene": {"id": feature["properties"]["escena"], "date": feature["properties"]["fecha"], "bbox": list(box)},
    }
    repo.create_report(r)
    return r


def run_step(repo: SqliteRepository, sender: alerts.SimulatedSender, index: int) -> None:
    day, title, reservoir_id, pest = STEPS[index]
    now = DAY0 + timedelta(days=day)
    print(f"\n=== Paso {index}. {title}")
    settings = alerts.Settings.from_env()
    if reservoir_id:
        r = report(repo, reservoir_id, pest, now)
        print(f"    reporte {r['id'][:8]} -> {r['estado']}")
        sent = alerts.run(repo, settings, sender, only=reservoir_id, now=now)
    else:
        sent = alerts.run(repo, settings, sender, now=now)
    print(f"    {len(sent)} SMS")
    for a in sent:
        print(f"    {a.phone}  [{a.risk_level:6}] {a.distance_km:5.1f} km {a.direction:9} ({a.language}, {len(a.sms)} car.)")
        print(f"        {a.sms}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(BACKEND / "data" / "gusanito.db"))
    parser.add_argument("--outbox", default=str(BACKEND / "data" / "outbox.jsonl"))
    parser.add_argument("--farmers", type=int, default=20)
    parser.add_argument("--reset", action="store_true", help="start from an empty DB and outbox")
    parser.add_argument("--step", type=int, choices=range(len(STEPS)), help="run only this step")
    parser.add_argument("--pause", action="store_true", help="wait for Enter between steps")
    args = parser.parse_args()

    if args.reset:
        for path in (args.db, args.outbox):
            Path(path).unlink(missing_ok=True)
    repo = SqliteRepository(args.db)
    if not repo.list_subscribers():
        seed(repo, args.farmers)
        print(f"Sembrados {args.farmers} agricultores y {len(LAYER['features'])} reservorios en {args.db}")
    # Always simulated, whatever SMS_SENDER says: a demo must never text real people.
    sender = alerts.SimulatedSender(args.outbox)

    steps = [args.step] if args.step is not None else range(len(STEPS))
    for i in steps:
        if args.pause and i != steps[0]:
            input("\n[Enter] siguiente paso...")
        run_step(repo, sender, i)

    subs = [s for s in repo.list_subscribers() if not s["optOut"]]
    near = sum(1 for s in subs if distance_km(s["location"]["lat"], s["location"]["lng"], *DEMO_CENTER) <= 10)
    print(f"\nOutbox: {len(repo.list_outbox())} SMS en total ({args.outbox}); {near} de {len(subs)} agricultores a <=10 km del centro")


if __name__ == "__main__":
    main()
