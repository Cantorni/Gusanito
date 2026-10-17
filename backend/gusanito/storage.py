import json
from typing import Any

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

from .config import Config

PHOTOS_CONTAINER = "photos"
LAYERS_CONTAINER = "layers"
# Published by the satellite and model pipeline.
RESERVOIRS_BLOB = "reservoirs/current.geojson"
REPORTS_TABLE = "reports"
NO_RESERVOIR = "_none"

_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_CONTENT_TYPES = {v: k for k, v in _EXTENSIONS.items()}
# Nested values are stored as JSON strings: Table Storage only holds flat properties.
_JSON_FIELDS = ("photos", "location", "meta", "analisis")
# None values are not stored, so reads fill them back in to keep one shape for clients.
REPORT_FIELDS = (
    "demo", "reservoirId", "label", "location", "capturedAt", "notes", "meta", "photos", "receivedAt",
    "estado", "analisis", "agrees", "refused", "model", "requestId", "analyzedAt", "analysisError",
)


class Repository:
    def __init__(self, blobs: BlobServiceClient, tables: TableServiceClient):
        self._blobs = blobs
        self._tables = tables
        self._ready = False

    @classmethod
    def from_config(cls, config: Config) -> "Repository":
        if config.storage_connection_string:
            return cls(
                BlobServiceClient.from_connection_string(config.storage_connection_string),
                TableServiceClient.from_connection_string(config.storage_connection_string),
            )
        # On Azure, DefaultAzureCredential uses the managed identity named in AZURE_CLIENT_ID.
        credential = DefaultAzureCredential()
        account = config.storage_account
        return cls(
            BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=credential),
            TableServiceClient(f"https://{account}.table.core.windows.net", credential=credential),
        )

    def _ensure(self) -> None:
        if self._ready:
            return
        for container in (PHOTOS_CONTAINER, LAYERS_CONTAINER):
            try:
                self._blobs.create_container(container)
            except ResourceExistsError:
                pass
        self._tables.create_table_if_not_exists(REPORTS_TABLE)
        self._ready = True

    def _table(self):
        self._ensure()
        return self._tables.get_table_client(REPORTS_TABLE)

    def save_photo(self, report_id: str, index: int, data: bytes, media_type: str) -> str:
        self._ensure()
        name = f"{report_id}/{index}.{_EXTENSIONS[media_type]}"
        self._blobs.get_blob_client(PHOTOS_CONTAINER, name).upload_blob(
            data, overwrite=True, content_settings=ContentSettings(content_type=media_type)
        )
        return name

    def read_photo(self, name: str) -> tuple[bytes, str] | None:
        self._ensure()
        try:
            data = self._blobs.get_blob_client(PHOTOS_CONTAINER, name).download_blob().readall()
        except ResourceNotFoundError:
            return None
        return data, _CONTENT_TYPES[name.rsplit(".", 1)[-1]]

    def read_reservoirs_layer(self) -> dict[str, Any] | None:
        self._ensure()
        try:
            download = self._blobs.get_blob_client(LAYERS_CONTAINER, RESERVOIRS_BLOB).download_blob()
        except ResourceNotFoundError:
            return None
        return json.loads(download.readall())

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        for entity in self._table().query_entities("RowKey eq @id", parameters={"id": report_id}):
            return _from_entity(entity)
        return None

    def list_reports(self, reservoir_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        table = self._table()
        if reservoir_id:
            entities = table.query_entities("PartitionKey eq @pk", parameters={"pk": reservoir_id})
        else:
            entities = table.list_entities()
        reports = sorted((_from_entity(e) for e in entities), key=lambda r: r["receivedAt"], reverse=True)
        return reports[:limit]

    def create_report(self, report: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Creates the report. On a concurrent resend returns the stored one and False."""
        table = self._table()
        try:
            table.create_entity(_to_entity(report))
        except ResourceExistsError:
            return _from_entity(table.get_entity(_partition(report), report["id"])), False
        return report, True

    def replace_report(self, report: dict[str, Any]) -> None:
        self._table().update_entity(_to_entity(report), mode=UpdateMode.REPLACE)


def _partition(report: dict[str, Any]) -> str:
    return report.get("reservoirId") or NO_RESERVOIR


def _to_entity(report: dict[str, Any]) -> dict[str, Any]:
    entity = {k: v for k, v in report.items() if k != "id" and v is not None}
    for field in _JSON_FIELDS:
        if field in entity:
            entity[field] = json.dumps(entity[field], ensure_ascii=False)
    return {"PartitionKey": _partition(report), "RowKey": report["id"], **entity}


def _from_entity(entity: Any) -> dict[str, Any]:
    data = dict(entity)
    data.pop("PartitionKey")
    report: dict[str, Any] = {"id": data.pop("RowKey"), **dict.fromkeys(REPORT_FIELDS)}
    for field in _JSON_FIELDS:
        if field in data:
            data[field] = json.loads(data[field])
    report.update(data)
    return report
