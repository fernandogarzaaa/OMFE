"""GoHighLevel (LeadConnector) API v2 adapter.

Verified against the official docs (marketplace.gohighlevel.com/docs, Oct 2026):
* Base URL  https://services.leadconnectorhq.com
* Headers   Authorization: Bearer <Private Integration token | OAuth sub-account token>
            Version: 2021-07-28   (also supported: "v3", released 2026-06-11 — set GHL_API_VERSION)
* POST /contacts/upsert                      (honors location "Allow Duplicate Contact" setting)
* POST /contacts/{contactId}/tags            {"tags": [...]}  (upsert `tags` would overwrite)
* POST /contacts/{contactId}/notes           {"body": "..."}
* POST /contacts/{contactId}/workflow/{workflowId}
* POST /opportunities/                       {pipelineId, locationId, name, pipelineStageId, status, contactId}
* PUT  /opportunities/{id}                   {pipelineStageId, status}
* GET  /opportunities/pipelines?locationId=
* GET  /calendars/?locationId=
* GET  /locations/{locationId}/customFields
* GET  /locations/{locationId}
* Rate limits: 100 req / 10 s burst and 200k / day per app per sub-account
  (X-RateLimit-Remaining etc.); 429s are retried with backoff by HttpClient.
* Webhooks: X-GHL-Signature (Ed25519, current) / X-WH-Signature (RSA-SHA256, legacy,
  deprecated 2026-09-01). Public keys published in the Webhook Integration Guide.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from ..settings import get_settings
from .http import HttpClient

GHL_ED25519_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAi2HR1srL4o18O8BRa7gVJY7G7bupbN3H9AwJrHCDiOg=
-----END PUBLIC KEY-----"""

GHL_RSA_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIICIjANBgkqhkiG9w0BAQEFAAOCAg8AMIICCgKCAgEAokvo/r9tVgcfZ5DysOSC
Frm602qYV0MaAiNnX9O8KxMbiyRKWeL9JpCpVpt4XHIcBOK4u3cLSqJGOLaPuXw6
dO0t6Q/ZVdAV5Phz+ZtzPL16iCGeK9po6D6JHBpbi989mmzMryUnQJezlYJ3DVfB
csedpinheNnyYeFXolrJvcsjDtfAeRx5ByHQmTnSdFUzuAnC9/GepgLT9SM4nCpv
uxmZMxrJt5Rw+VUaQ9B8JSvbMPpez4peKaJPZHBbU3OdeCVx5klVXXZQGNHOs8gF
3kvoV5rTnXV0IknLBXlcKKAQLZcY/Q9rG6Ifi9c+5vqlvHPCUJFT5XUGG5RKgOKU
J062fRtN+rLYZUV+BjafxQauvC8wSWeYja63VSUruvmNj8xkx2zE/Juc+yjLjTXp
IocmaiFeAO6fUtNjDeFVkhf5LNb59vECyrHD2SQIrhgXpO4Q3dVNA5rw576PwTzN
h/AMfHKIjE4xQA1SZuYJmNnmVZLIZBlQAF9Ntd03rfadZ+yDiOXCCs9FkHibELhC
HULgCsnuDJHcrGNd5/Ddm5hxGQ0ASitgHeMZ0kcIOwKDOzOU53lDza6/Y09T7sYJ
PQe7z0cvj7aE4B+Ax1ZoZGPzpJlZtGXCsu9aTEGEnKzmsFqwcSsnw3JB31IGKAyk
T1hhTiaCeIY/OwwwNUY2yvcCAwEAAQ==
-----END PUBLIC KEY-----"""


def _mock_id(prefix: str, *parts: str) -> str:
    return f"mock_{prefix}_" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def mock_handler(request: httpx.Request) -> httpx.Response:
    """In-process emulation of the GHL endpoints we use (deterministic, offline)."""
    path = request.url.path
    body: dict[str, Any] = json.loads(request.content or b"{}") if request.content else {}
    if not request.headers.get("Version") and not path.startswith("/hooks/"):
        return httpx.Response(400, json={"message": "Version header required"})
    if path == "/contacts/upsert" and request.method == "POST":
        key = body.get("email") or body.get("phone") or body.get("name", "")
        return httpx.Response(200, json={"new": True, "contact": {"id": _mock_id("c", body.get("locationId", ""), key),
                                                                 "locationId": body.get("locationId")}})
    if path.endswith("/tags") and request.method == "POST":
        return httpx.Response(201, json={"tags": body.get("tags", [])})
    if path.endswith("/notes") and request.method == "POST":
        return httpx.Response(201, json={"note": {"id": _mock_id("n", path, body.get("body", ""))}})
    if "/workflow/" in path and request.method == "POST":
        return httpx.Response(200, json={"succeded": True})
    if path in ("/opportunities/", "/opportunities") and request.method == "POST":
        return httpx.Response(201, json={"opportunity": {"id": _mock_id("o", body.get("contactId", "")),
                                                         "pipelineStageId": body.get("pipelineStageId")}})
    if path.startswith("/opportunities/") and request.method == "PUT":
        return httpx.Response(200, json={"opportunity": {"id": path.rsplit("/", 1)[-1], **body}})
    if path == "/opportunities/pipelines":
        return httpx.Response(200, json={"pipelines": [{
            "id": "mock_pipeline", "name": "Creator Recruitment",
            "stages": [{"id": f"mock_stage_{s}", "name": s.title()} for s in
                       ("new", "qualified", "booked", "signed", "lost")]}]})
    if path in ("/calendars/", "/calendars"):
        return httpx.Response(200, json={"calendars": [{"id": "mock_calendar", "name": "Creator Intro Call"}]})
    if path.endswith("/customFields"):
        return httpx.Response(200, json={"customFields": [{"id": "mock_cf_score", "fieldKey": "contact.lead_score"}]})
    if path.startswith("/locations/"):
        return httpx.Response(200, json={"location": {"id": path.rsplit("/", 1)[-1], "name": "Mock Location"}})
    if path.startswith("/hooks/"):
        return httpx.Response(200, json={"status": "Success: request sent to trigger execution server"})
    return httpx.Response(404, json={"message": f"mock route not found: {request.method} {path}"})


@dataclass
class GHLCredentials:
    token: str | None
    location_id: str

    @property
    def live(self) -> bool:
        return bool(self.token and self.location_id)


class GHLClient:
    def __init__(self, creds: GHLCredentials, http: HttpClient | None = None) -> None:
        s = get_settings()
        self.creds = creds
        self.mode = "live" if creds.live else "mock"
        self.base = s.ghl_api_base.rstrip("/")
        self.version = s.ghl_api_version
        self.location_id = creds.location_id or "mock_location"
        self.http = http or HttpClient(transport=None if creds.live else httpx.MockTransport(mock_handler))

    def _headers(self) -> dict[str, str]:
        token = self.creds.token or "mock-token"
        return {"Authorization": f"Bearer {token}", "Version": self.version, "Accept": "application/json"}

    def _req(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        resp = self.http.request(method, f"{self.base}{path}", headers=self._headers(), **kw)
        data = resp.json() if resp.content else {}
        return data if isinstance(data, dict) else {"data": data}

    # --- contacts ---------------------------------------------------------------
    def upsert_contact(self, contact: dict[str, Any]) -> tuple[str, bool]:
        payload = {k: v for k, v in contact.items() if v not in (None, "", [], {})}
        payload["locationId"] = self.location_id
        data = self._req("POST", "/contacts/upsert", json=payload)
        return str(data["contact"]["id"]), bool(data.get("new", False))

    def add_tags(self, contact_id: str, tags: list[str]) -> None:
        if tags:
            self._req("POST", f"/contacts/{contact_id}/tags", json={"tags": sorted(set(tags))})

    def add_note(self, contact_id: str, body: str) -> str:
        data = self._req("POST", f"/contacts/{contact_id}/notes", json={"body": body[:5000]})
        return str(data.get("note", {}).get("id", ""))

    def add_to_workflow(self, contact_id: str, workflow_id: str) -> None:
        self._req("POST", f"/contacts/{contact_id}/workflow/{workflow_id}", json={})

    # --- opportunities --------------------------------------------------------------
    def create_opportunity(self, *, contact_id: str, pipeline_id: str, stage_id: str, name: str,
                           monetary_value: float | None = None) -> str:
        payload: dict[str, Any] = {
            "pipelineId": pipeline_id, "locationId": self.location_id, "name": name,
            "pipelineStageId": stage_id, "status": "open", "contactId": contact_id,
        }
        if monetary_value is not None:
            payload["monetaryValue"] = monetary_value
        data = self._req("POST", "/opportunities/", json=payload)
        return str(data["opportunity"]["id"])

    def update_opportunity_stage(self, opportunity_id: str, stage_id: str, status: str = "open") -> None:
        self._req("PUT", f"/opportunities/{opportunity_id}", json={"pipelineStageId": stage_id, "status": status})

    def list_pipelines(self) -> list[dict[str, Any]]:
        return list(self._req("GET", "/opportunities/pipelines", params={"locationId": self.location_id})
                    .get("pipelines", []))

    def list_calendars(self) -> list[dict[str, Any]]:
        return list(self._req("GET", "/calendars/", params={"locationId": self.location_id}).get("calendars", []))

    def list_custom_fields(self) -> list[dict[str, Any]]:
        return list(self._req("GET", f"/locations/{self.location_id}/customFields").get("customFields", []))

    def get_location(self) -> dict[str, Any]:
        return self._req("GET", f"/locations/{self.location_id}")

    def trigger_inbound_webhook(self, url: str | None, payload: dict[str, Any]) -> bool:
        """POST to a GHL workflow 'Inbound Webhook' trigger URL (services.leadconnectorhq.com/hooks/...)."""
        target = url if (url and self.mode == "live") else f"{self.base}/hooks/mock/webhook-trigger/mock"
        self.http.request("POST", target, json=payload, headers={"Content-Type": "application/json"})
        return True


# --- inbound webhook signature verification -------------------------------------
def _load_key(pem: str) -> Any:
    return serialization.load_pem_public_key(pem.encode())


def verify_ghl_signature(body: bytes, headers: dict[str, str], ed25519_pem: str | None = None) -> bool:
    """Prefer X-GHL-Signature (Ed25519); fall back to legacy X-WH-Signature (RSA-SHA256)."""
    lower = {k.lower(): v for k, v in headers.items()}
    ghl_sig = lower.get("x-ghl-signature")
    legacy_sig = lower.get("x-wh-signature")
    try:
        if ghl_sig:
            key = _load_key(ed25519_pem or get_settings().ghl_webhook_public_key or GHL_ED25519_PUBLIC_KEY)
            if not isinstance(key, Ed25519PublicKey):
                return False
            key.verify(base64.b64decode(ghl_sig), body)
            return True
        if legacy_sig:
            rsa_key = _load_key(GHL_RSA_PUBLIC_KEY)
            if not isinstance(rsa_key, RSAPublicKey):  # pragma: no cover
                return False
            rsa_key.verify(base64.b64decode(legacy_sig), body, padding.PKCS1v15(), hashes.SHA256())
            return True
    except (InvalidSignature, ValueError, TypeError):
        return False
    return False
