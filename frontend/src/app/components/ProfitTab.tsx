"use client";
import React, { useState, useEffect } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line } from "recharts";
import { TrendingUp, ShoppingBag } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface ProfitTabProps {
  restaurantId: string;
  token: string;
}

export default function ProfitTab({ restaurantId, token }: ProfitTabProps) {
  const [data, setData]       = useState<any>(null);
  const [orders, setOrders]   = useState<any>(null);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const [profitRes, ordersRes] = await Promise.all([
        fetch(`${API}/api/dashboard/${restaurantId}/profit`, { headers:{"Authorization":`Bearer ${token}`} }),
        fetch(`${API}/api/restaurant/${restaurantId}/orders`, { headers:{"Authorization":`Bearer ${token}`} }),
      ]);
      if (profitRes.ok) setData(await profitRes.json());
      if (ordersRes.ok) setOrders(await ordersRes.json());
    } catch {}
    setLoading(false);
  };

  useEffect(() => { load(); }, [restaurantId]);

  const updateOrderStatus = async (orderId: string, status: string, cancelReason?: string) => {
    const url = `${API}/api/restaurant/${restaurantId}/orders/${orderId}?status=${status}${cancelReason ? `&cancel_reason=${encodeURIComponent(cancelReason)}` : ""}`;
    await fetch(url, { method:"PATCH", headers:{"Authorization":`Bearer ${token}`} });
    load();
  };

  if (loading) return <div style={{padding:"40px",textAlign:"center",color:"var(--txt3)"}}>Loading profit data…</div>;

  const today = data?.today;
  const weekly = data?.weekly || [];

  const statCard = (label: string, value: string, sub?: string, color = "var(--green)") => (
    <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"18px"}}>
      <p style={{margin:"0 0 4px",fontSize:"0.78rem",color:"var(--txt3)"}}>{label}</p>
      <p style={{margin:"0 0 2px",fontSize:"1.5rem",fontWeight:800,color}}>{value}</p>
      {sub && <p style={{margin:0,fontSize:"0.73rem",color:"var(--txt3)"}}>{sub}</p>}
    </div>
  );

  return (
    <div style={{display:"flex",flexDirection:"column",gap:"18px"}}>

      {}
      {today && (
        <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(160px,1fr))",gap:"14px"}}>
          {statCard("💰 Your Earnings Today", `₹${today.shopkeeper_earnings_rm?.toFixed(2) ?? "0.00"}`, "Regular + Marketplace")}
          {statCard("🛍️ Marketplace Revenue", `₹${today.marketplace_revenue_rm?.toFixed(2) ?? "0.00"}`, `${today.total_orders ?? 0} orders`, "#f59e0b")}
          {statCard("📊 Regular Sales", `₹${today.regular_sales_rm?.toFixed(2) ?? "0.00"}`, "From today's sales data")}
          {statCard("🏦 Platform Fee (10%)", `₹${today.platform_fee_rm?.toFixed(2) ?? "0.00"}`, "WasteWise AI platform share", "#6366f1")}
        </div>
      )}

      {}
      {weekly.length > 0 && (
        <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"20px"}}>
          <div style={{display:"flex",alignItems:"center",gap:"8px",marginBottom:"16px"}}>
            <TrendingUp size={18} color="var(--green)"/>
            <h2 style={{margin:0,fontSize:"0.95rem",fontWeight:600}}>7-Day Earnings Breakdown</h2>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={weekly}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" vertical={false}/>
              <XAxis dataKey="weekday" stroke="var(--txt3)" tick={{fill:"var(--txt3)",fontSize:11}} axisLine={false}/>
              <YAxis stroke="var(--txt3)" tick={{fill:"var(--txt3)",fontSize:11}} axisLine={false} tickLine={false}/>
              <Tooltip contentStyle={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"8px"}} itemStyle={{color:"var(--txt)"}} labelStyle={{color:"var(--txt2)"}} formatter={(v:any)=>[`₹${Number(v).toFixed(2)}`]}/>
              <Bar dataKey="regular_profit_rm" stackId="a" fill="var(--green)" name="Regular Sales" radius={[0,0,0,0]}/>
              <Bar dataKey="marketplace_profit_rm" stackId="a" fill="#f59e0b" name="Marketplace" radius={[4,4,0,0]}/>
            </BarChart>
          </ResponsiveContainer>
          <div style={{display:"flex",gap:"16px",justifyContent:"center",marginTop:"8px"}}>
            {[{color:"var(--green)",label:"Regular Sales"},{color:"#f59e0b",label:"Marketplace"}].map(l=>(
              <div key={l.label} style={{display:"flex",alignItems:"center",gap:"5px"}}>
                <div style={{width:"10px",height:"10px",borderRadius:"2px",background:l.color}}/>
                <span style={{fontSize:"0.75rem",color:"var(--txt3)"}}>{l.label}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {}
      {orders && (
        <div style={{background:"var(--card)",border:"1px solid var(--bdr)",borderRadius:"12px",padding:"20px"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:"14px"}}>
            <div style={{display:"flex",alignItems:"center",gap:"8px"}}>
              <ShoppingBag size={18} color="var(--green)"/>
              <h2 style={{margin:0,fontSize:"0.95rem",fontWeight:600}}>Today's Customer Orders ({orders.total_orders ?? 0})</h2>
            </div>
            <button onClick={load} style={{background:"transparent",border:"1px solid var(--bdr)",color:"var(--txt3)",padding:"5px 10px",borderRadius:"7px",cursor:"pointer",fontSize:"0.78rem"}}>↻ Refresh</button>
          </div>

          {(!orders.orders || orders.orders.length === 0) ? (
            <div style={{textAlign:"center",padding:"28px",color:"var(--txt3)"}}>
              <p style={{fontSize:"2rem",margin:"0 0 8px"}}>🛍️</p>
              <p style={{margin:0,fontSize:"0.85rem"}}>No customer orders yet today.</p>
              <p style={{margin:"4px 0 0",fontSize:"0.78rem"}}>Set up your closing time to enable the customer marketplace.</p>
            </div>
          ) : (
            <div style={{display:"flex",flexDirection:"column",gap:"10px"}}>
              {orders.orders.map((o: any) => {
                const statusColors: Record<string,string> = {pending:"#f59e0b",accepted:"#3b82f6",completed:"var(--green)",cancelled:"#ef4444"};
                const col = statusColors[o.status] ?? "var(--txt2)";
                return (
                  <div key={o.order_id} style={{background:"var(--input)",borderRadius:"10px",padding:"14px",border:`1px solid ${col}30`}}>
                    <div style={{display:"flex",justifyContent:"space-between",alignItems:"flex-start",marginBottom:"8px"}}>
                      <div>
                        <p style={{margin:"0 0 2px",fontWeight:600,fontSize:"0.88rem"}}>👤 {o.customer_name}</p>
                        <p style={{margin:"0 0 4px",fontSize:"0.78rem",color:"var(--txt3)"}}>📞 {o.phone}</p>
                        <p style={{margin:0,fontSize:"0.82rem",color:"var(--txt2)"}}>
                          {o.items?.map((i:any)=>`${i.qty}× ${i.item}`).join(", ")}
                        </p>
                        {o.pickup_notes && <p style={{margin:"4px 0 0",fontSize:"0.75rem",color:"var(--txt3)"}}>📝 {o.pickup_notes}</p>}
                      </div>
                      <div style={{textAlign:"right",flexShrink:0,marginLeft:"12px"}}>
                        <p style={{margin:"0 0 4px",fontWeight:700,fontSize:"0.95rem",color:"var(--green)"}}>₹{o.total_rm?.toFixed(2)}</p>
                        <span style={{background:`${col}20`,color:col,padding:"2px 8px",borderRadius:"20px",fontSize:"0.72rem",fontWeight:600}}>
                          {o.status}
                        </span>
                      </div>
                    </div>
                    {o.status === "pending" && (
                      <div style={{display:"flex",gap:"8px",marginTop:"8px"}}>
                        <button onClick={()=>updateOrderStatus(o.order_id,"accepted")} style={{flex:1,padding:"7px",borderRadius:"7px",background:"rgba(16,185,129,0.15)",border:"1px solid rgba(16,185,129,0.4)",color:"var(--green)",cursor:"pointer",fontWeight:600,fontSize:"0.8rem"}}>✓ Accept</button>
                        <button onClick={()=>updateOrderStatus(o.order_id,"cancelled", "Shopkeeper rejected")} style={{flex:1,padding:"7px",borderRadius:"7px",background:"rgba(239,68,68,0.1)",border:"1px solid rgba(239,68,68,0.3)",color:"#ef4444",cursor:"pointer",fontWeight:600,fontSize:"0.8rem"}}>✕ Reject</button>
                      </div>
                    )}
                    {o.status === "accepted" && (
                      <div style={{display:"flex",gap:"8px",marginTop:"8px"}}>
                        <button onClick={()=>updateOrderStatus(o.order_id,"completed")} style={{flex:1,padding:"7px",borderRadius:"7px",background:"rgba(16,185,129,0.15)",border:"1px solid rgba(16,185,129,0.4)",color:"var(--green)",cursor:"pointer",fontWeight:600,fontSize:"0.8rem"}}>✔ Collected</button>
                        <button onClick={()=>updateOrderStatus(o.order_id,"cancelled", "Customer didn't pick up")} style={{flex:1,padding:"7px",borderRadius:"7px",background:"rgba(245,158,11,0.15)",border:"1px solid rgba(245,158,11,0.35)",color:"#f59e0b",cursor:"pointer",fontWeight:600,fontSize:"0.8rem"}}>⚠ Not Picked Up</button>
                      </div>
                    )}
                    {o.status === "cancelled" && o.cancel_reason && (
                      <div style={{marginTop:"8px",fontSize:"0.75rem",color:"#ef4444",padding:"4px 8px",background:"rgba(239,68,68,0.05)",borderRadius:"4px"}}>
                        <strong>Reason:</strong> {o.cancel_reason}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {orders.total_orders > 0 && (
            <div style={{marginTop:"14px",paddingTop:"14px",borderTop:"1px solid var(--bdr)",display:"flex",justifyContent:"space-between",fontSize:"0.85rem"}}>
              <span style={{color:"var(--txt2)"}}>Total Revenue: <b style={{color:"var(--green)"}}>₹{orders.total_revenue_rm?.toFixed(2)}</b></span>
              <span style={{color:"var(--txt2)"}}>Your Share (90%): <b style={{color:"var(--green)"}}>₹{orders.shopkeeper_earnings_rm?.toFixed(2)}</b></span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
