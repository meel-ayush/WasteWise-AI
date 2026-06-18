"use client";
import React, { useState, useRef } from "react";
import { Loader2 } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
type AuthStep = "idle" | "email" | "otp" | "done";

const inp = { style:{ width:"100%",padding:"12px",borderRadius:"9px",background:"var(--input)",color:"var(--txt)",border:"1px solid var(--bdr)",fontSize:"0.9rem",outline:"none",boxSizing:"border-box" as const }};

export default function LoginFlow({ onLogin }: { onLogin: (token:string, restId:string, email:string) => void }) {
  const [step, setStep]       = useState<AuthStep>("email");
  const [email, setEmail]     = useState("");
  const [otp, setOtp]         = useState("");
  const [err, setErr]         = useState("");
  const [loading, setLoading] = useState(false);
  const [countdown, setCountdown] = useState(0);
  const timerRef = useRef<NodeJS.Timeout|null>(null);

  const startCountdown = (secs: number) => {
    setCountdown(secs);
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = setInterval(() => setCountdown(c => { if (c<=1){clearInterval(timerRef.current!);return 0;} return c-1; }), 1000);
  };

  const requestOtp = async () => {
    if (!email.includes("@")) { setErr("Enter a valid email address."); return; }
    setLoading(true); setErr("");
    try {
      const r = await fetch(`${API}/api/auth/request_otp?email=${encodeURIComponent(email)}`, { method:"POST" });
      if (!r.ok) {
        try { const d = await r.json(); setErr(d.detail || "Email not found."); }
        catch { setErr(`Server error ${r.status}.`); }
        return;
      }
      const d = await r.json();
      setStep("otp"); startCountdown(d.expires_in || 90);
    } catch { setErr("Network error. Is the backend running?"); }
    finally { setLoading(false); }
  };

  const verifyOtp = async () => {
    if (otp.length !== 6) { setErr("OTP must be 6 digits."); return; }
    setLoading(true); setErr("");
    try {
      const r = await fetch(`${API}/api/auth/verify_otp?email=${encodeURIComponent(email)}&otp=${otp}`, { method:"POST" });
      if (!r.ok) {
        try { const d = await r.json(); setErr(d.detail || "Invalid OTP."); }
        catch { setErr(`Server error ${r.status}.`); }
        return;
      }
      const d = await r.json();
      onLogin(d.token, d.restaurant_id, d.email);
    } catch { setErr("Network error."); }
    finally { setLoading(false); }
  };

  return (
    <>
      <h2 style={{ margin:"0 0 6px",fontSize:"1.1rem",fontWeight:700 }}>🔑 Login to WasteWise AI</h2>
      {step === "email" && (
        <>
          <p style={{ margin:"0 0 18px",fontSize:"0.85rem",color:"var(--txt2)" }}>Enter your registered email. An OTP will be sent to your Telegram.</p>
          {err && <p style={{ color:"#f87171",fontSize:"0.82rem",marginBottom:"10px" }}>{err}</p>}
          <input {...inp} type="email" placeholder="your@email.com" value={email} onChange={e=>setEmail(e.target.value)} onKeyDown={e=>e.key==="Enter"&&requestOtp()} autoFocus/>
          <button onClick={requestOtp} disabled={loading} style={{ marginTop:"12px",width:"100%",padding:"12px",background:"var(--green)",color:"#fff",border:"none",borderRadius:"9px",fontWeight:700,cursor:loading?"not-allowed":"pointer",opacity:loading?0.6:1 }}>
            {loading ? "Sending…" : "Send OTP to Telegram"}
          </button>
        </>
      )}
      {step === "otp" && (
        <>
          <p style={{ margin:"0 0 4px",fontSize:"0.85rem",color:"var(--txt2)" }}>Check your Telegram bot for a 6-digit code.</p>
          {countdown > 0 && <p style={{ margin:"0 0 14px",fontSize:"0.8rem",color:countdown<=10?"#f87171":"var(--txt3)" }}>Expires in {countdown}s</p>}
          {err && <p style={{ color:"#f87171",fontSize:"0.82rem",marginBottom:"10px" }}>{err}</p>}
          <input {...inp} type="text" placeholder="123456" maxLength={6} value={otp} onChange={e=>setOtp(e.target.value.replace(/\D/g,""))} onKeyDown={e=>e.key==="Enter"&&verifyOtp()} autoFocus/>
          <button onClick={verifyOtp} disabled={loading||otp.length!==6} style={{ marginTop:"12px",width:"100%",padding:"12px",background:"var(--green)",color:"#fff",border:"none",borderRadius:"9px",fontWeight:700,cursor:(loading||otp.length!==6)?"not-allowed":"pointer",opacity:(loading||otp.length!==6)?0.6:1 }}>
            {loading ? "Verifying…" : "Confirm OTP"}
          </button>
          <button onClick={()=>{setStep("email");setOtp("");setErr("");}} style={{ marginTop:"8px",width:"100%",padding:"10px",background:"transparent",border:"1px solid var(--bdr)",color:"var(--txt2)",borderRadius:"9px",cursor:"pointer",fontSize:"0.85rem" }}>
            Use a different email
          </button>
        </>
      )}
    </>
  );
}
