"""Runs the repository against Azurite. Skipped unless Azurite is listening on localhost."""

import json
import socket
import uuid

import pytest

from gusanito.config import Config
from gusanito.storage import LAYERS_CONTAINER, REPORT_FIELDS, RESERVOIRS_BLOB, Repository

AZURITE = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;TableEndpoint=http://127.0.0.1:10002/devstoreaccount1;"
)


def _azurite_running() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 10000), timeout=0.5).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _azurite_running(), reason="Azurite is not running")


@pytest.fixture
def repo():
    repository = Repository.from_config(Config(AZURITE, None, "claude-opus-5-5", "medium", 0.7, None))
    yield repository
    # Azurite is shared with local development: leave no test reports behind.
    table = repository._table()
    for entity in table.list_entities():
        if entity.get("receivedAt") == TEST_RECEIVED_AT:
            table.delete_entity(entity["PartitionKey"], entity["RowKey"])


TEST_RECEIVED_AT = "2000-01-01T00:00:00+00:00"


def new_report(**changes) -> dict:
    report_id = str(uuid.uuid4())
    report = dict.fromkeys(REPORT_FIELDS) | {
        "id": report_id,
        "reservoirId": f"r-{uuid.uuid4().hex[:8]}",
        "label": "armyworm",
        "location": {"lat": -13.03, "lng": 33.48, "accuracy": 8.0},
        "photos": [{"file": f"{report_id}/0.jpg", "mediaType": "image/jpeg"}],
        "receivedAt": TEST_RECEIVED_AT,
        "estado": "pendiente",
    }
    return report | changes


def test_report_round_trip(repo):
    report = new_report()
    repo.save_photo(report["id"], 0, b"\xff\xd8data", "image/jpeg")

    assert repo.create_report(report) == (report, True)
    assert repo.get_report(report["id"]) == report
    assert repo.list_reports(report["reservoirId"]) == [report]
    assert repo.read_photo(report["photos"][0]["file"]) == (b"\xff\xd8data", "image/jpeg")
    # A concurrent resend keeps what was stored first.
    assert repo.create_report(report | {"label": "not_armyworm"}) == (report, False)

    analysed = report | {"estado": "descartado", "analisis": {"senales_gusano": []}, "agrees": False}
    repo.replace_report(analysed)
    assert repo.get_report(report["id"]) == analysed
    assert repo.get_report(str(uuid.uuid4())) is None
    assert repo.read_photo("missing/0.jpg") is None


def test_report_without_reservoir(repo):
    report = new_report(reservoirId=None, location=None, label=None)
    repo.create_report(report)
    assert repo.get_report(report["id"]) == report
    assert report["id"] in [r["id"] for r in repo.list_reports()]


def test_reservoirs_layer(repo):
    layer = {"type": "FeatureCollection", "features": [], "test": True}
    previous = repo.read_reservoirs_layer()
    blob = repo._blobs.get_blob_client(LAYERS_CONTAINER, RESERVOIRS_BLOB)
    blob.upload_blob(json.dumps(layer), overwrite=True)
    try:
        assert repo.read_reservoirs_layer() == layer
    finally:  # put back the layer used for local development, if there was one
        if previous is None:
            blob.delete_blob()
        else:
            blob.upload_blob(json.dumps(previous), overwrite=True)
