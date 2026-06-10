# Changelog

All notable changes to HN Doom-Scroll are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

## [1.2.1] - 2026-06-10

### Added
- **Bulk-hide dimmed stories.** A "✕ Hide all dimmed stories" banner appears
  in the feed between your normal stories and the down-ranked ones. One click
  moves all dimmed stories to Hidden at once — no need to dismiss them
  individually.
- **`POST /api/hide-downranked` endpoint** for bulk-hiding all currently
  down-ranked stories in a single request.

## [1.2.0] - 2026-06-10

### Changed
- **Semantic search is faster.** Three performance optimizations:
  1. **Background embedding backfill.** Previously, every semantic search
     request blocked while embedding up to 40 unembedded stories. Now backfill
     runs in the background after summaries complete — search returns instantly
     using whatever embeddings already exist.
  2. **Query embedding cache.** A 10-second TTL cache deduplicates redundant
     Bedrock API calls when you're typing (debounce fires multiple searches).
  3. **Numpy-accelerated cosine similarity.** Vector comparison uses numpy when
     available (installed by default), falling back to pure Python.
- **Cached boto3 client.** The Bedrock client is reused across calls instead of
  being re-created per request (~2s cold-start eliminated after first call).
- **Lowered similarity threshold** from 0.4 to 0.1 to match Titan Embed v2's
  tighter score distribution (previously returned 0 results for most queries).

### Added
- `numpy` added to requirements.txt for vector math acceleration.

## [1.1.1] - 2026-06-10

### Added
- **Comma-separated search.** Type multiple terms separated by commas (e.g.
  "react, vue, angular") to find stories matching any of those terms. Works
  in both keyword and semantic search modes.
- **Clear button** next to the kw/ai toggle — a visible "clear" pill appears
  when a search is active, providing a second way to exit search (alongside
  the ✕ and Escape key).

### Fixed
- **Tab clicks clear search.** Clicking Feed, Saved, Read, or Hidden while a
  search is active now clears the search input and returns to that tab view.

### Changed
- Search placeholder updated to: "Search everything… comma-separate multiple
  terms" to hint at the multi-term feature.
- Keyword search backend refactored to support OR-matching across comma-
  separated terms with title-match ranking preserved.
- Semantic search embeds each comma-separated term independently and takes the
  max similarity score across terms for ranking.

## [1.1.0] - 2026-06-10

### Added
- **In-app provider toggle.** Switch between ☁️ Bedrock and 💻 Ollama directly
  in Settings without restarting the server or setting env vars.
- **Bedrock model selection UI.** Curated dropdowns for both summarization
  (Claude 3 Haiku, Gemma 3 4B, Llama 3 8B, Nova Micro) and embeddings (Titan
  Embed v2, Cohere Embed English v3, Cohere Embed v4).
- **Live provider status** indicator in Settings showing connection health.
- **`POST /api/provider` endpoint** for switching providers and models at
  runtime; choices persist across restarts.
- **Provider settings screenshot** added to README.

### Changed
- Provider and model choices are now stored in SQLite and restored on startup
  (previously only Ollama model names were persisted).
- Settings UI restructured: provider toggle at the top, with Bedrock and Ollama
  model sections shown/hidden based on active provider.
- `BEDROCK_EMBED_MODEL` is now configurable via env var (was hardcoded).

## [1.0.3] - 2026-06-10

### Fixed
- **README accuracy:** Corrected all claims that semantic search is "always
  local" or "requires Ollama." Embeddings follow the same provider as
  summaries — Bedrock Titan Embed v2 by default, Ollama `nomic-embed-text` in
  offline mode.
- **Provider config table:** Added `BEDROCK_EMBED_MODEL` env var documentation.
- **Settings description:** Clarified that model selection UI applies to Ollama
  mode; Bedrock models are configured via env vars.
- **Consistency pass:** Aligned README, CHANGELOG, and local steering file to
  match actual code behavior across all provider references.

## [1.0.2] - 2026-06-10

### Changed
- **README rewritten.** Reflects dual-provider architecture (Bedrock default,
  Ollama optional). Removed outdated "everything runs locally" language. Added
  provider configuration table, separate quick-start paths for Bedrock and
  Ollama, and updated feature descriptions.
- **Screenshots regenerated** with current UI (header queue count, updated card
  styling).

## [1.0.1] - 2026-06-10

### Fixed
- **Bedrock provider support:** added `boto3` to requirements.txt so Bedrock
  summarization works out of the box. Previously, a fresh venv would silently
  fall back to "provider unreachable" because `boto3` wasn't installed.
- **Crash recovery for stuck summaries:** on startup, any stories left in
  `pending` status from a previous crash/kill are now reset so they re-queue
  automatically when scrolled into view.
- **Stuck pending cards retry:** visible cards that remain in `pending` state
  now auto-retry every 2 seconds instead of staying frozen until a page reload.

### Changed
- **Summary queue count moved to header bar:** the status line now shows
  "⏳ N summarizing" alongside story counts, replacing the per-card queue
  position text. Cards just show a clean "⏳ Summarizing…" spinner.
- **Status bar widened** from 260px to 380px max-width to prevent the
  summarizing count from being truncated.

## [0.1.1] - 2026-06-05

### Added
- **Model selection in Settings.** A new Models section lists the Ollama models
  installed on your machine (polled live) and lets you pick the summary model
  and the semantic-search embedding model from dropdowns. The choice is saved
  server-side in SQLite and persists across restarts; only installed models are
  selectable.
- **Re-embed all stories** — an optional action in the Models section that
  clears and regenerates every stored embedding with the current embedding
  model (useful after switching it). Runs in the background with live progress;
  semantic search keeps working meanwhile.

## [0.1.0] - 2026-06-05

First working version: a local Hacker News reader with AI summaries, built and
iterated on in a single session.

### Added
- **Doom-scroll feed** of the Hacker News front page with infinite scroll,
  backed by SQLite and a FastAPI server.
- **AI article summaries** generated by a local Ollama model (`llama3.2:3b`),
  with article text extracted via `trafilatura`.
- **On-demand summarization** — summaries are generated as cards scroll into
  view (serialized so the CPU-bound model runs one at a time), instead of an
  upfront batch. Stories you never reach cost no compute.
- **Read / Save / Not-interested** actions. Read, saved, and hidden stories
  leave the feed and are remembered; **Saved**, **Read**, and **Hidden** tabs
  let you review and restore them.
- **Layered article extraction:** direct fetch with browser headers → Playwright
  headless-Chromium render → Hacker News discussion fallback. Per-card badges
  show the summary source (discussion, rendered) or why it couldn't be read
  (paywall, PDF, video).
- **Learns from what you skip** — once you've hidden 10+ stories, recurring
  terms and domains down-rank similar new stories to the bottom of the feed
  (dimmed, with a reason), rather than hiding them outright.
- **Keyword filters** — hide stories whose title or summary contains a word;
  stored server-side, applied across the whole feed.
- **Search** across everything you've seen (feed, read, saved, hidden):
  - Keyword search, ranked by relevance (title matches above summary-only).
  - **Semantic search** via a local embedding model (`nomic-embed-text`) that
    ranks by meaning, with a match-percentage badge on each result. Falls back
    to keyword search if the embedding model isn't installed.
  - Results badged with where each story currently lives.
- **Settings panel:** Light / Dark / System themes, auto-refresh with a
  configurable interval, configurable feed size, and keyword-filter management.
- **Auto-refresh countdown** merged into the Refresh button — it shows time to
  the next pull and refreshes on click.
- **One-click launcher** (`run.bat`) that sets up the virtualenv, installs
  dependencies, and downloads the Playwright browser on first run.
- **Tooling:** reusable screenshot capture script (`scripts/capture_screenshots.py`),
  a GitHub publish helper (`push-to-github.bat`), MIT license, and README
  screenshots.

### Notes
- Everything runs locally — no external services beyond fetching Hacker News
  and the article pages themselves.
- Playwright and the `nomic-embed-text` model are both optional; the app
  degrades gracefully without them.

[1.2.1]: https://github.com/kristianjackson/hn_doomscroll/releases/tag/v1.2.1
[1.2.0]: https://github.com/kristianjackson/hn_doomscroll/releases/tag/v1.2.0
[1.1.1]: https://github.com/kristianjackson/hn_doomscroll/releases/tag/v1.1.1
[1.1.0]: https://github.com/kristianjackson/hn_doomscroll/releases/tag/v1.1.0
[1.0.3]: https://github.com/kristianjackson/hn_doomscroll/releases/tag/v1.0.3
[1.0.2]: https://github.com/kristianjackson/hn_doomscroll/releases/tag/v1.0.2
[1.0.1]: https://github.com/kristianjackson/hn_doomscroll/releases/tag/v1.0.1
[0.1.1]: https://github.com/kristianjackson/hn_doomscroll/releases/tag/v0.1.1
[0.1.0]: https://github.com/kristianjackson/hn_doomscroll/releases/tag/v0.1.0
