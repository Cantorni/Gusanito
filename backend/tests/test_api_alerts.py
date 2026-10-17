"""API behaviour on the local SQLite repository: subscribers, scene link, confirmed reservoirs, alerts after analysis,
and the local runner that serves function_app without Functions Core Tools."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import function_app
from conftest import report_body
from gusanito.config import Config
from gusanito.storage import SqliteRepository
from test_api import TOKEN, FakeAnalyzer, call, post_report, run_analysis

LAYER = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "id": "kasungu-0042", "geometry": {"type": "Point", "coordinates": [33.4833, -13.0333]},
     "properties": {"probabilidad": 0.6, "fecha": "2026-09-05", "escena": "S2-demo"}},
]}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    sqlite = SqliteRepository(str(tmp_path / "g.db"))
    sqlite.write_reservoirs_layer(LAYER)
    monkeypatch.setattr(function_app, "_repository", lambda: sqlite)
    monkeypatch.setattr(function_app, "_config", lambda: Config(None, None, "m", "medium", 0.7, TOKEN))
    monkeypatch.setattr(function_app, "_analyzer", lambda: FakeAnalyzer())
    monkeypatch.setenv("ALERT_OUTBOX_FILE", str(tmp_path / "outbox.jsonl"))
    monkeypatch.delenv("SMS_SENDER", raising=False)
    return sqlite


def subscribe(phone="+260971000001", lat=-13.04, lng=33.48, **extra):
    body = {"phone": phone, "location": {"lat": lat, "lng": lng}, "language": "ny", **extra}
    return call(function_app.subscribe, "POST", json.dumps(body).encode())


def test_subscribe_is_idempotent_per_phone(repo):
    status, body, _ = subscribe()
    assert status == 201 and body == {"phone": "+260971000001", "status": "registrado"}
    created = repo.get_subscriber("+260971000001")["createdAt"]
    assert subscribe(lat=-13.05)[0] == 200
    stored = repo.get_subscriber("+260971000001")
    assert stored["location"]["lat"] == -13.05 and stored["createdAt"] == created and stored["optOut"] is False
    assert len(repo.list_subscribers()) == 1


@pytest.mark.parametrize("phone", ["0971000001", "+0971000001", "+26097 1000001", "+12", "+2609710000011234567"])
def test_phone_must_be_e164(repo, phone):
    assert subscribe(phone)[0] == 400
    assert repo.list_subscribers() == []


def test_subscribe_needs_a_location_and_a_known_language(repo):
    assert call(function_app.subscribe, "POST", b'{"phone": "+260971000001"}')[0] == 400
    assert subscribe(language="fr")[0] == 400


def test_unsubscribe_and_back(repo):
    subscribe()
    status, body, _ = call(function_app.unsubscribe, "POST", b'{"phone": "+260971000001"}')
    assert status == 200 and repo.get_subscriber("+260971000001")["optOut"] is True
    # Unknown number: same answer, nothing stored.
    assert call(function_app.unsubscribe, "POST", b'{"phone": "+260971000009"}')[0] == 200
    assert repo.get_subscriber("+260971000009") is None
    subscribe()
    assert repo.get_subscriber("+260971000001")["optOut"] is False


def test_report_links_the_satellite_scene(repo):
    post_report()
    scene = repo.get_report(report_body()["id"])["scene"]
    assert scene == {"id": "S2-demo", "date": "2026-09-05", "bbox": [33.4833, -13.0333, 33.4833, -13.0333]}


def test_confirmation_alerts_subscribers_and_shows_on_the_layer(repo, tmp_path):
    subscribe()  # ~1 km from kasungu-0042
    subscribe("+260971000002", lat=-13.5)  # ~50 km away
    props = call(function_app.reservoirs)[1]["features"][0]["properties"]
    assert props["confirmado"] is False and props["verificacion"] is None

    post_report()
    run_analysis()  # FakeAnalyzer: pest seen -> reservorio_confirmado -> alert engine

    props = call(function_app.reservoirs)[1]["features"][0]["properties"]
    assert props["confirmado"] is True and props["probabilidad"] == 0.6
    (sms,) = repo.list_outbox()
    assert sms["phone"] == "+260971000001" and sms["riskLevel"] == "high" and sms["reservoirId"] == "kasungu-0042"
    assert len(sms["sms"]) <= 160 and "/?p=" in sms["sms"]
    assert (tmp_path / "outbox.jsonl").read_text().count("\n") == 1


def test_local_runner_serves_function_app(repo):
    import local_api

    server = ThreadingHTTPServer(("127.0.0.1", 0), local_api.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/api"
    try:
        with urllib.request.urlopen(f"{base}/health") as r:
            assert json.load(r) == {"status": "ok"}
        req = urllib.request.Request(f"{base}/subscribers/unsubscribe", data=b'{"phone": "+260971000001"}', method="POST")
        with urllib.request.urlopen(req) as r:
            assert json.load(r)["status"] == "dado_de_baja"
        with urllib.request.urlopen(f"{base}/reservoirs?bbox=33.4,-13.1,33.6,-12.9") as r:
            assert [f["id"] for f in json.load(r)["features"]] == ["kasungu-0042"]
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}/reports")
        assert e.value.code == 401
    finally:
        server.shutdown()


def test_outbox_lists_alert_sms_newest_first_with_token(repo):
    subscribe()
    day1, day2 = "2026-09-15T07:00:00+00:00", "2026-09-16T07:00:00+00:00"
    for id_, risk, km, sent in [("old", "high", 1.0, day1), ("far", "low", 8.0, day2), ("near", "medium", 3.0, day2),
                                ("nearest", "medium", 2.5, day2)]:
        repo.add_outbox({"id": id_, "phone": f"+26097100000{len(id_)}", "riskLevel": risk, "distanceKm": km,
                         "sms": id_, "sentAt": sent})
    assert call(function_app.list_outbox)[0] == 401
    status, body, _ = call(function_app.list_outbox, headers={"x-report-token": TOKEN})
    assert status == 200 and [m["id"] for m in body] == ["nearest", "near", "far", "old"]


def test_demo_reports_are_not_training_labels(monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", "unused")  # the tool sets a default on import; keep it out of other tests
    import export_labels

    analysed = {"estado": "reservorio_confirmado", "analisis": {"hay_plaga": True}}
    reports = [{"id": "field", **analysed}, {"id": "demo", "demo": True, **analysed}]
    assert [r["id"] for r in export_labels.verified(reports)] == ["field"]
