"""Capture README screenshots from a running, seeded server (optional dev tool).

    pip install playwright && playwright install chromium
    ofm seed-demo --known-passwords && ofm serve &
    python scripts/screenshots.py --base http://127.0.0.1:8000

Writes docs/screenshots/{landing,dashboard,pipeline,settings}.png and fails on console errors.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "docs" / "screenshots"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--email", default="admin@demo.agency")
    ap.add_argument("--password", default="demo-pass-2026")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        # 401s from /auth/me + /auth/refresh are expected while signed out (landing/login).
        page.on(
            "console",
            lambda m: errors.append(f"{page.url}: {m.text}")
            if m.type == "error" and "401" not in m.text
            else None,
        )
        page.on("pageerror", lambda e: errors.append(f"{page.url}: {e}"))
        page.goto(f"{a.base}/", wait_until="load")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "landing.png"), full_page=False)
        page.goto(f"{a.base}/login", wait_until="load")
        page.get_by_label("Email").fill(a.email)
        page.get_by_label("Password").fill(a.password)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_url("**/app**")
        page.wait_for_timeout(2500)  # charts + live stream
        page.screenshot(path=str(OUT / "dashboard.png"), full_page=False)
        page.goto(f"{a.base}/app/pipeline", wait_until="load")
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "pipeline.png"), full_page=False)
        page.goto(f"{a.base}/app/settings/integrations", wait_until="load")
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "settings.png"), full_page=False)
        for extra in ("revenue", "automations", "links", "creators", "ai"):
            page.goto(f"{a.base}/app/{extra}", wait_until="load")
            page.wait_for_timeout(500)
        browser.close()
    for e in errors:
        print("console error:", e)
    print(f"wrote {len(list(OUT.glob('*.png')))} screenshots to {OUT}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
