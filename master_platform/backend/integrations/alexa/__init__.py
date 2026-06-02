"""Custom Alexa Smart Home Skill integration.

This is the inbound path: the operator builds a private Alexa Smart
Home Skill on their Amazon Developer account (one-time, ~30 min);
Alexa Cloud → Lambda → POST /api/alexa/directive on this Master.
Discovery returns every Tado zone + Ring device the platform already
knows about, and the supported control directives proxy to the
existing Tado / Ring clients.

The only Amazon-side fact this code cares about is the operator's
Alexa account email — recorded so the setup page can address them by
name when guiding the Skill creation steps.
"""
from .store import AlexaStore, AlexaState

__all__ = ["AlexaStore", "AlexaState"]
