from types import SimpleNamespace

import pytest

from conftest import PHOTO_BYTES
from gusanito.analysis import Analyzer, ModelResponseError, Photo
from gusanito.models import AlertIn, AlertMessage, Analysis

ANALYSIS = Analysis(
    hay_plaga=True,
    confianza=0.8,
    calidad_fotos="buena",
    tipo_vegetacion="maiz",
    es_hospedero="si",
    senales_gusano=["hojas_ventana"],
    severidad="media",
)


class FakeClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def response(parsed, stop_reason="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(stop_reason=stop_reason, parsed_output=parsed, model=model, _request_id="req_123")


def analyze(client, model="claude-opus-5-5"):
    return Analyzer(client, model, "medium").analyze_report([Photo("image/jpeg", PHOTO_BYTES)], "Huerto con riego")


def test_sends_images_schema_and_fallback():
    client = FakeClient(response(ANALYSIS))
    result = analyze(client)

    assert result.analysis == ANALYSIS and not result.refused and result.request_id == "req_123"
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_format"] is Analysis
    assert call["output_config"] == {"effort": "medium"}
    assert call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    content = call["messages"][0]["content"]
    assert content[0]["type"] == "image" and content[0]["source"]["media_type"] == "image/jpeg"
    # GPS and date are not sent: they do not help judge the photo and made the model second-guess it.
    assert content[-1]["text"] == "Fotos: 1.\nNotas del extensionista: Huerto con riego"


def test_works_without_notes():
    client = FakeClient(response(ANALYSIS))
    Analyzer(client, "claude-opus-5-5", "medium").analyze_report([Photo("image/png", PHOTO_BYTES)], None)
    assert client.calls[0]["messages"][0]["content"][-1]["text"] == "Fotos: 1."


def test_haiku_has_no_server_side_fallback():
    client = FakeClient(response(ANALYSIS, model="claude-haiku-5-5"))
    analyze(client, model="claude-haiku-5-5")
    assert "fallbacks" not in client.calls[0] and "betas" not in client.calls[0]


def test_refusal_is_flagged_not_raised():
    result = analyze(FakeClient(response(None, stop_reason="refusal")))
    assert result.refused and result.analysis is None


def test_truncated_answer_raises():
    with pytest.raises(ModelResponseError):
        analyze(FakeClient(response(None, stop_reason="max_tokens")))


def test_alert_retries_when_sms_is_long():
    long = AlertMessage(sms="x" * 200, voice="...")
    short = AlertMessage(sms="Fall armyworm reservoir 2 km north. Check your maize whorls this week.", voice="...")
    client = FakeClient(response(long), response(short))
    alert = AlertIn(risk_level="high", distance_km=2, direction="north", days_to_act=7, language="en")

    assert Analyzer(client, "claude-opus-5-5", "medium").draft_alert(alert) == short
    assert len(client.calls) == 2
    assert "200 characters" in client.calls[1]["messages"][0]["content"]
    assert client.calls[0]["output_config"] == {"effort": "low"}


def test_alert_fails_if_still_long():
    long = AlertMessage(sms="x" * 200, voice="...")
    alert = AlertIn(risk_level="medium", distance_km=3, days_to_act=5)
    with pytest.raises(ModelResponseError):
        Analyzer(FakeClient(response(long), response(long)), "claude-opus-5-5", "medium").draft_alert(alert)
