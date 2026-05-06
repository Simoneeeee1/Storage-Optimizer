# 🗄️ Storage Optimizer

Un sistema di gestione automatica dello storage locale che monitora file, cartelle e applicazioni inutilizzate, notifica l'utente prima di agire e registra ogni operazione in un log di audit trasparente.

---

## Funzionamento

Lo scanner analizza periodicamente le directory configurate alla ricerca di elementi che superano le soglie di inattività definite. Quando un candidato viene rilevato, viene creata una notifica con una finestra di risposta di 48 ore: l'utente può scegliere di mantenere o eliminare l'elemento. Se non risponde entro la scadenza, il sistema interviene automaticamente in base alla modalità attiva (`DRY_RUN` o reale).

Tutte le operazioni — sia manuali che automatiche — vengono tracciate nella tabella `audit_logs` del database SQLite.

---

## Policy di eliminazione

| Tipo | Soglia inattività | Nota |
|------|-------------------|------|
| File | 120 giorni | Dimensione minima: 0.1 GB |
| Cartella | 120 giorni | Dimensione minima: 0.1 GB |
| Applicazione (`.app`) | 180 giorni | Dimensione minima: 0.1 GB |

Gli elementi più piccoli di `MIN_SIZE_GB` e quelli nascosti (nome che inizia con `.`) vengono ignorati automaticamente.

---

## Struttura del progetto

```
.
├── init_db.py       # Inizializzazione del database SQLite
├── scanner.py       # Logica di scansione e rilevamento candidati
├── main.py          # Server FastAPI + scheduler + API REST
├── frontend/        # Interfaccia React (Storage Optimizer UI)
├── system_transparency.db   # Database SQLite (generato al primo avvio)
├── scanner.log      # Log dello scanner
└── main.log         # Log del server
```

---

## Schema del database

**`items`** — elementi rilevati dallo scanner  
**`notifications`** — notifiche generate per ogni candidato, con scadenza 48h  
**`audit_logs`** — registro di ogni azione eseguita (eliminazione, mantenimento, reinstall)  
**`exceptions`** — lista di elementi esclusi dalla policy (whitelist)

---

## Installazione

**Prerequisiti:** Python 3.10+, Node.js 18+

```bash
# 1. Clona il repository
git clone https://github.com/tuo-user/storage-optimizer
cd storage-optimizer

# 2. Installa le dipendenze Python
pip install fastapi uvicorn apscheduler send2trash

# 3. Inizializza il database
python init_db.py

# 4. Avvia il server
uvicorn main:app --reload

# 5. Avvia il frontend (in una seconda finestra)
cd frontend
npm install
npm run dev
```

L'interfaccia sarà disponibile su `http://localhost:5173`, il server API su `http://127.0.0.1:8000`.

---

## Configurazione

Apri `scanner.py` e modifica le costanti nella sezione **Configurazione**:

```python
# Directory da monitorare
SCAN_TARGETS = [
    os.path.expanduser("~/Downloads"),
    os.path.expanduser("~/Documents"),
    os.path.expanduser("~/Desktop"),
    # "/Applications",  # decommentare su macOS per scansionare le app
]

FILE_THRESHOLD_DAYS = 120   # giorni di inattività per file e cartelle
APP_THRESHOLD_DAYS  = 180   # giorni di inattività per applicazioni .app
MIN_SIZE_GB         = 0.1   # dimensione minima per essere considerato candidato
```

### Modalità DRY RUN

Per default il sistema opera in modalità sicura: nessun file viene effettivamente eliminato.

```python
DRY_RUN = True   # solo log e DB, nessuna operazione su disco
DRY_RUN = False  # eliminazione reale (spostamento nel cestino di sistema)
```

> ⚠️ Imposta `DRY_RUN = False` solo dopo aver verificato il comportamento dello scanner nella tua configurazione.

---

## API REST

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| `GET` | `/api/notifications` | Notifiche attive in attesa di risposta |
| `POST` | `/api/delete/{id}` | Elimina l'elemento (o simula in DRY RUN) |
| `POST` | `/api/keep/{id}` | Mantieni l'elemento, chiudi la notifica |
| `GET` | `/api/audit` | Storico completo delle operazioni |
| `POST` | `/api/reinstall/{audit_id}` | Segna un elemento eliminato come da ripristinare |
| `GET` | `/api/exceptions` | Lista degli elementi in whitelist |
| `POST` | `/api/exceptions` | Aggiunge un'eccezione |
| `DELETE` | `/api/exceptions/{id}` | Rimuove un'eccezione |
| `GET` | `/api/status` | GB totali liberati e modalità corrente |
| `GET` | `/api/config` | Configurazione attiva (`dry_run`) |

---

## Gestione delle eccezioni

È possibile escludere elementi dalla policy in tre modi:

- **Per nome** — es. `Progetto importante` (corrisponde a file, cartelle o `.app` con quel nome)
- **Per nome senza estensione** — es. `Xcode` esclude anche `Xcode.app`
- **Per path assoluto** — match preciso, consigliato per evitare falsi positivi

Le eccezioni possono essere gestite dall'interfaccia grafica nella scheda **Eccezioni**, oppure via API.

---

## Scheduler automatico

Il server esegue due job in background:

- **Scansione** — ogni 6 ore, cerca nuovi candidati nelle directory configurate
- **Elaborazione scaduti** — ogni minuto, processa le notifiche scadute senza risposta

---

## Dipendenze principali

| Pacchetto | Utilizzo |
|-----------|----------|
| `fastapi` | Server API REST |
| `uvicorn` | ASGI server |
| `apscheduler` | Job scheduler in background |
| `send2trash` | Spostamento nel cestino (cross-platform) |
| `sqlite3` | Database locale (incluso in Python) |
| `React` + `lucide-react` | Interfaccia utente |
