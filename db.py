"""SQLite storage for the HN doom-scroll dashboard."""
import sqlite3
import threading
from pathlib import Path

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
    state           TEXT    DEFAULT 'new',    -- new | read | hidden
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


def set_summary(story_id: int, summary: str, status: str):
    with _lock:
        _conn.execute(
            "UPDATE stories SET summary=?, summary_status=?, updated_at=strftime('%s','now') WHERE id=?",
            (summary, status, story_id),
        )
        _conn.commit()


def set_state(story_id: int, state: str):
    with _lock:
        cur = _conn.execute(
            "UPDATE stories SET state=?, updated_at=strftime('%s','now') WHERE id=?",
            (state, story_id),
        )
        _conn.commit()
        return cur.rowcount


def get_feed(limit: int = 50, offset: int = 0):
    """Active feed: stories the user hasn't read or hidden, minus keyword-filtered."""
    where, params = _filter_clause()
    with _lock:
        rows = _conn.execute(
            f"""
            SELECT * FROM stories
            WHERE state = 'new'{where}
            ORDER BY rank ASC
            LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def get_by_state(state: str, limit: int = 100):
    with _lock:
        rows = _conn.execute(
            "SELECT * FROM stories WHERE state=? ORDER BY updated_at DESC LIMIT ?",
            (state, limit),
        ).fetchall()
    return [dict(r) for r in rows]


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
    out = {"new": 0, "read": 0, "hidden": 0,
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
