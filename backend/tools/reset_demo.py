"""Leaves an environment as it should be when a presentation starts: no alert SMS (the engine would not repeat
an alert within 14 days), no demo reports (no reservoir already confirmed), and the Zambia reservoirs layer
and synthetic farmers back in place (tools/seed_demo.py). Reports from the field (not demo) and farmers who
registered from the PWA are kept.

Usage (from backend/):
    .venv/bin/python tools/reset_demo.py                                     # local SQLite, data/gusanito.db
    DATA_STORAGE_CONNECTION_STRING=... .venv/bin/python tools/reset_demo.py  # Azure (terraform output storage_connection_string)
"""

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "tools"))
if not os.environ.get("DATA_STORAGE_CONNECTION_STRING") and not os.environ.get("DATA_STORAGE_ACCOUNT"):
    os.environ.setdefault("SQLITE_PATH", str(BACKEND / "data" / "gusanito.db"))

from seed_demo import demo_layer, farmers  # noqa: E402

from gusanito import config  # noqa: E402
from gusanito.storage import open_repository  # noqa: E402


def reset(repo, layer: dict, n_farmers: int = 20) -> tuple[int, int]:
    """Returns (SMS deleted, demo reports deleted)."""
    sms = repo.clear_outbox()
    demo = [r for r in repo.list_reports(limit=100_000) if r.get("demo")]
    for report in demo:
        repo.delete_report(report)
    repo.write_reservoirs_layer(layer)
    for farmer in farmers(n_farmers, datetime.now(UTC)):
        repo.save_subscriber(farmer)
    return sms, len(demo)


def main() -> None:
    layer = demo_layer()
    sms, demo = reset(open_repository(config.load()), layer)
    print(f"{sms} SMS and {demo} demo reports deleted; {len(layer['features'])} reservoirs and the synthetic farmers restored")


if __name__ == "__main__":
    main()
