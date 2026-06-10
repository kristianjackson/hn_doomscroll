"""Capture additional screenshots: semantic search + settings provider panel."""
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


async def main():
    DOCS.mkdir(exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1100, "height": 1000}, device_scale_factor=2
        )

        # --- Semantic search screenshot ---
        await page.goto("http://localhost:8000", wait_until="networkidle")
        js = "(t) => { localStorage.setItem('theme', t); document.documentElement.setAttribute('data-theme', t); }"
        await page.evaluate(js, "light")
        await page.reload(wait_until="networkidle")
        await page.wait_for_selector("#search-input", timeout=15000)
        # Switch to semantic mode
        mode_btn = page.locator("#search-mode")
        mode_text = await mode_btn.inner_text()
        if mode_text.strip().lower() == "kw":
            await mode_btn.click()
        await page.fill("#search-input", "machine learning")
        await page.wait_for_timeout(6000)
        await page.screenshot(path=str(DOCS / "search-semantic.png"))
        print(f"saved docs/search-semantic.png")

        # --- Settings with provider toggle screenshot ---
        await page.goto("http://localhost:8000", wait_until="networkidle")
        await page.evaluate(js, "light")
        await page.reload(wait_until="networkidle")
        await page.wait_for_selector("#settings-btn", timeout=15000)
        await page.click("#settings-btn")
        await page.wait_for_selector("#settings-overlay:not(.hidden)", timeout=5000)
        # Scroll modal to show AI Provider section
        await page.evaluate("document.querySelector('.modal').scrollTop = 300")
        await page.wait_for_timeout(1000)
        await page.screenshot(path=str(DOCS / "settings-provider.png"))
        print(f"saved docs/settings-provider.png")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
