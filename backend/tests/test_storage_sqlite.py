"""The SQLite repository (local demo) must behave like the Azure one: same tests as test_storage_azurite."""

import uuid

import pytest

from gusanito.config import Config
from gusanito.storage import REPORT_FIELDS, SqliteRepository, open_repository
from test_storage_azurite import new_report


@pytest.fixture
def repo(tmp_path):
    return open_repository(Config(None, None, "m", "medium", 0.7, None, sqlite_path=str(tmp_path / "db" / "g.db")))


def test_selected_by_config(repo):
    assert isinstance(repo, SqliteRepository)


def test_report_round_trip(repo):
    report = new_report(scene={"id": "s1", "date": "2026-09-05", "bbox": [31.5, -14.3, 31.6, -14.2]})
    repo.save_photo(report["id"], 0, b"\xff\xd8data", "image/jpeg")

    assert repo.create_report(report) == (report, True)
    assert repo.get_report(report["id"]) == report
    assert repo.list_reports(report["reservoirId"]) == [report]
    assert repo.read_photo(report["photos"][0]["file"]) == (b"\xff\xd8data", "image/jpeg")
    assert repo.create_report(report | {"label": "not_armyworm"}) == (report, False)

    analysed = report | {"estado": "descartado", "analisis": {"senales_gusano": []}, "agrees": False}
    repo.replace_report(analysed)
    assert repo.get_report(report["id"]) == analysed
    assert repo.get_report(str(uuid.uuid4())) is None
    assert repo.read_photo("missing/0.jpg") is None


def test_reports_newest_first_with_every_field(repo):
    old, new = new_report(receivedAt="2026-01-01"), new_report(receivedAt="2026-02-01", reservoirId=None)
    repo.create_report(old)
    repo.create_report(new)
    assert [r["id"] for r in repo.list_reports()] == [new["id"], old["id"]]
    assert set(repo.list_reports(limit=1)[0]) == {"id", *REPORT_FIELDS}


def test_layer_subscribers_outbox(repo):
    assert repo.read_reservoirs_layer() is None
    repo.write_reservoirs_layer({"type": "FeatureCollection", "features": []})
    assert repo.read_reservoirs_layer()["features"] == []

    s = {"phone": "+260971000001", "location": {"lat": -14.3, "lng": 31.6}, "language": "ny", "optOut": False}
    repo.save_subscriber(s)
    repo.save_subscriber(s | {"optOut": True})
    assert repo.get_subscriber("+260971000001")["optOut"] is True
    assert repo.list_subscribers() == [s | {"optOut": True}] and repo.get_subscriber("+1555") is None

    for day in ("2026-09-01", "2026-09-10"):
        repo.add_outbox({"id": day, "phone": s["phone"], "sentAt": day, "reservoirIds": ["r1"], "riskLevel": "low"})
    assert [m["id"] for m in repo.list_outbox()] == ["2026-09-01", "2026-09-10"]
    assert [m["id"] for m in repo.list_outbox(since="2026-09-05")] == ["2026-09-10"]


def test_reset_demo_keeps_field_data(repo, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", "unused")  # reset_demo sets a default on import; keep it out of other tests
    import reset_demo

    field, demo = new_report(), new_report(demo=True)
    for r in (field, demo):
        repo.save_photo(r["id"], 0, b"\xff\xd8data", "image/jpeg")
        repo.create_report(r)
    repo.save_subscriber({"phone": "+56912345678", "location": {"lat": -14.3, "lng": 31.6}, "optOut": False})
    repo.add_outbox({"id": "m1", "phone": "+56912345678", "sentAt": "2026-10-17T00:00:00+00:00"})

    assert reset_demo.reset(repo, n_farmers=3) == (1, 1)
    assert repo.list_outbox() == []
    assert [r["id"] for r in repo.list_reports()] == [field["id"]]
    assert repo.read_photo(demo["photos"][0]["file"]) is None and repo.read_photo(field["photos"][0]["file"])
    assert {s["phone"] for s in repo.list_subscribers()} == {"+56912345678", "+260971000000", "+260971000001", "+260971000002"}
    assert len(repo.read_reservoirs_layer()["features"]) == 5
