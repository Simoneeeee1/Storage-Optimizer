import sqlite3

DB_NAME = "system_transparency.db"


def get_connection(timeout: int = 10) -> sqlite3.Connection:
    """Factory centralizzata per le connessioni al DB.

    - check_same_thread=False: necessario per FastAPI (thread multipli)
    - timeout=10: evita errori immediati in caso di lock concorrente
    - WAL mode: permette letture concorrenti durante le scritture
    - foreign_keys=ON: attiva i vincoli FK (richiesto esplicitamente in SQLite)
    """
    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(c: sqlite3.Cursor):
    """Migrazioni live: aggiunge colonne mancanti senza distruggere dati esistenti."""

    # duplicates.mtime — aggiunto per il delta scan
    dup_cols = [r[1] for r in c.execute("PRAGMA table_info(duplicates)").fetchall()]
    if "mtime" not in dup_cols:
        c.execute("ALTER TABLE duplicates ADD COLUMN mtime REAL")
        print("Migrazione: aggiunta colonna mtime a duplicates.")

    # items.status — check constraint esteso per includere KEPT
    # SQLite non supporta ALTER COLUMN: verifichiamo solo che il valore sia accettato
    # dalla logica applicativa; il CHECK esistente accetta qualsiasi TEXT.
    # Nessuna ALTER necessaria, il vincolo CHECK originale non era restrittivo su status.


def init_db():
    conn = get_connection()
    c = conn.cursor()

    # Tabelle
    c.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT,
            type      TEXT CHECK(type IN ('FILE', 'APP', 'FOLDER')),
            size_gb   REAL,
            last_used DATETIME,
            real_path TEXT UNIQUE,
            status    TEXT DEFAULT 'ACTIVE'
                        CHECK(status IN ('ACTIVE', 'DELETED', 'KEPT'))
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
            FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS duplicates (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            file_hash TEXT,
            name      TEXT,
            size_gb   REAL,
            real_path TEXT UNIQUE,
            status    TEXT DEFAULT 'ACTIVE'
                        CHECK(status IN ('ACTIVE', 'DELETED', 'ORPHAN')),
            found_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            mtime     REAL  -- os.path.getmtime() per il delta scan
        )
    """)

    # Migrazioni live 
    _migrate(c)

    # Indici 

    c.execute("CREATE INDEX IF NOT EXISTS idx_notifications_expires  ON notifications(expires_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_path             ON items(real_path)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp        ON audit_logs(timestamp)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_exceptions_path        ON exceptions(real_path)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_duplicates_hash        ON duplicates(file_hash)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_duplicates_path        ON duplicates(real_path)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_duplicates_status      ON duplicates(status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_items_status           ON items(status)")

    # Indice unico parziale: impedisce notifiche duplicate sullo stesso item.
    # WHERE user_action IS NULL = solo notifiche ancora aperte vengono considerate.
    # Se l'utente ha gia risposto (KEEP/DELETE) un nuovo ciclo puo riaprirne una.
    c.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_item_active
        ON notifications(item_id)
        WHERE user_action IS NULL
    """)

    # Trigger 

    # Quando una notifica riceve user_action = 'DELETE' → item diventa DELETED.
    # Sostituisce il doppio UPDATE manuale in main.py (rimane come fallback).
    c.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_notify_delete
        AFTER UPDATE OF user_action ON notifications
        WHEN NEW.user_action = 'DELETE'
        BEGIN
            UPDATE items SET status = 'DELETED' WHERE id = NEW.item_id;
        END
    """)

    # Quando una notifica riceve user_action = 'KEEP' → item diventa KEPT.
    # Lo scanner skippa gli item con status != 'ACTIVE', evitando di risegnalare
    # file che l'utente ha scelto consciamente di mantenere.
    c.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_notify_keep
        AFTER UPDATE OF user_action ON notifications
        WHEN NEW.user_action = 'KEEP'
        BEGIN
            UPDATE items SET status = 'KEPT' WHERE id = NEW.item_id;
        END
    """)

    # Quando un duplicato viene eliminato, controlla se nel suo gruppo rimane
    # almeno un'altra copia ACTIVE. Se il gruppo scende a 1 solo file, quel file
    # non e piu un duplicato: viene marcato ORPHAN e sparisce dalla UI.
    c.execute("""
        CREATE TRIGGER IF NOT EXISTS trg_duplicate_orphan
        AFTER UPDATE OF status ON duplicates
        WHEN NEW.status = 'DELETED'
        BEGIN
            UPDATE duplicates
            SET status = 'ORPHAN'
            WHERE file_hash = NEW.file_hash
              AND status = 'ACTIVE'
              AND (
                  SELECT COUNT(*)
                  FROM duplicates
                  WHERE file_hash = NEW.file_hash
                    AND status = 'ACTIVE'
              ) < 2;
        END
    """)

    conn.commit()
    conn.close()
    print("DB inizializzato.")


if __name__ == "__main__":
    init_db()