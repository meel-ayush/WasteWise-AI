"use client";
import React, { useState, useEffect, useRef } from "react";
import { Store, Clock, Tag, RefreshCw, Brain, Camera, Eye, EyeOff, X, Upload } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface StoreSettingsProps { restaurantId:string; token:string; onStatus:(msg:string)=>void; }
interface ListingItem {
  item:string; listed:boolean; price_rm:number; menu_price_rm:number;
  discount_pct:number|null; effective_discount_pct:number;
  photo_b64:string|null; ai_last_action:string|null; ai_last_action_at:string|null;
  forecasted:number; sold:number; remaining:number;
}

export default function StoreSettings({ restaurantId, token, onStatus }: StoreSettingsProps) {
  const [closingTime, setClosingTime] = useState("21:00");
  const [discountPct, setDiscountPct] = useState(30);
  const [mktEnabled, setMktEnabled]   = useState(true);
  const [listings, setListings]       = useState<ListingItem[]>([]);
  const [loading, setLoading]         = useState(false);
  const [saving, setSaving]           = useState(false);
  const [aiLoading, setAiLoading]     = useState(false);
  const [aiResult, setAiResult]       = useState<any>(null);
  const [selected, setSelected]       = useState<ListingItem|null>(null);
  
  const [editPrice, setEditPrice]     = useState("");
  const [editDisc, setEditDisc]       = useState<number|null>(null);
  const [useItemDisc, setUseItemDisc] = useState(false);
  const [uploading, setUploading]     = useState(false);
  const photoRef = useRef<HTMLInputElement>(null);

  useEffect(() => { loadAll(); }, [restaurantId]);

  const loadAll = async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/restaurant/${restaurantId}/marketplace_listings`,
        { headers:{"Authorization":`Bearer ${token}`} });
      if (r.ok) {
        try {
          const d = await r.json();
          setClosingTime(d.closing_time || "21:00");
          setDiscountPct(d.global_discount_pct ?? 30);
          setMktEnabled(d.marketplace_enabled ?? true);
          setListings(d.listings || []);
        } catch {  }
      }
    } catch {}
    setLoading(false);
  };

  const saveSettings = async () => {
    setSaving(true);
    try {
      const r = await fetch(`${API}/api/restaurant/${restaurantId}/closing_time`, {
        method:"POST", headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json"},
        body:JSON.stringify({ closing_time:closingTime, discount_pct:discountPct, marketplace_enabled:mktEnabled }),
      });
      onStatus(r.ok ? "✅ Settings saved!" : "❌ Save failed.");
    } catch { onStatus("❌ Network error."); }
    setSaving(false);
  };

  const toggleListed = async (item: ListingItem) => {
    const newVal = !item.listed;
    setListings(prev => prev.map(l => l.item===item.item ? {...l, listed:newVal} : l));
    await fetch(`${API}/api/restaurant/${restaurantId}/marketplace_listings/${encodeURIComponent(item.item)}`, {
      method:"PATCH", headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json"},
      body:JSON.stringify({ listed:newVal }),
    });
  };

  const openEditor = (item: ListingItem) => {
    setSelected(item);
    setEditPrice(String(item.price_rm));
    setUseItemDisc(item.discount_pct !== null);
    setEditDisc(item.discount_pct ?? discountPct);
  };

  const saveItemSettings = async () => {
    if (!selected) return;
    const body: any = { price_rm: parseFloat(editPrice) || selected.menu_price_rm };
    if (useItemDisc) body.discount_pct = editDisc;
    else             body.discount_pct = null;
    await fetch(`${API}/api/restaurant/${restaurantId}/marketplace_listings/${encodeURIComponent(selected.item)}`, {
      method:"PATCH", headers:{"Authorization":`Bearer ${token}`,"Content-Type":"application/json"},
      body:JSON.stringify(body),
    });
    onStatus(`✅ ${selected.item} settings saved.`);
    setSelected(null);
    loadAll();
  };

  const uploadPhoto = async (file: File) => {
    if (!selected) return;
    setUploading(true);
    const fd = new FormData(); fd.append("file", file);
    const r = await fetch(`${API}/api/restaurant/${restaurantId}/marketplace_listings/${encodeURIComponent(selected.item)}/photo`, {
      method:"POST", headers:{"Authorization":`Bearer ${token}`}, body:fd,
    });
    if (r.ok) { onStatus("✅ Photo uploaded."); loadAll(); }
    else       onStatus("❌ Photo upload failed.");
    setUploading(false);
  };

  const runAI = async () => {
    setAiLoading(true); setAiResult(null);
    try {
      const r = await fetch(`${API}/api/restaurant/${restaurantId}/ai_discount_optimize`, {
        method:"POST", headers:{"Authorization":`Bearer ${token}`},
      });
      if (r.ok) {
        try {
          const d = await r.json();
          setAiResult(d);
          if (d.global_change) setDiscountPct(d.global_change);
          onStatus(`🤖 AI updated ${d.changes_made} item(s).`);
          loadAll();
        } catch { onStatus("❌ AI response error."); }
      } else { try { const e = await r.json(); onStatus("❌ " + (e.detail || "AI optimization failed.")); } catch { onStatus(`❌ Server error ${r.status}.`); } }
    } catch { onStatus("❌ Network error."); }
    setAiLoading(false);
  };

  if (loading) return <div style={{padding:"30px",textAlign:"center",color:"var(--txt3)"}}>Loading marketplace settings…</div>;

  return (
    <div style={{display:"flex",flexDirection:"column",gap:"18px"}}>

      {}
      <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"20px"}}>
        <div style={{display:"flex",alignItems:"center",gap:"8px",marginBottom:"16px"}}>
          <Store size={18} color="var(--green)"/><h2 style={{margin:0,fontSize:"0.95rem",fontWeight:600}}>Store & Marketplace Settings</h2>
        </div>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:"14px",marginBottom:"14px"}}>
          <div>
            <label style={{display:"block",fontSize:"0.8rem",color:"var(--txt2)",marginBottom:"6px"}}>
              <Clock size={12} style={{marginRight:"5px",verticalAlign:"middle"}}/>Closing Time
            </label>
            <input type="time" value={closingTime} onChange={e=>setClosingTime(e.target.value)}
              style={{width:"100%",padding:"10px 12px",borderRadius:"8px",background:"var(--input)",color:"var(--txt)",border:"1px solid var(--bdr)",fontSize:"0.95rem",outline:"none",boxSizing:"border-box"}}/>
          </div>
          <div>
            <label style={{display:"block",fontSize:"0.8rem",color:"var(--txt2)",marginBottom:"6px"}}>
              <Tag size={12} style={{marginRight:"5px",verticalAlign:"middle"}}/>Global Closing Discount (%)
            </label>
            <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
              <input type="range" min={0} max={70} step={5} value={discountPct} onChange={e=>setDiscountPct(Number(e.target.value))}
                style={{flex:1,accentColor:"var(--green)"}}/>
              <span style={{minWidth:"40px",fontWeight:700,color:"var(--green)",fontSize:"1rem",textAlign:"right"}}>{discountPct}%</span>
            </div>
            <p style={{margin:"4px 0 0",fontSize:"0.71rem",color:"var(--txt3)"}}>
              Applies to items <b>without</b> an item-specific discount. If an item has its own discount set, that overrides this slider.
            </p>
          </div>
        </div>
        <div style={{display:"flex",alignItems:"center",gap:"10px",padding:"11px",background:"var(--input)",borderRadius:"9px",marginBottom:"14px"}}>
          <input type="checkbox" checked={mktEnabled} onChange={e=>setMktEnabled(e.target.checked)}
            style={{width:"17px",height:"17px",accentColor:"var(--green)",cursor:"pointer"}}/>
          <div>
            <p style={{margin:0,fontWeight:600,fontSize:"0.87rem"}}>Enable Customer Marketplace</p>
            <p style={{margin:0,fontSize:"0.74rem",color:"var(--txt3)"}}>Customers can browse and order listed items from the public WasteWise Market page</p>
          </div>
        </div>
        <button onClick={saveSettings} disabled={saving}
          style={{width:"100%",padding:"11px",borderRadius:"9px",background:"var(--green)",color:"#fff",border:"none",fontWeight:700,cursor:saving?"not-allowed":"pointer",opacity:saving?0.7:1,fontSize:"0.88rem"}}>
          {saving ? "Saving…" : "💾 Save Settings"}
        </button>
      </div>

      {}
      <div style={{background:"var(--card)",border:"1px solid rgba(139,92,246,0.3)",borderRadius:"12px",padding:"18px"}}>
        <div style={{display:"flex",alignItems:"center",gap:"8px",marginBottom:"8px"}}>
          <Brain size={17} color="#a78bfa"/><h3 style={{margin:0,fontSize:"0.9rem",fontWeight:600}}>AI Discount Optimizer</h3>
        </div>
        <p style={{margin:"0 0 10px",fontSize:"0.8rem",color:"var(--txt3)",lineHeight:1.5}}>
          AI analyses today's remaining inventory and sets smart per-item discounts — boosting surplus items, removing discounts from scarce ones, and raising the global rate if most items need it.
        </p>
        <button onClick={runAI} disabled={aiLoading}
          style={{padding:"10px 16px",borderRadius:"8px",background:"rgba(139,92,246,0.15)",color:"#a78bfa",border:"1px solid rgba(139,92,246,0.35)",cursor:aiLoading?"not-allowed":"pointer",fontWeight:600,fontSize:"0.82rem",opacity:aiLoading?0.6:1}}>
          {aiLoading ? "Optimizing…" : "🤖 Optimize Discounts Now"}
        </button>
        {aiResult && (
          <div style={{marginTop:"12px",background:"var(--input)",borderRadius:"8px",padding:"12px"}}>
            <p style={{margin:"0 0 7px",fontSize:"0.82rem",fontWeight:600}}>
              {aiResult.changes_made === 0 ? "✅ No changes needed — discounts are already optimal." : `✅ Updated ${aiResult.changes_made} item(s)${aiResult.global_change ? ` · Global → ${aiResult.global_change}%` : ""}`}
            </p>
            {Object.entries(aiResult.actions||{}).map(([item, a]:any) => (
              <p key={item} style={{margin:"3px 0",fontSize:"0.78rem",color:"var(--txt2)"}}>• <b>{item}</b>: {a.action}</p>
            ))}
          </div>
        )}
      </div>

      {}
      <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"20px"}}>
        <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"12px"}}>
          <h3 style={{margin:0,fontSize:"0.9rem",fontWeight:600}}>📦 Marketplace Listings</h3>
          <button onClick={loadAll} style={{background:"transparent",border:"1px solid var(--bdr)",color:"var(--txt3)",padding:"4px 10px",borderRadius:"7px",cursor:"pointer",fontSize:"0.75rem"}}>
            <RefreshCw size={12}/>
          </button>
        </div>
        {listings.length === 0
          ? <p style={{color:"var(--txt3)",fontSize:"0.83rem",textAlign:"center",padding:"16px"}}>No menu items yet.</p>
          : <div style={{display:"flex",flexDirection:"column",gap:"8px"}}>
              {listings.map((item) => {
                const pct = item.forecasted > 0 ? Math.round((item.sold/item.forecasted)*100) : 0;
                const col = pct>=90?"var(--green)":pct>=55?"#f59e0b":"#ef4444";
                return (
                  <div key={item.item} style={{display:"flex",alignItems:"center",gap:"10px",padding:"10px 12px",background:"var(--input)",borderRadius:"9px",opacity:item.listed?1:0.55}}>
                    {item.photo_b64 && <img src={item.photo_b64} alt="" style={{width:"36px",height:"36px",borderRadius:"6px",objectFit:"cover",flexShrink:0}}/>}
                    <div style={{flex:1,minWidth:0}}>
                      <p style={{margin:"0 0 2px",fontWeight:600,fontSize:"0.85rem",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{item.item}</p>
                      <div style={{display:"flex",alignItems:"center",gap:"6px",fontSize:"0.73rem",color:"var(--txt3)"}}>
                        <span>₹{item.price_rm.toFixed(2)}</span>
                        <span>·</span>
                        <span style={{color:item.discount_pct!==null?"#f59e0b":"var(--txt3)"}}>
                          {item.discount_pct!==null ? `${item.discount_pct}% off (item)` : `${discountPct}% off (global)`}
                        </span>
                        <span>·</span>
                        <span style={{color:col}}>{item.remaining} left</span>
                      </div>
                      {item.ai_last_action && <p style={{margin:"2px 0 0",fontSize:"0.7rem",color:"#a78bfa",overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>🤖 {item.ai_last_action}</p>}
                    </div>
                    <div style={{display:"flex",alignItems:"center",gap:"6px",flexShrink:0}}>
                      <button onClick={()=>openEditor(item)} title="Edit item"
                        style={{background:"rgba(16,185,129,0.12)",border:"1px solid rgba(16,185,129,0.3)",color:"var(--green)",borderRadius:"6px",padding:"5px 9px",cursor:"pointer",fontSize:"0.75rem"}}>
                        Edit
                      </button>
                      <button onClick={()=>toggleListed(item)} title={item.listed?"Unlist":"List"}
                        style={{background:"transparent",border:"1px solid var(--bdr)",color:"var(--txt3)",borderRadius:"6px",padding:"5px 8px",cursor:"pointer"}}>
                        {item.listed ? <Eye size={13}/> : <EyeOff size={13}/>}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
        }
      </div>

      {}
      {selected && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.7)",display:"flex",alignItems:"flex-end",justifyContent:"center",zIndex:300}}
          onClick={e=>{if(e.target===e.currentTarget)setSelected(null);}}>
          <div style={{background:"var(--card)",borderRadius:"20px 20px 0 0",padding:"24px",width:"100%",maxWidth:"640px",maxHeight:"85vh",overflowY:"auto"}}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"18px"}}>
              <h3 style={{margin:0,fontSize:"1rem",fontWeight:700}}>{selected.item}</h3>
              <button onClick={()=>setSelected(null)} style={{background:"transparent",border:"none",color:"var(--txt3)",cursor:"pointer"}}><X size={20}/></button>
            </div>

            {}
            <div style={{marginBottom:"14px"}}>
              <p style={{margin:"0 0 6px",fontSize:"0.8rem",color:"var(--txt2)",fontWeight:500}}>Item Photo</p>
              {selected.photo_b64
                ? <div style={{position:"relative",display:"inline-block"}}>
                    <img src={selected.photo_b64} alt="" style={{height:"90px",borderRadius:"10px",objectFit:"cover"}}/>
                    <button onClick={()=>photoRef.current?.click()} style={{position:"absolute",bottom:"4px",right:"4px",background:"rgba(0,0,0,0.6)",border:"none",color:"#fff",borderRadius:"6px",padding:"3px 7px",cursor:"pointer",fontSize:"0.72rem"}}>Change</button>
                  </div>
                : <button onClick={()=>photoRef.current?.click()} disabled={uploading}
                    style={{display:"flex",alignItems:"center",gap:"8px",padding:"10px 16px",borderRadius:"9px",background:"rgba(255,255,255,0.05)",border:"1px dashed var(--bdr)",color:"var(--txt3)",cursor:"pointer",fontSize:"0.82rem"}}>
                    <Camera size={16}/>{uploading?"Uploading…":"Upload Photo"}
                  </button>
              }
              <input ref={photoRef} type="file" accept="image/*" style={{display:"none"}} onChange={e=>{const f=e.target.files?.[0];if(f)uploadPhoto(f);e.target.value="";}}/>
            </div>
            <div style={{marginBottom:"14px"}}>
              <label style={{display:"block",fontSize:"0.8rem",color:"var(--txt2)",marginBottom:"6px"}}>Price (₹) <span style={{color:"var(--txt3)"}}>— menu default: ₹{selected.menu_price_rm.toFixed(2)}</span></label>
              <input type="number" min={0.10} step={0.50} value={editPrice} onChange={e=>setEditPrice(e.target.value)}
                style={{width:"100%",padding:"10px 12px",borderRadius:"8px",background:"var(--input)",color:"var(--txt)",border:"1px solid var(--bdr)",fontSize:"0.9rem",outline:"none",boxSizing:"border-box"}}/>
            </div>

            {}
            <div style={{marginBottom:"18px"}}>
              <label style={{display:"block",fontSize:"0.8rem",color:"var(--txt2)",marginBottom:"8px"}}>Discount</label>
              <div style={{display:"flex",gap:"8px",marginBottom:"10px"}}>
                <button onClick={()=>setUseItemDisc(false)}
                  style={{flex:1,padding:"8px",borderRadius:"8px",background:!useItemDisc?"rgba(16,185,129,0.15)":"var(--input)",border:`1px solid ${!useItemDisc?"var(--green)":"var(--bdr)"}`,color:!useItemDisc?"var(--green)":"var(--txt3)",cursor:"pointer",fontSize:"0.8rem",fontWeight:!useItemDisc?700:400}}>
                  Use Global ({discountPct}%)
                </button>
                <button onClick={()=>setUseItemDisc(true)}
                  style={{flex:1,padding:"8px",borderRadius:"8px",background:useItemDisc?"rgba(245,158,11,0.15)":"var(--input)",border:`1px solid ${useItemDisc?"#f59e0b":"var(--bdr)"}`,color:useItemDisc?"#f59e0b":"var(--txt3)",cursor:"pointer",fontSize:"0.8rem",fontWeight:useItemDisc?700:400}}>
                  Item-Specific
                </button>
              </div>
              {useItemDisc && (
                <div style={{display:"flex",alignItems:"center",gap:"10px"}}>
                  <input type="range" min={0} max={70} step={5} value={editDisc??discountPct} onChange={e=>setEditDisc(Number(e.target.value))}
                    style={{flex:1,accentColor:"#f59e0b"}}/>
                  <span style={{minWidth:"40px",fontWeight:700,color:"#f59e0b",fontSize:"1rem",textAlign:"right"}}>{editDisc??discountPct}%</span>
                </div>
              )}
            </div>

            {}
            {selected.ai_last_action && (
              <div style={{background:"rgba(139,92,246,0.1)",border:"1px solid rgba(139,92,246,0.25)",borderRadius:"8px",padding:"10px 12px",marginBottom:"14px"}}>
                <p style={{margin:0,fontSize:"0.78rem",color:"#a78bfa"}}>🤖 AI last action: {selected.ai_last_action}</p>
              </div>
            )}

            <button onClick={saveItemSettings}
              style={{width:"100%",padding:"12px",borderRadius:"10px",background:"var(--green)",color:"#fff",border:"none",fontWeight:700,fontSize:"0.9rem",cursor:"pointer"}}>
              💾 Save Item Settings
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
