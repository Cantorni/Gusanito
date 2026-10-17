"""Runs the alert engine on demand: who gets a preventive SMS now, and the exact text.

Usage (from backend/):
    .venv/bin/python tools/run_alerts.py --dry-run                     # print only, record nothing
    .venv/bin/python tools/run_alerts.py [--reservoir chimunsi-0007]   # send (simulated) and record in the outbox

Storage: SQLITE_PATH (default data/gusanito.db), or DATA_STORAGE_CONNECTION_STRING / DATA_STORAGE_ACCOUNT.
The sender is simulated unless SMS_SENDER=twilio (and TWILIO_*) is set. Radii etc.: ALERT_* (gusanito/alerts.py).
"""

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
if not os.environ.get("DATA_STORAGE_CONNECTION_STRING") and not os.environ.get("DATA_STORAGE_ACCOUNT"):
    os.environ.setdefault("SQLITE_PATH", str(BACKEND / "data" / "gusanito.db"))

from gusanito import alerts, config  # noqa: E402
from gusanito.storage import open_repository  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="print who would be alerted, send and record nothing")
    parser.add_argument("--reservoir", help="only this reservoir id")
    args = parser.parse_args()

    settings = alerts.Settings.from_env()
    sender = alerts.sender_from_env()
    repo = open_repository(config.load())
    print(f"radius {settings.radius_km} km (immediate {settings.immediate_km}, near {settings.near_km}), "
          f"unverified >= {settings.min_probability}, dedup {settings.dedup_days} days, "
          f"sender {'none (dry run)' if args.dry_run else sender.name}")
    sent = alerts.run(repo, settings, sender, only=args.reservoir, dry_run=args.dry_run)
    for a in sent:
        print(f"{a.phone}  {a.risk_level:6} {a.distance_km:5.1f} km {a.direction:9} {a.reservoir_id} ({len(a.sms)} chars)")
        print(f"    {a.sms}")
    print(f"{len(sent)} SMS {'would be sent' if args.dry_run else 'sent'}")


if __name__ == "__main__":
    main()
