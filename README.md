# Storage Optimizer

Applicazione desktop per la gestione automatica dello spazio su disco.
Rileva file inattivi, app non utilizzate e duplicati, notifica l'utente e — dopo conferma — li sposta nel cestino di sistema con possibilità di ripristino.

---

## Funzionamento

Lo scanner analizza le cartelle configurate (Downloads, Documents, Desktop) e segnala:

- **File inattivi da più di 120 giorni**
- **App non avviate da più di 180 giorni**
- **File duplicati** (rilevati con hash MD5 a tre livelli)

Per ogni elemento trovato viene creata una **notifica con finestra di 48 ore**: l'utente può scegliere di eliminare o mantenere. Se non viene presa nessuna azione entro la scadenza, il file viene spostato automaticamente nel cestino. Tutto è tracciato in un log di audit immutabile.

---

## Struttura del progetto

```
storage-optimizer/
├── backend/
│   ├── config.py       # Unico punto di configurazione
│   ├── init_db.py      # Schema SQLite, indici, trigger, migrazioni
│   ├── scanner.py      # Scanner, rilevatore duplicati, cleanup
│   └── main.py         # API FastAPI + scheduler
├── frontend/
│   └── storage_optimizer.jsx   # UI React
├── data/
│   └── .gitkeep        # Cartella DB (esclusa da git)
├── log/
│   └── .gitkeep        # Cartella log (esclusa da git)
└── requirements.txt
```

---

## Requisiti

- **Python 3.10+**
- **Node.js** (per il frontend React)
- Su Windows: `winshell` e `pywin32` per il ripristino dal cestino

---

## Installazione

### Backend

```bash
# Dalla root del progetto
pip install -r requirements.txt

# Solo su Windows, per abilitare il ripristino dal cestino:
pip install winshell pywin32
```

### Frontend

```bash
cd frontend
npm install
```

---

## Avvio

### 1. Inizializza il database (prima esecuzione)

```bash
python -m backend.init_db
```

### 2. Avvia il backend

```bash
python -m uvicorn backend.main:app --reload
```

Il server parte su `http://127.0.0.1:8000`.

### 3. Avvia il frontend

```bash
cd frontend
npm run dev
```

---

## Configurazione

Tutto si configura in `backend/config.py`:

| Parametro | Default | Descrizione |
|---|---|---|
| `DRY_RUN` | `False` | Se `True`, nessun file viene toccato — solo simulazione |
| `SCAN_TARGETS` | Downloads, Documents, Desktop | Cartelle analizzate |
| `FILE_THRESHOLD_DAYS` | `120` | Giorni di inattività prima di segnalare un file |
| `APP_THRESHOLD_DAYS` | `180` | Giorni di inattività prima di segnalare un'app |
| `MIN_SIZE_GB` | `0.01` | Dimensione minima per essere segnalato |
| `RECURSIVE` | `False` | Scansione ricorsiva nelle sottocartelle |
| `CLEANUP_BATCH_SIZE` | `500` | Elementi processati per ciclo nel cleanup |

> **Prima di passare a `DRY_RUN = False`**, esegui qualche ciclo in modalità simulazione per verificare che lo scanner rilevi solo quello che ti aspetti.

---

## API

| Metodo | Endpoint | Descrizione |
|---|---|---|
| `GET` | `/api/notifications` | Notifiche attive con tempo rimanente |
| `POST` | `/api/delete/{id}` | Elimina elemento (sposta nel cestino) |
| `POST` | `/api/keep/{id}` | Mantieni elemento (aggiunge a whitelist) |
| `POST` | `/api/reinstall/{id}` | Ripristina dal cestino |
| `GET` | `/api/duplicates` | Duplicati raggruppati per hash |
| `POST` | `/api/duplicates/delete/{id}` | Elimina copia duplicata |
| `GET` | `/api/audit` | Log completo di tutte le operazioni |
| `GET` | `/api/status` | Stato generale (GB liberati, scan in corso) |
| `GET` | `/api/exceptions` | Whitelist elementi esclusi |
| `POST` | `/api/exceptions` | Aggiungi elemento alla whitelist |
| `DELETE` | `/api/exceptions/{id}` | Rimuovi dalla whitelist |
| `POST` | `/api/scan/trigger` | Avvia scansione manuale |
| `GET` | `/api/scan/status` | Stato della scansione in corso |

---

## Schema del database

Il database SQLite si trova in `data/system_transparency.db` e viene creato automaticamente al primo avvio con `python -m backend.init_db`.

### `items`
Contiene tutti gli elementi rilevati dallo scanner come candidati alla rimozione.

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | INTEGER | Chiave primaria |
| `name` | TEXT | Nome del file/app/cartella |
| `type` | TEXT | `FILE`, `APP` o `FOLDER` |
| `size_gb` | REAL | Dimensione in GB |
| `last_used` | DATETIME | Data ultimo accesso (atime) |
| `real_path` | TEXT | Percorso assoluto (univoco) |
| `status` | TEXT | `ACTIVE`, `DELETED` o `KEPT` |

### `notifications`
Una notifica per ogni elemento candidato. La finestra di azione è di 48 ore dalla creazione; alla scadenza senza risposta il file viene eliminato automaticamente dallo scheduler.

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | INTEGER | Chiave primaria |
| `item_id` | INTEGER | Riferimento a `items.id` |
| `sent_at` | DATETIME | Timestamp di creazione |
| `expires_at` | DATETIME | Scadenza della notifica |
| `user_action` | TEXT | `KEEP`, `DELETE` o `DELETED_EXTERNALLY` — NULL finché l'utente non agisce |

### `duplicates`
File duplicati rilevati dall'analisi MD5. I file vengono raggruppati per `file_hash`; quando in un gruppo rimane una sola copia viene marcata `ORPHAN` e sparisce dalla UI.

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | INTEGER | Chiave primaria |
| `file_hash` | TEXT | Hash MD5 del contenuto |
| `name` | TEXT | Nome del file |
| `size_gb` | REAL | Dimensione in GB |
| `real_path` | TEXT | Percorso assoluto (univoco) |
| `status` | TEXT | `ACTIVE`, `DELETED` o `ORPHAN` |
| `found_at` | DATETIME | Timestamp di rilevamento |
| `mtime` | REAL | Data modifica (usata per il delta scan) |

### `exceptions`
Whitelist permanente. Gli elementi presenti qui vengono saltati dallo scanner e dallo scheduler in ogni run successiva, anche dopo un reset del database.

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | INTEGER | Chiave primaria |
| `name` | TEXT | Nome dell'elemento (univoco) |
| `type` | TEXT | `FILE`, `APP` o `FOLDER` |
| `real_path` | TEXT | Percorso assoluto (opzionale, per match preciso) |
| `added_at` | DATETIME | Timestamp di aggiunta |

### `audit_logs`
Log immutabile di tutte le operazioni. Non viene mai modificato né cancellato — solo INSERT. Permette di rispondere sempre a "quando è stato eliminato X?" e "chi ha fatto cosa?".

| Colonna | Tipo | Descrizione |
|---|---|---|
| `id` | INTEGER | Chiave primaria |
| `timestamp` | DATETIME | Timestamp dell'operazione |
| `item_name` | TEXT | Nome dell'elemento |
| `action` | TEXT | `SCAN_FOUND`, `DELETE`, `KEEP`, `REINSTALL`, `DELETE_DUPLICATE` |
| `reason` | TEXT | Motivo dell'azione |
| `size_gb` | REAL | Dimensione in GB (negativa per REINSTALL) |
| `real_path` | TEXT | Percorso assoluto |
| `dry_run` | INTEGER | `1` = simulazione, `0` = operazione reale |
| `moved_to_trash` | INTEGER | `1` = spostato nel cestino dal sistema, abilita il ripristino |

### Trigger

I trigger gestiscono le transizioni di stato direttamente nel database, garantendo atomicità anche in caso di crash del processo Python tra due operazioni consecutive.

| Trigger | Evento | Effetto |
|---|---|---|
| `trg_notify_delete` | `notifications.user_action` → `DELETE` | Imposta `items.status = 'DELETED'` automaticamente |
| `trg_notify_keep` | `notifications.user_action` → `KEEP` | Imposta `items.status = 'KEPT'` automaticamente |
| `trg_duplicate_orphan` | `duplicates.status` → `DELETED` | Se nel gruppo rimane una sola copia, la marca `ORPHAN` — sparisce dalla UI |

Il codice Python aggiorna solo `notifications.user_action`; sono i trigger a propagare il cambio su `items`. Questo evita che un crash tra i due UPDATE lasci il database in uno stato inconsistente.

---

## Note tecniche

- Il database SQLite usa **WAL mode** per supportare letture concorrenti durante le scritture (FastAPI multi-thread + scheduler APScheduler).
- Le transizioni di stato (`ACTIVE → DELETED → KEPT`) sono gestite da **trigger SQL** per garantire atomicità anche in caso di crash Python.
- Il rilevamento duplicati usa **tre livelli**: dimensione in byte → hash parziale (primo blocco) → hash MD5 completo. Il **delta scan** salta i file non modificati dall'ultima run.
- Il ripristino dal cestino è supportato **solo su Windows** tramite `winshell`. Su macOS e Linux il file va recuperato manualmente dal cestino di sistema.
- La whitelist (`exceptions`) persiste nel DB: gli elementi marcati "mantieni" non vengono mai risegnalati, nemmeno dopo un reset.