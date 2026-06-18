"use client";
import React, { useState } from "react";
import Modal from "./Modal";
import { Upload } from "lucide-react";

type FileIntent = "none"|"sales"|"append"|"overwrite";

export default function FileIntentModal({ filename, onConfirm, onCancel, issue }: {
  filename:string; onConfirm:(i:FileIntent)=>void; onCancel:()=>void; issue?:string;
}) {
  const [selected, setSelected] = useState<FileIntent>("none");
  const opts = [
    {val:"sales" as FileIntent,label:"📊 Log Sales Data",desc:"Today's quantities sold or daily totals",color:"#3b82f6"},
    {val:"append" as FileIntent,label:"➕ Add to Menu",desc:"New dishes to add alongside existing ones",color:"#f59e0b"},
    {val:"overwrite" as FileIntent,label:"🔄 Replace Menu",desc:"Completely replace the menu with this file",color:"#10b981"},
  ];
  return (
    <Modal onClose={onCancel}>
      <h2 style={{margin:"0 0 6px",fontSize:"1.05rem",fontWeight:700}}>What is this file?</h2>
      <p style={{margin:"0 0 16px",color:"var(--txt2)",fontSize:"0.85rem"}}><b>{filename}</b></p>
      {issue && <div style={{background:"rgba(239,68,68,0.1)",border:"1px solid rgba(239,68,68,0.3)",borderRadius:"8px",padding:"10px",marginBottom:"14px",fontSize:"0.82rem",color:"#f87171"}}>⚠️ {issue}</div>}
      <div style={{display:"flex",flexDirection:"column",gap:"9px",marginBottom:"18px"}}>
        {opts.map(o=>(
          <button key={o.val} onClick={()=>setSelected(o.val)} style={{padding:"12px 14px",borderRadius:"9px",border:`2px solid ${selected===o.val?o.color:"var(--bdr)"}`,background:selected===o.val?`${o.color}18`:"var(--input)",cursor:"pointer",textAlign:"left"}}>
            <p style={{margin:"0 0 2px",fontWeight:600,color:selected===o.val?o.color:"var(--txt)",fontSize:"0.88rem"}}>{o.label}</p>
            <p style={{margin:0,fontSize:"0.76rem",color:"var(--txt3)"}}>{o.desc}</p>
          </button>
        ))}
      </div>
      <div style={{display:"flex",gap:"9px"}}>
        <button onClick={onCancel} style={{flex:1,padding:"10px",borderRadius:"8px",background:"transparent",border:"1px solid var(--bdr)",color:"var(--txt2)",cursor:"pointer",fontSize:"0.87rem"}}>Cancel</button>
        <button onClick={()=>selected!=="none"&&onConfirm(selected)} disabled={selected==="none"} style={{flex:2,padding:"10px",borderRadius:"8px",background:selected!=="none"?"var(--green)":"var(--input)",color:selected!=="none"?"#fff":"var(--txt3)",border:"none",cursor:selected!=="none"?"pointer":"not-allowed",fontWeight:700,fontSize:"0.88rem",opacity:selected!=="none"?1:0.6}}>
          Confirm & Process
        </button>
      </div>
    </Modal>
  );
}
