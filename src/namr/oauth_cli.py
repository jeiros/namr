"""One-shot local OAuth flow.

Boots an http.server on the redirect URI's port, opens the authorize URL,
captures `code` from the callback, exchanges for tokens, and persists them.
"""
from __future__ import annotations

import logging
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from . import db, strava

log = logging.getLogger(__name__)


class _Callback(BaseHTTPRequestHandler):
    server_state: Optional[str] = None
    captured_code: Optional[str] = None
    captured_state: Optional[str] = None
    captured_error: Optional[str] = None

    def log_message(self, fmt: str, *args: object) -> None:  # silence default access log
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        qs = parse_qs(parsed.query)
        type(self).captured_code = (qs.get("code") or [None])[0]
        type(self).captured_state = (qs.get("state") or [None])[0]
        type(self).captured_error = (qs.get("error") or [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        body = (
            "<html><body style='font-family:sans-serif'>"
            "<h2>namr authorized — you can close this tab.</h2>"
            "</body></html>"
        )
        self.wfile.write(body.encode("utf-8"))


def run_authorize(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    db_path: Path,
    open_browser: bool = True,
) -> dict:
    parsed = urlparse(redirect_uri)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8721

    state = secrets.token_urlsafe(16)
    _Callback.server_state = state
    _Callback.captured_code = None
    _Callback.captured_state = None
    _Callback.captured_error = None

    server = HTTPServer((host, port), _Callback)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    url = strava.build_authorize_url(client_id, redirect_uri, state)
    print(f"\nAuthorize URL:\n  {url}\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    thread.join(timeout=300)
    server.server_close()

    if _Callback.captured_error:
        raise RuntimeError(f"oauth error: {_Callback.captured_error}")
    if not _Callback.captured_code:
        raise RuntimeError("no authorization code received (timeout?)")
    if _Callback.captured_state != state:
        raise RuntimeError("state mismatch — possible csrf, aborting")

    token = strava.exchange_code(client_id, client_secret, _Callback.captured_code)
    db.save_token(
        db_path,
        athlete_id=token.athlete_id,
        access_token=token.access_token,
        refresh_token=token.refresh_token,
        expires_at=token.expires_at,
        scope=token.scope,
    )
    return {
        "athlete_id": token.athlete_id,
        "expires_at": token.expires_at,
        "scope": token.scope,
    }
