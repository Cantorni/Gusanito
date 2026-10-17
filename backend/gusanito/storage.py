import json
import sqlite3
import threading
from pathlib import Path
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
SUBSCRIBERS_TABLE = "subscribers"
# Every alert SMS (simulated or sent). Also the log the alert engine deduplicates against.
OUTBOX_TABLE = "outbox"
SUBSCRIBER_PARTITION = "subscriber"
NO_RESERVOIR = "_none"

_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
_CONTENT_TYPES = {v: k for k, v in _EXTENSIONS.items()}
# Nested values are stored as JSON strings: Table Storage only holds flat properties.
_JSON_FIELDS = ("photos", "location", "meta", "analisis", "scene", "reservoirIds")
# None values are not stored, so reads fill them back in to keep one shape for clients.
REPORT_FIELDS = (
    "demo", "reservoirId", "label", "location", "capturedAt", "notes", "meta", "photos", "receivedAt",
    "estado", "analisis", "agrees", "refused", "model", "requestId", "analyzedAt", "analysisError",
    # The satellite scene the reservoir was mapped from: {"id", "date", "bbox"}, copied from the layer.
    "scene",
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
        for table in (REPORTS_TABLE, SUBSCRIBERS_TABLE, OUTBOX_TABLE):
            self._tables.create_table_if_not_exists(table)
        self._ready = True

    def _table(self, name: str = REPORTS_TABLE):
        self._ensure()
        return self._tables.get_table_client(name)

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

    def write_reservoirs_layer(self, layer: dict[str, Any]) -> None:
        self._ensure()
        self._blobs.get_blob_client(LAYERS_CONTAINER, RESERVOIRS_BLOB).upload_blob(
            json.dumps(layer), overwrite=True, content_settings=ContentSettings(content_type="application/geo+json")
        )

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

    def get_subscriber(self, phone: str) -> dict[str, Any] | None:
        try:
            entity = self._table(SUBSCRIBERS_TABLE).get_entity(SUBSCRIBER_PARTITION, phone)
        except ResourceNotFoundError:
            return None
        return _from_row(entity, "phone")

    def save_subscriber(self, subscriber: dict[str, Any]) -> None:
        self._table(SUBSCRIBERS_TABLE).upsert_entity(
            _to_row(subscriber, SUBSCRIBER_PARTITION, "phone"), mode=UpdateMode.REPLACE
        )

    def list_subscribers(self) -> list[dict[str, Any]]:
        return [_from_row(e, "phone") for e in self._table(SUBSCRIBERS_TABLE).list_entities()]

    def add_outbox(self, message: dict[str, Any]) -> None:
        self._table(OUTBOX_TABLE).create_entity(_to_row(message, message["phone"], "id"))

    def list_outbox(self, since: str | None = None) -> list[dict[str, Any]]:
        table = self._table(OUTBOX_TABLE)
        rows = table.query_entities("sentAt ge @since", parameters={"since": since}) if since else table.list_entities()
        return sorted((_from_row(e, "id") for e in rows), key=lambda m: m["sentAt"])


def _to_row(item: dict[str, Any], partition: str, key: str) -> dict[str, Any]:
    entity = {k: json.dumps(v) if k in _JSON_FIELDS else v for k, v in item.items() if v is not None}
    return {"PartitionKey": partition, "RowKey": item[key], **entity}


def _from_row(entity: Any, key: str) -> dict[str, Any]:
    data = {k: json.loads(v) if k in _JSON_FIELDS else v for k, v in dict(entity).items()}
    data.pop("PartitionKey")
    data[key] = data.pop("RowKey")
    return data


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


class SqliteRepository:
    """Same interface as Repository, in one SQLite file: the local demo needs no Azurite or Docker.
    Each item is kept as the same JSON document the Azure path returns."""

    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the local server's threads, serialised by a lock.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, reservoir_id TEXT, received_at TEXT, doc TEXT);
                CREATE TABLE IF NOT EXISTS photos (name TEXT PRIMARY KEY, media_type TEXT, data BLOB);
                CREATE TABLE IF NOT EXISTS layers (name TEXT PRIMARY KEY, doc TEXT);
                CREATE TABLE IF NOT EXISTS subscribers (phone TEXT PRIMARY KEY, doc TEXT);
                CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, phone TEXT, sent_at TEXT, doc TEXT);
            """)

    def _all(self, sql: str, *args) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _write(self, sql: str, *args) -> int:
        with self._lock, self._db:
            return self._db.execute(sql, args).rowcount

    def save_photo(self, report_id: str, index: int, data: bytes, media_type: str) -> str:
        name = f"{report_id}/{index}.{_EXTENSIONS[media_type]}"
        self._write("INSERT OR REPLACE INTO photos VALUES (?, ?, ?)", name, media_type, data)
        return name

    def read_photo(self, name: str) -> tuple[bytes, str] | None:
        rows = self._all("SELECT data, media_type FROM photos WHERE name = ?", name)
        return (bytes(rows[0][0]), rows[0][1]) if rows else None

    def read_reservoirs_layer(self) -> dict[str, Any] | None:
        rows = self._all("SELECT doc FROM layers WHERE name = ?", RESERVOIRS_BLOB)
        return json.loads(rows[0][0]) if rows else None

    def write_reservoirs_layer(self, layer: dict[str, Any]) -> None:
        self._write("INSERT OR REPLACE INTO layers VALUES (?, ?)", RESERVOIRS_BLOB, json.dumps(layer))

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT doc FROM reports WHERE id = ?", report_id)
        return _report(rows[0][0]) if rows else None

    def list_reports(self, reservoir_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where, args = ("WHERE reservoir_id = ?", (reservoir_id,)) if reservoir_id else ("", ())
        rows = self._all(f"SELECT doc FROM reports {where} ORDER BY received_at DESC LIMIT ?", *args, limit)
        return [_report(r[0]) for r in rows]

    def create_report(self, report: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        created = self._write(
            "INSERT OR IGNORE INTO reports VALUES (?, ?, ?, ?)",
            report["id"], report.get("reservoirId"), report["receivedAt"], json.dumps(report, ensure_ascii=False),
        )
        return (report, True) if created else (self.get_report(report["id"]), False)

    def replace_report(self, report: dict[str, Any]) -> None:
        self._write(
            "UPDATE reports SET reservoir_id = ?, received_at = ?, doc = ? WHERE id = ?",
            report.get("reservoirId"), report["receivedAt"], json.dumps(report, ensure_ascii=False), report["id"],
        )

    def get_subscriber(self, phone: str) -> dict[str, Any] | None:
        rows = self._all("SELECT doc FROM subscribers WHERE phone = ?", phone)
        return json.loads(rows[0][0]) if rows else None

    def save_subscriber(self, subscriber: dict[str, Any]) -> None:
        self._write("INSERT OR REPLACE INTO subscribers VALUES (?, ?)", subscriber["phone"], json.dumps(subscriber))

    def list_subscribers(self) -> list[dict[str, Any]]:
        return [json.loads(r[0]) for r in self._all("SELECT doc FROM subscribers ORDER BY phone")]

    def add_outbox(self, message: dict[str, Any]) -> None:
        self._write(
            "INSERT INTO outbox VALUES (?, ?, ?, ?)",
            message["id"], message["phone"], message["sentAt"], json.dumps(message, ensure_ascii=False),
        )

    def list_outbox(self, since: str | None = None) -> list[dict[str, Any]]:
        rows = self._all("SELECT doc FROM outbox WHERE sent_at >= ? ORDER BY sent_at", since or "")
        return [json.loads(r[0]) for r in rows]


def _report(doc: str) -> dict[str, Any]:
    # Reports stored before a field existed still come back with every field.
    return {"id": None, **dict.fromkeys(REPORT_FIELDS), **json.loads(doc)}


def open_repository(config: Config) -> "Repository | SqliteRepository":
    if config.sqlite_path:
        return SqliteRepository(config.sqlite_path)
    return Repository.from_config(config)
