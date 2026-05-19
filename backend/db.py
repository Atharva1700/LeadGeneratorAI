import sqlite3
import os

DB_PATH = "leads.db"

def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)

def init_db() -> None:
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
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
        )
    """)
    
    cursor.execute("""
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
        )
    """)
    
    cursor.execute("""
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
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS progress_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            phase TEXT NOT NULL,
            message TEXT NOT NULL,
            leads_count INTEGER DEFAULT 0,
            timestamp TEXT NOT NULL
        )
    """)
    
    conn.commit()
    conn.close()

def create_run(run_id: str) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO pipeline_runs (run_id) VALUES (?)", (run_id,))
    conn.commit()
    conn.close()

def update_run_status(run_id: str, status: str, **kwargs) -> None:
    conn = get_connection()
    cursor = conn.cursor()
    fields = ", ".join([f"{k} = ?" for k in kwargs.keys()])
    values = list(kwargs.values())
    values.append(status)
    values.append(run_id)
    cursor.execute(f"UPDATE pipeline_runs SET {fields}, status = ? WHERE run_id = ?", values)
    conn.commit()
    conn.close()

def insert_raw_lead(run_id: str, data: dict) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    keys = list(data.keys())
    values = list(data.values())
    placeholders = ", ".join(["?"] * len(keys))
    cursor.execute(f"INSERT INTO raw_leads (run_id, {', '.join(keys)}) VALUES (?, {placeholders})", [run_id] + values)
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id

def insert_enriched_lead(run_id: str, data: dict) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    keys = list(data.keys())
    values = list(data.values())
    placeholders = ", ".join(["?"] * len(keys))
    cursor.execute(f"INSERT INTO enriched_leads (run_id, {', '.join(keys)}) VALUES (?, {placeholders})", [run_id] + values)
    row_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return row_id

def log_progress(run_id: str, phase: str, message: str, leads_count: int) -> None:
    from datetime import datetime
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO progress_events (run_id, phase, message, leads_count, timestamp) VALUES (?, ?, ?, ?, ?)",
        (run_id, phase, message, leads_count, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()

def get_run(run_id: str) -> dict:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {}

def get_enriched_leads(run_id: str, min_score: int = 0) -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM enriched_leads WHERE run_id = ? AND quality_score >= ?", (run_id, min_score))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_progress_events(run_id: str, after_id: int = 0) -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM progress_events WHERE run_id = ? AND id > ? ORDER BY id ASC", (run_id, after_id))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_stats(run_id: str) -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    # Placeholder for stats calculation
    conn.close()
    return {}
