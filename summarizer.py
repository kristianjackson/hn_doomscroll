"""Article extraction + summarization via AWS Bedrock (or local Ollama fallback).

Extraction strategy (in order, stopping at the first success):
  1. Direct HTTP fetch with realistic browser headers.
  2. Playwright headless-Chromium render (for JS-heavy pages) — optional;
     used only if installed.
  3. Fall back to the Hacker News discussion text.

Each story is labeled with where its summary came from, and known
paywall/PDF/video cases are reported clearly instead of as generic failures.

Provider selection (env var):
  HN_PROVIDER=bedrock (default) | ollama
"""
import asyncio
import json as _json
import os
from urllib.parse import urlparse

import httpx
import trafilatura

import hn

# --- Provider config ---
PROVIDER = os.environ.get("HN_PROVIDER", "bedrock")  # "bedrock" or "ollama"

# Ollama settings (fallback)
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_EMBED_URL = "http://localhost:11434/api/embeddings"
OLLAMA_TAGS_URL = "http://localhost:11434/api/tags"

# Bedrock settings
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
BEDROCK_REASON_MODEL = os.environ.get("BEDROCK_REASON_MODEL", "google.gemma-3-4b-it")
BEDROCK_EMBED_MODEL = os.environ.get("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")

# Curated Bedrock model lists (cost-effective options)
BEDROCK_SUMMARY_MODELS = [
    {"id": "anthropic.claude-3-haiku-20240307-v1:0", "name": "Claude 3 Haiku", "provider": "Anthropic"},
    {"id": "google.gemma-3-4b-it", "name": "Gemma 3 4B", "provider": "Google"},
    {"id": "meta.llama3-8b-instruct-v1:0", "name": "Llama 3 8B", "provider": "Meta"},
    {"id": "amazon.nova-micro-v1:0", "name": "Nova Micro", "provider": "Amazon"},
]
BEDROCK_EMBED_MODELS = [
    {"id": "amazon.titan-embed-text-v2:0", "name": "Titan Embed v2", "provider": "Amazon"},
    {"id": "cohere.embed-english-v3", "name": "Embed English v3", "provider": "Cohere"},
    {"id": "cohere.embed-v4:0", "name": "Embed v4", "provider": "Cohere"},
]


def set_provider(provider: str):
    """Switch between 'bedrock' and 'ollama' at runtime."""
    global PROVIDER
    PROVIDER = provider


def set_bedrock_models(reason_model: str | None = None, embed_model: str | None = None):
    """Update Bedrock model selections at runtime."""
    global BEDROCK_REASON_MODEL, BEDROCK_EMBED_MODEL
    if reason_model:
        BEDROCK_REASON_MODEL = reason_model
    if embed_model:
        BEDROCK_EMBED_MODEL = embed_model

# Active models — mutable so they can be changed at runtime via Settings.
# These only apply in Ollama mode.
MODEL = "llama3.2:3b"
EMBED_MODEL = "nomic-embed-text"
DEFAULT_MODEL = "llama3.2:3b"
DEFAULT_EMBED_MODEL = "nomic-embed-text"
MAX_ARTICLE_CHARS = 8000  # keep the prompt small for a fast model
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
    """Generate a summary using the configured provider."""
    if PROVIDER == "bedrock":
        return await _summarize_bedrock(prompt)
    return await _summarize_ollama(prompt)


async def _summarize_ollama(prompt: str) -> str | None:
    """Summarize via local Ollama."""
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


async def _summarize_bedrock(prompt: str) -> str | None:
    """Summarize via AWS Bedrock (runs sync boto3 call in executor)."""
    try:
        import boto3
        loop = asyncio.get_event_loop()

        def _call():
            client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
            if "anthropic" in BEDROCK_REASON_MODEL:
                body = _json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 220,
                    "temperature": 0.3,
                    "messages": [{"role": "user", "content": prompt}],
                })
            elif "meta.llama" in BEDROCK_REASON_MODEL:
                body = _json.dumps({
                    "prompt": prompt,
                    "max_gen_len": 220,
                    "temperature": 0.3,
                })
            elif "google.gemma" in BEDROCK_REASON_MODEL:
                body = _json.dumps({
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 220,
                    "temperature": 0.3,
                })
            else:
                body = _json.dumps({
                    "inputText": prompt,
                    "textGenerationConfig": {"maxTokenCount": 220, "temperature": 0.3},
                })
            resp = client.invoke_model(
                modelId=BEDROCK_REASON_MODEL,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            data = _json.loads(resp["body"].read())
            if "anthropic" in BEDROCK_REASON_MODEL:
                content = data.get("content", [])
                return content[0]["text"].strip() if content else ""
            elif "meta.llama" in BEDROCK_REASON_MODEL:
                return data.get("generation", "").strip()
            elif "google.gemma" in BEDROCK_REASON_MODEL:
                choices = data.get("choices", [])
                return choices[0].get("message", {}).get("content", "").strip() if choices else ""
            else:
                results = data.get("results", [{}])
                return results[0].get("outputText", "").strip() if results else ""

        result = await loop.run_in_executor(None, _call)
        return result or None
    except Exception:
        return None


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


async def provider_available() -> bool:
    """Check if the configured provider is reachable."""
    if PROVIDER == "bedrock":
        return await _bedrock_available()
    return await _ollama_available()


async def _ollama_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("http://localhost:11434/api/tags")
            return r.status_code == 200
    except Exception:
        return False


async def _bedrock_available() -> bool:
    """Check Bedrock connectivity (runs STS call in executor)."""
    try:
        import boto3
        loop = asyncio.get_event_loop()

        def _check():
            sts = boto3.client("sts", region_name=BEDROCK_REGION)
            sts.get_caller_identity()
            return True

        return await loop.run_in_executor(None, _check)
    except Exception:
        return False


# Keep old name as alias for backward compat with app.py
ollama_available = provider_available


async def list_installed_models() -> list[str]:
    """Return available models. In Bedrock mode, returns the configured model names.
    In Ollama mode, queries the local Ollama instance."""
    if PROVIDER == "bedrock":
        return [BEDROCK_REASON_MODEL, BEDROCK_EMBED_MODEL]
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
    """Return an embedding vector for text using the configured provider."""
    text = (text or "").strip()
    if not text:
        return None
    if PROVIDER == "bedrock":
        return await _embed_bedrock(text)
    return await _embed_ollama(text)


async def _embed_ollama(text: str) -> list[float] | None:
    """Get embedding from local Ollama."""
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


async def _embed_bedrock(text: str) -> list[float] | None:
    """Get embedding from Titan Embed v2 via Bedrock (runs in executor)."""
    try:
        import boto3
        loop = asyncio.get_event_loop()

        def _call():
            client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
            body = _json.dumps({"inputText": text[:8192]})
            resp = client.invoke_model(
                modelId=BEDROCK_EMBED_MODEL,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            data = _json.loads(resp["body"].read())
            return data.get("embedding")

        vec = await loop.run_in_executor(None, _call)
        return vec if isinstance(vec, list) and vec else None
    except Exception:
        return None


async def embed_model_available() -> bool:
    """True if the embedding capability is available."""
    if PROVIDER == "bedrock":
        return await _bedrock_available()
    # Ollama: check if the specific embed model is pulled
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
