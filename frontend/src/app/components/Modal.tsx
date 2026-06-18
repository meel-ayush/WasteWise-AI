"use client";
import React from "react";
import { X } from "lucide-react";

export default function Modal({ children, onClose }: { children: React.ReactNode; onClose?: () => void }) {
  return (
    <div style={{ position:"fixed",inset:0,background:"rgba(0,0,0,0.75)",display:"flex",alignItems:"center",justifyContent:"center",zIndex:1000,padding:"20px" }}>
      <div style={{ background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"16px",padding:"28px",maxWidth:"460px",width:"100%",position:"relative",boxShadow:"0 20px 60px rgba(0,0,0,0.5)" }}>
        {onClose && <button onClick={onClose} style={{ position:"absolute",top:"14px",right:"14px",background:"transparent",border:"none",color:"var(--txt2)",cursor:"pointer",padding:"4px" }}><X size={18}/></button>}
        {children}
      </div>
    </div>
  );
}
