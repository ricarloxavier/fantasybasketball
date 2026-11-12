"""Gunicorn entry point: `gunicorn web.wsgi:app`."""
import logging

from .app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
