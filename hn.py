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
