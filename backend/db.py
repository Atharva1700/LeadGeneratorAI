"""
db.py — SQLite database layer. All queries centralized here.
"""

import sqlite3
import logging
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "leads.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent access
    return conn


def init_db() -> None:
    """Create all tables if they don't exist."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            started_at TEXT,
            completed_at TEXT,
            total_discovered INTEGER DEFAULT 0,
            total_enriched INTEGER DEFAULT 0,
            total_scored INTEGER DEFAULT 0,
            total_final INTEGER DEFAULT 0,
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS raw_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            source TEXT NOT NULL,
            raw_html TEXT,
            company_name TEXT,
            first_name TEXT,
            last_name TEXT,
            email TEXT,
            phone TEXT,
            address TEXT,
            industry TEXT,
            business_age_months INTEGER,
            scraped_at TEXT,
            extraction_method TEXT
        );

        CREATE TABLE IF NOT EXISTS enriched_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            raw_lead_id INTEGER REFERENCES raw_leads(id),
            first_name TEXT,
            last_name TEXT,
            email TEXT,
            phone TEXT,
            company_name TEXT,
            industry TEXT,
            business_age_months INTEGER,
            source TEXT,
            email_verified INTEGER DEFAULT 0,
            phone_valid INTEGER DEFAULT 0,
            quality_score INTEGER DEFAULT 0,
            score_reason TEXT,
            outreach_note TEXT,
            enriched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS progress_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            message TEXT NOT NULL,
            leads_count INTEGER DEFAULT 0,
            timestamp TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    logger.info(f"Database initialized at {DB_PATH}")


def create_run(run_id: str) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, status, started_at) VALUES (?, 'pending', ?)",
        (run_id, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()
    conn.close()


def update_run_status(run_id: str, status: str, **kwargs) -> None:
    if not kwargs:
        conn = get_connection()
        conn.execute(
            "UPDATE pipeline_runs SET status = ? WHERE run_id = ?",
            (status, run_id)
        )
        conn.commit()
        conn.close()
        return

    set_clauses = ["status = ?"]
    values = [status]
    allowed = {
        "started_at", "completed_at", "total_discovered", "total_enriched",
        "total_scored", "total_final", "error_message"
    }
    for k, v in kwargs.items():
        if k in allowed:
            set_clauses.append(f"{k} = ?")
            values.append(v)

    values.append(run_id)
    sql = f"UPDATE pipeline_runs SET {', '.join(set_clauses)} WHERE run_id = ?"
    conn = get_connection()
    conn.execute(sql, values)
    conn.commit()
    conn.close()


def insert_raw_lead(run_id: str, data: dict) -> int:
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO raw_leads
           (run_id, source, raw_html, company_name, first_name, last_name,
            email, phone, address, industry, business_age_months,
            scraped_at, extraction_method)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            data.get("source", "unknown"),
            data.get("raw_html"),
            data.get("company_name"),
            data.get("first_name"),
            data.get("last_name"),
            data.get("email"),
            data.get("phone"),
            data.get("address"),
            data.get("industry"),
            data.get("business_age_months"),
            datetime.now(timezone.utc).isoformat(),
            data.get("extraction_method"),
        )
    )
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def insert_enriched_lead(run_id: str, data: dict) -> int:
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO enriched_leads
           (run_id, raw_lead_id, first_name, last_name, email, phone,
            company_name, industry, business_age_months, source,
            email_verified, phone_valid, quality_score, score_reason,
            outreach_note, enriched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            data.get("raw_lead_id"),
            data.get("first_name"),
            data.get("last_name"),
            data.get("email"),
            data.get("phone"),
            data.get("company_name"),
            data.get("industry"),
            data.get("business_age_months"),
            data.get("source", "unknown"),
            data.get("email_verified", 0),
            data.get("phone_valid", 0),
            data.get("quality_score", 0),
            data.get("score_reason", ""),
            data.get("outreach_note", ""),
            data.get("enriched_at", datetime.now(timezone.utc).isoformat()),
        )
    )
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id


def log_progress(run_id: str, phase: str, message: str, leads_count: int) -> None:
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO progress_events (run_id, phase, message, leads_count, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, phase, message, leads_count, datetime.now(timezone.utc).isoformat())
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to log progress: {e}")


def get_run(run_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_enriched_leads(run_id: str, min_score: int = 0) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM enriched_leads
           WHERE run_id = ? AND quality_score >= ?
           ORDER BY quality_score DESC""",
        (run_id, min_score)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_progress_events(run_id: str, after_id: int = 0) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT * FROM progress_events
           WHERE run_id = ? AND id > ?
           ORDER BY id ASC""",
        (run_id, after_id)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats(run_id: str) -> dict:
    conn = get_connection()

    # Source breakdown
    sources = conn.execute(
        """SELECT source, COUNT(*) as cnt
           FROM enriched_leads WHERE run_id = ?
           GROUP BY source""",
        (run_id,)
    ).fetchall()

    # Score distribution
    score_dist = conn.execute(
        """SELECT
             SUM(CASE WHEN quality_score >= 90 THEN 1 ELSE 0 END) as s90,
             SUM(CASE WHEN quality_score >= 80 AND quality_score < 90 THEN 1 ELSE 0 END) as s80,
             SUM(CASE WHEN quality_score >= 70 AND quality_score < 80 THEN 1 ELSE 0 END) as s70,
             SUM(CASE WHEN quality_score >= 60 AND quality_score < 70 THEN 1 ELSE 0 END) as s60,
             SUM(CASE WHEN quality_score < 60 THEN 1 ELSE 0 END) as slow
           FROM enriched_leads WHERE run_id = ?""",
        (run_id,)
    ).fetchone()

    # Validation stats
    val_stats = conn.execute(
        """SELECT
             SUM(email_verified) as emails_verified,
             SUM(phone_valid) as phones_valid,
             SUM(CASE WHEN email_verified = 1 AND phone_valid = 1 THEN 1 ELSE 0 END) as both_valid,
             COUNT(*) as total
           FROM enriched_leads WHERE run_id = ?""",
        (run_id,)
    ).fetchone()

    conn.close()

    return {
        "sources": [dict(r) for r in sources],
        "score_distribution": dict(score_dist) if score_dist else {},
        "validation": dict(val_stats) if val_stats else {},
    }