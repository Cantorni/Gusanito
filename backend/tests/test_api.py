import copy
import json
from types import SimpleNamespace

import anthropic
import azure.functions as func
import httpx2
import pytest

import function_app
from conftest import PHOTO_BYTES, REPORT_ID, report_body
from gusanito.analysis import AnalysisResult, ModelResponseError
from gusanito.config import Config, ConfigError
from gusanito.models import AlertMessage
from gusanito.storage import REPORT_FIELDS
from test_analysis import ANALYSIS

TOKEN = "s3cret"


class FakeRepository:
    def __init__(self):
        self.reports: dict[str, dict] = {}
        self.photos: dict[str, bytes] = {}
        self.layer: dict | None = None

    def save_photo(self, report_id, index, data, media_type):
        name = f"{report_id}/{index}.jpg"
        self.photos[name] = data
        return name

    def read_photo(self, name):
        return (self.photos[name], "image/jpeg") if name in self.photos else None

    def read_reservoirs_layer(self):
        return self.layer

    def get_report(self, report_id):
        return copy.deepcopy(self.reports.get(report_id))

    def list_reports(self, reservoir_id=None, limit=200):
        return [r for r in self.reports.values() if reservoir_id in (None, r["reservoirId"])]

    def create_report(self, report):
        created = report["id"] not in self.reports
        self.reports.setdefault(report["id"], copy.deepcopy(report))
        return copy.deepcopy(self.reports[report["id"]]), created

    def replace_report(self, report):
        assert set(report) == {"id", *REPORT_FIELDS}
        self.reports[report["id"]] = copy.deepcopy(report)


class FakeAnalyzer:
    def __init__(self, result=None, error=None):
        self.result = result or AnalysisResult(ANALYSIS, False, "claude-opus-5-5", "req_123")
        self.error = error
        self.calls = []

    def analyze_report(self, photos, notes):
        self.calls.append((photos, notes))
        if self.error:
            raise self.error
        return self.result

    def draft_alert(self, alert):
        return AlertMessage(sms="Check your maize this week.", voice="Hello. Check your maize this week.")


class FakeQueue:
    def __init__(self):
        self.messages = []

    def set(self, value):
        self.messages.append(json.loads(value))


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepository()
    monkeypatch.setattr(function_app, "_repository", lambda: fake)
    monkeypatch.setattr(function_app, "_config", lambda: Config(None, "account", "claude-opus-5-5", "medium", 0.7, TOKEN))
    return fake


@pytest.fixture
def analyzer(monkeypatch):
    fake = FakeAnalyzer()
    monkeypatch.setattr(function_app, "_analyzer", lambda: fake)
    return fake


def call(fn, method="GET", body=b"", route_params=None, params=None, headers=None, *extra):
    req = func.HttpRequest(
        method=method, url="/api/x", body=body, route_params=route_params or {}, params=params or {}, headers=headers or {}
    )
    res = fn.build().get_user_function()(req, *extra)
    content_type = res.mimetype
    payload = json.loads(res.get_body()) if content_type == "application/json" else res.get_body()
    return res.status_code, payload, res.headers


def post_report(body=None):
    queue = FakeQueue()
    status, payload, _ = call(
        function_app.create_report, "POST", json.dumps(body or report_body()).encode(), None, None, None, queue
    )
    return status, payload, queue


def run_analysis(report_id=REPORT_ID, dequeue_count=1):
    msg = SimpleNamespace(get_body=lambda: json.dumps({"id": report_id}).encode(), dequeue_count=dequeue_count)
    function_app.analyze_report.build().get_user_function()(msg)


AUTH = {"x-report-token": TOKEN}


def test_health():
    assert call(function_app.health)[0] == 200


def test_post_stores_report_and_queues_analysis(repo, analyzer):
    status, ack, queue = post_report()

    assert status == 202
    assert ack == {"id": REPORT_ID, "receivedAt": ack["receivedAt"], "status": "recibido"}
    assert queue.messages == [{"id": REPORT_ID}]
    stored = repo.reports[REPORT_ID]
    assert stored["estado"] == "pendiente" and stored["analisis"] is None
    assert stored["photos"] == [{"file": f"{REPORT_ID}/0.jpg", "mediaType": "image/jpeg"}]
    assert stored["location"] == {"lat": -13.0333, "lng": 33.4833, "accuracy": 8}
    assert stored["meta"]["originalSize"] == 2_400_000
    assert repo.photos[f"{REPORT_ID}/0.jpg"] == PHOTO_BYTES
    assert analyzer.calls == []  # analysed later, from the queue


def test_resend_is_acknowledged_once(repo, analyzer):
    first = post_report()
    second = post_report()
    assert (first[0], second[0]) == (202, 200)
    assert second[1] == first[1]
    assert second[2].messages == []


def test_invalid_body(repo, analyzer):
    status, payload, queue = post_report(report_body(location={"lat": 200, "lng": 0}))
    assert status == 400
    assert payload["detail"][0]["loc"] == ["location", "lat"]
    assert queue.messages == [] and repo.reports == {}


def test_queue_analysis_updates_report(repo, analyzer):
    post_report()
    run_analysis()

    report = repo.reports[REPORT_ID]
    assert report["estado"] == "reservorio_confirmado"
    assert report["analisis"]["hay_plaga"] is True and report["analisis"]["confianza"] == 0.8
    assert report["agrees"] is True  # officer said armyworm, Claude saw signs
    assert report["model"] == "claude-opus-5-5" and report["requestId"] == "req_123"
    photos, notes = analyzer.calls[0]
    assert photos[0].data == PHOTO_BYTES and notes == "Huerto con riego junto al río"


def test_duplicate_queue_message_is_ignored(repo, analyzer):
    post_report()
    run_analysis()
    run_analysis()
    assert len(analyzer.calls) == 1


def test_refusal_goes_to_human_review(repo, monkeypatch):
    monkeypatch.setattr(function_app, "_analyzer", lambda: FakeAnalyzer(AnalysisResult(None, True, "claude-opus-5-5", "r")))
    post_report()
    run_analysis()
    report = repo.reports[REPORT_ID]
    assert report["estado"] == "revision_humana" and report["refused"] is True and report["agrees"] is None


def _http_error(cls, status):
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com"))
    return cls("error", response=response, body=None)


@pytest.mark.parametrize(
    "error",
    [
        _http_error(anthropic.RateLimitError, 429),
        _http_error(anthropic.InternalServerError, 529),
        anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")),
        ModelResponseError("max_tokens"),
        ConfigError("no key yet"),
    ],
)
def test_transient_errors_retry_then_give_up(repo, monkeypatch, error):
    monkeypatch.setattr(function_app, "_analyzer", lambda: FakeAnalyzer(error=error))
    post_report()

    with pytest.raises(type(error)):
        run_analysis(dequeue_count=1)
    assert repo.reports[REPORT_ID]["estado"] == "pendiente"

    run_analysis(dequeue_count=function_app.MAX_DEQUEUE_COUNT)
    report = repo.reports[REPORT_ID]
    assert report["estado"] == "error_analisis" and report["analysisError"] == type(error).__name__


def test_permanent_error_fails_without_retry(repo, monkeypatch):
    monkeypatch.setattr(function_app, "_analyzer", lambda: FakeAnalyzer(error=_http_error(anthropic.AuthenticationError, 401)))
    post_report()
    run_analysis(dequeue_count=1)
    assert repo.reports[REPORT_ID]["estado"] == "error_analisis"


def test_reading_reports_needs_the_token(repo, analyzer):
    post_report()
    assert call(function_app.list_reports)[0] == 401
    assert call(function_app.list_reports, headers={"x-report-token": "wrong"})[0] == 401

    status, reports, _ = call(function_app.list_reports, headers=AUTH)
    assert status == 200 and [r["id"] for r in reports] == [REPORT_ID]
    assert call(function_app.list_reports, params={"reservoirId": "kasungu-0042"}, headers=AUTH)[0] == 200
    assert call(function_app.list_reports, params={"reservoirId": "a'b"}, headers=AUTH)[0] == 400
    # The token is never read from the query string, where it would land in logs.
    assert call(function_app.list_reports, params={"token": TOKEN})[0] == 401


def test_token_not_configured(repo, monkeypatch):
    monkeypatch.setattr(function_app, "_config", lambda: Config(None, "account", "m", "medium", 0.7, None))
    assert call(function_app.list_reports, headers=AUTH)[0] == 503


def test_get_report_and_photo(repo, analyzer):
    post_report()
    assert call(function_app.get_report, route_params={"id": REPORT_ID}, headers=AUTH)[0] == 200
    assert call(function_app.get_report, route_params={"id": "nope"}, headers=AUTH)[0] == 400
    assert call(function_app.get_report, route_params={"id": "11111111-1111-1111-1111-111111111111"}, headers=AUTH)[0] == 404

    req = func.HttpRequest(method="GET", url="/api/x", body=b"", route_params={"id": REPORT_ID, "index": "0"}, headers=AUTH)
    res = function_app.get_photo.build().get_user_function()(req)
    assert res.status_code == 200 and res.get_body() == PHOTO_BYTES and res.mimetype == "image/jpeg"
    assert call(function_app.get_photo, route_params={"id": REPORT_ID, "index": "3"}, headers=AUTH)[0] == 404
    assert call(function_app.get_photo, route_params={"id": REPORT_ID, "index": "0"})[0] == 401


def test_reservoirs(repo):
    assert call(function_app.reservoirs)[0] == 404
    repo.layer = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "r1", "geometry": {"type": "Point", "coordinates": [33.48, -13.03]}, "properties": {}},
            {"type": "Feature", "id": "r2", "geometry": {"type": "Point", "coordinates": [35.0, -15.0]}, "properties": {}},
        ],
    }
    assert len(call(function_app.reservoirs)[1]["features"]) == 2
    status, layer, _ = call(function_app.reservoirs, params={"bbox": "33.4,-13.1,33.6,-12.9"})
    assert status == 200 and [f["id"] for f in layer["features"]] == ["r1"]
    assert call(function_app.reservoirs, params={"bbox": "bad"})[0] == 400


def test_alert_message(repo, analyzer):
    body = json.dumps({"riskLevel": "high", "distanceKm": 2, "daysToAct": 7, "language": "es"}).encode()
    assert call(function_app.alert_message, "POST", body)[0] == 401
    status, message, _ = call(function_app.alert_message, "POST", body, headers=AUTH)
    assert status == 200 and message["sms"]
    assert call(function_app.alert_message, "POST", b'{"riskLevel": "extreme"}', headers=AUTH)[0] == 400


def test_alert_without_anthropic_key(repo, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    function_app._analyzer.cache_clear()
    body = json.dumps({"riskLevel": "high", "distanceKm": 2, "daysToAct": 7}).encode()
    status, payload, _ = call(function_app.alert_message, "POST", body, headers=AUTH)
    assert status == 500 and payload == {"error": "service not configured"}
