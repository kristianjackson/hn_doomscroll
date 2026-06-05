const feedEl = document.getElementById("feed");
const loaderEl = document.getElementById("loader");
const emptyEl = document.getElementById("empty");
const statusEl = document.getElementById("status");
const refreshBtn = document.getElementById("refresh");
const sentinel = document.getElementById("sentinel");

let view = "feed";          // feed | read | hidden
let offset = 0;
const PAGE = 20;
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

function summaryBlock(s) {
  if (s.summary_status === "done" || s.summary_status === "skipped")
    return `<p class="summary">${escapeHtml(s.summary)}</p>`;
  if (s.summary_status === "failed")
    return `<p class="summary failed">${escapeHtml(s.summary || "No summary.")}</p>`;
  return `<p class="summary pending">⏳ summarizing locally…</p>`;
}

function escapeHtml(t) {
  const d = document.createElement("div");
  d.textContent = t || "";
  return d.innerHTML;
}

function cardHtml(s) {
  const link = s.url || s.hn_url;
  const actions = view === "feed"
    ? `<button class="btn read" data-act="read">✓ Read</button>
       <button class="btn hide" data-act="hide">✕ Not interested</button>`
    : `<button class="btn" data-act="restore">↩ Back to feed</button>`;
  return `
    <article class="card" data-id="${s.id}">
      <div class="meta">
        <span class="score">▲ ${s.score}</span>
        <span class="domain">${domain(s.url)}</span>
        <span>${s.num_comments} comments</span>
        <span>${timeAgo(s.posted_at)}</span>
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
  } else {
    const r = await fetch(`/api/list/${view}`);
    stories = (await r.json()).stories;
    exhausted = true; // list views return everything at once
  }

  if (stories.length === 0 && offset === 0) {
    emptyEl.textContent = view === "feed"
      ? "All caught up. Hit Refresh to pull the latest from HN."
      : `Nothing in ${view} yet.`;
    emptyEl.classList.remove("hidden");
  }
  if (stories.length < PAGE) exhausted = true;

  feedEl.insertAdjacentHTML("beforeend", stories.map(cardHtml).join(""));
  offset += stories.length;
  loading = false;
  loaderEl.classList.add("hidden");
}

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
    resetFeed();
  });
});

refreshBtn.addEventListener("click", async () => {
  refreshBtn.disabled = true;
  refreshBtn.textContent = "↻ Fetching…";
  await fetch(`/api/refresh?limit=${feedSize()}`, { method: "POST" });
  refreshBtn.disabled = false;
  refreshBtn.textContent = "↻ Refresh";
  if (view === "feed") resetFeed();
  refreshStatus();
});

function updateStatus(counts) {
  if (!counts) return;
  let pend = counts.pending_summaries
    ? ` · ${counts.pending_summaries} summarizing` : "";
  let filt = counts.filtered
    ? ` · ${counts.filtered} filtered` : "";
  statusEl.dataset.counts = JSON.stringify(counts);
  statusEl.innerHTML =
    `<span class="dot ${statusEl.dataset.ollama === "off" ? "off" : "ok"}"></span>` +
    `${counts.new} new · ${counts.read} read${pend}${filt}`;
}

async function refreshStatus() {
  try {
    const r = await fetch("/api/status");
    const s = await r.json();
    statusEl.dataset.ollama = s.ollama ? "ok" : "off";
    updateStatus(s.counts);
    // If summaries are still being generated, re-poll the visible feed.
    if (view === "feed" && s.counts.pending_summaries > 0) {
      hydrateSummaries();
    }
  } catch {}
}

// Quietly refresh summary text for cards currently showing "pending".
async function hydrateSummaries() {
  const pendingCards = [...feedEl.querySelectorAll(".summary.pending")];
  if (pendingCards.length === 0) return;
  const r = await fetch(`/api/feed?limit=${offset || PAGE}&offset=0`);
  const data = await r.json();
  const byId = Object.fromEntries(data.stories.map((s) => [s.id, s]));
  feedEl.querySelectorAll(".card").forEach((card) => {
    const s = byId[card.dataset.id];
    if (!s) return;
    const cur = card.querySelector(".summary");
    if (cur && cur.classList.contains("pending") && s.summary_status !== "pending") {
      cur.outerHTML = summaryBlock(s);
    }
  });
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
  await fetch(`/api/refresh?limit=${feedSize()}`, { method: "POST" });
  if (view === "feed") resetFeed();
  refreshStatus();
}

function scheduleAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
  if (autoRefreshEnabled()) {
    autoRefreshTimer = setInterval(doRefresh, autoRefreshMinutes() * 60 * 1000);
  }
}

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
