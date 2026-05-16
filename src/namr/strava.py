from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from . import db

log = logging.getLogger(__name__)

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"
SCOPES = "read,activity:read_all,activity:write"


class StravaError(Exception):
    pass


class AuthRequired(StravaError):
    """No token in DB — run `namr authorize` first."""


@dataclass
class Token:
    access_token: str
    refresh_token: str
    expires_at: int
    athlete_id: Optional[int] = None
    scope: Optional[str] = None

    @property
    def is_expired(self) -> bool:
        # refresh a bit before expiry
        return time.time() >= (self.expires_at - 60)


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    from urllib.parse import urlencode

    qs = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "approval_prompt": "auto",
            "scope": SCOPES,
            "state": state,
        }
    )
    return f"{AUTH_URL}?{qs}"


def exchange_code(client_id: str, client_secret: str, code: str) -> Token:
    with httpx.Client(timeout=30) as client:
        r = client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        r.raise_for_status()
        data = r.json()
        return Token(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=int(data["expires_at"]),
            athlete_id=(data.get("athlete") or {}).get("id"),
            scope=SCOPES,
        )


def refresh_token(client_id: str, client_secret: str, refresh: str) -> Token:
    with httpx.Client(timeout=30) as client:
        r = client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
        r.raise_for_status()
        data = r.json()
        return Token(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=int(data["expires_at"]),
        )


class StravaClient:
    def __init__(self, *, client_id: str, client_secret: str, db_path: Path):
        self.client_id = client_id
        self.client_secret = client_secret
        self.db_path = db_path
        self._http = httpx.Client(timeout=30, base_url=API_BASE)
        self._token: Optional[Token] = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "StravaClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --- auth ---

    def _load_token(self) -> Token:
        if self._token is not None:
            return self._token
        row = db.load_token(self.db_path)
        if row is None:
            raise AuthRequired("no oauth token stored; run `namr authorize`")
        self._token = Token(
            access_token=row["access_token"],
            refresh_token=row["refresh_token"],
            expires_at=int(row["expires_at"]),
            athlete_id=row["athlete_id"],
            scope=row["scope"],
        )
        return self._token

    def _ensure_fresh(self) -> str:
        tok = self._load_token()
        if tok.is_expired:
            log.info("token_expired_refreshing", extra={"expires_at": tok.expires_at})
            new = refresh_token(self.client_id, self.client_secret, tok.refresh_token)
            self._token = Token(
                access_token=new.access_token,
                refresh_token=new.refresh_token,
                expires_at=new.expires_at,
                athlete_id=tok.athlete_id,
                scope=tok.scope,
            )
            db.save_token(
                self.db_path,
                athlete_id=self._token.athlete_id,
                access_token=self._token.access_token,
                refresh_token=self._token.refresh_token,
                expires_at=self._token.expires_at,
                scope=self._token.scope,
            )
        return self._token.access_token

    def _force_refresh(self) -> str:
        tok = self._load_token()
        new = refresh_token(self.client_id, self.client_secret, tok.refresh_token)
        self._token = Token(
            access_token=new.access_token,
            refresh_token=new.refresh_token,
            expires_at=new.expires_at,
            athlete_id=tok.athlete_id,
            scope=tok.scope,
        )
        db.save_token(
            self.db_path,
            athlete_id=self._token.athlete_id,
            access_token=self._token.access_token,
            refresh_token=self._token.refresh_token,
            expires_at=self._token.expires_at,
            scope=self._token.scope,
        )
        return self._token.access_token

    # --- requests with retry ---

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        access = self._ensure_fresh()
        headers = kw.pop("headers", {}) or {}
        headers["Authorization"] = f"Bearer {access}"

        backoff = 2.0
        for attempt in range(4):
            r = self._http.request(method, path, headers=headers, **kw)
            if r.status_code == 401 and attempt == 0:
                log.info("got_401_refreshing")
                access = self._force_refresh()
                headers["Authorization"] = f"Bearer {access}"
                continue
            if r.status_code == 429:
                retry_after = float(r.headers.get("Retry-After", backoff))
                log.warning("rate_limited", extra={"retry_after": retry_after, "attempt": attempt})
                time.sleep(min(retry_after, 900))
                backoff *= 2
                continue
            if 500 <= r.status_code < 600:
                log.warning("upstream_5xx", extra={"status": r.status_code, "attempt": attempt})
                time.sleep(backoff)
                backoff *= 2
                continue
            return r
        r.raise_for_status()
        return r

    # --- endpoints ---

    def list_recent_activities(self, after_epoch: int, per_page: int = 30) -> list[dict]:
        r = self._request(
            "GET",
            "/athlete/activities",
            params={"after": after_epoch, "per_page": per_page},
        )
        r.raise_for_status()
        return r.json()

    def get_activity(self, activity_id: int) -> dict:
        r = self._request("GET", f"/activities/{activity_id}")
        r.raise_for_status()
        return r.json()

    def update_activity_name(self, activity_id: int, name: str) -> dict:
        r = self._request(
            "PUT",
            f"/activities/{activity_id}",
            json={"name": name},
        )
        r.raise_for_status()
        return r.json()

    def update_activity(
        self,
        activity_id: int,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> dict:
        payload: dict[str, str] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        r = self._request("PUT", f"/activities/{activity_id}", json=payload)
        r.raise_for_status()
        return r.json()
