#!/usr/bin/env python3
"""Prowlarr Search Watcher — Flask web application."""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from pathlib import Path  # noqa: E402

from flask import Flask, request, send_from_directory  # noqa: E402

from db import init_db  # noqa: E402
from routes import bp, problem  # noqa: E402
from scheduler import scheduler  # noqa: E402
from worker import work_queue  # noqa: E402

STATIC_DIR = Path(__file__).parent / "static"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.register_blueprint(bp)


@app.route("/")
def index_page():
    return send_from_directory(app.static_folder, "index.html")


@app.errorhandler(404)
def not_found(_exc):
    """API routes get a JSON 404; everything else falls back to the SPA shell."""
    if request.path.startswith("/api/"):
        return problem(404, "Not found")
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return send_from_directory(app.static_folder, "index.html")
    return problem(404, "Not found")


init_db()
work_queue.start()
scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
