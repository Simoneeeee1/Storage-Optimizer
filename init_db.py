import sqlite3
import random
from datetime import datetime, timedelta

DB_NAME = "system_transparency.db"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # --- RISORSE DI SISTEMA ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            type TEXT CHECK(type IN ('FILE','APP')),
            size_gb REAL,
            last_used DATETIME,
            status TEXT DEFAULT 'ACTIVE'
        )
    """)

    # --- ECCEZIONI ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS exceptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            type TEXT,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # --- LOG ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            item_name TEXT,
            action TEXT,
            reason TEXT,
            size_gb REAL
        )
    """)

    # --- NOTIFICHE ---
    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER,
            sent_at DATETIME,
            expires_at DATETIME,
            user_action TEXT,
            FOREIGN KEY(item_id) REFERENCES items(id)
        )
    """)

    # ✅ INDICI (fix performance)
    c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_expires ON notifications(expires_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_name ON items(name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp)")

    conn.commit()
    conn.close()


def seed():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    c.execute("DELETE FROM notifications")
    c.execute("DELETE FROM audit_logs")
    c.execute("DELETE FROM items")
    c.execute("DELETE FROM exceptions")

    exceptions_to_seed = [
        ('Final Cut Pro Library', 'FILE'),
        ('Docker Desktop', 'APP'),
        ('PostgreSQL Data', 'DATABASE'),
        ('Progetti Lavoro 2026', 'FOLDER')
    ]

    c.executemany(
        "INSERT INTO exceptions (name, type) VALUES (?, ?)",
        exceptions_to_seed
    )

    audit_data = [
        ('Cache Browser Chrome', 'DELETE', 'Policy Inattività 30gg', 4.2),
        ('Xcode Derivative Data', 'DELETE', 'Ottimizzazione Manuale', 12.5),
        ('Backup iPhone 2024', 'KEEP', 'Confermato da utente', 45.0),
        ('Minecraft.app', 'REINSTALL', 'Ripristinato', -2.5)
    ]

    for name, action, reason, size in audit_data:
        c.execute(
            "INSERT INTO audit_logs (item_name, action, reason, size_gb) VALUES (?, ?, ?, ?)",
            (name, action, reason, size)
        )

    items = [
        ('Adobe Photoshop 2022', 'APP', 12.4, 150),
        ('Virtual Machine Test', 'FILE', 40.0, 130),
        ('Log Server 2025', 'FILE', 5.1, 200)
    ]

    for name, type_, size, days in items:
        last_used = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

        c.execute(
            "INSERT INTO items (name, type, size_gb, last_used) VALUES (?, ?, ?, ?)",
            (name, type_, size, last_used)
        )

        item_id = c.lastrowid

        expires = (datetime.now() + timedelta(hours=random.randint(10, 48))).strftime('%Y-%m-%d %H:%M:%S')

        c.execute(
            "INSERT INTO notifications (item_id, sent_at, expires_at) VALUES (?, CURRENT_TIMESTAMP, ?)",
            (item_id, expires)
        )

    conn.commit()
    conn.close()

    print("Sistema popolato.")


if __name__ == "__main__":
    init_db()
    seed()
