from collections.abc import Iterator
from typing import Any

BBox = tuple[float, float, float, float]  # lon_min, lat_min, lon_max, lat_max


def parse_bbox(text: str) -> BBox:
    parts = text.split(",")
    if len(parts) != 4:
        raise ValueError("bbox must be lon_min,lat_min,lon_max,lat_max")
    lon_min, lat_min, lon_max, lat_max = (float(p) for p in parts)
    if not (-180 <= lon_min <= lon_max <= 180 and -90 <= lat_min <= lat_max <= 90):
        raise ValueError("bbox out of range or min greater than max")
    return lon_min, lat_min, lon_max, lat_max


def _positions(coordinates: Any) -> Iterator[tuple[float, float]]:
    if coordinates and isinstance(coordinates[0], (int, float)):
        yield coordinates[0], coordinates[1]
        return
    for part in coordinates:
        yield from _positions(part)


def geometry_bbox(geometry: dict[str, Any]) -> BBox | None:
    if geometry.get("type") == "GeometryCollection":
        boxes = [b for g in geometry.get("geometries", []) if (b := geometry_bbox(g))]
        if not boxes:
            return None
        return (
            min(b[0] for b in boxes),
            min(b[1] for b in boxes),
            max(b[2] for b in boxes),
            max(b[3] for b in boxes),
        )
    positions = list(_positions(geometry.get("coordinates", [])))
    if not positions:
        return None
    lons = [p[0] for p in positions]
    lats = [p[1] for p in positions]
    return min(lons), min(lats), max(lons), max(lats)


def intersects(a: BBox, b: BBox) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def filter_by_bbox(collection: dict[str, Any], bbox: BBox) -> dict[str, Any]:
    features = [
        f
        for f in collection.get("features", [])
        if f.get("geometry") and (box := geometry_bbox(f["geometry"])) and intersects(box, bbox)
    ]
    return {**collection, "features": features}
