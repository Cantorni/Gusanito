import json
from datetime import UTC, datetime, timedelta

import pytest

from gusanito import alerts
from gusanito.alerts import Reservoir, Settings, build_sms, is_gsm7, plan, risk_level
from gusanito.geo import bearing_deg, compass, distance_km
from gusanito.models import MAX_SMS_CHARS
from gusanito.storage import REPORT_FIELDS, SqliteRepository

S = Settings()
LAT, LON = -14.30, 31.60
KM_LAT = 1 / 111.2  # degrees of latitude per km


def test_distance_and_bearing():
    assert distance_km(LAT, LON, LAT + 10 * KM_LAT, LON) == pytest.approx(10, rel=0.01)
    # 1 degree of longitude at 14.3 S is cos(14.3) * 111.2 km
    assert distance_km(LAT, LON, LAT, LON + 1) == pytest.approx(107.7, rel=0.01)
    assert compass(bearing_deg(LAT, LON, LAT + 0.1, LON)) == "north"
    assert compass(bearing_deg(LAT, LON, LAT, LON + 0.1)) == "east"
    assert compass(bearing_deg(LAT, LON, LAT - 0.1, LON - 0.1)) == "southwest"


def reservoir(confirmed=True, probability=0.5, verification=None, km_north=1.0, rid="r1"):
    if confirmed:
        verification = "reservorio_confirmado"
    return Reservoir(rid, LAT + km_north * KM_LAT, LON, probability, confirmed, verification)


@pytest.mark.parametrize(
    ("res", "km", "expected"),
    [
        (reservoir(), 1.5, "high"),
        (reservoir(), 4, "medium"),
        (reservoir(), 9, "low"),
        (reservoir(), 11, None),
        # Unverified but likely: one level lower, and nothing past the near band.
        (reservoir(False, 0.9), 1.5, "medium"),
        (reservoir(False, 0.9), 4, "low"),
        (reservoir(False, 0.9), 9, None),
        (reservoir(False, 0.5), 1, None),
        # Checked on the ground without larvae: never, whatever the model said.
        (reservoir(False, 0.99, "descartado"), 1, None),
        (reservoir(False, 0.99, "hospedero_sin_senales"), 1, None),
    ],
)
def test_risk_level(res, km, expected):
    assert risk_level(res, km, S) == expected


def sub(phone="+260971000001", km_south=0.0, language="es", opt_out=False):
    return {"phone": phone, "location": {"lat": LAT - km_south * KM_LAT, "lng": LON}, "language": language,
            "optOut": opt_out}


def test_plan_picks_who_is_in_range():
    found = [reservoir(km_north=0)]
    subs = [sub("+260971000001", 1), sub("+260971000002", 6), sub("+260971000003", 12),
            sub("+260971000004", 1, opt_out=True)]
    out = plan(found, subs, [], S)
    assert [(a.phone, a.risk_level, a.direction) for a in out] == [
        ("+260971000001", "high", "north"), ("+260971000002", "low", "north")]
    assert out[0].distance_km == 1.0


def test_dedup_and_escalation():
    found = [reservoir(km_north=0)]
    first = plan(found, [sub(km_south=1)], [], S)
    sent = [{"phone": a.phone, "reservoirId": a.reservoir_id, "reservoirIds": a.reservoir_ids,
             "riskLevel": a.risk_level} for a in first]
    assert plan(found, [sub(km_south=1)], sent, S) == []
    # Was a "probable" alert at medium: a confirmation (high) gets through.
    probable = [{**sent[0], "riskLevel": "medium"}]
    assert [a.risk_level for a in plan(found, [sub(km_south=1)], probable, S)] == ["high"]
    # A failed SMS does not count as sent.
    assert plan(found, [sub(km_south=1)], [{**sent[0], "status": "fallido"}], S)


def test_most_severe_reservoir_leads_and_all_go_in_the_link():
    found = [reservoir(km_north=4, rid="far"), reservoir(km_north=1, rid="near")]
    (a,) = plan(found, [sub()], [], S)
    assert a.reservoir_id == "near" and a.reservoir_ids == ["near", "far"]
    assert a.sms.count("_") == 5  # two points, three numbers each


def test_only_one_reservoir():
    found = [reservoir(rid="a"), reservoir(rid="b", km_north=2)]
    assert [a.reservoir_ids for a in plan(found, [sub()], [], S, only="b")] == [["b"]]


@pytest.mark.parametrize("language", ["es", "en", "ny"])
@pytest.mark.parametrize("direction", alerts.DIRECTIONS["en"])
@pytest.mark.parametrize("base_url", ["http://127.0.0.1:8787", "https://gentle-river-0a1b2c3d4.5.azurestaticapps.net"])
def test_sms_fits_one_gsm7_message(language, direction, base_url):
    points = [(-14.3123 + i * 0.0137, 31.6011 - i * 0.0101, 0.83) for i in range(6)]
    for confirmed in (True, False):
        sms, n = build_sms(language, confirmed, 9.96, direction, 14, base_url, points)
        assert len(sms) <= MAX_SMS_CHARS and is_gsm7(sms), sms
        # The link is always whole, with at least one point.
        assert n >= 1 and sms.split("/?p=")[1].count("_") == 3 * n - 1


def test_text_is_shortened_before_the_link_is_cut():
    long_url = "https://" + "x" * 80 + ".example"
    sms, _ = build_sms("es", True, 3.0, "northwest", 7, long_url, [(-14.3, 31.6, 0.9)])
    assert len(sms) <= MAX_SMS_CHARS and sms.endswith(f"{long_url}/?p=-143000_316000_90")


def test_gsm7_check():
    assert is_gsm7("Revisa tu maiz") and not is_gsm7("Revisa tu maíz")


def layer():
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": "r1", "geometry": {"type": "Point", "coordinates": [LON, LAT]},
         "properties": {"probabilidad": 0.6, "fecha": "2026-09-05"}},
    ]}


def report(estado, received="2026-09-16T07:00:00+00:00", **changes):
    return dict.fromkeys(REPORT_FIELDS) | {"id": received, "reservoirId": "r1", "receivedAt": received,
                                            "analyzedAt": received, "estado": estado} | changes


def test_latest_verification_wins_and_annotates_the_layer():
    reports = [report("reservorio_confirmado", "2026-09-16T07:00:00+00:00"),
               report("revision_humana", "2026-09-18T07:00:00+00:00")]
    props = alerts.annotate_layer(layer(), reports)["features"][0]["properties"]
    assert props["confirmado"] is True and props["verificacion"] == "reservorio_confirmado"
    reports.append(report("descartado", "2026-09-17T07:00:00+00:00"))
    props = alerts.annotate_layer(layer(), reports)["features"][0]["properties"]
    assert props["confirmado"] is False and props["verificacion"] == "descartado"


@pytest.fixture
def repo(tmp_path):
    r = SqliteRepository(str(tmp_path / "t.db"))
    r.write_reservoirs_layer(layer())
    r.save_subscriber(sub(km_south=1) | {"createdAt": "x", "updatedAt": "x"})
    return r


def test_run_writes_outbox_and_dedups_within_the_window(repo, tmp_path):
    sender = alerts.SimulatedSender(str(tmp_path / "outbox.jsonl"))
    now = datetime(2026, 9, 16, tzinfo=UTC)
    assert alerts.run(repo, S, sender, now=now) == []  # 0.6 and unverified: nothing yet
    repo.create_report(report("reservorio_confirmado"))

    assert len(alerts.run(repo, S, sender, dry_run=True, now=now)) == 1
    assert repo.list_outbox() == []  # dry run records nothing

    (a,) = alerts.run(repo, S, sender, now=now)
    assert alerts.run(repo, S, sender, now=now + timedelta(days=13)) == []
    assert len(alerts.run(repo, S, sender, now=now + timedelta(days=15))) == 1
    stored = repo.list_outbox()
    assert len(stored) == 2 and stored[0]["sms"] == a.sms and stored[0]["status"] == "simulado"
    lines = (tmp_path / "outbox.jsonl").read_text().splitlines()
    assert json.loads(lines[0])["phone"] == "+260971000001"


def test_sender_is_simulated_unless_twilio_is_asked_for(monkeypatch):
    monkeypatch.delenv("SMS_SENDER", raising=False)
    assert isinstance(alerts.sender_from_env(), alerts.SimulatedSender)
    monkeypatch.setenv("SMS_SENDER", "twilio")
    with pytest.raises(KeyError):  # no TWILIO_* settings: refuses rather than pretending
        alerts.sender_from_env()


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("ALERT_RADIUS_KM", "8")
    monkeypatch.setenv("PWA_URL", "https://x.app/")
    s = Settings.from_env()
    assert s.radius_km == 8 and s.pwa_url == "https://x.app" and s.near_km == 5
