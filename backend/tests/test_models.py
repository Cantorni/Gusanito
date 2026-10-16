import base64

import pytest
from pydantic import ValidationError

from conftest import PHOTO_B64, report_body
from gusanito.models import MAX_PHOTO_BYTES, Analysis, ReportIn, agrees_with_officer, decide_estado


def analysis(**changes) -> Analysis:
    data = {
        "hay_plaga": False,
        "confianza": 0.9,
        "calidad_fotos": "buena",
        "tipo_vegetacion": "maiz",
        "es_hospedero": "si",
        "senales_gusano": [],
        "severidad": "ninguna",
    }
    data.update(changes)
    return Analysis(**data)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"calidad_fotos": "no_sirve", "confianza": 0.95}, "repetir_fotos"),
        ({"hay_plaga": True, "confianza": 0.5, "senales_gusano": ["larva_visible"]}, "revision_humana"),
        ({"hay_plaga": True, "senales_gusano": ["hojas_ventana", "excremento_en_cogollo"]}, "reservorio_confirmado"),
        # Sure there is no pest: high confidence, not low.
        ({"hay_plaga": False, "confianza": 0.95}, "hospedero_sin_senales"),
        ({"tipo_vegetacion": "arboles_o_arbustos", "es_hospedero": "no"}, "descartado"),
        ({}, "hospedero_sin_senales"),
        ({"es_hospedero": "incierto"}, "revision_humana"),
    ],
)
def test_decide_estado(changes, expected):
    assert decide_estado(analysis(**changes), min_confidence=0.7) == expected


def test_no_analysis_goes_to_human_review():
    assert decide_estado(None, min_confidence=0.7) == "revision_humana"


@pytest.mark.parametrize(
    ("label", "changes", "expected"),
    [
        ("armyworm", {"hay_plaga": True}, True),
        ("armyworm", {"hay_plaga": False}, False),
        ("not_armyworm", {"hay_plaga": False}, True),
        ("not_armyworm", {"hay_plaga": True}, False),
        (None, {}, None),
        ("unsure", {"hay_plaga": True}, None),
        ("armyworm", {"calidad_fotos": "no_sirve"}, None),
    ],
)
def test_agrees_with_officer(label, changes, expected):
    assert agrees_with_officer(label, analysis(**changes)) is expected


def test_valid_report_uses_camel_case():
    report = ReportIn.model_validate(report_body())
    assert report.reservoir_id == "kasungu-0042"
    assert report.photos[0].raw().startswith(b"\xff\xd8")
    assert report.meta.model_dump(by_alias=True)["originalSize"] == 2_400_000


def test_minimal_report_without_gps_or_reservoir():
    body = {"id": report_body()["id"], "photos": report_body()["photos"]}
    report = ReportIn.model_validate(body)
    assert report.location is None and report.reservoir_id is None and report.label is None


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "not-a-uuid"},
        {"reservoirId": "with/slash"},
        {"label": "maybe"},
        {"location": {"lat": -91, "lng": 0}},
        {"photos": []},
        {"photos": [{"mediaType": "image/jpeg", "data": PHOTO_B64}] * 4},
        {"photos": [{"mediaType": "image/tiff", "data": PHOTO_B64}]},
        {"photos": [{"mediaType": "image/jpeg", "data": "%%%not-base64%%%"}]},
        {"image": "data:image/jpeg;base64,abc"},  # the old /api/report body
    ],
)
def test_invalid_report(changes):
    with pytest.raises(ValidationError):
        ReportIn.model_validate(report_body(**changes))


def test_photo_too_large():
    big = base64.b64encode(b"\x00" * (MAX_PHOTO_BYTES + 1)).decode()
    with pytest.raises(ValidationError, match="larger than"):
        ReportIn.model_validate(report_body(photos=[{"mediaType": "image/jpeg", "data": big}]))
