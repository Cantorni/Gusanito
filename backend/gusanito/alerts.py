"""Alert engine: which subscribers get a preventive SMS about which reservoir.

A reservoir triggers alerts when its latest field verification confirmed the pest, or, before anyone has
checked it, when the satellite model gives it a high probability. Subscribers whose field lies within the
alert radius get one SMS (severity by distance and confirmation), with an offline map link (smslink) to the
reservoirs near them. Distances and their justification: docs/distancia-alerta.md.
"""

import base64
import json
import logging
import os
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import smslink
from .geo import bbox_center, bearing_deg, compass, distance_km, geometry_bbox
from .models import MAX_SMS_CHARS

# Reports that settle whether a reservoir has the pest. The others (pending, unusable photos, human review,
# analysis error) leave the reservoir as it was.
VERIFIED = ("reservorio_confirmado", "hospedero_sin_senales", "descartado")
LEVELS = ("high", "medium", "low")  # most severe first
DAYS_TO_ACT = {"high": 3, "medium": 7, "low": 14}  # "check your maize within N days", by level


@dataclass(frozen=True)
class Settings:
    # Bands, in km from the field to the reservoir (docs/distancia-alerta.md). radius_km is the outer one.
    immediate_km: float = 2.0
    near_km: float = 5.0
    radius_km: float = 10.0
    # An unverified reservoir alerts only at or above this model probability, one level lower.
    min_probability: float = 0.8
    # The same subscriber is not alerted again about the same reservoir within this time, unless it got worse.
    dedup_days: int = 14
    # One SMS per farmer only when the situation got more urgent than anything already sent to them (any reservoir).
    per_farmer: bool = False
    # Base of the offline link in the SMS: the installed PWA's address.
    pwa_url: str = "http://127.0.0.1:8787"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        d = cls()
        return cls(
            immediate_km=float(env("ALERT_IMMEDIATE_KM", d.immediate_km)),
            near_km=float(env("ALERT_NEAR_KM", d.near_km)),
            radius_km=float(env("ALERT_RADIUS_KM", d.radius_km)),
            min_probability=float(env("ALERT_MIN_PROBABILITY", d.min_probability)),
            dedup_days=int(env("ALERT_DEDUP_DAYS", d.dedup_days)),
            per_farmer=env("ALERT_PER_FARMER", "") == "1",
            pwa_url=env("PWA_URL", d.pwa_url).rstrip("/"),
        )


@dataclass(frozen=True)
class Reservoir:
    id: str
    lat: float
    lon: float
    probability: float
    confirmed: bool
    verification: str | None  # estado of the latest verified report, None if nobody checked it yet


@dataclass(frozen=True)
class Alert:
    phone: str
    language: str
    reservoir_id: str  # the one the SMS talks about: the most severe, then the nearest
    reservoir_ids: list[str]  # every reservoir in the link, all marked as alerted
    risk_level: str
    distance_km: float
    direction: str
    sms: str


# ---- reservoirs and their verification state ----

def verification_by_reservoir(reports: list[dict[str, Any]]) -> dict[str, str]:
    """Latest verified estado per reservoir: a later no-larva visit overrides an older confirmation."""
    latest: dict[str, dict[str, Any]] = {}
    for r in reports:
        rid = r.get("reservoirId")
        if rid and r.get("estado") in VERIFIED:
            key = r.get("analyzedAt") or r["receivedAt"]
            if rid not in latest or key > (latest[rid].get("analyzedAt") or latest[rid]["receivedAt"]):
                latest[rid] = r
    return {rid: r["estado"] for rid, r in latest.items()}


def reservoirs(layer: dict[str, Any] | None, reports: list[dict[str, Any]]) -> list[Reservoir]:
    states = verification_by_reservoir(reports)
    found = []
    for f in (layer or {}).get("features", []):
        box = f.get("geometry") and geometry_bbox(f["geometry"])
        if f.get("id") is None or not box:
            continue
        # shortcut: distance to the bbox centre; patches are a few hundred metres, use the polygon edge if they grow
        lat, lon = bbox_center(box)
        state = states.get(str(f["id"]))
        found.append(Reservoir(
            str(f["id"]), lat, lon, float((f.get("properties") or {}).get("probabilidad") or 0),
            state == "reservorio_confirmado", state,
        ))
    return found


def annotate_layer(layer: dict[str, Any], reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Adds `confirmado` and `verificacion` to each feature, so the PWA can draw confirmed reservoirs apart."""
    states = verification_by_reservoir(reports)
    features = []
    for f in layer.get("features", []):
        state = states.get(str(f.get("id")))
        props = {**(f.get("properties") or {}), "confirmado": state == "reservorio_confirmado", "verificacion": state}
        features.append({**f, "properties": props})
    return {**layer, "features": features}


def risk_level(reservoir: Reservoir, km: float, s: Settings) -> str | None:
    if reservoir.verification in ("hospedero_sin_senales", "descartado"):
        return None  # checked on the ground: no larvae
    if not reservoir.confirmed and reservoir.probability < s.min_probability:
        return None
    band = 0 if km <= s.immediate_km else 1 if km <= s.near_km else 2 if km <= s.radius_km else None
    if band is None:
        return None
    band += 0 if reservoir.confirmed else 1
    return LEVELS[band] if band < len(LEVELS) else None


# ---- SMS text ----

# GSM-7 only (no accents): one SMS is 160 GSM-7 characters, but a single accented letter makes it UCS-2 (70).
# Nyanja text is a first draft: have a native speaker check it before using it with farmers.
TEMPLATES = {
    "es": (
        "Gusanito: reservorio de gusano cogollero {state} a {km} km al {dir} de tu campo. Revisa tu maiz en {days} dias. Mapa:",
        "Gusanito: gusano cogollero {state} a {km} km al {dir}. Revisa tu maiz. Mapa:",
    ),
    "en": (
        "Gusanito: {state} fall armyworm reservoir {km} km {dir} of your field. Check your maize within {days} days. Map:",
        "Gusanito: {state} fall armyworm {km} km {dir}. Check your maize. Map:",
    ),
    "ny": (
        "Gusanito: fall armyworm {state} {km} km {dir} kwa munda wanu. Yang'anani chimanga chanu m'masiku {days}. Mapu:",
        "Gusanito: fall armyworm {state} {km} km {dir}. Yang'anani chimanga. Mapu:",
    ),
}
STATE_WORDS = {
    "es": ("confirmado", "probable"),
    "en": ("confirmed", "probable"),
    "ny": ("yatsimikizika", "ingakhalepo"),
}
DIRECTIONS = {
    "es": {"north": "norte", "northeast": "noreste", "east": "este", "southeast": "sureste",
           "south": "sur", "southwest": "suroeste", "west": "oeste", "northwest": "noroeste"},
    "en": {d: d for d in ("north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest")},
    # Nyanja names only the four cardinal points; the diagonals use the nearest of them.
    "ny": {"north": "kumpoto", "northeast": "kumpoto", "east": "kum'mawa", "southeast": "kum'mawa",
           "south": "kum'mwera", "southwest": "kum'mwera", "west": "kumadzulo", "northwest": "kumadzulo"},
}
GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
    "¿abcdefghijklmnopqrstuvwxyzäöñüà"
)


def is_gsm7(text: str) -> bool:
    return all(c in GSM7 for c in text)


def build_sms(language: str, confirmed: bool, km: float, direction: str, days: int,
              base_url: str, points: list[tuple[float, float, float]]) -> tuple[str, int]:
    """The SMS text plus the offline link, within MAX_SMS_CHARS, and how many points the link carries.
    Prefers the short text over dropping points, and never cuts the link (it would show garbled points)."""
    words = dict(state=STATE_WORDS[language][0 if confirmed else 1], km=f"{km:.1f}",
                 dir=DIRECTIONS[language][direction], days=days)
    texts = [t.format(**words) for t in TEMPLATES[language]]
    for n in range(len(points), 0, -1):
        link = smslink.encode(base_url, points[:n])
        for text in texts:
            if len(text) + 1 + len(link) <= MAX_SMS_CHARS:
                return f"{text} {link}", n
    # Even one point does not fit beside the short text (a very long PWA URL): cut the text, not the link.
    room = MAX_SMS_CHARS - len(link) - 1
    return (f"{texts[-1][:room]} {link}", 1) if room > 0 else (texts[-1][:MAX_SMS_CHARS], 0)


# ---- planning ----

def plan(found: list[Reservoir], subscribers: list[dict[str, Any]], outbox: list[dict[str, Any]],
         s: Settings, only: str | None = None) -> list[Alert]:
    """Pure decision: who gets which SMS. `outbox` is the recent history to deduplicate against;
    `only` limits the run to one reservoir (the one just verified)."""
    already: dict[tuple[str, str], int] = {}  # (phone, reservoir) -> most severe level index sent
    for m in outbox:
        if m.get("status") == "fallido":  # a failed SMS does not count as alerted
            continue
        for rid in m.get("reservoirIds") or [m["reservoirId"]]:
            key = (m["phone"], rid)
            already[key] = min(already.get(key, 9), LEVELS.index(m["riskLevel"]))

    sent_level: dict[str, int] = {}  # phone -> most severe level index sent for any reservoir
    for m in outbox:
        if m.get("status") != "fallido":
            sent_level[m["phone"]] = min(sent_level.get(m["phone"], 9), LEVELS.index(m["riskLevel"]))

    alerts = []
    for sub in subscribers:
        if sub.get("optOut"):
            continue
        field = sub["location"]
        candidates = []
        for r in found:
            if only and r.id != only:
                continue
            km = distance_km(field["lat"], field["lng"], r.lat, r.lon)
            level = risk_level(r, km, s)
            # Repeat only if it got worse since the last SMS (probable -> confirmed, or closer band).
            if level and LEVELS.index(level) < already.get((sub["phone"], r.id), 9):
                candidates.append((LEVELS.index(level), km, r, level))
        if not candidates:
            continue
        candidates.sort(key=lambda c: (c[0], c[1]))
        if s.per_farmer and candidates[0][0] >= sent_level.get(sub["phone"], 9):
            continue
        _, km, main, level = candidates[0]
        points = [(r.lat, r.lon, 0.99 if r.confirmed else r.probability) for _, _, r, _ in candidates]
        direction = compass(bearing_deg(field["lat"], field["lng"], main.lat, main.lon))
        sms, n = build_sms(sub.get("language", "en"), main.confirmed, km, direction, DAYS_TO_ACT[level],
                           s.pwa_url, points)
        # Only the reservoirs the SMS really carries count as alerted; the rest go in a later SMS.
        ids = [c[2].id for c in candidates][: max(n, 1)]
        alerts.append(Alert(sub["phone"], sub.get("language", "en"), main.id, ids, level, round(km, 1), direction, sms))
    return alerts


# ---- senders ----

class SimulatedSender:
    """Default: sends nothing. The message lands in the outbox table and in a JSONL file a presenter can read."""

    name = "simulado"

    def __init__(self, path: str | None):
        self.path = path

    def send(self, phone: str, text: str) -> str:
        return "simulado"

    def written(self, message: dict[str, Any]) -> None:
        if not self.path:
            return
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(message, ensure_ascii=False) + "\n")
        except OSError as e:  # the outbox table already holds it
            logging.warning("Could not write the outbox file %s: %s", self.path, e)


class TwilioSender:
    """Real SMS. Only with SMS_SENDER=twilio and the TWILIO_* settings: off by default."""

    name = "twilio"

    def __init__(self, sid: str, token: str, sender: str):
        self._sid, self._token, self._from = sid, token, sender

    def send(self, phone: str, text: str) -> str:
        req = urllib.request.Request(
            f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json",
            data=urllib.parse.urlencode({"To": phone, "From": self._from, "Body": text}).encode(),
            headers={"Authorization": "Basic " + base64.b64encode(f"{self._sid}:{self._token}".encode()).decode()},
        )
        try:
            with urllib.request.urlopen(req, timeout=30):
                return "enviado"
        except OSError as e:  # HTTPError and URLError are OSErrors
            logging.warning("Twilio did not take the SMS to %s: %s", phone[:-4] + "****", e)
            return "fallido"

    def written(self, message: dict[str, Any]) -> None:
        pass


def sender_from_env() -> SimulatedSender | TwilioSender:
    if os.environ.get("SMS_SENDER") == "twilio":
        return TwilioSender(*(os.environ[k] for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM")))
    if "ALERT_OUTBOX_FILE" in os.environ:
        return SimulatedSender(os.environ["ALERT_OUTBOX_FILE"])
    # On Azure the app folder is read-only; the outbox table already holds every message.
    if os.environ.get("WEBSITE_SITE_NAME"):
        return SimulatedSender(None)
    return SimulatedSender(str(Path(__file__).resolve().parent.parent / "data" / "outbox.jsonl"))


# ---- running ----

def run(repo: Any, settings: Settings, sender: Any, only: str | None = None, dry_run: bool = False,
        now: datetime | None = None) -> list[Alert]:
    now = now or datetime.now(UTC)
    found = reservoirs(repo.read_reservoirs_layer(), repo.list_reports(limit=100_000))
    recent = repo.list_outbox(since=(now - timedelta(days=settings.dedup_days)).isoformat())
    alerts = plan(found, repo.list_subscribers(), recent, settings, only)
    if dry_run:
        return alerts
    for a in alerts:
        status = sender.send(a.phone, a.sms)
        message = {
            "id": str(uuid.uuid4()), "phone": a.phone, "language": a.language, "reservoirId": a.reservoir_id,
            "reservoirIds": a.reservoir_ids, "riskLevel": a.risk_level, "distanceKm": a.distance_km,
            "direction": a.direction, "sms": a.sms, "sender": sender.name, "status": status,
            "sentAt": now.isoformat(),
        }
        repo.add_outbox(message)
        sender.written(message)
    return alerts


def on_report_verified(repo: Any, report: dict[str, Any], now: datetime | None = None) -> list[Alert]:
    """Called once a report's analysis is stored: re-runs the engine for its reservoir."""
    if not report.get("reservoirId") or report.get("estado") not in VERIFIED:
        return []
    return run(repo, Settings.from_env(), sender_from_env(), only=report["reservoirId"], now=now)
