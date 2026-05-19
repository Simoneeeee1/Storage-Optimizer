import os
import sqlite3
import logging
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from init_db import get_connection

DB_NAME = "system_transparency.db"
DRY_RUN = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("scanner.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SCAN_TARGETS = [
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Desktop"),
]

FILE_THRESHOLD_DAYS = 120
APP_THRESHOLD_DAYS  = 180
MIN_SIZE_GB         = 0.1
MIN_SIZE_BYTES      = int(MIN_SIZE_GB * 1024 ** 3)


# Helpers 

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


def get_last_used(path: str) -> Optional[datetime]:
    """
    Restituisce None in caso di OSError invece di datetime.now(),
    cosi il chiamante puo gestire esplicitamente i file inaccessibili
    senza bypassare silenziosamente la soglia di inattivita.
    """
    try:
        return datetime.fromtimestamp(os.path.getatime(path))
    except OSError:
        return None


def log_dry_run_action(conn: sqlite3.Connection, item_name: str, action: str,
                       reason: str, size_gb: float, real_path: str):
    conn.execute("""
        INSERT INTO audit_logs (item_name, action, reason, size_gb, real_path, dry_run)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (item_name, action, reason, round(size_gb, 2), real_path))
    logger.info(
        f"[DRY RUN] {action} — '{item_name}' ({round(size_gb, 2)} GB) "
        f"| path: {real_path} | motivo: {reason}"
    )


# Hashing (MD5 per dedup, NON per sicurezza)

def get_partial_hash(filepath: str, block_size: int = 1024) -> Optional[str]:
    """Legge solo il primo blocco: veloce per pre-filtrare candidati diversi."""
    hasher = hashlib.md5()  # noqa: S324
    try:
        with open(filepath, "rb") as f:
            hasher.update(f.read(block_size))
        return hasher.hexdigest()
    except OSError:
        return None


def get_full_hash(filepath: str) -> Optional[str]:
    """Hash completo: usato solo se hash parziale e dimensione coincidono."""
    hasher = hashlib.md5()  # noqa: S324
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


# Rilevatore duplicati con delta scan 

def check_duplicates(conn: sqlite3.Connection):
    """
    FIX 1 — Chiave di raggruppamento: size_bytes (int) invece di size_gb (float).
    Due file identici al byte avevano dimensioni float leggermente diverse per
    errori di rappresentazione IEEE 754, finendo in bucket separati e non
    venendo mai confrontati tramite hash.

    FIX 2 — Delta scan: prima di hashare un file, controlliamo se esiste gia
    in duplicates con lo stesso real_path e lo stesso mtime (os.path.getmtime).
    Se il file non e cambiato dall'ultima scansione, skippiamo hashing e INSERT,
    riducendo drasticamente il lavoro su cartelle con migliaia di file stabili.
    """
    logger.info("Avvio analisi file duplicati...")

    # Carichiamo lo snapshot del DB: { real_path -> (file_hash, mtime) }
    # per il confronto delta senza query per-file nel loop interno.
    known: dict[str, tuple[str, float]] = {}
    for row in conn.execute("SELECT real_path, file_hash, mtime FROM duplicates WHERE status = 'ACTIVE'").fetchall():
        if row["mtime"] is not None:
            known[row["real_path"]] = (row["file_hash"], row["mtime"])

    # FIX 1: raggruppiamo per byte interi, non per float GB
    files_by_size: dict[int, list[str]] = defaultdict(list)

    for folder in SCAN_TARGETS:
        if not os.path.exists(folder):
            continue
        for root, _, files in os.walk(folder):
            for file in files:
                if file.startswith("."):
                    continue
                path = os.path.join(root, file)
                try:
                    size_bytes = os.path.getsize(path)
                    if size_bytes >= MIN_SIZE_BYTES:
                        files_by_size[size_bytes].append(path)
                except OSError:
                    continue

    potential_duplicates = {
        size: paths for size, paths in files_by_size.items() if len(paths) > 1
    }

    duplicati_inseriti  = 0
    duplicati_skippati  = 0  # delta: file gia noti e invariati

    for size_bytes, paths in potential_duplicates.items():
        size_gb = size_bytes / (1024 ** 3)

        files_by_partial_hash: dict[str, list[str]] = defaultdict(list)
        for path in paths:
            # FIX 2: delta scan — salta file che non sono cambiati dall'ultima run
            try:
                current_mtime = os.path.getmtime(path)
            except OSError:
                continue

            if path in known:
                _, saved_mtime = known[path]
                if saved_mtime is not None and abs(current_mtime - saved_mtime) < 0.001:
                    duplicati_skippati += 1
                    continue  # file invariato, hash gia in DB

            p_hash = get_partial_hash(path)
            if p_hash:
                files_by_partial_hash[p_hash].append(path)

        for p_hash, p_paths in files_by_partial_hash.items():
            if len(p_paths) <= 1:
                continue

            files_by_full_hash: dict[str, list[str]] = defaultdict(list)
            for path in p_paths:
                f_hash = get_full_hash(path)
                if f_hash:
                    files_by_full_hash[f_hash].append(path)

            for f_hash, f_paths in files_by_full_hash.items():
                if len(f_paths) <= 1:
                    continue

                for path in f_paths:
                    name = os.path.basename(path)

                    in_exception = conn.execute(
                        "SELECT 1 FROM exceptions WHERE real_path = ? OR name = ?",
                        (path, name)
                    ).fetchone()
                    if in_exception:
                        continue

                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        mtime = None

                    # INSERT OR REPLACE aggiorna anche mtime se il file era gia noto
                    conn.execute("""
                        INSERT INTO duplicates (file_hash, name, size_gb, real_path, mtime)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(real_path) DO UPDATE SET
                            file_hash = excluded.file_hash,
                            size_gb   = excluded.size_gb,
                            mtime     = excluded.mtime,
                            status    = 'ACTIVE'
                    """, (f_hash, name, round(size_gb, 4), path, mtime))
                    duplicati_inseriti += 1

    conn.commit()
    logger.info(
        f"Analisi duplicati conclusa. "
        f"Nuovi/aggiornati: {duplicati_inseriti}, skippati (delta): {duplicati_skippati}."
    )


# Scanner principale 

def scan():
    conn = get_connection()

    scanned = 0
    nuovi   = 0
    saltati = 0

    try:
        for folder in SCAN_TARGETS:
            if not os.path.exists(folder):
                logger.warning(f"Cartella non trovata, salto: {folder}")
                continue

            logger.info(f"Scansione: {folder}")

            for entry in os.scandir(folder):
                path = entry.path
                name = entry.name
                scanned += 1

                if name.startswith("."):
                    saltati += 1
                    continue

                name_clean = name.removesuffix(".app").strip()
                in_exception = conn.execute("""
                    SELECT 1 FROM exceptions WHERE real_path = ? OR name = ? OR name = ?
                """, (path, name, name_clean)).fetchone()
                if in_exception:
                    saltati += 1
                    continue

                size_gb   = get_size_gb(path)
                last_used = get_last_used(path)

                if last_used is None:
                    logger.warning(f"Impossibile leggere atime di '{path}', salto.")
                    saltati += 1
                    continue

                if size_gb < MIN_SIZE_GB:
                    saltati += 1
                    continue

                item_type = "APP"    if name.endswith(".app") else \
                            "FOLDER" if os.path.isdir(path)   else "FILE"

                threshold     = APP_THRESHOLD_DAYS if item_type == "APP" else FILE_THRESHOLD_DAYS
                days_inactive = (datetime.now() - last_used).days

                existing = conn.execute(
                    "SELECT id, size_gb, status FROM items WHERE real_path = ?", (path,)
                ).fetchone()

                if existing:
                    # Salta item che l'utente ha gia gestito consapevolmente:
                    # KEPT  = l'utente ha scelto di mantenerlo → non risegnalare
                    # DELETED = gia eliminato → non risegnalare
                    if existing["status"] in ("KEPT", "DELETED"):
                        saltati += 1
                        continue

                    # Item ACTIVE gia noto: aggiorna dimensione e data ultimo accesso
                    conn.execute("""
                        UPDATE items SET size_gb = ?, last_used = ? WHERE real_path = ?
                    """, (round(size_gb, 2), last_used.strftime("%Y-%m-%d %H:%M:%S"), path))
                else:
                    if days_inactive < threshold:
                        saltati += 1
                        continue

                    conn.execute("""
                        INSERT INTO items (name, type, size_gb, last_used, real_path)
                        VALUES (?, ?, ?, ?, ?)
                    """, (name, item_type, round(size_gb, 2),
                          last_used.strftime("%Y-%m-%d %H:%M:%S"), path))
                    item_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                    expires_at = (datetime.now() + timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
                    # L'indice unico parziale su notifications(item_id) WHERE user_action IS NULL
                    # impedisce automaticamente notifiche duplicate per lo stesso item.
                    conn.execute("""
                        INSERT OR IGNORE INTO notifications (item_id, sent_at, expires_at)
                        VALUES (?, CURRENT_TIMESTAMP, ?)
                    """, (item_id, expires_at))

                    nuovi += 1

                    if DRY_RUN:
                        log_dry_run_action(
                            conn, item_name=name, action="SCAN_FOUND",
                            reason=f"Inattivo da {days_inactive} giorni (soglia: {threshold}gg)",
                            size_gb=size_gb, real_path=path
                        )

        conn.commit()
        check_duplicates(conn)

    except Exception as e:
        logger.error(f"Errore durante la scansione: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(
        f"Scansione completata — totale: {scanned}, "
        f"nuovi candidati: {nuovi}, saltati: {saltati}"
    )


if __name__ == "__main__":
    scan()