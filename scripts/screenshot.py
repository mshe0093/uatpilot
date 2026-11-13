"""Capture the README screenshot of the dashboard.

uatpilot serve &                       # API on :8000 with some run history
cd web && npm run dev &                # dashboard on :3000
python scripts/screenshot.py http://localhost:3000 docs/assets/dashboard.png
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main(url: str, out: str, suite: str = "restful-booker") -> None:
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(
            viewport={"width": 1280, "height": 900}, color_scheme="light", device_scale_factor=1
        )
        page.goto(url, wait_until="networkidle")
        page.locator(".sets button", has_text=suite).click()
        page.wait_for_selector("svg[role=img]")
        # Open one failed case so the triage explanation and evidence are visible.
        failed = page.locator("tr.row", has_text="Fail").first
        if failed.count():
            failed.click()
        # Hide the Next.js dev-mode indicator if the dashboard runs under `npm run dev`.
        page.add_style_tag(content="nextjs-portal { display: none !important; }")
        page.wait_for_timeout(500)
        # KPIs, trend chart, case table and one expanded failure; the rest is the same pattern.
        page.screenshot(
            path=out, full_page=True, clip={"x": 0, "y": 0, "width": 1280, "height": 1700}
        )
        browser.close()
    print(f"wrote {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
