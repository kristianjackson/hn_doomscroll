# 🍊 HN Doom-Scroll

A local dashboard that turns the Hacker News front page into a doom-scroll feed
with AI-generated summaries of the actual articles. Mark stories as **read** or
**not interested**, and they never show up again.

Everything runs on your machine. SQLite for storage, your local Ollama model for
summaries. No external services beyond fetching HN itself and the article pages.

## Screenshots

The feed, in light and dark themes:

| Light | Dark |
|-------|------|
| ![Feed, light theme](docs/feed-light.png) | ![Feed, dark theme](docs/feed-dark.png) |

Settings — themes, auto-refresh, feed size, and keyword filters:

![Settings panel](docs/settings.png)

Semantic search ranks results by meaning, with a match score on each:

![Semantic search results](docs/search-semantic.png)

> Regenerate these anytime with the app running: `python scripts/capture_screenshots.py`
> (uses Playwright; set `SHOT_DELAY_MS` higher if summaries need longer to fill in).

## Stack

- **Backend:** Python + FastAPI
- **Storage:** SQLite (`hn.db`, created automatically)
- **Summaries:** local LLM via [Ollama](https://ollama.com) — model `llama3.2:3b`
- **Article extraction:** `trafilatura`, with a `playwright` headless-Chromium
  fallback for JS-heavy pages
- **Frontend:** vanilla HTML/CSS/JS (no build step), infinite scroll

## Quick start

1. Make sure Ollama is running and the models are pulled:
   ```
   ollama pull llama3.2:3b
   ollama pull nomic-embed-text   # optional — enables semantic search
   ```
2. Double-click **`run.bat`** (or run it from a terminal).
   - First run sets up the virtualenv, installs dependencies, and downloads the
     Playwright Chromium browser (~150 MB, one time).
   - Your browser opens to http://localhost:8000.

Playwright is optional — if the Chromium download fails or you skip it, the app
still works; it just falls back to direct fetch and the HN-discussion summary
for JS-heavy pages instead of rendering them.

That's it. The app fetches the top 50 HN stories on first boot. Summaries are
generated on demand as you scroll, using your local model.

## How it works

- On startup it pulls the HN top stories and stores them in SQLite.
- **Summaries are generated on demand.** As each card scrolls near the viewport,
  the app asks the local model to summarize that article — so you see summaries
  for what you're actually reading within seconds, and stories you never scroll
  to (or hide) cost no compute. Generation is serialized so the CPU-bound model
  handles one at a time. Cards show "summarizing locally…" until their summary
  lands, then update in place.
- **Article extraction is layered.** For each story the app tries, in order:
  (1) a direct fetch with realistic browser headers, (2) a headless-Chromium
  render via Playwright for JS-heavy pages, and (3) a fallback that summarizes
  the **Hacker News discussion** when the article itself can't be retrieved.
  A small badge shows where a summary came from — 💬 From HN discussion,
  🌐 Rendered page — or why it couldn't be read — 🔒 Paywalled, 📄 PDF, 🎥 Video.
- **✓ Read**, **★ Save**, and **✕ Not interested** each move a story out of the
  feed and remember the choice. Read/saved/hidden stories never reappear in the
  feed.
- **It learns from what you skip.** Once you've hidden 10+ stories, the app
  notices recurring words and domains in them and gently **down-ranks** similar
  new stories — they sink to the bottom of the feed, dimmed, with a note like
  "↓ You usually skip stories like this (crypto, coindesk.com)". Nothing is
  hidden outright, so you stay in control; hover to un-dim. It only acts on
  patterns it's seen at least twice, so a one-off skip won't bury anything.
- The **★ Saved**, **Read**, and **Hidden** tabs let you review past choices.
  Saved is your read-it-later list; from any of these tabs you can send a story
  back to the feed (or, from Saved, mark it read).
- **Search** (the box in the header) finds anything you've seen — across the
  feed, read, saved, and hidden — by title or summary text. So when you remember
  reading something but not where, just type a keyword. Each result shows a badge
  for where it currently lives (In feed / Read / Saved / Hidden). Press Escape or
  the ✕ to return to where you were. Toggle the **kw/ai** button to switch
  between keyword (exact text) and **semantic** search — semantic finds stories
  by *meaning*, so "machine learning" surfaces AI stories that never use those
  exact words. Semantic search uses a local embedding model (`nomic-embed-text`);
  if it isn't installed, search falls back to keyword automatically.
- The **↻ Refresh** button pulls the latest front page. Already-seen stories
  keep their state; only genuinely new stories get added. When auto-refresh is
  on, this button doubles as a live countdown to the next refresh (see below).

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

When auto-refresh is on, the header's **↻ Refresh** button turns into a live
countdown (`↻ 4:59`) showing time until the next pull. Clicking it refreshes
immediately and resets the timer. There's no separate timer or button — one
control does both.

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
