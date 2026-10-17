"""Posts real photos to /api/reports, waits for Claude's analysis and prints it.

Usage (from backend/):
    .venv/bin/python tools/send_photo.py photo1.jpg [photo2.jpg ...] \
        [--url http://127.0.0.1:8787/api] [--label armyworm] [--reservoir kasungu-0042] [--demo]
"""

import argparse
import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid


def request(url, body=None):
    headers = {"Content-Type": "application/json"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as res:
        return res.status, json.load(res)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("photos", nargs="+")
    parser.add_argument("--url", default="http://127.0.0.1:8787/api")
    parser.add_argument("--label", choices=["armyworm", "not_armyworm", "unsure"])
    parser.add_argument("--reservoir")
    parser.add_argument("--demo", action="store_true", help="mark it as a demo report: kept out of the ML labels")
    parser.add_argument("--wait", type=int, default=180, help="seconds to wait for the analysis")
    args = parser.parse_args()

    photos = []
    for path in args.photos:
        with open(path, "rb") as f:
            photos.append({"mediaType": mimetypes.guess_type(path)[0], "data": base64.b64encode(f.read()).decode()})
    report_id = str(uuid.uuid4())
    body = {"id": report_id, "photos": photos, "label": args.label, "reservoirId": args.reservoir, "demo": args.demo or None}

    try:
        status, ack = request(f"{args.url}/reports", body={k: v for k, v in body.items() if v is not None})
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{e.code} {e.read().decode()}")
    print(status, ack)

    deadline = time.time() + args.wait
    while time.time() < deadline:
        _, report = request(f"{args.url}/reports/{report_id}")
        if report["estado"] != "pendiente":
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return
        time.sleep(3)
    raise SystemExit("still pending: is the Functions host running the queue trigger?")


if __name__ == "__main__":
    main()
