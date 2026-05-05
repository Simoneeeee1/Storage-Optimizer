import React, { useState, useEffect } from 'react';
import { ShieldCheck, Clock, Database, History, RotateCcw, Search, Plus, Trash2 } from 'lucide-react';

const API = "http://127.0.0.1:8000/api";

const App = () => {
  const [activeTab, setActiveTab] = useState('notifications');
  const [notifications, setNotifications] = useState([]);
  const [audit, setAudit] = useState([]);
  const [exceptions, setExceptions] = useState([]);
  const [totalSaved, setTotalSaved] = useState(0);

  const [searchTerm, setSearchTerm] = useState("");
  const [newExceptionName, setNewExceptionName] = useState("");
  const [newExceptionType, setNewExceptionType] = useState("FILE");

  const fetchData = async () => {
    try {
      const [notifyRes, auditRes, statusRes, exceptRes] = await Promise.all([
        fetch(`${API}/notifications`),
        fetch(`${API}/audit`),
        fetch(`${API}/status`),
        fetch(`${API}/exceptions`)
      ]);
      setNotifications(await notifyRes.json());
      setAudit(await auditRes.json());
      setExceptions(await exceptRes.json());
      const statusData = await statusRes.json();
      setTotalSaved(statusData.total_saved || 0);
    } catch (err) {
      console.error("Errore nel recupero dati:", err);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  const handleKeep = async (id) => {
    await fetch(`${API}/keep/${id}`, { method: 'POST' });
    fetchData();
  };

  const handleDelete = async (id) => {
    await fetch(`${API}/delete/${id}`, { method: 'POST' });
    fetchData();
  };

  const handleReinstall = async (auditId) => {
    await fetch(`${API}/reinstall/${auditId}`, { method: 'POST' });
    fetchData();
  };

  const handleAddException = async (e) => {
    e.preventDefault();
    if (!newExceptionName) return;
    await fetch(`${API}/exceptions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: newExceptionName, type: newExceptionType })
    });
    setNewExceptionName("");
    fetchData();
  };

  const handleRemoveException = async (name) => {
    await fetch(`${API}/exceptions/${name}`, { method: 'DELETE' });
    fetchData();
  };

  const filteredExceptions = exceptions.filter(ex => 
    ex.name.toLowerCase().includes(searchTerm.toLowerCase())
  );

  return (
    <div className="min-h-screen bg-slate-950 py-10 text-slate-200 font-sans">
      <div className="max-w-5xl mx-auto bg-slate-900/50 rounded-3xl border border-slate-800 shadow-2xl overflow-hidden">
        
        {/* HEADER */}
        <div className="p-8 border-b border-slate-800 flex justify-between items-center bg-slate-900/80">
          <div>
            <h1 className="text-2xl font-black text-white tracking-tighter uppercase">Storage Optimizer</h1>
            <p className="text-slate-500 text-sm font-medium">LE POLICY SU FILE E APP</p>
          </div>
          <div className="bg-blue-500/10 border border-blue-500/20 px-4 py-2 rounded-2xl">
            <span className="text-blue-400 font-bold">{totalSaved} GB</span>
            <span className="text-slate-400 text-xs tracking-widest ml-1">LIBERATI</span>
          </div>
        </div>

        {/* POLICY INFO BOXES */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 px-8 mt-6">
          <div className="p-4 bg-blue-500/5 border border-blue-500/10 rounded-2xl">
            <div className="flex items-center gap-2 mb-1">
              <div className="w-2 h-2 rounded-full bg-blue-500"></div>
              <h4 className="text-blue-400 font-bold text-xs uppercase tracking-wider">Regola File</h4>
            </div>
            <p className="text-slate-400 text-xs leading-relaxed">Eliminazione file non aperti da 120 giorni. Avviso 48 ore prima.</p>
          </div>
          <div className="p-4 bg-emerald-500/5 border border-emerald-500/10 rounded-2xl">
            <div className="flex items-center gap-2 mb-1">
              <div className="w-2 h-2 rounded-full bg-emerald-500"></div>
              <h4 className="text-emerald-400 font-bold text-xs uppercase tracking-wider">Regola Applicazioni</h4>
            </div>
            <p className="text-slate-400 text-xs leading-relaxed">Disinstallazione app non avviate da 180 giorni. Conferma manuale sopra i 10GB.</p>
          </div>
        </div>

        {/* NAVIGATION */}
        <div className="flex p-2 bg-slate-900/80 gap-2 m-6 rounded-2xl border border-slate-800">
          {[
            { id: 'notifications', label: 'NOTIFICHE', icon: Clock, count: notifications.length },
            { id: 'audit', label: 'LOG AUDIT', icon: History },
            { id: 'exceptions', label: 'ECCEZIONI', icon: ShieldCheck }
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 flex items-center justify-center gap-2 py-3 rounded-xl font-bold transition ${activeTab === tab.id ? 'bg-slate-800 text-white shadow-lg' : 'text-slate-500 hover:text-slate-300'}`}
            >
              <tab.icon size={18}/>
              {tab.label} {tab.count !== undefined && `(${tab.count})`}
            </button>
          ))}
        </div>

        {/* MAIN CONTENT */}
        <div className="p-6 min-h-[450px]">
          
          {/* TAB NOTIFICHE */}
          {activeTab === 'notifications' && (
            <div className="space-y-4">
              {notifications.length === 0 ? (
                <div className="text-center py-20 text-slate-600 italic font-medium">Nessuna violazione delle policy rilevata. Spazio ottimizzato.</div>
              ) : (
                notifications.map(n => (
                  <div key={n.id} className="p-5 bg-slate-900 border border-slate-800 rounded-2xl flex justify-between items-center hover:border-blue-500/50 transition group">
                    <div className="flex items-center gap-4">
                      <div className="p-3 bg-blue-500/10 text-blue-500 rounded-2xl group-hover:bg-blue-500 group-hover:text-white transition"><Database size={24}/></div>
                      <div>
                        <h3 className="font-bold text-lg text-white">{n.name}</h3>
                        <p className="text-xs text-slate-500 uppercase font-semibold">{n.type} • {n.size_gb} GB</p>
                      </div>
                    </div>
                    <div className="flex items-center gap-4">
                      <span className="text-amber-400 font-mono text-sm bg-amber-400/5 px-3 py-1 rounded-lg border border-amber-400/10">SCADE IN: {n.remaining_time}</span>
                      <button onClick={() => handleDelete(n.id)} className="bg-slate-800 text-slate-300 px-4 py-2 rounded-xl font-black text-xs uppercase hover:bg-red-600 hover:text-white transition-all">Elimina</button>
                      <button onClick={() => handleKeep(n.id)} className="bg-white text-black px-6 py-2 rounded-xl font-black text-xs uppercase hover:bg-blue-600 hover:text-white transition-all shadow-xl">Mantieni</button>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}

          {/* TAB LOG AUDIT */}
          {activeTab === 'audit' && (
            <div className="bg-slate-950 rounded-2xl border border-slate-800 overflow-hidden">
              <table className="w-full text-left text-sm">
                <thead className="bg-slate-900/50 text-slate-400 uppercase text-[10px] tracking-widest">
                  <tr>
                    <th className="p-4">Data</th>
                    <th className="p-4">Elemento</th>
                    <th className="p-4">Azione Effettuata</th>
                    <th className="p-4">Motivo Policy</th>
                    <th className="p-4 text-right">Azioni</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-900">
                  {audit.map(a => (
                    <tr key={a.id} className="hover:bg-slate-900/30 transition group">
                      <td className="p-4 text-slate-500 text-xs">{new Date(a.timestamp).toLocaleDateString()}</td>
                      <td className="p-4 font-bold text-white">{a.item_name}</td>
                      <td className="p-4">
                        <span className={`px-2 py-1 rounded text-[10px] font-black uppercase ${
                          a.action === 'DELETE' ? 'bg-red-500/10 text-red-500 border border-red-500/20' : 
                          a.action === 'REINSTALL' ? 'bg-blue-500/10 text-blue-500 border border-blue-500/20' : 
                          'bg-emerald-500/10 text-emerald-500 border border-emerald-500/20'
                        }`}>
                          {a.action}
                        </span>
                      </td>
                      <td className="p-4 text-slate-400 text-xs italic">{a.reason}</td>
                      <td className="p-4 text-right">
                        {a.action === 'DELETE' && (
                          <button
                            onClick={() => handleReinstall(a.id)}
                            className="p-2 text-slate-400 hover:text-white hover:bg-slate-800 rounded-lg transition"
                            title="Reinstalla"
                          >
                            <RotateCcw size={16}/>
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* TAB ECCEZIONI */}
          {activeTab === 'exceptions' && (
            <div className="space-y-6">
              <div className="flex flex-col md:flex-row gap-4">
                <div className="relative flex-1">
                  <Search className="absolute left-3 top-3 text-slate-500" size={18}/>
                  <input 
                    type="text" 
                    placeholder="Filtra eccezioni attive..." 
                    className="w-full bg-slate-950 border border-slate-800 rounded-xl py-2.5 pl-10 pr-4 focus:outline-none focus:border-blue-500 transition"
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                  />
                </div>
                <form onSubmit={handleAddException} className="flex gap-2 bg-slate-900 p-1.5 rounded-2xl border border-slate-800">
                  <input 
                    type="text" 
                    placeholder="Aggiungi nome..." 
                    className="bg-transparent border-none rounded-xl py-2 px-3 focus:outline-none text-sm w-40"
                    value={newExceptionName}
                    onChange={(e) => setNewExceptionName(e.target.value)}
                  />
                  <select 
                    className="bg-slate-800 border-none rounded-lg px-2 text-[10px] font-bold uppercase"
                    value={newExceptionType}
                    onChange={(e) => setNewExceptionType(e.target.value)}
                  >
                    <option value="FILE">FILE</option>
                    <option value="APP">APP</option>
                  </select>
                  <button type="submit" className="bg-blue-600 hover:bg-blue-500 text-white p-2 rounded-xl transition shadow-lg">
                    <Plus size={18}/>
                  </button>
                </form>
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
                      <tr><td colSpan="4" className="p-10 text-center text-slate-600 italic font-medium">Nessuna eccezione configurata.</td></tr>
                    ) : (
                      filteredExceptions.map(ex => (
                        <tr key={ex.id} className="hover:bg-slate-900/50 transition">
                          <td className="p-4 font-bold text-white flex items-center gap-2">
                            <ShieldCheck size={14} className="text-emerald-500"/>
                            {ex.name}
                          </td>
                          <td className="p-4 text-slate-500 text-[10px] font-bold uppercase tracking-tighter">{ex.type}</td>
                          <td className="p-4 text-slate-500 text-xs">{new Date(ex.added_at).toLocaleDateString()}</td>
                          <td className="p-4 text-right">
                            <button onClick={() => handleRemoveException(ex.name)} className="p-2 text-slate-600 hover:text-red-500 transition-all">
                              <Trash2 size={16}/>
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
    </div>
  );
};

export default App;