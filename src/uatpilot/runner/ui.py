"""Execute a UiSpec in a real headless Chromium through Playwright, and let the generation agent
inspect a page's interactive elements before it writes selectors."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from uatpilot.config import Settings
from uatpilot.models import UiAction, UiSpec, UiStep
from uatpilot.target import RATE_LIMITER, check_target, join

try:  # Playwright is an optional extra: API-only installs still work.
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised only without the extra
    sync_playwright = None  # type: ignore[assignment]
    PlaywrightError = PlaywrightTimeout = Exception  # type: ignore[assignment,misc]


class PlaywrightMissing(RuntimeError):
    pass


def _require() -> None:
    if sync_playwright is None:
        raise PlaywrightMissing(
            "UI testing needs Playwright: pip install 'uatpilot[ui]' && playwright install chromium"
        )


SPLIT_TEXT = "every expected word is on the page, in order, but not as one contiguous string"


def words_in_order(expected: str, text: str) -> bool:
    """True when the expected text's words all appear in `text` in order, with other content in
    between: the assertion glued separate elements together, so the test is wrong, not the app."""
    want, have = expected.split(), iter(text.split())
    return len(want) > 1 and all(any(w == h for h in have) for w in want)


def _do(page: Any, step: UiStep, base_url: str, settings: Settings) -> str | None:
    """Perform one step. Returns None on success or a failure message for an expect_* step."""
    t = settings.ui_timeout_ms
    loc = page.locator(step.selector) if step.selector else None
    match step.action:
        case UiAction.GOTO:
            url = join(base_url, step.value or "/")
            check_target(url, settings)
            RATE_LIMITER.wait(page_host(url), settings.min_request_interval)
            page.goto(url, timeout=t, wait_until="domcontentloaded")
        case UiAction.FILL:
            loc.first.fill(step.value or "", timeout=t)
        case UiAction.CLICK:
            loc.first.click(timeout=t)
        case UiAction.SELECT:
            loc.first.select_option(step.value, timeout=t)
        case UiAction.EXPECT_VISIBLE:
            try:
                loc.first.wait_for(state="visible", timeout=t)
            except PlaywrightTimeout:
                return f"expected {step.selector!r} to be visible, it was not"
        case UiAction.EXPECT_HIDDEN:
            try:
                loc.first.wait_for(state="hidden", timeout=t)
            except PlaywrightTimeout:
                return f"expected {step.selector!r} to be hidden, it was visible"
        case UiAction.EXPECT_TEXT:
            deadline = time.monotonic() + t / 1000
            text = ""
            while time.monotonic() < deadline:
                if loc.count():
                    text = loc.first.inner_text(timeout=t)
                    if (step.value or "") in text:
                        return None
                page.wait_for_timeout(200)
            msg = f"expected {step.selector!r} text to contain {step.value!r}, got {text[:200]!r}"
            if words_in_order(step.value or "", text):
                msg += f" [{SPLIT_TEXT}]"
            return msg
        case UiAction.EXPECT_URL:
            deadline = time.monotonic() + t / 1000
            while time.monotonic() < deadline:
                if (step.value or "") in page.url:
                    return None
                page.wait_for_timeout(200)
            return f"expected URL to contain {step.value!r}, got {page.url!r}"
        case UiAction.EXPECT_COUNT:
            want = int(step.value or 0)
            deadline = time.monotonic() + t / 1000
            got = loc.count()
            while got != want and time.monotonic() < deadline:
                page.wait_for_timeout(200)
                got = loc.count()
            if got != want:
                return f"expected {want} element(s) matching {step.selector!r}, found {got}"
        case UiAction.EXPECT_ATTRIBUTE:
            attr, _, expected = (step.value or "").partition("=")
            actual = loc.first.get_attribute(attr, timeout=t) if loc.count() else None
            if actual is None or expected not in actual:
                return f"expected [{attr}] of {step.selector!r} to contain {expected!r}, got {actual!r}"
    return None


def page_host(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).hostname or ""


def run_ui_spec(
    spec: UiSpec, base_url: str, settings: Settings, run_id: str
) -> tuple[bool, dict[str, Any]]:
    _require()
    steps: list[dict[str, Any]] = []
    console_errors: list[str] = []
    evidence: dict[str, Any] = {"steps": steps, "console_errors": console_errors}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context(user_agent=settings.user_agent).new_page()
            page.on("console", lambda m: m.type == "error" and console_errors.append(m.text[:300]))
            page.on("pageerror", lambda e: console_errors.append(f"pageerror: {str(e)[:300]}"))
            for step in spec.steps:
                record: dict[str, Any] = step.model_dump(mode="json")
                steps.append(record)
                try:
                    failure = _do(page, step, base_url, settings)
                except PlaywrightTimeout as exc:
                    failure = None
                    record["error"] = {"kind": "timeout", "detail": str(exc).splitlines()[0]}
                except PlaywrightError as exc:
                    failure = None
                    msg = str(exc).splitlines()[0]
                    kind = "connection" if "net::" in msg else "browser"
                    record["error"] = {"kind": kind, "detail": msg}
                if failure:
                    record["failure"] = failure
                if failure or "error" in record:
                    evidence["failed_step"] = (
                        f"{step.action} {step.selector or step.value or ''}".strip()
                    )
                    evidence["url"] = page.url
                    evidence["screenshot"] = _screenshot(page, settings, run_id)
                    return False, evidence
                record["ok"] = True
            evidence["url"] = page.url
            return True, evidence
        finally:
            browser.close()


def _screenshot(page: Any, settings: Settings, run_id: str) -> str | None:
    try:
        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = Path(settings.artifacts_dir) / f"{run_id}.png"
        page.screenshot(path=str(path), full_page=False)
        return path.name
    except PlaywrightError:
        return None


_INSPECT_JS = """
() => {
  const out = [];
  const els = document.querySelectorAll(
    'input, button, a, select, textarea, [data-test], [role=button], h1, h2, h3, .error, [class*=error]');
  for (const el of els) {
    if (out.length >= 80) break;
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    out.push({
      tag: el.tagName.toLowerCase(),
      id: el.id || undefined,
      name: el.getAttribute('name') || undefined,
      data_test: el.getAttribute('data-test') || undefined,
      type: el.getAttribute('type') || undefined,
      placeholder: el.getAttribute('placeholder') || undefined,
      text: (el.innerText || el.value || '').trim().slice(0, 60) || undefined,
      href: el.getAttribute('href') || undefined,
    });
  }
  return out;
}
"""


def inspect_page(base_url: str, setup_steps: list[UiStep], settings: Settings) -> dict[str, Any]:
    """Open the target (optionally running goto/fill/click steps first, e.g. to log in) and list
    the visible interactive elements with the attributes a stable selector can be built from."""
    _require()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_context(user_agent=settings.user_agent).new_page()
            steps = setup_steps or [UiStep(action=UiAction.GOTO, value="/")]
            for s in steps:
                if s.is_assertion:
                    continue
                _do(page, s, base_url, settings)
            page.wait_for_timeout(300)
            return {"url": page.url, "title": page.title(), "elements": page.evaluate(_INSPECT_JS)}
        finally:
            browser.close()
