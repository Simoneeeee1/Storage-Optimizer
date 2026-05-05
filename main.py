from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
import sqlite3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_NAME = "system_transparency.db"
app = FastAPI()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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


# ------------------ SCHEDULER ------------------

def process_expired_notifications():
    conn = get_db_direct()
    try:
        expired = conn.execute("""
            SELECT n.id AS notif_id, n.item_id, i.name, i.size_gb
            FROM notifications n
            JOIN items i ON i.id = n.item_id
            WHERE n.user_action IS NULL
              AND n.expires_at <= datetime('now')
              AND i.name NOT IN (SELECT name FROM exceptions)
        """).fetchall()

        for row in expired:
            conn.execute(
                "UPDATE notifications SET user_action = 'DELETE' WHERE id = ? AND user_action IS NULL",
                (row["notif_id"],),
            )

            conn.execute(
                "UPDATE items SET status = 'DELETED' WHERE id = ?",
                (row["item_id"],),
            )

            conn.execute(
                """
                INSERT INTO audit_logs (item_name, action, reason, size_gb)
                VALUES (?, 'DELETE', 'Eliminato automaticamente per policy', ?)
                """,
                (row["name"], row["size_gb"]),
            )

        if expired:
            conn.commit()

    except Exception as e:
        logger.error(e)
    finally:
        conn.close()


scheduler = BackgroundScheduler()
scheduler.add_job(process_expired_notifications, "interval", minutes=1)


@app.on_event("startup")
def startup():
    scheduler.start()


@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown()


# ------------------ API ------------------

@app.get("/api/notifications")
def get_notifications(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("""
        SELECT n.id, i.name, i.type, i.size_gb,
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
        SELECT n.id, n.item_id, i.name, i.size_gb
        FROM notifications n
        JOIN items i ON i.id = n.item_id
        WHERE n.id = ? AND n.user_action IS NULL
    """, (notification_id,)).fetchone()

    if not notif:
        raise HTTPException(status_code=404, detail="Notifica non trovata")

    with db:
        db.execute(
            "UPDATE notifications SET user_action = 'DELETE' WHERE id = ? AND user_action IS NULL",
            (notification_id,),
        )

        db.execute(
            "UPDATE items SET status = 'DELETED' WHERE id = ?",
            (notif["item_id"],),
        )

        db.execute(
            """
            INSERT INTO audit_logs (item_name, action, reason, size_gb)
            VALUES (?, 'DELETE', 'Eliminato manualmente', ?)
            """,
            (notif["name"], notif["size_gb"]),
        )

    return {"status": "ok"}


@app.post("/api/keep/{notification_id}")
def keep_item(notification_id: int, db: sqlite3.Connection = Depends(get_db)):
    with db:
        db.execute(
            "UPDATE notifications SET user_action = 'KEEP' WHERE id = ? AND user_action IS NULL",
            (notification_id,),
        )

        db.execute("""
            INSERT INTO audit_logs (item_name, action, reason, size_gb)
            SELECT i.name, 'KEEP', 'Confermato', i.size_gb
            FROM items i JOIN notifications n ON n.item_id = i.id
            WHERE n.id = ?
        """, (notification_id,))

    return {"status": "ok"}


@app.get("/api/audit")
def get_audit(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/status")
def get_status(db: sqlite3.Connection = Depends(get_db)):
    # Sommiamo sia i DELETE (positivi) che i REINSTALL (negativi)
    # Usiamo IN ('DELETE', 'REINSTALL') per essere sicuri di calcolare il netto
    res = db.execute("""
        SELECT SUM(size_gb) AS total 
        FROM audit_logs 
        WHERE action IN ('DELETE', 'REINSTALL')
    """).fetchone()
    
    return {"total_saved": round(res["total"] or 0.0, 1)}


@app.post("/api/reinstall/{audit_id}")
def reinstall_item(audit_id: int, db: sqlite3.Connection = Depends(get_db)):
    old = db.execute("SELECT item_name, size_gb FROM audit_logs WHERE id = ?", (audit_id,)).fetchone()

    if not old:
        raise HTTPException(status_code=404)

    db.execute("""
        INSERT INTO audit_logs (item_name, action, reason, size_gb)
        VALUES (?, 'REINSTALL', 'Ripristinato', ?)
    """, (old["item_name"], -old["size_gb"]))

    db.commit()
    return {"status": "ok"}


@app.get("/api/exceptions")
def get_exceptions(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM exceptions ORDER BY added_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.post("/api/exceptions")
async def add_exception(data: dict, db: sqlite3.Connection = Depends(get_db)):
    db.execute(
        "INSERT OR IGNORE INTO exceptions (name, type) VALUES (?, ?)",
        (data["name"], data["type"]),
    )
    db.commit()
    return {"status": "ok"}


@app.delete("/api/exceptions/{name}")
def remove_exception(name: str, db: sqlite3.Connection = Depends(get_db)):
    db.execute("DELETE FROM exceptions WHERE name = ?", (name,))
    db.commit()
    return {"status": "removed"}