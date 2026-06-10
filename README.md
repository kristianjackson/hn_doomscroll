# 🍊 HN Doom-Scroll

A personal Hacker News reader that turns the front page into a doom-scroll feed
with AI-generated article summaries. Mark stories as **read**, **save** them for
later, or flag them **not interested** — and they leave the feed. Search
everything you've seen by keyword or meaning.

SQLite for storage. Summaries via AWS Bedrock (default) or a local Ollama model.
Semantic search via a local embedding model. No accounts, no tracking, no ads.

## Screenshots

| Light | Dark |
|-------|------|
| ![Feed, light theme](docs/feed-light.png) | ![Feed, dark theme](docs/feed-dark.png) |

Settings — themes, auto-refresh, feed size, model selection, and keyword filters:

![Settings panel](docs/settings.png)

Semantic search ranks results by meaning, with a match score on each:

![Semantic search results](docs/search-semantic.png)

> Regenerate screenshots: `python scripts/capture_screenshots.py` (requires a
> running instance and Playwright Chromium).

## Stack

| Layer | Tech |
|-------|------|
| Backend | Python + FastAPI |
| Storage | SQLite (`hn.db`, auto-created) |
| Summaries | AWS Bedrock (`google.gemma-3-4b-it`, default) or local Ollama (`llama3.2:3b`) |
| Embeddings | Local Ollama (`nomic-embed-text`) for semantic search |
| Article extraction | `trafilatura` + Playwright headless Chromium fallback |
| Frontend | Vanilla HTML/CSS/JS, infinite scroll, no build step |

## Quick start

### Option A — AWS Bedrock (default, fast)

1. Configure AWS credentials with Bedrock access (`aws configure` or env vars).
2. Double-click **`run.bat`** (or run from a terminal).
3. Browser opens to http://localhost:8000.

No model downloads required. Summaries generate in ~1 second per story.

### Option B — Local Ollama (fully offline)

1. Install and start [Ollama](https://ollama.com).
2. Pull models:
   ```
   ollama pull llama3.2:3b
   ollama pull nomic-embed-text   # optional — enables semantic search
   ```
3. Set the env var before launching:
   ```
   set HN_PROVIDER=ollama
   run.bat
   ```

Summaries are slower (CPU-bound) but everything stays on your machine.

### First run

`run.bat` creates the virtualenv, installs dependencies (including `boto3` for
Bedrock), and downloads Playwright Chromium (~150 MB, one-time). Playwright is
optional — skip it and the app falls back to direct fetch + HN discussion
summaries for JS-heavy pages.

## How it works

- Fetches HN top stories on startup and stores them in SQLite.
- **On-demand summaries.** As each card scrolls near the viewport, the app calls
  the configured AI provider to summarize that article. The header bar shows a
  running count of pending summaries. Cards display "⏳ Summarizing…" until
  their summary arrives, then update in place.
- **Article extraction is layered.** For each story: (1) direct fetch with
  browser headers, (2) headless Chromium render for JS-heavy pages, (3) fallback
  that summarizes the HN discussion. Badges show the source — 💬 From HN
  discussion, 🌐 Rendered page — or why it couldn't be read — 🔒 Paywalled,
  📄 PDF, 🎥 Video.
- **Crash recovery.** If the server is killed mid-summarization, stuck pending
  stories are automatically reset on the next startup.
- **✓ Read / ★ Save / ✕ Not interested** — each moves a story out of the feed.
  Stories never reappear once acted on.
- **Learns from what you skip.** After 10+ hidden stories, recurring patterns
  (words, domains) down-rank similar new stories to the bottom of the feed,
  dimmed with a reason. Nothing is hidden outright.
- **Search** across everything (feed, read, saved, hidden) by keyword or
  semantic meaning. Toggle kw/ai in the search box. Semantic search uses local
  embeddings via Ollama.
- **Auto-refresh** pulls the latest front page on a timer. The refresh button
  doubles as a countdown.

## Settings

Click **⚙** in the header. Everything persists between sessions.

- **Themes:** Light, Dark, or System (follows OS preference).
- **Auto-refresh:** 5–60 min interval, countdown in the refresh button.
- **Feed size:** 25–200 top stories per refresh.
- **Keyword filters:** hide stories containing specific words.
- **Models:** (Ollama mode) pick summary + embedding models from installed list.
- **Re-embed all:** regenerate all stored embeddings after switching models.

## Provider configuration

| Env var | Values | Default |
|---------|--------|---------|
| `HN_PROVIDER` | `bedrock` or `ollama` | `bedrock` |
| `BEDROCK_REGION` | AWS region | `us-east-1` |
| `BEDROCK_REASON_MODEL` | Bedrock model ID | `google.gemma-3-4b-it` |

In Ollama mode, models are selected in-app via Settings → Models.

## Files

| File | Purpose |
|------|---------|
| `app.py` | FastAPI app, routes, on-demand summary + embedding generation |
| `hn.py` | Hacker News API client |
| `summarizer.py` | Article extraction, summarization (Bedrock + Ollama), embeddings |
| `db.py` | SQLite schema, queries, search, dislike-learning |
| `static/` | Frontend (`index.html`, `style.css`, `app.js`) |
| `scripts/capture_screenshots.py` | Screenshot generator for README |
| `docs/` | README screenshots |
| `run.bat` | One-click launcher |
| `push-to-github.bat` | Publishes to standalone GitHub repo |
| `requirements.txt` | Python dependencies |
| `CHANGELOG.md` | Version history |
| `LICENSE` | MIT |

## Notes

- The status indicator in the header shows green when the provider is reachable,
  red when it isn't.
- Bedrock mode runs summaries concurrently (fast). Ollama mode serializes them
  (one at a time, to avoid overloading the local model).
- Semantic search requires the `nomic-embed-text` model in Ollama. If it's not
  installed, search falls back to keyword automatically.
- Ask HN / Show HN text posts are summarized from the post + discussion directly.
