"use client";
import React, { useState, useEffect, useCallback, Suspense } from "react";
import { useSearchParams } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";


interface MenuItem { item:string; original_price_rm:number; price_rm:number; discount_pct:number; qty_available:number|null; is_closing_stock:boolean; photo_b64?:string; ai_last_action?:string; has_item_discount?:boolean }
interface Restaurant { id:string; name:string; region:string; type:string; closing_time:string; discount_pct:number; discount_label:string; urgency:string; minutes_to_close:number|null; menu:MenuItem[]; is_closing_stock:boolean; orders_today:number; total_items:number }
interface CartItem { item:string; qty:number; unit_price:number; restaurant_id:string; restaurant_name:string }


const TYPE_EMOJI: Record<string,string> = { streetstall:"🍛", dhaba:"🍵", cafe:"☕", restaurant:"🍽️", dessert:"🍨", other:"🏪" };
const URGENCY_COLOR: Record<string,string> = { none:"#10b981", low:"#f59e0b", medium:"#f97316", high:"#ef4444", closed:"#6b7280" };
const URGENCY_BG: Record<string,string>    = { none:"rgba(16,185,129,0.12)", low:"rgba(245,158,11,0.12)", medium:"rgba(249,115,22,0.12)", high:"rgba(239,68,68,0.12)", closed:"rgba(107,114,128,0.12)" };

function fmtTime(mins: number|null): string {
  if (mins === null) return "";
  if (mins <= 0) return "closing now";
  if (mins < 60) return `${mins}min left`;
  return `${Math.floor(mins/60)}h ${mins%60}m left`;
}


const GLOBAL_CSS = `
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{background:#080b10;color:#e4e4e7;font-family:'Inter',sans-serif;min-height:100vh}
  input,textarea{background:rgba(255,255,255,0.07);color:#e4e4e7;border:1px solid rgba(255,255,255,0.1);border-radius:10px;padding:12px 14px;font-size:0.9rem;outline:none;width:100%;font-family:inherit;transition:border-color 0.2s}
  input:focus,textarea:focus{border-color:#10b981}
  button{font-family:inherit;cursor:pointer;transition:all 0.15s}
  ::-webkit-scrollbar{width:5px;height:5px}::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.1);border-radius:3px}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.6}}
  @keyframes slideUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
  @keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}
  @keyframes fadeIn{from{opacity:0}to{opacity:1}}
`;



function UrgencyBadge({ urgency, label, mins }: { urgency:string; label:string; mins:number|null }) {
  const col = URGENCY_COLOR[urgency] || "#10b981";
  const bg  = URGENCY_BG[urgency]   || "rgba(16,185,129,0.12)";
  if (urgency === "none") return <span style={{background:bg,color:col,padding:"3px 10px",borderRadius:"20px",fontSize:"0.72rem",fontWeight:600}}>🟢 Open</span>;
  if (urgency === "closed") return <span style={{background:bg,color:col,padding:"3px 10px",borderRadius:"20px",fontSize:"0.72rem",fontWeight:600}}>⛔ Closed</span>;
  return (
    <span style={{background:bg,border:`1px solid ${col}40`,color:col,padding:"3px 10px",borderRadius:"20px",fontSize:"0.72rem",fontWeight:700,animation:urgency==="high"?"pulse 1.5s infinite":"none",display:"inline-flex",alignItems:"center",gap:"4px"}}>
      🔥 {label} {mins!==null&&<>· {fmtTime(mins)}</>}
    </span>
  );
}

function DiscountBadge({ pct }: { pct:number }) {
  if (pct === 0) return null;
  const col = pct >= 30 ? "#ef4444" : pct >= 20 ? "#f97316" : "#f59e0b";
  return <span style={{background:`${col}20`,color:col,padding:"2px 8px",borderRadius:"6px",fontSize:"0.75rem",fontWeight:800}}>{pct}% OFF</span>;
}


function RestaurantCard({ r, onClick }: { r:Restaurant; onClick:()=>void }) {
  const emoji = TYPE_EMOJI[r.type] || "🏪";
  const hasDeal = r.discount_pct > 0;
  return (
    <div onClick={onClick} style={{background:"#0d1117",border:`1px solid ${hasDeal?URGENCY_COLOR[r.urgency]+"40":"rgba(255,255,255,0.07)"}`,borderRadius:"16px",overflow:"hidden",cursor:"pointer",transition:"transform 0.15s,box-shadow 0.15s",animation:"fadeIn 0.3s ease"}}
      onMouseEnter={e=>{(e.currentTarget as HTMLElement).style.transform="translateY(-3px)";(e.currentTarget as HTMLElement).style.boxShadow=`0 8px 30px ${URGENCY_COLOR[r.urgency]}20`}}
      onMouseLeave={e=>{(e.currentTarget as HTMLElement).style.transform="";(e.currentTarget as HTMLElement).style.boxShadow=""}}
    >
      {}
      <div style={{background:`linear-gradient(135deg, ${URGENCY_BG[r.urgency]}, rgba(255,255,255,0.02))`,padding:"22px 20px 16px",position:"relative"}}>
        {hasDeal && <div style={{position:"absolute",top:"12px",right:"12px"}}><DiscountBadge pct={r.discount_pct}/></div>}
        <div style={{fontSize:"2.8rem",marginBottom:"8px"}}>{emoji}</div>
        <h3 style={{fontSize:"1.05rem",fontWeight:700,marginBottom:"4px"}}>{r.name}</h3>
        <p style={{fontSize:"0.78rem",color:"#a1a1aa",marginBottom:"8px"}}>📍 {r.region}</p>
        <UrgencyBadge urgency={r.urgency} label={r.discount_label} mins={r.minutes_to_close}/>
      </div>
      {}
      <div style={{padding:"12px 18px",borderTop:"1px solid rgba(255,255,255,0.05)",display:"flex",justifyContent:"space-between",alignItems:"center"}}>
        <div style={{display:"flex",gap:"14px",fontSize:"0.78rem",color:"#71717a"}}>
          <span>🍽️ {r.total_items} items</span>
          {r.orders_today > 0 && <span>📦 {r.orders_today} orders today</span>}
          {r.closing_time && <span>⏰ Closes {r.closing_time}</span>}
        </div>
        <span style={{fontSize:"0.78rem",color:URGENCY_COLOR[r.urgency],fontWeight:600}}>
          {r.urgency==="closed"?"Closed":"Order →"}
        </span>
      </div>
    </div>
  );
}


function RestaurantDetail({
  restaurantId, cart, setCart, onBack
}: {
  restaurantId:string; cart:CartItem[]; setCart:React.Dispatch<React.SetStateAction<CartItem[]>>; onBack:()=>void;
}) {
  const [data, setData] = useState<Restaurant|null>(null);
  const [loading, setLoading] = useState(true);
  const [showOrder, setShowOrder] = useState(false);
  const [name, setName]   = useState("");
  const [phone, setPhone] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [confirmed, setConfirmed] = useState<any>(null);

  const myItems = cart.filter(c => c.restaurant_id === restaurantId);
  const myTotal = myItems.reduce((s,c) => s + c.unit_price * c.qty, 0);

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/api/marketplace/${restaurantId}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if(d) setData(d); setLoading(false); })
      .catch(() => setLoading(false));
    const i = setInterval(() => {
      fetch(`${API}/api/marketplace/${restaurantId}`).then(r=>r.ok?r.json():null).then(d=>{if(d)setData(d);}).catch(()=>null);
    }, 30_000);
    return () => clearInterval(i);
  }, [restaurantId]);

  const addToCart = (item: MenuItem) => {
    
    setCart(prev => {
      const filtered = prev.filter(c => c.restaurant_id === restaurantId);
      const others   = prev.filter(c => c.restaurant_id !== restaurantId);
      if (others.length > 0 && filtered.length === 0) {
        if (!confirm("Your cart has items from another restaurant. Clear it?")) return prev;
        return [{ item:item.item, qty:1, unit_price:item.price_rm, restaurant_id:restaurantId, restaurant_name:data?.name||"" }];
      }
      const existing = filtered.find(c => c.item === item.item);
      if (existing) return [...others, ...filtered.map(c => c.item===item.item ? {...c, qty:c.qty+1} : c)];
      return [...prev, { item:item.item, qty:1, unit_price:item.price_rm, restaurant_id:restaurantId, restaurant_name:data?.name||"" }];
    });
  };

  const removeFromCart = (itemName:string) => setCart(prev => {
    const updated = prev.map(c => c.item===itemName&&c.restaurant_id===restaurantId ? {...c,qty:c.qty-1} : c).filter(c=>c.qty>0);
    return updated;
  });

  const placeOrder = async () => {
    if (!name.trim()||!phone.trim()) return;
    setSubmitting(true);
    try {
      const payload = { 
        restaurant_id:restaurantId, 
        customer_name:name.trim(), 
        phone:phone.trim(), 
        items:myItems.map(c=>({item:c.item,qty:c.qty})), 
        pickup_notes:notes.trim(),
        ref_token: localStorage.getItem("token") || undefined
      };
      const r = await fetch(`${API}/api/customer/order`, {
        method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        try { const d = await r.json(); alert(Array.isArray(d.detail) ? d.detail[0].msg : (d.detail || "Order failed.")); }
        catch { alert(`Server error ${r.status}.`); }
      } else {
        const d = await r.json();
        const saved = JSON.parse(localStorage.getItem("my_orders") || "[]");
        if (!saved.includes(d.order_id)) saved.push(d.order_id);
        localStorage.setItem("my_orders", JSON.stringify(saved));
        setConfirmed(d); setCart(prev=>prev.filter(c=>c.restaurant_id!==restaurantId));
      }
    } catch { alert("Network error."); }
    setSubmitting(false);
  };

  if (loading) return <div style={{textAlign:"center",padding:"80px",color:"#71717a"}}><div style={{fontSize:"2rem",animation:"spin 1.5s linear infinite",display:"inline-block",marginBottom:"12px"}}>🌿</div><p>Loading menu…</p></div>;
  if (!data) return <div style={{textAlign:"center",padding:"64px",color:"#71717a"}}><p>Restaurant not found.</p><button onClick={onBack} style={{marginTop:"12px",color:"#10b981",background:"transparent",border:"none",fontSize:"0.9rem"}}>← Back</button></div>;

  if (confirmed) return (
    <div style={{maxWidth:"480px",margin:"60px auto",background:"#0d1117",border:"1px solid rgba(16,185,129,0.4)",borderRadius:"20px",padding:"36px",textAlign:"center",animation:"slideUp 0.3s ease"}}>
      <div style={{fontSize:"4rem",marginBottom:"14px"}}>✅</div>
      <h2 style={{fontSize:"1.4rem",fontWeight:800,color:"#10b981",marginBottom:"8px"}}>Order Placed!</h2>
      <p style={{color:"#a1a1aa",marginBottom:"20px"}}>Pick up at <b style={{color:"#e4e4e7"}}>{data.name}</b> before {data.closing_time}</p>
      <div style={{background:"rgba(16,185,129,0.08)",border:"1px solid rgba(16,185,129,0.2)",borderRadius:"12px",padding:"16px",marginBottom:"20px",textAlign:"left"}}>
        <p style={{fontSize:"0.75rem",color:"#71717a",marginBottom:"3px"}}>Order ID</p>
        <p style={{fontFamily:"monospace",color:"#10b981",fontWeight:700,fontSize:"1rem"}}>{confirmed.order_id}</p>
        <p style={{fontSize:"0.75rem",color:"#71717a",marginTop:"10px",marginBottom:"3px"}}>Total (pay on pickup)</p>
        <p style={{fontWeight:800,fontSize:"1.3rem"}}>₹{confirmed.total_rm?.toFixed(2)}</p>
      </div>
      <p style={{fontSize:"0.78rem",color:"#71717a",marginBottom:"16px"}}>💳 Pay cash on pickup · Show order ID</p>
      <button onClick={onBack} style={{padding:"12px 28px",borderRadius:"10px",background:"#10b981",color:"#fff",border:"none",fontWeight:700}}>← Back to Marketplace</button>
    </div>
  );

  const urgColor = URGENCY_COLOR[data.urgency] || "#10b981";
  const emoji = TYPE_EMOJI[data.type] || "🏪";

  return (
    <div style={{animation:"fadeIn 0.25s ease"}}>
      {}
      <div style={{background:`linear-gradient(135deg, ${URGENCY_BG[data.urgency]}, #0d1117)`,borderRadius:"16px",padding:"24px",marginBottom:"20px",position:"relative"}}>
        <div style={{display:"flex",alignItems:"center",gap:"8px",marginBottom:"14px"}}>
          <button onClick={onBack} style={{background:"rgba(255,255,255,0.08)",border:"none",color:"#e4e4e7",borderRadius:"8px",padding:"8px 14px",fontSize:"0.85rem"}}>← Back</button>
          <button onClick={()=>{
            const url = `${window.location.origin}/marketplace?store=${restaurantId}`;
            if (navigator.share) navigator.share({title:data.name,text:`Check out ${data.name} on WasteWise Market!`,url}).catch(()=>null);
            else navigator.clipboard?.writeText(url).then(()=>alert('Restaurant link copied!')).catch(()=>null);
          }} style={{background:"rgba(16,185,129,0.15)",border:"1px solid rgba(16,185,129,0.35)",color:"#10b981",borderRadius:"8px",padding:"8px 14px",fontSize:"0.82rem",fontWeight:600,cursor:"pointer"}}>🔗 Share</button>
        </div>
        <div style={{display:"flex",alignItems:"flex-start",gap:"14px"}}>
          <div style={{fontSize:"3.5rem"}}>{emoji}</div>
          <div style={{flex:1}}>
            <h2 style={{fontSize:"1.4rem",fontWeight:800,marginBottom:"4px"}}>{data.name}</h2>
            <p style={{color:"#a1a1aa",fontSize:"0.83rem",marginBottom:"8px"}}>📍 {data.region} · ⏰ Closes {data.closing_time}</p>
            <div style={{display:"flex",gap:"8px",flexWrap:"wrap"}}>
              <UrgencyBadge urgency={data.urgency} label={data.discount_label} mins={data.minutes_to_close}/>
              {data.discount_pct > 0 && <DiscountBadge pct={data.discount_pct}/>}
              {data.is_closing_stock && <span style={{background:"rgba(239,68,68,0.15)",color:"#ef4444",padding:"3px 10px",borderRadius:"20px",fontSize:"0.72rem",fontWeight:700}}>🔥 Limited Stock</span>}
            </div>
          </div>
        </div>
      </div>

      {}
      {data.discount_pct > 0 && (
        <div style={{background:"rgba(245,158,11,0.08)",border:"1px solid rgba(245,158,11,0.2)",borderRadius:"10px",padding:"12px 16px",marginBottom:"18px",fontSize:"0.82rem",color:"#fbbf24"}}>
          ⚡ <b>Dynamic pricing active</b> — {data.discount_label}. Prices shown already include the discount.
          {data.minutes_to_close && data.minutes_to_close > 0 && <> Closes in <b>{fmtTime(data.minutes_to_close)}</b>.</>}
        </div>
      )}

      {}
      <div style={{display:"flex",flexDirection:"column",gap:"10px",marginBottom:"100px"}}>
        {data.menu.length === 0 ? (
          <div style={{textAlign:"center",padding:"48px",color:"#71717a"}}>
            <p style={{fontSize:"2rem",marginBottom:"8px"}}>🍽️</p>
            <p>No items available right now.</p>
          </div>
        ) : data.menu.map(item => {
          const inCart = myItems.find(c => c.item === item.item);
          const unavail = item.qty_available !== null && item.qty_available <= 0;
          return (
            <div key={item.item} style={{background:"#0d1117",border:"1px solid rgba(255,255,255,0.07)",borderRadius:"12px",padding:"16px",display:"flex",justifyContent:"space-between",alignItems:"center",gap:"12px",opacity:unavail ? 0.45 : 1}}>
              {item.photo_b64 && <img src={item.photo_b64} alt={item.item} style={{width:"56px",height:"56px",borderRadius:"10px",objectFit:"cover",flexShrink:0}}/>}
              <div style={{flex:1}}>
                <div style={{display:"flex",alignItems:"center",gap:"8px",marginBottom:"4px"}}>
                  <h4 style={{fontSize:"0.95rem",fontWeight:600}}>{item.item}</h4>
                  {item.discount_pct > 0 && <DiscountBadge pct={item.discount_pct}/>}
                  {(item as any).has_item_discount && <span style={{fontSize:"0.68rem",background:"rgba(245,158,11,0.15)",color:"#f59e0b",padding:"1px 6px",borderRadius:"4px"}}>custom</span>}
                  {item.is_closing_stock && item.qty_available !== null && <span style={{fontSize:"0.72rem",color:"#a1a1aa"}}>{item.qty_available} left</span>}
                </div>
                <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                  <span style={{fontSize:"1.05rem",fontWeight:800,color:"#10b981"}}>₹{item.price_rm.toFixed(2)}</span>
                  {item.discount_pct > 0 && <span style={{fontSize:"0.82rem",color:"#71717a",textDecoration:"line-through"}}>₹{item.original_price_rm.toFixed(2)}</span>}
                </div>
                {(item as any).ai_last_action && <p style={{margin:"4px 0 0",fontSize:"0.7rem",color:"#a78bfa",opacity:0.85}}>🤖 {(item as any).ai_last_action}</p>}
              </div>

              {!unavail && (
                inCart ? (
                  <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                    <button onClick={()=>removeFromCart(item.item)} style={{width:"34px",height:"34px",borderRadius:"50%",background:"rgba(255,255,255,0.09)",border:"none",color:"#e4e4e7",fontSize:"1.2rem"}}>−</button>
                    <span style={{fontWeight:700,minWidth:"20px",textAlign:"center"}}>{inCart.qty}</span>
                    <button onClick={()=>addToCart(item)} disabled={item.qty_available!==null&&inCart.qty>=item.qty_available} style={{width:"34px",height:"34px",borderRadius:"50%",background:"rgba(16,185,129,0.2)",border:"none",color:"#10b981",fontSize:"1.2rem",opacity:item.qty_available!==null&&inCart.qty>=item.qty_available?0.4:1}}>+</button>
                  </div>
                ) : (
                  <button onClick={()=>addToCart(item)} style={{padding:"9px 18px",borderRadius:"9px",background:"rgba(16,185,129,0.14)",border:"1px solid rgba(16,185,129,0.35)",color:"#10b981",fontWeight:700,fontSize:"0.88rem"}}>Add</button>
                )
              )}
              {unavail && <span style={{fontSize:"0.78rem",color:"#6b7280"}}>Sold out</span>}
            </div>
          );
        })}
      </div>

      {}
      {myItems.length > 0 && !showOrder && (
        <div style={{position:"fixed",bottom:"20px",left:"50%",transform:"translateX(-50%)",width:"calc(100% - 32px)",maxWidth:"680px",background:"#10b981",borderRadius:"14px",padding:"16px 20px",display:"flex",justifyContent:"space-between",alignItems:"center",boxShadow:"0 8px 40px rgba(16,185,129,0.45)",zIndex:90,animation:"slideUp 0.25s ease"}}>
          <div>
            <p style={{margin:0,color:"#fff",fontWeight:700}}>{myItems.reduce((s,c)=>s+c.qty,0)} items · ₹{myTotal.toFixed(2)}</p>
            <p style={{margin:0,fontSize:"0.78rem",color:"rgba(255,255,255,0.75)"}}>Pay on pickup</p>
          </div>
          <button onClick={()=>setShowOrder(true)} style={{background:"#fff",color:"#10b981",border:"none",borderRadius:"9px",padding:"10px 22px",fontWeight:800,fontSize:"0.9rem"}}>Checkout →</button>
        </div>
      )}

      {}
      {showOrder && (
        <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.8)",display:"flex",alignItems:"flex-end",justifyContent:"center",zIndex:200,animation:"fadeIn 0.2s"}}>
          <div style={{background:"#0d1117",borderRadius:"20px 20px 0 0",padding:"28px 24px",width:"100%",maxWidth:"680px",maxHeight:"90vh",overflowY:"auto"}}>
            <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"18px"}}>
              <h3 style={{fontSize:"1.1rem",fontWeight:700}}>📋 Confirm Order</h3>
              <button onClick={()=>setShowOrder(false)} style={{background:"transparent",border:"none",color:"#71717a",fontSize:"1.2rem"}}>✕</button>
            </div>
            {myItems.map(c=>(
              <div key={c.item} style={{display:"flex",justifyContent:"space-between",padding:"8px 0",borderBottom:"1px solid rgba(255,255,255,0.05)",fontSize:"0.88rem"}}>
                <span>{c.qty}× {c.item}</span>
                <span style={{color:"#10b981",fontWeight:700}}>₹{(c.unit_price*c.qty).toFixed(2)}</span>
              </div>
            ))}
            <div style={{display:"flex",justifyContent:"space-between",padding:"14px 0",fontWeight:800,fontSize:"1rem",borderBottom:"1px solid rgba(255,255,255,0.08)",marginBottom:"16px"}}>
              <span>Total</span><span style={{color:"#10b981"}}>₹{myTotal.toFixed(2)}</span>
            </div>
            <div style={{display:"flex",flexDirection:"column",gap:"10px",marginBottom:"16px"}}>
              <input value={name} onChange={e=>setName(e.target.value)} placeholder="Your name *"/>
              <input value={phone} onChange={e=>setPhone(e.target.value)} placeholder="Phone number *" type="tel"/>
              <textarea value={notes} onChange={e=>setNotes(e.target.value)} placeholder="Pickup notes (optional)" rows={2} style={{resize:"none"}}/>
            </div>
            <button onClick={placeOrder} disabled={submitting||!name.trim()||!phone.trim()} style={{width:"100%",padding:"14px",borderRadius:"10px",background:"#10b981",color:"#fff",border:"none",fontWeight:700,fontSize:"0.95rem",opacity:(submitting||!name.trim()||!phone.trim())?0.6:1}}>
              {submitting?"Placing Order…":`✅ Place Order · ₹${myTotal.toFixed(2)}`}
            </button>
            <p style={{textAlign:"center",marginTop:"8px",fontSize:"0.75rem",color:"#71717a"}}>💳 Pay cash on pickup · Show order ID to shopkeeper</p>
          </div>
        </div>
      )}
    </div>
  );
}

function MyOrdersModal({ onClose }: { onClose: () => void }) {
  const [orders, setOrders] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  
  const fetchOrders = useCallback(async () => {
    const saved = JSON.parse(localStorage.getItem("my_orders") || "[]");
    if (saved.length === 0) { setLoading(false); return; }
    try {
      const r = await fetch(`${API}/api/customer/orders/batch`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ order_ids: saved })
      });
      if (r.ok) {
        const d = await r.json();
        setOrders(d.orders || []);
      }
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { 
    fetchOrders(); 
    const i = setInterval(fetchOrders, 5000); 
    return () => clearInterval(i); 
  }, [fetchOrders]);

  const cancelOrder = async (orderId: string) => {
    if (!confirm("Are you sure you want to cancel this order?")) return;
    try {
      const r = await fetch(`${API}/api/customer/orders/${orderId}/cancel`, { method: "POST" });
      if (r.ok) {
        alert("Order cancelled successfully.");
        fetchOrders();
      } else {
        const d = await r.json();
        alert(d.detail || "Failed to cancel.");
      }
    } catch { alert("Network error."); }
  };

  return (
    <div style={{position:"fixed",inset:0,background:"rgba(0,0,0,0.8)",display:"flex",alignItems:"flex-end",justifyContent:"center",zIndex:300,animation:"fadeIn 0.2s"}}>
      <div style={{background:"#0d1117",borderRadius:"20px 20px 0 0",padding:"28px 24px",width:"100%",maxWidth:"680px",maxHeight:"90vh",overflowY:"auto"}}>
        <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"18px"}}>
          <h3 style={{fontSize:"1.1rem",fontWeight:700}}>📦 My Orders</h3>
          <button onClick={onClose} style={{background:"transparent",border:"none",color:"#71717a",fontSize:"1.2rem"}}>✕</button>
        </div>
        {loading ? (
          <p style={{color:"#a1a1aa",textAlign:"center",padding:"20px"}}>Loading...</p>
        ) : orders.length === 0 ? (
          <p style={{color:"#a1a1aa",textAlign:"center",padding:"20px"}}>No recent orders found.</p>
        ) : (
          <div style={{display:"flex",flexDirection:"column",gap:"14px"}}>
            {orders.map(o => {
              const statusColor = o.status === "completed" ? "#10b981" : o.status === "cancelled" ? "#ef4444" : o.status === "accepted" ? "#3b82f6" : "#f59e0b";
              return (
                <div key={o.order_id} style={{background:"rgba(255,255,255,0.03)",border:"1px solid rgba(255,255,255,0.08)",borderRadius:"12px",padding:"16px"}}>
                  <div style={{display:"flex",justifyContent:"space-between",marginBottom:"8px"}}>
                    <div style={{fontWeight:700}}>{o.restaurant_name}</div>
                    <div style={{color:statusColor,fontWeight:700,fontSize:"0.85rem",textTransform:"uppercase"}}>{o.status}</div>
                  </div>
                  <div style={{fontSize:"0.8rem",color:"#a1a1aa",marginBottom:"10px"}}>
                    Order #{o.order_num} · {new Date(o.created_at).toLocaleDateString()}
                  </div>
                  <div style={{marginBottom:"12px",fontSize:"0.85rem"}}>
                    {o.items.map((i:any) => (
                      <div key={i.item} style={{display:"flex",justifyContent:"space-between",padding:"2px 0"}}>
                        <span>{i.qty}× {i.item}</span>
                        <span>₹{i.line_total_rm.toFixed(2)}</span>
                      </div>
                    ))}
                  </div>
                  <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",borderTop:"1px solid rgba(255,255,255,0.05)",paddingTop:"10px"}}>
                    <span style={{fontWeight:700,color:"#10b981"}}>Total: ₹{o.total_rm.toFixed(2)}</span>
                    {(o.status === "pending" || o.status === "accepted") && (
                      <button onClick={() => cancelOrder(o.order_id)} style={{padding:"6px 14px",borderRadius:"6px",background:"rgba(239,68,68,0.15)",color:"#ef4444",border:"1px solid rgba(239,68,68,0.3)",fontSize:"0.8rem",fontWeight:600}}>Cancel</button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function MarketplaceInner() {
  const params = useSearchParams();
  const initStore = params.get("store") || "";

  useEffect(() => {
    const rToken = params.get("ref");
    if (rToken) {
      localStorage.setItem("token", rToken);
    }
  }, [params]);

  const [restaurants, setRestaurants] = useState<Restaurant[]>([]);
  const [loading, setLoading]         = useState(true);
  const [search, setSearch]           = useState("");
  const [filter, setFilter]           = useState<"all"|"deals"|"open">("all");
  const [activeId, setActiveId]       = useState<string>(initStore);
  const [cart, setCart]               = useState<CartItem[]>([]);
  const [showOrders, setShowOrders]   = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/marketplace`);
      if (r.ok) setRestaurants((await r.json()).restaurants || []);
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { load(); const i = setInterval(load, 60_000); return ()=>clearInterval(i); }, [load]);
  useEffect(() => { if (initStore) setActiveId(initStore); }, [initStore]);

  const filtered = restaurants.filter(r => {
    const matchSearch = !search || r.name.toLowerCase().includes(search.toLowerCase()) || r.region.toLowerCase().includes(search.toLowerCase());
    const matchFilter = filter==="all" || (filter==="deals"&&r.discount_pct>0) || (filter==="open"&&r.urgency!=="closed");
    return matchSearch && matchFilter;
  });

  const cartTotal = cart.reduce((s,c)=>s+c.unit_price*c.qty,0);
  const cartRestaurant = cart[0]?.restaurant_name;

  return (
    <div style={{minHeight:"100vh",background:"#080b10"}}>
      <style>{GLOBAL_CSS}</style>

      {showOrders && <MyOrdersModal onClose={() => setShowOrders(false)} />}

      {}
      <header style={{background:"rgba(13,17,23,0.95)",borderBottom:"1px solid rgba(255,255,255,0.07)",padding:"0 20px",position:"sticky",top:0,zIndex:100,backdropFilter:"blur(12px)"}}>
        <div style={{maxWidth:"1100px",margin:"0 auto",display:"flex",alignItems:"center",gap:"16px",height:"64px"}}>
          <div style={{display:"flex",alignItems:"center",gap:"10px",cursor:"pointer"}} onClick={()=>setActiveId("")}>
            <span style={{fontSize:"1.6rem"}}>🌿</span>
            <div>
              <span style={{fontWeight:800,fontSize:"1rem"}}>WasteWise </span>
              <span style={{fontWeight:800,fontSize:"1rem",color:"#10b981"}}>Market</span>
            </div>
          </div>
          {!activeId && (
            <div style={{flex:1,display:"flex",gap:"10px"}}>
              <input value={search} onChange={e=>setSearch(e.target.value)} placeholder="🔍 Search restaurants or areas…" style={{flex:1,padding:"9px 14px",borderRadius:"10px",maxWidth:"380px"}}/>
            </div>
          )}
          {activeId && (
            <button onClick={()=>setActiveId("")} style={{background:"rgba(255,255,255,0.07)",border:"none",color:"#e4e4e7",borderRadius:"8px",padding:"7px 14px",fontSize:"0.85rem"}}>
              ← All Restaurants
            </button>
          )}
          <div style={{marginLeft:"auto",display:"flex",alignItems:"center",gap:"12px"}}>
            <button onClick={() => setShowOrders(true)} style={{background:"rgba(16,185,129,0.1)",color:"#10b981",border:"1px solid rgba(16,185,129,0.2)",padding:"7px 12px",borderRadius:"8px",fontSize:"0.85rem",fontWeight:700}}>📦 My Orders</button>
            <div style={{fontSize:"0.82rem",color:"#71717a",display:"none"}}>
              🔄 Prices update every minute
            </div>
          </div>
        </div>
      </header>

      <div style={{maxWidth:"1100px",margin:"0 auto",padding:"24px 20px"}}>
        {activeId ? (
          <RestaurantDetail restaurantId={activeId} cart={cart} setCart={setCart} onBack={()=>setActiveId("")}/>
        ) : (
          <>
            {}
            <div style={{textAlign:"center",padding:"40px 20px 32px",animation:"fadeIn 0.4s"}}>
              <h1 style={{fontSize:"clamp(1.8rem,4vw,2.8rem)",fontWeight:900,marginBottom:"10px"}}>
                Fresh Food, <span style={{color:"#10b981"}}>Smart Deals</span>
              </h1>
              <p style={{color:"#a1a1aa",maxWidth:"520px",margin:"0 auto",fontSize:"1rem",lineHeight:"1.6"}}>
                Order from local food stalls at full price — or catch automatic discounts as they approach closing time. Zero waste, maximum flavour.
              </p>
            </div>



            {}
            <div style={{display:"flex",gap:"8px",marginBottom:"20px"}}>
              {(["all","deals","open"] as const).map(f=>(
                <button key={f} onClick={()=>setFilter(f)} style={{padding:"8px 18px",borderRadius:"20px",border:`1px solid ${filter===f?"#10b981":"rgba(255,255,255,0.1)"}`,background:filter===f?"rgba(16,185,129,0.15)":"transparent",color:filter===f?"#10b981":"#a1a1aa",fontWeight:filter===f?700:400,fontSize:"0.85rem"}}>
                  {f==="all"?"🍽️ All":f==="deals"?"🔥 Deals Now":"🟢 Open Now"}
                </button>
              ))}
              {restaurants.length > 0 && <span style={{marginLeft:"auto",color:"#71717a",fontSize:"0.82rem",display:"flex",alignItems:"center"}}>{filtered.length} restaurant{filtered.length!==1?"s":""}</span>}
            </div>

            {}
            {loading ? (
              <div style={{textAlign:"center",padding:"80px",color:"#71717a"}}>
                <div style={{fontSize:"2.5rem",animation:"spin 1.5s linear infinite",display:"inline-block",marginBottom:"14px"}}>🌿</div>
                <p>Loading restaurants…</p>
              </div>
            ) : filtered.length === 0 ? (
              <div style={{textAlign:"center",padding:"80px",color:"#71717a"}}>
                <p style={{fontSize:"3rem",marginBottom:"12px"}}>🏪</p>
                <p style={{fontSize:"1rem",marginBottom:"6px"}}>No restaurants found</p>
                <p style={{fontSize:"0.85rem"}}>{search?"Try a different search.":"No restaurants have enabled the marketplace yet."}</p>
              </div>
            ) : (
              <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(280px,1fr))",gap:"16px"}}>
                {filtered.map(r=>(
                  <RestaurantCard key={r.id} r={r} onClick={()=>setActiveId(r.id)}/>
                ))}
              </div>
            )}
          </>
        )}
      </div>

      {}
      {!activeId && cart.length > 0 && (
        <div style={{position:"fixed",bottom:"20px",left:"50%",transform:"translateX(-50%)",width:"calc(100% - 32px)",maxWidth:"680px",background:"#0d1117",border:"1px solid rgba(16,185,129,0.4)",borderRadius:"14px",padding:"14px 20px",display:"flex",justifyContent:"space-between",alignItems:"center",boxShadow:"0 8px 40px rgba(0,0,0,0.5)",zIndex:90,animation:"slideUp 0.3s"}}>
          <div>
            <p style={{margin:0,fontWeight:700,fontSize:"0.9rem"}}>🛒 {cart.reduce((s,c)=>s+c.qty,0)} items from {cartRestaurant}</p>
            <p style={{margin:0,fontSize:"0.78rem",color:"#71717a"}}>₹{cartTotal.toFixed(2)} total</p>
          </div>
          <button onClick={()=>setActiveId(cart[0].restaurant_id)} style={{background:"#10b981",color:"#fff",border:"none",borderRadius:"9px",padding:"10px 20px",fontWeight:700,fontSize:"0.88rem"}}>
            Continue →
          </button>
        </div>
      )}
    </div>
  );
}

export default function MarketplacePage() {
  return (
    <Suspense fallback={<div style={{minHeight:"100vh",background:"#080b10",display:"flex",alignItems:"center",justifyContent:"center",color:"#a1a1aa"}}>Loading…</div>}>
      <MarketplaceInner/>
    </Suspense>
  );
}
