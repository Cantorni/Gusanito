"""Gusanito API.

The PWA posts a photo report and gets an acknowledgement right away; a queue-triggered function
asks Claude to analyse it and stores the result for the agronomist (receiver page). The PWA never
sees Claude's answer.
"""

import functools
import hmac
import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import anthropic
import azure.functions as func
from pydantic import ValidationError

from gusanito import alerts, config
from gusanito.analysis import Analyzer, ModelResponseError, Photo
from gusanito.geo import filter_by_bbox, geometry_bbox, parse_bbox
from gusanito.models import (
    RESERVOIR_ID_PATTERN,
    AlertIn,
    ReportIn,
    SubscriberIn,
    UnsubscribeIn,
    agrees_with_officer,
    decide_estado,
)
from gusanito.storage import REPORT_FIELDS, Repository, SqliteRepository, open_repository

# Routes are anonymous: anyone with the PWA posts reports, and the receiver page reads them with just its link
# (a demo choice). Only drafting alerts with Claude needs REPORT_TOKEN, since every call spends credits.
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# The Azure SDKs log every HTTP request at INFO: noise and cost in Application Insights.
logging.getLogger("azure").setLevel(logging.WARNING)

ANALYSIS_QUEUE = "analysis-requests"
MAX_DEQUEUE_COUNT = 5  # keep in sync with host.json extensions.queues.maxDequeueCount
_RESERVOIR_ID = re.compile(RESERVOIR_ID_PATTERN)
OUTBOX_LIMIT = 500  # the receiver page shows the latest alert SMS only


@functools.cache
def _config() -> config.Config:
    return config.load()


@functools.cache
def _repository() -> Repository | SqliteRepository:
    return open_repository(_config())


@functools.cache
def _analyzer() -> Analyzer:
    config.require_anthropic_key()
    c = _config()
    return Analyzer(anthropic.Anthropic(timeout=180.0, max_retries=2), c.claude_model, c.claude_effort)


def _json(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(body, ensure_ascii=False), status_code=status, mimetype="application/json", headers=headers
    )


def _error(status: int, message: str, detail: Any = None) -> func.HttpResponse:
    body: dict[str, Any] = {"error": message}
    if detail is not None:
        body["detail"] = detail
    return _json(body, status)


def _invalid(e: ValidationError) -> func.HttpResponse:
    return _error(400, "invalid body", json.loads(e.json(include_url=False, include_input=False)))


def _check_token(req: func.HttpRequest) -> func.HttpResponse | None:
    """Returns an error response unless the request carries REPORT_TOKEN."""
    expected = _config().report_token
    if not expected:
        return _error(503, "REPORT_TOKEN not configured")
    # Header only: a token in the query string would end up in request logs and telemetry.
    given = req.headers.get("x-report-token") or ""
    if not hmac.compare_digest(given.encode(), expected.encode()):
        return _error(401, "bad token")
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat()


@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    return _json({"status": "ok"})


@app.route(route="reservoirs", methods=["GET"])
def reservoirs(req: func.HttpRequest) -> func.HttpResponse:
    repo = _repository()
    layer = repo.read_reservoirs_layer()
    if layer is None:
        return _error(404, "no reservoirs layer published yet")
    bbox_text = req.params.get("bbox")
    if bbox_text:
        try:
            layer = filter_by_bbox(layer, parse_bbox(bbox_text))
        except ValueError as e:
            return _error(400, str(e))
    # shortcut: reads every report per request; keep a per-reservoir state table once reports grow
    return _json(alerts.annotate_layer(layer, repo.list_reports(limit=100_000)))


@app.route(route="reports", methods=["POST"])
@app.queue_output(arg_name="queue", queue_name=ANALYSIS_QUEUE, connection="AzureWebJobsStorage")
def create_report(req: func.HttpRequest, queue: func.Out[str]) -> func.HttpResponse:
    try:
        report_in = ReportIn.model_validate_json(req.get_body())
    except ValidationError as e:
        return _invalid(e)

    repo = _repository()
    report_id = str(report_in.id)
    # A resend of the same report (retry after a dropped connection) is acknowledged, not duplicated.
    existing = repo.get_report(report_id)
    if existing is not None:
        return _json(_ack(existing), 200)

    photos = [repo.save_photo(report_id, i, p.raw(), p.media_type) for i, p in enumerate(report_in.photos)]
    report = dict.fromkeys(REPORT_FIELDS) | {
        "id": report_id,
        "demo": report_in.demo,
        "reservoirId": report_in.reservoir_id,
        "label": report_in.label,
        "location": report_in.location.model_dump() if report_in.location else None,
        "capturedAt": report_in.captured_at.isoformat() if report_in.captured_at else None,
        "notes": report_in.notes,
        "meta": report_in.meta.model_dump(by_alias=True) if report_in.meta else None,
        "photos": [{"file": name, "mediaType": p.media_type} for name, p in zip(photos, report_in.photos)],
        "receivedAt": _now(),
        "estado": "pendiente",
        "scene": _scene(repo, report_in.reservoir_id),
    }
    stored, created = repo.create_report(report)
    if created:
        queue.set(json.dumps({"id": report_id}))
    return _json(_ack(stored), 202 if created else 200)


def _scene(repo, reservoir_id: str | None) -> dict[str, Any] | None:
    """The satellite scene the reservoir was mapped from, so each field record points at the image it checked."""
    layer = repo.read_reservoirs_layer() if reservoir_id else None
    for f in (layer or {}).get("features", []):
        if str(f.get("id")) == reservoir_id:
            props = f.get("properties") or {}
            box = f.get("geometry") and geometry_bbox(f["geometry"])
            return {"id": props.get("escena"), "date": props.get("fecha"), "bbox": list(box) if box else None}
    return None


def _ack(report: dict[str, Any]) -> dict[str, Any]:
    return {"id": report["id"], "receivedAt": report["receivedAt"], "status": "recibido"}


@app.route(route="reports", methods=["GET"])
def list_reports(req: func.HttpRequest) -> func.HttpResponse:
    reservoir_id = req.params.get("reservoirId")
    if reservoir_id is not None and not _RESERVOIR_ID.match(reservoir_id):
        return _error(400, "invalid reservoirId")
    return _json(_repository().list_reports(reservoir_id))


@app.route(route="outbox", methods=["GET"])
def list_outbox(req: func.HttpRequest) -> func.HttpResponse:
    """Alert SMS, newest first: what each farmer would receive (the sender is simulated unless SMS_SENDER is set).
    The SMS of one alert run share sentAt: the most severe and nearest come first."""
    messages = sorted(_repository().list_outbox(), key=lambda m: (alerts.LEVELS.index(m["riskLevel"]), m["distanceKm"]))
    messages.sort(key=lambda m: m["sentAt"], reverse=True)  # stable: keeps severity order within a run
    # The receiver page is public: never show a full phone number.
    return _json([m | {"phone": _mask_phone(m["phone"])} for m in messages[:OUTBOX_LIMIT]])


def _mask_phone(phone: str) -> str:
    """+56912345678 -> +569…5678: enough for someone to recognise their own number."""
    return f"{phone[:4]}…{phone[-4:]}"


def _report_id(req: func.HttpRequest) -> str | None:
    try:
        return str(UUID(req.route_params["id"]))
    except ValueError:
        return None


@app.route(route="reports/{id}", methods=["GET"])
def get_report(req: func.HttpRequest) -> func.HttpResponse:
    report_id = _report_id(req)
    if report_id is None:
        return _error(400, "id must be a UUID")
    report = _repository().get_report(report_id)
    return _json(report) if report else _error(404, "report not found")


@app.route(route="reports/{id}/photos/{index:int}", methods=["GET"])
def get_photo(req: func.HttpRequest) -> func.HttpResponse:
    report_id = _report_id(req)
    if report_id is None:
        return _error(400, "id must be a UUID")
    repo = _repository()
    report = repo.get_report(report_id)
    index = int(req.route_params["index"])
    if report is None or not 0 <= index < len(report["photos"]):
        return _error(404, "photo not found")
    photo = repo.read_photo(report["photos"][index]["file"])
    if photo is None:
        return _error(404, "photo not found")
    data, content_type = photo
    return func.HttpResponse(data, mimetype=content_type, headers={"Cache-Control": "private, max-age=86400"})


def _retryable(e: Exception) -> bool:
    if isinstance(e, anthropic.APIStatusError):
        return e.status_code == 429 or e.status_code >= 500
    # Connection errors, unusable answers and a key not configured yet can all succeed later.
    return isinstance(e, (anthropic.APIConnectionError, ModelResponseError, config.ConfigError))


@app.queue_trigger(arg_name="msg", queue_name=ANALYSIS_QUEUE, connection="AzureWebJobsStorage")
def analyze_report(msg: func.QueueMessage) -> None:
    report_id = json.loads(msg.get_body())["id"]
    repo = _repository()
    report = repo.get_report(report_id)
    if report is None or report["estado"] != "pendiente":
        return  # already analysed (duplicate message) or gone

    photos = []
    for photo in report["photos"]:
        found = repo.read_photo(photo["file"])
        if found is None:
            raise RuntimeError(f"photo {photo['file']} missing for report {report_id}")
        photos.append(Photo(media_type=found[1], data=found[0]))

    try:
        result = _analyzer().analyze_report(photos, report["notes"])
    except (anthropic.APIError, ModelResponseError, config.ConfigError) as e:
        last_try = (msg.dequeue_count or 1) >= MAX_DEQUEUE_COUNT
        if _retryable(e) and not last_try:
            logging.warning("Analysis of %s failed (try %s), retrying: %s", report_id, msg.dequeue_count, e)
            raise  # the message becomes visible again after visibilityTimeout
        logging.error("Analysis of %s failed for good: %s", report_id, e)
        repo.replace_report(report | {"estado": "error_analisis", "analysisError": type(e).__name__, "analyzedAt": _now()})
        return

    min_confidence = _config().min_confidence
    analysed = report | {
        "analisis": result.analysis.model_dump() if result.analysis else None,
        "estado": decide_estado(result.analysis, min_confidence),
        "agrees": agrees_with_officer(report["label"], result.analysis),
        "refused": result.refused,
        "model": result.model,
        "requestId": result.request_id,
        "analyzedAt": _now(),
    }
    repo.replace_report(analysed)
    # The analysis is stored; a failing alert run must not retry it (the message would be ignored anyway).
    try:
        sent = alerts.on_report_verified(repo, analysed)
        if sent:
            logging.info("Report %s: %d alert SMS for reservoir %s", report_id, len(sent), analysed["reservoirId"])
    except Exception:
        logging.exception("Alert run after report %s failed; run tools/run_alerts.py", report_id)


@app.route(route="alerts/message", methods=["POST"])
def alert_message(req: func.HttpRequest) -> func.HttpResponse:
    if denied := _check_token(req):
        return denied
    try:
        alert = AlertIn.model_validate_json(req.get_body())
    except ValidationError as e:
        return _invalid(e)
    try:
        message = _analyzer().draft_alert(alert)
    except config.ConfigError as e:
        logging.error("Incomplete configuration: %s", e)
        return _error(500, "service not configured")
    except anthropic.RateLimitError:
        return _error(503, "analysis service busy, retry later")
    except (anthropic.APIConnectionError, ModelResponseError) as e:
        logging.warning("Alert drafting failed: %s", e)
        return _error(502, "could not draft the alert")
    except anthropic.APIStatusError as e:
        if e.status_code >= 500:
            return _error(502, "could not draft the alert")
        logging.exception("Claude rejected the alert request")
        return _error(500, "internal error")
    return _json(message.model_dump())


@app.route(route="subscribers", methods=["POST"])
def subscribe(req: func.HttpRequest) -> func.HttpResponse:
    """Registers a farmer for preventive alerts. Idempotent per phone: a resend updates the field."""
    try:
        sub_in = SubscriberIn.model_validate_json(req.get_body())
    except ValidationError as e:
        return _invalid(e)
    repo = _repository()
    existing = repo.get_subscriber(sub_in.phone)
    now = _now()
    repo.save_subscriber({
        "phone": sub_in.phone,
        "location": sub_in.location.model_dump(),
        "language": sub_in.language,
        "createdAt": existing["createdAt"] if existing else now,
        "updatedAt": now,
        "optOut": False,
    })
    # Never echo what was stored: anyone can post any number.
    return _json({"phone": sub_in.phone, "status": "registrado"}, 200 if existing else 201)


@app.route(route="subscribers/unsubscribe", methods=["POST"])
def unsubscribe(req: func.HttpRequest) -> func.HttpResponse:
    try:
        phone = UnsubscribeIn.model_validate_json(req.get_body()).phone
    except ValidationError as e:
        return _invalid(e)
    repo = _repository()
    existing = repo.get_subscriber(phone)
    if existing and not existing["optOut"]:
        repo.save_subscriber(existing | {"optOut": True, "updatedAt": _now()})
    # Same answer whether or not the number was registered.
    return _json({"phone": phone, "status": "dado_de_baja"})
