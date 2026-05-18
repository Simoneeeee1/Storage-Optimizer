import sqlite3

DB_NAME = "system_transparency.db"


def get_connection(timeout: int = 10) -> sqlite3.Connection:
    """Factory centralizzata per le connessioni al DB.

    - check_same_thread=False: necessario per FastAPI (thread multipli)
    - timeout=10: evita errori immediati in caso di lock concorrente
    - WAL mode: permette letture concorrenti durante le scritture
    """
    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT,
            type      TEXT CHECK(type IN ('FILE', 'APP', 'FOLDER')),
            size_gb   REAL,
            last_used DATETIME,
            real_path TEXT UNIQUE,
            status    TEXT DEFAULT 'ACTIVE'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS exceptions (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT UNIQUE,
            type      TEXT,
            real_path TEXT,
            added_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            item_name TEXT,
            action    TEXT,
            reason    TEXT,
            size_gb   REAL,
            real_path TEXT,
            dry_run   INTEGER DEFAULT 1
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id     INTEGER,
            sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
            expires_at  DATETIME,
            user_action TEXT,
            FOREIGN KEY(item_id) REFERENCES items(id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS duplicates (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash TEXT,
            name      TEXT,
            size_gb   REAL,
            real_path TEXT UNIQUE,
            status    TEXT DEFAULT 'ACTIVE',
            found_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            mtime     REAL  -- os.path.getmtime() salvato per il delta scan
        )
    """)

    # Migrazione live: aggiunge mtime se il DB esiste gia senza quella colonna
    existing_cols = [row[1] for row in c.execute("PRAGMA table_info(duplicates)").fetchall()]
    if "mtime" not in existing_cols:
        c.execute("ALTER TABLE duplicates ADD COLUMN mtime REAL")
        print("Migrazione: aggiunta colonna mtime a duplicates.")

    # Indici originali
    c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_expires ON notifications(expires_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_path            ON items(real_path)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp       ON audit_logs(timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_exceptions_path       ON exceptions(real_path)")

    # Indici duplicati
    c.execute("CREATE INDEX IF NOT EXISTS idx_duplicates_hash       ON duplicates(file_hash)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_duplicates_path       ON duplicates(real_path)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_duplicates_status     ON duplicates(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_status          ON items(status)")

    conn.commit()
    conn.close()
    print("DB inizializzato.")


if __name__ == "__main__":
    init_db()