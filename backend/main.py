import os
import platform
import sqlite3
import logging
import threading
import ctypes
from collections import defaultdict
from contextlib import asynccontextmanager

from send2trash import send2trash
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

from backend.config import cfg
from backend.init_db import get_connection
from backend.scanner import scan

DRY_RUN = cfg.DRY_RUN

# Logging 

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(cfg.LOG_DIR / "main.log"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


# Stato scan (thread-safe) 
#
# _scan_lock protegge il flag _scan_running da race condition:
# senza lock, due richieste simultanee a /api/scan/trigger possono
# leggere False entrambe prima che una imposti True, avviando due scan.

_scan_lock    = threading.Lock()
_scan_running = False


# Scheduler 

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(scan, "interval", hours=6)
    scheduler.add_job(process_expired_notifications, "interval", minutes=15)
    scheduler.start()
    logger.info(f"Server avviato — modalità: {'DRY RUN' if cfg.DRY_RUN else 'REALE'}")
    yield
    scheduler.shutdown()
    logger.info("Server spento — scheduler fermato.")


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# DB helper 

def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


# Path helper 

def normalize(path: str) -> str:
    """Normalizza i separatori di path (fix Windows path misti)."""
    return os.path.normpath(path)


# Disk helpers 

def move_to_trash(real_path: str) -> bool:
    """
    Sposta il file nel cestino di sistema tramite send2trash.
    Funziona su Windows, macOS e Linux.
    Ritorna True se l'operazione è riuscita, False altrimenti.
    """
    real_path = normalize(real_path)
    try:
        if os.path.exists(real_path):
            send2trash(real_path)
            logger.info(f"[DISK] Spostato nel cestino: {real_path}")
            return True
        logger.warning(f"[DISK] Percorso non trovato: {real_path}")
        return False
    except Exception as e:
        logger.error(f"[DISK] Errore spostamento nel cestino di {real_path}: {e}")
        return False


def restore_from_trash(real_path: str) -> bool:
    """
    Ripristina un file dal cestino alla posizione originale.

    Supporto per SO:
      - Windows: usa winshell con quattro strategie di confronto in cascata
        (path completo → nome+cartella → solo nome → stem+cartella).
        winshell a volte omette l'estensione, da cui la strategia 4.
        Richiede: pip install winshell pywin32
      - macOS / Linux: send2trash non espone API di ripristino.
        Il file deve essere recuperato manualmente dal cestino di sistema.
        Ritorna False con log esplicito — l'endpoint gestisce questo caso
        restituendo un errore 501 comprensibile all'utente.
    """
    real_path = normalize(real_path)

    if platform.system() != "Windows":
        logger.warning(
            f"[DISK] Ripristino automatico non supportato su {platform.system()}. "
            f"Recupera manualmente dal cestino: {real_path}"
        )
        return False

    return _restore_from_trash_windows(real_path)


def _restore_from_trash_windows(real_path: str) -> bool:
    """
    Implementazione Windows-only del ripristino dal cestino.
    Separata da restore_from_trash per chiarezza e testabilità.

    Strategie di confronto (in ordine di precisione):
      1. Path completo case-insensitive
      2. Nome file + cartella padre
      3. Solo nome file (fallback)
      4. Stem senza estensione + cartella padre
         (winshell omette l'estensione per alcuni tipi di file)

    Costruisce un indice { stem_lower: [items] } una sola volta
    invece di scorrere la lista per ogni strategia → O(1) lookup.
    """
    target_name = os.path.basename(real_path).lower()
    target_stem = os.path.splitext(target_name)[0]
    real_parent = os.path.dirname(real_path).lower()

    # Flag per sapere se CoInitialize è andata a buon fine,
    # così CoUninitialize viene chiamata solo se necessario.
    com_initialized = False

    try:
        import winshell  # Windows-only, importato qui per non crashare su altri SO

        ctypes.windll.ole32.CoInitialize(None)
        com_initialized = True

        items = list(winshell.recycle_bin())
        logger.info(f"[TRASH] Cercando {real_path!r} — elementi: {len(items)}")

        # Indice per lookup O(1): { stem_lower -> [(item, ip, iname, iparent)] }
        index: dict[str, list] = defaultdict(list)
        for item in items:
            try:
                ip = normalize(item.original_filename())
            except Exception:
                continue
            iname   = os.path.basename(ip).lower()
            istem   = os.path.splitext(iname)[0]
            iparent = os.path.dirname(ip).lower()
            index[istem].append((item, ip, iname, iparent))

        candidates = index.get(target_stem, [])

        # Strategia 1: path completo
        for item, ip, iname, iparent in candidates:
            if ip.lower() == real_path.lower():
                item.undelete()
                logger.info(f"[DISK] Ripristinato (path esatto): {real_path}")
                return True

        # Strategia 2: nome + cartella padre
        for item, ip, iname, iparent in candidates:
            if iname == target_name and iparent == real_parent:
                item.undelete()
                logger.info(f"[DISK] Ripristinato (nome + cartella): {real_path}")
                return True

        # Strategia 3: solo nome file
        for item, ip, iname, iparent in candidates:
            if iname == target_name:
                item.undelete()
                logger.info(f"[DISK] Ripristinato (solo nome): {ip}")
                return True

        # Strategia 4: stem + cartella (winshell tronca l'estensione)
        for item, ip, iname, iparent in candidates:
            if iparent == real_parent:
                item.undelete()
                logger.info(f"[DISK] Ripristinato (stem + cartella): {ip}")
                return True

        logger.warning(f"[DISK] File non trovato nel cestino: {real_path}")
        return False

    except ImportError:
        logger.error("[DISK] winshell non installato — esegui: pip install winshell pywin32")
        return False
    except Exception as e:
        logger.error(f"[DISK] Errore ripristino di {real_path}: {e}")
        return False
    finally:
        if com_initialized:
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass


# Audit log 

def log_action(
    conn: sqlite3.Connection,
    item_name: str,
    action: str,
    reason: str,
    size_gb: float,
    real_path: str,
    moved_to_trash: int = 0,
) -> None:
    """
    Scrive un'azione in audit_logs.
    moved_to_trash=1 solo quando siamo stati noi a spostare il file
    nel cestino — permette al frontend di mostrare il bottone Ripristina
    solo per operazioni che possiamo effettivamente annullare.
    """
    conn.execute("""
        INSERT INTO audit_logs
            (item_name, action, reason, size_gb, real_path, dry_run, moved_to_trash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (item_name, action, reason, round(size_gb, 2), real_path,
          1 if DRY_RUN else 0, moved_to_trash))

    prefix = "[DRY RUN]" if DRY_RUN else "[REALE]"
    logger.info(
        f"{prefix} {action} — '{item_name}' ({round(size_gb, 2)} GB) "
        f"| {real_path} | {reason}"
    )


# Scheduler job 

def _is_in_exception_sql(name: str, real_path: str) -> tuple[str, tuple]:
    """
    Restituisce frammento SQL e parametri per il controllo whitelist,
    coerente con is_in_exception() in scanner.py:
    controlla real_path, nome completo e nome senza .app.
    """
    name_clean = name.removesuffix(".app").strip()
    sql = """
        NOT EXISTS (
            SELECT 1 FROM exceptions e
            WHERE e.real_path = ? OR e.name = ? OR e.name = ?
        )
    """
    return sql, (real_path, name, name_clean)


def process_expired_notifications() -> None:
    """
    Elabora le notifiche scadute senza azione utente.
    Aggiorna solo notifications.user_action — il trigger trg_notify_delete
    propaga automaticamente items.status = 'DELETED'.

    Il controllo whitelist usa real_path + nome + nome senza .app,
    coerente con is_in_exception() in scanner.py (fix bug #4).
    """
    conn = get_connection()
    try:
        expired = conn.execute("""
            SELECT n.id AS notif_id, n.item_id, i.name, i.size_gb, i.real_path
            FROM notifications n
            JOIN items i ON i.id = n.item_id
            WHERE n.user_action IS NULL
              AND n.expires_at <= datetime('now')
              AND i.status = 'ACTIVE'
              AND NOT EXISTS (
                  SELECT 1 FROM exceptions e
                  WHERE e.real_path = i.real_path
                     OR e.name      = i.name
                     OR e.name      = TRIM(REPLACE(i.name, '.app', ''))
              )
        """).fetchall()

        for row in expired:
            sent_to_trash = False
            success       = True

            if DRY_RUN:
                logger.info(
                    f"[DRY RUN] DELETE automatico scaduto — "
                    f"'{row['name']}' ({row['size_gb']} GB)"
                )
            else:
                real_path = normalize(row["real_path"])
                if not os.path.exists(real_path):
                    logger.warning(
                        f"[DISK] File già assente, aggiorno solo DB: {real_path}"
                    )
                else:
                    success       = move_to_trash(real_path)
                    sent_to_trash = success

            if success:
                # Il trigger trg_notify_delete aggiorna items.status automaticamente
                conn.execute(
                    "UPDATE notifications SET user_action = 'DELETE' WHERE id = ?",
                    (row["notif_id"],)
                )
                log_action(
                    conn,
                    item_name=row["name"],
                    action="DELETE",
                    reason="Eliminato automaticamente per policy (notifica scaduta)",
                    size_gb=row["size_gb"],
                    real_path=row["real_path"],
                    moved_to_trash=1 if sent_to_trash else 0,
                )

        if expired:
            conn.commit()

    except Exception as e:
        logger.error(f"Errore scheduler: {e}")
        conn.rollback()
    finally:
        conn.close()


# Scan trigger 

def _run_scan_safe() -> None:
    """
    Esegue la scansione garantendo che non ci siano run concorrenti.
    Il lock protegge il flag sia in lettura che in scrittura,
    incluso il reset finale nel blocco finally.
    """
    global _scan_running

    with _scan_lock:
        if _scan_running:
            logger.warning("Scan già in corso, skip.")
            return
        _scan_running = True

    try:
        logger.info("Scan manuale avviato.")
        scan()
        logger.info("Scan manuale completato.")
    except Exception as e:
        logger.error(f"Errore scan manuale: {e}")
    finally:
        # Reset dentro il lock: evita race condition su runtime
        # senza GIL (es. nogil CPython, PyPy STM).
        with _scan_lock:
            _scan_running = False


@app.post("/api/scan/trigger")
def trigger_scan(background_tasks: BackgroundTasks):
    with _scan_lock:
        if _scan_running:
            return {"status": "already_running", "message": "Uno scan è già in corso."}
    background_tasks.add_task(_run_scan_safe)
    return {"status": "started", "message": "Scansione avviata in background."}


@app.get("/api/scan/status")
def scan_status():
    return {"running": _scan_running}


# API: notifiche 

@app.get("/api/notifications")
def get_notifications(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("""
        SELECT
            n.id,
            i.name,
            i.type,
            i.size_gb,
            i.real_path,
            MAX(0, CAST(
                (strftime('%s', n.expires_at) - strftime('%s', 'now')) / 3600
            AS INTEGER)) || 'h' AS remaining_time
        FROM notifications n
        JOIN items i ON i.id = n.item_id
        WHERE n.user_action IS NULL
          AND i.status = 'ACTIVE'
          AND n.expires_at > datetime('now')
          AND NOT EXISTS (
              SELECT 1 FROM exceptions e
              WHERE e.real_path = i.real_path
                 OR e.name      = i.name
                 OR e.name      = TRIM(REPLACE(i.name, '.app', ''))
          )
    """).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/delete/{notification_id}")
def delete_item(notification_id: int, db: sqlite3.Connection = Depends(get_db)):
    # BEGIN IMMEDIATE: blocca altri writer fin dalla SELECT,
    # evitando la race condition lettura→scrittura sulla stessa notifica (bug #5).
    db.execute("BEGIN IMMEDIATE")
    try:
        notif = db.execute("""
            SELECT n.id, n.item_id, i.name, i.size_gb, i.real_path
            FROM notifications n
            JOIN items i ON i.id = n.item_id
            WHERE n.id = ? AND n.user_action IS NULL AND i.status = 'ACTIVE'
        """, (notification_id,)).fetchone()

        if not notif:
            db.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail="Notifica non trovata")

        real_path     = normalize(notif["real_path"])
        sent_to_trash = False

        if DRY_RUN:
            logger.info(f"[DRY RUN] DELETE manuale — '{notif['name']}'")
        else:
            if not os.path.exists(real_path):
                logger.warning(f"[DISK] File già assente, aggiorno solo DB: {real_path}")
            else:
                if not move_to_trash(real_path):
                    db.execute("ROLLBACK")
                    raise HTTPException(
                        status_code=500,
                        detail="Errore durante l'eliminazione dal disco"
                    )
                sent_to_trash = True

        # Il trigger trg_notify_delete aggiorna items.status automaticamente
        db.execute(
            "UPDATE notifications SET user_action = 'DELETE' WHERE id = ?",
            (notification_id,)
        )
        log_action(
            db,
            item_name=notif["name"],
            action="DELETE",
            reason="Eliminato manualmente dall'utente",
            size_gb=notif["size_gb"],
            real_path=real_path,
            moved_to_trash=1 if sent_to_trash else 0,
        )
        db.execute("COMMIT")
    except HTTPException:
        raise
    except Exception as e:
        db.execute("ROLLBACK")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"status": "ok", "dry_run": DRY_RUN}


@app.post("/api/keep/{notification_id}")
def keep_item(notification_id: int, db: sqlite3.Connection = Depends(get_db)):
    # BEGIN IMMEDIATE: stessa protezione TOCTOU di delete_item.
    db.execute("BEGIN IMMEDIATE")
    try:
        notif = db.execute("""
            SELECT n.id, n.item_id, i.name, i.size_gb, i.real_path
            FROM notifications n
            JOIN items i ON i.id = n.item_id
            WHERE n.id = ? AND n.user_action IS NULL AND i.status = 'ACTIVE'
        """, (notification_id,)).fetchone()

        if not notif:
            db.execute("ROLLBACK")
            raise HTTPException(status_code=404, detail="Notifica non trovata")

        # Il trigger trg_notify_keep aggiorna items.status = 'KEPT' automaticamente.
        # Aggiungiamo anche exceptions per garantire che l'elemento non ricompaia
        # mai più nelle scansioni future, anche dopo un reset di status (bug #2).
        db.execute(
            "UPDATE notifications SET user_action = 'KEEP' WHERE id = ?",
            (notification_id,)
        )
        db.execute("""
            INSERT OR IGNORE INTO exceptions (name, type, real_path)
            SELECT i.name, i.type, i.real_path
            FROM items i WHERE i.id = ?
        """, (notif["item_id"],))

        log_action(
            db,
            item_name=notif["name"],
            action="KEEP",
            reason="Confermato dall'utente — aggiunto a whitelist permanente",
            size_gb=notif["size_gb"],
            real_path=notif["real_path"],
        )
        db.execute("COMMIT")
    except HTTPException:
        raise
    except Exception as e:
        db.execute("ROLLBACK")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"status": "ok"}


# API: audit & status 

@app.get("/api/audit")
def get_audit(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM audit_logs ORDER BY timestamp DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/status")
def get_status(db: sqlite3.Connection = Depends(get_db)):
    saved = db.execute(
        "SELECT COALESCE(SUM(size_gb), 0.0) AS total FROM audit_logs WHERE action = 'DELETE'"
    ).fetchone()
    kept = db.execute(
        "SELECT COUNT(*) AS cnt FROM items WHERE status = 'KEPT'"
    ).fetchone()
    return {
        "total_saved":  round(saved["total"], 1),
        "dry_run":      DRY_RUN,
        "scan_running": _scan_running,
        "kept_count":   kept["cnt"],
    }


@app.post("/api/reinstall/{audit_id}")
def reinstall_item(audit_id: int, db: sqlite3.Connection = Depends(get_db)):
    old = db.execute("""
        SELECT item_name, size_gb, real_path, moved_to_trash
        FROM audit_logs WHERE id = ?
    """, (audit_id,)).fetchone()

    if not old:
        raise HTTPException(status_code=404, detail="Log non trovato")

    # Blocca il reinstall se non siamo stati noi a spostare il file
    if not DRY_RUN and not old["moved_to_trash"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "Questo file non è stato spostato nel cestino dal sistema — "
                "impossibile ripristinarlo automaticamente"
            )
        )

    real_path = normalize(old["real_path"])

    if DRY_RUN:
        logger.info(f"[DRY RUN] REINSTALL — '{old['item_name']}'")
        success = True
    else:
        success = restore_from_trash(real_path)

    if not success:
        if platform.system() != "Windows":
            raise HTTPException(
                status_code=501,
                detail=(
                    f"Ripristino automatico non supportato su {platform.system()}. "
                    "Recupera il file manualmente dal cestino di sistema."
                )
            )
        raise HTTPException(
            status_code=500,
            detail="File non trovato nel cestino — potrebbe essere stato eliminato definitivamente"
        )

    with db:
        db.execute(
            "UPDATE items SET status = 'ACTIVE' WHERE real_path = ?",
            (real_path,)
        )
        db.execute("""
            INSERT INTO audit_logs
                (item_name, action, reason, size_gb, real_path, dry_run, moved_to_trash)
            VALUES (?, 'REINSTALL', 'Ripristinato dal cestino', ?, ?, ?, 0)
        """, (old["item_name"], -old["size_gb"], real_path, 1 if DRY_RUN else 0))

    return {"status": "ok", "dry_run": DRY_RUN}


# API: eccezioni 

@app.get("/api/exceptions")
def get_exceptions(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM exceptions ORDER BY added_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/exceptions")
async def add_exception(data: dict, db: sqlite3.Connection = Depends(get_db)):
    name      = data.get("name")
    item_type = data.get("type")
    real_path = data.get("real_path")

    if not name or not item_type:
        raise HTTPException(
            status_code=422,
            detail="I campi 'name' e 'type' sono obbligatori"
        )
    if real_path:
        real_path = normalize(real_path)

    db.execute(
        "INSERT OR IGNORE INTO exceptions (name, type, real_path) VALUES (?, ?, ?)",
        (name, item_type, real_path)
    )
    db.commit()
    return {"status": "ok"}


@app.delete("/api/exceptions/{exception_id}")
def remove_exception(exception_id: int, db: sqlite3.Connection = Depends(get_db)):
    ex = db.execute(
        "SELECT name FROM exceptions WHERE id = ?", (exception_id,)
    ).fetchone()
    if not ex:
        raise HTTPException(status_code=404, detail="Eccezione non trovata")
    db.execute("DELETE FROM exceptions WHERE id = ?", (exception_id,))
    db.commit()
    return {"status": "removed"}


@app.get("/api/config")
def get_config():
    return {"dry_run": DRY_RUN}


# API: duplicati 

@app.get("/api/duplicates")
def get_duplicates(db: sqlite3.Connection = Depends(get_db)):
    """
    Restituisce i duplicati raggruppati per hash MD5.
    Una sola query con subquery invece di due query separate —
    evita il round-trip e la costruzione manuale dei placeholders.
    """
    rows = db.execute("""
        SELECT id, file_hash, name, size_gb, real_path
        FROM duplicates
        WHERE status = 'ACTIVE'
          AND file_hash IN (
              SELECT file_hash
              FROM duplicates
              WHERE status = 'ACTIVE'
              GROUP BY file_hash
              HAVING COUNT(*) > 1
          )
        ORDER BY file_hash, found_at
    """).fetchall()

    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        grouped[r["file_hash"]].append(dict(r))

    return dict(grouped)


@app.post("/api/duplicates/delete/{duplicate_id}")
def delete_duplicate(duplicate_id: int, db: sqlite3.Connection = Depends(get_db)):
    dup = db.execute("""
        SELECT id, name, size_gb, real_path
        FROM duplicates
        WHERE id = ? AND status = 'ACTIVE'
    """, (duplicate_id,)).fetchone()

    if not dup:
        raise HTTPException(status_code=404, detail="File duplicato non trovato")

    real_path     = normalize(dup["real_path"])
    sent_to_trash = False

    if DRY_RUN:
        logger.info(f"[DRY RUN] DELETE DUPLICATO — '{dup['name']}' | {real_path}")
    else:
        if not os.path.exists(real_path):
            logger.warning(
                f"[DISK] Duplicato già assente, aggiorno solo DB: {real_path}"
            )
        else:
            if not move_to_trash(real_path):
                raise HTTPException(
                    status_code=500,
                    detail="Impossibile rimuovere il file dal disco"
                )
            sent_to_trash = True

    with db:
        # Il trigger trg_duplicate_orphan marca ORPHAN l'eventuale ultimo
        # duplicato rimasto solo nello stesso gruppo — nessun UPDATE aggiuntivo.
        db.execute(
            "UPDATE duplicates SET status = 'DELETED' WHERE id = ?",
            (duplicate_id,)
        )
        log_action(
            db,
            item_name=dup["name"],
            action="DELETE_DUPLICATE",
            reason="Rimozione copia duplicata superflua",
            size_gb=dup["size_gb"],
            real_path=real_path,
            moved_to_trash=1 if sent_to_trash else 0,
        )

    return {"status": "ok", "dry_run": DRY_RUN}