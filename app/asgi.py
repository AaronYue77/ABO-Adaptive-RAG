"""ASGI entrypoint exposing the module-level FastAPI app."""

from app.abo.http import create_abo_app

app = create_abo_app()
