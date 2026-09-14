"""Browser-origin protection for the local API (audit P0.1).

Security boundary of this local-first, single-user deployment:

* The API binds to ``KP_API_HOST`` (127.0.0.1 by default) — nothing on the network can reach it.
* Anything that can run on this machine (a shell, ``kp``, a script) is trusted: there is no user login.
* The one thing that *must not* be trusted is a web page open in the user's browser: without these checks any site
  could call ``http://127.0.0.1:8010/api/...`` and change the model endpoint, add sources or read settings.

Two layers, both keyed on the browser-supplied ``Origin``:

1. CORS is limited to the application's own origins (``allowed_origins``), so cross-origin scripts cannot *read*
   responses and preflights from other origins are refused.
2. ``OriginGuardMiddleware`` refuses every mutating request (POST/PUT/PATCH/DELETE) whose ``Origin`` is not an
   application origin — this also stops "simple" cross-site POSTs that CORS alone would let through. Requests
   without a browser ``Origin`` header (curl, ``kp``, server-to-server) are unaffected.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import get_settings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")  # vite dev server (proxies /api)


def allowed_origins() -> list[str]:
    """Origins the browser may use: the served SPA (api host/port on every loopback spelling), the Vite dev
    server, plus ``KP_ALLOWED_ORIGINS`` (comma-separated) for reverse proxies or other ports."""
    s = get_settings()
    hosts = {s.api_host, "127.0.0.1", "localhost", "[::1]"}
    if s.api_host in ("0.0.0.0", "::"):
        hosts.discard(s.api_host)
    origins = [f"http://{h}:{s.api_port}" for h in sorted(hosts)]
    origins += list(DEV_ORIGINS)
    origins += [o.strip().rstrip("/") for o in (s.allowed_origins or "").split(",") if o.strip()]
    return list(dict.fromkeys(origins))


def origin_allowed(origin: str | None) -> bool:
    return bool(origin) and origin.rstrip("/") in allowed_origins()


class OriginGuardMiddleware:
    """Refuse cross-origin *mutations* from browsers. Pure ASGI so it costs nothing on the hot path."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"].upper() not in SAFE_METHODS:
            request = Request(scope)
            origin = request.headers.get("origin")
            fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
            refused = (origin is not None and not origin_allowed(origin)) or fetch_site == "cross-site"
            if refused:
                response = JSONResponse(
                    {"detail": "cross-origin request refused: this API only accepts mutations from its own UI"},
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


__all__ = ["OriginGuardMiddleware", "allowed_origins", "origin_allowed"]
