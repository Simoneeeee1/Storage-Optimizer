import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import {
  ShieldCheck, Clock, Database, History, RotateCcw,
  Search, Plus, Trash2, Copy, CheckCheck,
  ChevronDown, ChevronUp, AlertTriangle, RefreshCw, X
} from 'lucide-react';

const API = "http://127.0.0.1:8000/api";


// Utilities

const formatDate = (ts) => {
  // Fix #6: timestamp null/undefined → '—' invece di '1/1/1970'
  if (!ts) return '—';
  return new Date(ts).toLocaleDateString('it-IT');
};

const formatPath = (p) => p.length > 52 ? '…' + p.slice(-52) : p;

/**
 * Estrae il messaggio di errore da una risposta HTTP non-ok.
 * Prova a leggere { detail } dal JSON; fallback al testo HTTP.
 */
async function extractError(res) {
  try {
    const data = await res.json();
    return data.detail || `Errore ${res.status}`;
  } catch {
    return `Errore ${res.status}`;
  }
}

/**
 * Formatta il tempo rimanente ricevuto dal backend.
 * Fix #1: isolato qui — se il backend cambia formato (es. secondi interi)
 * basta aggiornare questa funzione senza toccare il resto del componente.
 * Attualmente il backend restituisce una stringa tipo "3h".
 */
function formatRemainingTime(value) {
  if (!value && value !== 0) return '—';
  return String(value); // es. "3h" già formattato dal backend
}


// Toast

const Toast = ({ toasts }) => (
  <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 pointer-events-none">
    {toasts.map(t => (
      <div
        key={t.id}
        className={`flex items-center gap-3 px-4 py-3 rounded-2xl shadow-2xl border text-sm font-semibold
          transition-all duration-300 animate-slide-in
          ${t.type === 'success'
            ? 'bg-emerald-950 border-emerald-700 text-emerald-300'
            : t.type === 'error'
            ? 'bg-red-950 border-red-700 text-red-300'
            : 'bg-slate-800 border-slate-600 text-slate-200'
          }`}
      >
        <span>{t.type === 'success' ? '✓' : t.type === 'error' ? '✕' : 'ℹ'}</span>
        {t.message}
      </div>
    ))}
  </div>
);

const useToast = () => {
  const [toasts, setToasts] = useState([]);
  const counter = useRef(0);

  const addToast = useCallback((message, type = 'success', duration = 3500) => {
    const id = ++counter.current;
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), duration);
  }, []);

  return { toasts, addToast };
};


// DuplicateGroup

const DuplicateGroup = ({ files, onDelete, deletingId }) => {
  const [expanded, setExpanded] = useState(true);

  const totalSize  = files.reduce((acc, f) => acc + f.size_gb, 0);
  const wastedSize = totalSize - files[0].size_gb;

  return (
    <div className="border border-slate-800 rounded-2xl overflow-hidden bg-slate-900/40 hover:border-slate-700 transition-colors">

      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center justify-between p-5 text-left group"
      >
        <div className="flex items-center gap-4 min-w-0">
          <div className="shrink-0 w-10 h-10 rounded-xl bg-orange-500/10 border border-orange-500/20 flex items-center justify-center">
            <Copy size={18} className="text-orange-400" />
          </div>
          <div className="min-w-0">
            <p className="font-bold text-white truncate">{files[0].name}</p>
            <p className="text-[11px] text-slate-500 font-mono mt-0.5 truncate">
              {files.length} copie &nbsp;·&nbsp; {totalSize.toFixed(2)} GB totali
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4 shrink-0 ml-4">
          <span className="hidden sm:block text-orange-400 text-xs font-black bg-orange-500/8 border border-orange-500/15 px-3 py-1 rounded-lg">
            +{wastedSize.toFixed(2)} GB sprecati
          </span>
          {expanded
            ? <ChevronUp  size={16} className="text-slate-500 group-hover:text-slate-300 transition-colors" />
            : <ChevronDown size={16} className="text-slate-500 group-hover:text-slate-300 transition-colors" />
          }
        </div>
      </button>

      {expanded && (
        <div className="border-t border-slate-800 divide-y divide-slate-800/60">
          {files.map((file, idx) => (
            <div
              key={file.id}
              className="flex items-center justify-between px-5 py-3.5 hover:bg-slate-800/30 transition-colors group/row"
            >
              <div className="flex items-center gap-3 min-w-0">
                {idx === 0 ? (
                  <span className="shrink-0 inline-flex items-center gap-1 text-[9px] font-black uppercase tracking-wider bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded">
                    <CheckCheck size={9} /> Originale
                  </span>
                ) : (
                  <span className="shrink-0 text-[9px] font-black uppercase tracking-wider bg-slate-800 text-slate-500 border border-slate-700 px-2 py-0.5 rounded">
                    Copia {idx}
                  </span>
                )}
                <span
                  className="text-slate-400 text-xs font-mono truncate"
                  title={file.real_path}
                >
                  {formatPath(file.real_path)}
                </span>
              </div>
              <div className="flex items-center gap-4 shrink-0 ml-3">
                <span className="text-slate-500 text-xs">{file.size_gb.toFixed(2)} GB</span>
                {idx !== 0 && (
                  <button
                    onClick={() => onDelete(file.id)}
                    disabled={deletingId === file.id}
                    className={`flex items-center gap-1.5 text-[11px] font-black uppercase px-3 py-1.5 rounded-lg transition-all
                      ${deletingId === file.id
                        ? 'bg-slate-800 text-slate-600 cursor-not-allowed'
                        : 'bg-slate-800 text-slate-400 hover:bg-red-600 hover:text-white border border-transparent'
                      }`}
                  >
                    <Trash2 size={12} />
                    {deletingId === file.id ? 'Eliminazione…' : 'Rimuovi copia'}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};


// DuplicatesTab

const DuplicatesTab = ({ duplicates, loading, onDelete, deletingId }) => {
  const groups = Object.entries(duplicates);

  const totalWasted = groups.reduce((acc, [, files]) => (
    acc + files.slice(1).reduce((s, f) => s + f.size_gb, 0)
  ), 0);

  if (loading) return (
    <div className="flex items-center justify-center py-24 text-slate-600">
      <div className="w-6 h-6 border-2 border-slate-700 border-t-blue-500 rounded-full animate-spin mr-3" />
      Analisi duplicati in corso…
    </div>
  );

  if (groups.length === 0) return (
    <div className="text-center py-20 text-slate-600 italic font-medium">
      Nessun file duplicato trovato. Archivio pulito.
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between p-4 bg-orange-500/5 border border-orange-500/15 rounded-2xl">
        <div className="flex items-center gap-3">
          <AlertTriangle size={16} className="text-orange-400" />
          <span className="text-slate-300 text-sm">
            <span className="font-black text-white">{groups.length}</span> gruppi di duplicati trovati
          </span>
        </div>
        <span className="text-orange-400 font-black text-sm">
          {totalWasted.toFixed(2)} GB recuperabili
        </span>
      </div>
      {groups.map(([hash, files]) => (
        <DuplicateGroup
          key={hash}
          files={files}
          onDelete={onDelete}
          deletingId={deletingId}
        />
      ))}
    </div>
  );
};


// App

const App = () => {
  const [activeTab, setActiveTab]                 = useState('notifications');
  const [notifications, setNotifications]         = useState([]);
  const [audit, setAudit]                         = useState([]);
  const [exceptions, setExceptions]               = useState([]);
  const [duplicates, setDuplicates]               = useState({});
  const [totalSaved, setTotalSaved]               = useState(0);
  const [keptCount, setKeptCount]                 = useState(0);
  const [loadingDuplicates, setLoadingDuplicates] = useState(false);
  const [deletingDupId, setDeletingDupId]         = useState(null);
  const [scanRunning, setScanRunning]             = useState(false);
  const [scanCooldown, setScanCooldown]           = useState(false);

  // Fix #8: stato di caricamento per i bottoni delle notifiche
  const [actioningNotifId, setActioningNotifId]   = useState(null);

  const [auditSearch, setAuditSearch]             = useState('');
  const [searchTerm, setSearchTerm]               = useState('');
  const [newExceptionName, setNewExceptionName]   = useState('');
  const [newExceptionType, setNewExceptionType]   = useState('FILE');
  // Fix #3: campo real_path opzionale per le eccezioni
  const [newExceptionPath, setNewExceptionPath]   = useState('');

  const { toasts, addToast } = useToast();


  // Fetch

  const fetchDuplicates = useCallback(async () => {
    setLoadingDuplicates(true);
    try {
      const res = await fetch(`${API}/duplicates`);
      if (!res.ok) throw new Error(await extractError(res));
      setDuplicates(await res.json());
    } catch (err) {
      console.error('Errore duplicati:', err);
    } finally {
      setLoadingDuplicates(false);
    }
  }, []);

  const fetchData = useCallback(async () => {
    try {
      const [notifyRes, auditRes, statusRes, exceptRes] = await Promise.all([
        fetch(`${API}/notifications`),
        fetch(`${API}/audit`),
        fetch(`${API}/status`),
        fetch(`${API}/exceptions`),
      ]);

      // Ogni endpoint viene gestito indipendentemente —
      // un 500 su /audit non azzera le notifiche
      if (notifyRes.ok) setNotifications(await notifyRes.json());
      if (auditRes.ok)  setAudit(await auditRes.json());
      if (exceptRes.ok) setExceptions(await exceptRes.json());

      if (statusRes.ok) {
        const s = await statusRes.json();
        setTotalSaved(s.total_saved  ?? 0);
        setScanRunning(s.scan_running ?? false);
        setKeptCount(s.kept_count    ?? 0);
      }
    } catch (err) {
      console.error('Errore nel recupero dati:', err);
    }
  }, []);

  useEffect(() => {
    fetchData();
    fetchDuplicates();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, [fetchData, fetchDuplicates]);

  useEffect(() => {
    if (activeTab === 'duplicates') fetchDuplicates();
  }, [activeTab, fetchDuplicates]);

  // Polling accelerato mentre uno scan è in corso: ogni 3s
  useEffect(() => {
    if (!scanRunning) return;
    const fastPoll = setInterval(fetchData, 3000);
    return () => clearInterval(fastPoll);
  }, [scanRunning, fetchData]);


  // Handlers

  // Fix #8: i bottoni "Mantieni" ed "Elimina" ora mostrano uno stato
  // di caricamento tramite actioningNotifId, impedendo doppi click.
  const handleKeep = async (id) => {
    if (actioningNotifId) return;
    setActioningNotifId(id);
    try {
      const res = await fetch(`${API}/keep/${id}`, { method: 'POST' });
      if (!res.ok) throw new Error(await extractError(res));
      addToast('Elemento mantenuto.');
      fetchData();
    } catch (err) {
      addToast(err.message, 'error');
    } finally {
      setActioningNotifId(null);
    }
  };

  const handleDelete = async (id) => {
    if (actioningNotifId) return;
    setActioningNotifId(id);
    try {
      const res = await fetch(`${API}/delete/${id}`, { method: 'POST' });
      if (!res.ok) throw new Error(await extractError(res));
      addToast('Elemento eliminato.');
      fetchData();
    } catch (err) {
      addToast(err.message, 'error');
    } finally {
      setActioningNotifId(null);
    }
  };

  const handleReinstall = async (auditId) => {
    try {
      const res = await fetch(`${API}/reinstall/${auditId}`, { method: 'POST' });
      if (!res.ok) throw new Error(await extractError(res));
      addToast('File ripristinato dal cestino.');
      fetchData();
    } catch (err) {
      // HTTP 501 = non supportato su questo SO; messaggio esplicito
      addToast(err.message, 'error', 5000);
    }
  };

  const handleAddException = async (e) => {
    e?.preventDefault();
    const name = newExceptionName.trim();
    const path = newExceptionPath.trim() || undefined;

    // Validazione client-side prima di toccare il backend
    if (!name) {
      addToast("Inserisci un nome prima di aggiungere un'eccezione.", 'info');
      return;
    }

    try {
      const res = await fetch(`${API}/exceptions`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        // Fix #3: invia real_path se compilato — abilita il match preciso
        // per path in is_in_exception() oltre al match per nome.
        body: JSON.stringify({ name, type: newExceptionType, real_path: path }),
      });
      if (!res.ok) throw new Error(await extractError(res));
      setNewExceptionName('');
      setNewExceptionPath('');
      addToast(`"${name}" aggiunto alle eccezioni.`);
      fetchData();
    } catch (err) {
      addToast(err.message, 'error');
    }
  };

  const handleRemoveException = async (id) => {
    try {
      const res = await fetch(`${API}/exceptions/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(await extractError(res));
      addToast('Eccezione rimossa.', 'info');
      fetchData();
    } catch (err) {
      addToast(err.message, 'error');
    }
  };

  const handleDeleteDuplicate = async (dupId) => {
    setDeletingDupId(dupId);
    try {
      const res = await fetch(`${API}/duplicates/delete/${dupId}`, { method: 'POST' });
      if (!res.ok) throw new Error(await extractError(res));

      // Aggiornamento ottimistico — solo se il backend ha confermato
      setDuplicates(prev => {
        const updated = {};
        for (const [hash, files] of Object.entries(prev)) {
          const remaining = files.filter(f => f.id !== dupId);
          if (remaining.length > 1) updated[hash] = remaining;
        }
        return updated;
      });

      addToast('Copia duplicata eliminata.');
      fetchData();
    } catch (err) {
      addToast(err.message, 'error');
    } finally {
      setDeletingDupId(null);
      // Fix #4: ricarica sempre i duplicati dal server nel finally,
      // così lo stato locale non diverge dal DB in caso di errore silenzioso
      // o di trigger trg_duplicate_orphan che marca ORPHAN l'ultimo file.
      fetchDuplicates();
    }
  };

  const handleTriggerScan = async () => {
    if (scanRunning || scanCooldown) return;
    setScanCooldown(true);
    try {
      const res  = await fetch(`${API}/scan/trigger`, { method: 'POST' });
      if (!res.ok) throw new Error(await extractError(res));
      const data = await res.json();
      if (data.status === 'already_running') {
        addToast('Uno scan è già in corso.', 'info');
      } else {
        setScanRunning(true);
        addToast('Scansione avviata. I risultati saranno disponibili a breve.');
      }
    } catch (err) {
      addToast(err.message, 'error');
    } finally {
      // Cooldown 5s per evitare spam
      setTimeout(() => setScanCooldown(false), 5000);
    }
  };


  // Dati derivati

  const filteredExceptions = exceptions.filter(ex =>
    ex.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  // Fix #5: memoizzato per evitare re-filter ad ogni render con log grandi
  const filteredAudit = useMemo(() =>
    audit.filter(a =>
      !auditSearch ||
      [a.item_name, a.action, a.reason].some(f =>
        f?.toLowerCase().includes(auditSearch.toLowerCase())
      )
    ),
    [audit, auditSearch]
  );

  const duplicateCount = Object.keys(duplicates).length;

  const tabs = [
    { id: 'notifications', label: 'NOTIFICHE', icon: Clock,      count: notifications.length },
    { id: 'duplicates',    label: 'DUPLICATI', icon: Copy,        count: duplicateCount },
    { id: 'audit',         label: 'LOG AUDIT', icon: History },
    { id: 'exceptions',    label: 'ECCEZIONI', icon: ShieldCheck },
  ];


  // Render

  return (
    <div className="min-h-screen bg-slate-950 py-10 text-slate-200 font-sans">
      <Toast toasts={toasts} />

      <div className="max-w-5xl mx-auto bg-slate-900/50 rounded-3xl border border-slate-800 shadow-2xl overflow-hidden">

        {/* HEADER */}
        <div className="p-8 border-b border-slate-800 flex justify-between items-center bg-slate-900/80">
          <div>
            <h1 className="text-2xl font-black text-white tracking-tighter uppercase">Storage Optimizer</h1>
            <p className="text-slate-500 text-sm font-medium">GESTIONE AUTOMATICA DEL DISCO</p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={handleTriggerScan}
              disabled={scanRunning || scanCooldown}
              title="Avvia una scansione immediata"
              className={`flex items-center gap-2 px-4 py-2 rounded-2xl border text-xs font-black uppercase transition-all
                ${scanRunning
                  ? 'bg-blue-500/10 border-blue-500/30 text-blue-400 cursor-not-allowed'
                  : scanCooldown
                  ? 'bg-slate-800 border-slate-700 text-slate-500 cursor-not-allowed'
                  : 'bg-slate-800 border-slate-700 text-slate-300 hover:bg-slate-700 hover:text-white hover:border-slate-600'
                }`}
            >
              <RefreshCw size={14} className={scanRunning ? 'animate-spin' : ''} />
              {scanRunning ? 'Scansione…' : 'Scansiona ora'}
            </button>

            {duplicateCount > 0 && (
              <div className="bg-orange-500/10 border border-orange-500/20 px-4 py-2 rounded-2xl">
                <span className="text-orange-400 font-bold">{duplicateCount}</span>
                <span className="text-slate-400 text-xs tracking-widest ml-1">DUPLICATI</span>
              </div>
            )}
            {keptCount > 0 && (
              <div
                className="bg-emerald-500/10 border border-emerald-500/20 px-4 py-2 rounded-2xl"
                // Fix #7: tooltip aggiornata per riflettere che "mantenuto"
                // aggiunge anche alla whitelist permanente (fix backend #2)
                title="Elementi che hai scelto di mantenere — aggiunti automaticamente alla whitelist"
              >
                <span className="text-emerald-400 font-bold">{keptCount}</span>
                <span className="text-slate-400 text-xs tracking-widest ml-1">MANTENUTI</span>
              </div>
            )}
            <div className="bg-blue-500/10 border border-blue-500/20 px-4 py-2 rounded-2xl">
              <span className="text-blue-400 font-bold">{totalSaved}</span>
              <span className="text-slate-400 text-xs tracking-widest ml-1">GB LIBERATI</span>
            </div>
          </div>
        </div>

        {/* Barra avanzamento scan */}
        {scanRunning && (
          <div className="h-0.5 bg-slate-800 overflow-hidden">
            <div className="h-full bg-gradient-to-r from-blue-600 via-blue-400 to-blue-600 animate-scan-bar" />
          </div>
        )}

        {/* POLICY INFO */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 px-8 mt-6">
          <div className="p-4 bg-blue-500/5 border border-blue-500/10 rounded-2xl">
            <div className="flex items-center gap-2 mb-1">
              <div className="w-2 h-2 rounded-full bg-blue-500" />
              <h4 className="text-blue-400 font-bold text-xs uppercase tracking-wider">Regola File</h4>
            </div>
            <p className="text-slate-400 text-xs leading-relaxed">Eliminazione file non aperti da 120 giorni. Avviso 48 ore prima.</p>
          </div>
          <div className="p-4 bg-emerald-500/5 border border-emerald-500/10 rounded-2xl">
            <div className="flex items-center gap-2 mb-1">
              <div className="w-2 h-2 rounded-full bg-emerald-500" />
              <h4 className="text-emerald-400 font-bold text-xs uppercase tracking-wider">Regola Applicazioni</h4>
            </div>
            <p className="text-slate-400 text-xs leading-relaxed">Disinstallazione app non avviate da 180 giorni. Conferma manuale sopra i 10 GB.</p>
          </div>
        </div>

        {/* NAVIGATION */}
        <div className="flex p-2 bg-slate-900/80 gap-1.5 m-6 rounded-2xl border border-slate-800">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 flex items-center justify-center gap-2 py-3 rounded-xl font-bold text-xs transition
                ${activeTab === tab.id
                  ? 'bg-slate-800 text-white shadow-lg'
                  : 'text-slate-500 hover:text-slate-300'
                }
                ${tab.id === 'duplicates' && duplicateCount > 0 && activeTab !== 'duplicates'
                  ? 'text-orange-400 hover:text-orange-300'
                  : ''
                }`}
            >
              <tab.icon size={16} />
              <span className="hidden sm:inline">{tab.label}</span>
              {tab.count !== undefined && (
                <span className={`text-[10px] px-1.5 py-0.5 rounded font-black
                  ${tab.id === 'duplicates' && tab.count > 0
                    ? 'bg-orange-500/20 text-orange-400'
                    : 'bg-slate-700 text-slate-400'
                  }`}>
                  {tab.count}
                </span>
              )}
            </button>
          ))}
        </div>

        {/* CONTENT */}
        <div className="p-6 min-h-[450px]">

          {/* TAB: NOTIFICHE */}
          {activeTab === 'notifications' && (
            <div className="space-y-4">
              {notifications.length === 0 ? (
                <div className="text-center py-20 text-slate-600 italic font-medium">
                  Nessuna violazione delle policy rilevata. Spazio ottimizzato.
                </div>
              ) : (
                notifications.map(n => (
                  <div
                    key={n.id}
                    className="p-5 bg-slate-900 border border-slate-800 rounded-2xl flex justify-between items-center hover:border-blue-500/50 transition group"
                  >
                    <div className="flex items-center gap-4">
                      <div className="p-3 bg-blue-500/10 text-blue-500 rounded-2xl group-hover:bg-blue-500 group-hover:text-white transition">
                        <Database size={24} />
                      </div>
                      <div>
                        <h3 className="font-bold text-lg text-white">{n.name}</h3>
                        <p className="text-xs text-slate-500 uppercase font-semibold">{n.type} · {n.size_gb} GB</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      {/* Fix #1: remaining_time passato a formatRemainingTime */}
                      <span className="text-amber-400 font-mono text-sm bg-amber-400/5 px-3 py-1 rounded-lg border border-amber-400/10">
                        SCADE IN: {formatRemainingTime(n.remaining_time)}
                      </span>
                      {/* Fix #8: disabled + testo cambia durante la chiamata */}
                      <button
                        onClick={() => handleDelete(n.id)}
                        disabled={actioningNotifId === n.id}
                        className={`px-4 py-2 rounded-xl font-black text-xs uppercase transition-all
                          ${actioningNotifId === n.id
                            ? 'bg-slate-700 text-slate-500 cursor-not-allowed'
                            : 'bg-slate-800 text-slate-300 hover:bg-red-600 hover:text-white'
                          }`}
                      >
                        {actioningNotifId === n.id ? '…' : 'Elimina'}
                      </button>
                      <button
                        onClick={() => handleKeep(n.id)}
                        disabled={actioningNotifId === n.id}
                        className={`px-6 py-2 rounded-xl font-black text-xs uppercase shadow-xl transition-all
                          ${actioningNotifId === n.id
                            ? 'bg-slate-700 text-slate-500 cursor-not-allowed'
                            : 'bg-white text-black hover:bg-blue-600 hover:text-white'
                          }`}
                      >
                        {actioningNotifId === n.id ? '…' : 'Mantieni'}
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* TAB: DUPLICATI */}
          {activeTab === 'duplicates' && (
            <DuplicatesTab
              duplicates={duplicates}
              loading={loadingDuplicates}
              onDelete={handleDeleteDuplicate}
              deletingId={deletingDupId}
            />
          )}

          {/* TAB: LOG AUDIT */}
          {activeTab === 'audit' && (
            <div className="space-y-4">

              {/* Barra di ricerca */}
              <div className="relative">
                <Search className="absolute left-3 top-3 text-slate-500" size={18} />
                <input
                  type="text"
                  placeholder="Cerca per nome, azione o motivo..."
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl py-2.5 pl-10 pr-10 focus:outline-none focus:border-blue-500 transition text-sm"
                  value={auditSearch}
                  onChange={(e) => setAuditSearch(e.target.value)}
                />
                {auditSearch && (
                  <button
                    onClick={() => setAuditSearch('')}
                    className="absolute right-3 top-3 text-slate-500 hover:text-slate-300 transition"
                    aria-label="Cancella ricerca"
                  >
                    <X size={16} />
                  </button>
                )}
              </div>

              {/* Contatore risultati */}
              {auditSearch && (
                <p className="text-xs text-slate-500 px-1">
                  {filteredAudit.length} risultati per &ldquo;{auditSearch}&rdquo;
                </p>
              )}

              <div className="bg-slate-950 rounded-2xl border border-slate-800 overflow-hidden">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-900/50 text-slate-400 uppercase text-[10px] tracking-widest">
                    <tr>
                      <th className="p-4">Data</th>
                      <th className="p-4">Elemento</th>
                      <th className="p-4">Azione</th>
                      <th className="p-4">Motivo</th>
                      <th className="p-4 text-right">Azioni</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-900">
                    {filteredAudit.length === 0 ? (
                      <tr>
                        <td colSpan="5" className="p-10 text-center text-slate-600 italic font-medium">
                          {auditSearch
                            ? `Nessun risultato per "${auditSearch}".`
                            : 'Nessuna operazione registrata.'
                          }
                        </td>
                      </tr>
                    ) : (
                      filteredAudit.map(a => (
                        <tr key={a.id} className="hover:bg-slate-900/30 transition">
                          {/* Fix #6: formatDate ora restituisce '—' per timestamp null */}
                          <td className="p-4 text-slate-500 text-xs">{formatDate(a.timestamp)}</td>
                          <td className="p-4 font-bold text-white">{a.item_name}</td>
                          <td className="p-4">
                            <span className={`px-2 py-1 rounded text-[10px] font-black uppercase ${
                              a.action === 'DELETE' || a.action === 'DELETE_DUPLICATE'
                                ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                                : a.action === 'REINSTALL'
                                ? 'bg-blue-500/10 text-blue-400 border border-blue-500/20'
                                : 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
                            }`}>
                              {a.action}
                            </span>
                          </td>
                          <td className="p-4 text-slate-400 text-xs italic">{a.reason}</td>
                          <td className="p-4 text-right">
                            {/*
                              Bottone Ripristina visibile solo se:
                              - azione è DELETE (non DELETE_DUPLICATE)
                              - il file è stato spostato nel cestino da noi (moved_to_trash = 1)
                              Altrimenti il backend risponderebbe con 400 o 501 comunque.
                            */}
                            {a.action === 'DELETE' && a.moved_to_trash === 1 && (
                              <button
                                onClick={() => handleReinstall(a.id)}
                                className="p-2 text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition"
                                title="Ripristina dal cestino"
                              >
                                <RotateCcw size={16} />
                              </button>
                            )}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* TAB: ECCEZIONI */}
          {activeTab === 'exceptions' && (
            <div className="space-y-6">
              <div className="flex flex-col gap-3">

                {/* Filtro ricerca */}
                <div className="relative">
                  <Search className="absolute left-3 top-3 text-slate-500" size={18} />
                  <input
                    type="text"
                    placeholder="Filtra eccezioni attive..."
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl py-2.5 pl-10 pr-4 focus:outline-none focus:border-blue-500 transition"
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                  />
                </div>

                {/* Input aggiunta — Fix #3: aggiunto campo path opzionale */}
                <div className="flex flex-col gap-2 bg-slate-900 p-1.5 rounded-2xl border border-slate-800">
                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder="Nome eccezione (obbligatorio)..."
                      className="flex-1 bg-transparent border-none rounded-xl py-2 px-3 focus:outline-none text-sm min-w-0"
                      value={newExceptionName}
                      onChange={(e) => setNewExceptionName(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleAddException(e)}
                    />
                    <select
                      className="shrink-0 bg-slate-800 border-none rounded-lg px-3 text-[10px] font-bold uppercase cursor-pointer"
                      value={newExceptionType}
                      onChange={(e) => setNewExceptionType(e.target.value)}
                    >
                      <option value="FILE">FILE</option>
                      <option value="APP">APP</option>
                      <option value="FOLDER">FOLDER</option>
                    </select>
                    <button
                      onClick={handleAddException}
                      className="shrink-0 bg-blue-600 hover:bg-blue-500 text-white px-4 py-2 rounded-xl transition shadow-lg flex items-center gap-1.5 text-xs font-bold"
                    >
                      <Plus size={15} />
                      Aggiungi
                    </button>
                  </div>
                  {/* Path opzionale: abilita il match preciso per real_path in is_in_exception() */}
                  <input
                    type="text"
                    placeholder="Percorso completo (opzionale, es. /Users/me/Documents/file.pdf)..."
                    className="bg-transparent border-none rounded-xl py-1.5 px-3 focus:outline-none text-xs text-slate-500 min-w-0"
                    value={newExceptionPath}
                    onChange={(e) => setNewExceptionPath(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleAddException(e)}
                  />
                </div>
              </div>

              <div className="bg-slate-950 rounded-2xl border border-slate-800 overflow-hidden">
                <table className="w-full text-left text-sm">
                  <thead className="bg-slate-900/50 text-slate-400 uppercase text-[10px] tracking-widest">
                    <tr>
                      <th className="p-4">Risorsa Protetta</th>
                      <th className="p-4">Tipologia</th>
                      <th className="p-4">Data Protezione</th>
                      <th className="p-4 text-right">Azione</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-900">
                    {filteredExceptions.length === 0 ? (
                      <tr>
                        <td colSpan="4" className="p-10 text-center text-slate-600 italic font-medium">
                          Nessuna eccezione configurata.
                        </td>
                      </tr>
                    ) : (
                      filteredExceptions.map(ex => (
                        <tr key={ex.id} className="hover:bg-slate-900/50 transition">
                          <td className="p-4 font-bold text-white">
                            <div className="flex items-center gap-2">
                              <ShieldCheck size={14} className="text-emerald-500" />
                              <div>
                                <div>{ex.name}</div>
                                {/* Fix #3: mostra il path se presente */}
                                {ex.real_path && (
                                  <div className="text-[10px] text-slate-600 font-mono font-normal mt-0.5" title={ex.real_path}>
                                    {formatPath(ex.real_path)}
                                  </div>
                                )}
                              </div>
                            </div>
                          </td>
                          <td className="p-4 text-slate-500 text-[10px] font-bold uppercase tracking-tighter">{ex.type}</td>
                          <td className="p-4 text-slate-500 text-xs">{formatDate(ex.added_at)}</td>
                          <td className="p-4 text-right">
                            <button
                              onClick={() => handleRemoveException(ex.id)}
                              className="p-2 text-slate-600 hover:text-red-500 transition-all"
                              aria-label={`Rimuovi eccezione ${ex.name}`}
                            >
                              <Trash2 size={16} />
                            </button>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

        </div>
      </div>

      {/* Animazioni CSS */}
      <style>{`
        @keyframes slide-in {
          from { opacity: 0; transform: translateY(8px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        .animate-slide-in { animation: slide-in 0.2s ease-out; }

        @keyframes scan-bar {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(200%); }
        }
        .animate-scan-bar {
          width: 50%;
          animation: scan-bar 1.4s ease-in-out infinite;
        }
      `}</style>
    </div>
  );
};

export default App;