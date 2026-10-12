"""Click attribution helpers: UTM / click-id capture, fbc/fbp construction, UA parsing."""

from __future__ import annotations

import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")
MAX_PARAM_LEN = 255

_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|facebookexternalhit|preview|monitor|curl|wget|python-requests|httpx|headless",
    re.I,
)
_TABLET_RE = re.compile(r"ipad|tablet|kindle|silk|playbook|(android(?!.*mobile))", re.I)
_MOBILE_RE = re.compile(r"mobi|iphone|ipod|android.*mobile|windows phone|blackberry", re.I)
_FBC_RE = re.compile(r"^fb\.\d\.\d{10,13}\.[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)?$")
_FBP_RE = re.compile(r"^fb\.\d\.\d{10,13}\.\d+(\.[A-Za-z0-9_\-]+)?$")


@dataclass
class ClickContext:
    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""
    utm_content: str = ""
    utm_term: str = ""
    fbclid: str = ""
    fbc: str = ""
    fbp: str = ""
    ttclid: str = ""
    referrer_host: str = ""
    device: str = ""
    os: str = ""
    browser: str = ""
    is_bot: bool = False

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def clean_param(value: str | None, max_len: int = MAX_PARAM_LEN) -> str:
    if not value:
        return ""
    value = "".join(ch for ch in value if ch.isprintable()).strip()
    return value[:max_len]


def build_fbc(fbclid: str, ts_ms: int | None = None) -> str:
    """fbc = fb.<subdomain_index>.<creation_time_ms>.<fbclid> (subdomain index 1 = eTLD+1)."""
    if not fbclid:
        return ""
    ts = int(time.time() * 1000) if ts_ms is None else ts_ms
    return f"fb.1.{ts}.{fbclid}"


def build_fbp(ts_ms: int | None = None, rand: int | None = None) -> str:
    ts = int(time.time() * 1000) if ts_ms is None else ts_ms
    r = secrets.randbelow(2**31) if rand is None else rand
    return f"fb.1.{ts}.{r}"


def valid_fbc(value: str) -> bool:
    return bool(value) and bool(_FBC_RE.match(value))


def valid_fbp(value: str) -> bool:
    return bool(value) and bool(_FBP_RE.match(value))


def parse_user_agent(ua: str) -> tuple[str, str, str, bool]:
    """Return (device, os, browser, is_bot) using cheap regexes (no external DB)."""
    ua = ua or ""
    if not ua or _BOT_RE.search(ua):
        return ("bot", "", "", True)
    device = "tablet" if _TABLET_RE.search(ua) else "mobile" if _MOBILE_RE.search(ua) else "desktop"
    low = ua.lower()
    if "iphone" in low or "ipad" in low or "ipod" in low:
        os_name = "ios"
    elif "android" in low:
        os_name = "android"
    elif "windows" in low:
        os_name = "windows"
    elif "mac os x" in low or "macintosh" in low:
        os_name = "macos"
    elif "linux" in low:
        os_name = "linux"
    else:
        os_name = "other"
    if "instagram" in low:
        browser = "instagram"
    elif "tiktok" in low or "musical_ly" in low or "bytedance" in low:
        browser = "tiktok"
    elif "fban" in low or "fbav" in low:
        browser = "facebook"
    elif "edg/" in low:
        browser = "edge"
    elif "opr/" in low or "opera" in low:
        browser = "opera"
    elif "firefox" in low or "fxios" in low:
        browser = "firefox"
    elif "chrome" in low or "crios" in low:
        browser = "chrome"
    elif "safari" in low:
        browser = "safari"
    else:
        browser = "other"
    return (device, os_name, browser, False)


def referrer_host(referrer: str | None) -> str:
    if not referrer:
        return ""
    try:
        host = urlsplit(referrer).hostname or ""
    except ValueError:
        return ""
    return host.lower()[:255]


def extract_click_context(
    query: Mapping[str, str], cookies: Mapping[str, str], user_agent: str, referrer: str | None,
    now_ms: int | None = None,
) -> ClickContext:
    ctx = ClickContext(**{k: clean_param(query.get(k), 120) for k in UTM_KEYS})
    ctx.fbclid = clean_param(query.get("fbclid"))
    ctx.ttclid = clean_param(query.get("ttclid"))
    cookie_fbc = cookies.get("_fbc", "")
    cookie_fbp = cookies.get("_fbp", "")
    if ctx.fbclid:
        # a fresh fbclid always wins over an older cookie
        ctx.fbc = build_fbc(ctx.fbclid, now_ms)
    elif valid_fbc(cookie_fbc):
        ctx.fbc = cookie_fbc
    ctx.fbp = cookie_fbp if valid_fbp(cookie_fbp) else ""
    ctx.referrer_host = referrer_host(referrer)
    ctx.device, ctx.os, ctx.browser, ctx.is_bot = parse_user_agent(user_agent)
    return ctx


def append_params(url: str, params: Mapping[str, str]) -> str:
    """Append params to a URL without clobbering ones already present."""
    parts = urlsplit(url)
    existing = dict(parse_qsl(parts.query, keep_blank_values=True))
    for k, v in params.items():
        if v and k not in existing:
            existing[k] = v
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(existing), parts.fragment))


def safe_destination(url: str) -> bool:
    """Only http(s) absolute destinations are allowed for redirects (no javascript:, data:)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)
