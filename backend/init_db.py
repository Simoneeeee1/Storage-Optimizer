import sqlite3
import logging

from backend.config import cfg

DB_NAME = str(cfg.DB_PATH)

logger = logging.getLogger(__name__)


# Schema 

_DDL = """
-- Elementi rilevati dallo scanner come candidati alla rimozione
CREATE TABLE IF NOT EXISTS items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT,
    type      TEXT CHECK(type IN ('FILE', 'APP', 'FOLDER')),
    size_gb   REAL,
    last_used DATETIME,
    real_path TEXT UNIQUE,
    status    TEXT DEFAULT 'ACTIVE'
              CHECK(status IN ('ACTIVE', 'DELETED', 'KEPT'))
);

-- Notifiche per ogni elemento candidato (finestra 48h)
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL,
    sent_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    expires_at  DATETIME,
    user_action TEXT     CHECK(user_action IN ('KEEP', 'DELETE', 'DELETED_EXTERNALLY')),
    FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
);

-- File duplicati (analisi MD5 a tre livelli)
CREATE TABLE IF NOT EXISTS duplicates (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash TEXT    NOT NULL,
    name      TEXT,
    size_gb   REAL,
    real_path TEXT    UNIQUE,
    status    TEXT    DEFAULT 'ACTIVE'
              CHECK(status IN ('ACTIVE', 'DELETED', 'ORPHAN')),
    found_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    mtime     REAL
);

-- Whitelist: elementi esclusi permanentemente da ogni analisi
CREATE TABLE IF NOT EXISTS exceptions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT    UNIQUE,
    type      TEXT    CHECK(type IN ('FILE', 'APP', 'FOLDER')),
    real_path TEXT,
    added_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Log immutabile di tutte le operazioni (solo INSERT, mai UPDATE/DELETE)
CREATE TABLE IF NOT EXISTS audit_logs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP,
    item_name      TEXT,
    action         TEXT     CHECK(action IN (
                       'SCAN_FOUND', 'DELETE', 'KEEP',
                       'REINSTALL', 'DELETE_DUPLICATE'
                   )),
    reason         TEXT,
    size_gb        REAL,
    real_path      TEXT,
    dry_run        INTEGER  DEFAULT 1,  -- 1 = simulazione, 0 = operazione reale
    moved_to_trash INTEGER  DEFAULT 0   -- 1 = spostato da noi → abilita il ripristino
);
"""


# Indici 

_INDEXES = """
-- Ricerca rapida per path (usata intensivamente da scanner e API)
CREATE INDEX IF NOT EXISTS idx_items_path
    ON items(real_path);

-- Filtro per status (usato da scanner per skippare KEPT/DELETED)
CREATE INDEX IF NOT EXISTS idx_items_status
    ON items(status);

-- Scadenza notifiche (usata dallo scheduler ogni 15m)
CREATE INDEX IF NOT EXISTS idx_notifications_expires
    ON notifications(expires_at);

-- Indice UNICO PARZIALE: impedisce a livello di DB notifiche duplicate
-- per lo stesso elemento finché la notifica è ancora aperta (user_action IS NULL).
-- INSERT OR IGNORE in scanner.py sfrutta questo indice per essere idempotente.
CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_unique_active
    ON notifications(item_id) WHERE user_action IS NULL;

-- Lookup audit per timestamp (usato dall'endpoint GET /api/audit)
CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON audit_logs(timestamp);

-- Lookup eccezioni per path (usato da scanner per filtrare elementi)
CREATE INDEX IF NOT EXISTS idx_exceptions_path
    ON exceptions(real_path);

-- Raggruppamento duplicati per hash (usato da GET /api/duplicates)
CREATE INDEX IF NOT EXISTS idx_duplicates_hash
    ON duplicates(file_hash);
"""


# Trigger SQL 
#
# I trigger gestiscono le transizioni di stato direttamente nel DB,
# garantendo atomicità anche in caso di crash tra due UPDATE Python.
# Il codice Python in main.py NON deve più fare gli UPDATE su items
# dopo aver aggiornato notifications — ci pensano i trigger.

_TRIGGERS = """
-- Quando l'utente (o lo scheduler) marca una notifica DELETE,
-- imposta automaticamente items.status = 'DELETED'.
CREATE TRIGGER IF NOT EXISTS trg_notify_delete
AFTER UPDATE OF user_action ON notifications
WHEN NEW.user_action = 'DELETE'
BEGIN
    UPDATE items SET status = 'DELETED' WHERE id = NEW.item_id;
END;

-- Quando l'utente marca una notifica KEEP,
-- imposta items.status = 'KEPT' → lo scanner skipperà questo elemento per sempre.
CREATE TRIGGER IF NOT EXISTS trg_notify_keep
AFTER UPDATE OF user_action ON notifications
WHEN NEW.user_action = 'KEEP'
BEGIN
    UPDATE items SET status = 'KEPT' WHERE id = NEW.item_id;
END;

-- Quando una copia duplicata viene eliminata, controlla se nel gruppo
-- rimane almeno un'altra copia ACTIVE.
-- Se il gruppo scende a un solo file, quest'ultimo non è più un duplicato:
-- viene marcato ORPHAN e sparisce automaticamente dalla UI.
CREATE TRIGGER IF NOT EXISTS trg_duplicate_orphan
AFTER UPDATE OF status ON duplicates
WHEN NEW.status = 'DELETED'
BEGIN
    UPDATE duplicates
    SET status = 'ORPHAN'
    WHERE file_hash = NEW.file_hash
      AND status    = 'ACTIVE'
      AND (
          SELECT COUNT(*)
          FROM duplicates
          WHERE file_hash = NEW.file_hash
            AND status    = 'ACTIVE'
      ) < 2;
END;
"""


# Migrazioni live 
#
# Aggiunge colonne mancanti su DB esistenti senza distruggere i dati.
# Ogni ALTER TABLE è in un try/except separato: se una colonna esiste già
# SQLite solleva OperationalError e si prosegue con le altre.

_MIGRATIONS = [
    ("audit_logs",    "moved_to_trash INTEGER DEFAULT 0"),
    ("duplicates",    "mtime REAL"),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for table, column_def in _MIGRATIONS:
        column_name = column_def.split()[0]
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")
            logger.info(f"[MIGRATION] Aggiunta colonna '{column_name}' a '{table}'")
        except sqlite3.OperationalError:
            pass  # colonna già presente, nessuna azione necessaria


# Inizializzazione 

def init_db() -> None:
    """
    Crea tabelle, indici e trigger se non esistono ancora.
    Applica le migrazioni live per i DB già esistenti.
    Deve essere chiamata una sola volta all'avvio (o da CLI).
    """
    conn = sqlite3.connect(DB_NAME)
    try:
        # WAL mode: letture concorrenti durante le scritture
        # (fondamentale con FastAPI multi-thread + scheduler in background)
        conn.execute("PRAGMA journal_mode=WAL")

        # Integrità referenziale ON (SQLite la disabilita di default)
        conn.execute("PRAGMA foreign_keys=ON")

        conn.executescript(_DDL)
        conn.executescript(_INDEXES)
        conn.executescript(_TRIGGERS)
        _apply_migrations(conn)
        conn.commit()
        logger.info("DB inizializzato correttamente.")
        print("DB inizializzato.")
    except Exception as e:
        logger.error(f"Errore inizializzazione DB: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()


# Factory connessione 

def get_connection() -> sqlite3.Connection:
    """
    Apre e restituisce una connessione al DB pronta all'uso.

    Impostazioni applicate ad ogni connessione:
      - row_factory = sqlite3.Row  → accesso alle colonne per nome
      - WAL mode                   → concorrenza lettura/scrittura
      - foreign_keys = ON          → integrità referenziale attiva
      - timeout = 10s              → evita 'database is locked' sotto carico
    """
    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    init_db()