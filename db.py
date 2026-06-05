"""SQLite storage for the HN doom-scroll dashboard."""
import re
import sqlite3
import threading
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = Path(__file__).parent / "hn.db"

# One connection, guarded by a lock. SQLite + a single local user is plenty.
_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row
_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id              INTEGER PRIMARY KEY,      -- HN item id
    title           TEXT    NOT NULL,
    url             TEXT,                     -- external article url (may be null for Ask HN etc.)
    hn_url          TEXT    NOT NULL,         -- comments page
    score           INTEGER DEFAULT 0,
    author          TEXT,
    num_comments    INTEGER DEFAULT 0,
    posted_at       INTEGER,                  -- unix seconds
    rank            INTEGER DEFAULT 0,        -- position on the front page
    summary         TEXT,
    summary_status  TEXT    DEFAULT 'pending',-- pending | done | failed | skipped
    summary_source  TEXT    DEFAULT '',       -- article | rendered | discussion | pdf | video | paywall | none
    embedding       TEXT,                     -- JSON float vector for semantic search
    state           TEXT    DEFAULT 'new',    -- new | read | hidden | saved
    fetched_at      INTEGER DEFAULT (strftime('%s','now')),
    updated_at      INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_stories_state ON stories(state);
CREATE INDEX IF NOT EXISTS idx_stories_rank  ON stories(rank);

CREATE TABLE IF NOT EXISTS filters (
    keyword    TEXT PRIMARY KEY,            -- lowercase match term
    created_at INTEGER DEFAULT (strftime('%s','now'))
);
"""


def init():
    with _lock:
        _conn.executescript(SCHEMA)
        # Lightweight migrations: add columns to pre-existing databases.
        cols = [r["name"] for r in _conn.execute("PRAGMA table_info(stories)").fetchall()]
        if "summary_source" not in cols:
            _conn.execute("ALTER TABLE stories ADD COLUMN summary_source TEXT DEFAULT ''")
        if "embedding" not in cols:
            # JSON-encoded float vector of the title+summary, for semantic search.
            _conn.execute("ALTER TABLE stories ADD COLUMN embedding TEXT")
        _conn.commit()


def set_summary(story_id: int, summary: str, status: str, source: str = ""):
    with _lock:
        _conn.execute(
            """UPDATE stories
               SET summary=?, summary_status=?, summary_source=?,
                   updated_at=strftime('%s','now')
               WHERE id=?""",
            (summary, status, source, story_id),
        )
        _conn.commit()


def upsert_story(item: dict, rank: int):
    """Insert a freshly fetched HN story, or refresh its volatile fields.

    Never clobbers a user's state (read/hidden) or an existing summary.
    """
    with _lock:
        _conn.execute(
            """
            INSERT INTO stories (id, title, url, hn_url, score, author,
                                 num_comments, posted_at, rank)
            VALUES (:id, :title, :url, :hn_url, :score, :author,
                    :num_comments, :posted_at, :rank)
            ON CONFLICT(id) DO UPDATE SET
                score        = excluded.score,
                num_comments = excluded.num_comments,
                rank         = excluded.rank,
                updated_at   = strftime('%s','now')
            """,
            item | {"rank": rank},
        )
        _conn.commit()


def stories_needing_summary(limit: int = 50):
    """Stories that still need a summary (used for diagnostics / batch tools)."""
    with _lock:
        rows = _conn.execute(
            """
            SELECT * FROM stories
            WHERE summary_status = 'pending' AND state != 'hidden'
            ORDER BY rank ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def set_state(story_id: int, state: str):
    with _lock:
        cur = _conn.execute(
            "UPDATE stories SET state=?, updated_at=strftime('%s','now') WHERE id=?",
            (state, story_id),
        )
        _conn.commit()
        return cur.rowcount


def get_feed(limit: int = 50, offset: int = 0):
    """Active feed, with disliked stories down-ranked (not removed).

    Stories matching your "not interested" signals sink to the bottom and are
    tagged with `downranked` + `downrank_reasons`. Keyword-filtered stories are
    excluded entirely (that's the explicit filter, this is the soft signal).
    """
    where, params = _filter_clause()
    with _lock:
        rows = _conn.execute(
            f"""
            SELECT * FROM stories
            WHERE state = 'new'{where}
            ORDER BY rank ASC
            """,
            params,
        ).fetchall()
    stories = [dict(r) for r in rows]

    profile = build_dislike_profile()
    for s in stories:
        score, reasons = score_against_dislikes(s, profile)
        s["downranked"] = score > 0
        s["downrank_reasons"] = reasons

    # Stable sort: disliked stories move to the bottom, HN order kept within
    # each group (Python's sort is stable, rows already came back rank-ordered).
    stories.sort(key=lambda s: 1 if s["downranked"] else 0)

    return stories[offset:offset + limit]


def get_by_state(state: str, limit: int = 100):
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM stories WHERE state=? ORDER BY updated_at DESC LIMIT ?",
            (state, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def search(query: str, limit: int = 100):
    """Keyword search across every story, ranked by relevance.

    Matches the query against title and summary (case-insensitive substring).
    Title matches rank above summary-only matches; ties broken by most-recent.
    Lets you find something you read/saved/hid earlier.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    like = f"%{q}%"
    with _lock:
        rows = _conn.execute(
            """
            SELECT *,
                   CASE WHEN LOWER(title) LIKE ? THEN 0 ELSE 1 END AS _rank
            FROM stories
            WHERE LOWER(title) LIKE ? OR LOWER(COALESCE(summary,'')) LIKE ?
            ORDER BY _rank ASC, updated_at DESC
            LIMIT ?
            """,
            (like, like, like, limit),
        ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        d.pop("_rank", None)  # internal ranking helper, not part of the story
        results.append(d)
    return results


# --- semantic search support ---------------------------------------------------
def set_embedding(story_id: int, vector_json: str):
    with _lock:
        _conn.execute(
            "UPDATE stories SET embedding=? WHERE id=?", (vector_json, story_id)
        )
        _conn.commit()


def get_embeddings(limit: int = 500):
    """Return [(id, embedding_json)] for stories that have an embedding."""
    with _lock:
        rows = _conn.execute(
            "SELECT id, embedding FROM stories WHERE embedding IS NOT NULL LIMIT ?",
            (limit,),
        ).fetchall()
    return [(r["id"], r["embedding"]) for r in rows]


def stories_missing_embedding(limit: int = 40):
    """Stories with a usable summary but no embedding yet (for backfill)."""
    with _lock:
        rows = _conn.execute(
            """
            SELECT id, title, summary FROM stories
            WHERE embedding IS NULL
              AND summary_status IN ('done', 'skipped')
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_stories_by_ids(ids):
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    with _lock:
        rows = _conn.execute(
            f"SELECT * FROM stories WHERE id IN ({placeholders})", list(ids)
        ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    # Preserve the caller's ordering (e.g. by similarity score).
    return [by_id[i] for i in ids if i in by_id]


def get_story(story_id: int):
    with _lock:
        row = _conn.execute(
            "SELECT * FROM stories WHERE id=?", (story_id,)
        ).fetchone()
    return dict(row) if row else None


# --- "not interested" learning -------------------------------------------------
# We learn from hidden stories: which words and domains you keep skipping.
# New stories matching those signals get down-ranked (pushed down, dimmed, and
# labeled) rather than removed — you still see everything.

MIN_HIDDEN_TO_LEARN = 10   # cold-start guard: do nothing until enough signal
TOP_TERMS = 15             # how many disliked terms to track

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "are", "was", "were", "be", "by", "with", "from", "as", "it", "its",
    "this", "that", "these", "those", "how", "why", "what", "when", "your",
    "you", "we", "i", "my", "our", "they", "their", "he", "she", "his", "her",
    "show", "ask", "hn", "new", "using", "use", "via", "vs", "into", "out",
    "up", "down", "about", "after", "before", "over", "more", "most", "can",
    "will", "not", "no", "yes", "do", "does", "has", "have", "had", "get",
}


def _tokens(text: str):
    """Lowercase word tokens of length >= 4, minus stopwords."""
    if not text:
        return []
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9+\-]{3,}", text.lower())
    return [w for w in words if w not in _STOPWORDS]


def _domain(url: str) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").replace("www.", "")
    except Exception:
        return ""


def build_dislike_profile():
    """Summarize what the user tends to hide: frequent terms and domains.

    Returns {"terms": {term: count}, "domains": {domain: count}, "n": hidden_count}
    or None when there isn't enough signal yet (cold start).
    """
    with _lock:
        rows = _conn.execute(
            "SELECT title, summary, url FROM stories WHERE state='hidden'"
        ).fetchall()
    if len(rows) < MIN_HIDDEN_TO_LEARN:
        return None

    term_counts = Counter()
    domain_counts = Counter()
    for r in rows:
        # Title terms carry the most signal; summary adds a little.
        term_counts.update(set(_tokens(r["title"])))
        d = _domain(r["url"])
        if d:
            domain_counts[d] += 1

    # Keep terms you've hidden at least twice (a single hide isn't a pattern).
    terms = {t: c for t, c in term_counts.most_common(TOP_TERMS) if c >= 2}
    domains = {d: c for d, c in domain_counts.items() if c >= 2}
    return {"terms": terms, "domains": domains, "n": len(rows)}


def score_against_dislikes(story: dict, profile: dict):
    """Return (score, reasons) for how much a story matches disliked signals.

    score is a small integer; higher = more likely to be skipped. reasons is a
    short list of human-readable matched signals for display.
    """
    if not profile:
        return 0, []
    reasons = []
    score = 0

    title_terms = set(_tokens(story.get("title", "")))
    summary_terms = set(_tokens(story.get("summary", "")))
    matched_terms = [t for t in profile["terms"] if t in title_terms or t in summary_terms]
    # Rank matched terms by how strongly you've disliked them.
    matched_terms.sort(key=lambda t: profile["terms"][t], reverse=True)
    if matched_terms:
        score += len(matched_terms)
        reasons.extend(matched_terms[:3])

    d = _domain(story.get("url", ""))
    if d and d in profile["domains"]:
        score += 1
        reasons.append(d)

    return score, reasons


def counts():
    where, params = _filter_clause()
    with _lock:
        rows = _conn.execute(
            "SELECT state, COUNT(*) AS n FROM stories GROUP BY state"
        ).fetchall()
        pending = _conn.execute(
            "SELECT COUNT(*) AS n FROM stories WHERE summary_status='pending' AND state!='hidden'"
        ).fetchone()["n"]
        # How many 'new' stories are currently hidden by keyword filters.
        filtered = 0
        if where:
            total_new = _conn.execute(
                "SELECT COUNT(*) AS n FROM stories WHERE state='new'"
            ).fetchone()["n"]
            visible = _conn.execute(
                f"SELECT COUNT(*) AS n FROM stories WHERE state='new'{where}",
                params,
            ).fetchone()["n"]
            filtered = total_new - visible
    out = {"new": 0, "read": 0, "hidden": 0, "saved": 0,
           "pending_summaries": pending, "filtered": filtered}
    for r in rows:
        out[r["state"]] = r["n"]
    # 'new' count should reflect what the user actually sees.
    out["new"] = max(0, out["new"] - filtered)
    return out


# --- keyword filters -----------------------------------------------------------
def _filter_clause():
    """Build a SQL fragment that excludes stories matching a keyword filter.

    Matches against both the title and the AI summary text. Returns
    (sql_fragment, params). Fragment is empty when no filters exist.
    """
    with _lock:
        rows = _conn.execute("SELECT keyword FROM filters").fetchall()
    keywords = [r["keyword"] for r in rows]
    if not keywords:
        return "", []
    # Exclude when the keyword appears in the title OR the summary.
    clause = "".join(
        " AND LOWER(title) NOT LIKE ? AND LOWER(COALESCE(summary,'')) NOT LIKE ?"
        for _ in keywords
    )
    params = []
    for k in keywords:
        like = f"%{k}%"
        params.extend([like, like])
    return clause, params


def list_filters():
    with _lock:
        rows = _conn.execute(
            "SELECT keyword FROM filters ORDER BY keyword ASC"
        ).fetchall()
    return [r["keyword"] for r in rows]


def add_filter(keyword: str):
    kw = keyword.strip().lower()
    if not kw:
        return False
    with _lock:
        _conn.execute(
            "INSERT OR IGNORE INTO filters (keyword) VALUES (?)", (kw,)
        )
        _conn.commit()
    return True


def remove_filter(keyword: str):
    kw = keyword.strip().lower()
    with _lock:
        cur = _conn.execute("DELETE FROM filters WHERE keyword=?", (kw,))
        _conn.commit()
    return cur.rowcount > 0
