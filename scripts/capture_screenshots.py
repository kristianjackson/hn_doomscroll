"""Capture README screenshots of the running app via Playwright.

Usage:
    # 1. Start the app in another terminal:
    python app.py
    # 2. Run this (from the project root or the scripts/ folder):
    python scripts/capture_screenshots.py

Saves PNGs into docs/. Requires the Playwright Chromium browser
(`python -m playwright install chromium`, which run.bat does on first launch).

Environment overrides:
    HN_URL          base URL of the running app (default http://localhost:8000)
    SHOT_DELAY_MS   pause before each capture so summaries can fill in
                    (default 2500)
"""
import asyncio
import os
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    sys.exit("Playwright not installed. Run: python -m playwright install chromium")

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
URL = os.environ.get("HN_URL", "http://localhost:8000")
DELAY = int(os.environ.get("SHOT_DELAY_MS", "2500"))


async def set_theme(page, theme):
    await page.evaluate(
        "(t) => { localStorage.setItem('theme', t);"
        "document.documentElement.setAttribute('data-theme', t); }",
        theme,
    )


async def shot(page, theme, filename):
    await set_theme(page, theme)
    await page.reload(wait_until="networkidle")
    await page.wait_for_selector(".card", timeout=15000)
    await page.wait_for_timeout(DELAY)  # let on-demand summaries populate
    out = DOCS / filename
    await page.screenshot(path=str(out))
    print(f"saved {out.relative_to(ROOT)}")


async def main():
    DOCS.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                viewport={"width": 1100, "height": 1000}, device_scale_factor=2
            )
            await page.goto(URL, wait_until="networkidle")

            await shot(page, "light", "feed-light.png")
            await shot(page, "dark", "feed-dark.png")

            # Settings panel (light theme).
            await set_theme(page, "light")
            await page.reload(wait_until="networkidle")
            await page.wait_for_selector("#settings-btn", timeout=15000)
            await page.click("#settings-btn")
            await page.wait_for_selector("#settings-overlay:not(.hidden)", timeout=5000)
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(DOCS / "settings.png"))
            print(f"saved {(DOCS / 'settings.png').relative_to(ROOT)}")
        finally:
            await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        sys.exit(f"Capture failed: {e}\nIs the app running at {URL}?")
