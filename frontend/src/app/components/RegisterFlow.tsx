"use client";
import React, { useState, useRef, useEffect } from "react";
import { Loader2 } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
type RegStep = "email"|"name"|"owner"|"type"|"region"|"telegram"|"closing_time"|"waiting_bot"|"done";

const inp = { style:{ width:"100%",padding:"12px",borderRadius:"9px",background:"var(--input)",color:"var(--txt)",border:"1px solid var(--bdr)",fontSize:"0.9rem",outline:"none",boxSizing:"border-box" as const }};
const TYPES = ["🍛 Street Stall / Thela","🍵 Dhaba / Eatery","☕ Cafe / Tea Stall","🍽️ Restaurant","🍦 Dessert / Mithai Shop","🍡 Other"];
const CLOSING_TIMES = ["18:00","19:00","20:00","21:00","22:00","23:00"];

export default function RegisterFlow({ onRegister }: { onRegister: (token:string, restId:string, email:string) => void }) {
  const [step, setStep] = useState<RegStep>("email");
  const fields = useRef({ email:"",name:"",owner:"",type:"",region:"",tgUsername:"",closingTime:"21:00" });
  const [val, setVal]         = useState("");
  const [err, setErr]         = useState("");
  const [loading, setLoading] = useState(false);
  const pollRef   = useRef<NodeJS.Timeout|null>(null);
  const [botName, setBotName] = useState("WasteWise_bot");

  useEffect(() => {
    fetch(`${API}/api/bot_info`).then(r=>r.ok?r.json():null).then(d=>{if(d?.bot_username)setBotName(d.bot_username);}).catch(()=>null);
  }, []);

  useEffect(() => {
    if (step !== "waiting_bot") { if (pollRef.current) clearInterval(pollRef.current); return; }
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/auth/check_pending?email=${encodeURIComponent(fields.current.email)}`);
        if (r.ok) { const d = await r.json(); if (d.status==="completed") { clearInterval(pollRef.current!); onRegister(d.token,d.restaurant_id,d.email); } }
      } catch {}
    }, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [step]);

  const STEPS: RegStep[] = ["email","name","owner","type","region","telegram","closing_time"];
  const stepNum = STEPS.indexOf(step) + 1;

  const next = async () => {
    setErr(""); setLoading(true);
    try {
      if (step==="email") {
        if (!val.includes("@")) { setErr("Enter a valid email."); return; }
        const r = await fetch(`${API}/api/auth/request_otp?email=${encodeURIComponent(val)}`,{method:"POST"});
        if (r.ok) { setErr("That email already has an account. Please log in instead."); return; }
        fields.current.email = val; setVal(""); setStep("name");
      } else if (step==="name") {
        if (!val.trim()) { setErr("Restaurant name required."); return; }
        fields.current.name = val.trim(); setVal(""); setStep("owner");
      } else if (step==="owner") {
        if (!val.trim()) { setErr("Your name is required."); return; }
        fields.current.owner = val.trim(); setVal(""); setStep("type");
      } else if (step==="type") {
        if (!val) { setErr("Please select a type."); return; }
        fields.current.type = val.replace(/^[^ ]+ /,"").split(" /")[0].toLowerCase().replace(/[^a-z]/g,"");
        setVal(""); setStep("region");
      } else if (step==="region") {
        if (!val.trim()) { setErr("Area/city required."); return; }
        fields.current.region = val.trim(); setVal(""); setStep("telegram");
      } else if (step==="telegram") {
        const tg = val.trim().replace(/^@/,"");
        if (!tg) { setErr("Enter your Telegram username."); return; }
        fields.current.tgUsername = tg; setVal(""); setStep("closing_time");
      } else if (step==="closing_time") {
        const ct = val || "21:00";
        fields.current.closingTime = ct;
        const f = fields.current;
        const reg = await fetch(`${API}/api/auth/register?` + new URLSearchParams({
          email:f.email,name:f.name,owner_name:f.owner,region:f.region,
          restaurant_type:f.type,telegram_username:f.tgUsername,closing_time:ct,
        }), { method:"POST" });
        if (!reg.ok) {
          try { const d = await reg.json(); setErr(d.detail || "Registration failed."); }
          catch { setErr(`Server error ${reg.status}. Please try again later.`); }
          return;
        }
        const regData = await reg.json();
        if (regData.status==="registered"&&regData.token) { onRegister(regData.token,regData.restaurant_id,regData.email); return; }
        if (regData.status==="pending_telegram") { setStep("waiting_bot"); return; }
        setErr(regData.detail || "Unexpected error.");
      }
    } catch { setErr("Network error."); }
    finally { setLoading(false); }
  };

  if (step==="waiting_bot") return (
    <div style={{padding:"4px 0"}}>
      <div style={{display:"flex",alignItems:"center",gap:"10px",marginBottom:"14px"}}>
        <Loader2 size={22} style={{animation:"spin 1s linear infinite",color:"var(--green)",flexShrink:0}}/>
        <h3 style={{margin:0,fontSize:"1rem",fontWeight:700}}>One more step — link your Telegram</h3>
      </div>
      <p style={{margin:"0 0 14px",fontSize:"0.83rem",color:"var(--txt2)",lineHeight:"1.5"}}>
        Your restaurant has been saved. Send any message to the WasteWise AI bot to activate your account.
      </p>
      <div style={{background:"rgba(0,136,204,0.1)",border:"1px solid rgba(0,136,204,0.35)",borderRadius:"9px",padding:"11px 14px",marginBottom:"14px",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
        <div>
          <p style={{margin:"0 0 2px",fontSize:"0.73rem",color:"var(--txt3)"}}>Search in Telegram:</p>
          <p style={{margin:0,fontSize:"1rem",fontWeight:700,color:"#38bdf8"}}>@{botName}</p>
        </div>
        <button onClick={()=>navigator.clipboard?.writeText(`@${botName}`).catch(()=>null)} style={{background:"transparent",border:"1px solid rgba(0,136,204,0.4)",color:"#38bdf8",padding:"5px 10px",borderRadius:"6px",cursor:"pointer",fontSize:"0.75rem"}}>Copy</button>
      </div>
      <a href={`https://t.me/${botName}`} target="_blank" rel="noopener noreferrer" style={{display:"flex",alignItems:"center",justifyContent:"center",gap:"8px",width:"100%",padding:"13px",background:"#0088cc",color:"#fff",borderRadius:"9px",fontWeight:700,fontSize:"0.9rem",textDecoration:"none",boxSizing:"border-box",marginBottom:"10px"}}>
        ✈️ Open @{botName} in Telegram
      </a>
      <p style={{textAlign:"center",margin:0,fontSize:"0.73rem",color:"var(--txt3)"}}>Waiting for confirmation… (checks every 3 seconds)</p>
    </div>
  );

  const prompts: Record<RegStep,string> = {
    email:"Enter your email address",name:"Your restaurant or stall name?",owner:"Your name (first name or nickname)",
    type:"What type of business?",region:"Which area or city?",telegram:"Your Telegram username (without @)",
    closing_time:"What time do you close? (e.g. 21:00)",waiting_bot:"",done:"",
  };

  return (
    <>
      <h2 style={{margin:"0 0 6px",fontSize:"1.1rem",fontWeight:700}}>📝 Create Account</h2>
      <p style={{margin:"0 0 14px",fontSize:"0.82rem",color:"var(--txt3)"}}>Step {stepNum} of {STEPS.length}</p>
      <p style={{margin:"0 0 12px",fontSize:"0.88rem",color:"var(--txt2)"}}>{prompts[step]}</p>
      {err && <p style={{color:"#f87171",fontSize:"0.82rem",marginBottom:"10px"}}>{err}</p>}
      {step==="type" ? (
        <div style={{display:"flex",flexDirection:"column",gap:"8px"}}>
          {TYPES.map(t=>(
            <button key={t} onClick={()=>setVal(t)} style={{padding:"11px",borderRadius:"9px",border:`2px solid ${val===t?"var(--green)":"var(--bdr)"}`,background:val===t?"rgba(16,185,129,0.15)":"var(--input)",color:val===t?"var(--green)":"var(--txt)",cursor:"pointer",textAlign:"left",fontSize:"0.88rem"}}>{t}</button>
          ))}
        </div>
      ) : step==="closing_time" ? (
        <div style={{display:"flex",flexDirection:"column",gap:"8px"}}>
          <p style={{margin:"0 0 8px",fontSize:"0.8rem",color:"var(--txt3)"}}>At closing time, I'll automatically send your inventory report to Telegram and post discounted items to the customer marketplace!</p>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr 1fr",gap:"8px",marginBottom:"8px"}}>
            {CLOSING_TIMES.map(t=>(
              <button key={t} onClick={()=>setVal(t)} style={{padding:"10px 6px",borderRadius:"9px",border:`2px solid ${val===t?"var(--green)":"var(--bdr)"}`,background:val===t?"rgba(16,185,129,0.15)":"var(--input)",color:val===t?"var(--green)":"var(--txt)",cursor:"pointer",fontSize:"0.85rem",fontWeight:val===t?700:400}}>{t}</button>
            ))}
          </div>
          <input {...inp} type="time" placeholder="21:00" value={val||"21:00"} onChange={e=>setVal(e.target.value)}/>
        </div>
      ) : (
        <input {...inp} type={step==="email"?"email":"text"} placeholder={step==="telegram"?"@username":step==="region"?"e.g. Lajpat Nagar, Delhi NCR":""} value={val} onChange={e=>setVal(e.target.value)} onKeyDown={e=>e.key==="Enter"&&next()} autoFocus/>
      )}
      <button onClick={next} disabled={loading||(step==="type"&&!val)||(step==="closing_time"&&!val)} style={{marginTop:"14px",width:"100%",padding:"12px",background:"var(--green)",color:"#fff",border:"none",borderRadius:"9px",fontWeight:700,cursor:(loading||(step==="type"&&!val))?"not-allowed":"pointer",opacity:(loading||(step==="type"&&!val))?0.6:1}}>
        {loading?"Please wait…":"Continue"}
      </button>
    </>
  );
}
