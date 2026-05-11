"""ASGI middleware that enforces the Edge access policy on every inbound request.

The browser dashboard (loopback only) and the public ``/healthz`` endpoint are
exempt from the policy check; everything else is checked against
``CompiledPolicy.evaluate_inbound`` before the route handler runs.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .policy import CompiledPolicy


_OPEN_PATHS = {"/healthz"}
_LOOPBACK = ("127.", "::1", "localhost")


class AccessPolicyMiddleware:
    def __init__(self, app: ASGIApp, *, policy: CompiledPolicy, port: int) -> None:
        self.app = app
        self.policy = policy
        self.port = port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        client = scope.get("client") or ("0.0.0.0", 0)
        client_ip = client[0]

        # Loopback (operator dashboard, local healthz, smoke tests) is exempt.
        if path in _OPEN_PATHS or any(client_ip.startswith(p) for p in _LOOPBACK):
            return await self.app(scope, receive, send)

        is_emergency = path.startswith("/api/emergency")
        principal_hint = "emergency" if is_emergency else None

        # Optional fingerprint header; populated by the TLS terminator.
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers") or []}
        fingerprint = headers.get("x-client-cert-sha256")

        allowed, reason = self.policy.evaluate_inbound(
            client_ip=client_ip,
            port=self.port,
            principal_hint=principal_hint,
            client_cert_fingerprint=fingerprint,
            is_emergency=is_emergency,
        )

        if not allowed:
            response = JSONResponse(
                {"error": "denied_by_edge_policy", "detail": reason},
                status_code=403,
            )
            return await response(scope, receive, send)

        await self.app(scope, receive, send)
