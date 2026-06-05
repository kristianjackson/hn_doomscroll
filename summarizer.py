"""Article extraction + local summarization via Ollama."""
import asyncio
import httpx
import trafilatura

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "llama3.2:3b"
MAX_ARTICLE_CHARS = 8000  # keep the prompt small for a fast local model

PROMPT = (
    "Summarize the following article in 2-3 concise sentences for a tech-savvy "
    "reader skimming a news feed. Focus on what is new or notable. Do not add "
    "preamble like 'This article'. Just the summary.\n\n"
    "TITLE: {title}\n\nARTICLE:\n{body}\n\nSUMMARY:"
)


async def extract_article(url: str) -> str | None:
    """Download a URL and pull the main readable text out of it."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (HN-Doomscroll)"})
            r.raise_for_status()
            html = r.text
    except Exception:
        return None

    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not text:
        return None
    return text.strip()


async def summarize_text(title: str, body: str) -> str | None:
    body = body[:MAX_ARTICLE_CHARS]
    payload = {
        "model": MODEL,
        "prompt": PROMPT.format(title=title, body=body),
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


async def summarize_story(story: dict) -> tuple[str, str]:
    """Return (summary_text, status) for a story dict.

    status is one of: done | skipped | failed | pending
    'pending' is returned for transient model errors so the worker retries.
    """
    url = story.get("url")
    title = story.get("title", "")

    # Ask HN / Show HN text posts have no external url.
    if not url:
        return ("No external article (discussion thread on Hacker News).", "skipped")

    body = await extract_article(url)
    if not body:
        return ("Couldn't extract article text (paywall, PDF, or JS-heavy page). "
                "Open the link to read it.", "failed")

    summary = await summarize_text(title, body)
    if not summary:
        # Likely the model is still loading or briefly unavailable — retry later.
        return ("Waiting for local model…", "pending")
    return (summary, "done")


async def ollama_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("http://localhost:11434/api/tags")
            return r.status_code == 200
    except Exception:
        return False
