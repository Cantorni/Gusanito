"""Compact link that carries probable points in an SMS, so the installed PWA can show them with no data.

Query `?p=` holds underscore-separated integers, three per point: latitude, longitude (units of 1e-4
degrees, ~11 m) and probability (0-99 %). The first point is absolute, the rest are deltas from the
previous one. Only URL-safe characters, so SMS apps do not cut the link. frontend/index.html decodes it.
"""

SCALE = 10_000


def encode(base_url: str, points: list[tuple[float, float, float]]) -> str:
    """points: (lat, lon, probability 0-1). Returns the full link."""
    parts: list[int] = []
    prev_lat = prev_lon = 0
    for lat, lon, prob in points:
        lat_i, lon_i = round(lat * SCALE), round(lon * SCALE)
        parts += [lat_i - prev_lat, lon_i - prev_lon, min(99, max(0, round(prob * 100)))]
        prev_lat, prev_lon = lat_i, lon_i
    return f"{base_url}/?p={'_'.join(map(str, parts))}"
