"""Article extraction + local summarization via Ollama.

Extraction strategy (in order, stopping at the first success):
  1. Direct HTTP fetch with realistic browser headers.
  2. Playwright headless-Chromium render (for JS-heavy pages) — optional;
     used only if installed.
  3. Fall back to the Hacker News discussion text.

Each story is labeled with where its summary came from, and known
paywall/PDF/video cases are reported clearly instead of as generic failures.
"""
import asyncio
from urllib.parse import urlparse

import httpx
import trafilatura

import hn

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"
# Active models — mutable so they can be changed at runtime via Settings.
# Defaults here; app.py loads any saved choice from the DB on startup.
MODEL = "llama3.2:3b"
EMBED_MODEL = "nomic-embed-text"
DEFAULT_MODEL = "llama3.2:3b"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
MAX_ARTICLE_CHARS = 8000  # keep the prompt small for a fast local model
MIN_USEFUL_CHARS = 200    # less than this isn't a real article body

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Domains with hard paywalls / aggressive bot-blocking that a simple fetch
# (and even headless render) won't beat. We label these rather than retry.
PAYWALL_DOMAINS = {
    "bloomberg.com", "wsj.com", "nytimes.com", "ft.com", "economist.com",
    "reuters.com", "thetimes.co.uk", "newyorker.com", "wired.com",
    "theatlantic.com", "medium.com",
}

PROMPT = (
    "Summarize the following article in 2-3 concise sentences for a tech-savvy "
    "reader skimming a news feed. Focus on what is new or notable. Do not add "
    "preamble like 'This article'. Just the summary.\n\n"
    "TITLE: {title}\n\nARTICLE:\n{body}\n\nSUMMARY:"
)

DISCUSSION_PROMPT = (
    "Below is a Hacker News discussion about an article titled \"{title}\". "
    "The article itself couldn't be retrieved. Based only on the discussion, "
    "summarize in 2-3 sentences what the article is likely about and what "
    "commenters are focused on. Start with 'Based on the HN discussion,'.\n\n"
    "DISCUSSION:\n{body}\n\nSUMMARY:"
)


def _domain(url: str) -> str:
    try:
        return urlparse(url).hostname.replace("www.", "") if url else ""
    except Exception:
        return ""


def _looks_like_pdf(url: str, content_type: str = "") -> bool:
    return url.lower().endswith(".pdf") or "application/pdf" in content_type


def _looks_like_video(url: str) -> bool:
    d = _domain(url)
    return any(
        v in d for v in ("youtube.com", "youtu.be", "fb.watch", "vimeo.com")
    ) or url.lower().endswith((".mp4", ".webm", ".mov"))


async def _fetch_direct(url: str) -> str | None:
    """Plain HTTP GET with browser-like headers; returns extracted text."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
            r = await client.get(url, headers=BROWSER_HEADERS)
            if r.status_code in (401, 403, 429):
                return None  # blocked — caller may try render or fall back
            r.raise_for_status()
            html = r.text
    except Exception:
        return None
    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if text and len(text.strip()) >= MIN_USEFUL_CHARS:
        return text.strip()
    return None


async def _fetch_rendered(url: str) -> str | None:
    """Render the page in headless Chromium (JS-heavy sites). Optional.

    Returns None if Playwright isn't installed or rendering fails.
    """
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=BROWSER_HEADERS["User-Agent"])
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                # Give client-side rendering a moment to populate content.
                await page.wait_for_timeout(1500)
                html = await page.content()
            finally:
                await browser.close()
    except Exception:
        return None
    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if text and len(text.strip()) >= MIN_USEFUL_CHARS:
        return text.strip()
    return None


async def summarize_text(prompt: str) -> str | None:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 220},
    }
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post(OLLAMA_URL, json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    out = (data.get("response") or "").strip()
    return out or None


async def _summarize_from_discussion(story: dict) -> tuple[str, str, str] | None:
    """Fallback: summarize from the HN thread. Returns (text, status, source)."""
    discussion = await hn.fetch_discussion_text(story["id"])
    if not discussion or len(discussion) < MIN_USEFUL_CHARS:
        return None
    summary = await summarize_text(
        DISCUSSION_PROMPT.format(title=story.get("title", ""),
                                 body=discussion[:MAX_ARTICLE_CHARS])
    )
    if not summary:
        return None
    return (summary, "done", "discussion")


async def summarize_story(story: dict) -> tuple[str, str, str]:
    """Return (summary_text, status, source) for a story dict.

    status: done | skipped | failed | pending
    source: article | rendered | discussion | none
    """
    url = story.get("url")
    title = story.get("title", "")

    # Ask HN / Show HN text posts: summarize the post + discussion directly.
    if not url:
        viahn = await _summarize_from_discussion(story)
        if viahn:
            return viahn
        return ("Discussion thread on Hacker News — open it to read.", "skipped", "none")

    # PDFs and videos: we can't read these. Try the discussion, else label clearly.
    if _looks_like_pdf(url):
        viahn = await _summarize_from_discussion(story)
        return viahn or ("PDF document — open the link to read it.", "failed", "pdf")
    if _looks_like_video(url):
        viahn = await _summarize_from_discussion(story)
        return viahn or ("Video link — open it to watch.", "failed", "video")

    # 1) Direct fetch with browser headers.
    body = await _fetch_direct(url)
    source = "article"

    # 2) Headless render for JS-heavy pages (skip known hard paywalls).
    if not body and _domain(url) not in PAYWALL_DOMAINS:
        body = await _fetch_rendered(url)
        if body:
            source = "rendered"

    # 3) Got article text? Summarize it.
    if body:
        summary = await summarize_text(
            PROMPT.format(title=title, body=body[:MAX_ARTICLE_CHARS])
        )
        if not summary:
            return ("Waiting for local model…", "pending", "none")
        return (summary, "done", source)

    # 4) Couldn't get the article — fall back to the HN discussion.
    viahn = await _summarize_from_discussion(story)
    if viahn:
        return viahn

    # 5) Nothing worked. Label the most likely reason.
    if _domain(url) in PAYWALL_DOMAINS:
        return (f"Paywalled ({_domain(url)}) — open the link to read it.",
                "failed", "paywall")
    return ("Couldn't extract article text (blocked or JS-heavy) and the HN "
            "discussion was too thin to summarize. Open the link to read it.",
            "failed", "none")


async def ollama_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("http://localhost:11434/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def list_installed_models() -> list[str]:
    """Return the names of models currently installed in Ollama."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(OLLAMA_TAGS_URL)
            r.raise_for_status()
            return [m.get("name", "") for m in r.json().get("models", []) if m.get("name")]
    except Exception:
        return []


def set_models(model: str | None = None, embed_model: str | None = None):
    """Update the active summary / embedding models at runtime."""
    global MODEL, EMBED_MODEL
    if model:
        MODEL = model
    if embed_model:
        EMBED_MODEL = embed_model


# --- embeddings (semantic search) ----------------------------------------------
import math


async def embed_text(text: str) -> list[float] | None:
    """Return an embedding vector for text via the local embedding model."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                OLLAMA_EMBED_URL,
                json={"model": EMBED_MODEL, "prompt": text[:MAX_ARTICLE_CHARS]},
            )
            r.raise_for_status()
            data = r.json()
    except Exception:
        return None
    vec = data.get("embedding")
    return vec if isinstance(vec, list) and vec else None


async def embed_model_available() -> bool:
    """True if the embedding model is pulled and reachable."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("http://localhost:11434/api/tags")
            if r.status_code != 200:
                return False
            names = [m.get("name", "") for m in r.json().get("models", [])]
            return any(n.startswith(EMBED_MODEL) for n in names)
    except Exception:
        return False


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
