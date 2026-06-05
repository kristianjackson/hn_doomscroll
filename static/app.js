const feedEl = document.getElementById("feed");
const loaderEl = document.getElementById("loader");
const emptyEl = document.getElementById("empty");
const statusEl = document.getElementById("status");
const refreshBtn = document.getElementById("refresh");
const sentinel = document.getElementById("sentinel");

let view = "feed";          // feed | saved | read | hidden | search
let offset = 0;
const PAGE = 20;
let searchQuery = "";       // active query when view === "search"
let prevView = "feed";      // view to restore when search is cleared
let searchMode = localStorage.getItem("searchMode") || "keyword"; // keyword | semantic
let searchModeNote = "";
let loading = false;
let exhausted = false;

function domain(url) {
  if (!url) return "news.ycombinator.com";
  try { return new URL(url).hostname.replace(/^www\./, ""); }
  catch { return ""; }
}

function timeAgo(unix) {
  if (!unix) return "";
  const s = Math.floor(Date.now() / 1000) - unix;
  const h = Math.floor(s / 3600);
  if (h < 1) return `${Math.floor(s / 60)}m ago`;
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function sourceBadge(s) {
  const map = {
    discussion: '<span class="badge badge-info">💬 From HN discussion</span>',
    rendered: '<span class="badge badge-info">🌐 Rendered page</span>',
    pdf: '<span class="badge badge-warn">📄 PDF</span>',
    video: '<span class="badge badge-warn">🎥 Video</span>',
    paywall: '<span class="badge badge-warn">🔒 Paywalled</span>',
  };
  return map[s] || "";
}

function summaryBlock(s) {
  const badge = sourceBadge(s.summary_source);
  if (s.summary_status === "done" || s.summary_status === "skipped")
    return `<p class="summary">${badge}${escapeHtml(s.summary)}</p>`;
  if (s.summary_status === "failed")
    return `<p class="summary failed">${badge}${escapeHtml(s.summary || "No summary.")}</p>`;
  return `<p class="summary pending">⏳ summarizing locally…</p>`;
}

function escapeHtml(t) {
  const d = document.createElement("div");
  d.textContent = t || "";
  return d.innerHTML;
}

function stateBadge(s) {
  // Shown in search results so you know where each story currently lives.
  const map = {
    new: '<span class="badge badge-state">In feed</span>',
    read: '<span class="badge badge-state">✓ Read</span>',
    saved: '<span class="badge badge-state">★ Saved</span>',
    hidden: '<span class="badge badge-state">✕ Hidden</span>',
  };
  return map[s.state] || "";
}

function cardHtml(s) {
  const link = s.url || s.hn_url;
  let actions;
  if (view === "feed") {
    actions = `<button class="btn read" data-act="read">✓ Read</button>
       <button class="btn save" data-act="save">★ Save</button>
       <button class="btn hide" data-act="hide">✕ Not interested</button>`;
  } else if (view === "saved") {
    actions = `<button class="btn read" data-act="read">✓ Mark read</button>
       <button class="btn" data-act="restore">↩ Back to feed</button>`;
  } else if (view === "search") {
    // Offer actions appropriate to where the story currently lives.
    actions = `<button class="btn save" data-act="save">★ Save</button>
       <button class="btn" data-act="restore">↩ To feed</button>`;
  } else {
    actions = `<button class="btn save" data-act="save">★ Save</button>
       <button class="btn" data-act="restore">↩ Back to feed</button>`;
  }
  const downranked = view === "feed" && s.downranked;
  const reasons = (s.downrank_reasons || []).map(escapeHtml).join(", ");
  const skipNote = downranked
    ? `<div class="skip-note">↓ You usually skip stories like this${reasons ? ` (${reasons})` : ""}</div>`
    : "";
  const stBadge = view === "search" ? stateBadge(s) : "";
  return `
    <article class="card${downranked ? " downranked" : ""}" data-id="${s.id}" data-summary-status="${s.summary_status}">
      ${skipNote}
      <div class="meta">
        <span class="score">▲ ${s.score}</span>
        <span class="domain">${domain(s.url)}</span>
        <span>${s.num_comments} comments</span>
        <span>${timeAgo(s.posted_at)}</span>
        ${stBadge}
      </div>
      <h2><a href="${link}" target="_blank" rel="noopener">${escapeHtml(s.title)}</a></h2>
      ${summaryBlock(s)}
      <div class="row">
        ${actions}
        <a class="btn-link spacer" href="${s.hn_url}" target="_blank" rel="noopener">💬 Discussion</a>
        <a class="btn-link" href="${link}" target="_blank" rel="noopener">Open article →</a>
      </div>
    </article>`;
}

async function loadMore() {
  if (loading || exhausted) return;
  loading = true;
  loaderEl.classList.remove("hidden");

  let stories = [];
  if (view === "feed") {
    const r = await fetch(`/api/feed?limit=${PAGE}&offset=${offset}`);
    const data = await r.json();
    stories = data.stories;
    updateStatus(data.counts);
  } else if (view === "search") {
    const endpoint = searchMode === "semantic" ? "semantic-search" : "search";
    const r = await fetch(`/api/${endpoint}?q=${encodeURIComponent(searchQuery)}`);
    const data = await r.json();
    stories = data.stories;
    searchModeNote = data.mode === "keyword-fallback"
      ? " (semantic unavailable — showing keyword matches)" : "";
    exhausted = true; // search returns all matches at once
  } else {
    const r = await fetch(`/api/list/${view}`);
    stories = (await r.json()).stories;
    exhausted = true; // list views return everything at once
  }

  if (stories.length === 0 && offset === 0) {
    if (view === "feed") {
      emptyEl.textContent = "All caught up. Hit Refresh to pull the latest from HN.";
    } else if (view === "search") {
      emptyEl.textContent = `No stories match "${searchQuery}"${searchModeNote}.`;
    } else {
      emptyEl.textContent = `Nothing in ${view} yet.`;
    }
    emptyEl.classList.remove("hidden");
  }
  if (stories.length < PAGE) exhausted = true;

  feedEl.insertAdjacentHTML("beforeend", stories.map(cardHtml).join(""));
  // Observe newly-added cards that still need a summary.
  feedEl.querySelectorAll(".card[data-summary-status='pending'], .card[data-summary-status='working'], .card[data-summary-status='failed']")
    .forEach((card) => {
      if (!card.dataset.observed) {
        card.dataset.observed = "1";
        summaryObserver.observe(card);
      }
    });
  offset += stories.length;
  loading = false;
  loaderEl.classList.add("hidden");
}

// On-demand summarization: ask the server to summarize a story only when its
// card is near the viewport. Keeps the CPU-bound model focused on what you're
// actually reading instead of grinding through the whole feed upfront.
const inFlight = new Set();

async function requestSummary(card) {
  const id = card.dataset.id;
  if (inFlight.has(id)) return;
  const status = card.dataset.summaryStatus;
  if (status === "done" || status === "skipped") return;
  inFlight.add(id);
  try {
    const r = await fetch(`/api/summarize/${id}`, { method: "POST" });
    if (!r.ok) return;
    const s = await r.json();
    card.dataset.summaryStatus = s.summary_status;
    const cur = card.querySelector(".summary");
    if (cur) cur.outerHTML = summaryBlock(s);
    // If the model wasn't ready, leave it observed so it retries on next view.
    if (s.summary_status === "done" || s.summary_status === "skipped" ||
        s.summary_status === "failed") {
      summaryObserver.unobserve(card);
    }
  } catch {
    // network blip — leave observed so it retries when scrolled past again
  } finally {
    inFlight.delete(id);
  }
}

const summaryObserver = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (entry.isIntersecting) requestSummary(entry.target);
  }
}, { rootMargin: "200px" });

function resetFeed() {
  feedEl.innerHTML = "";
  emptyEl.classList.add("hidden");
  offset = 0;
  exhausted = false;
  loadMore();
}

async function act(id, action) {
  const card = feedEl.querySelector(`.card[data-id="${id}"]`);
  if (card) card.classList.add("leaving");
  await fetch(`/api/story/${id}/${action}`, { method: "POST" });
  setTimeout(() => {
    if (view === "search") {
      // Keep results in place but refresh so the state badge updates.
      resetFeed();
      refreshStatus();
      return;
    }
    if (card) card.remove();
    refreshStatus();
    if (feedEl.children.length === 0) resetFeed();
  }, 280);
}

feedEl.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-act]");
  if (!btn) return;
  const id = btn.closest(".card").dataset.id;
  act(id, btn.dataset.act);
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelector(".tab.active").classList.remove("active");
    tab.classList.add("active");
    view = tab.dataset.view;
    // Switching tabs exits search mode.
    if (searchInput.value) { searchInput.value = ""; searchClear.classList.add("hidden"); }
    searchQuery = "";
    resetFeed();
  });
});

// --- Search ------------------------------------------------------------------
const searchInput = document.getElementById("search-input");
const searchClear = document.getElementById("search-clear");
let searchDebounce = null;

function runSearch(q) {
  searchQuery = q.trim();
  if (!searchQuery) { exitSearch(); return; }
  if (view !== "search") {
    prevView = view;                       // remember where we were
    document.querySelector(".tab.active")?.classList.remove("active");
  }
  view = "search";
  searchClear.classList.remove("hidden");
  resetFeed();
}

function exitSearch() {
  searchQuery = "";
  searchInput.value = "";
  searchClear.classList.add("hidden");
  view = prevView || "feed";
  const tab = document.querySelector(`.tab[data-view="${view}"]`);
  if (tab) {
    document.querySelector(".tab.active")?.classList.remove("active");
    tab.classList.add("active");
  }
  resetFeed();
}

searchInput.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  const q = searchInput.value;
  searchClear.classList.toggle("hidden", !q);
  searchDebounce = setTimeout(() => runSearch(q), 250);
});
searchInput.addEventListener("keydown", (e) => {
  if (e.key === "Escape") exitSearch();
});
searchClear.addEventListener("click", exitSearch);

// Keyword <-> semantic toggle.
const searchModeBtn = document.getElementById("search-mode");
function syncSearchModeBtn() {
  const semantic = searchMode === "semantic";
  searchModeBtn.textContent = semantic ? "ai" : "kw";
  searchModeBtn.classList.toggle("active", semantic);
  searchModeBtn.title = semantic
    ? "Semantic search (meaning-based) — click for keyword"
    : "Keyword search (exact text) — click for semantic";
}
searchModeBtn.addEventListener("click", () => {
  searchMode = searchMode === "semantic" ? "keyword" : "semantic";
  localStorage.setItem("searchMode", searchMode);
  syncSearchModeBtn();
  if (searchQuery) runSearch(searchInput.value);  // re-run with new mode
});
syncSearchModeBtn();


let isRefreshing = false;

async function manualRefresh() {
  if (isRefreshing) return;
  isRefreshing = true;
  refreshBtn.disabled = true;
  refreshBtn.textContent = "↻ Fetching…";
  await fetch(`/api/refresh?limit=${feedSize()}`, { method: "POST" });
  refreshBtn.disabled = false;
  isRefreshing = false;
  if (view === "feed") resetFeed();
  refreshStatus();
  if (typeof scheduleAutoRefresh === "function") scheduleAutoRefresh(); // reset countdown
  updateRefreshLabel();
}

refreshBtn.addEventListener("click", manualRefresh);

function updateStatus(counts) {
  if (!counts) return;
  let saved = counts.saved ? ` · ${counts.saved} saved` : "";
  let filt = counts.filtered ? ` · ${counts.filtered} filtered` : "";
  statusEl.dataset.counts = JSON.stringify(counts);
  statusEl.innerHTML =
    `<span class="dot ${statusEl.dataset.ollama === "off" ? "off" : "ok"}"></span>` +
    `${counts.new} new · ${counts.read} read${saved}${filt}`;
}

async function refreshStatus() {
  try {
    const r = await fetch("/api/status");
    const s = await r.json();
    statusEl.dataset.ollama = s.ollama ? "ok" : "off";
    updateStatus(s.counts);
  } catch {}
}

new IntersectionObserver((entries) => {
  if (entries[0].isIntersecting) loadMore();
}, { rootMargin: "400px" }).observe(sentinel);

// --- Settings / theme --------------------------------------------------------
const settingsBtn = document.getElementById("settings-btn");
const settingsOverlay = document.getElementById("settings-overlay");
const settingsClose = document.getElementById("settings-close");
const themeOptions = document.getElementById("theme-options");

function currentTheme() {
  return localStorage.getItem("theme") || "system";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("theme", theme);
  // Reflect the selection in the modal.
  themeOptions.querySelectorAll(".theme-card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.themeChoice === theme);
  });
}

function openSettings() {
  applyTheme(currentTheme()); // refresh selected highlight
  settingsOverlay.classList.remove("hidden");
}
function closeSettings() {
  settingsOverlay.classList.add("hidden");
}

settingsBtn.addEventListener("click", openSettings);
settingsClose.addEventListener("click", closeSettings);
settingsOverlay.addEventListener("click", (e) => {
  if (e.target === settingsOverlay) closeSettings();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSettings();
});

themeOptions.addEventListener("click", (e) => {
  const card = e.target.closest("[data-theme-choice]");
  if (card) applyTheme(card.dataset.themeChoice);
});

// Initialize selected state on load (theme itself was set pre-paint in HTML).
applyTheme(currentTheme());

// --- Auto-refresh ------------------------------------------------------------
const autoToggle = document.getElementById("autorefresh-toggle");
const autoInterval = document.getElementById("autorefresh-interval");
const intervalRow = document.getElementById("interval-row");
let autoRefreshTimer = null;

function autoRefreshEnabled() {
  return localStorage.getItem("autorefresh") === "on";
}
function autoRefreshMinutes() {
  return parseInt(localStorage.getItem("autorefresh_min") || "15", 10);
}

async function doRefresh() {
  await manualRefresh();
}

function scheduleAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
  if (autoRefreshEnabled()) {
    const ms = autoRefreshMinutes() * 60 * 1000;
    nextRefreshAt = Date.now() + ms;
    // doRefresh() -> manualRefresh() reschedules the next cycle on completion.
    autoRefreshTimer = setInterval(doRefresh, ms);
  } else {
    nextRefreshAt = null;
  }
  updateRefreshLabel();
}

// --- Refresh button label (doubles as the auto-refresh countdown) -----------
let nextRefreshAt = null;

function updateRefreshLabel() {
  if (isRefreshing) return; // don't clobber the "Fetching…" state
  if (autoRefreshEnabled() && nextRefreshAt) {
    let remaining = Math.max(0, Math.round((nextRefreshAt - Date.now()) / 1000));
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    refreshBtn.textContent = `↻ ${m}:${String(s).padStart(2, "0")}`;
    refreshBtn.title = "Click to refresh now · auto-refresh on";
  } else {
    refreshBtn.textContent = "↻ Refresh";
    refreshBtn.title = "Fetch latest from Hacker News";
  }
}

setInterval(updateRefreshLabel, 1000);

function syncAutoRefreshUI() {
  autoToggle.checked = autoRefreshEnabled();
  autoInterval.value = String(autoRefreshMinutes());
  intervalRow.classList.toggle("disabled", !autoRefreshEnabled());
}

autoToggle.addEventListener("change", () => {
  localStorage.setItem("autorefresh", autoToggle.checked ? "on" : "off");
  syncAutoRefreshUI();
  scheduleAutoRefresh();
});
autoInterval.addEventListener("change", () => {
  localStorage.setItem("autorefresh_min", autoInterval.value);
  scheduleAutoRefresh();
});

syncAutoRefreshUI();
scheduleAutoRefresh();

// --- Feed size ---------------------------------------------------------------
const feedSizeSelect = document.getElementById("feedsize-select");

function feedSize() {
  return parseInt(localStorage.getItem("feedsize") || "50", 10);
}

feedSizeSelect.value = String(feedSize());
feedSizeSelect.addEventListener("change", () => {
  localStorage.setItem("feedsize", feedSizeSelect.value);
});

// --- Keyword filters ---------------------------------------------------------
const filterForm = document.getElementById("filter-add");
const filterInput = document.getElementById("filter-input");
const filterChips = document.getElementById("filter-chips");

function renderFilters(filters) {
  filterChips.innerHTML = filters
    .map(
      (k) =>
        `<span class="chip">${escapeHtml(k)}<button data-kw="${escapeHtml(k)}" aria-label="Remove ${escapeHtml(k)}">✕</button></span>`
    )
    .join("");
}

async function loadFilters() {
  try {
    const r = await fetch("/api/filters");
    const data = await r.json();
    renderFilters(data.filters);
  } catch {}
}

filterForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const kw = filterInput.value.trim();
  if (!kw) return;
  const r = await fetch("/api/filters", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keyword: kw }),
  });
  const data = await r.json();
  renderFilters(data.filters);
  filterInput.value = "";
  if (view === "feed") resetFeed(); // hide newly-filtered stories immediately
  refreshStatus();
});

filterChips.addEventListener("click", async (e) => {
  const btn = e.target.closest("button[data-kw]");
  if (!btn) return;
  const r = await fetch(`/api/filters/${encodeURIComponent(btn.dataset.kw)}`, {
    method: "DELETE",
  });
  const data = await r.json();
  renderFilters(data.filters);
  if (view === "feed") resetFeed(); // un-filtered stories may reappear
  refreshStatus();
});

resetFeed();
refreshStatus();
loadFilters();
setInterval(refreshStatus, 8000);
