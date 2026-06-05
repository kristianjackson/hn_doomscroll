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

# Tracks a running "re-embed all" job so the UI can show progress.
_reembed_state = {"running": False, "done": 0, "total": 0}


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    # Restore any saved model choices (server-side setting).
    summarizer.set_models(
        model=db.get_setting("model"),
        embed_model=db.get_setting("embed_model"),
    )
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
            # Embed the title + summary for semantic search (best-effort).
            if status in ("done", "skipped"):
                await _embed_story(story_id, story.get("title", ""), text)
        except Exception as e:
            _summary_stats["last_error"] = str(e)
            db.set_summary(story_id, "Summary error.", "failed", "none")
    return db.get_story(story_id)


async def _embed_story(story_id: int, title: str, summary: str):
    """Compute and store an embedding for a story (no-op if model absent)."""
    import json
    vec = await summarizer.embed_text(f"{title}\n\n{summary}")
    if vec:
        db.set_embedding(story_id, json.dumps(vec))


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


@app.get("/api/search")
async def api_search(q: str = ""):
    """Search all stored stories (any state) by title or summary text."""
    return {"stories": db.search(q), "query": q}


@app.get("/api/semantic-search")
async def api_semantic_search(q: str = ""):
    """Rank stored stories by semantic similarity to the query.

    Embeds the query with the local embedding model and compares against
    per-story embeddings (cosine). Backfills a bounded number of missing
    embeddings on the fly. Falls back to keyword search if the embedding
    model isn't available.
    """
    import json
    query = (q or "").strip()
    if not query:
        return {"stories": [], "query": q, "mode": "semantic"}

    if not await summarizer.embed_model_available():
        # Graceful fallback so search still works without the embed model.
        return {"stories": db.search(query), "query": q, "mode": "keyword-fallback"}

    # Backfill embeddings for a bounded batch of summarized-but-unembedded
    # stories so results aren't limited to only what you've already viewed.
    async with _summary_lock:
        for s in db.stories_missing_embedding(limit=40):
            await _embed_story(s["id"], s.get("title", ""), s.get("summary", ""))

    qvec = await summarizer.embed_text(query)
    if not qvec:
        return {"stories": db.search(query), "query": q, "mode": "keyword-fallback"}

    scored = []
    for sid, emb_json in db.get_embeddings(limit=1000):
        try:
            vec = json.loads(emb_json)
        except Exception:
            continue
        sim = summarizer.cosine_similarity(qvec, vec)
        if sim > 0.4:  # drop weak matches
            scored.append((sid, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    top_ids = [sid for sid, _ in scored[:60]]
    stories = db.get_stories_by_ids(top_ids)
    sim_by_id = dict(scored)
    for s in stories:
        s["similarity"] = round(sim_by_id.get(s["id"], 0), 3)
    return {"stories": stories, "query": q, "mode": "semantic"}


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


# --- model selection -----------------------------------------------------------
@app.get("/api/models")
async def api_models():
    """List installed Ollama models and which ones are currently active."""
    installed = await summarizer.list_installed_models()
    return {
        "installed": installed,
        "model": summarizer.MODEL,
        "embed_model": summarizer.EMBED_MODEL,
        "default_model": summarizer.DEFAULT_MODEL,
        "default_embed_model": summarizer.DEFAULT_EMBED_MODEL,
        "ollama": await summarizer.ollama_available(),
    }


@app.post("/api/models")
async def api_set_models(payload: dict):
    """Set the active summary and/or embedding model. Persists in SQLite."""
    payload = payload or {}
    model = (payload.get("model") or "").strip() or None
    embed_model = (payload.get("embed_model") or "").strip() or None
    if not model and not embed_model:
        raise HTTPException(400, "provide model and/or embed_model")

    # Only accept models that are actually installed.
    installed = set(await summarizer.list_installed_models())
    if model and model not in installed:
        raise HTTPException(400, f"model '{model}' is not installed in Ollama")
    if embed_model and embed_model not in installed:
        raise HTTPException(400, f"model '{embed_model}' is not installed in Ollama")

    summarizer.set_models(model=model, embed_model=embed_model)
    if model:
        db.set_setting("model", model)
    if embed_model:
        db.set_setting("embed_model", embed_model)
    return {"ok": True, "model": summarizer.MODEL, "embed_model": summarizer.EMBED_MODEL}


async def _reembed_worker():
    """Clear and regenerate all embeddings with the current embedding model."""
    import json
    _reembed_state["running"] = True
    try:
        db.clear_all_embeddings()
        # Pull everything that has a summary and re-embed it in batches.
        while True:
            batch = db.stories_missing_embedding(limit=20)
            if not batch:
                break
            for s in batch:
                async with _summary_lock:
                    vec = await summarizer.embed_text(
                        f"{s.get('title','')}\n\n{s.get('summary','')}"
                    )
                    if vec:
                        db.set_embedding(s["id"], json.dumps(vec))
                    else:
                        # Embed model unavailable — stop rather than spin.
                        return
                _reembed_state["done"] += 1
    finally:
        _reembed_state["running"] = False


@app.post("/api/reembed")
async def api_reembed():
    """Optionally re-embed every summarized story with the current model.

    Useful after switching the embedding model so all vectors are comparable.
    Runs in the background; poll /api/reembed for progress.
    """
    if _reembed_state["running"]:
        raise HTTPException(409, "a re-embed job is already running")
    if not await summarizer.embed_model_available():
        raise HTTPException(400, "embedding model not available in Ollama")
    _reembed_state.update({"running": True, "done": 0, "total": db.count_embeddable()})
    asyncio.create_task(_reembed_worker())
    return {"started": True, "total": _reembed_state["total"]}


@app.get("/api/reembed")
async def api_reembed_status():
    return _reembed_state


# --- static frontend -----------------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
