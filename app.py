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

# --- background summary worker -------------------------------------------------
_worker_status = {"running": False, "last_error": None, "done": 0}


async def summary_worker():
    """Continuously turn 'pending' stories into summaries, one at a time.

    Serial on purpose: a CPU-bound local model is happier without concurrent
    generation requests fighting over cores.
    """
    _worker_status["running"] = True
    while True:
        batch = db.stories_needing_summary(limit=10)
        if not batch:
            await asyncio.sleep(5)
            continue
        # Don't burn through the queue if the model server is down — wait for it.
        if not await summarizer.ollama_available():
            _worker_status["last_error"] = "Ollama unreachable; waiting…"
            await asyncio.sleep(10)
            continue
        for story in batch:
            try:
                text, status = await summarizer.summarize_story(story)
                db.set_summary(story["id"], text, status)
                if status == "done":
                    _worker_status["done"] += 1
                    _worker_status["last_error"] = None
                elif status == "pending":
                    # Model not ready yet; pause so we don't hot-loop.
                    _worker_status["last_error"] = "Model warming up…"
                    await asyncio.sleep(8)
            except Exception as e:  # keep the worker alive no matter what
                _worker_status["last_error"] = str(e)
                db.set_summary(story["id"], "Summary error.", "failed")
            await asyncio.sleep(0.2)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    task = asyncio.create_task(summary_worker())
    # Pull stories on first boot if the DB is empty.
    if db.counts()["new"] == 0 and db.counts()["read"] == 0:
        with contextlib.suppress(Exception):
            await refresh_stories(limit=50)
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


app = FastAPI(title="HN Doom-Scroll", lifespan=lifespan)


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
    if state not in ("read", "hidden", "new"):
        raise HTTPException(400, "state must be new, read, or hidden")
    return {"stories": db.get_by_state(state)}


@app.post("/api/refresh")
async def api_refresh(limit: int = 50):
    n = await refresh_stories(limit=limit)
    return {"fetched": n, "counts": db.counts()}


@app.post("/api/story/{story_id}/{action}")
async def api_action(story_id: int, action: str):
    mapping = {"read": "read", "hide": "hidden", "unhide": "new", "restore": "new"}
    if action not in mapping:
        raise HTTPException(400, "action must be read, hide, unhide, or restore")
    changed = db.set_state(story_id, mapping[action])
    if not changed:
        raise HTTPException(404, "story not found")
    return {"ok": True, "counts": db.counts()}


@app.get("/api/status")
async def api_status():
    return {
        "worker": _worker_status,
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
