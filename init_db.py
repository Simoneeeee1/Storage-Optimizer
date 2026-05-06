import sqlite3
import os

DB_NAME = "system_transparency.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            type TEXT CHECK(type IN ('FILE','APP','FOLDER')),
            size_gb REAL,
            last_used DATETIME,
            real_path TEXT UNIQUE,
            status TEXT DEFAULT 'ACTIVE'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS exceptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            type TEXT,
            real_path TEXT,          -- path assoluto sul disco (opzionale ma preferito)
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            item_name TEXT,
            action TEXT,
            reason TEXT,
            size_gb REAL,
            real_path TEXT,
            dry_run INTEGER DEFAULT 1   -- 1 = solo log, 0 = operazione reale
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at DATETIME,
            user_action TEXT,
            FOREIGN KEY(item_id) REFERENCES items(id)
        )
    """)

    c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_expires ON notifications(expires_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_path ON items(real_path)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_exceptions_path ON exceptions(real_path)")

    conn.commit()
    conn.close()
    print("DB inizializzato.")


if __name__ == "__main__":
    init_db()