import json
import time

import httpx
import pytest
import respx

from ofm_engine import attribution as attr
from ofm_engine import hashing, scoring
from ofm_engine.adapters import meta_capi, tiktok
from ofm_engine.adapters.http import HttpClient, IntegrationError, backoff_delay, retry_after_seconds
from ofm_engine.adapters.llm import LLMClient, LLMConfig, extract_json


# --------------------------------------------------------------------------- hashing / CAPI
def test_capi_normalisation_rules():
    assert hashing.hash_field("em", "  John.Doe@Example.COM ") == hashing.sha256("john.doe@example.com")
    assert hashing.normalize_phone("+1 (555) 010-0200") == "15550100200"
    assert hashing.normalize_phone("555-010-0200", default_country_code="1") == "15550100200"
    assert hashing.normalize_name(" O'Brien ") == "obrien"
    assert hashing.normalize_state("California") == "ca"
    assert hashing.normalize_zip("94105-1234", "us") == "94105"
    assert hashing.normalize_country("US") == "us"
    assert hashing.normalize_dob("1990-01-31") == "19900131" and hashing.normalize_dob("1990") == ""
    assert hashing.normalize_gender("Female") == "f" and hashing.normalize_gender("x") == ""
    h = hashing.sha256("already")
    assert hashing.hash_field("em", h.upper()) == h  # pre-hashed passthrough
    assert hashing.hash_field("em", None) is None and hashing.hash_field("db", "nope") is None
    with pytest.raises(ValueError):
        hashing.hash_field("fbc", "x")
    assert hashing.hash_user_data({"em": "a@b.co", "fbc": "keep-out", "ph": ""}) == {"em": [hashing.sha256("a@b.co")]}
    assert len(hashing.keyed_hash("1.2.3.4", "salt")) == 64


def _ev(**kw):
    base = dict(event_name="Lead", event_id="e1", event_source_url="https://x.example/apply/demo",
                raw_user={"em": "A@B.co", "ph": "+1 555 0100", "external_id": "L1"}, client_user_agent="UA",
                client_ip_address="1.2.3.4", fbc="fb.1.1700000000000.abc", fbp="fb.1.1700000000000.123")
    base.update(kw)
    return meta_capi.CapiEvent(**base)


def test_capi_payload_shape():
    now = int(time.time())
    out = meta_capi.build_server_event(_ev(event_time=now, custom_data={"tier": "hot"}), now=now)
    assert out["event_name"] == "Lead" and out["event_id"] == "e1" and out["action_source"] == "website"
    ud = out["user_data"]
    assert ud["em"] == [hashing.sha256("a@b.co")] and ud["ph"] == [hashing.sha256("15550100")]
    assert ud["fbc"] == "fb.1.1700000000000.abc" and ud["client_ip_address"] == "1.2.3.4"  # not hashed
    assert out["custom_data"] == {"tier": "hot"}
    body = meta_capi.build_request_body([out], "TEST1")
    assert body == {"data": [out], "test_event_code": "TEST1"}


@pytest.mark.parametrize("kw,msg", [
    ({"event_name": ""}, "event_name"), ({"action_source": "fax"}, "action_source"), ({"event_id": ""}, "event_id"),
    ({"event_time": int(time.time()) + 3600}, "future"), ({"event_time": int(time.time()) - 8 * 86400}, "7 days"),
    ({"event_source_url": None}, "event_source_url"), ({"client_user_agent": None}, "client_user_agent"),
])
def test_capi_validation(kw, msg):
    with pytest.raises(meta_capi.CapiValidationError, match=msg):
        meta_capi.build_server_event(_ev(**kw))


def test_capi_no_identifiers_and_batch_limits():
    with pytest.raises(meta_capi.CapiValidationError):
        meta_capi.build_server_event(_ev(action_source="system_generated", raw_user={}, client_user_agent=None,
                                         client_ip_address=None, fbc=None, fbp=None))
    with pytest.raises(meta_capi.CapiValidationError):
        meta_capi.build_request_body([])
    with pytest.raises(meta_capi.CapiValidationError):
        meta_capi.build_request_body([{}] * 1001)


def test_meta_client_mock_and_live():
    mock = meta_capi.MetaCapiClient(meta_capi.MetaCredentials(pixel_id="", access_token=None))
    assert mock.mode == "mock" and mock.send([_ev()])["events_received"] == 1
    assert mock.check_auth()["mode"] == "mock"
    live = meta_capi.MetaCapiClient(meta_capi.MetaCredentials(pixel_id="PX1", access_token="EAAtoken",
                                                              test_event_code="T9"),
                                    http=HttpClient(max_retries=0))
    with respx.mock() as rx:
        route = rx.post("https://graph.facebook.com/v25.0/PX1/events").mock(
            return_value=httpx.Response(200, json={"events_received": 1}))
        live.send([_ev()])
        body = json.loads(route.calls[0].request.content)
    assert body["access_token"] == "EAAtoken" and body["test_event_code"] == "T9"
    assert "access_token" not in str(route.calls[0].request.url)


def test_tiktok_build_and_mock():
    ev = tiktok.TikTokEvent(event="Lead", event_id="e", email="A@b.co", phone="+1 555", ttclid="tt", url="https://x")
    out = tiktok.build_event(ev)
    assert out["event"] == "SubmitForm" and out["user"]["email"] == hashing.sha256("a@b.co")
    assert out["user"]["ttclid"] == "tt" and out["page"] == {"url": "https://x"}
    c = tiktok.TikTokClient(tiktok.TikTokCredentials(pixel_code="", access_token=None))
    assert c.send([ev])["code"] == 0


# --------------------------------------------------------------------------- http client
def test_http_retries_then_succeeds():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "0"}) if calls["n"] < 3 else httpx.Response(200, json={})

    sleeps = []
    c = HttpClient(transport=httpx.MockTransport(handler), sleep=sleeps.append, max_retries=4)
    assert c.request("GET", "https://x.example/", idempotency_key="k").status_code == 200
    assert calls["n"] == 3 and sleeps == [0.0, 0.0]


def test_http_errors():
    c = HttpClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, text="nope")), max_retries=0)
    with pytest.raises(IntegrationError) as ei:
        c.request("GET", "https://x.example/")
    assert ei.value.status_code == 404 and not ei.value.retryable

    def boom(r):
        raise httpx.ConnectError("down")

    c2 = HttpClient(transport=httpx.MockTransport(boom), sleep=lambda s: None, max_retries=1)
    with pytest.raises(IntegrationError) as ei:
        c2.request("GET", "https://x.example/")
    assert ei.value.retryable
    guarded = HttpClient(max_retries=0)
    from ofm_engine.security.ssrf import UnsafeURLError

    with pytest.raises(UnsafeURLError):
        guarded.request("GET", "https://evil.example/")
    assert backoff_delay(10) <= 25 and retry_after_seconds(httpx.Response(429, headers={"Retry-After": "x"})) is None
    assert retry_after_seconds(httpx.Response(429, headers={"Retry-After": "999"})) == 30.0


def test_llm_mock_and_openai():
    m = LLMClient(LLMConfig("mock", None, "mock"))
    assert m.complete("s", "p").startswith("MOCK::") and m.complete("s", "p") == m.complete("s", "p")
    o = LLMClient(LLMConfig("openai", None, "gpt"))
    assert o.mode == "mock" and o.complete("s", "p").startswith("MOCK::")
    assert extract_json('noise {"a": 1} tail') == {"a": 1} and extract_json("none") is None
    assert extract_json("{bad json}") is None


def test_ghl_mock_and_signature():
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from ofm_engine.adapters.ghl import GHLClient, GHLCredentials, verify_ghl_signature

    g = GHLClient(GHLCredentials(token=None, location_id=""))
    cid, new = g.upsert_contact({"email": "a@b.co", "name": "A"})
    assert cid.startswith("mock_c_") and new
    assert g.list_pipelines()[0]["id"] == "mock_pipeline" and g.list_calendars() and g.list_custom_fields()
    assert g.add_note(cid, "hi") and g.trigger_inbound_webhook(None, {"a": 1})
    g.add_to_workflow(cid, "wf")
    g.update_opportunity_stage("o1", "s1")
    key = Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    body = b'{"type":"ContactCreate"}'
    sig = base64.b64encode(key.sign(body)).decode()
    assert verify_ghl_signature(body, {"X-GHL-Signature": sig}, ed25519_pem=pem)
    assert not verify_ghl_signature(body + b"x", {"X-GHL-Signature": sig}, ed25519_pem=pem)
    assert not verify_ghl_signature(body, {"X-WH-Signature": "bm90LWEtc2ln"})
    assert not verify_ghl_signature(body, {})


# --------------------------------------------------------------------------- scoring
def test_scoring_breakdown_and_tiers():
    r = scoring.score_lead(followers={"instagram": 250_000, "tiktok": 400_000}, earnings_range="10k-25k",
                           has_phone=True, socials={"instagram": "@x"}, timezone="America/New_York",
                           target_timezones=["America/"])
    assert r.tier == "hot" and r.score >= 70
    assert set(r.breakdown) >= {"audience", "multi_platform", "earnings", "contactable", "socials", "timezone"}
    cold = scoring.score_lead(followers={}, earnings_range="none", has_phone=False, socials={})
    assert cold.score == 0 and cold.tier == "cold"
    adj = scoring.score_lead(followers={"ig": 1000}, earnings_range="1k-5k", has_phone=False, socials={},
                             ai_adjustment=50)
    assert adj.breakdown["ai_adjustment"] == 10
    assert scoring.tier_for(45) == "warm" and scoring.tier_for(45, {"hot": 40, "warm": 20}) == "hot"
    capped = scoring.follower_points(10**12, 35, 1_000_000)
    assert capped == 35
    custom = scoring.score_lead(followers={}, earnings_range="none", has_phone=True, socials={},
                                weights={"has_phone": 50})
    assert custom.score == 50


# --------------------------------------------------------------------------- attribution
def test_attribution_extraction():
    ctx = attr.extract_click_context(
        {"utm_source": "ig\x00", "utm_campaign": "c" * 500, "fbclid": "FB1", "ttclid": "TT"},
        {"_fbc": "fb.1.1600000000000.old", "_fbp": "fb.1.1600000000000.42"},
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/120 Mobile Safari", "https://www.tiktok.com/@x",
        now_ms=1_700_000_000_000)
    assert ctx.utm_source == "ig" and len(ctx.utm_campaign) == 120
    assert ctx.fbc == "fb.1.1700000000000.FB1"  # fresh fbclid beats cookie
    assert ctx.fbp == "fb.1.1600000000000.42" and ctx.ttclid == "TT"
    assert (ctx.device, ctx.os, ctx.browser, ctx.is_bot) == ("mobile", "android", "chrome", False)
    assert ctx.referrer_host == "www.tiktok.com"
    ctx2 = attr.extract_click_context({}, {"_fbc": "fb.1.1600000000000.old", "_fbp": "junk"}, "", None)
    assert ctx2.fbc == "fb.1.1600000000000.old" and ctx2.fbp == "" and ctx2.is_bot


@pytest.mark.parametrize("ua,expected", [
    ("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Safari/604.1", ("tablet", "ios", "safari")),
    ("Mozilla/5.0 (Windows NT 10.0; Win64) Edg/120", ("desktop", "windows", "edge")),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14) Firefox/121", ("desktop", "macos", "firefox")),
    ("Mozilla/5.0 (X11; Linux x86_64) OPR/100", ("desktop", "linux", "opera")),
    ("Mozilla/5.0 (iPhone) FBAN/FBIOS FBAV/400", ("mobile", "ios", "facebook")),
    ("Mozilla/5.0 (iPhone) musical_ly_30", ("mobile", "ios", "tiktok")),
    ("SomethingElse/1.0", ("desktop", "other", "other")),
])
def test_user_agent_parsing(ua, expected):
    assert attr.parse_user_agent(ua)[:3] == expected


def test_url_helpers():
    assert attr.append_params("https://x.com/p?a=1#f", {"a": "2", "b": "3", "c": ""}) == "https://x.com/p?a=1&b=3#f"
    assert attr.safe_destination("https://x.com") and not attr.safe_destination("javascript:alert(1)")
    assert not attr.safe_destination("//x.com") and not attr.safe_destination("http://[bad")
    assert attr.referrer_host("not a url") == "" and attr.referrer_host(None) == ""
    assert attr.build_fbc("") == "" and attr.valid_fbp(attr.build_fbp())
    assert attr.clean_param(None) == ""


def test_tenant_config_layers(tmp_path):
    from ofm_engine.tenant_config import build_config, env_var_name, load_yaml

    (tmp_path / "acme.yaml").write_text("slug: acme\nname: Acme\nbrand:\n  primary_color: '#112233'\n")
    cfg = build_config("acme", "Acme", tmp_path, {"brand": {"name": "Acme UI"}, "creators": [{"slug": "evil"}]})
    assert cfg.brand.primary_color == "#112233" and cfg.brand.name == "Acme UI" and cfg.creators == []
    assert load_yaml("missing", tmp_path) == {"slug": "missing", "name": "missing"}
    with pytest.raises(ValueError):
        load_yaml("../etc", tmp_path)
    assert env_var_name("demo-agency", "GHL_TOKEN") == "CLIENT_DEMO_AGENCY_GHL_TOKEN"
    with pytest.raises(ValueError):
        build_config("acme", "Acme", tmp_path, {"brand": {"primary_color": "red"}})
