"""Fetch front-page stories from the official Hacker News Firebase API."""
import asyncio
import httpx

API = "https://hacker-news.firebaseio.com/v0"
HN_ITEM = "https://news.ycombinator.com/item?id={}"


async def _get_json(client: httpx.AsyncClient, url: str):
    r = await client.get(url, timeout=20)
    r.raise_for_status()
    return r.json()


async def fetch_top_stories(limit: int = 50):
    """Return a list of story dicts ready for db.upsert_story."""
    async with httpx.AsyncClient() as client:
        ids = await _get_json(client, f"{API}/topstories.json")
        ids = ids[:limit]

        async def one(item_id: int):
            try:
                it = await _get_json(client, f"{API}/item/{item_id}.json")
            except Exception:
                return None
            if not it or it.get("type") != "story" or it.get("dead") or it.get("deleted"):
                return None
            return {
                "id": it["id"],
                "title": it.get("title", "(untitled)"),
                "url": it.get("url"),  # None for Ask/Show HN text posts
                "hn_url": HN_ITEM.format(it["id"]),
                "score": it.get("score", 0),
                "author": it.get("by"),
                "num_comments": it.get("descendants", 0),
                "posted_at": it.get("time", 0),
            }

        results = await asyncio.gather(*(one(i) for i in ids))
    return [r for r in results if r]


def _strip_html(text: str) -> str:
    """HN comment/post text is HTML-ish. Crude but dependency-free cleanup."""
    import html
    import re
    if not text:
        return ""
    text = text.replace("<p>", "\n\n").replace("</p>", "")
    text = re.sub(r"<[^>]+>", "", text)        # drop remaining tags
    return html.unescape(text).strip()


async def fetch_discussion_text(item_id: int, max_comments: int = 8) -> str | None:
    """Return the post's own text plus its top comments, as plain text.

    Used as a fallback when an external article can't be fetched: the HN
    discussion usually contains enough context to summarize what it's about.
    """
    async with httpx.AsyncClient() as client:
        try:
            item = await _get_json(client, f"{API}/item/{item_id}.json")
        except Exception:
            return None
        if not item:
            return None

        parts = []
        if item.get("text"):
            parts.append(_strip_html(item["text"]))

        kid_ids = (item.get("kids") or [])[:max_comments]

        async def comment(cid: int):
            try:
                c = await _get_json(client, f"{API}/item/{cid}.json")
            except Exception:
                return None
            if not c or c.get("deleted") or c.get("dead") or not c.get("text"):
                return None
            return _strip_html(c["text"])

        comments = await asyncio.gather(*(comment(c) for c in kid_ids))
        parts.extend(c for c in comments if c)

    text = "\n\n".join(p for p in parts if p).strip()
    return text or None
