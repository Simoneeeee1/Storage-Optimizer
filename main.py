import os
import sqlite3
import logging
from collections import defaultdict
from contextlib import asynccontextmanager

from send2trash import send2trash
from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

from init_db import get_connection
from scanner import scan, DRY_RUN

# Logging 

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("main.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Flag per evitare scan concorrenti avviati dal trigger manuale
_scan_running = False

# Scheduler 

scheduler = BackgroundScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(scan, "interval", hours=6)
    scheduler.add_job(process_expired_notifications, "interval", minutes=15)
    scheduler.start()
    logger.info(f"Server avviato — modalita: {'DRY RUN' if DRY_RUN else 'REALE'}")
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


# DB helpers 

def get_db():
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


# Trash helper 

def move_to_trash(real_path: str) -> bool:
    try:
        if os.path.exists(real_path):
            send2trash(real_path)
            logger.info(f"[DISK] Elemento spostato nel cestino: {real_path}")
            return True
        else:
            logger.warning(f"[DISK] Percorso non trovato: {real_path}")
            return False
    except Exception as e:
        logger.error(f"[DISK] Errore durante lo spostamento nel cestino di {real_path}: {e}")
        return False


def log_action(conn: sqlite3.Connection, item_name: str, action: str,
               reason: str, size_gb: float, real_path: str):
    conn.execute("""
        INSERT INTO audit_logs (item_name, action, reason, size_gb, real_path, dry_run)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (item_name, action, reason, round(size_gb, 2), real_path, 1 if DRY_RUN else 0))

    prefix = "[DRY RUN]" if DRY_RUN else "[REALE]"
    logger.info(f"{prefix} {action} — '{item_name}' ({round(size_gb, 2)} GB) | {real_path} | {reason}")


# Scheduler job 

def process_expired_notifications():
    conn = get_connection()
    try:
        expired = conn.execute("""
            SELECT n.id AS notif_id, n.item_id, i.name, i.size_gb, i.real_path
            FROM notifications n
            JOIN items i ON i.id = n.item_id
            WHERE n.user_action IS NULL
              AND n.expires_at <= datetime('now')
              AND i.status = 'ACTIVE'
              AND i.name NOT IN (SELECT name FROM exceptions)
        """).fetchall()

        for row in expired:
            success = True
            if DRY_RUN:
                logger.info(f"[DRY RUN] DELETE automatico scaduto — '{row['name']}' ({row['size_gb']} GB)")
            else:
                success = move_to_trash(row["real_path"])

            if success:
                conn.execute("UPDATE notifications SET user_action = 'DELETE' WHERE id = ?", (row["notif_id"],))
                conn.execute("UPDATE items SET status = 'DELETED' WHERE id = ?", (row["item_id"],))
                log_action(conn, item_name=row["name"], action="DELETE",
                           reason="Eliminato automaticamente per policy (notifica scaduta)",
                           size_gb=row["size_gb"], real_path=row["real_path"])

        if expired:
            conn.commit()

    except Exception as e:
        logger.error(f"Errore scheduler: {e}")
    finally:
        conn.close()


# Scan trigger helper 

def _run_scan_safe():
    """Wrapper che impedisce run concorrenti e logga inizio/fine."""
    global _scan_running
    if _scan_running:
        logger.warning("Scan manuale richiesto ma uno scan e gia in corso, skip.")
        return
    _scan_running = True
    try:
        logger.info("Scan manuale avviato.")
        scan()
        logger.info("Scan manuale completato.")
    except Exception as e:
        logger.error(f"Errore durante scan manuale: {e}")
    finally:
        _scan_running = False


# API: scan trigger manuale 

@app.post("/api/scan/trigger")
def trigger_scan(background_tasks: BackgroundTasks):
    """
    Avvia una scansione immediata in background senza bloccare la risposta HTTP.
    Utile durante lo sviluppo o subito dopo aver spostato/aggiunto file.
    Restituisce immediatamente; il frontend puo interrogare /api/status o
    ricaricare le notifiche dopo qualche secondo.
    """
    if _scan_running:
        return {"status": "already_running", "message": "Uno scan e gia in corso."}
    background_tasks.add_task(_run_scan_safe)
    return {"status": "started", "message": "Scansione avviata in background."}


@app.get("/api/scan/status")
def scan_status():
    """Restituisce se uno scan e attualmente in esecuzione."""
    return {"running": _scan_running}


# API: notifiche 

@app.get("/api/notifications")
def get_notifications(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("""
        SELECT n.id, i.name, i.type, i.size_gb, i.real_path,
               MAX(0, CAST((strftime('%s', n.expires_at) - strftime('%s','now')) / 3600 AS INTEGER)) || 'h' AS remaining_time
        FROM notifications n
        JOIN items i ON i.id = n.item_id
        WHERE i.name NOT IN (SELECT name FROM exceptions)
          AND n.user_action IS NULL
          AND i.status = 'ACTIVE'
          AND n.expires_at > datetime('now')
    """).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/delete/{notification_id}")
def delete_item(notification_id: int, db: sqlite3.Connection = Depends(get_db)):
    notif = db.execute("""
        SELECT n.id, n.item_id, i.name, i.size_gb, i.real_path
        FROM notifications n
        JOIN items i ON i.id = n.item_id
        WHERE n.id = ? AND n.user_action IS NULL AND i.status = 'ACTIVE'
    """, (notification_id,)).fetchone()

    if not notif:
        raise HTTPException(status_code=404, detail="Notifica non trovata")

    success = True
    if DRY_RUN:
        logger.info(f"[DRY RUN] DELETE manuale — '{notif['name']}'")
    else:
        success = move_to_trash(notif["real_path"])

    if not success:
        raise HTTPException(status_code=500, detail="Errore durante l'eliminazione dal disco")

    with db:
        # UPDATE notifica → il trigger trg_notify_delete imposta items.status = 'DELETED'.
        # L'UPDATE esplicito e un fallback nel caso il DB venga ricreato senza trigger.
        db.execute("UPDATE notifications SET user_action = 'DELETE' WHERE id = ?", (notification_id,))
        db.execute("UPDATE items SET status = 'DELETED' WHERE id = ?", (notif["item_id"],))
        log_action(db, item_name=notif["name"], action="DELETE",
                   reason="Eliminato manualmente dall'utente",
                   size_gb=notif["size_gb"], real_path=notif["real_path"])

    return {"status": "ok", "dry_run": DRY_RUN}


@app.post("/api/keep/{notification_id}")
def keep_item(notification_id: int, db: sqlite3.Connection = Depends(get_db)):
    notif = db.execute("""
        SELECT n.id, i.name, i.size_gb, i.real_path
        FROM notifications n
        JOIN items i ON i.id = n.item_id
        WHERE n.id = ? AND n.user_action IS NULL
    """, (notification_id,)).fetchone()

    if not notif:
        raise HTTPException(status_code=404, detail="Notifica non trovata")

    with db:
        # UPDATE notifica → il trigger trg_notify_keep imposta items.status = 'KEPT'.
        # L'UPDATE esplicito e un fallback nel caso il DB venga ricreato senza trigger.
        db.execute("UPDATE notifications SET user_action = 'KEEP' WHERE id = ?", (notification_id,))
        db.execute(
            "UPDATE items SET status = 'KEPT' WHERE id = "
            "(SELECT item_id FROM notifications WHERE id = ?)",
            (notification_id,)
        )
        log_action(db, item_name=notif["name"], action="KEEP",
                   reason="Confermato dall'utente — escluso da scansioni future",
                   size_gb=notif["size_gb"], real_path=notif["real_path"])

    return {"status": "ok"}


# API: audit & status 

@app.get("/api/audit")
def get_audit(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/status")
def get_status(db: sqlite3.Connection = Depends(get_db)):
    res = db.execute(
        "SELECT SUM(size_gb) AS total FROM audit_logs WHERE action = 'DELETE'"
    ).fetchone()
    kept = db.execute(
        "SELECT COUNT(*) AS cnt FROM items WHERE status = 'KEPT'"
    ).fetchone()

    return {
        "total_saved":  round(res["total"] or 0.0, 1),
        "dry_run":      DRY_RUN,
        "scan_running": _scan_running,
        "kept_count":   kept["cnt"],   # elementi che l'utente ha scelto di mantenere
    }


@app.post("/api/reinstall/{audit_id}")
def reinstall_item(audit_id: int, db: sqlite3.Connection = Depends(get_db)):
    old = db.execute(
        "SELECT item_name, size_gb, real_path FROM audit_logs WHERE id = ?", (audit_id,)
    ).fetchone()
    if not old:
        raise HTTPException(status_code=404)

    if DRY_RUN:
        logger.info(f"[DRY RUN] REINSTALL — '{old['item_name']}'")
    else:
        logger.info(f"[REALE] REINSTALL richiesto per '{old['item_name']}'")

    with db:
        db.execute("""
            INSERT INTO audit_logs (item_name, action, reason, size_gb, real_path, dry_run)
            VALUES (?, 'REINSTALL', 'Ripristinato', ?, ?, ?)
        """, (old["item_name"], -old["size_gb"], old["real_path"], 1 if DRY_RUN else 0))

    return {"status": "ok", "dry_run": DRY_RUN}


# API: eccezioni 

@app.get("/api/exceptions")
def get_exceptions(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM exceptions ORDER BY added_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/exceptions")
async def add_exception(data: dict, db: sqlite3.Connection = Depends(get_db)):
    real_path = data.get("real_path", None)
    db.execute(
        "INSERT OR IGNORE INTO exceptions (name, type, real_path) VALUES (?, ?, ?)",
        (data["name"], data["type"], real_path)
    )
    db.commit()
    return {"status": "ok"}


@app.delete("/api/exceptions/{exception_id}")
def remove_exception(exception_id: int, db: sqlite3.Connection = Depends(get_db)):
    ex = db.execute("SELECT name FROM exceptions WHERE id = ?", (exception_id,)).fetchone()
    if not ex:
        raise HTTPException(status_code=404)
    db.execute("DELETE FROM exceptions WHERE id = ?", (exception_id,))
    db.commit()
    return {"status": "removed"}


@app.get("/api/config")
def get_config():
    return {"dry_run": DRY_RUN}


# API: duplicati 

@app.get("/api/duplicates")
def get_duplicates(db: sqlite3.Connection = Depends(get_db)):
    hashes = db.execute("""
        SELECT file_hash
        FROM duplicates
        WHERE status = 'ACTIVE'
        GROUP BY file_hash
        HAVING COUNT(*) > 1
    """).fetchall()

    if not hashes:
        return {}

    hash_list    = [r["file_hash"] for r in hashes]
    placeholders = ",".join("?" * len(hash_list))

    rows = db.execute(f"""
        SELECT id, file_hash, name, size_gb, real_path
        FROM duplicates
        WHERE status = 'ACTIVE'
          AND file_hash IN ({placeholders})
        ORDER BY file_hash, found_at
    """, hash_list).fetchall()

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

    success = True
    if DRY_RUN:
        logger.info(f"[DRY RUN] DELETE DUPLICATO — '{dup['name']}' | {dup['real_path']}")
    else:
        success = move_to_trash(dup["real_path"])

    if not success:
        raise HTTPException(status_code=500, detail="Impossibile rimuovere il file dal disco")

    with db:
        db.execute("UPDATE duplicates SET status = 'DELETED' WHERE id = ?", (duplicate_id,))
        log_action(
            db, item_name=dup["name"], action="DELETE_DUPLICATE",
            reason="Rimozione copia duplicata superflua",
            size_gb=dup["size_gb"], real_path=dup["real_path"]
        )

    return {"status": "ok", "dry_run": DRY_RUN}