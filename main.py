import os
import sqlite3
import logging
from send2trash import send2trash

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler

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

DB_NAME = "system_transparency.db"
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


#  DB 
def get_db():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def get_db_direct():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


#  Helpers trash 
def move_to_trash(real_path: str) -> bool:
    """
    Sposta il file o la cartella nel cestino di sistema invece di eliminarli
    definitivamente. Supporta Windows, macOS e Linux.
    """
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


def log_action(conn, item_name: str, action: str, reason: str, size_gb: float, real_path: str):
    """
    Scrive in audit_logs marcando dry_run in base alla costante importata da scanner.
    """
    conn.execute("""
        INSERT INTO audit_logs (item_name, action, reason, size_gb, real_path, dry_run)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (item_name, action, reason, round(size_gb, 2), real_path, 1 if DRY_RUN else 0))

    prefix = "[DRY RUN]" if DRY_RUN else "[REALE]"
    logger.info(f"{prefix} {action} — '{item_name}' ({round(size_gb, 2)} GB) | {real_path} | {reason}")


#  Scheduler 
def process_expired_notifications():
    """
    Gira ogni minuto.
    Raccoglie le notifiche scadute senza risposta e le processa:
    - DRY_RUN = True  → solo log, niente disco
    - DRY_RUN = False → eliminazione reale
    """
    conn = get_db_direct()
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
                logger.info(
                    f"[DRY RUN] DELETE automatico scaduto — '{row['name']}' "
                    f"({row['size_gb']} GB) | path: {row['real_path']}"
                )
            else:
                success = move_to_trash(row["real_path"])

            if success:
                conn.execute(
                    "UPDATE notifications SET user_action = 'DELETE' WHERE id = ?",
                    (row["notif_id"],)
                )
                conn.execute(
                    "UPDATE items SET status = 'DELETED' WHERE id = ?",
                    (row["item_id"],)
                )
                log_action(
                    conn,
                    item_name=row["name"],
                    action="DELETE",
                    reason="Eliminato automaticamente per policy (notifica scaduta)",
                    size_gb=row["size_gb"],
                    real_path=row["real_path"]
                )

        if expired:
            conn.commit()

    except Exception as e:
        logger.error(f"Errore scheduler: {e}")
    finally:
        conn.close()


scheduler = BackgroundScheduler()
scheduler.add_job(scan, "interval", hours=6)
scheduler.add_job(process_expired_notifications, "interval", minutes=1)


@app.on_event("startup")
def startup():
    scheduler.start()
    logger.info(f"Server avviato — modalità: {'DRY RUN' if DRY_RUN else 'REALE'}")


@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()


#  API 

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
        logger.info(
            f"[DRY RUN] DELETE manuale — '{notif['name']}' "
            f"({notif['size_gb']} GB) | path: {notif['real_path']}"
        )
    else:
        success = move_to_trash(notif["real_path"])

    if not success:
        raise HTTPException(status_code=500, detail="Errore durante l'eliminazione dal disco")

    with db:
        db.execute(
            "UPDATE notifications SET user_action = 'DELETE' WHERE id = ?",
            (notification_id,)
        )
        db.execute(
            "UPDATE items SET status = 'DELETED' WHERE id = ?",
            (notif["item_id"],)
        )
        log_action(
            db,
            item_name=notif["name"],
            action="DELETE",
            reason="Eliminato manualmente dall'utente",
            size_gb=notif["size_gb"],
            real_path=notif["real_path"]
        )

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
        db.execute(
            "UPDATE notifications SET user_action = 'KEEP' WHERE id = ?",
            (notification_id,)
        )
        log_action(
            db,
            item_name=notif["name"],
            action="KEEP",
            reason="Confermato dall'utente",
            size_gb=notif["size_gb"],
            real_path=notif["real_path"]
        )

    return {"status": "ok"}


@app.get("/api/audit")
def get_audit(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM audit_logs ORDER BY timestamp DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/status")
def get_status(db: sqlite3.Connection = Depends(get_db)):
    res = db.execute("""
        SELECT SUM(size_gb) AS total FROM audit_logs
        WHERE action = 'DELETE'
    """).fetchone()
    return {
        "total_saved": round(res["total"] or 0.0, 1),
        "dry_run": DRY_RUN
    }


@app.post("/api/reinstall/{audit_id}")
def reinstall_item(audit_id: int, db: sqlite3.Connection = Depends(get_db)):
    old = db.execute(
        "SELECT item_name, size_gb, real_path FROM audit_logs WHERE id = ?",
        (audit_id,)
    ).fetchone()

    if not old:
        raise HTTPException(status_code=404)

    if DRY_RUN:
        logger.info(f"[DRY RUN] REINSTALL — '{old['item_name']}' | path: {old['real_path']}")
    else:
        logger.info(f"[REALE] REINSTALL richiesto per '{old['item_name']}' — implementare restore da cestino")

    with db:
        db.execute("""
            INSERT INTO audit_logs (item_name, action, reason, size_gb, real_path, dry_run)
            VALUES (?, 'REINSTALL', 'Ripristinato', ?, ?, ?)
        """, (old["item_name"], -old["size_gb"], old["real_path"], 1 if DRY_RUN else 0))

    db.commit()
    return {"status": "ok", "dry_run": DRY_RUN}


@app.get("/api/exceptions")
def get_exceptions(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM exceptions ORDER BY added_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/exceptions")
async def add_exception(data: dict, db: sqlite3.Connection = Depends(get_db)):
    # real_path opzionale: se presente permette match preciso sul disco nello scanner
    real_path = data.get("real_path", None)
    db.execute(
        "INSERT OR IGNORE INTO exceptions (name, type, real_path) VALUES (?, ?, ?)",
        (data["name"], data["type"], real_path)
    )
    db.commit()
    logger.info(f"Eccezione aggiunta: {data['name']} ({data['type']}) | path: {real_path}")
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
    logger.info(f"Eccezione rimossa: {ex['name']} (id: {exception_id})")
    return {"status": "removed"}


@app.get("/api/config")
def get_config():
    """Espone la modalità corrente al frontend."""
    return {"dry_run": DRY_RUN}