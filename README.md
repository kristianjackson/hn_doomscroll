# 🍊 HN Doom-Scroll

A local dashboard that turns the Hacker News front page into a doom-scroll feed
with AI-generated summaries of the actual articles. Mark stories as **read** or
**not interested**, and they never show up again.

Everything runs on your machine. SQLite for storage, your local Ollama model for
summaries. No external services beyond fetching HN itself and the article pages.

## Stack

- **Backend:** Python + FastAPI
- **Storage:** SQLite (`hn.db`, created automatically)
- **Summaries:** local LLM via [Ollama](https://ollama.com) — model `llama3.2:3b`
- **Article extraction:** `trafilatura`
- **Frontend:** vanilla HTML/CSS/JS (no build step), infinite scroll

## Quick start

1. Make sure Ollama is running and the model is pulled:
   ```
   ollama pull llama3.2:3b
   ```
2. Double-click **`run.bat`** (or run it from a terminal).
   - First run sets up the virtualenv and installs dependencies.
   - Your browser opens to http://localhost:8000.

That's it. The app fetches the top 50 HN stories on first boot and starts
summarizing them in the background.

## How it works

- On startup it pulls the HN top stories and stores them in SQLite.
- A background worker summarizes one article at a time (serial, to keep the
  CPU-bound local model responsive). Cards show "summarizing locally…" until
  their summary lands, then update in place.
- **✓ Read** and **✕ Not interested** both remove a story from the feed and
  remember the choice. Hidden/read stories never reappear in the feed.
- The **Read** and **Hidden** tabs let you review past choices and send a story
  back to the feed if you change your mind.
- **↻ Refresh** pulls the latest front page. Already-seen stories keep their
  state; only genuinely new stories get added.

## Settings

Click the **⚙ gear** in the header to open Settings. Everything here is
remembered between sessions.

### Appearance
Three themes: **Light**, **Dark**, and **System**. Light is the default. System
follows your OS setting and switches automatically if your device is in dark
mode. Your choice is saved in the browser and applied before the page paints, so
there's no flash of the wrong theme on reload.

### Auto-refresh
Toggle on to automatically pull the latest front page on a timer. Pick an
interval (5, 10, 15, 30, or 60 minutes). Default is 15. The setting is saved in
the browser, so it persists across reloads.

### Feed size
Choose how many top stories to pull from Hacker News on each refresh (25, 50,
75, 100, 150, or 200). Default is 50. Larger sizes mean more to scroll but also
more articles for the local model to summarize.

### Keyword filters
Hide stories you don't care about. Add a word (e.g. `crypto`, `layoffs`) and any
story whose **title or summary** contains it disappears from the feed — both
stories already loaded and any pulled in future refreshes. Filters show as
removable chips; remove one and those stories come back. The status bar shows a
"N filtered" count when filters are active.

Filters are stored server-side in SQLite, so they apply globally and survive
restarts. Matching is case-insensitive substring matching at query time, so
toggling is instant.

## Changing the model

Edit `MODEL` at the top of `summarizer.py`. Good options for a CPU-only machine:

| Model | Notes |
|-------|-------|
| `llama3.2:3b` | Default. Fast, solid 2-3 sentence summaries. |
| `qwen2.5:7b-instruct` | Higher quality, noticeably slower on CPU. |
| `llama3.2:1b` | Fastest, lower quality. Good if 3b feels sluggish. |

After changing it, run `ollama pull <model>` once.

## Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI app, routes, background summary worker |
| `hn.py` | Hacker News API client |
| `summarizer.py` | Article extraction + Ollama summarization |
| `db.py` | SQLite schema and queries |
| `static/` | Frontend (index.html, style.css, app.js) |
| `run.bat` | One-click launcher |

## Notes

- Paywalled, PDF, or heavily-JS pages can't always be extracted; those cards say
  so and link straight to the article.
- Ask HN / Show HN text posts have no external article, so they're labeled as
  discussion threads.
- The status pill in the header shows a green dot when Ollama is reachable, red
  when it isn't.
