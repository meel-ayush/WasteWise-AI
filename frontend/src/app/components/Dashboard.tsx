"use client";
import React, { useState, useEffect, useCallback, useRef } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import {
  Leaf, LogOut, Upload, CalendarDays, PlusCircle, BarChart2,
  Loader2, Trash2, RefreshCw, ShieldCheck, Volume2, DollarSign, Store,
  Brain, Camera, Zap, ShoppingBag, Link2, Monitor, Smartphone, Globe,
} from "lucide-react";
import Modal from "./Modal";
import FileIntentModal from "./FileIntentModal";
import VoicePanel from "./VoicePanel";
import ProfitTab from "./ProfitTab";
import StoreSettings from "./StoreSettings";
import OrdersPanel from "./OrdersPanel";
import ChainsPanel from "./ChainsPanel";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
type FileIntent = "none"|"sales"|"append"|"overwrite";
type TabKey = "upload"|"event"|"addItem"|"profit"|"store"|"orders"|"chains"|"insights"|"scan";

type MenuItem  = { item:string; base_daily_demand:number; profit_margin_rm:number };
type Restaurant = { id:string; name:string; region:string; menu:MenuItem[]; active_events:any[] };
type AccuracyRec = { date:string; day:string; total_actual:number; actual_sales?: any };
type DashboardData = { restaurant:Restaurant; region_info:{type?:string}; ai_forecast_message:string; accuracy_data:AccuracyRec[] };
type Session = {
  session_id:string; type:string; label:string; is_primary:boolean;
  expires_at:string|null; chat_id?:number; telegram_username?:string;
  last_active?:string; device_info?:string;
};
type IntelData = {
  item_trends: Record<string,{trend_dir:string;trend_pct:number;recommended_qty:number;confidence:string;has_anomaly:boolean;anomaly_note:string}>;
  mape_per_item: Record<string,{mape:number;bias:number;n:number}>;
  waste_metrics: {total_saved_rm:number;total_saved_kg:number;weekly_saved_rm:number;monthly_saved_rm:number};
  data_quality: {score:number;grade:string;label:string;n_records:number};
};

export default function Dashboard({ token, restaurantId, email, onLogout }: {token:string;restaurantId:string;email:string;onLogout:()=>void}) {
  const [data, setData]         = useState<DashboardData|null>(null);
  const [status, setStatus]     = useState("");
  const [tab, setTab]           = useState<TabKey>("upload");
  const [rawInput, setRawInput] = useState("");
  const [file, setFile]         = useState<File|null>(null);
  const [uploading, setUploading] = useState(false);
  const [evDesc, setEvDesc]     = useState("");
  const [evPeople, setEvPeople] = useState("");
  const [evDays, setEvDays]     = useState("1");
  const [newItem, setNewItem]   = useState("");
  const [newDemand, setNewDemand] = useState("50");
  const [newMargin, setNewMargin] = useState("3.00");
  const [intel, setIntel]       = useState<IntelData|null>(null);
  const [shopping, setShopping] = useState<any[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [showSecurity, setShowSecurity] = useState(false);
  const [showFileModal, setShowFileModal] = useState(false);
  const [pendingFile, setPendingFile] = useState<File|null>(null);
  const [speakText, setSpeakText] = useState<string|undefined>();
  const [causalResult, setCausalResult]   = useState<any>(null);
  const [menuEngResult, setMenuEngResult] = useState<any>(null);
  const [insightsLoading, setInsightsLoading] = useState(false);
  const [insightsStatus, setInsightsStatus]   = useState("");
  const [ttsActive, setTtsActive]             = useState<"causal"|"menu"|null>(null);
  const [scanFile, setScanFile]     = useState<File|null>(null);
  const [scanResult, setScanResult] = useState<any>(null);
  const [scanLoading, setScanLoading] = useState(false);
  
  const [makingPrimary, setMakingPrimary] = useState<string|null>(null);
  const [deleteRestFlow, setDeleteRestFlow] = useState(false);
  const [deleteApprovalToken, setDeleteApprovalToken] = useState<string|null>(null);
  const [deleteApprovalStatus, setDeleteApprovalStatus] = useState<"idle"|"pending"|"approved"|"denied">("idle");
  const [deleteKeepData, setDeleteKeepData] = useState(true);
  const prevForecast = useRef("");

  const fetchDashboard = useCallback(async (silent=false) => {
    if (!silent) setStatus("🔄 Refreshing…");
    try {
      const ts = Date.now();
      const r = await fetch(`${API}/api/dashboard/${restaurantId}?t=${ts}`, { headers: {"Authorization": `Bearer ${token}`}, cache: "no-store" });
      if (r.status === 401) { onLogout(); return null; }
      if (!r.ok) { if (!silent) setStatus("❌ Backend error: "+r.status); return null; }
      const j: DashboardData = await r.json();
      setData(j);
      if (!silent) setStatus("");
      fetch(`${API}/api/intelligence/${restaurantId}?t=${ts}`, { headers: {"Authorization": `Bearer ${token}`}, cache: "no-store" }).then(r=>r.ok?r.json():null).then(d=>{if(d)setIntel(d);}).catch(()=>null);
      fetch(`${API}/api/shopping_list/${restaurantId}?t=${ts}`, { headers: {"Authorization": `Bearer ${token}`}, cache: "no-store" }).then(r=>r.ok?r.json():null).then(d=>{if(d)setShopping(d.shopping_list||[]);}).catch(()=>null);
      return j.ai_forecast_message;
    } catch {
      if (!silent) setStatus("❌ Cannot reach the server. Please check your connection or try again later.");
      return null;
    }
  }, [restaurantId, token]);

  const fetchSessions = useCallback(async () => {
    if (!token) return;
    try {
      const r = await fetch(`${API}/api/auth/me`, { headers:{"Authorization":`Bearer ${token}`} });
      if (r.status === 401) { onLogout(); return; }
      if (r.ok) setSessions((await r.json()).sessions || []);
    } catch {}
  }, [token]);

  useEffect(() => { fetchDashboard(); fetchSessions(); }, [fetchDashboard, fetchSessions]);

  // Real-time SSE updates
  useEffect(() => {
    if (!token || !restaurantId) return;
    const API = process.env.NEXT_PUBLIC_API_URL || "";
    const es = new EventSource(`${API}/api/dashboard/${restaurantId}/stream?token=${token}`);
    
    es.onmessage = (event) => {
      if (event.data === "refresh") {
        fetchDashboard(true);
        window.dispatchEvent(new Event('refresh_orders'));
      } else if (event.data.startsWith("logout_session:")) {
        const prefix = event.data.split(":")[1];
        if (token.startsWith(prefix)) {
          onLogout();
        } else {
          fetchSessions();
        }
      }
    };
    
    es.onerror = () => {
      es.close();
      setTimeout(() => {
         if (document.visibilityState === "visible") fetchDashboard(true);
      }, 5000);
    };
    
    return () => es.close();
  }, [token, restaurantId, fetchDashboard]);



  const handleUpload = async (mode:string, f?:File|null) => {
    const activeFile = f!==undefined ? f : file;
    if (!rawInput.trim()&&!activeFile) { setStatus("⚠️ Enter data or attach a file first."); return; }
    setUploading(true); setStatus("🤖 Processing…");
    try {
      let res: Response;
      if (activeFile) {
        const fd = new FormData();
        fd.append("restaurant_id",restaurantId); fd.append("menu_mode",mode); fd.append("file",activeFile);
        res = await fetch(`${API}/api/upload_file`,{method:"POST",headers:{"Authorization": `Bearer ${token}`},body:fd});
      } else {
        res = await fetch(`${API}/api/upload`,{method:"POST",headers:{"Authorization": `Bearer ${token}`,"Content-Type":"application/json"},body:JSON.stringify({restaurant_id:restaurantId,action:rawInput,menu_mode:mode})});
      }
      if (!res.ok) {
        try { const err = await res.json(); setStatus("❌ " + (err.detail || "Upload failed.")); }
        catch { setStatus(`❌ Server Error ${res.status}`); }
        return;
      }
      const j = await res.json();
      setStatus(j.message||j.detail||"✅ Done!"); setRawInput(""); setFile(null); setPendingFile(null);
    } catch { setStatus("❌ Upload failed."); }
    finally { setUploading(false); }
  };

  const removeSession = async (sessionId:string) => {
    const r = await fetch(`${API}/api/auth/session/${sessionId}`,{method:"DELETE",headers:{"Authorization":`Bearer ${token}`}});
    if (!r.ok && r.status !== 404) { const e = await r.json().catch(()=>({})); setStatus("❌ "+(e.detail||"Could not remove session.")); }
    fetchSessions();
  };

  const makePrimarySession = async (sessionId:string) => {
    setMakingPrimary(sessionId);
    const r = await fetch(`${API}/api/auth/session/${sessionId}/make_primary`,{method:"PATCH",headers:{"Authorization":`Bearer ${token}`}});
    if (r.ok) { setStatus("✅ Primary transferred."); fetchSessions(); }
    else { const e = await r.json().catch(()=>({})); setStatus("❌ "+(e.detail||"Could not transfer primary.")); }
    setMakingPrimary(null);
  };

  const requestDeleteApproval = async (keepData: boolean) => {
    setDeleteKeepData(keepData);
    setDeleteApprovalStatus("pending");
    try {
      const r = await fetch(`${API}/api/auth/dashboard_action/request?action=delete_restaurant&restaurant_id=${restaurantId}`,
        { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      if (r.ok) { const j = await r.json(); setDeleteApprovalToken(j.approval_token); }
      else { setDeleteApprovalStatus("idle"); }
    } catch { setDeleteApprovalStatus("idle"); }
  };

  
  useEffect(() => {
    if (!deleteApprovalToken) return;
    const i = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/auth/dashboard_action/status/${deleteApprovalToken}`,
          { headers: { Authorization: `Bearer ${token}` } });
        if (r.ok) {
          const j = await r.json();
          if (j.status === "approved") {
            setDeleteApprovalStatus("approved");
            clearInterval(i);
            setDeleteApprovalToken(null);
            
            const dr = await fetch(`${API}/api/restaurant/${restaurantId}`, {
              method: "DELETE",
              headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
              body: JSON.stringify({ keep_data: deleteKeepData, delete_entire_chain: false }),
            });
            if (dr.ok) { onLogout(); }
            else { const e = await dr.json().catch(()=>({})); setStatus("❌ "+(e.detail||"Delete failed.")); }
          } else if (j.status === "denied") {
            setDeleteApprovalStatus("denied");
            clearInterval(i);
            setDeleteApprovalToken(null);
            setStatus("❌ Delete rejected by primary Telegram account.");
          }
        }
      } catch {}
    }, 3000);
    return () => clearInterval(i);
  
  }, [deleteApprovalToken]);

  const runCausalAnalysis = async (targetDate?:string) => {
    setInsightsLoading(true); setInsightsStatus("🔍 Analysing root cause…"); setCausalResult(null);
    try {
      let derivedDate = new Date(Date.now()-86400000).toISOString().slice(0,10);
      if (data && data.accuracy_data && data.accuracy_data.length > 0) {
        const sortedDates = data.accuracy_data.map(a => a.date).sort();
        if (sortedDates.length >= 2) {
          derivedDate = sortedDates[sortedDates.length - 2];
        } else if (sortedDates.length === 1) {
          derivedDate = sortedDates[0];
        }
      }
      const date = targetDate || derivedDate;
      const r = await fetch(`${API}/api/restaurant/${restaurantId}/causal_analysis?target_date=${date}`,
        {headers:{"Authorization":`Bearer ${token}`}});
      if (!r.ok) {
        try { const err = await r.json(); setInsightsStatus("❌ "+(err.detail||r.status)); }
        catch { setInsightsStatus(`❌ Server Error ${r.status}`); }
        return;
      }
      setCausalResult(await r.json()); setInsightsStatus("");
    } catch { setInsightsStatus("❌ Could not reach backend."); }
    finally { setInsightsLoading(false); }
  };

  const runMenuEngineering = async () => {
    setInsightsLoading(true); setInsightsStatus("🧠 Classifying menu items…"); setMenuEngResult(null);
    try {
      const r = await fetch(`${API}/api/restaurant/${restaurantId}/menu_engineering`,
        {headers:{"Authorization":`Bearer ${token}`}});
      if (!r.ok) {
        try { const err = await r.json(); setInsightsStatus("❌ "+(err.detail||r.status)); }
        catch { setInsightsStatus(`❌ Server Error ${r.status}`); }
        return;
      }
      setMenuEngResult(await r.json()); setInsightsStatus("");
    } catch { setInsightsStatus("❌ Could not reach backend."); }
    finally { setInsightsLoading(false); }
  };

  
  useEffect(() => {
    if (tab === "insights" && !causalResult && !insightsLoading) {
      runCausalAnalysis();
    }
  
  }, [tab]);

  useEffect(() => {
    if (tab === "insights" && causalResult && !menuEngResult && !insightsLoading) {
      runMenuEngineering();
    }
  
  }, [causalResult]);

  const speakInsights = (text: string, which: "causal"|"menu") => {
    if (typeof window === "undefined" || !window.speechSynthesis) return;
    if (ttsActive === which) {
      window.speechSynthesis.cancel();
      setTtsActive(null);
      return;
    }
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(text.replace(/\*/g, ''));
    utt.lang  = "en-US";
    utt.rate  = 0.95;
    utt.pitch = 1;
    utt.onend = () => setTtsActive(null);
    utt.onerror = () => setTtsActive(null);
    setTtsActive(which);
    window.speechSynthesis.speak(utt);
  };

  const runCVScan = async () => {
    if (!scanFile) return;
    setScanLoading(true); setScanResult(null);
    try {
      const fd = new FormData(); fd.append("file", scanFile);
      const r = await fetch(`${API}/api/restaurant/${restaurantId}/cv_inventory`,
        {method:"POST",headers:{"Authorization":`Bearer ${token}`},body:fd});
      if (r.ok) {
        try { setScanResult(await r.json()); }
        catch { setScanResult({error:"Could not parse response."}); }
      } else {
        try { const e = await r.json(); setScanResult({error: e.detail || `Server error ${r.status}.`}); }
        catch { setScanResult({error: `Server error ${r.status}.`}); }
      }
    } catch { setScanResult({error:"Could not reach backend."}); }
    finally { setScanLoading(false); }
  };

  if (!data) return (
    <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center",background:"var(--bg)"}}>
      <div style={{textAlign:"center",color:"var(--txt2)"}}>
        <Loader2 size={40} style={{animation:"spin 1s linear infinite",marginBottom:"16px",color:"var(--green)"}}/>
        <p>Loading your dashboard…</p>
      </div>
    </div>
  );

  const {restaurant,region_info,ai_forecast_message,accuracy_data} = data;
  const chartData = accuracy_data.map(r=>({name:r.day,Actual:r.total_actual||0,actual_sales:r.actual_sales}));
  const isRegen   = ai_forecast_message.includes("regenerating");

  const tabs = [
    {key:"upload"   as TabKey,icon:<Upload size={14}/>,label:"Upload"},
    {key:"event"    as TabKey,icon:<CalendarDays size={14}/>,label:"Event"},
    {key:"addItem"  as TabKey,icon:<PlusCircle size={14}/>,label:"Add Item"},
    {key:"profit"   as TabKey,icon:<DollarSign size={14}/>,label:"Sales & Profit"},
    {key:"store"    as TabKey,icon:<Store size={14}/>,label:"Marketplace"},
    {key:"orders"   as TabKey,icon:<ShoppingBag size={14}/>,label:"Orders"},
    {key:"chains"   as TabKey,icon:<Link2 size={14}/>,label:"Chains"},
    {key:"insights" as TabKey,icon:<Brain size={14}/>,label:"AI Insights"},
    {key:"scan"     as TabKey,icon:<Camera size={14}/>,label:"Scan Stock"},
  ];

  return (
    <div style={{minHeight:"100vh",background:"var(--bg)",color:"var(--txt)",fontFamily:"var(--font)"}}>

      {showFileModal && pendingFile && (
        <FileIntentModal filename={pendingFile.name} onConfirm={intent=>{setShowFileModal(false);setFile(pendingFile);handleUpload(intent,pendingFile);}} onCancel={()=>{setShowFileModal(false);setPendingFile(null);}}/>
      )}

      {showSecurity && (
        <Modal onClose={()=>{setShowSecurity(false);setDeleteRestFlow(false);setDeleteApprovalStatus("idle");}}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",margin:"0 0 14px"}}>
            <h2 style={{margin:0,fontSize:"1rem",fontWeight:700}}>🔐 Account Security</h2>
          </div>
          <p style={{margin:"0 0 12px",fontSize:"0.8rem",color:"var(--txt3)"}}>{email}</p>
          <div style={{display:"flex",flexDirection:"column",gap:"8px",maxHeight:"360px",overflowY:"auto"}}>
            {sessions.map(s=>{
              const isTelegram = s.type==="telegram";
              const isWeb = s.type==="web" || !s.type;
              const iAmPrimary = sessions.some(x=>x.is_primary && x.session_id===token.substring(0,8));
              let activeStr = "";
              let isActiveNow = false;
              if (s.last_active) {
                try {
                  const d = new Date(s.last_active.endsWith('Z') ? s.last_active : s.last_active + 'Z');
                  const diff = Date.now() - d.getTime();
                  if (diff < 5 * 60 * 1000) { isActiveNow = true; activeStr = "Active now"; }
                  else if (diff < 60 * 60 * 1000) activeStr = `Active ${Math.floor(diff/60000)}m ago`;
                  else if (diff < 24 * 60 * 60 * 1000) activeStr = `Active ${Math.floor(diff/3600000)}h ago`;
                  else activeStr = `Active ${Math.floor(diff/86400000)}d ago`;
                } catch {}
              }
              return (
                <div key={s.session_id} style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",padding:"10px 12px",background:"var(--input)",borderRadius:"8px",gap:"10px"}}>
                  <div style={{flex:1,minWidth:0}}>
                    <div style={{display:"flex",alignItems:"center",gap:"6px",marginBottom:"2px"}}>
                      {s.is_primary && <span style={{fontSize:"0.7rem",background:"rgba(16,185,129,0.2)",color:"var(--green)",padding:"1px 6px",borderRadius:"20px",fontWeight:700}}>⭐ PRIMARY</span>}
                      {isTelegram && <Smartphone size={12} color="#3b82f6"/>}
                      {isWeb && <Globe size={12} color="var(--txt3)"/>}
                      <p style={{margin:0,fontSize:"0.85rem",fontWeight:600,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>
                        {isTelegram ? `@${s.telegram_username||s.label||"Telegram"}` : s.label||"Web session"}
                      </p>
                    </div>
                    <p style={{margin:0,fontSize:"0.72rem",color:"var(--txt3)",display:"flex",alignItems:"center"}}>
                      {s.is_primary?"Primary · never expires":s.expires_at?`Exp. ${s.expires_at.slice(0,10)}`:""}
                      {activeStr && (
                        <>
                          {(s.is_primary || s.expires_at) && <span style={{margin:"0 6px",opacity:0.5}}>•</span>}
                          <span style={{color:isActiveNow?"var(--green)":"inherit",display:"flex",alignItems:"center"}}>
                            {isActiveNow && <span style={{display:"inline-block",width:"6px",height:"6px",borderRadius:"50%",background:"var(--green)",marginRight:"4px"}}></span>}
                            {activeStr}
                          </span>
                        </>
                      )}
                    </p>
                  </div>
                  <div style={{display:"flex",flexDirection:"column",gap:"4px",flexShrink:0}}>
                    {!s.is_primary && (
                      <button onClick={()=>removeSession(s.session_id)} style={{background:"transparent",border:"1px solid #ef4444",color:"#ef4444",borderRadius:"6px",padding:"3px 9px",cursor:"pointer",fontSize:"0.75rem"}}>Remove</button>
                    )}
                    {isTelegram && !s.is_primary && iAmPrimary && (
                      <button onClick={()=>makePrimarySession(s.session_id)} disabled={makingPrimary===s.session_id}
                        style={{background:"transparent",border:"1px solid var(--green)",color:"var(--green)",borderRadius:"6px",padding:"3px 9px",cursor:"pointer",fontSize:"0.75rem"}}>
                        {makingPrimary===s.session_id?"…":"Make Primary"}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <p style={{margin:"12px 0 0",fontSize:"0.72rem",color:"var(--txt3)"}}>Web sessions expire after 30 days. Telegram primary never expires.</p>
          
          <hr style={{border:"none",borderTop:"1px solid var(--bdr)",margin:"14px 0"}}/>
          {!deleteRestFlow ? (
            <button onClick={()=>setDeleteRestFlow(true)}
              style={{width:"100%",padding:"10px",borderRadius:"8px",background:"rgba(239,68,68,0.1)",color:"#ef4444",border:"1px solid rgba(239,68,68,0.25)",cursor:"pointer",fontWeight:700,fontSize:"0.83rem"}}>
              🗑️ Delete This Restaurant…
            </button>
          ) : (
            <div style={{display:"flex",flexDirection:"column",gap:"10px"}}>
              <p style={{margin:0,fontSize:"0.83rem",fontWeight:700,color:"#ef4444"}}>🗑️ Delete Restaurant — Choose how:</p>
              {deleteApprovalStatus==="pending" ? (
                <div style={{padding:"12px",background:"rgba(245,158,11,0.1)",borderRadius:"8px",display:"flex",alignItems:"center",gap:"8px"}}>
                  <Loader2 size={16} color="#f59e0b" style={{animation:"spin 1s linear infinite"}}/>
                  <p style={{margin:0,fontSize:"0.8rem",color:"#f59e0b"}}>Waiting for primary Telegram approval…</p>
                </div>
              ) : deleteApprovalStatus==="denied" ? (
                <p style={{margin:0,fontSize:"0.8rem",color:"#ef4444"}}>❌ Rejected by primary Telegram account.</p>
              ) : (
                <>
                  <p style={{margin:0,fontSize:"0.77rem",color:"var(--txt3)",lineHeight:1.5}}>⚠️ Requires approval from your primary Telegram account.</p>
                  <button onClick={()=>requestDeleteApproval(true)}
                    style={{padding:"10px",borderRadius:"8px",background:"rgba(16,185,129,0.1)",color:"var(--green)",border:"1px solid rgba(16,185,129,0.3)",cursor:"pointer",fontWeight:600,fontSize:"0.82rem"}}>
                    🌿 Anonymise &amp; keep AI data (recommended)
                  </button>
                  <button onClick={()=>requestDeleteApproval(false)}
                    style={{padding:"10px",borderRadius:"8px",background:"rgba(239,68,68,0.08)",color:"#ef4444",border:"1px solid rgba(239,68,68,0.2)",cursor:"pointer",fontWeight:600,fontSize:"0.82rem"}}>
                    💣 Delete everything permanently
                  </button>
                  <button onClick={()=>setDeleteRestFlow(false)}
                    style={{padding:"8px",borderRadius:"8px",background:"transparent",color:"var(--txt3)",border:"1px solid var(--bdr)",cursor:"pointer",fontSize:"0.8rem"}}>
                    Cancel
                  </button>
                </>
              )}
            </div>
          )}
        </Modal>
      )}

      {}
      <header style={{background:"var(--card)",borderBottom:"1px solid var(--bdr)",padding:"14px 24px",display:"flex",alignItems:"center",justifyContent:"space-between",position:"sticky",top:0,zIndex:100}}>
        <div style={{display:"flex",alignItems:"center",gap:"10px"}}>
          <span style={{fontSize:"1.4rem"}}>🍱</span>
          <div>
            <h1 style={{margin:0,fontSize:"1.05rem",fontWeight:700}}>{restaurant.name}</h1>
            <p style={{margin:0,fontSize:"0.78rem",color:"var(--txt2)"}}>{region_info.type||restaurant.region}</p>
          </div>
        </div>
        <div style={{display:"flex",gap:"8px"}}>
          <a href={`/marketplace?store=${restaurantId}&ref=${token}`} target="_blank" rel="noreferrer"
            style={{background:"rgba(16,185,129,0.15)",border:"1px solid rgba(16,185,129,0.3)",color:"#10b981",padding:"7px 10px",borderRadius:"8px",cursor:"pointer",display:"flex",alignItems:"center",gap:"5px",fontSize:"0.82rem",fontWeight:600,textDecoration:"none"}}><ShoppingBag size={14}/>View Marketplace</a>
          <button onClick={()=>{setShowSecurity(true);fetchSessions();}} style={{background:"transparent",border:"1px solid var(--bdr)",color:"var(--txt2)",padding:"7px 10px",borderRadius:"8px",cursor:"pointer",display:"flex",alignItems:"center",gap:"5px",fontSize:"0.82rem"}}><ShieldCheck size={14}/>Security</button>
          <button onClick={()=>fetchDashboard()} style={{background:"transparent",border:"1px solid var(--bdr)",color:"var(--txt2)",padding:"7px 10px",borderRadius:"8px",cursor:"pointer",display:"flex",alignItems:"center",gap:"5px",fontSize:"0.82rem"}}><RefreshCw size={14}/>Refresh</button>
          <button onClick={onLogout} style={{background:"transparent",border:"1px solid var(--bdr)",color:"var(--txt2)",padding:"7px 10px",borderRadius:"8px",cursor:"pointer",display:"flex",alignItems:"center",gap:"5px",fontSize:"0.82rem"}}><LogOut size={14}/>Logout</button>
        </div>
      </header>

      <div style={{maxWidth:"1400px",margin:"0 auto",padding:"20px",display:"grid",gap:"18px"}}>

        {}
        {status && (
          <div style={{padding:"12px 16px",borderRadius:"9px",background:status.startsWith("❌")||status.startsWith("⚠")?"rgba(239,68,68,0.1)":"rgba(16,185,129,0.1)",border:`1px solid ${status.startsWith("❌")||status.startsWith("⚠")?"rgba(239,68,68,0.3)":"rgba(16,185,129,0.3)"}`,color:status.startsWith("❌")||status.startsWith("⚠")?"#f87171":"#34d399",fontSize:"0.85rem",whiteSpace:"pre-wrap",position:"relative"}}>
            {status}
            <button onClick={()=>setStatus("")} style={{position:"absolute",top:"8px",right:"10px",background:"transparent",border:"none",color:"inherit",cursor:"pointer"}}>✕</button>
          </div>
        )}

        {}
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"18px"}}>
          <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"20px"}}>
            <div style={{display:"flex",alignItems:"center",gap:"7px",marginBottom:"12px"}}>
              <BarChart2 size={18} color="var(--green)"/>
              <h2 style={{margin:0,fontSize:"0.95rem",fontWeight:600}}>Today's Forecast</h2>
              {isRegen && <span style={{display:"flex",alignItems:"center",gap:"3px",marginLeft:"auto",color:"var(--txt3)",fontSize:"0.75rem"}}><Loader2 size={11} style={{animation:"spin 1s linear infinite"}}/>updating</span>}
              <button
                onClick={()=>setSpeakText(ai_forecast_message.replace(/\*/g, ''))}
                title="Read forecast aloud"
                style={{marginLeft:"auto",background:"transparent",border:"1px solid var(--bdr)",color:"var(--txt3)",borderRadius:"7px",padding:"4px 8px",cursor:"pointer",display:"flex",alignItems:"center",gap:"4px",fontSize:"0.75rem"}}
              >
                <Volume2 size={13}/> Listen
              </button>
            </div>
            <div style={{background:"var(--input)",borderRadius:"8px",padding:"14px",fontSize:"0.85rem",lineHeight:"1.75",whiteSpace:"pre-wrap",minHeight:"100px"}}>{ai_forecast_message||"Loading…"}</div>
          </div>

          <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"20px"}}>
            <div style={{display:"flex",alignItems:"center",gap:"7px",marginBottom:"12px"}}>
              <Leaf size={18} color="var(--green)"/>
              <h2 style={{margin:0,fontSize:"0.95rem",fontWeight:600}}>Menu ({restaurant.menu.length})</h2>
            </div>
            <div style={{maxHeight:"200px",overflowY:"auto"}}>
              {restaurant.menu.length===0
                ? <p style={{color:"var(--txt3)",fontSize:"0.83rem",textAlign:"center",paddingTop:"16px"}}>No items yet. Use the Upload tab to add your menu.</p>
                : restaurant.menu.map((m,i)=>(
                  <div key={i} style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"7px 9px",borderRadius:"7px",marginBottom:"4px",background:"var(--input)"}}>
                    <span style={{fontSize:"0.85rem"}}>{m.item} <span style={{color:"var(--txt3)"}}>₹{m.profit_margin_rm.toFixed(2)}</span></span>
                    <button onClick={async()=>{if(!confirm(`Delete "${m.item}"?`))return;await fetch(`${API}/api/menu/${restaurantId}/${encodeURIComponent(m.item)}`,{method:"DELETE",headers:{"Authorization":`Bearer ${token}`}});fetchDashboard();}} style={{background:"transparent",border:"none",color:"#ef4444",cursor:"pointer"}}><Trash2 size={13}/></button>
                  </div>
                ))
              }
            </div>
          </div>
        </div>

        {}
        {intel && (
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:"18px"}}>
            <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"18px"}}>
              <h3 style={{margin:"0 0 12px",fontSize:"0.88rem",fontWeight:600}}>📡 AI Learning Level</h3>
              <div style={{display:"flex",alignItems:"center",gap:"10px"}}>
                <div style={{fontSize:"2.2rem",fontWeight:800,color:intel.data_quality.score>=80?"var(--green)":intel.data_quality.score>=60?"#f59e0b":"#ef4444"}}>{intel.data_quality.grade}</div>
                <div><p style={{margin:0,fontWeight:700}}>{intel.data_quality.score}/100</p><p style={{margin:0,fontSize:"0.78rem",color:"var(--txt2)"}}>{intel.data_quality.label}</p><p style={{margin:0,fontSize:"0.74rem",color:"var(--txt3)"}}>{intel.data_quality.n_records} days of history</p></div>
              </div>
            </div>
            <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"18px"}}>
              <h3 style={{margin:"0 0 12px",fontSize:"0.88rem",fontWeight:600}}>💰 Money Saved</h3>
              <p style={{margin:"0 0 3px",fontSize:"1.4rem",fontWeight:800,color:"var(--green)"}}>₹{intel.waste_metrics.total_saved_rm.toFixed(2)}</p>
              <p style={{margin:"0 0 3px",fontSize:"0.8rem",color:"var(--txt2)"}}>{intel.waste_metrics.total_saved_kg.toFixed(1)} kg less waste</p>
              <p style={{margin:0,fontSize:"0.75rem",color:"var(--txt3)"}}>₹{intel.waste_metrics.weekly_saved_rm.toFixed(2)}/week</p>
            </div>
            <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"18px"}}>
              <h3 style={{margin:"0 0 4px",fontSize:"0.88rem",fontWeight:600}}>🎯 Prediction Accuracy</h3>
              <p style={{margin:"0 0 10px",fontSize:"0.73rem",color:"var(--txt3)"}}>Per item — worst first</p>
              {Object.entries(intel.mape_per_item).length===0
                ? <p style={{color:"var(--txt3)",fontSize:"0.8rem"}}>Upload sales to track accuracy</p>
                : <div style={{display:"flex",flexDirection:"column",gap:"9px",maxHeight:"160px",overflowY:"auto"}}>
                    {Object.entries(intel.mape_per_item).sort((a,b)=>(b[1] as any).mape-(a[1] as any).mape).map(([item,d])=>{
                      const acc=Math.max(0,100-(d as any).mape);
                      const col=acc>=85?"var(--green)":acc>=70?"#f59e0b":"#ef4444";
                      return (
                        <div key={item}>
                          <div style={{display:"flex",justifyContent:"space-between",fontSize:"0.78rem",marginBottom:"3px"}}>
                            <span style={{overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap",maxWidth:"58%"}}>{item}</span>
                            <span style={{color:col,fontWeight:700}}>{acc.toFixed(0)}%</span>
                          </div>
                          <div style={{height:"4px",background:"var(--input)",borderRadius:"2px"}}><div style={{height:"100%",width:`${acc}%`,background:col,borderRadius:"2px",transition:"width 0.4s"}}/></div>
                        </div>
                      );
                    })}
                  </div>
              }
            </div>
          </div>
        )}

        {}
        {shopping.length>0 && (
          <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"20px"}}>
            <h2 style={{margin:"0 0 6px",fontSize:"0.95rem",fontWeight:600}}>🛒 Today's Ingredient Shopping List</h2>
            <p style={{margin:"0 0 14px",fontSize:"0.8rem",color:"var(--txt3)"}}>Buy exactly these amounts to prepare just enough and waste nothing.</p>
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(180px,1fr))",gap:"7px"}}>
              {shopping.map((s,i)=>(
                <div key={i} style={{display:"flex",justifyContent:"space-between",padding:"9px 12px",background:"var(--input)",borderRadius:"7px"}}>
                  <span style={{fontSize:"0.84rem"}}>{s.ingredient}</span>
                  <span style={{fontSize:"0.84rem",color:"var(--green)",fontWeight:700,whiteSpace:"nowrap",marginLeft:"8px"}}>{s.quantity} {s.unit}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {}
        <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"20px"}}>
          <h2 style={{margin:"0 0 4px",fontSize:"0.95rem",fontWeight:600}}>📊 Sales History</h2>
          <p style={{margin:"0 0 16px",fontSize:"0.8rem",color:"var(--txt3)"}}>Upload daily sales to see your actual numbers here.</p>
          {chartData.length===0
            ? <div style={{textAlign:"center",padding:"32px",color:"var(--txt3)"}}><BarChart2 size={36} style={{opacity:0.3,marginBottom:"10px"}}/><p>No sales data yet.</p></div>
            : <ResponsiveContainer width="100%" height={200}><BarChart data={chartData}><CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false}/><XAxis dataKey="name" stroke="var(--txt3)" tick={{fill:"var(--txt3)",fontSize:11}} axisLine={false} interval={0} angle={-35} textAnchor="end" height={45} /><YAxis stroke="var(--txt3)" tick={{fill:"var(--txt3)",fontSize:11}} axisLine={false} tickLine={false}/><Tooltip content={({active,payload,label}:any)=>{if(active&&payload&&payload.length){const d=payload[0].payload;return <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"8px",padding:"12px",fontSize:"0.85rem"}}><p style={{margin:"0 0 6px",fontWeight:600,color:"var(--txt)"}}>{label}</p><p style={{margin:"0 0 10px",color:"var(--green)",fontWeight:600}}>Total: {d.Actual}</p>{d.actual_sales && Object.keys(d.actual_sales).length>0 && <div style={{display:"flex",flexDirection:"column",gap:"4px"}}>{Object.entries(d.actual_sales).sort((a:any,b:any)=>b[1]-a[1]).map(([k,v]:any)=><div key={k} style={{display:"flex",justifyContent:"space-between",gap:"20px"}}><span style={{color:"var(--txt2)"}}>{k}</span><span style={{color:"var(--txt)",fontWeight:500}}>{v}</span></div>)}</div>}</div>;}return null;}} cursor={{fill:"rgba(255,255,255,0.05)"}}/><Bar dataKey="Actual" fill="var(--green)" radius={[4,4,0,0]} name="Items sold"/></BarChart></ResponsiveContainer>
          }
        </div>

        {}
        <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",overflow:"hidden"}}>
          <div style={{display:"flex",borderBottom:"1px solid var(--bdr)",overflowX:"auto"}}>
            {tabs.map(t=>(
              <button key={t.key} onClick={()=>setTab(t.key)} style={{flex:"0 0 auto",padding:"13px 16px",background:tab===t.key?"var(--input)":"transparent",border:"none",borderBottom:tab===t.key?"2px solid var(--green)":"2px solid transparent",color:tab===t.key?"var(--txt)":"var(--txt3)",cursor:"pointer",fontWeight:tab===t.key?600:400,display:"flex",alignItems:"center",gap:"5px",fontSize:"0.85rem",whiteSpace:"nowrap"}}>
                {t.icon}{t.label}
              </button>
            ))}
          </div>

          <div style={{padding:"20px"}}>
            {}
            {tab==="upload" && (
              <div style={{display:"flex",flexDirection:"column",gap:"12px"}}>
                <p style={{margin:0,color:"var(--txt2)",fontSize:"0.83rem"}}>Paste text or attach a file — a popup will ask what to do with it. Or use the 🎤 voice button to speak your data!</p>
                <div style={{display:"flex",gap:"10px"}}>
                  <textarea
                    id="upload-text-area"
                    value={rawInput}
                    onChange={e=>setRawInput(e.target.value)}
                    disabled={!!file||uploading}
                    placeholder={"Paste sales data or menu items as text…\ne.g. Pyaaz Kachori 95, Masala Chai 62"}
                    style={{flex:1,height:"80px",resize:"none",background:"var(--input)",color:"var(--txt)",border:"1px solid var(--bdr)",borderRadius:"9px",padding:"11px",fontFamily:"monospace",fontSize:"0.83rem",outline:"none"}}
                  />
                  <label style={{width:"120px",display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",border:`1px dashed ${file?"var(--green)":"var(--bdr)"}`,borderRadius:"9px",cursor:"pointer",background:file?"rgba(16,185,129,0.1)":"var(--input)",color:file?"var(--green)":"var(--txt3)",fontSize:"0.78rem",textAlign:"center",gap:"4px",padding:"8px"}}>
                    <input type="file" accept=".csv,.txt,.xlsx,.xls,.jpg,.jpeg,.png" style={{display:"none"}} onChange={e=>{const f=e.target.files?.[0]||null;if(f){setPendingFile(f);setShowFileModal(true);e.target.value="";}}}/>
                    <Upload size={16}/>{file?file.name:"Attach File"}
                  </label>
                </div>
                {rawInput.trim()&&!file&&(
                  <div style={{display:"flex",gap:"8px"}}>
                    {([{mode:"none",label:"📈 Log Sales",color:"#3b82f6",bg:"rgba(59,130,246,0.12)"},{mode:"append",label:"➕ Add to Menu",color:"#f59e0b",bg:"rgba(245,158,11,0.12)"},{mode:"overwrite",label:"🔄 Replace Menu",color:"var(--green)",bg:"rgba(16,185,129,0.12)"}] as const).map(b=>(
                      <button key={b.mode} onClick={()=>handleUpload(b.mode)} disabled={uploading} style={{flex:1,padding:"10px",borderRadius:"8px",background:b.bg,color:b.color,border:`1px solid ${b.color}40`,cursor:uploading?"not-allowed":"pointer",fontWeight:600,fontSize:"0.8rem",opacity:uploading?0.5:1}}>{uploading?"…":b.label}</button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {}
            {tab==="event" && (
              <div style={{display:"flex",flexDirection:"column",gap:"12px"}}>
                <p style={{margin:0,color:"var(--txt2)",fontSize:"0.83rem"}}>Declare a special event so the AI scales the forecast to your expected guests.</p>
                <div style={{display:"grid",gridTemplateColumns:"1fr auto auto",gap:"9px"}}>
                  <input value={evDesc} onChange={e=>setEvDesc(e.target.value)} placeholder="e.g. Wedding reception" style={{padding:"11px",borderRadius:"8px",background:"var(--input)",color:"var(--txt)",border:"1px solid var(--bdr)",fontSize:"0.85rem",outline:"none"}}/>
                  <input value={evPeople} onChange={e=>setEvPeople(e.target.value)} type="number" min="1" placeholder="Guests" style={{width:"90px",padding:"11px",borderRadius:"8px",background:"var(--input)",color:"var(--txt)",border:"1px solid var(--bdr)",fontSize:"0.85rem",outline:"none"}}/>
                  <div style={{display:"flex",alignItems:"center",gap:"5px",background:"var(--input)",border:"1px solid var(--bdr)",borderRadius:"8px",padding:"0 10px"}}>
                    <input value={evDays} onChange={e=>setEvDays(e.target.value)} type="number" min="1" max="30" style={{width:"40px",background:"transparent",border:"none",color:"var(--txt)",fontSize:"0.85rem",outline:"none",textAlign:"center"}}/>
                    <span style={{color:"var(--txt3)",fontSize:"0.78rem",whiteSpace:"nowrap"}}>day(s)</span>
                  </div>
                </div>
                <button onClick={async()=>{if(!evDesc||!evPeople)return;setStatus("🎉 Registering event…");const r=await fetch(`${API}/api/event/${restaurantId}`,{method:"POST",headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json"},body:JSON.stringify({description:evDesc.trim(),headcount:parseInt(evPeople),days:parseInt(evDays)})});if(!r.ok){try{setStatus("❌ "+((await r.json()).detail||r.status));}catch{setStatus(`❌ Error ${r.status}`);}return;}setStatus((await r.json()).message||"✅ Done");setEvDesc("");setEvPeople("");setEvDays("1");}} disabled={!evDesc||!evPeople} style={{padding:"12px",borderRadius:"8px",background:"rgba(251,191,36,0.15)",color:"#fbbf24",border:"1px solid rgba(251,191,36,0.35)",cursor:(!evDesc||!evPeople)?"not-allowed":"pointer",fontWeight:700,opacity:(!evDesc||!evPeople)?0.5:1}}>
                  🎉 Register Event & Recalculate Forecast
                </button>
              </div>
            )}

            {}
            {tab==="addItem" && (
              <div style={{display:"flex",flexDirection:"column",gap:"12px"}}>
                <p style={{margin:0,color:"var(--txt2)",fontSize:"0.83rem"}}>Add a single item. After saving, the app will ask for its ingredient ratios.</p>
                <div style={{display:"grid",gridTemplateColumns:"1fr auto auto",gap:"9px"}}>
                  <input value={newItem} onChange={e=>setNewItem(e.target.value)} placeholder="Item name" style={{padding:"11px",borderRadius:"8px",background:"var(--input)",color:"var(--txt)",border:"1px solid var(--bdr)",fontSize:"0.85rem",outline:"none"}}/>
                  <div style={{display:"flex",alignItems:"center",gap:"5px",background:"var(--input)",border:"1px solid var(--bdr)",borderRadius:"8px",padding:"0 10px"}}>
                    <input value={newDemand} onChange={e=>setNewDemand(e.target.value)} type="number" min="1" style={{width:"50px",background:"transparent",border:"none",color:"var(--txt)",fontSize:"0.85rem",outline:"none",textAlign:"center"}}/>
                    <span style={{color:"var(--txt3)",fontSize:"0.75rem"}}>/day</span>
                  </div>
                  <div style={{display:"flex",alignItems:"center",gap:"4px",background:"var(--input)",border:"1px solid var(--bdr)",borderRadius:"8px",padding:"0 10px"}}>
                    <span style={{color:"var(--txt3)",fontSize:"0.8rem"}}>₹</span>
                    <input value={newMargin} onChange={e=>setNewMargin(e.target.value)} type="number" min="0.10" step="0.50" style={{width:"55px",background:"transparent",border:"none",color:"var(--txt)",fontSize:"0.85rem",outline:"none",textAlign:"center"}}/>
                  </div>
                </div>
                <button onClick={async()=>{if(!newItem.trim())return;setStatus("➕ Adding…");const r=await fetch(`${API}/api/menu/${restaurantId}`,{method:"POST",headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json"},body:JSON.stringify({item:newItem.trim(),base_daily_demand:parseInt(newDemand),profit_margin_rm:parseFloat(newMargin)})});if(!r.ok){try{setStatus("❌ "+((await r.json()).detail||r.status));}catch{setStatus(`❌ Error ${r.status}`);}return;}setStatus((await r.json()).message||"✅ Added");setNewItem("");setNewDemand("50");setNewMargin("3.00");fetchDashboard(true);}} disabled={!newItem.trim()} style={{padding:"12px",borderRadius:"8px",background:"rgba(16,185,129,0.15)",color:"var(--green)",border:"1px solid rgba(16,185,129,0.35)",cursor:!newItem.trim()?"not-allowed":"pointer",fontWeight:700,opacity:!newItem.trim()?0.5:1}}>
                  ➕ Add Item & Recalculate Forecast
                </button>
              </div>
            )}

            {}
            {tab==="profit" && <ProfitTab restaurantId={restaurantId} token={token}/>}

            {}
            {tab==="store" && <StoreSettings restaurantId={restaurantId} token={token} onStatus={setStatus}/>}

            {}
            {tab==="orders" && <OrdersPanel restaurantId={restaurantId} token={token} />}

            {}
            {tab==="chains" && <ChainsPanel restaurantId={restaurantId} token={token} email={email}/>}

            {}
            {tab==="insights" && (
              <div style={{display:"flex",flexDirection:"column",gap:"20px"}}>

                {}
                <div>
                  <div style={{display:"flex",alignItems:"center",gap:"8px",marginBottom:"8px"}}>
                    <Zap size={15} color="#f59e0b"/>
                    <h3 style={{margin:0,fontSize:"0.9rem",fontWeight:700}}>Why Did Your Last Shift Underperform?</h3>
                  </div>
                  <p style={{margin:"0 0 10px",fontSize:"0.8rem",color:"var(--txt3)"}}>Causal AI breaks down the exact factors that caused a sales dip: weather, day-of-week, events, or unexplained residual.</p>
                  <div style={{display:"flex",gap:"8px",alignItems:"center",flexWrap:"wrap"}}>
                    <button id="btn-causal-yesterday" onClick={()=>runCausalAnalysis()} disabled={insightsLoading}
                      style={{padding:"10px 16px",borderRadius:"8px",background:"rgba(245,158,11,0.15)",color:"#f59e0b",border:"1px solid rgba(245,158,11,0.35)",cursor:insightsLoading?"not-allowed":"pointer",fontWeight:600,fontSize:"0.82rem",opacity:insightsLoading?0.5:1,display:"flex",alignItems:"center",gap:"6px"}}>
                      {insightsLoading?<Loader2 size={13} style={{animation:"spin 1s linear infinite"}}/>:"🔄"} {causalResult?"Regenerate":"Loading…"}
                    </button>
                    {causalResult && (
                      <button id="btn-causal-tts" onClick={()=>{
                        const txt = `Latest analysis: ${causalResult.verdict||""}.`
                          + (causalResult.recommendation ? ` Recommendation: ${causalResult.recommendation}` : "");
                        speakInsights(txt, "causal");
                      }}
                      style={{padding:"10px 14px",borderRadius:"8px",background:ttsActive==="causal"?"rgba(245,158,11,0.3)":"rgba(245,158,11,0.08)",color:"#f59e0b",border:"1px solid rgba(245,158,11,0.3)",cursor:"pointer",fontWeight:600,fontSize:"0.82rem",display:"flex",alignItems:"center",gap:"6px"}}>
                        <Volume2 size={13}/> {ttsActive==="causal"?"Stop":"Listen"}
                      </button>
                    )}
                  </div>
                  {insightsStatus && !menuEngResult && <p style={{margin:"8px 0 0",fontSize:"0.82rem",color:insightsStatus.startsWith("❌")?"#ef4444":"var(--txt2)"}}>{insightsStatus}</p>}
                  {causalResult && !causalResult.error && (
                    <div style={{marginTop:"12px",background:"var(--input)",borderRadius:"9px",padding:"14px"}}>
                      {causalResult.available === false ? (
                        <p style={{margin:"0",fontSize:"0.82rem",fontWeight:600,color:"var(--txt3)"}}>ℹ️ {causalResult.reason || "Not enough data for causal analysis yet."}</p>
                      ) : (
                        <>
                          <p style={{margin:"0 0 10px",fontSize:"0.82rem",fontWeight:600}}>📅 {causalResult.target_date} — {causalResult.verdict || causalResult.message || "Analysis complete"}</p>
                      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"8px"}}>
                        {Object.entries(causalResult.factors||{}).map(([k,v]:any)=>(
                          <div key={k} style={{padding:"9px 11px",background:"var(--card)",borderRadius:"7px"}}>
                            <p style={{margin:0,fontSize:"0.75rem",color:"var(--txt3)",textTransform:"capitalize"}}>{k.replace(/_/g," ")}</p>
                            <p style={{margin:"2px 0 0",fontSize:"0.88rem",fontWeight:700,color:typeof v==="number"&&v<0?"#ef4444":typeof v==="number"&&v>0?"var(--green)":"var(--txt)"}}>
                              {typeof v==="number"?`${v>0?"+":""}${v.toFixed(1)}%`:String(v)}
                            </p>
                          </div>
                        ))}
                      </div>
                      {causalResult.recommendation && <p style={{margin:"10px 0 0",fontSize:"0.8rem",color:"var(--txt2)",lineHeight:1.5}}>💡 {causalResult.recommendation}</p>}
                      </>
                    )}
                    </div>
                  )}
                </div>

                <hr style={{border:"none",borderTop:"1px solid var(--bdr)",margin:0}}/>

                {}
                <div>
                  <div style={{display:"flex",alignItems:"center",gap:"8px",marginBottom:"8px"}}>
                    <Brain size={15} color="var(--green)"/>
                    <h3 style={{margin:0,fontSize:"0.9rem",fontWeight:700}}>Menu Engineering — BCG Matrix</h3>
                  </div>
                  <div style={{marginBottom:"14px",fontSize:"0.8rem",color:"var(--txt2)",lineHeight:1.6}}>
                    <p style={{margin:"0 0 6px"}}>The AI categorizes your menu items into 4 types based on your sales and profit margins:</p>
                    <ul style={{margin:0,paddingLeft:"20px",color:"var(--txt3)"}}>
                      <li>⭐ <strong style={{color:"#f59e0b"}}>Star</strong>: High demand, High margin <i>(Your best items — promote heavily)</i></li>
                      <li>🐴 <strong style={{color:"#3b82f6"}}>Ploughhorse</strong>: High demand, Low margin <i>(Sells well but low profit — test slight price increase)</i></li>
                      <li>❓ <strong style={{color:"#a855f7"}}>Puzzle</strong>: Low demand, High margin <i>(Very profitable but unpopular — try bundling or renaming)</i></li>
                      <li>🐶 <strong style={{color:"#ef4444"}}>Dog</strong>: Low demand, Low margin <i>(Consider removing from menu to cut prep costs)</i></li>
                    </ul>
                  </div>
                  <div style={{display:"flex",gap:"8px",alignItems:"center",flexWrap:"wrap"}}>
                    <button id="btn-menu-engineering" onClick={runMenuEngineering} disabled={insightsLoading||restaurant.menu.length===0}
                      style={{padding:"10px 16px",borderRadius:"8px",background:"rgba(16,185,129,0.15)",color:"var(--green)",border:"1px solid rgba(16,185,129,0.35)",cursor:(insightsLoading||restaurant.menu.length===0)?"not-allowed":"pointer",fontWeight:600,fontSize:"0.82rem",opacity:(insightsLoading||restaurant.menu.length===0)?0.5:1,display:"flex",alignItems:"center",gap:"6px"}}>
                      {insightsLoading?<Loader2 size={13} style={{animation:"spin 1s linear infinite"}}/>:"🔄"} {menuEngResult?"Regenerate":"Loading…"}
                    </button>
                    {menuEngResult && (
                      <button id="btn-menu-tts" onClick={()=>{
                        const recs = menuEngResult.recommendations?.map((r:any) => r.reason || r).join(". ") || "";
                        const cats = ["stars", "ploughhorses", "puzzles", "dogs"].map(cat => {
                            const items = (menuEngResult.classification?.[cat] || []).map((e:any)=>e.item).join(", ");
                            return items ? `${cat}: ${items}` : "";
                        }).filter(Boolean).join(". ");
                        speakInsights(`Menu analysis: ${cats}. Recommendations: ${recs}`, "menu");
                      }}
                      style={{padding:"10px 14px",borderRadius:"8px",background:ttsActive==="menu"?"rgba(16,185,129,0.3)":"rgba(16,185,129,0.08)",color:"var(--green)",border:"1px solid rgba(16,185,129,0.3)",cursor:"pointer",fontWeight:600,fontSize:"0.82rem",display:"flex",alignItems:"center",gap:"6px"}}>
                        <Volume2 size={13}/> {ttsActive==="menu"?"Stop":"Listen"}
                      </button>
                    )}
                  </div>
                  {restaurant.menu.length===0 && <p style={{margin:"8px 0 0",fontSize:"0.78rem",color:"var(--txt3)"}}>Add menu items first to use this feature.</p>}
                  {menuEngResult && (
                    <div style={{marginTop:"12px"}}>
                      {menuEngResult.data_days<14 && <p style={{margin:"0 0 10px",fontSize:"0.78rem",color:"#f59e0b"}}>⚠️ {menuEngResult.note}</p>}
                      <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(200px,1fr))",gap:"8px"}}>
                        {["stars", "ploughhorses", "puzzles", "dogs"].flatMap(cat => menuEngResult.classification?.[cat] || []).length === 0 ? (
                          <div style={{gridColumn:"1/-1",padding:"16px",background:"var(--input)",borderRadius:"8px",textAlign:"center",color:"var(--txt3)"}}>
                            <p style={{margin:0,fontSize:"0.85rem"}}>Your menu analysis is empty because you haven't logged any sales data yet.</p>
                            <p style={{margin:"4px 0 0",fontSize:"0.78rem"}}>Go to the Upload tab and log some daily sales to see your AI classification!</p>
                          </div>
                        ) : (
                          ["stars", "ploughhorses", "puzzles", "dogs"].flatMap(cat => {
                            const items = (menuEngResult.classification?.[cat] || []) as any[];
                            return items.map((entry: any) => {
                              const emoji = cat==="stars"?"⭐":cat==="ploughhorses"?"🐴":cat==="puzzles"?"❓":"🐶";
                              const col   = cat==="stars"?"#f59e0b":cat==="ploughhorses"?"#3b82f6":cat==="puzzles"?"#a855f7":"#ef4444";
                              const label = cat==="stars"?"Star":cat==="ploughhorses"?"Ploughhorse":cat==="puzzles"?"Puzzle":"Dog";
                              return (
                                <div key={entry.item} style={{padding:"10px 12px",background:"var(--input)",borderRadius:"8px",borderLeft:`3px solid ${col}`}}>
                                  <p style={{margin:0,fontSize:"0.82rem",fontWeight:600}}>{emoji} {entry.item}</p>
                                  <p style={{margin:"2px 0 0",fontSize:"0.74rem",color:col,textTransform:"capitalize"}}>{label}</p>
                                </div>
                              );
                            });
                          })
                        )}
                      </div>
                      {menuEngResult.recommendations?.length>0 && (
                        <div style={{marginTop:"12px",background:"var(--input)",borderRadius:"9px",padding:"12px"}}>
                          <p style={{margin:"0 0 8px",fontSize:"0.82rem",fontWeight:600}}>💡 AI Recommendations</p>
                          {menuEngResult.recommendations.map((r:any,i:number)=>(
                            <p key={i} style={{margin:"0 0 5px",fontSize:"0.8rem",color:"var(--txt2)",lineHeight:1.5}}>• {r.reason || r}</p>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )}

            {}
            {tab==="scan" && (
              <div style={{display:"flex",flexDirection:"column",gap:"14px"}}>
                <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                  <Camera size={16} color="#3b82f6"/>
                  <h3 style={{margin:0,fontSize:"0.9rem",fontWeight:700}}>Computer Vision Inventory Scan</h3>
                </div>
                <p style={{margin:0,fontSize:"0.8rem",color:"var(--txt3)",lineHeight:1.6}}>
                  Take a photo of your ingredient shelf or storage area. The AI will detect items and quantities, then cross-reference against your menu's Bill of Materials.
                </p>
                <label id="scan-upload-label" style={{display:"flex",flexDirection:"column",alignItems:"center",justifyContent:"center",minHeight:"120px",border:`2px dashed ${scanFile?"var(--green)":"var(--bdr)"}`,borderRadius:"12px",cursor:"pointer",background:scanFile?"rgba(16,185,129,0.05)":"var(--input)",color:scanFile?"var(--green)":"var(--txt3)",gap:"8px",transition:"all 0.2s"}}>
                  <input type="file" accept="image/jpeg,image/png,image/webp" style={{display:"none"}} onChange={e=>{const f=e.target.files?.[0]||null;setScanFile(f);setScanResult(null);e.target.value="";}}/>
                  <Camera size={28} style={{opacity:0.6}}/>
                  <span style={{fontSize:"0.85rem",fontWeight:500}}>{scanFile?scanFile.name:"Tap to upload shelf photo"}</span>
                  <span style={{fontSize:"0.75rem",opacity:0.7}}>JPEG, PNG or WebP — max 10MB</span>
                </label>
                {scanFile && (
                  <button id="btn-cv-scan" onClick={runCVScan} disabled={scanLoading}
                    style={{padding:"12px",borderRadius:"9px",background:"rgba(59,130,246,0.15)",color:"#3b82f6",border:"1px solid rgba(59,130,246,0.35)",cursor:scanLoading?"not-allowed":"pointer",fontWeight:700,fontSize:"0.85rem",display:"flex",alignItems:"center",justifyContent:"center",gap:"8px"}}>
                    {scanLoading?<Loader2 size={14} style={{animation:"spin 1s linear infinite"}}/>:<Camera size={14}/>}
                    {scanLoading?"Scanning…":"Scan Inventory"}
                  </button>
                )}
                {scanResult && !scanResult.error && (
                  <div style={{background:"var(--input)",borderRadius:"10px",padding:"14px"}}>
                    <p style={{margin:"0 0 10px",fontSize:"0.85rem",fontWeight:600}}>📦 Detected Ingredients</p>
                    {Object.keys(scanResult.detected||{}).length===0
                      ? <p style={{margin:0,fontSize:"0.82rem",color:"var(--txt3)"}}>No ingredients detected. Try a clearer, better-lit photo.</p>
                      : <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(150px,1fr))",gap:"7px"}}>
                          {Object.entries(scanResult.detected||{}).map(([ingredientName, details]: any, i: number)=>(
                            <div key={i} style={{padding:"9px 12px",background:"var(--card)",borderRadius:"8px"}}>
                              <p style={{margin:0,fontSize:"0.82rem",fontWeight:600}}>{ingredientName}</p>
                              {details.qty&&<p style={{margin:"2px 0 0",fontSize:"0.76rem",color:"var(--green)"}}>{details.qty} {details.unit||""}</p>}
                            </div>
                          ))}
                        </div>
                    }
                    {scanResult.summary&&<p style={{margin:"10px 0 0",fontSize:"0.8rem",color:"var(--txt2)",lineHeight:1.5}}>💡 {scanResult.summary}</p>}
                  </div>
                )}
                {scanResult?.error && <p style={{margin:0,fontSize:"0.82rem",color:"#ef4444"}}>❌ {scanResult.error}</p>}
              </div>
            )}
          </div>
        </div>
      </div>

      {}
      <VoicePanel
        onTranscript={text => {
          setRawInput(text);
          setTab("upload");
        }}
        speakText={speakText}
        onSpeakDone={() => setSpeakText(undefined)}
      />

      <style>{`
        @keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
        :root{--bg:#0a0d12;--card:#0f1319;--input:rgba(255,255,255,0.04);--bdr:rgba(255,255,255,0.08);--txt:#e4e4e7;--txt2:#a1a1aa;--txt3:#71717a;--green:#10b981;--font:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',sans-serif}
        *,*::before,*::after{box-sizing:border-box}body{margin:0;background:var(--bg)}
        ::-webkit-scrollbar{width:5px;height:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--bdr);border-radius:3px}
      `}</style>
    </div>
  );
}
