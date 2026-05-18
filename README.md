# Storage Optimizer

Un tool locale per il monitoraggio e la pulizia automatica dello spazio su disco. Scansiona periodicamente le cartelle utente, rileva file e app inattivi e file duplicati, notifica l'utente prima di agire e registra ogni operazione in un log di audit immutabile.

---

## Indice

- [Funzionalità](#funzionalità)
- [Architettura](#architettura)
- [Stack tecnologico](#stack-tecnologico)
- [Struttura del progetto](#struttura-del-progetto)
- [Schema del database](#schema-del-database)
- [API REST](#api-rest)
- [Frontend](#frontend)
- [Configurazione](#configurazione)
- [Installazione e avvio](#installazione-e-avvio)
- [Modalità DRY RUN](#modalità-dry-run)
- [Decisioni tecniche rilevanti](#decisioni-tecniche-rilevanti)

---

## Funzionalità

### Scansione automatica
Lo scanner gira ogni **6 ore** tramite scheduler in background e analizza le cartelle configurate (`~/Downloads`, `~/Documents`, `~/Desktop`). Ogni file o app che supera le soglie di inattività viene registrato come candidato alla rimozione.

| Tipo      | Soglia inattività | Dimensione minima |
|-----------|-------------------|-------------------|
| FILE      | 120 giorni        | 100 MB            |
| APP       | 180 giorni        | 100 MB            |
| FOLDER    | 120 giorni        | 100 MB            |

### Sistema di notifiche
Quando un elemento supera la soglia, viene generata una notifica con una finestra di **48 ore** entro cui l'utente può scegliere:
- **Mantieni** — l'elemento viene marcato come confermato e rimosso dalla coda
- **Elimina** — l'elemento viene spostato nel cestino di sistema
- **Nessuna azione** — allo scadere delle 48 ore l'eliminazione avviene automaticamente

### Rilevamento duplicati
Ad ogni scansione viene eseguita un'analisi dei duplicati a tre livelli per minimizzare le operazioni disco:
1. **Raggruppamento per dimensione** (in byte interi, non float, per evitare errori IEEE 754)
2. **Hash parziale MD5** sul primo KB — scarta subito i file diversi
3. **Hash completo MD5** — conferma i duplicati reali

Il sistema implementa un **delta scan**: i file già noti e non modificati (confronto tramite `mtime`) vengono saltati nelle run successive, riducendo drasticamente il lavoro su cartelle con molti file stabili.

### Lista eccezioni
Qualsiasi file, app o cartella può essere aggiunto alla lista eccezioni dal frontend. Gli elementi in eccezione vengono ignorati da scanner, notifiche e rilevamento duplicati.

### Log di audit
Ogni azione (scansione, eliminazione, mantenimento, reinstall, rimozione duplicati) viene registrata nella tabella `audit_logs` con timestamp, motivo e flag `dry_run`. Il log è append-only: nessuna riga viene mai modificata o cancellata.

### Scan manuale
Un endpoint dedicato (`POST /api/scan/trigger`) permette di avviare una scansione immediata in background senza aspettare il ciclo automatico. Il frontend espone un pulsante "Scansiona ora" con feedback visivo (spinner + barra di avanzamento).

---

## Architettura

```
┌─────────────────────────────────────────────────────┐
│                     Frontend                        │
│              React + Tailwind CSS                   │
│   Notifiche │ Duplicati │ Audit │ Eccezioni         │
└──────────────────┬──────────────────────────────────┘
                   │ HTTP REST (polling 30s)
┌──────────────────▼──────────────────────────────────┐
│                   Backend FastAPI                   │
│                                                     │
│  ┌─────────────┐   ┌──────────────────────────┐    │
│  │  Scheduler  │   │       API Router         │    │
│  │  scan/6h    │   │  /api/notifications      │    │
│  │  notify/15m │   │  /api/duplicates         │    │
│  └──────┬──────┘   │  /api/audit              │    │
│         │          │  /api/exceptions         │    │
│  ┌──────▼──────┐   │  /api/scan/trigger       │    │
│  │   Scanner   │   │  /api/status             │    │
│  │  + Dedup    │   └──────────────────────────┘    │
│  └──────┬──────┘                                   │
└─────────┼───────────────────────────────────────────┘
          │ SQLite WAL
┌─────────▼───────────────────────────────────────────┐
│            system_transparency.db                   │
│  items │ notifications │ duplicates                 │
│  exceptions │ audit_logs                            │
└─────────────────────────────────────────────────────┘
          │
    send2trash → Cestino di sistema
```

---

## Stack tecnologico

**Backend**
- Python 3.11+
- [FastAPI](https://fastapi.tiangolo.com/) — framework HTTP asincrono
- [APScheduler](https://apscheduler.readthedocs.io/) — scheduler in background
- [send2trash](https://github.com/arsenetar/send2trash) — eliminazione sicura (cestino, non `rm`)
- SQLite con WAL mode — database embedded

**Frontend**
- [React 18](https://react.dev/) con hooks
- [Tailwind CSS](https://tailwindcss.com/) — utility-first styling
- [Lucide React](https://lucide.dev/) — icone

---

## Struttura del progetto

```
storage-optimizer/
│
├── init_db.py        # Inizializzazione schema DB, factory get_connection()
├── scanner.py        # Logica di scansione e rilevamento duplicati
├── main.py           # Server FastAPI, scheduler, tutti gli endpoint REST
│
├── storage_optimizer.jsx           # Frontend React (single-file component)
│
├── system_transparency.db   # Database SQLite (generato al primo avvio)
├── scanner.log              # Log delle scansioni
└── main.log                 # Log del server
```

---

## Schema del database

### `items`
Elementi rilevati dallo scanner come candidati alla rimozione.

| Colonna     | Tipo     | Note                              |
|-------------|----------|-----------------------------------|
| `id`        | INTEGER  | PK autoincrement                  |
| `name`      | TEXT     | Nome del file/app/cartella        |
| `type`      | TEXT     | `FILE`, `APP`, o `FOLDER`         |
| `size_gb`   | REAL     | Dimensione in GB                  |
| `last_used` | DATETIME | Ultimo accesso (`atime`)          |
| `real_path` | TEXT     | Path assoluto — UNIQUE            |
| `status`    | TEXT     | `ACTIVE` o `DELETED`              |

### `notifications`
Una notifica per ogni elemento in `items`. Gestisce la finestra di 48h.

| Colonna       | Tipo     | Note                                    |
|---------------|----------|-----------------------------------------|
| `id`          | INTEGER  | PK autoincrement                        |
| `item_id`     | INTEGER  | FK → `items.id`                         |
| `sent_at`     | DATETIME | Timestamp creazione                     |
| `expires_at`  | DATETIME | `sent_at` + 48h                         |
| `user_action` | TEXT     | `NULL`, `KEEP`, o `DELETE`              |

### `duplicates`
File identificati come duplicati dall'analisi hash.

| Colonna     | Tipo     | Note                                        |
|-------------|----------|---------------------------------------------|
| `id`        | INTEGER  | PK autoincrement                            |
| `file_hash` | TEXT     | MD5 completo — chiave di raggruppamento     |
| `name`      | TEXT     | Nome del file                               |
| `size_gb`   | REAL     | Dimensione in GB                            |
| `real_path` | TEXT     | Path assoluto — UNIQUE                      |
| `status`    | TEXT     | `ACTIVE` o `DELETED`                        |
| `found_at`  | DATETIME | Timestamp prima rilevazione                 |
| `mtime`     | REAL     | `os.path.getmtime()` — usato per delta scan |

### `exceptions`
Elementi esclusi permanentemente da ogni analisi.

| Colonna     | Tipo     | Note                      |
|-------------|----------|---------------------------|
| `id`        | INTEGER  | PK autoincrement          |
| `name`      | TEXT     | Nome — UNIQUE             |
| `type`      | TEXT     | `FILE` o `APP`            |
| `real_path` | TEXT     | Path opzionale            |
| `added_at`  | DATETIME | Timestamp aggiunta        |

### `audit_logs`
Log immutabile di tutte le operazioni. Solo INSERT, mai UPDATE o DELETE.

| Colonna     | Tipo     | Note                                              |
|-------------|----------|---------------------------------------------------|
| `id`        | INTEGER  | PK autoincrement                                  |
| `timestamp` | DATETIME | Timestamp automatico                              |
| `item_name` | TEXT     | Nome elemento coinvolto                           |
| `action`    | TEXT     | `SCAN_FOUND`, `DELETE`, `KEEP`, `REINSTALL`, `DELETE_DUPLICATE` |
| `reason`    | TEXT     | Motivazione leggibile                             |
| `size_gb`   | REAL     | GB coinvolti (negativo per REINSTALL)             |
| `real_path` | TEXT     | Path assoluto                                     |
| `dry_run`   | INTEGER  | `1` se eseguito in modalità simulazione           |

---

## API REST

Il server gira su `http://127.0.0.1:8000`. Tutti gli endpoint restituiscono JSON.

### Scansione
| Metodo | Endpoint            | Descrizione                                      |
|--------|---------------------|--------------------------------------------------|
| `POST` | `/api/scan/trigger` | Avvia una scansione immediata in background      |
| `GET`  | `/api/scan/status`  | Restituisce `{ "running": bool }`                |

### Notifiche
| Metodo | Endpoint                        | Descrizione                        |
|--------|---------------------------------|------------------------------------|
| `GET`  | `/api/notifications`            | Lista notifiche attive non scadute |
| `POST` | `/api/delete/{notification_id}` | Elimina elemento nel cestino       |
| `POST` | `/api/keep/{notification_id}`   | Mantieni elemento                  |

### Duplicati
| Metodo | Endpoint                          | Descrizione                               |
|--------|-----------------------------------|-------------------------------------------|
| `GET`  | `/api/duplicates`                 | Duplicati raggruppati per hash MD5        |
| `POST` | `/api/duplicates/delete/{id}`     | Elimina una copia duplicata specifica     |

### Audit e stato
| Metodo | Endpoint             | Descrizione                                   |
|--------|----------------------|-----------------------------------------------|
| `GET`  | `/api/audit`         | Tutti i log in ordine cronologico inverso     |
| `GET`  | `/api/status`        | GB liberati totali e stato scan in corso      |
| `POST` | `/api/reinstall/{id}`| Registra un reinstall nel log audit           |

### Eccezioni
| Metodo   | Endpoint                    | Descrizione                     |
|----------|-----------------------------|---------------------------------|
| `GET`    | `/api/exceptions`           | Lista eccezioni attive          |
| `POST`   | `/api/exceptions`           | Aggiunge un'eccezione           |
| `DELETE` | `/api/exceptions/{id}`      | Rimuove un'eccezione            |

### Configurazione
| Metodo | Endpoint      | Descrizione                        |
|--------|---------------|------------------------------------|
| `GET`  | `/api/config` | Restituisce `{ "dry_run": bool }`  |

---

## Frontend

Applicazione React single-page con quattro sezioni principali.

### Notifiche
Mostra gli elementi che hanno superato la soglia di inattività e stanno per essere eliminati. Per ogni elemento viene mostrato nome, tipo, dimensione e tempo rimanente prima dell'eliminazione automatica. L'utente può confermare l'eliminazione o mantenere l'elemento.

### Duplicati
Mostra i file duplicati raggruppati per contenuto (hash MD5). Ogni gruppo è una card collassabile che indica il file "originale" (primo rilevato) e le copie superflue. Per ogni gruppo viene mostrato il totale di GB recuperabili. Le copie possono essere eliminate singolarmente con aggiornamento ottimistico dell'interfaccia.

### Log Audit
Tabella completa di tutte le operazioni con filtro di ricerca full-text su nome, azione e motivo. Per le azioni `DELETE` è disponibile il pulsante di reinstall.

### Eccezioni
Gestione della whitelist. Il campo di input occupa tutta la larghezza disponibile; tipo e pulsante di aggiunta sono allineati a destra. Supporta ricerca per filtrare le eccezioni esistenti.

### Header globale
- Badge con numero duplicati attivi (arancione)
- Contatore GB liberati totali
- Pulsante "Scansiona ora" con spinner durante l'esecuzione e barra di avanzamento animata sotto l'header
- Toast notifications (angolo in basso a destra) per il feedback di ogni azione

---

## Configurazione

Le variabili principali si trovano in cima a `scanner.py`:

```python
# Cartelle analizzate
SCAN_TARGETS = [
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Desktop"),
]

# Soglie di inattività
FILE_THRESHOLD_DAYS = 120   # giorni
APP_THRESHOLD_DAYS  = 180   # giorni

# Dimensione minima per essere considerato
MIN_SIZE_GB = 0.1           # 100 MB

# Modalità simulazione (nessuna azione reale su disco)
DRY_RUN = True
```

---

## Installazione e avvio

### Prerequisiti
- Python 3.11+
- Node.js 18+ (per il frontend)

### Backend

```bash
# Clona il repository
git clone <repo-url>
cd storage-optimizer

# Installa le dipendenze Python
pip install fastapi uvicorn apscheduler send2trash

# Inizializza il database
python init_db.py

# Avvia il server
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend

```bash
# Nella cartella del progetto React
npm install
npm run dev
```

Il frontend si aspetta il backend su `http://127.0.0.1:8000`. Se usi una porta diversa, aggiorna la costante `API` in cima ad `storage_optimizer.jsx`.

---

## Modalità DRY RUN

Per default `DRY_RUN = True` in `scanner.py`. In questa modalità:

- **Nessun file viene mai spostato nel cestino** — le chiamate a `send2trash` vengono saltate
- Tutte le operazioni vengono comunque registrate in `audit_logs` con `dry_run = 1`
- I log mostrano il prefisso `[DRY RUN]` per ogni azione simulata
- Il frontend mostra le stesse notifiche e duplicati che verrebbero gestiti in modalità reale

Per attivare la modalità reale, impostare `DRY_RUN = False` in `scanner.py` e riavviare il server.

> ⚠️ Prima di disabilitare DRY RUN, verificare che la lista eccezioni sia completa e che il comportamento del sistema sia quello atteso osservando i log in modalità simulazione.

---

## Decisioni tecniche rilevanti

**SQLite WAL mode** — la modalità Write-Ahead Logging permette letture concorrenti durante le scritture, fondamentale perché FastAPI gestisce richieste su thread multipli mentre lo scheduler scrive in background.

**`send2trash` invece di `os.remove`** — gli elementi eliminati vanno nel cestino di sistema, non vengono cancellati definitivamente. L'utente può recuperarli manualmente se necessario.

**Hash parziale prima di quello completo** — leggere solo il primo KB di ogni file prima di fare l'hash completo riduce drasticamente le operazioni I/O su gruppi di file che hanno solo la stessa dimensione ma contenuto diverso.

**Dimensione in byte interi per il raggruppamento duplicati** — usare `float` per raggruppare file della stessa dimensione causava errori di rappresentazione IEEE 754: due file identici al byte potevano avere valori float leggermente diversi dopo la divisione per `1024**3`, finendo in bucket separati e non venendo mai comparati.

**Delta scan su `mtime`** — ad ogni run di `check_duplicates` viene caricato uno snapshot `{real_path: mtime}` dei duplicati già noti. I file il cui `mtime` non è cambiato vengono saltati completamente, evitando hashing inutile su archivi stabili con migliaia di file.

**Factory `get_connection()` centralizzata** — tutte le connessioni al DB passano dalla stessa funzione in `init_db.py`, che imposta `timeout`, WAL mode e `foreign_keys` in modo consistente. Evita che una singola connessione aperta senza timeout blocchi l'intera applicazione in caso di lock.