"""
RZ Automedata - SQLite Database Module
Handles all asset storage and metadata persistence.
"""

import sqlite3
import os
from datetime import datetime

# Use AppData folder for persistent storage (works with exe and script)
_APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RZAutomedata")
os.makedirs(_APP_DIR, exist_ok=True)
DB_PATH = os.path.join(_APP_DIR, "rz_automedata.db")


def get_connection():
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database schema."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            file_type TEXT NOT NULL,
            preview_path TEXT,
            filename TEXT NOT NULL,
            title TEXT DEFAULT '',
            keywords TEXT DEFAULT '',
            category TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()


def save_setting(key, value):
    """Save a setting to the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (key, str(value)))
    conn.commit()
    conn.close()


def get_setting(key, default=""):
    """Get a setting from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cursor.fetchone()
    conn.close()
    return row["value"] if row else default


def add_asset(file_path, file_type, preview_path, filename):
    """Add a new asset to the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO assets (file_path, file_type, preview_path, filename, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (file_path, file_type, preview_path, filename, datetime.now().isoformat()))
    asset_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return asset_id


def get_all_assets():
    """Get all assets from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM assets ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_pending_assets():
    """Get all assets with 'pending' status."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM assets WHERE status = 'pending' ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_metadata(asset_id, title, keywords, category):
    """Update metadata for a specific asset."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE assets SET title = ?, keywords = ?, category = ?, status = 'done'
        WHERE id = ?
    """, (title, keywords, category, asset_id))
    conn.commit()
    conn.close()


def update_status(asset_id, status):
    """Update the status of an asset."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE assets SET status = ? WHERE id = ?", (status, asset_id))
    conn.commit()
    conn.close()


def clear_all():
    """Delete all assets from the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM assets")
    conn.commit()
    conn.close()


def get_asset_by_id(asset_id):
    """Get a single asset by ID."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_done_assets():
    """Get all assets with 'done' status."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM assets WHERE status = 'done' ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


# Initialize database on import
init_db()

