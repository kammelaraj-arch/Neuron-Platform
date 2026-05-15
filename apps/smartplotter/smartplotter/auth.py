"""API-key authentication gate. SmartPlotter accepts the same Bearer
tokens the Neuron platform's /ui/secrets issues — bind the device to a
single integration-tier key and the master can authenticate against it
the same way as anything else."""
from __future__ import annotations

from functools import wraps

from flask import abort, request


def require_api_key(app):
    """Decorator factory bound to the Flask app's config (which carries
    the configured API key). Usage:

        @app.route(...)
        @require_api_key(app)
        def handler(): ...
    """
    expected = (app.config.get("SMARTPLOTTER_API_KEY") or "").strip()

    def decorator(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not expected:
                # No key configured → allow (dev mode). Production
                # MUST set SMARTPLOTTER_API_KEY.
                return fn(*a, **kw)
            given = (
                request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                or request.headers.get("X-API-Key", "").strip()
                or request.cookies.get("smartplotter_key", "").strip()
            )
            if given != expected:
                abort(401)
            return fn(*a, **kw)
        return wrapper
    return decorator
