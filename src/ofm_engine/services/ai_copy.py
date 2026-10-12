"""AI marketing-copy generator with a hard content guardrail.

Refuses (before and after generation): sexually explicit content, anything involving minors,
and impersonation (writing *as* a creator to fans, fake personas, "pretend to be ..."). The
platform is for agency marketing / recruitment copy only, never for chatting as a creator.
Mock mode is deterministic: the same brief always yields the same variants.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..adapters.http import IntegrationError
from .context import TenantContext

KINDS = ("recruitment_ad", "headline", "followup_sms", "followup_email", "social_caption")

_EXPLICIT = re.compile(
    r"\b(explicit|nsfw|porn\w*|nude\w*|naked|sex\w*|xxx|fetish\w*|orgasm\w*|genital\w*|"
    r"blow\s*job|nipples?|cum\w*|horny|erotic\w*|onlyfans\s+leak\w*|dick\w*|pussy|boobs?|tits?)\b", re.I)
_MINORS = re.compile(r"\b(minor|underage|under\s*18|teen(?:age(?:r)?)?s?|child\w*|kid|kids|schoolgirl|"
                     r"high\s*school)\b", re.I)
_IMPERSONATION = re.compile(
    r"(pretend(?:ing)?\s+(?:to\s+be|you(?:'re| are))|impersonat\w*|as\s+if\s+you\s+were|"
    r"(?:write|reply|respond|chat|talk|message|dm)\w*\s+(?:as|like)\s+(?:the\s+|her|him|a\s+)?"
    r"(?:creator|model|girl|her|him|she|he)\b|"
    r"in\s+the\s+voice\s+of|posing\s+as|fake\s+(?:persona|profile|account)|sexting|"
    r"chat(?:ting)?\s+with\s+fans\s+as)", re.I)


class ContentRefused(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def check_content(text: str) -> None:
    if _MINORS.search(text):
        raise ContentRefused("content referencing minors is not allowed")
    if _EXPLICIT.search(text):
        raise ContentRefused("sexually explicit content is not allowed")
    if _IMPERSONATION.search(text):
        raise ContentRefused("impersonating creators or fake personas is not allowed")


_TEMPLATES: dict[str, list[str]] = {
    "recruitment_ad": [
        "Creators: {b}. Our team handles strategy, marketing and ops so you can focus on content. Apply in 60s.",
        "Ready to scale? {b} — dedicated manager, transparent splits, real growth plans. Apply today.",
        "Stop doing it all alone. {b}. Join an agency that treats your brand like a business.",
        "{b}. We're accepting a few new creators this month — see if you qualify.",
        "Grow smarter, not harder: {b}. Free strategy call for qualified creators.",
    ],
    "headline": [
        "Grow your creator brand with a team that's all-in", "{b} — without the burnout",
        "Your content. Our growth engine.", "Turn your audience into a real business", "Scale up: {b}",
    ],
    "followup_sms": [
        "Hi {{first_name}}! Thanks for applying — {b}. Grab a time for a quick intro call: {{booking_link}} Reply STOP to opt out.",
        "Hey {{first_name}}, following up on your application. {b}. Free 15-min call? {{booking_link}} Reply STOP to opt out.",
        "{{first_name}}, your spot for an intro call is still open — {b}. {{booking_link}} Reply STOP to opt out.",
        "Quick one {{first_name}}: {b}. Want to chat this week? {{booking_link}} Reply STOP to opt out.",
        "Last check-in {{first_name}} — {b}. Book here if you're still interested: {{booking_link}} Reply STOP to opt out.",
    ],
    "followup_email": [
        "Subject: Next steps on your application\n\nHi {{first_name}},\n\nThanks for applying. {b}. "
        "Pick a time for a short intro call: {{booking_link}}\n\nTalk soon,\n{{agency_name}}",
        "Subject: Quick question, {{first_name}}\n\nHi {{first_name}},\n\n{b}. Do you have 15 minutes this week? "
        "{{booking_link}}\n\nBest,\n{{agency_name}}",
        "Subject: Your growth plan\n\nHi {{first_name}},\n\nWe reviewed your profile — {b}. "
        "Let's map out a plan together: {{booking_link}}\n\n{{agency_name}}",
        "Subject: Still interested?\n\nHi {{first_name}},\n\n{b}. If now isn't the right time, no worries. "
        "{{booking_link}}\n\n{{agency_name}}",
        "Subject: Closing your application\n\nHi {{first_name}},\n\nWe'll close your file soon. {b}. "
        "Book before then: {{booking_link}}\n\n{{agency_name}}",
    ],
    "social_caption": [
        "Behind the scenes of building a creator brand: {b} ✨", "{b}. Consistency beats luck. 💼",
        "New week, new goals — {b} 🚀", "{b}. Link in bio to work with us.", "Creators who plan, grow. {b} 📈",
    ],
}

SYSTEM_PROMPT = (
    "You write short, SFW marketing copy for a talent-management agency (recruitment ads, headlines, "
    "follow-up messages to applicants, social captions). Never write sexual or explicit content, never "
    "reference minors, and never write as, or impersonate, a creator or a fake persona. Return one variant "
    "per line with no numbering."
)


@dataclass
class CopyResult:
    variants: list[str]
    provider: str
    mock: bool


def _mock_variants(kind: str, brief: str, n: int) -> list[str]:
    templates = _TEMPLATES[kind]
    seed = int(hashlib.sha256(f"{kind}|{brief}".encode()).hexdigest(), 16)
    start = seed % len(templates)
    b = brief.strip().rstrip(".")
    return [templates[(start + i) % len(templates)].replace("{b}", b) for i in range(n)]


def generate(ctx: TenantContext, kind: str, brief: str, variants: int) -> CopyResult:
    if kind not in KINDS:
        raise ValueError("unknown kind")
    check_content(brief)
    client = ctx.llm()
    if client.mode == "mock":
        return CopyResult(_mock_variants(kind, brief, variants), "mock", True)
    prompt = f"Kind: {kind}\nAgency: {ctx.config.brand.name}\nBrief: {brief}\nWrite {variants} variants."
    try:
        text = ctx.call("llm", "copy", client.mode, lambda: client.complete(SYSTEM_PROMPT, prompt, 600))
    except IntegrationError:
        return CopyResult(_mock_variants(kind, brief, variants), "mock", True)
    lines = [ln.strip(" -•\t") for ln in text.splitlines() if ln.strip()][:variants]
    for ln in lines:
        check_content(ln)
    return CopyResult(lines or _mock_variants(kind, brief, variants), client.cfg.provider, False)
