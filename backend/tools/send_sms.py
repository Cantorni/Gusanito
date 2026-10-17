"""Sends the PWA a link with probable points by SMS (Twilio), or just prints it.

Usage (from backend/):
  .venv/bin/python tools/send_sms.py --pwa https://TU-PWA.azurestaticapps.net            # print the link
  .venv/bin/python tools/send_sms.py --pwa https://... --to +57XXXXXXXXXX                # send it
  .venv/bin/python tools/send_sms.py --pwa https://... --points "-15.4165,28.2849,0.8;-15.41,28.295,0.5"

Needs TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN and TWILIO_FROM to send. A trial account only reaches verified numbers.
"""

import argparse
import base64
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from gusanito.smslink import encode  # noqa: E402

# Three sample patches in the demo area near Chimunsi, Eastern Province (tools/publish_sample_layer.py).
SAMPLE = "-14.2980,31.6000,0.99;-14.3220,31.6270,0.83;-14.3430,31.5560,0.71"


def send(to: str, body: str) -> None:
    sid, token, sender = (os.environ[k] for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM"))
    req = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=urllib.parse.urlencode({"To": to, "From": sender, "Body": body}).encode(),
        headers={"Authorization": "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode()},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("Twilio answered", r.status)
    except urllib.error.HTTPError as e:
        sys.exit(f"Twilio refused ({e.code}): {e.read().decode()}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pwa", required=True, help="PWA base URL")
    ap.add_argument("--points", default=SAMPLE, help="lat,lon,prob;lat,lon,prob")
    ap.add_argument("--to", help="phone number in E.164; omit to only print the link")
    args = ap.parse_args()

    points = [tuple(map(float, p.split(","))) for p in args.points.split(";")]
    link = encode(args.pwa.rstrip("/"), points)
    print(link, f"({len(link)} chars)")
    if args.to:
        send(args.to, link)


if __name__ == "__main__":
    main()
