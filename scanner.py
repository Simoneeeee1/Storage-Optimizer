import os
import sqlite3
import logging
from datetime import datetime, timedelta

DB_NAME = "system_transparency.db"
DRY_RUN = True  # ← Cambia a False solo quando sei pronto per operazioni reali

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scanner.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

#  Configurazione 
SCAN_TARGETS = [
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Desktop"),
    # "/Applications",  # decommentare su macOS
]

FILE_THRESHOLD_DAYS = 120
APP_THRESHOLD_DAYS  = 180
MIN_SIZE_GB         = 0.1


#  Helpers 
def get_size_gb(path: str) -> float:
    try:
        if os.path.isfile(path):
            return os.path.getsize(path) / (1024 ** 3)
        total = 0
        for dirpath, _, files in os.walk(path):
            for f in files:
                fp = os.path.join(dirpath, f)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
        return total / (1024 ** 3)
    except OSError:
        return 0.0


def get_last_used(path: str) -> datetime:
    try:
        ts = os.path.getatime(path)
        return datetime.fromtimestamp(ts)
    except OSError:
        return datetime.now()


def log_dry_run_action(conn, item_name: str, action: str, reason: str, size_gb: float, real_path: str):
    """
    In modalità DRY_RUN scrive solo nel DB e nel log,
    senza toccare nulla sul disco.
    """
    conn.execute("""
        INSERT INTO audit_logs (item_name, action, reason, size_gb, real_path, dry_run)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (item_name, action, reason, round(size_gb, 2), real_path))

    logger.info(
        f"[DRY RUN] {action} — '{item_name}' ({round(size_gb, 2)} GB) "
        f"| path: {real_path} | motivo: {reason}"
    )


#  Scanner principale 
def scan():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row

    scanned = 0
    nuovi   = 0
    saltati = 0

    for folder in SCAN_TARGETS:
        if not os.path.exists(folder):
            logger.warning(f"Cartella non trovata, salto: {folder}")
            continue

        logger.info(f"Scansione: {folder}")

        for entry in os.scandir(folder):
            path = entry.path
            name = entry.name
            scanned += 1

            # Salta file nascosti
            if name.startswith("."):
                saltati += 1
                continue

            # Controlla eccezioni: prima per path reale, poi per nome (con e senza .app)
            name_clean = name.removesuffix(".app").strip()
            in_exception = conn.execute("""
                SELECT 1 FROM exceptions
                WHERE real_path = ?
                   OR name = ?
                   OR name = ?
            """, (path, name, name_clean)).fetchone()
            if in_exception:
                logger.debug(f"Eccezione attiva, salto: {name}")
                saltati += 1
                continue

            size_gb   = get_size_gb(path)
            last_used = get_last_used(path)

            # Salta file troppo piccoli
            if size_gb < MIN_SIZE_GB:
                saltati += 1
                continue

            item_type = "APP"    if name.endswith(".app") else \
                        "FOLDER" if os.path.isdir(path)   else "FILE"

            threshold     = APP_THRESHOLD_DAYS if item_type == "APP" else FILE_THRESHOLD_DAYS
            days_inactive = (datetime.now() - last_used).days

            existing = conn.execute(
                "SELECT id, size_gb FROM items WHERE real_path = ?", (path,)
            ).fetchone()

            if existing:
                # Aggiorna size e last_used se già presente
                conn.execute("""
                    UPDATE items SET size_gb = ?, last_used = ?
                    WHERE real_path = ?
                """, (round(size_gb, 2), last_used.strftime('%Y-%m-%d %H:%M:%S'), path))
            else:
                # Non ancora abbastanza inattivo → salta
                if days_inactive < threshold:
                    saltati += 1
                    continue

                # Inserisce nuovo candidato
                conn.execute("""
                    INSERT INTO items (name, type, size_gb, last_used, real_path)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    name,
                    item_type,
                    round(size_gb, 2),
                    last_used.strftime('%Y-%m-%d %H:%M:%S'),
                    path
                ))
                item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                # Crea notifica con scadenza 48h
                expires_at = (datetime.now() + timedelta(hours=48)).strftime('%Y-%m-%d %H:%M:%S')
                conn.execute("""
                    INSERT INTO notifications (item_id, sent_at, expires_at)
                    VALUES (?, CURRENT_TIMESTAMP, ?)
                """, (item_id, expires_at))

                nuovi += 1

                if DRY_RUN:
                    log_dry_run_action(
                        conn,
                        item_name=name,
                        action="SCAN_FOUND",
                        reason=f"Inattivo da {days_inactive} giorni (soglia: {threshold}gg)",
                        size_gb=size_gb,
                        real_path=path
                    )

    conn.commit()
    conn.close()

    logger.info(
        f"Scansione completata — "
        f"totale: {scanned}, nuovi candidati: {nuovi}, saltati: {saltati}"
    )


if __name__ == "__main__":
    scan()