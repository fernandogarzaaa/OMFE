"""Exact-value tests written to kill surviving testteeth mutants in the domain modules
(hashing, Meta CAPI payload builder, scoring, attribution). See quality/mutation/RESULTS.md."""

from __future__ import annotations

import hashlib
import hmac
import math

import pytest

from ofm_engine import attribution as attr
from ofm_engine import hashing, scoring
from ofm_engine.adapters import meta_capi


# --------------------------------------------------------------------------- hashing
def test_sha256_and_is_hashed_exact():
    assert hashing.sha256("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert hashing.sha256("é") == hashlib.sha256("é".encode()).hexdigest()
    h = hashing.sha256("x")
    assert hashing.is_hashed(h) is True
    assert hashing.is_hashed(h.upper()) is False  # caller lower-cases first
    assert hashing.is_hashed(h[:-1]) is False and hashing.is_hashed(h + "0") is False
    assert hashing.is_hashed("g" * 64) is False


@pytest.mark.parametrize(
    "raw,cc,expected",
    [
        ("+1 (555) 010-0200", "", "15550100200"),
        ("555-010-0200", "1", "15550100200"),  # 10 digits -> prefixed
        ("0555-010-0200", "1", "15550100200"),  # leading zero stripped first
        ("15550100200", "1", "15550100200"),  # already starts with cc
        ("+44 20 7946 0958", "1", "442079460958"),  # explicit + keeps its own cc
        ("5550100200", "", "5550100200"),  # no default cc
        ("+63 912 345", "1", "63912345"),  # short number with explicit + is never prefixed
        ("44 20 7946 09581", "1", "442079460958" + "1"),  # > 10 digits: not prefixed
        ("phone: n/a", "1", ""),
        ("", "1", ""),
    ],
)
def test_normalize_phone_table(raw, cc, expected):
    assert hashing.normalize_phone(raw, default_country_code=cc) == expected


def test_normalize_phone_ten_digit_boundary():
    assert hashing.normalize_phone("2345678901", "63") == "632345678901"  # exactly 10
    assert hashing.normalize_phone("23456789012", "63") == "23456789012"  # 11: left alone


def test_text_normalisers_exact():
    assert hashing.normalize_email("  John.Doe@Example.COM ") == "john.doe@example.com"
    assert hashing.normalize_name("  Mary-Jane O'Neil ") == "maryjaneoneil"
    assert hashing.normalize_name("JOSÉ") == "josé"  # UTF-8 kept, NFC
    assert hashing.normalize_name("Jose\u0301") == "jos\u00e9"
    assert hashing.normalize_city("San Francisco") == "sanfrancisco"
    assert hashing.normalize_state(" N.Y. ") == "ny"
    assert hashing.normalize_state("Texas") == "te"
    assert hashing.normalize_zip(" SW1A 1AA ", "gb") == "sw1a1aa"
    assert hashing.normalize_zip("94105-1234", "US") == "94105"
    assert hashing.normalize_zip("94105-1234") == "941051234"
    assert hashing.normalize_country("United States") == "un"
    assert hashing.normalize_country(" PH ") == "ph"
    assert hashing.normalize_dob("31/01/1990") == "31011990"
    assert hashing.normalize_dob("1990-1-31") == ""  # 7 digits
    assert hashing.normalize_dob("1990-01-311") == ""  # 9 digits
    assert hashing.normalize_gender(" Male ") == "m" and hashing.normalize_gender("F") == "f"
    assert hashing.normalize_gender("") == ""


def test_hash_field_per_field_and_passthrough():
    assert hashing.hash_field("ph", "+1 555 0100") == hashing.sha256("15550100")
    assert hashing.hash_field("fn", " O'Brien ") == hashing.sha256("obrien")
    assert hashing.hash_field("ge", "female") == hashing.sha256("f")
    assert hashing.hash_field("external_id", "  L1 ") == hashing.sha256("L1")  # trimmed, case kept
    h = hashing.sha256("pre")
    assert hashing.hash_field("ph", f"  {h.upper()} ") == h
    assert hashing.hash_field("em", "   ") is None
    assert hashing.hash_field("ge", "other") is None
    with pytest.raises(ValueError, match="fbp"):
        hashing.hash_field("fbp", "x")
    assert hashing.hash_field("fbp", None) is None  # None short-circuits before field check
    fields = {"em", "ph", "fn", "ln", "ct", "st", "zp", "country", "db", "ge", "external_id"}
    assert fields == hashing.HASHED_FIELDS


def test_hash_user_data_array_form():
    out = hashing.hash_user_data(
        {"em": "A@B.co", "ln": "Smith", "fbc": "x", "client_user_agent": "UA", "ge": "?", "zp": None}
    )
    assert out == {"em": [hashing.sha256("a@b.co")], "ln": [hashing.sha256("smith")]}
    assert hashing.hash_user_data({}) == {}


def test_keyed_hash_is_hmac():
    assert hashing.keyed_hash("1.2.3.4", "salt") == hmac.new(b"salt", b"1.2.3.4", hashlib.sha256).hexdigest()
    assert hashing.keyed_hash("1.2.3.4", "salt") != hashing.keyed_hash("1.2.3.4", "pepper")


# --------------------------------------------------------------------------- meta_capi
NOW = 1_800_000_000


def _ev(**kw):
    base = dict(
        event_name="Lead",
        event_id="e1",
        event_time=NOW,
        event_source_url="https://x.example/apply/demo",
        raw_user={"em": "A@B.co"},
        client_user_agent="UA",
    )
    base.update(kw)
    return meta_capi.CapiEvent(**base)


def test_server_event_exact_minimal_shape():
    out = meta_capi.build_server_event(_ev(), now=NOW)
    assert out == {
        "event_name": "Lead",
        "event_time": NOW,
        "event_id": "e1",
        "action_source": "website",
        "user_data": {"em": [hashing.sha256("a@b.co")], "client_user_agent": "UA"},
        "event_source_url": "https://x.example/apply/demo",
    }


def test_server_event_optional_fields():
    out = meta_capi.build_server_event(
        _ev(
            client_ip_address="1.2.3.4",
            fbc="fb.1.1.a",
            fbp="fb.1.1.2",
            custom_data={"v": 1},
            opt_out=True,
        ),
        now=NOW,
    )
    ud = out["user_data"]
    assert ud["client_ip_address"] == "1.2.3.4" and ud["fbc"] == "fb.1.1.a" and ud["fbp"] == "fb.1.1.2"
    assert out["custom_data"] == {"v": 1} and out["opt_out"] is True


def test_server_event_non_website_needs_no_url_or_ua():
    out = meta_capi.build_server_event(
        _ev(action_source="system_generated", event_source_url=None, client_user_agent=None), now=NOW
    )
    assert out["action_source"] == "system_generated"
    assert "event_source_url" not in out and out["user_data"] == {"em": [hashing.sha256("a@b.co")]}
    assert "custom_data" not in out and "opt_out" not in out


def test_server_event_identifier_only_from_unhashed_fields():
    out = meta_capi.build_server_event(_ev(raw_user={}, fbp="fb.1.1.2"), now=NOW)
    assert out["user_data"] == {"client_user_agent": "UA", "fbp": "fb.1.1.2"}
    with pytest.raises(meta_capi.CapiValidationError, match="identifier"):
        meta_capi.build_server_event(_ev(raw_user={}, action_source="other", client_user_agent=None), now=NOW)


def test_server_event_time_boundaries():
    assert meta_capi.build_server_event(_ev(event_time=NOW + 60), now=NOW)["event_time"] == NOW + 60
    with pytest.raises(meta_capi.CapiValidationError, match="future"):
        meta_capi.build_server_event(_ev(event_time=NOW + 61), now=NOW)
    age = meta_capi.MAX_EVENT_AGE_SECONDS
    assert age == 604_800
    assert meta_capi.build_server_event(_ev(event_time=NOW - age), now=NOW)["event_time"] == NOW - age
    with pytest.raises(meta_capi.CapiValidationError, match="7 days"):
        meta_capi.build_server_event(_ev(event_time=NOW - age - 1), now=NOW)


def test_server_event_name_length_boundary():
    assert meta_capi.build_server_event(_ev(event_name="E" * 50), now=NOW)["event_name"] == "E" * 50
    for bad in ("", "E" * 51):
        with pytest.raises(meta_capi.CapiValidationError, match="event_name"):
            meta_capi.build_server_event(_ev(event_name=bad), now=NOW)


@pytest.mark.parametrize(
    "kw,msg",
    [
        ({"action_source": "fax"}, "action_source"),
        ({"event_id": ""}, "event_id"),
        ({"event_source_url": None}, "event_source_url"),
        ({"client_user_agent": None}, "client_user_agent"),
    ],
)
def test_server_event_validation_messages(kw, msg):
    with pytest.raises(meta_capi.CapiValidationError, match=msg):
        meta_capi.build_server_event(_ev(**kw), now=NOW)


def test_request_body_bounds_and_test_code():
    assert meta_capi.build_request_body([{"a": 1}]) == {"data": [{"a": 1}]}
    assert meta_capi.build_request_body([{}], "T1") == {"data": [{}], "test_event_code": "T1"}
    assert len(meta_capi.build_request_body([{}] * 1000)["data"]) == 1000
    with pytest.raises(meta_capi.CapiValidationError, match="1000"):
        meta_capi.build_request_body([{}] * 1001)
    with pytest.raises(meta_capi.CapiValidationError, match="at least one"):
        meta_capi.build_request_body([])


def test_constants():
    assert "Lead" in meta_capi.STANDARD_EVENTS and "website" in meta_capi.ACTION_SOURCES


# --------------------------------------------------------------------------- scoring
def test_follower_points_exact():
    assert scoring.follower_points(0, 35, 1_000_000) == 0.0
    assert scoring.follower_points(-5, 35, 1_000_000) == 0.0
    assert scoring.follower_points(1_000_000, 35, 1_000_000) == 35
    assert scoring.follower_points(2_000_000, 35, 1_000_000) == 35
    expected = round(35 * math.log10(1001) / math.log10(1_000_001), 2)
    assert scoring.follower_points(1000, 35, 1_000_000) == expected == 17.5
    assert scoring.follower_points(1, 10, 100) == round(10 * math.log10(2) / math.log10(101), 2)


def test_tier_boundaries():
    assert scoring.tier_for(70) == "hot" and scoring.tier_for(69) == "warm"
    assert scoring.tier_for(40) == "warm" and scoring.tier_for(39) == "cold"
    assert scoring.tier_for(5, {"hot": 10, "warm": 5}) == "warm"
    assert scoring.tier_for(4, {"hot": 10, "warm": 5}) == "cold"
    assert scoring.tier_for(5, {}) == "cold"  # empty dict falls back to defaults


def _score(**kw):
    base = dict(followers={}, earnings_range="none", has_phone=False, socials={})
    base.update(kw)
    return scoring.score_lead(**base)


def test_score_breakdown_exact():
    r = _score(
        followers={"ig": 1_000_000, "tt": 1000, "x": 999, "yt": 2000, "fb": 5000},
        earnings_range="10k-25k",
        has_phone=True,
        socials={"ig": "@x", "tt": ""},
        timezone="America/New_York",
        target_timezones=["Europe/", "America/"],
    )
    # 4 platforms >= 1k -> 3 extra -> 12 points (capped at 3 * 4)
    assert r.breakdown == {
        "audience": 35.0,
        "multi_platform": 12.0,
        "earnings": 32.0,
        "contactable": 5.0,
        "socials": 5.0,
        "timezone": 5.0,
    }
    assert r.score == 94 and r.tier == "hot"


def test_multi_platform_counts_and_cap():
    assert _score(followers={"a": 1000}).breakdown["multi_platform"] == 0.0
    assert _score(followers={"a": 1000, "b": 1000}).breakdown["multi_platform"] == 4.0
    assert _score(followers={"a": 1000, "b": 999}).breakdown["multi_platform"] == 0.0
    six = {k: 5000 for k in "abcdef"}
    assert _score(followers=six).breakdown["multi_platform"] == 12.0
    assert (
        _score(followers={"a": 1000, "b": 1000}, weights={"multi_platform": 7}).breakdown["multi_platform"]
        == 7.0
    )


def test_negative_and_none_followers_clamped():
    r = _score(followers={"a": -500, "b": None, "c": 10})
    assert r.breakdown["audience"] == scoring.follower_points(10, 35, 1_000_000)


EARNINGS_SPEC = {"none": 0, "0-1k": 5, "1k-5k": 15, "5k-10k": 25, "10k-25k": 32, "25k+": 35}


def test_scoring_tables_match_spec():
    # documented contract (README / GHL note): pinned literally so a silent re-weighting is caught
    assert scoring.EARNINGS_POINTS == EARNINGS_SPEC
    assert scoring.DEFAULT_WEIGHTS == {
        "followers_max": 35,
        "followers_cap": 1_000_000,
        "multi_platform": 4,
        "has_phone": 5,
        "has_socials": 5,
        "timezone_match": 5,
    }
    assert scoring.DEFAULT_TIERS == {"hot": 70, "warm": 40}


@pytest.mark.parametrize("rng,pts", [*EARNINGS_SPEC.items(), ("bogus", 0)])
def test_earnings_points(rng, pts):
    assert _score(earnings_range=rng).breakdown["earnings"] == float(pts)


def test_timezone_match_rules():
    assert _score(timezone="Asia/Manila", target_timezones=["Asia/"]).breakdown["timezone"] == 5.0
    assert _score(timezone="Asia/Manila", target_timezones=["Europe/"]).breakdown["timezone"] == 0.0
    assert _score(timezone="", target_timezones=["Asia/"]).breakdown["timezone"] == 0.0
    assert _score(timezone="Asia/Manila", target_timezones=[]).breakdown["timezone"] == 0.0
    assert _score(timezone="Asia/Manila").breakdown["timezone"] == 0.0
    custom = _score(timezone="Asia/Manila", target_timezones=["Asia/"], weights={"timezone_match": 9})
    assert custom.score == 9


def test_socials_and_phone_weights():
    assert _score(socials={"ig": ""}).breakdown["socials"] == 0.0
    assert _score(socials={"ig": "@a"}).breakdown["socials"] == 5.0
    assert _score(socials={"ig": "@a"}, weights={"has_socials": 8}).score == 8
    assert _score(has_phone=True).breakdown["contactable"] == 5.0


def test_ai_adjustment_clamp_and_omission():
    assert "ai_adjustment" not in _score().breakdown
    assert _score(ai_adjustment=3).breakdown["ai_adjustment"] == 3.0
    assert _score(earnings_range="25k+", ai_adjustment=-50).breakdown["ai_adjustment"] == -10.0
    assert _score(earnings_range="25k+", ai_adjustment=-50).score == 25
    assert _score(ai_adjustment=10).score == 10 and _score(ai_adjustment=11).score == 10
    assert _score(ai_adjustment=-3).score == 0  # floor at 0


def test_score_clamped_to_100_and_rounding():
    r = _score(has_phone=True, weights={"has_phone": 250})
    assert r.score == 100 and r.tier == "hot"
    assert _score(followers={"a": 1000}).score == 18  # 17.5 -> round-half-even 18
    assert _score(followers={"a": 1000}, tiers={"hot": 18, "warm": 1}).tier == "hot"


def test_followers_cap_weight():
    assert _score(followers={"a": 100}, weights={"followers_cap": 100}).breakdown["audience"] == 35.0
    assert _score(followers={"a": 100}, weights={"followers_max": 10, "followers_cap": 100}).score == 10


# --------------------------------------------------------------------------- attribution
def test_clean_param_exact():
    assert attr.clean_param("  a\x00b\n ") == "ab"
    assert attr.clean_param("x" * 300) == "x" * 255
    assert attr.clean_param("abcdef", 3) == "abc"
    assert attr.clean_param("") == "" and attr.MAX_PARAM_LEN == 255


def test_fbc_fbp_builders_and_validators():
    assert attr.build_fbc("ABC", 1_700_000_000_000) == "fb.1.1700000000000.ABC"
    assert attr.build_fbp(1_700_000_000_000, 42) == "fb.1.1700000000000.42"
    auto = attr.build_fbc("Z")
    assert auto.startswith("fb.1.") and auto.endswith(".Z") and len(auto.split(".")[2]) == 13
    fbp = attr.build_fbp()
    _, _, ts, rnd = fbp.split(".")
    assert len(ts) == 13 and 0 <= int(rnd) < 2**31
    assert attr.valid_fbc("fb.1.1700000000.abc-_x") and attr.valid_fbc("fb.2.1700000000000.abc.AQ")
    assert not attr.valid_fbc("fb.1.170000000.abc")  # 9-digit ts
    assert not attr.valid_fbc("fb.1.17000000000000.abc")  # 14-digit ts
    assert not attr.valid_fbc("") and not attr.valid_fbc("fb.1.1700000000000.a b")
    assert attr.valid_fbp("fb.1.1700000000000.42") and not attr.valid_fbp("fb.1.1700000000000.abc")
    assert not attr.valid_fbp("") and not attr.valid_fbp("xfb.1.1700000000000.42")


@pytest.mark.parametrize(
    "ua,expected",
    [
        ("", ("bot", "", "", True)),
        ("Googlebot/2.1", ("bot", "", "", True)),
        ("curl/8.0", ("bot", "", "", True)),
        ("Mozilla/5.0 HeadlessChrome/120", ("bot", "", "", True)),
        ("Mozilla/5.0 (Linux; Android 14; Tab) Chrome/120 Safari", ("tablet", "android", "chrome", False)),
        ("Mozilla/5.0 (Linux; Android 14) Instagram 300 Mobile", ("mobile", "android", "instagram", False)),
        ("Mozilla/5.0 (iPod touch) Version/17 Safari/604", ("mobile", "ios", "safari", False)),
        ("Mozilla/5.0 (iPhone) CriOS/120 Mobile", ("mobile", "ios", "chrome", False)),
        ("Mozilla/5.0 (iPhone) FxiOS/120 Mobile", ("mobile", "ios", "firefox", False)),
        ("Mozilla/5.0 (Linux; Android 14) BytedanceWebview Mobile", ("mobile", "android", "tiktok", False)),
        ("Opera/9.80 (Windows NT 6.1)", ("desktop", "windows", "opera", False)),
        ("Mozilla/5.0 (Windows Phone 10.0) Edge", ("mobile", "windows", "other", False)),
        ("Mozilla/5.0 (Macintosh) Version/17 Safari/605", ("desktop", "macos", "safari", False)),
        ("Mozilla/5.0 (X11; Linux) Gecko Firefox/121", ("desktop", "linux", "firefox", False)),
        ("Mozilla/5.0 (Kindle Fire) Silk/1", ("tablet", "other", "other", False)),
        ("Mozilla/5.0 (FBAV/400; iPad)", ("tablet", "ios", "facebook", False)),
    ],
)
def test_parse_user_agent_full(ua, expected):
    assert attr.parse_user_agent(ua) == expected


def test_parse_user_agent_none_is_bot():
    assert attr.parse_user_agent(None) == ("bot", "", "", True)  # type: ignore[arg-type]


def test_referrer_host_exact():
    assert attr.referrer_host("https://WWW.Example.COM/path") == "www.example.com"
    assert attr.referrer_host("http://[::1") == ""
    assert attr.referrer_host("") == ""
    long = "https://" + "a" * 300 + ".com/"
    assert len(attr.referrer_host(long)) == 255


def test_extract_click_context_cookie_paths():
    ctx = attr.extract_click_context(
        {"utm_source": "s", "utm_medium": "m", "utm_content": "c", "utm_term": "t" * 200},
        {"_fbc": "bad", "_fbp": "fb.1.1700000000000.7"},
        "Mozilla/5.0 (Windows NT 10.0) Chrome/120",
        "https://l.instagram.com/",
    )
    assert (ctx.utm_source, ctx.utm_medium, ctx.utm_content) == ("s", "m", "c")
    assert ctx.utm_term == "t" * 120 and ctx.utm_campaign == ""
    assert ctx.fbc == "" and ctx.fbclid == "" and ctx.fbp == "fb.1.1700000000000.7"
    assert ctx.referrer_host == "l.instagram.com"
    assert (ctx.device, ctx.os, ctx.browser, ctx.is_bot) == ("desktop", "windows", "chrome", False)
    d = ctx.as_dict()
    assert d["utm_source"] == "s" and d["is_bot"] is False and len(d) == 14
    ctx2 = attr.extract_click_context({"fbclid": "F" * 300}, {}, "x", None, now_ms=1)
    assert ctx2.fbclid == "F" * 255 and ctx2.fbc == "fb.1.1." + "F" * 255


def test_append_params_and_safe_destination_exact():
    assert attr.append_params("https://x.com/p", {"b": "2", "a": "1"}) == "https://x.com/p?b=2&a=1"
    assert attr.append_params("https://x.com/p?a=", {"a": "1"}) == "https://x.com/p?a="  # blank kept
    assert attr.append_params("https://x.com/p", {}) == "https://x.com/p"
    assert attr.safe_destination("http://x.com/a") is True
    assert attr.safe_destination("https:///nohost") is False
    assert attr.safe_destination("ftp://x.com") is False
    assert attr.safe_destination("data:text/html,hi") is False


# --------------------------------------------------------------------------- meta_capi mock + creds
def test_mock_handler_responses():
    import httpx

    ok = meta_capi.mock_handler(httpx.Request("POST", "https://x/events", content=b'{"data": [1, 2]}'))
    trace = hashlib.sha256(b'{"data": [1, 2]}').hexdigest()[:12]
    assert ok.status_code == 200
    assert ok.json() == {"events_received": 2, "messages": [], "fbtrace_id": f"mock{trace}"}
    bad = meta_capi.mock_handler(httpx.Request("POST", "https://x/events", content=b"{nope"))
    assert bad.status_code == 400 and bad.json() == {"error": {"message": "invalid json"}}
    empty = meta_capi.mock_handler(httpx.Request("POST", "https://x/events", content=b'{"data": []}'))
    assert empty.status_code == 400 and empty.json() == {"error": {"message": "data required", "code": 100}}
    nobody = meta_capi.mock_handler(httpx.Request("POST", "https://x/events"))
    assert nobody.status_code == 400 and nobody.json()["error"]["code"] == 100


@pytest.mark.parametrize(
    "pixel,token,live",
    [("PX", "tok", True), ("PX", None, False), ("", "tok", False), ("", None, False)],
)
def test_meta_credentials_live_requires_both(pixel, token, live):
    creds = meta_capi.MetaCredentials(pixel_id=pixel, access_token=token)
    assert creds.live is live
    client = meta_capi.MetaCapiClient(creds)
    assert client.mode == ("live" if live else "mock")


def test_meta_mock_client_endpoint_and_auth():
    with_pixel = meta_capi.MetaCapiClient(meta_capi.MetaCredentials(pixel_id="PX9", access_token=None))
    assert with_pixel.check_auth() == {"id": "PX9", "mode": "mock"}
    assert with_pixel.endpoint.endswith("/PX9/events")
    anon = meta_capi.MetaCapiClient(meta_capi.MetaCredentials(pixel_id="", access_token=None))
    assert anon.check_auth() == {"id": "mock_pixel", "mode": "mock"}
    assert anon.endpoint.endswith("/mock_pixel/events")


def test_capi_event_defaults():
    ev = meta_capi.CapiEvent(event_name="Lead", event_id="x")
    assert ev.opt_out is False and ev.action_source == "website" and ev.raw_user == {}


# --------------------------------------------------------------------------- attribution clocks/defaults
def test_click_context_defaults():
    ctx = attr.ClickContext()
    assert ctx.is_bot is False and ctx.fbc == "" and ctx.device == ""


def test_fbc_fbp_use_millisecond_clock_and_31_bit_random(monkeypatch):
    monkeypatch.setattr(attr.time, "time", lambda: 1_700_000_000.5)
    seen = []

    def fake_randbelow(n):
        seen.append(n)
        return 7

    monkeypatch.setattr(attr.secrets, "randbelow", fake_randbelow)
    assert attr.build_fbc("Q") == "fb.1.1700000000500.Q"
    assert attr.build_fbp() == "fb.1.1700000000500.7"
    assert seen == [2**31]
