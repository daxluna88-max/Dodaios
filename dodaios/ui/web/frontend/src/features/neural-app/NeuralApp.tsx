import { useEffect, useMemo, useState } from "react";
import { BrainCircuit, LayoutDashboard, Mic, MicOff, Plus, Shield, Trash2, X } from "lucide-react";
import "./NeuralApp.css";

/**
 * Isolated visual shell for the SECOND Jarvis app; not wired to the existing
 * chat, memory or recording runtime. No personal memory is inferred here.
 * All memories are provided by the host through props, never written to storage.
 */
export type MemoryNode = { id: string; label: string; category: string; activity?: number };
export type DashboardWidget = { id: string; title: string; detail?: string };
export type NeuralAppProps = {
  memories?: MemoryNode[];
  widgets?: DashboardWidget[];
  listening?: boolean;
  busy?: boolean;
  onStartVoice?: () => void;
  onStopVoice?: () => void;
  onOpenWidget?: (id: string) => void;
  onAddWidget?: (title: string) => void;
  onRemoveWidget?: (id: string) => void;
  onOpenMemory?: (id: string) => void;
};
type View = "voice" | "memory" | "dashboard";
const defaults = [
  { id: "projects", title: "Progetti" }, { id: "calendar", title: "Calendario" },
  { id: "documents", title: "Documenti" }, { id: "tasks", title: "Attività" }
];
const colors = ["#40dfff", "#9c82ff", "#ffb75a", "#73e5ab"];
function NeuralMap({ nodes, onOpen }: { nodes: MemoryNode[]; onOpen?: (id: string) => void }) {
  const visible = nodes.slice(0, 14);
  const points = useMemo(() => visible.map((n, i) => {
    const t = i * 2.39996;
    const r = 36 + Math.sqrt((i + 1) / Math.max(visible.length, 1)) * 34;
    return { ...n, x: 50 + Math.cos(t) * r * .75, y: 50 + Math.sin(t) * r * .75 };
  }), [nodes]);
  return <div className="neural-graph" aria-label="Rete della memoria">
    <svg viewBox="0 0 100 100" role="img" aria-label="Connessioni dei ricordi">
      <defs><radialGradient id="neural-halo"><stop stopColor="#0fd6ff" stopOpacity=".35"/><stop offset="1" stopColor="#0fd6ff" stopOpacity="0"/></radialGradient></defs>
      <circle cx="50" cy="50" r="46" fill="url(#neural-halo)" />
      {points.map((p,i) => <g key={"wire-"+p.id}>
        <line x1="50" y1="50" x2={p.x} y2={p.y} className="neural-wire" style={{animationDelay:`${i * .19}s`}}/>
        {i > 0 && <line x1={points[i-1].x} y1={points[i-1].y} x2={p.x} y2={p.y} className="neural-wire-secondary" />}
      </g>)}
      <circle cx="50" cy="50" r="6" fill="#70e6ff" className="neural-core"/>
      {points.map((p,i)=><g key={p.id} className="neural-node" onClick={()=>onOpen?.(p.id)} role={onOpen?"button":undefined} tabIndex={onOpen?0:undefined} onKeyDown={e=>{if((e.key==="Enter"||e.key===" ")&&onOpen){e.preventDefault();onOpen(p.id)}}}>
        <circle cx={p.x} cy={p.y} r="3.2" fill={colors[i%colors.length]} />
        <circle cx={p.x} cy={p.y} r="5.5" fill="none" stroke={colors[i%colors.length]} opacity=".45"/>
        <title>{p.label}</title>
      </g>)}
    </svg>
    {!points.length && <p className="neural-empty">I ricordi compariranno qui quando Jarvis li renderà disponibili.</p>}
  </div>;
}
export function NeuralApp({ memories=[], widgets=defaults, listening=false, busy=false, onStartVoice, onStopVoice, onOpenWidget, onAddWidget, onRemoveWidget, onOpenMemory }: NeuralAppProps) {
  const [view,setView]=useState<View>("voice");
  const [adding,setAdding]=useState(false);
  const [widgetName,setWidgetName]=useState("");
  const [customWidgets,setCustomWidgets]=useState<DashboardWidget[]>([]);
  const [hidden,setHidden]=useState<string[]>([]);
  const shown = [...widgets,...customWidgets].filter(w=>!hidden.includes(w.id));
  useEffect(()=>{ if(adding) document.getElementById("neural-widget-name")?.focus(); },[adding]);
  function addWidget(){
    const title=widgetName.trim();
    if(!title) return;
    if(onAddWidget) onAddWidget(title);
    else setCustomWidgets(prev=>[...prev,{id:"custom-"+Date.now(),title,detail:"Personalizza in seguito"}]);
    setWidgetName("");setAdding(false);
  }
  function removeWidget(id:string){ if(onRemoveWidget) onRemoveWidget(id); else setHidden(prev=>[...prev,id]); }
  return <section className="neural-app" aria-label="DAX Jarvis, seconda app">
    <header className="neural-header"><div><span className="neural-brand">JARVIS</span><small>by DAX · SECONDA APP</small></div><span className="neural-status"><i/> Interfaccia privata</span></header>
    <main className="neural-main">
      {view==="voice" && <div className="neural-voice">
        <div className={"neural-orb "+(listening?"is-listening":"")}><BrainCircuit size={100} strokeWidth={.8}/><span className="neural-ring"/></div>
        <h1>{listening?"Ti ascolto":"Sono qui."}</h1><p>{listening?"Parla pure. Puoi interrompere in ogni momento.":"Parla con Jarvis e gestisci tutto con la tua voce."}</p>
        <div className="neural-voice-controls"><button className="neural-mic" type="button" onClick={listening?onStopVoice:onStartVoice} disabled={busy||(!onStartVoice&&!listening)} aria-label={listening?"Ferma microfono":"Attiva microfono"}>{listening?<MicOff/>:<Mic/>}</button>
        {listening && <button type="button" className="neural-stop" onClick={onStopVoice}>STOP</button>}</div>
        {!onStartVoice && <small className="neural-muted">La funzione vocale sarà disponibile dopo il collegamento al motore Jarvis.</small>}
      </div>}
      {view==="memory" && <div className="neural-memory"><h1>La tua memoria</h1><p>Le connessioni si illuminano durante l'attività.</p><NeuralMap nodes={memories} onOpen={onOpenMemory}/><div className="neural-memory-list">{memories.slice(0,8).map(n=><button key={n.id} onClick={()=>onOpenMemory?.(n.id)} type="button"><span>{n.label}</span><small>{n.category}</small></button>)}</div><p className="neural-muted">Visualizzazione locale: nessun dato viene trasmesso da questa schermata.</p></div>}
      {view==="dashboard" && <div className="neural-dashboard"><div className="neural-dashboard-head"><div><h1>Dashboard</h1><p>I tuoi strumenti, come vuoi tu.</p></div><button type="button" aria-label="Aggiungi riquadro" onClick={()=>setAdding(true)}><Plus/></button></div>
        <div className="neural-tiles">{shown.map((w,i)=><div key={w.id} className="neural-tile"><button className="neural-tile-open" onClick={()=>onOpenWidget?.(w.id)} type="button"><span className="neural-tile-icon" style={{color:colors[i%colors.length]}}><LayoutDashboard/></span><strong>{w.title}</strong><small>{w.detail||"Apri sezione"}</small></button><button type="button" className="neural-tile-remove" aria-label={`Nascondi ${w.title}`} onClick={()=>removeWidget(w.id)}><X size={14}/></button></div>)}</div>
        {adding && <form className="neural-add" onSubmit={e=>{e.preventDefault();addWidget()}}><label htmlFor="neural-widget-name">Nome del nuovo riquadro</label><input id="neural-widget-name" maxLength={48} value={widgetName} onChange={e=>setWidgetName(e.target.value)} placeholder="Es. La mia azienda"/><div><button type="submit">Aggiungi</button><button type="button" onClick={()=>setAdding(false)}>Annulla</button></div></form>}
        <p className="neural-muted">I riquadri sono personalizzabili; le funzioni reali saranno collegate una alla volta.</p>
      </div>}
    </main>
    <nav className="neural-nav" aria-label="Navigazione seconda app">
      <button type="button" aria-current={view==="voice"?"page":undefined} onClick={()=>setView("voice")}><Mic/><span>Voce</span></button>
      <button type="button" aria-current={view==="memory"?"page":undefined} onClick={()=>setView("memory")}><BrainCircuit/><span>Memoria</span></button>
      <button type="button" aria-current={view==="dashboard"?"page":undefined} onClick={()=>setView("dashboard")}><LayoutDashboard/><span>Dashboard</span></button>
    </nav><footer className="neural-footer"><Shield size={12}/> Accesso e dati governati da Jarvis</footer>
  </section>;
}
export default NeuralApp;
