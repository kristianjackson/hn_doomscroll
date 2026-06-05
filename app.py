"""HN Doom-Scroll: a local dashboard for reading Hacker News with AI summaries.

Run:  python app.py   (then open http://localhost:8000)
"""
import asyncio
import contextlib
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import db
import hn
import summarizer

STATIC_DIR = Path(__file__).parent / "static"

# Serializes summary generation: the CPU-bound local model runs one at a time,
# even though requests arrive concurrently as cards scroll into view.
_summary_lock = asyncio.Lock()
_summary_stats = {"done": 0, "last_error": None}


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    # Pull stories on first boot if the DB is empty. Summaries are generated
    # on demand as cards scroll into view (see /api/summarize).
    if db.counts()["new"] == 0 and db.counts()["read"] == 0:
        with contextlib.suppress(Exception):
            await refresh_stories(limit=50)
    yield


app = FastAPI(title="HN Doom-Scroll", lifespan=lifespan)


async def generate_summary(story_id: int):
    """Generate one story's summary, serialized against other generations."""
    async with _summary_lock:
        story = db.get_story(story_id)
        if not story:
            return None
        # Another request may have finished it while we waited for the lock.
        if story["summary_status"] in ("done", "skipped"):
            return story
        if not await summarizer.ollama_available():
            db.set_summary(story_id, "Waiting for local model (Ollama)…", "pending")
            _summary_stats["last_error"] = "Ollama unreachable"
            return db.get_story(story_id)
        try:
            text, status, source = await summarizer.summarize_story(story)
            db.set_summary(story_id, text, status, source)
            if status == "done":
                _summary_stats["done"] += 1
                _summary_stats["last_error"] = None
        except Exception as e:
            _summary_stats["last_error"] = str(e)
            db.set_summary(story_id, "Summary error.", "failed", "none")
    return db.get_story(story_id)


async def refresh_stories(limit: int = 50):
    stories = await hn.fetch_top_stories(limit=limit)
    for rank, story in enumerate(stories):
        db.upsert_story(story, rank)
    return len(stories)


# --- API -----------------------------------------------------------------------
@app.get("/api/feed")
async def api_feed(limit: int = 50, offset: int = 0):
    return {"stories": db.get_feed(limit=limit, offset=offset), "counts": db.counts()}


@app.get("/api/list/{state}")
async def api_list(state: str):
    if state not in ("read", "hidden", "new", "saved"):
        raise HTTPException(400, "state must be new, read, hidden, or saved")
    return {"stories": db.get_by_state(state)}


@app.post("/api/summarize/{story_id}")
async def api_summarize(story_id: int):
    """Generate (or fetch) a single story's summary on demand."""
    story = await generate_summary(story_id)
    if not story:
        raise HTTPException(404, "story not found")
    return {
        "id": story["id"],
        "summary": story["summary"],
        "summary_status": story["summary_status"],
        "summary_source": story.get("summary_source", ""),
    }


@app.post("/api/refresh")
async def api_refresh(limit: int = 50):
    n = await refresh_stories(limit=limit)
    return {"fetched": n, "counts": db.counts()}


@app.post("/api/story/{story_id}/{action}")
async def api_action(story_id: int, action: str):
    mapping = {
        "read": "read",
        "hide": "hidden",
        "save": "saved",
        "unhide": "new",
        "restore": "new",
    }
    if action not in mapping:
        raise HTTPException(400, "action must be read, hide, save, unhide, or restore")
    changed = db.set_state(story_id, mapping[action])
    if not changed:
        raise HTTPException(404, "story not found")
    return {"ok": True, "counts": db.counts()}


@app.get("/api/status")
async def api_status():
    return {
        "worker": _summary_stats,
        "counts": db.counts(),
        "ollama": await summarizer.ollama_available(),
        "model": summarizer.MODEL,
    }


# --- keyword filters -----------------------------------------------------------
@app.get("/api/filters")
async def api_filters_list():
    return {"filters": db.list_filters(), "counts": db.counts()}


@app.post("/api/filters")
async def api_filters_add(payload: dict):
    keyword = (payload or {}).get("keyword", "")
    if not db.add_filter(keyword):
        raise HTTPException(400, "keyword is required")
    return {"filters": db.list_filters(), "counts": db.counts()}


@app.delete("/api/filters/{keyword}")
async def api_filters_remove(keyword: str):
    db.remove_filter(keyword)
    return {"filters": db.list_filters(), "counts": db.counts()}


# --- static frontend -----------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
