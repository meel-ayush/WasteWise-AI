"use client";
import React, { useState, useEffect, useCallback } from "react";
import { Link2, Plus, Trash2, ChevronRight, Loader2, Building2, GitBranch } from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Branch = { id: string; name: string; region: string };
type Chain = {
  chain_id: string;
  chain_name: string;
  chain_type: string;
  branch_count: number;
  branches: Branch[];
  created_at: string;
};

const CHAIN_TYPES = [
  { value: "franchise", label: "🏪 Franchise", desc: "Same brand, multiple locations" },
  { value: "multi_brand", label: "🎨 Multi-Brand", desc: "Different brands, one owner" },
  { value: "food_court", label: "🍱 Food Court", desc: "Multiple stalls in one place" },
];

export default function ChainsPanel({
  restaurantId, token, email,
}: {
  restaurantId: string; token: string; email: string;
}) {
  const [chains, setChains] = useState<Chain[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusMsg, setStatusMsg] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [chainName, setChainName] = useState("");
  const [chainType, setChainType] = useState("franchise");
  const [creating, setCreating] = useState(false);
  const [approvalToken, setApprovalToken] = useState<string | null>(null);
  const [approvalStatus, setApprovalStatus] = useState<"pending" | "approved" | "denied" | null>(null);
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  const fetchChains = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${API}/api/chains`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.ok) setChains((await r.json()).chains || []);
    } catch {}
    setLoading(false);
  }, [token]);

  useEffect(() => { fetchChains(); }, [fetchChains]);

  
  useEffect(() => {
    if (!approvalToken) return;
    const i = setInterval(async () => {
      try {
        const r = await fetch(`${API}/api/auth/dashboard_action/status/${approvalToken}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (r.ok) {
          const j = await r.json();
          if (j.status === "approved") {
            setApprovalStatus("approved");
            clearInterval(i);
            setApprovalToken(null);
            
            if (pendingAction === "create_chain") doCreateChain();
          } else if (j.status === "denied") {
            setApprovalStatus("denied");
            clearInterval(i);
            setApprovalToken(null);
            setStatusMsg("❌ Action denied by primary Telegram account.");
          }
        }
      } catch {}
    }, 3000);
    return () => clearInterval(i);
    
  }, [approvalToken]);

  const requestApproval = async (action: string, extra?: Record<string, string>) => {
    setPendingAction(action);
    setApprovalStatus("pending");
    const params = new URLSearchParams({ action, ...(extra || {}) });
    try {
      const r = await fetch(`${API}/api/auth/dashboard_action/request?${params}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (r.ok) {
        const j = await r.json();
        setApprovalToken(j.approval_token);
        setStatusMsg("⏳ Approval request sent to your primary Telegram. Check your bot!");
      } else {
        const e = await r.json().catch(() => ({}));
        setStatusMsg("❌ " + (e.detail || "Could not request approval."));
        setApprovalStatus(null);
      }
    } catch {
      setStatusMsg("❌ Network error.");
      setApprovalStatus(null);
    }
  };

  const doCreateChain = async () => {
    if (!chainName.trim()) return;
    setCreating(true);
    try {
      const r = await fetch(`${API}/api/chains`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: JSON.stringify({ chain_name: chainName.trim(), chain_type: chainType }),
      });
      if (r.ok) {
        const j = await r.json();
        setStatusMsg(`✅ Chain "${j.chain?.name || chainName}" created!`);
        setShowCreate(false);
        setChainName("");
        setApprovalStatus(null);
        fetchChains();
      } else {
        const e = await r.json().catch(() => ({}));
        setStatusMsg("❌ " + (e.detail || "Could not create chain."));
        setApprovalStatus(null);
      }
    } catch {
      setStatusMsg("❌ Network error.");
      setApprovalStatus(null);
    }
    setCreating(false);
  };

  const handleCreateChain = async () => {
    if (!chainName.trim()) { setStatusMsg("⚠️ Enter a chain name first."); return; }
    
    await requestApproval("create_chain");
  };

  const addThisRestaurantToChain = async (chainId: string) => {
    await requestApproval("add_branch", { chain_id: chainId });
    
    
    setPendingAction(`add_branch:${chainId}`);
  };

  useEffect(() => {
    if (approvalStatus === "approved" && pendingAction?.startsWith("add_branch:")) {
      const chainId = pendingAction.split(":")[1];
      (async () => {
        const r = await fetch(`${API}/api/chains/${chainId}/branches`, {
          method: "POST",
          headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
          body: JSON.stringify({ restaurant_id: restaurantId }),
        });
        if (r.ok) {
          setStatusMsg("✅ Restaurant added to chain!");
          fetchChains();
        } else {
          const e = await r.json().catch(() => ({}));
          setStatusMsg("❌ " + (e.detail || "Could not add branch."));
        }
        setApprovalStatus(null);
        setPendingAction(null);
      })();
    }
    
  }, [approvalStatus]);

  const typeLabel = (t: string) => CHAIN_TYPES.find(x => x.value === t)?.label || t;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {statusMsg && (
        <div style={{
          padding: "10px 14px", borderRadius: "9px",
          background: statusMsg.startsWith("✅") ? "rgba(16,185,129,0.1)" : statusMsg.startsWith("⏳") ? "rgba(245,158,11,0.1)" : "rgba(239,68,68,0.1)",
          color: statusMsg.startsWith("✅") ? "var(--green)" : statusMsg.startsWith("⏳") ? "#f59e0b" : "#ef4444",
          border: `1px solid ${statusMsg.startsWith("✅") ? "rgba(16,185,129,0.3)" : statusMsg.startsWith("⏳") ? "rgba(245,158,11,0.3)" : "rgba(239,68,68,0.3)"}`,
          fontSize: "0.83rem", display: "flex", justifyContent: "space-between", alignItems: "center"
        }}>
          {statusMsg}
          <button onClick={() => setStatusMsg("")} style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", fontSize: "1rem" }}>✕</button>
        </div>
      )}

      {approvalStatus === "pending" && (
        <div style={{ padding: "14px", borderRadius: "10px", background: "rgba(245,158,11,0.1)", border: "1px solid rgba(245,158,11,0.3)", display: "flex", alignItems: "center", gap: "10px" }}>
          <Loader2 size={18} color="#f59e0b" style={{ animation: "spin 1s linear infinite", flexShrink: 0 }} />
          <div>
            <p style={{ margin: 0, fontSize: "0.85rem", fontWeight: 700, color: "#f59e0b" }}>Awaiting Telegram Approval</p>
            <p style={{ margin: "2px 0 0", fontSize: "0.75rem", color: "var(--txt2)" }}>Open your WasteWise Telegram bot and type <strong>approve [code]</strong> to proceed.</p>
          </div>
        </div>
      )}

      {}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <h3 style={{ margin: 0, fontSize: "0.95rem", fontWeight: 700 }}>🔗 Restaurant Chains</h3>
          <p style={{ margin: "3px 0 0", fontSize: "0.78rem", color: "var(--txt3)" }}>Group your restaurants into chains for consolidated reporting.</p>
        </div>
        <button
          onClick={() => { setShowCreate(!showCreate); setStatusMsg(""); }}
          style={{ display: "flex", alignItems: "center", gap: "5px", padding: "8px 14px", borderRadius: "8px", background: "rgba(16,185,129,0.15)", color: "var(--green)", border: "1px solid rgba(16,185,129,0.35)", cursor: "pointer", fontWeight: 700, fontSize: "0.82rem" }}
        >
          <Plus size={14} /> New Chain
        </button>
      </div>

      {}
      {showCreate && (
        <div style={{ background: "var(--input)", borderRadius: "12px", padding: "18px", border: "1px solid var(--bdr)" }}>
          <p style={{ margin: "0 0 12px", fontSize: "0.88rem", fontWeight: 700 }}>Create New Chain</p>
          <p style={{ margin: "0 0 12px", fontSize: "0.78rem", color: "var(--txt3)", lineHeight: 1.5 }}>
            ⚠️ Requires approval from your primary Telegram account. You'll receive a message in your bot.
          </p>
          <input
            value={chainName}
            onChange={e => setChainName(e.target.value)}
            placeholder="e.g. Aggarwal Sweets Group"
            style={{ width: "100%", padding: "11px", borderRadius: "8px", background: "var(--card)", color: "var(--txt)", border: "1px solid var(--bdr)", fontSize: "0.85rem", outline: "none", marginBottom: "10px", boxSizing: "border-box" }}
          />
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "8px", marginBottom: "14px" }}>
            {CHAIN_TYPES.map(ct => (
              <button key={ct.value} onClick={() => setChainType(ct.value)}
                style={{ padding: "10px 8px", borderRadius: "9px", border: `1px solid ${chainType === ct.value ? "var(--green)" : "var(--bdr)"}`, background: chainType === ct.value ? "rgba(16,185,129,0.12)" : "var(--card)", color: chainType === ct.value ? "var(--green)" : "var(--txt3)", cursor: "pointer", fontSize: "0.78rem", textAlign: "center" }}>
                <p style={{ margin: 0, fontWeight: 700 }}>{ct.label}</p>
                <p style={{ margin: "3px 0 0", fontSize: "0.7rem", opacity: 0.8 }}>{ct.desc}</p>
              </button>
            ))}
          </div>
          <button
            onClick={handleCreateChain}
            disabled={creating || !chainName.trim() || approvalStatus === "pending"}
            style={{ width: "100%", padding: "12px", borderRadius: "9px", background: "rgba(16,185,129,0.15)", color: "var(--green)", border: "1px solid rgba(16,185,129,0.35)", cursor: "pointer", fontWeight: 700, fontSize: "0.85rem", opacity: (creating || !chainName.trim() || approvalStatus === "pending") ? 0.5 : 1 }}>
            {creating ? "Creating…" : "🔗 Create Chain (Requires Telegram Approval)"}
          </button>
        </div>
      )}

      {}
      {loading ? (
        <div style={{ textAlign: "center", padding: "32px", color: "var(--txt3)" }}>
          <Loader2 size={28} style={{ animation: "spin 1s linear infinite", marginBottom: "8px" }} />
          <p style={{ margin: 0, fontSize: "0.85rem" }}>Loading chains…</p>
        </div>
      ) : chains.length === 0 ? (
        <div style={{ textAlign: "center", padding: "40px 20px", background: "var(--input)", borderRadius: "12px" }}>
          <Link2 size={40} style={{ opacity: 0.3, marginBottom: "12px", color: "var(--green)" }} />
          <p style={{ margin: 0, fontSize: "0.9rem", fontWeight: 600 }}>No chains yet</p>
          <p style={{ margin: "6px 0 0", fontSize: "0.78rem", color: "var(--txt3)" }}>
            Create a chain to group multiple restaurants for combined reporting.
          </p>
          <p style={{ margin: "8px 0 0", fontSize: "0.78rem", color: "var(--txt3)" }}>
            Or on Telegram: <code style={{ background: "var(--card)", padding: "2px 6px", borderRadius: "4px" }}>create chain My Group</code>
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {chains.map(chain => (
            <div key={chain.chain_id} style={{ background: "var(--input)", borderRadius: "12px", padding: "16px", border: "1px solid var(--bdr)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "12px" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <Building2 size={16} color="var(--green)" />
                    <p style={{ margin: 0, fontSize: "0.95rem", fontWeight: 700 }}>{chain.chain_name}</p>
                    <span style={{ fontSize: "0.72rem", background: "rgba(16,185,129,0.15)", color: "var(--green)", padding: "2px 8px", borderRadius: "20px" }}>
                      {typeLabel(chain.chain_type)}
                    </span>
                  </div>
                  <p style={{ margin: "4px 0 0", fontSize: "0.75rem", color: "var(--txt3)" }}>
                    ID: <code style={{ background: "var(--card)", padding: "1px 5px", borderRadius: "3px" }}>{chain.chain_id}</code>
                  </p>
                </div>
                <span style={{ background: "var(--card)", padding: "4px 10px", borderRadius: "20px", fontSize: "0.75rem", color: "var(--txt2)" }}>
                  {chain.branch_count} branch{chain.branch_count !== 1 ? "es" : ""}
                </span>
              </div>

              {}
              {chain.branches.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginBottom: "12px" }}>
                  {chain.branches.map(b => (
                    <div key={b.id} style={{ display: "flex", alignItems: "center", gap: "8px", padding: "8px 10px", background: "var(--card)", borderRadius: "8px" }}>
                      <GitBranch size={12} color="var(--txt3)" />
                      <span style={{ fontSize: "0.82rem", fontWeight: 500 }}>{b.name}</span>
                      <span style={{ fontSize: "0.75rem", color: "var(--txt3)" }}>{b.region}</span>
                      {b.id === restaurantId && (
                        <span style={{ marginLeft: "auto", fontSize: "0.7rem", color: "var(--green)", fontWeight: 700 }}>← THIS RESTAURANT</span>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {}
              {!chain.branches.some(b => b.id === restaurantId) && (
                <button
                  onClick={() => addThisRestaurantToChain(chain.chain_id)}
                  disabled={approvalStatus === "pending"}
                  style={{ width: "100%", padding: "9px", borderRadius: "8px", background: "transparent", color: "var(--txt3)", border: "1px dashed var(--bdr)", cursor: "pointer", fontSize: "0.78rem", display: "flex", alignItems: "center", justifyContent: "center", gap: "5px" }}>
                  <Plus size={12} /> Add this restaurant to chain (needs Telegram approval)
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {}
      <div style={{ padding: "12px 14px", background: "rgba(16,185,129,0.06)", borderRadius: "9px", border: "1px solid rgba(16,185,129,0.15)", fontSize: "0.78rem", color: "var(--txt2)", lineHeight: 1.6 }}>
        <strong>Telegram commands:</strong><br />
        <code>create chain My Group</code> · <code>my chains</code> · <code>add to chain chain_xxxx</code>
      </div>
    </div>
  );
}
