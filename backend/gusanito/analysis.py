import base64
from dataclasses import dataclass
from datetime import datetime

import anthropic

from .models import MAX_SMS_CHARS, AlertIn, AlertMessage, Analysis

# Spanish prompt: the analysis fields and the note for the officer are in Spanish.
REPORT_PROMPT = """\
Eres un asistente agronómico que revisa fotos de terreno para un sistema de alerta temprana del \
gusano cogollero (Spodoptera frugiperda) en África subsahariana. Un extensionista agrícola tomó las \
fotos en un parche de vegetación verde durante la estación seca, que podría ser un reservorio de la \
plaga. Tu análisis decide si el parche es un reservorio activo, si es solo vegetación hospedera sin \
plaga, o si se descarta.

Qué buscar:
- Tipo de vegetación y si puede hospedar al gusano cogollero: maíz, sorgo y muchos pastos sí; árboles, \
arbustos y suelo desnudo no.
- Señales de daño: hojas raspadas con aspecto de ventana translúcida (larvas jóvenes), agujeros \
irregulares en las hojas, excremento parecido a aserrín en el cogollo, larvas visibles, masas de \
huevos cubiertas de escamas.
- Rasgos de la larva: una Y invertida clara en la cabeza y cuatro puntos oscuros en cuadrado sobre el \
penúltimo segmento del cuerpo.

Otros gusanos y barrenadores causan daños parecidos. Marca una señal solo si la ves en las fotos. Si un \
daño podría ser de otra plaga, baja la confianza y pide revisión humana. Si las fotos están borrosas, \
oscuras, muy lejos o no muestran plantas, marca la calidad como no_sirve. Escribe la nota para el \
extensionista en español simple, sin jerga técnica."""

ALERT_PROMPT = """\
You write alerts for smallholder maize farmers about fall armyworm. Alerts come from a satellite map \
of reservoirs: places where the pest survived the dry season and from where it can move into newly \
planted maize. Write short, concrete sentences without jargon that say what to do and when: check the \
whorl and the underside of the leaves for scraped "window" leaves, ragged holes or sawdust-like frass. \
Do not overstate the risk and do not recommend specific chemical products."""

_LANGUAGES = {"es": "Spanish", "en": "English", "ny": "Chichewa (Nyanja)"}


@dataclass(frozen=True)
class AnalysisResult:
    analysis: Analysis | None
    refused: bool
    model: str
    request_id: str | None


@dataclass(frozen=True)
class Photo:
    media_type: str
    data: bytes


class ModelResponseError(Exception):
    """The model answered, but not with something usable."""


class Analyzer:
    def __init__(self, client: anthropic.Anthropic, model: str, effort: str):
        self._client = client
        self._model = model
        self._effort = effort

    def _fallback_options(self) -> dict:
        # If a safety classifier declines the request, the API reruns it on the model Anthropic
        # recommends for that category. Claude Haiku 5.5 has no server-side fallback.
        if self._model.startswith("claude-haiku"):
            return {}
        return {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}

    def analyze_report(
        self,
        photos: list[Photo],
        location: dict | None,
        captured_at: datetime | None,
        notes: str | None,
    ) -> AnalysisResult:
        content: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": p.media_type,
                    "data": base64.standard_b64encode(p.data).decode(),
                },
            }
            for p in photos
        ]
        context = [f"Fotos: {len(photos)}."]
        if location:
            context.append(f"Ubicación: {location['lat']:.5f}, {location['lng']:.5f}.")
        if captured_at:
            context.append(f"Fecha de la foto: {captured_at.date().isoformat()}.")
        if notes:
            context.append(f"Notas del extensionista: {notes}")
        content.append({"type": "text", "text": "\n".join(context)})

        response = self._client.beta.messages.parse(
            model=self._model,
            max_tokens=16000,
            system=REPORT_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self._effort},
            output_format=Analysis,
            **self._fallback_options(),
        )
        if response.stop_reason == "refusal":
            return AnalysisResult(None, True, response.model, response._request_id)
        if response.stop_reason != "end_turn" or response.parsed_output is None:
            raise ModelResponseError(f"unexpected stop_reason: {response.stop_reason}")
        return AnalysisResult(response.parsed_output, False, response.model, response._request_id)

    def draft_alert(self, alert: AlertIn) -> AlertMessage:
        direction = f", to the {alert.direction}" if alert.direction else ""
        request = (
            f"Language: {_LANGUAGES[alert.language]}.\n"
            f"Risk: {alert.risk_level}.\n"
            f"Confirmed reservoir {alert.distance_km:g} km from the field{direction}.\n"
            f"Check the maize within {alert.days_to_act} days.\n"
            f"The SMS must be at most {MAX_SMS_CHARS} characters."
        )
        # One retry if the SMS comes out too long.
        for _ in range(2):
            response = self._client.beta.messages.parse(
                model=self._model,
                max_tokens=4000,
                system=ALERT_PROMPT,
                messages=[{"role": "user", "content": request}],
                output_config={"effort": "low"},
                output_format=AlertMessage,
                **self._fallback_options(),
            )
            if response.stop_reason != "end_turn" or response.parsed_output is None:
                raise ModelResponseError(f"unexpected stop_reason: {response.stop_reason}")
            message = response.parsed_output
            if len(message.sms) <= MAX_SMS_CHARS:
                return message
            request += (
                f"\nA previous attempt was {len(message.sms)} characters long: \"{message.sms}\". "
                "Write a shorter one."
            )
        raise ModelResponseError("the SMS is still longer than allowed")
