"use client";
import React, { useState, useEffect, useCallback } from "react";
import { ShoppingBag, CheckCircle, XCircle, Clock, RefreshCw, Loader2 } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type OrderItem = { item: string; qty: number; unit_price_rm: number; line_total_rm: number };
type Order = {
  order_id: string;
  order_num?: number;
  date: string;
  created_at: string;
  customer_name: string;
  phone: string;
  items: OrderItem[];
  total_rm: number;
  shopkeeper_earnings_rm: number;
  pickup_notes?: string;
  status: "pending" | "accepted" | "completed" | "cancelled";
  cancel_reason?: string;
  updated_at?: string;
};

export default function OrdersPanel({ restaurantId, token }: { restaurantId: string; token: string }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [revenue, setRevenue] = useState(0);
  const [statusMsg, setStatusMsg] = useState("");
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | "pending" | "accepted" | "completed" | "cancelled">("all");

  const fetchOrders = useCallback(async () => {
    setLoading(true);
    try {
      const ts = Date.now();
      const r = await fetch(`${API}/api/restaurant/${restaurantId}/orders?t=${ts}`, {
        headers: { Authorization: `Bearer ${token}` },
        cache: "no-store"
      });
      if (r.ok) {
        const j = await r.json();
        setOrders(j.orders || []);
        setRevenue(j.shopkeeper_earnings_rm || 0);
      }
    } catch {}
    setLoading(false);
  }, [restaurantId, token]);

  useEffect(() => { fetchOrders(); }, [fetchOrders]);
  useEffect(() => {
    const i = setInterval(fetchOrders, 30_000);
    return () => clearInterval(i);
  }, [fetchOrders]);
  
  useEffect(() => {
    const handler = () => fetchOrders();
    window.addEventListener('refresh_orders', handler);
    return () => window.removeEventListener('refresh_orders', handler);
  }, [fetchOrders]);

  const updateStatus = async (orderRef: string, status: string, cancelReason?: string) => {
    setUpdatingId(orderRef);
    try {
      const url = `${API}/api/restaurant/${restaurantId}/orders/${orderRef}?status=${status}${cancelReason ? `&cancel_reason=${encodeURIComponent(cancelReason)}` : ""}`;
      const r = await fetch(url, { method: "PATCH", headers: { Authorization: `Bearer ${token}` } } );
      if (r.ok) {
        setStatusMsg(`✅ Order updated to ${status}`);
        fetchOrders();
      } else {
        const e = await r.json().catch(() => ({}));
        setStatusMsg("❌ " + (e.detail || "Could not update order."));
      }
    } catch {
      setStatusMsg("❌ Network error.");
    }
    setUpdatingId(null);
    setTimeout(() => setStatusMsg(""), 3000);
  };

  const filtered = orders.filter(o => filter === "all" || o.status === filter);
  const pending  = orders.filter(o => o.status === "pending").length;
  const done     = orders.filter(o => o.status === "completed").length;

  const statusColor = (s: string) =>
    s === "completed" ? "var(--green)" : s === "cancelled" ? "#ef4444" : "#f59e0b";
  const statusEmoji = (s: string) =>
    s === "completed" ? "✅" : s === "cancelled" ? "❌" : "⏳";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: "10px" }}>
        {[
          { label: "Total Orders", value: orders.length, color: "var(--txt)" },
          { label: "⏳ Pending", value: pending, color: "#f59e0b" },
          { label: "✅ Completed", value: done, color: "var(--green)" },
          { label: "💰 Your Earnings", value: `₹${revenue.toFixed(2)}`, color: "var(--green)" },
        ].map(c => (
          <div key={c.label} style={{ background: "var(--input)", borderRadius: "10px", padding: "14px 16px" }}>
            <p style={{ margin: 0, fontSize: "0.75rem", color: "var(--txt3)" }}>{c.label}</p>
            <p style={{ margin: "4px 0 0", fontSize: "1.2rem", fontWeight: 800, color: c.color }}>{c.value}</p>
          </div>
        ))}
      </div>

      {}
      <div style={{ padding: "10px 14px", background: "rgba(16,185,129,0.08)", borderRadius: "9px", border: "1px solid rgba(16,185,129,0.2)", fontSize: "0.78rem", color: "var(--txt2)" }}>
        💡 <strong>Telegram tip:</strong> Type <code>done 5</code> or <code>miss 5</code> to update order #5 directly from your bot. Order numbers reset daily.
      </div>

      {statusMsg && (
        <div style={{ padding: "10px 14px", borderRadius: "8px", background: statusMsg.startsWith("✅") ? "rgba(16,185,129,0.1)" : "rgba(239,68,68,0.1)", color: statusMsg.startsWith("✅") ? "var(--green)" : "#ef4444", fontSize: "0.83rem" }}>
          {statusMsg}
        </div>
      )}

      {}
      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", alignItems: "center" }}>
        {(["all", "pending", "accepted", "completed", "cancelled"] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            style={{ padding: "6px 14px", borderRadius: "20px", border: "1px solid var(--bdr)", background: filter === f ? "var(--green)" : "var(--input)", color: filter === f ? "#000" : "var(--txt3)", fontWeight: filter === f ? 700 : 400, fontSize: "0.8rem", cursor: "pointer", textTransform: "capitalize" }}>
            {f}
          </button>
        ))}
        <button onClick={fetchOrders} style={{ marginLeft: "auto", padding: "6px 12px", borderRadius: "20px", border: "1px solid var(--bdr)", background: "var(--input)", color: "var(--txt3)", cursor: "pointer", display: "flex", alignItems: "center", gap: "5px", fontSize: "0.8rem" }}>
          <RefreshCw size={12} /> Refresh
        </button>
      </div>

      {}
      {loading ? (
        <div style={{ textAlign: "center", padding: "32px", color: "var(--txt3)" }}>
          <Loader2 size={28} style={{ animation: "spin 1s linear infinite", marginBottom: "8px" }} />
          <p style={{ margin: 0, fontSize: "0.85rem" }}>Loading orders…</p>
        </div>
      ) : filtered.length === 0 ? (
        <div style={{ textAlign: "center", padding: "40px 20px", color: "var(--txt3)" }}>
          <ShoppingBag size={40} style={{ opacity: 0.3, marginBottom: "12px" }} />
          <p style={{ margin: 0, fontSize: "0.9rem" }}>No {filter !== "all" ? filter : ""} orders today yet.</p>
          <p style={{ margin: "6px 0 0", fontSize: "0.78rem" }}>Share your store link to start receiving orders!</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {filtered.map(order => (
            <div key={order.order_id} style={{
              background: "var(--input)", borderRadius: "12px", padding: "16px",
              border: `1px solid ${order.status === "pending" ? "rgba(245,158,11,0.3)" : order.status === "completed" ? "rgba(16,185,129,0.2)" : "rgba(239,68,68,0.2)"}`,
              position: "relative"
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "10px" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    {order.order_num && (
                      <span style={{ background: "var(--green)", color: "#000", fontWeight: 800, fontSize: "0.75rem", padding: "2px 8px", borderRadius: "20px" }}>
                        #{order.order_num}
                      </span>
                    )}
                    <p style={{ margin: 0, fontSize: "0.9rem", fontWeight: 700 }}>{order.customer_name}</p>
                  </div>
                  <p style={{ margin: "3px 0 0", fontSize: "0.78rem", color: "var(--txt3)" }}>
                    📱 {order.phone} · {new Date(order.created_at).toLocaleTimeString("en-MY", { hour: "2-digit", minute: "2-digit" })}
                  </p>
                </div>
                <div style={{ textAlign: "right" }}>
                  <span style={{ fontSize: "0.78rem", color: statusColor(order.status), fontWeight: 700 }}>
                    {statusEmoji(order.status)} {order.status.toUpperCase()}
                  </span>
                  <p style={{ margin: "2px 0 0", fontSize: "0.88rem", fontWeight: 800, color: "var(--green)" }}>
                    ₹{order.total_rm.toFixed(2)}
                  </p>
                </div>
              </div>

              {}
              <div style={{ background: "var(--card)", borderRadius: "8px", padding: "10px 12px", marginBottom: "10px" }}>
                {order.items.map((it, i) => (
                  <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: "0.82rem", padding: "2px 0" }}>
                    <span>{it.qty}× {it.item}</span>
                    <span style={{ color: "var(--txt2)" }}>₹{it.line_total_rm.toFixed(2)}</span>
                  </div>
                ))}
                {order.pickup_notes && (
                  <p style={{ margin: "6px 0 0", fontSize: "0.75rem", color: "#f59e0b" }}>📝 {order.pickup_notes}</p>
                )}
              </div>

              {}
              {order.status === "pending" && (
                <div style={{ display: "flex", gap: "8px" }}>
                  <button
                    onClick={() => updateStatus(order.order_num ? String(order.order_num) : order.order_id, "accepted")}
                    disabled={updatingId === order.order_id}
                    style={{ flex: 1, padding: "9px", borderRadius: "8px", background: "rgba(16,185,129,0.15)", color: "var(--green)", border: "1px solid rgba(16,185,129,0.35)", cursor: "pointer", fontWeight: 700, fontSize: "0.82rem", display: "flex", alignItems: "center", justifyContent: "center", gap: "5px" }}
                  >
                    {updatingId === order.order_id ? "..." : "✓ Accept"}
                  </button>
                  <button
                    onClick={() => {
                      const reason = "Shopkeeper rejected";
                      updateStatus(order.order_num ? String(order.order_num) : order.order_id, "cancelled", reason);
                    }}
                    disabled={updatingId === order.order_id}
                    style={{ flex: 1, padding: "9px", borderRadius: "8px", background: "rgba(239,68,68,0.1)", color: "#ef4444", border: "1px solid rgba(239,68,68,0.3)", cursor: "pointer", fontWeight: 700, fontSize: "0.82rem", display: "flex", alignItems: "center", justifyContent: "center", gap: "5px" }}
                  >
                    ✕ Reject
                  </button>
                </div>
              )}

              {order.status === "accepted" && (
                <div style={{ display: "flex", gap: "8px" }}>
                  <button
                    onClick={() => updateStatus(order.order_num ? String(order.order_num) : order.order_id, "completed")}
                    disabled={updatingId === order.order_id}
                    style={{ flex: 1, padding: "9px", borderRadius: "8px", background: "rgba(16,185,129,0.15)", color: "var(--green)", border: "1px solid rgba(16,185,129,0.35)", cursor: "pointer", fontWeight: 700, fontSize: "0.82rem", display: "flex", alignItems: "center", justifyContent: "center", gap: "5px" }}
                  >
                    {updatingId === order.order_id ? "..." : "✔ Collected"}
                  </button>
                  <button
                    onClick={() => {
                      const reason = "Customer didn't pick up";
                      updateStatus(order.order_num ? String(order.order_num) : order.order_id, "cancelled", reason);
                    }}
                    disabled={updatingId === order.order_id}
                    style={{ flex: 1, padding: "9px", borderRadius: "8px", background: "rgba(245,158,11,0.15)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.35)", cursor: "pointer", fontWeight: 700, fontSize: "0.82rem", display: "flex", alignItems: "center", justifyContent: "center", gap: "5px" }}
                  >
                    ⚠ Not Picked Up
                  </button>
                </div>
              )}

              {order.status === "cancelled" && order.cancel_reason && (
                <div style={{ marginTop: "10px", padding: "8px", borderRadius: "6px", background: "rgba(239,68,68,0.05)", border: "1px solid rgba(239,68,68,0.2)", fontSize: "0.8rem", color: "#ef4444" }}>
                  <strong>Reason:</strong> {order.cancel_reason}
                </div>
              )}
              {order.status !== "pending" && (
                <button
                  onClick={() => updateStatus(order.order_num ? String(order.order_num) : order.order_id, "pending")}
                  style={{ width: "100%", marginTop: "10px", padding: "8px", borderRadius: "8px", background: "transparent", color: "var(--txt3)", border: "1px solid var(--bdr)", cursor: "pointer", fontSize: "0.78rem" }}
                >
                  <Clock size={12} style={{ marginRight: "5px" }} /> Undo — Mark as Pending
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
