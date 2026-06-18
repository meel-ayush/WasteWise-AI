"use client";
import React, { useState, useEffect, useCallback, Suspense } from "react";
import { useSearchParams } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface StoreItem {
  item: string;
  qty_available: number;
  original_price_rm: number;
  discounted_price_rm: number;
  discount_pct: number;
}

interface StoreData {
  restaurant_id: string;
  restaurant_name: string;
  region: string;
  closing_time: string;
  discount_pct: number;
  closing_stock: StoreItem[];
  has_stock: boolean;
  marketplace_active: boolean;
  total_orders_today: number;
}

interface CartItem {
  item: string;
  qty: number;
  unit_price_rm: number;
}

function CustomerPageInner() {
  const params = useSearchParams();
  const storeId = params.get("store") || "";

  const [store, setStore]           = useState<StoreData|null>(null);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState("");
  const [cart, setCart]             = useState<CartItem[]>([]);
  const [step, setStep]             = useState<"browse"|"order"|"confirm">("browse");
  const [name, setName]             = useState("");
  const [phone, setPhone]           = useState("");
  const [notes, setNotes]           = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [orderId, setOrderId]       = useState("");
  const [orderTotal, setOrderTotal] = useState(0);

  const loadStore = useCallback(async () => {
    if (!storeId) { setError("No store ID provided. Please use a valid store link."); setLoading(false); return; }
    try {
      const r = await fetch(`${API}/api/customer/store/${storeId}`);
      if (!r.ok) {
        try { const d = await r.json(); setError(d.detail || "Store not found."); }
        catch { setError(`Server error ${r.status}.`); }
      } else setStore(await r.json());
    } catch { setError("Could not load store. Please try again."); }
    setLoading(false);
  }, [storeId]);

  useEffect(() => { loadStore(); const i = setInterval(loadStore, 60_000); return () => clearInterval(i); }, [loadStore]);

  const addToCart = (item: StoreItem, qty: number) => {
    setCart(prev => {
      const existing = prev.find(c => c.item === item.item);
      if (existing) {
        return prev.map(c => c.item === item.item ? {...c, qty: Math.min(qty, item.qty_available)} : c);
      }
      return [...prev, { item: item.item, qty, unit_price_rm: item.discounted_price_rm }];
    });
  };

  const removeFromCart = (itemName: string) => setCart(prev => prev.filter(c => c.item !== itemName));

  const cartTotal = cart.reduce((sum, c) => sum + c.unit_price_rm * c.qty, 0);

  const placeOrder = async () => {
    if (!name.trim() || !phone.trim()) { return; }
    if (cart.length === 0) { return; }
    setSubmitting(true);
    try {
      const r = await fetch(`${API}/api/customer/order`, {
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body: JSON.stringify({
          restaurant_id: storeId,
          customer_name: name.trim(),
          phone: phone.trim(),
          items: cart.map(c=>({item:c.item, qty:c.qty})),
          pickup_notes: notes.trim(),
        }),
      });
      if (!r.ok) {
        try { const d = await r.json(); alert(d.detail || "Order failed. Please try again."); }
        catch { alert(`Server error ${r.status}.`); }
      } else {
        const d = await r.json();
        setOrderId(d.order_id); setOrderTotal(d.total_rm); setStep("confirm");
      }
    } catch { alert("Network error. Please try again."); }
    setSubmitting(false);
  };

  const css = `
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{background:#0a0d12;color:#e4e4e7;font-family:'Inter',sans-serif;min-height:100vh}
    input,textarea{background:rgba(255,255,255,0.06);color:#e4e4e7;border:1px solid rgba(255,255,255,0.1);border-radius:9px;padding:11px 14px;font-size:0.9rem;outline:none;width:100%;font-family:inherit}
    input:focus,textarea:focus{border-color:#10b981}
    button{font-family:inherit;cursor:pointer}
    ::-webkit-scrollbar{width:5px}::-webkit-scrollbar-thumb{background:rgba(255,255,255,0.1);border-radius:3px}
  `;

  if (loading) return (
    <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center"}}>
      <style>{css}</style>
      <div style={{textAlign:"center"}}>
        <div style={{fontSize:"3rem",marginBottom:"12px",animation:"spin 2s linear infinite",display:"inline-block"}}>🌿</div>
        <p style={{color:"#a1a1aa"}}>Loading store…</p>
        <style>{`@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}`}</style>
      </div>
    </div>
  );

  if (error) return (
    <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center",padding:"20px"}}>
      <style>{css}</style>
      <div style={{textAlign:"center",maxWidth:"400px"}}>
        <div style={{fontSize:"3rem",marginBottom:"12px"}}>🏪</div>
        <h1 style={{fontSize:"1.4rem",fontWeight:700,marginBottom:"8px"}}>Store Not Found</h1>
        <p style={{color:"#a1a1aa",fontSize:"0.9rem"}}>{error}</p>
      </div>
    </div>
  );

  if (!store) return null;

  
  if (step === "confirm") return (
    <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center",padding:"20px"}}>
      <style>{css}</style>
      <div style={{maxWidth:"440px",width:"100%",background:"#0f1319",border:"1px solid rgba(16,185,129,0.4)",borderRadius:"20px",padding:"40px",textAlign:"center"}}>
        <div style={{fontSize:"4rem",marginBottom:"16px"}}>✅</div>
        <h1 style={{fontSize:"1.5rem",fontWeight:800,marginBottom:"8px",color:"#10b981"}}>Order Placed!</h1>
        <p style={{color:"#a1a1aa",marginBottom:"20px",fontSize:"0.9rem"}}>Your order has been sent to <b style={{color:"#e4e4e7"}}>{store.restaurant_name}</b>. They&apos;ll prepare it for pickup.</p>
        <div style={{background:"rgba(16,185,129,0.08)",border:"1px solid rgba(16,185,129,0.2)",borderRadius:"12px",padding:"16px",marginBottom:"20px",textAlign:"left"}}>
          <p style={{fontSize:"0.78rem",color:"#71717a",marginBottom:"4px"}}>Order ID</p>
          <p style={{fontWeight:700,fontFamily:"monospace",color:"#10b981",fontSize:"1rem"}}>{orderId}</p>
          <p style={{fontSize:"0.78rem",color:"#71717a",marginTop:"10px",marginBottom:"4px"}}>Total</p>
          <p style={{fontWeight:800,fontSize:"1.3rem"}}>₹{orderTotal.toFixed(2)}</p>
          <p style={{fontSize:"0.78rem",color:"#71717a",marginTop:"10px"}}>Pick up before {store.closing_time} at {store.restaurant_name}</p>
        </div>
        <p style={{fontSize:"0.8rem",color:"#71717a"}}>💡 Pay on pickup. Show this screen or your order ID to the shopkeeper.</p>
        <button onClick={()=>{setStep("browse");setCart([]);setName("");setPhone("");setNotes("");}} style={{marginTop:"20px",padding:"12px 24px",borderRadius:"9px",background:"#10b981",color:"#fff",border:"none",fontWeight:700,fontSize:"0.9rem"}}>Browse More</button>
      </div>
    </div>
  );

  return (
    <div style={{minHeight:"100vh",background:"#0a0d12"}}>
      <style>{css}</style>

      {}
      <header style={{background:"#0f1319",borderBottom:"1px solid rgba(255,255,255,0.08)",padding:"16px 20px",position:"sticky",top:0,zIndex:100}}>
        <div style={{maxWidth:"700px",margin:"0 auto",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <div style={{display:"flex",alignItems:"center",gap:"10px"}}>
            <span style={{fontSize:"1.8rem"}}>🌿</span>
            <div>
              <h1 style={{fontSize:"1rem",fontWeight:700,color:"#e4e4e7"}}>{store.restaurant_name}</h1>
              <p style={{fontSize:"0.75rem",color:"#71717a"}}>📍 {store.region}</p>
            </div>
          </div>
          {cart.length > 0 && step === "browse" && (
            <button onClick={()=>setStep("order")} style={{background:"#10b981",color:"#fff",border:"none",borderRadius:"9px",padding:"9px 16px",fontWeight:700,fontSize:"0.88rem",display:"flex",alignItems:"center",gap:"6px"}}>
              🛒 {cart.length} item{cart.length!==1?"s":""} · ₹{cartTotal.toFixed(2)}
            </button>
          )}
        </div>
      </header>

      <div style={{maxWidth:"700px",margin:"0 auto",padding:"20px"}}>

        {}
        <div style={{background:"rgba(245,158,11,0.1)",border:"1px solid rgba(245,158,11,0.3)",borderRadius:"12px",padding:"14px 18px",marginBottom:"20px",display:"flex",alignItems:"center",gap:"10px"}}>
          <span style={{fontSize:"1.4rem"}}>⏰</span>
          <div>
            <p style={{margin:0,fontWeight:600,fontSize:"0.9rem",color:"#fbbf24"}}>Closing Time Deals — Until {store.closing_time}</p>
            <p style={{margin:0,fontSize:"0.78rem",color:"#a1a1aa"}}>Up to {store.discount_pct}% off on remaining items · Pay on pickup</p>
          </div>
        </div>

        {}
        {step === "order" && (
          <div style={{background:"#0f1319",border:"1px solid rgba(255,255,255,0.08)",borderRadius:"16px",padding:"24px",marginBottom:"20px"}}>
            <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"16px"}}>
              <h2 style={{fontSize:"1rem",fontWeight:700}}>📋 Your Order</h2>
              <button onClick={()=>setStep("browse")} style={{background:"transparent",border:"1px solid rgba(255,255,255,0.1)",color:"#a1a1aa",borderRadius:"7px",padding:"5px 12px",fontSize:"0.8rem"}}>← Back</button>
            </div>

            {}
            {cart.map(c=>(
              <div key={c.item} style={{display:"flex",justifyContent:"space-between",alignItems:"center",padding:"10px 0",borderBottom:"1px solid rgba(255,255,255,0.06)"}}>
                <div>
                  <p style={{margin:0,fontWeight:600,fontSize:"0.88rem"}}>{c.item}</p>
                  <p style={{margin:0,fontSize:"0.75rem",color:"#71717a"}}>₹{c.unit_price_rm.toFixed(2)} × {c.qty}</p>
                </div>
                <div style={{display:"flex",alignItems:"center",gap:"10px"}}>
                  <span style={{fontWeight:700,color:"#10b981"}}>₹{(c.unit_price_rm*c.qty).toFixed(2)}</span>
                  <button onClick={()=>removeFromCart(c.item)} style={{background:"rgba(239,68,68,0.15)",border:"none",color:"#ef4444",borderRadius:"6px",padding:"4px 8px",fontSize:"0.75rem"}}>Remove</button>
                </div>
              </div>
            ))}

            <div style={{display:"flex",justifyContent:"space-between",padding:"14px 0",fontWeight:800,fontSize:"1rem"}}>
              <span>Total</span><span style={{color:"#10b981"}}>₹{cartTotal.toFixed(2)}</span>
            </div>

            {}
            <div style={{display:"flex",flexDirection:"column",gap:"10px",marginTop:"8px"}}>
              <input value={name} onChange={e=>setName(e.target.value)} placeholder="Your name *" required/>
              <input value={phone} onChange={e=>setPhone(e.target.value)} placeholder="Phone number *" type="tel" required/>
              <textarea value={notes} onChange={e=>setNotes(e.target.value)} placeholder="Pickup notes (optional)" rows={2} style={{resize:"none"}}/>
            </div>

            <button
              onClick={placeOrder}
              disabled={submitting||!name.trim()||!phone.trim()}
              style={{marginTop:"16px",width:"100%",padding:"14px",borderRadius:"9px",background:"#10b981",color:"#fff",border:"none",fontWeight:700,fontSize:"0.95rem",cursor:(submitting||!name.trim()||!phone.trim())?"not-allowed":"pointer",opacity:(submitting||!name.trim()||!phone.trim())?0.6:1}}
            >
              {submitting ? "Placing Order…" : `✅ Place Order · ₹${cartTotal.toFixed(2)}`}
            </button>
            <p style={{textAlign:"center",marginTop:"8px",fontSize:"0.75rem",color:"#71717a"}}>💳 Pay on pickup · No card required</p>
          </div>
        )}

        {}
        {step === "browse" && (
          <>
            {!store.has_stock || !store.marketplace_active ? (
              <div style={{textAlign:"center",padding:"60px 20px"}}>
                <div style={{fontSize:"4rem",marginBottom:"16px"}}>🏪</div>
                <h2 style={{fontSize:"1.2rem",fontWeight:700,marginBottom:"8px"}}>No Stock Available Right Now</h2>
                <p style={{color:"#a1a1aa",fontSize:"0.9rem"}}>Closing-time deals will appear here when {store.restaurant_name} posts remaining stock.</p>
                <p style={{color:"#71717a",fontSize:"0.8rem",marginTop:"8px"}}>Come back closer to closing time ({store.closing_time})</p>
              </div>
            ) : (
              <>
                <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"14px"}}>
                  <h2 style={{fontSize:"1rem",fontWeight:600}}>🍽️ Available Now ({store.closing_stock.length} items)</h2>
                  <span style={{fontSize:"0.78rem",color:"#71717a"}}>{store.total_orders_today} orders today</span>
                </div>

                <div style={{display:"flex",flexDirection:"column",gap:"12px"}}>
                  {store.closing_stock.map((item)=>{
                    const inCart = cart.find(c=>c.item===item.item);
                    return (
                      <div key={item.item} style={{background:"#0f1319",border:"1px solid rgba(255,255,255,0.08)",borderRadius:"14px",padding:"18px",display:"flex",justifyContent:"space-between",alignItems:"center",gap:"12px"}}>
                        <div style={{flex:1}}>
                          <div style={{display:"flex",alignItems:"center",gap:"8px",marginBottom:"4px"}}>
                            <h3 style={{fontSize:"1rem",fontWeight:700}}>{item.item}</h3>
                            <span style={{background:"rgba(245,158,11,0.2)",color:"#fbbf24",padding:"2px 8px",borderRadius:"20px",fontSize:"0.72rem",fontWeight:600}}>{item.discount_pct}% OFF</span>
                          </div>
                          <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                            <span style={{fontSize:"1.1rem",fontWeight:800,color:"#10b981"}}>₹{item.discounted_price_rm.toFixed(2)}</span>
                            <span style={{fontSize:"0.82rem",color:"#71717a",textDecoration:"line-through"}}>₹{item.original_price_rm.toFixed(2)}</span>
                          </div>
                          <p style={{fontSize:"0.75rem",color:"#71717a",marginTop:"4px"}}>
                            {item.qty_available} portion{item.qty_available!==1?"s":""} available
                          </p>
                        </div>

                        <div style={{flexShrink:0}}>
                          {inCart ? (
                            <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
                              <button onClick={()=>{ if(inCart.qty<=1){removeFromCart(item.item);}else{addToCart(item,inCart.qty-1);} }} style={{width:"32px",height:"32px",borderRadius:"50%",background:"rgba(255,255,255,0.08)",border:"none",color:"#e4e4e7",fontSize:"1.2rem",display:"flex",alignItems:"center",justifyContent:"center"}}>−</button>
                              <span style={{fontWeight:700,minWidth:"20px",textAlign:"center"}}>{inCart.qty}</span>
                              <button onClick={()=>addToCart(item,inCart.qty+1)} disabled={inCart.qty>=item.qty_available} style={{width:"32px",height:"32px",borderRadius:"50%",background:"rgba(16,185,129,0.2)",border:"none",color:"#10b981",fontSize:"1.2rem",display:"flex",alignItems:"center",justifyContent:"center",opacity:inCart.qty>=item.qty_available?0.4:1}}>+</button>
                            </div>
                          ) : (
                            <button onClick={()=>addToCart(item,1)} style={{padding:"9px 18px",borderRadius:"9px",background:"rgba(16,185,129,0.15)",border:"1px solid rgba(16,185,129,0.4)",color:"#10b981",fontWeight:700,fontSize:"0.88rem"}}>
                              Add
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>

                {}
                {cart.length > 0 && (
                  <div style={{position:"fixed",bottom:"20px",left:"50%",transform:"translateX(-50%)",width:"calc(100% - 40px)",maxWidth:"660px",background:"#10b981",borderRadius:"14px",padding:"16px 20px",display:"flex",justifyContent:"space-between",alignItems:"center",boxShadow:"0 8px 40px rgba(16,185,129,0.4)",zIndex:90}}>
                    <div>
                      <p style={{margin:0,fontWeight:700,color:"#fff",fontSize:"0.95rem"}}>{cart.reduce((a,c)=>a+c.qty,0)} item{cart.reduce((a,c)=>a+c.qty,0)!==1?"s":""} in cart</p>
                      <p style={{margin:0,fontSize:"0.8rem",color:"rgba(255,255,255,0.8)"}}>₹{cartTotal.toFixed(2)} total</p>
                    </div>
                    <button onClick={()=>setStep("order")} style={{background:"#fff",color:"#10b981",border:"none",borderRadius:"9px",padding:"10px 20px",fontWeight:800,fontSize:"0.9rem"}}>
                      Checkout →
                    </button>
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function CustomerPage() {
  return (
    <Suspense fallback={
      <div style={{minHeight:"100vh",display:"flex",alignItems:"center",justifyContent:"center",background:"#0a0d12",color:"#a1a1aa"}}>
        Loading store…
      </div>
    }>
      <CustomerPageInner/>
    </Suspense>
  );
}
