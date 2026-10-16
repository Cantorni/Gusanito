import pytest

from gusanito.geo import filter_by_bbox, geometry_bbox, parse_bbox


def square(lon, lat, side=0.01):
    return {
        "type": "Polygon",
        "coordinates": [[[lon, lat], [lon + side, lat], [lon + side, lat + side], [lon, lat + side], [lon, lat]]],
    }


def test_bbox_of_polygon_and_point():
    assert geometry_bbox(square(33.0, -13.0, 0.5)) == (33.0, -13.0, 33.5, -12.5)
    assert geometry_bbox({"type": "Point", "coordinates": [33.1, -13.2]}) == (33.1, -13.2, 33.1, -13.2)


def test_bbox_of_geometry_collection():
    collection = {"type": "GeometryCollection", "geometries": [square(33.0, -13.0), square(34.0, -12.0)]}
    assert geometry_bbox(collection) == (33.0, -13.0, 34.01, -11.99)


def test_filter_by_bbox():
    layer = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": "near", "geometry": square(33.48, -13.03), "properties": {}},
            {"type": "Feature", "id": "far", "geometry": square(35.0, -15.0), "properties": {}},
            {"type": "Feature", "id": "no-geometry", "geometry": None, "properties": {}},
        ],
    }
    filtered = filter_by_bbox(layer, (33.4, -13.1, 33.6, -12.9))
    assert [f["id"] for f in filtered["features"]] == ["near"]
    assert filtered["type"] == "FeatureCollection"


@pytest.mark.parametrize("text", ["1,2,3", "a,b,c,d", "34,-13,33,-12", "0,-95,1,1"])
def test_invalid_bbox(text):
    with pytest.raises(ValueError):
        parse_bbox(text)
