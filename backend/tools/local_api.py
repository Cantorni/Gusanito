"""Runs the HTTP API locally without Azure Functions Core Tools, Azurite or Docker.

Serves the same handlers as function_app.py on http://127.0.0.1:7071 (where frontend/dev-server.js proxies
/api), with storage in one SQLite file. The analysis queue becomes a background thread.

Usage (from backend/):
    .venv/bin/python tools/local_api.py                 # data in data/gusanito.db
    .venv/bin/python tools/local_api.py --fake-analysis # no ANTHROPIC_API_KEY: the officer's label decides

--fake-analysis is for demos only: it answers what the officer picked, so a photo labelled "Hay plaga" on a
reservoir confirms it and triggers the alert engine. Without it and without a key, reports stay pendiente.
"""

import argparse
import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlsplit

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("SQLITE_PATH", str(BACKEND / "data" / "gusanito.db"))
os.environ.setdefault("REPORT_TOKEN", "dev-token")

import azure.functions as func  # noqa: E402

import function_app  # noqa: E402
from gusanito.analysis import AnalysisResult  # noqa: E402
from gusanito.models import Analysis  # noqa: E402


_label = threading.local()  # the report's officer label, for --fake-analysis


class LabelAnalyzer:
    """Demo stand-in for Claude: answers what the officer picked, with high confidence."""

    def analyze_report(self, photos, notes):
        label = _label.value
        pest = label == "armyworm"
        analysis = Analysis(
            hay_plaga=pest, confianza=0.9 if label in ("armyworm", "not_armyworm") else 0.4,
            calidad_fotos="buena", tipo_vegetacion="maiz", es_hospedero="si",
            senales_gusano=["larva_visible"] if pest else [], severidad="media" if pest else "ninguna",
        )
        return AnalysisResult(analysis, False, "demo-label", None)


def _routes():
    """(method, regex, function) for every HTTP function in function_app, read from its decorators."""
    routes = []
    for fn in function_app.app.get_functions():
        trigger = fn.get_trigger()
        if trigger.get_binding_name() != "httpTrigger":
            continue
        # reports/{id}/photos/{index:int} -> named groups
        pattern = re.sub(r"\{(\w+)(?::\w+)?\}", r"(?P<\1>[^/]+)", trigger.route)
        for method in trigger.methods:
            routes.append((str(method.value), re.compile(f"^/api/{pattern}$"), fn.get_user_function()))
    # Literal routes first, so subscribers/unsubscribe never matches a {param} route.
    return sorted(routes, key=lambda r: "(?P" in r[1].pattern)


ROUTES = _routes()


class LocalQueue:
    """Stands in for the analysis-requests queue: analyses each report on a thread, with the host's retries."""

    analyze = True

    def set(self, value: str) -> None:
        if self.analyze:
            threading.Thread(target=_analyze, args=(json.loads(value),), daemon=True).start()


def _analyze(message: dict) -> None:
    report = function_app._repository().get_report(message["id"])
    _label.value = report and report["label"]
    for attempt in range(1, function_app.MAX_DEQUEUE_COUNT + 1):
        msg = SimpleNamespace(get_body=lambda: json.dumps(message).encode(), dequeue_count=attempt)
        try:
            function_app.analyze_report.build().get_user_function()(msg)
            return
        except Exception as e:  # the Functions host would retry after visibilityTimeout
            logging.warning("analysis try %d failed: %s", attempt, e)
            time.sleep(min(30, 2**attempt))


class Handler(BaseHTTPRequestHandler):
    def _handle(self, method: str) -> None:
        url = urlsplit(self.path)
        for route_method, regex, fn in ROUTES:
            m = regex.match(url.path)
            if m and route_method == method:
                break
        else:
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        req = func.HttpRequest(
            method=method, url=self.path, body=self.rfile.read(length) if length else b"",
            headers=dict(self.headers.items()), params=dict(parse_qsl(url.query)), route_params=m.groupdict(),
        )
        try:
            res = fn(req, LocalQueue()) if fn.__name__ == "create_report" else fn(req)
        except Exception:
            logging.exception("handler failed")
            return self._send(500, {"error": "internal error"})
        self.send_response(res.status_code)
        self.send_header("Content-Type", res.mimetype or "application/octet-stream")
        for k, v in res.headers.items():
            if k.lower() not in ("content-type", "content-length"):
                self.send_header(k, v)
        body = res.get_body()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, fmt, *args):
        logging.info("%s %s", self.command, self.path.split("?")[0])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=7071)
    parser.add_argument("--fake-analysis", action="store_true", help="demo: the officer's label is the analysis")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.fake_analysis:
        function_app._analyzer = lambda: LabelAnalyzer()
        logging.warning("--fake-analysis: the officer's label is taken as the analysis (demo only)")
    elif not os.environ.get("ANTHROPIC_API_KEY"):
        LocalQueue.analyze = False
        logging.warning("No ANTHROPIC_API_KEY: new reports stay pendiente (use --fake-analysis for a demo)")

    print(f"Gusanito API on http://127.0.0.1:{args.port}/api (storage: {os.environ['SQLITE_PATH']})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
