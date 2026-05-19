# Storage Optimizer

Un tool locale per il monitoraggio e la pulizia automatica dello spazio su disco. Scansiona periodicamente le cartelle utente, rileva file e app inattivi e file duplicati, notifica l'utente prima di agire e registra ogni operazione in un log di audit immutabile.

---

## Indice

- [Funzionalità](#funzionalità)
- [Architettura](#architettura)
- [Stack tecnologico](#stack-tecnologico)
- [Struttura del progetto](#struttura-del-progetto)
- [Schema del database](#schema-del-database)
- [Trigger SQL](#trigger-sql)
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

- **Mantieni** — l'elemento viene marcato `KEPT` e **non verrà mai più risegnalato** da scansioni future
- **Elimina** — l'elemento viene spostato nel cestino di sistema e marcato `DELETED`
- **Nessuna azione** — allo scadere delle 48 ore l'eliminazione avviene automaticamente

Un indice unico parziale su `notifications(item_id) WHERE user_action IS NULL` impedisce a livello di DB la creazione di notifiche duplicate per lo stesso elemento.

### Rilevamento duplicati
Ad ogni scansione viene eseguita un'analisi dei duplicati a tre livelli per minimizzare le operazioni disco:

1. **Raggruppamento per dimensione** — in byte interi (non float) per evitare errori di rappresentazione IEEE 754
2. **Hash parziale MD5** sul primo KB — scarta subito i file con contenuto diverso
3. **Hash completo MD5** — conferma i duplicati reali

**Delta scan**: i file già noti e non modificati (confronto tramite `mtime`) vengono saltati nelle run successive. Quando una copia viene eliminata e nel gruppo rimane un solo file, quest'ultimo viene automaticamente marcato `ORPHAN` tramite trigger SQL e sparisce dalla UI.

### Lista eccezioni
Qualsiasi file, app o cartella può essere aggiunto alla whitelist dal frontend. Gli elementi in eccezione vengono ignorati da scanner, notifiche e rilevamento duplicati.

### Log di audit
Ogni azione viene registrata nella tabella `audit_logs` con timestamp, motivo e flag `dry_run`. Il log è append-only: nessuna riga viene mai modificata o cancellata.

Le azioni registrate sono: `SCAN_FOUND`, `DELETE`, `KEEP`, `REINSTALL`, `DELETE_DUPLICATE`.

### Scan manuale
Un endpoint dedicato (`POST /api/scan/trigger`) permette di avviare una scansione immediata in background. Il frontend espone un pulsante "Scansiona ora" con spinner e barra di avanzamento animata. Un flag globale `_scan_running` impedisce run concorrenti.

---

## Architettura

```
┌─────────────────────────────────────────────────────────┐
│                       Frontend                          │
│                React + Tailwind CSS                     │
│   Notifiche │ Duplicati │ Audit │ Eccezioni             │
│   Badge: DUPLICATI · MANTENUTI · GB LIBERATI            │
└──────────────────┬──────────────────────────────────────┘
                   │ HTTP REST (polling 30s / 3s scan)
┌──────────────────▼──────────────────────────────────────┐
│                   Backend FastAPI                       │
│                                                         │
│  ┌─────────────┐   ┌────────────────────────────────┐  │
│  │  Scheduler  │   │         API Router             │  │
│  │  scan/6h    │   │  /api/notifications            │  │
│  │  notify/15m │   │  /api/duplicates               │  │
│  └──────┬──────┘   │  /api/audit                   │  │
│         │          │  /api/exceptions               │  │
│  ┌──────▼──────┐   │  /api/scan/trigger             │  │
│  │   Scanner   │   │  /api/status                   │  │
│  │  + Dedup    │   └────────────────────────────────┘  │
│  └──────┬──────┘                                       │
└─────────┼───────────────────────────────────────────────┘
          │ SQLite WAL + Trigger
┌─────────▼───────────────────────────────────────────────┐
│              system_transparency.db                     │
│  items │ notifications │ duplicates                     │
│  exceptions │ audit_logs                                │
│                                                         │
│  Trigger:  trg_notify_delete                            │
│            trg_notify_keep                              │
│            trg_duplicate_orphan                         │
└─────────────────────────────────────────────────────────┘
          │
    send2trash → Cestino di sistema
```

---

## Stack tecnologico

**Backend**
- Python 3.11+
- [FastAPI](https://fastapi.tiangolo.com/) — framework HTTP asincrono
- [APScheduler](https://apscheduler.readthedocs.io/) — scheduler in background
- [send2trash](https://github.com/arsenetar/send2trash) — eliminazione sicura tramite cestino di sistema
- SQLite con WAL mode, trigger e indici parziali

**Frontend**
- [React 18](https://react.dev/) con hooks
- [Tailwind CSS](https://tailwindcss.com/) — utility-first styling
- [Lucide React](https://lucide.dev/) — icone

---

## Struttura del progetto

```
storage-optimizer/
│
├── init_db.py        # Schema DB, trigger SQL, indici, factory get_connection()
├── scanner.py        # Logica di scansione e rilevamento duplicati con delta scan
├── main.py           # Server FastAPI, scheduler, tutti gli endpoint REST
│
├── frontend/
│   └── App.jsx       # Frontend React (single-file component)
│
├── .gitignore
├── README.md
│
# Generati a runtime — esclusi da git
├── system_transparency.db
├── scanner.log
└── main.log
```

---

## Schema del database

### `items`
Elementi rilevati dallo scanner come candidati alla rimozione.

| Colonna     | Tipo     | Note                                          |
|-------------|----------|-----------------------------------------------|
| `id`        | INTEGER  | PK autoincrement                              |
| `name`      | TEXT     | Nome del file/app/cartella                    |
| `type`      | TEXT     | `FILE`, `APP`, o `FOLDER`                     |
| `size_gb`   | REAL     | Dimensione in GB                              |
| `last_used` | DATETIME | Ultimo accesso (`atime`)                      |
| `real_path` | TEXT     | Path assoluto — UNIQUE                        |
| `status`    | TEXT     | `ACTIVE`, `DELETED`, o `KEPT`                 |

### `notifications`
Una notifica per ogni elemento in `items`. Gestisce la finestra di 48h.

| Colonna       | Tipo     | Note                                          |
|---------------|----------|-----------------------------------------------|
| `id`          | INTEGER  | PK autoincrement                              |
| `item_id`     | INTEGER  | FK → `items.id` ON DELETE CASCADE             |
| `sent_at`     | DATETIME | Timestamp creazione                           |
| `expires_at`  | DATETIME | `sent_at` + 48h                               |
| `user_action` | TEXT     | `NULL`, `KEEP`, o `DELETE`                    |

> Indice unico parziale su `(item_id) WHERE user_action IS NULL`: impedisce notifiche duplicate per lo stesso elemento ancora aperto.

### `duplicates`
File identificati come duplicati dall'analisi hash.

| Colonna     | Tipo     | Note                                              |
|-------------|----------|---------------------------------------------------|
| `id`        | INTEGER  | PK autoincrement                                  |
| `file_hash` | TEXT     | MD5 completo — chiave di raggruppamento           |
| `name`      | TEXT     | Nome del file                                     |
| `size_gb`   | REAL     | Dimensione in GB                                  |
| `real_path` | TEXT     | Path assoluto — UNIQUE                            |
| `status`    | TEXT     | `ACTIVE`, `DELETED`, o `ORPHAN`                   |
| `found_at`  | DATETIME | Timestamp prima rilevazione                       |
| `mtime`     | REAL     | `os.path.getmtime()` — usato per il delta scan    |

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

| Colonna     | Tipo     | Note                                                                        |
|-------------|----------|-----------------------------------------------------------------------------|
| `id`        | INTEGER  | PK autoincrement                                                            |
| `timestamp` | DATETIME | Timestamp automatico                                                        |
| `item_name` | TEXT     | Nome elemento coinvolto                                                     |
| `action`    | TEXT     | `SCAN_FOUND`, `DELETE`, `KEEP`, `REINSTALL`, `DELETE_DUPLICATE`            |
| `reason`    | TEXT     | Motivazione leggibile                                                       |
| `size_gb`   | REAL     | GB coinvolti (negativo per REINSTALL)                                       |
| `real_path` | TEXT     | Path assoluto                                                               |
| `dry_run`   | INTEGER  | `1` se eseguito in modalità simulazione                                     |

---

## Trigger SQL

I trigger gestiscono automaticamente le transizioni di stato, riducendo la logica duplicata nel codice Python.

### `trg_notify_delete`
Quando una notifica riceve `user_action = 'DELETE'`, imposta automaticamente `items.status = 'DELETED'`. Evita il doppio UPDATE manuale in Python, che rimane come fallback ridondante.

```sql
CREATE TRIGGER trg_notify_delete
AFTER UPDATE OF user_action ON notifications
WHEN NEW.user_action = 'DELETE'
BEGIN
    UPDATE items SET status = 'DELETED' WHERE id = NEW.item_id;
END;
```

### `trg_notify_keep`
Quando una notifica riceve `user_action = 'KEEP'`, imposta `items.status = 'KEPT'`. Lo scanner skippa gli item con status `KEPT` o `DELETED`, impedendo che un elemento confermato dall'utente venga risegnalato nelle scansioni successive.

```sql
CREATE TRIGGER trg_notify_keep
AFTER UPDATE OF user_action ON notifications
WHEN NEW.user_action = 'KEEP'
BEGIN
    UPDATE items SET status = 'KEPT' WHERE id = NEW.item_id;
END;
```

### `trg_duplicate_orphan`
Quando un duplicato viene eliminato, controlla se nel gruppo rimane almeno un'altra copia `ACTIVE`. Se il gruppo scende a un solo file, quest'ultimo non è più un duplicato e viene marcato `ORPHAN`, sparendo automaticamente dalla UI.

```sql
CREATE TRIGGER trg_duplicate_orphan
AFTER UPDATE OF status ON duplicates
WHEN NEW.status = 'DELETED'
BEGIN
    UPDATE duplicates
    SET status = 'ORPHAN'
    WHERE file_hash = NEW.file_hash
      AND status = 'ACTIVE'
      AND (SELECT COUNT(*) FROM duplicates
           WHERE file_hash = NEW.file_hash AND status = 'ACTIVE') < 2;
END;
```

---

## API REST

Il server gira su `http://127.0.0.1:8000`. Tutti gli endpoint restituiscono JSON.

### Scansione
| Metodo | Endpoint             | Descrizione                                    |
|--------|----------------------|------------------------------------------------|
| `POST` | `/api/scan/trigger`  | Avvia una scansione immediata in background    |
| `GET`  | `/api/scan/status`   | Restituisce `{ "running": bool }`              |

### Notifiche
| Metodo | Endpoint                        | Descrizione                        |
|--------|---------------------------------|------------------------------------|
| `GET`  | `/api/notifications`            | Lista notifiche attive non scadute |
| `POST` | `/api/delete/{notification_id}` | Elimina elemento nel cestino       |
| `POST` | `/api/keep/{notification_id}`   | Mantieni elemento (→ status KEPT)  |

### Duplicati
| Metodo | Endpoint                      | Descrizione                           |
|--------|-------------------------------|---------------------------------------|
| `GET`  | `/api/duplicates`             | Duplicati raggruppati per hash MD5    |
| `POST` | `/api/duplicates/delete/{id}` | Elimina una copia duplicata specifica |

### Audit e stato
| Metodo | Endpoint              | Descrizione                                          |
|--------|-----------------------|------------------------------------------------------|
| `GET`  | `/api/audit`          | Tutti i log in ordine cronologico inverso            |
| `GET`  | `/api/status`         | GB liberati, elementi mantenuti, stato scan in corso |
| `POST` | `/api/reinstall/{id}` | Registra un reinstall nel log audit                  |

### Eccezioni
| Metodo   | Endpoint               | Descrizione                  |
|----------|------------------------|------------------------------|
| `GET`    | `/api/exceptions`      | Lista eccezioni attive       |
| `POST`   | `/api/exceptions`      | Aggiunge un'eccezione        |
| `DELETE` | `/api/exceptions/{id}` | Rimuove un'eccezione         |

### Configurazione
| Metodo | Endpoint      | Descrizione                        |
|--------|---------------|------------------------------------|
| `GET`  | `/api/config` | Restituisce `{ "dry_run": bool }`  |

---

## Frontend

Applicazione React single-page con quattro sezioni principali.

### Header globale
- **Badge DUPLICATI** (arancione) — visibile solo se ci sono gruppi di duplicati attivi
- **Badge MANTENUTI** (verde) — visibile solo se l'utente ha confermato elementi; indica quanti file sono stati esclusi consapevolmente dalle scansioni future
- **Badge GB LIBERATI** (blu) — totale spazio recuperato da tutte le eliminazioni
- **Pulsante "Scansiona ora"** — avvia una scansione immediata con spinner e barra di avanzamento animata sotto l'header durante l'esecuzione
- **Toast notifications** (angolo in basso a destra) — feedback visivo per ogni azione con colori distinti per successo, errore e info

### Notifiche
Mostra gli elementi che hanno superato la soglia di inattività. Per ogni elemento: nome, tipo, dimensione e countdown al momento dell'eliminazione automatica. L'utente può eliminare manualmente o mantenere l'elemento (che non verrà più risegnalato).

### Duplicati
File duplicati raggruppati per hash MD5. Ogni gruppo è una card collassabile con il totale di GB recuperabili. Il primo file è marcato come "Originale", le copie hanno un badge numerato. Le copie possono essere eliminate con aggiornamento ottimistico dell'interfaccia — quando un gruppo scende a un file, sparisce automaticamente grazie al trigger `trg_duplicate_orphan`.

### Log Audit
Tabella completa di tutte le operazioni con barra di ricerca full-text su nome file, tipo azione e motivo. Mostra il contatore dei risultati filtrati. Per le azioni `DELETE` è disponibile il pulsante di reinstall.

### Eccezioni
Gestione della whitelist. Il campo di input occupa tutta la larghezza; tipo (`FILE`/`APP`) e pulsante di aggiunta sono allineati a destra. Supporta ricerca per filtrare le eccezioni esistenti.

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
- Node.js 18+

### Backend

```bash
# Clona il repository
git clone <repo-url>
cd storage-optimizer

# Installa le dipendenze Python
pip install fastapi uvicorn apscheduler send2trash

# Inizializza il database (crea tabelle, indici e trigger)
python init_db.py

# Avvia il server
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Il frontend si aspetta il backend su `http://127.0.0.1:8000`. Se usi una porta diversa, aggiorna la costante `API` in cima ad `App.jsx`.

---

## Modalità DRY RUN

Per default `DRY_RUN = True` in `scanner.py`. In questa modalità:

- **Nessun file viene mai spostato nel cestino** — le chiamate a `send2trash` vengono saltate
- Tutte le operazioni vengono registrate in `audit_logs` con `dry_run = 1`
- I log mostrano il prefisso `[DRY RUN]` per ogni azione simulata
- Il frontend mostra le stesse notifiche e duplicati che verrebbero gestiti in modalità reale

Per attivare la modalità reale, impostare `DRY_RUN = False` in `scanner.py` e riavviare il server.

> ⚠️ Prima di disabilitare DRY RUN, verificare che la lista eccezioni sia completa e che il comportamento del sistema sia quello atteso osservando i log in modalità simulazione.

---

## Decisioni tecniche rilevanti

**SQLite WAL mode** — la modalità Write-Ahead Logging permette letture concorrenti durante le scritture, fondamentale perché FastAPI gestisce richieste su thread multipli mentre lo scheduler scrive in background.

**Trigger SQL invece di doppio UPDATE Python** — le transizioni di stato (`ACTIVE → DELETED`, `ACTIVE → KEPT`, `ACTIVE → ORPHAN`) avvengono tramite trigger SQL che scattano automaticamente all'aggiornamento della notifica. Il codice Python mantiene gli UPDATE espliciti come fallback ridondante (belt-and-suspenders).

**Status `KEPT` per gli elementi mantenuti** — senza questo status, un file che l'utente ha scelto di mantenere veniva risegnalato ad ogni scansione perché risultava ancora inattivo. Con `status = 'KEPT'` lo scanner lo skippa esplicitamente, rispettando la scelta dell'utente in modo permanente.

**Status `ORPHAN` per i duplicati rimasti soli** — quando tutte le copie di un gruppo tranne una vengono eliminate, l'ultimo file non è più tecnicamente un duplicato. Il trigger `trg_duplicate_orphan` lo marca `ORPHAN` automaticamente, evitando che rimanga visibile nella UI come duplicato di sé stesso.

**Indice unico parziale su notifiche** — `CREATE UNIQUE INDEX ON notifications(item_id) WHERE user_action IS NULL` impedisce a livello di DB la creazione di notifiche duplicate per lo stesso elemento. Uno stesso file non può comparire due volte nella lista notifiche.

**Dimensione in byte interi per il raggruppamento duplicati** — usare `float` causava errori di rappresentazione IEEE 754: due file identici al byte potevano avere valori `size_gb` leggermente diversi dopo la divisione per `1024**3`, finendo in bucket separati e non venendo mai comparati tramite hash.

**Delta scan su `mtime`** — ad ogni run di `check_duplicates` viene caricato uno snapshot `{real_path: mtime}` dei duplicati già noti. I file il cui `mtime` non è cambiato vengono saltati completamente, evitando hashing inutile su archivi stabili.

**`send2trash` invece di `os.remove`** — gli elementi eliminati vanno nel cestino di sistema, non vengono cancellati definitivamente. L'utente può recuperarli manualmente se necessario.

**Factory `get_connection()` centralizzata** — tutte le connessioni al DB passano dalla stessa funzione in `init_db.py`, che imposta `timeout`, WAL mode e `foreign_keys=ON` in modo consistente ovunque — endpoint FastAPI, scheduler job e scanner.