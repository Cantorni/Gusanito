import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

# A tiny JPEG-looking payload; the API checks base64 and size, it never decodes the image.
PHOTO_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64 + b"\xff\xd9"
PHOTO_B64 = base64.b64encode(PHOTO_BYTES).decode()
REPORT_ID = "0b6f6b8e-6a8f-4f0e-9a4f-2d0b1c3e4f50"


def report_body(**changes) -> dict:
    """A report as the PWA sends it (camelCase)."""
    body = {
        "id": REPORT_ID,
        "reservoirId": "kasungu-0042",
        "label": "armyworm",
        "location": {"lat": -13.0333, "lng": 33.4833, "accuracy": 8},
        "capturedAt": "2026-09-12T08:30:00+02:00",
        "notes": "Huerto con riego junto al río",
        "photos": [{"mediaType": "image/jpeg", "data": PHOTO_B64}],
        "meta": {"name": "IMG_0001.jpg", "originalSize": 2_400_000, "width": 4032, "height": 3024},
    }
    body.update(changes)
    return body
