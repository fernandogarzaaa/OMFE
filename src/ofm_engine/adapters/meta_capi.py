"""Meta Conversions API adapter + payload builder.

Verified against developers.facebook.com/docs/marketing-api/conversions-api (Oct 2026):
* POST https://graph.facebook.com/{version}/{pixel_id}/events  (access_token param or body field)
* body: {"data": [server_event, ...], "test_event_code"?: "TEST123"}
* server_event required: event_name, event_time (unix s), action_source, user_data;
  website events also require event_source_url and user_data.client_user_agent.
* event_id + event_name dedupe against the browser Pixel's eventID (48h window).
* user_data em/ph/fn/ln/ct/st/zp/country/db/ge/external_id SHA-256 hashed after
  normalisation; fbc, fbp, client_ip_address, client_user_agent NOT hashed.
* event_time must not be more than 7 days in the past.

COMPLIANCE: Meta prohibits adult content in ads and on ad landing pages. CAPI here is for
the *creator-recruitment funnel* and SFW link-in-bio pages only. Fan-traffic redirects to
adult destinations do not send CAPI events unless a link explicitly opts in (capi_on_click),
and the docs warn against doing so for adult destinations.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..hashing import hash_user_data
from ..settings import get_settings
from .http import HttpClient

STANDARD_EVENTS = frozenset(
    {
        "PageView",
        "ViewContent",
        "Lead",
        "CompleteRegistration",
        "Schedule",
        "Contact",
        "SubmitApplication",
        "Subscribe",
        "Purchase",
        "StartTrial",
    }
)
ACTION_SOURCES = frozenset(
    {
        "website",
        "email",
        "app",
        "phone_call",
        "chat",
        "physical_store",
        "system_generated",
        "business_messaging",
        "other",
    }
)
MAX_EVENT_AGE_SECONDS = 7 * 24 * 3600


class CapiValidationError(ValueError):
    pass


@dataclass
class CapiEvent:
    event_name: str
    event_id: str
    event_time: int = field(default_factory=lambda: int(time.time()))
    action_source: str = "website"
    event_source_url: str | None = None
    raw_user: dict[str, str | None] = field(default_factory=dict)  # em, ph, fn, ln, external_id ...
    client_ip_address: str | None = None
    client_user_agent: str | None = None
    fbc: str | None = None
    fbp: str | None = None
    custom_data: dict[str, Any] = field(default_factory=dict)
    opt_out: bool = False


def build_server_event(ev: CapiEvent, now: int | None = None) -> dict[str, Any]:
    """Validate and serialise one server event (hashing PII). Raises CapiValidationError."""
    current = int(time.time()) if now is None else now
    if not ev.event_name or len(ev.event_name) > 50:
        raise CapiValidationError("event_name required (<=50 chars)")
    if ev.action_source not in ACTION_SOURCES:
        raise CapiValidationError(f"invalid action_source {ev.action_source!r}")
    if not ev.event_id:
        raise CapiValidationError("event_id required for Pixel deduplication")
    if ev.event_time > current + 60:
        raise CapiValidationError("event_time is in the future")
    if current - ev.event_time > MAX_EVENT_AGE_SECONDS:
        raise CapiValidationError("event_time older than 7 days")
    user_data: dict[str, Any] = hash_user_data(ev.raw_user)
    if ev.client_ip_address:
        user_data["client_ip_address"] = ev.client_ip_address
    if ev.client_user_agent:
        user_data["client_user_agent"] = ev.client_user_agent
    if ev.fbc:
        user_data["fbc"] = ev.fbc
    if ev.fbp:
        user_data["fbp"] = ev.fbp
    if ev.action_source == "website":
        if not ev.event_source_url:
            raise CapiValidationError("website events require event_source_url")
        if not ev.client_user_agent:
            raise CapiValidationError("website events require client_user_agent")
    if not user_data:
        raise CapiValidationError("user_data must contain at least one identifier")
    out: dict[str, Any] = {
        "event_name": ev.event_name,
        "event_time": int(ev.event_time),
        "event_id": ev.event_id,
        "action_source": ev.action_source,
        "user_data": user_data,
    }
    if ev.event_source_url:
        out["event_source_url"] = ev.event_source_url
    if ev.custom_data:
        out["custom_data"] = ev.custom_data
    if ev.opt_out:
        out["opt_out"] = True
    return out


def build_request_body(events: list[dict[str, Any]], test_event_code: str | None = None) -> dict[str, Any]:
    if not events:
        raise CapiValidationError("at least one event required")
    if len(events) > 1000:
        raise CapiValidationError("max 1000 events per request")
    body: dict[str, Any] = {"data": events}
    if test_event_code:
        body["test_event_code"] = test_event_code
    return body


def mock_handler(request: httpx.Request) -> httpx.Response:
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        return httpx.Response(400, json={"error": {"message": "invalid json"}})
    data = body.get("data") or []
    if not data:
        return httpx.Response(400, json={"error": {"message": "data required", "code": 100}})
    trace = hashlib.sha256(request.content).hexdigest()[:12]
    return httpx.Response(
        200, json={"events_received": len(data), "messages": [], "fbtrace_id": f"mock{trace}"}
    )


@dataclass
class MetaCredentials:
    pixel_id: str
    access_token: str | None
    test_event_code: str | None = None

    @property
    def live(self) -> bool:
        return bool(self.pixel_id and self.access_token)


class MetaCapiClient:
    def __init__(self, creds: MetaCredentials, http: HttpClient | None = None) -> None:
        self.creds = creds
        self.mode = "live" if creds.live else "mock"
        self.version = get_settings().meta_graph_version
        self.http = http or HttpClient(transport=None if creds.live else httpx.MockTransport(mock_handler))

    @property
    def endpoint(self) -> str:
        return f"https://graph.facebook.com/{self.version}/{self.creds.pixel_id or 'mock_pixel'}/events"

    def send(self, events: list[CapiEvent], test_event_code: str | None = None) -> dict[str, Any]:
        payload = build_request_body(
            [build_server_event(e) for e in events], test_event_code or self.creds.test_event_code
        )
        # access_token goes in the JSON body (not the query string) so it never lands in URL logs
        payload["access_token"] = self.creds.access_token or "mock"
        resp = self.http.request("POST", self.endpoint, json=payload)
        return dict(resp.json())

    def check_auth(self) -> dict[str, Any]:
        url = f"https://graph.facebook.com/{self.version}/{self.creds.pixel_id}"
        if self.mode == "mock":
            return {"id": self.creds.pixel_id or "mock_pixel", "mode": "mock"}
        resp = self.http.request(
            "GET", url, params={"fields": "id,name", "access_token": self.creds.access_token}
        )
        return dict(resp.json())
