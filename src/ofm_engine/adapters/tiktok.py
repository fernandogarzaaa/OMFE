"""TikTok Events API (v1.3) adapter — optional, mock by default.

POST {TIKTOK_API_BASE}/event/track/  header Access-Token
body: {"event_source": "web", "event_source_id": <pixel_code>, "test_event_code"?,
       "data": [{"event", "event_time", "event_id", "user": {email, phone, ttclid, ip, user_agent},
                 "page": {"url", "referrer"}, "properties": {...}}]}
email/phone are SHA-256 hashed after normalisation (same rules as Meta).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..hashing import hash_field
from ..settings import get_settings
from .http import HttpClient

EVENT_MAP = {
    "Lead": "SubmitForm",
    "CompleteRegistration": "CompleteRegistration",
    "Schedule": "Contact",
    "PageView": "ViewContent",
    "Subscribe": "Subscribe",
    "Purchase": "CompletePayment",
}


@dataclass
class TikTokEvent:
    event: str
    event_id: str
    event_time: int = field(default_factory=lambda: int(time.time()))
    email: str | None = None
    phone: str | None = None
    ttclid: str | None = None
    ip: str | None = None
    user_agent: str | None = None
    url: str | None = None
    referrer: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


def build_event(ev: TikTokEvent) -> dict[str, Any]:
    user: dict[str, Any] = {}
    if ev.email and (h := hash_field("em", ev.email)):
        user["email"] = h
    if ev.phone and (h := hash_field("ph", ev.phone)):
        user["phone"] = h
    for key, val in (("ttclid", ev.ttclid), ("ip", ev.ip), ("user_agent", ev.user_agent)):
        if val:
            user[key] = val
    out: dict[str, Any] = {
        "event": EVENT_MAP.get(ev.event, ev.event),
        "event_time": int(ev.event_time),
        "event_id": ev.event_id,
        "user": user,
    }
    page = {k: v for k, v in (("url", ev.url), ("referrer", ev.referrer)) if v}
    if page:
        out["page"] = page
    if ev.properties:
        out["properties"] = ev.properties
    return out


def mock_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content or b"{}")
    if not request.headers.get("Access-Token"):
        return httpx.Response(200, json={"code": 40001, "message": "Access token required"})
    return httpx.Response(
        200, json={"code": 0, "message": "OK", "data": {"received": len(body.get("data", []))}}
    )


@dataclass
class TikTokCredentials:
    pixel_code: str
    access_token: str | None
    test_event_code: str | None = None

    @property
    def live(self) -> bool:
        return bool(self.pixel_code and self.access_token)


class TikTokClient:
    def __init__(self, creds: TikTokCredentials, http: HttpClient | None = None) -> None:
        self.creds = creds
        self.mode = "live" if creds.live else "mock"
        self.base = get_settings().tiktok_api_base.rstrip("/")
        self.http = http or HttpClient(transport=None if creds.live else httpx.MockTransport(mock_handler))

    def send(self, events: list[TikTokEvent]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "event_source": "web",
            "event_source_id": self.creds.pixel_code or "mock_pixel",
            "data": [build_event(e) for e in events],
        }
        if self.creds.test_event_code:
            body["test_event_code"] = self.creds.test_event_code
        resp = self.http.request(
            "POST",
            f"{self.base}/event/track/",
            json=body,
            headers={"Access-Token": self.creds.access_token or "mock"},
        )
        data = dict(resp.json())
        if data.get("code") not in (0, None):
            from .http import IntegrationError

            raise IntegrationError(f"TikTok error {data.get('code')}: {data.get('message')}", status_code=400)
        return data
