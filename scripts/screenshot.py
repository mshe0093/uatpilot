"""Capture the README screenshots of the dashboard.

uatpilot serve &                       # API on :8000 with some run history
cd web && npm run dev &                # dashboard on :3000
python scripts/screenshot.py http://localhost:3000 docs/assets
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HIDE_DEV = "nextjs-portal { display: none !important; }"  # Next.js dev-mode indicator


def main(url: str, out_dir: str, suite: str = "restful-booker") -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for scheme in ("light", "dark"):
            page = browser.new_page(viewport={"width": 1440, "height": 900}, color_scheme=scheme)
            page.goto(url, wait_until="networkidle")
            page.locator(".nav button", has_text=suite).click()
            page.wait_for_selector(".matrix")
            page.add_style_tag(content=HIDE_DEV)
            page.wait_for_timeout(400)
            page.screenshot(path=str(out / f"dashboard-{scheme}.png"))
            if scheme == "light":
                # The case sheet, opened from the newest cell of a failing row.
                row = page.locator(".matrix tbody tr", has_text="Cancelling a booking")
                row.locator(".cell button").last.click()
                page.wait_for_selector(".sheet .triage-box")
                page.wait_for_timeout(400)
                page.screenshot(path=str(out / "case-sheet.png"))
            page.close()
        browser.close()
    print(f"wrote screenshots to {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
