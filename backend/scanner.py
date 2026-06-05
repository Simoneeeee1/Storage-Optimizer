import os
import sqlite3
import logging
import hashlib
from collections import defaultdict
from datetime import datetime, timedelta

from backend.config import cfg
from backend.init_db import get_connection


# Configurazione 

DRY_RUN        = cfg.DRY_RUN
SCAN_TARGETS   = cfg.SCAN_TARGETS
FILE_THRESHOLD_DAYS = cfg.FILE_THRESHOLD_DAYS
APP_THRESHOLD_DAYS  = cfg.APP_THRESHOLD_DAYS
MIN_SIZE_GB    = cfg.MIN_SIZE_GB
MIN_SIZE_BYTES = int(cfg.MIN_SIZE_GB * 1024 ** 3)
RECURSIVE      = cfg.RECURSIVE
CLEANUP_BATCH_SIZE  = cfg.CLEANUP_BATCH_SIZE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(cfg.LOG_DIR / "scanner.log"),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


# Helpers 

def normalize(path: str) -> str:
    """Normalizza i separatori di path per il SO corrente (fix Windows path misti)."""
    return os.path.normpath(path)


def get_size_gb(path: str) -> float:
    """
    Calcola la dimensione in GB.
    Per i file usa getsize diretto; per le cartelle fa os.walk ricorsivo.
    Ignora silenziosamente i file inaccessibili.
    La dimensione delle cartelle è sempre calcolata ricorsivamente
    indipendentemente dal flag RECURSIVE (serve la dimensione reale totale).

    Nota: restituisce float GB — usato solo per soglie di filtraggio (MIN_SIZE_GB)
    e per la visualizzazione. Non usare per confrontare file identici: usa
    os.path.getsize() (int byte) come fa check_duplicates per evitare errori IEEE 754.
    """
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


def get_last_used(path: str) -> datetime | None:
    """
    Restituisce il datetime dell'ultimo utilizzo effettivo.
 
    Usa il massimo tra atime e mtime perché:
      - su Linux con mount noatime, atime non viene aggiornato
      - su macOS con SIP attivo, atime è spesso congelato
    In entrambi i casi mtime cattura almeno l'ultima modifica,
    evitando falsi positivi su file recenti proposti per la cancellazione.
 
    Ritorna None se il file è inaccessibile — il chiamante
    deve scartare esplicitamente questi elementi senza bypassare la soglia.
    """
    try:
        stat = os.stat(path)
        ts = max(stat.st_atime, stat.st_mtime)
        return datetime.fromtimestamp(ts)
    except OSError:
        return None


def is_in_exception(conn: sqlite3.Connection, path: str, name: str) -> bool:
    """
    Controlla se un elemento è in whitelist.
    Confronta su real_path esatto, nome completo e nome senza .app.
    Centralizzata qui per evitare query duplicate tra scanner e check_duplicates.
    """
    name_clean = name.removesuffix(".app").strip()
    return conn.execute("""
        SELECT 1 FROM exceptions
        WHERE real_path = ? OR name = ? OR name = ?
    """, (path, name, name_clean)).fetchone() is not None


# Audit log 

def log_scan_found(conn: sqlite3.Connection, item_name: str, reason: str,
                   size_gb: float, real_path: str) -> None:
    """
    Registra SCAN_FOUND in audit_logs sia in DRY RUN che in modalità reale.
    Lo scanner è l'unico responsabile di questa azione — main.py non la tocca.
    Il prefisso nel log indica la modalità attiva.
    """
    conn.execute("""
        INSERT INTO audit_logs (item_name, action, reason, size_gb, real_path, dry_run)
        VALUES (?, 'SCAN_FOUND', ?, ?, ?, ?)
    """, (item_name, reason, round(size_gb, 2), real_path, 1 if DRY_RUN else 0))

    prefix = "[DRY RUN]" if DRY_RUN else "[REALE]"
    logger.info(
        f"{prefix} SCAN_FOUND — '{item_name}' ({round(size_gb, 2)} GB) "
        f"| path: {real_path} | motivo: {reason}"
    )


# Cleanup file fantasma 

def cleanup_missing_files(conn: sqlite3.Connection) -> int:
    """
    Scansiona gli item ACTIVE in batch e marca DELETED quelli
    non più presenti su disco (eliminati esternamente al sistema).
    Processa CLEANUP_BATCH_SIZE elementi per volta per evitare
    di caricare tutto il DB in memoria su dataset grandi.
    Scrive un record SCAN_FOUND (azione CLEANUP_DELETED) in audit_logs
    per ogni file sparito, garantendo tracciabilità completa.
    Ritorna il numero di item rimossi.
    """
    rimossi = 0
    offset  = 0

    while True:
        batch = conn.execute("""
            SELECT id, name, size_gb, real_path
            FROM items
            WHERE status = 'ACTIVE'
            LIMIT ? OFFSET ?
        """, (CLEANUP_BATCH_SIZE, offset)).fetchall()

        if not batch:
            break

        for item in batch:
            path = normalize(item["real_path"])
            if not os.path.exists(path):
                conn.execute(
                    "UPDATE items SET status = 'DELETED' WHERE id = ?",
                    (item["id"],)
                )
                # Chiude le notifiche aperte per questo item
                conn.execute("""
                    UPDATE notifications
                    SET user_action = 'DELETED_EXTERNALLY'
                    WHERE item_id = ? AND user_action IS NULL
                """, (item["id"],))

                # Audit: traccia la rimozione esterna per garantire
                # che "quando è stato eliminato X?" abbia sempre una risposta.
                conn.execute("""
                    INSERT INTO audit_logs
                        (item_name, action, reason, size_gb, real_path, dry_run)
                    VALUES (?, 'DELETE', ?, ?, ?, ?)
                """, (
                    item["name"],
                    "File sparito dal disco (rimosso esternamente al sistema)",
                    round(item["size_gb"], 2),
                    path,
                    1 if DRY_RUN else 0,
                ))

                logger.info(f"[CLEANUP] File sparito dal disco: {path}")
                rimossi += 1

        offset += CLEANUP_BATCH_SIZE

    if rimossi:
        logger.info(f"[CLEANUP] File fantasma rimossi: {rimossi}")

    return rimossi


# Hashing (MD5 per dedup — NON per sicurezza) 

def get_partial_hash(filepath: str, block_size: int = 1024) -> str | None:
    """
    Legge solo il primo blocco del file.
    Veloce per pre-filtrare candidati con contenuto diverso
    prima di fare l'hash completo.
    """
    hasher = hashlib.md5()  # noqa: S324
    try:
        with open(filepath, "rb") as f:
            hasher.update(f.read(block_size))
        return hasher.hexdigest()
    except OSError:
        return None


def get_full_hash(filepath: str) -> str | None:
    """
    Hash completo del file a blocchi da 4KB.
    Chiamato solo quando hash parziale e dimensione coincidono,
    per confermare che due file siano effettivamente identici.
    """
    hasher = hashlib.md5()  # noqa: S324
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


# Rilevatore duplicati 

def check_duplicates(conn: sqlite3.Connection) -> None:
    """
    Trova file duplicati in SCAN_TARGETS usando tre livelli di confronto:
      1. Dimensione in byte (int, non float — evita errori IEEE 754)
      2. Hash parziale del primo blocco (pre-filtro veloce)
      3. Hash MD5 completo (conferma definitiva)

    Delta scan: salta i file già noti in DB con lo stesso mtime,
    riducendo drasticamente il lavoro su cartelle stabili.

    Rispetta la whitelist con la stessa logica dello scanner principale
    (real_path, nome completo, nome senza .app).
    """
    logger.info("Avvio analisi duplicati...")

    # Carica snapshot DB per delta scan: { path -> (hash, mtime) }
    known: dict[str, tuple[str, float]] = {}
    for row in conn.execute(
        "SELECT real_path, file_hash, mtime FROM duplicates WHERE status = 'ACTIVE'"
    ).fetchall():
        if row["mtime"] is not None:
            known[normalize(row["real_path"])] = (row["file_hash"], row["mtime"])

    # Raggruppa file per dimensione in byte (chiave int, non float)
    # — evita errori di rappresentazione IEEE 754 che manderebbero file
    #   identici in bucket separati impedendone il confronto.
    files_by_size: dict[int, list[str]] = defaultdict(list)

    for folder in SCAN_TARGETS:
        if not os.path.exists(folder):
            continue
        for root, dirs, files in os.walk(folder):
            if not RECURSIVE:
                dirs.clear()
            for file in files:
                if file.startswith("."):
                    continue
                path = normalize(os.path.join(root, file))
                try:
                    size_bytes = os.path.getsize(path)
                    if size_bytes >= MIN_SIZE_BYTES:
                        files_by_size[size_bytes].append(path)
                except OSError:
                    continue

    # Considera solo le dimensioni con almeno 2 file (potenziali duplicati)
    potential = {sz: ps for sz, ps in files_by_size.items() if len(ps) > 1}

    inseriti = 0
    skippati = 0

    for size_bytes, paths in potential.items():
        size_gb = size_bytes / (1024 ** 3)

        # Livello 2: hash parziale
        by_partial: dict[str, list[str]] = defaultdict(list)
        for path in paths:
            try:
                current_mtime = os.path.getmtime(path)
            except OSError:
                continue

            # Delta scan: salta se non modificato dall'ultima run
            if path in known:
                _, saved_mtime = known[path]
                if saved_mtime is not None and abs(current_mtime - saved_mtime) < 0.001:
                    skippati += 1
                    continue

            ph = get_partial_hash(path)
            if ph:
                by_partial[ph].append(path)

        # Livello 3: hash completo solo su collisioni di hash parziale
        for ph_paths in by_partial.values():
            if len(ph_paths) <= 1:
                continue

            by_full: dict[str, list[str]] = defaultdict(list)
            for path in ph_paths:
                fh = get_full_hash(path)
                if fh:
                    by_full[fh].append(path)

            for fh, fh_paths in by_full.items():
                if len(fh_paths) <= 1:
                    continue

                for path in fh_paths:
                    name = os.path.basename(path)

                    if is_in_exception(conn, path, name):
                        continue

                    try:
                        mtime = os.path.getmtime(path)
                    except OSError:
                        mtime = None

                    conn.execute("""
                        INSERT INTO duplicates (file_hash, name, size_gb, real_path, mtime)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(real_path) DO UPDATE SET
                            file_hash = excluded.file_hash,
                            size_gb   = excluded.size_gb,
                            mtime     = excluded.mtime,
                            status    = 'ACTIVE'
                    """, (fh, name, round(size_gb, 4), path, mtime))
                    inseriti += 1

    conn.commit()
    logger.info(
        f"Analisi duplicati — nuovi/aggiornati: {inseriti}, "
        f"skippati (delta): {skippati}."
    )


# Iteratore voci cartella 

def _iter_entries(folder: str) -> list[str]:
    """
    Restituisce la lista dei path da esaminare nella cartella data.

    - RECURSIVE=False: usa os.scandir sul primo livello — semplice e diretto,
      senza il doppio meccanismo os.walk + break che era nel codice originale.
    - RECURSIVE=True: usa os.walk per discendere nelle sottocartelle,
      includendo sia file che directory (es. bundle .app) al primo livello
      e solo file nei livelli più profondi.

    In entrambi i casi salta i file nascosti (nome che inizia con '.').
    """
    entries: list[str] = []

    if not RECURSIVE:
        try:
            for entry in os.scandir(folder):
                if not entry.name.startswith("."):
                    entries.append(normalize(entry.path))
        except OSError as e:
            logger.warning(f"Impossibile leggere cartella {folder}: {e}")
        return entries

    # Scansione ricorsiva: include directory al primo livello (bundle .app, ecc.)
    # e file in tutti i livelli. Le cartelle nascoste vengono saltate a ogni livello.
    for root, dirs, files in os.walk(folder):
        # Salta cartelle nascoste a ogni livello di profondità
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        # Al primo livello includi anche le directory (es. bundle .app)
        if root == folder:
            for d in dirs:
                entries.append(normalize(os.path.join(root, d)))

        for f in files:
            if not f.startswith("."):
                entries.append(normalize(os.path.join(root, f)))

    return entries


# Scanner principale 

def scan() -> None:
    conn    = get_connection()
    scanned = 0
    nuovi   = 0
    saltati = 0

    try:
        # 1. Pulizia file fantasma prima di tutto
        cleanup_missing_files(conn)
        conn.commit()

        # 2. Scansione cartelle target
        for folder in SCAN_TARGETS:
            if not os.path.exists(folder):
                logger.warning(f"Cartella non trovata, salto: {folder}")
                continue

            logger.info(f"Scansione: {folder}")

            for path in _iter_entries(folder):
                name = os.path.basename(path)
                scanned += 1

                # Eccezioni: controlla path, nome completo e nome senza .app
                if is_in_exception(conn, path, name):
                    saltati += 1
                    continue

                size_gb   = get_size_gb(path)
                last_used = get_last_used(path)

                if last_used is None:
                    logger.warning(f"atime non leggibile, salto: {path}")
                    saltati += 1
                    continue

                if size_gb < MIN_SIZE_GB:
                    saltati += 1
                    continue

                item_type = (
                    "APP"    if name.endswith(".app") else
                    "FOLDER" if os.path.isdir(path)   else
                    "FILE"
                )
                threshold     = APP_THRESHOLD_DAYS if item_type == "APP" else FILE_THRESHOLD_DAYS
                days_inactive = (datetime.now() - last_used).days

                existing = conn.execute(
                    "SELECT id, status FROM items WHERE real_path = ?", (path,)
                ).fetchone()

                if existing:
                    # Già gestito dall'utente → non risegnalare mai
                    if existing["status"] in ("KEPT", "DELETED"):
                        saltati += 1
                        continue
                    # Item ACTIVE già noto: aggiorna dimensione e data accesso
                    conn.execute("""
                        UPDATE items SET size_gb = ?, last_used = ?
                        WHERE real_path = ?
                    """, (round(size_gb, 2),
                          last_used.strftime("%Y-%m-%d %H:%M:%S"),
                          path))
                else:
                    # Nuovo elemento: controlla soglia inattività
                    if days_inactive < threshold:
                        saltati += 1
                        continue

                    # cursor.lastrowid è più affidabile di una SELECT separata
                    cur = conn.execute("""
                        INSERT INTO items (name, type, size_gb, last_used, real_path)
                        VALUES (?, ?, ?, ?, ?)
                    """, (name, item_type, round(size_gb, 2),
                          last_used.strftime("%Y-%m-%d %H:%M:%S"), path))

                    expires_at = (
                        datetime.now() + timedelta(hours=48)
                    ).strftime("%Y-%m-%d %H:%M:%S")

                    # INSERT OR IGNORE: l'indice unico parziale su
                    # notifications(item_id) WHERE user_action IS NULL
                    # garantisce idempotenza a livello DB.
                    conn.execute("""
                        INSERT OR IGNORE INTO notifications (item_id, sent_at, expires_at)
                        VALUES (?, CURRENT_TIMESTAMP, ?)
                    """, (cur.lastrowid, expires_at))

                    reason = f"Inattivo da {days_inactive}gg (soglia: {threshold}gg)"
                    log_scan_found(conn, name, reason, size_gb, path)
                    nuovi += 1

        conn.commit()

        # 3. Analisi duplicati
        check_duplicates(conn)

    except Exception as e:
        logger.error(f"Errore scansione: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

    logger.info(
        f"Scansione completata — totale: {scanned}, "
        f"nuovi: {nuovi}, saltati: {saltati}"
    )


if __name__ == "__main__":
    scan()