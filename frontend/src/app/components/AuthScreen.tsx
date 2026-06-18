"use client";
import React, { useState } from "react";
import LoginFlow from "./LoginFlow";
import RegisterFlow from "./RegisterFlow";

export default function AuthScreen({ onAuth }: { onAuth: (token:string, restId:string, email:string) => void }) {
  const [mode, setMode] = useState<"login"|"register">("login");
  return (
    <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center",background:"var(--bg)"}}>
      <div style={{width:"100%",maxWidth:"460px",background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"20px",padding:"38px",boxShadow:"0 8px 60px rgba(0,0,0,0.45)"}}>
        <div style={{textAlign:"center",marginBottom:"28px"}}>
          <div style={{fontSize:"3rem",marginBottom:"6px"}}>🌿</div>
          <h1 style={{fontSize:"1.8rem",fontWeight:800,margin:0}}>WasteWise <span style={{color:"var(--green)"}}>AI</span></h1>
          <p style={{color:"var(--txt2)",fontSize:"0.88rem",marginTop:"6px"}}>Reducing food waste for indian SMEs</p>
        </div>
        <div style={{display:"flex",gap:"8px",marginBottom:"26px",background:"var(--input)",borderRadius:"10px",padding:"4px"}}>
          {(["login","register"] as const).map(m=>(
            <button key={m} onClick={()=>setMode(m)} style={{flex:1,padding:"10px",borderRadius:"8px",border:"none",background:mode===m?"var(--green)":"transparent",color:mode===m?"#fff":"var(--txt2)",fontWeight:mode===m?700:400,cursor:"pointer",fontSize:"0.9rem",transition:"all 0.2s"}}>
              {m==="login"?"Sign In":"Register"}
            </button>
          ))}
        </div>
        {mode==="login" ? <LoginFlow onLogin={onAuth}/> : <RegisterFlow onRegister={onAuth}/>}
        <p style={{textAlign:"center",color:"var(--txt3)",fontSize:"0.75rem",marginTop:"22px"}}>
          🔒 Login is passwordless — verified via your Telegram bot
        </p>
      </div>
    </div>
  );
}
