#!/usr/bin/env python3
"""Prowlarr Search Watcher — Flask web application."""

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from flask import Flask  # noqa: E402

from db import init_db  # noqa: E402
from routes import bp  # noqa: E402
from scheduler import scheduler  # noqa: E402
from worker import work_queue  # noqa: E402

app = Flask(__name__)
app.register_blueprint(bp)

init_db()
work_queue.start()
scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
