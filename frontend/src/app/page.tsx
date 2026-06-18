"use client";
import React, { useState, useEffect } from "react";
import AuthScreen from "./components/AuthScreen";
import Dashboard from "./components/Dashboard";


const COOKIE_CONSENT_KEY = "ww_cookie_ok";
const SESSION_DAYS = 30;

function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : null;
}
function setCookie(name: string, value: string, days: number) {
  const exp = new Date(Date.now() + days * 864e5).toUTCString();
  document.cookie = `${name}=${encodeURIComponent(value)}; expires=${exp}; path=/; SameSite=Lax`;
}
function delCookie(name: string) {
  document.cookie = `${name}=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/`;
}


function CookieBanner({ onAccept, onDecline }: { onAccept: () => void; onDecline: () => void }) {
  return (
    <div style={{
      position: "fixed", bottom: 0, left: 0, right: 0, zIndex: 9999,
      background: "linear-gradient(135deg, #0d1117, #161b22)",
      borderTop: "1px solid rgba(16,185,129,0.3)",
      padding: "16px 24px",
      display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap",
      boxShadow: "0 -4px 20px rgba(0,0,0,0.5)",
    }}>
      <div style={{ flex: 1, minWidth: "220px" }}>
        <p style={{ margin: 0, fontWeight: 600, fontSize: "0.88rem", color: "#e4e4e7" }}>
          🍪 WasteWise uses cookies
        </p>
        <p style={{ margin: "4px 0 0", fontSize: "0.78rem", color: "#a1a1aa", lineHeight: 1.5 }}>
          We store your login session in a secure cookie (30-day expiry) so you stay signed in.
          No tracking, no third parties.
        </p>
      </div>
      <div style={{ display: "flex", gap: "10px", flexShrink: 0 }}>
        <button onClick={onDecline}
          style={{ padding: "8px 16px", borderRadius: "8px", border: "1px solid #3f3f46",
            background: "transparent", color: "#a1a1aa", cursor: "pointer", fontSize: "0.82rem" }}>
          Decline
        </button>
        <button onClick={onAccept}
          style={{ padding: "8px 20px", borderRadius: "8px", border: "none",
            background: "linear-gradient(135deg, #10b981, #059669)", color: "#fff",
            cursor: "pointer", fontSize: "0.82rem", fontWeight: 700 }}>
          Accept & Stay Logged In
        </button>
      </div>
    </div>
  );
}

export default function App() {
  const [mounted, setMounted]         = useState(false);
  const [token, setToken]             = useState<string|null>(null);
  const [restId, setRestId]           = useState<string|null>(null);
  const [email, setEmail]             = useState<string|null>(null);
  const [cookieConsent, setConsent]   = useState<boolean|null>(null);
  const [showBanner, setShowBanner]   = useState(false);
  const [pendingAuth, setPendingAuth] = useState<[string,string,string]|null>(null);

  useEffect(() => {
    setMounted(true);
    const consented = getCookie(COOKIE_CONSENT_KEY) || (typeof window !== "undefined" ? localStorage.getItem(COOKIE_CONSENT_KEY) : null);

    if (consented === "1") {
      const t = getCookie("ww_token") || localStorage.getItem("ww_token");
      const r = getCookie("ww_rid") || localStorage.getItem("ww_rid");
      const e = getCookie("ww_email") || localStorage.getItem("ww_email");
      if (t && r && e) { setToken(t); setRestId(r); setEmail(e); }
      setConsent(true);
    } else if (consented === "0") {
      const t = sessionStorage.getItem("ww_token");
      const r = sessionStorage.getItem("ww_rid");
      const e = sessionStorage.getItem("ww_email");
      if (t && r && e) { setToken(t); setRestId(r); setEmail(e); }
      setConsent(false);
    }
  }, []);

  if (!mounted) return null;

  const persistSession = (t: string, r: string, e: string, useCookies: boolean) => {
    if (useCookies) {
      setCookie("ww_token", t, SESSION_DAYS);
      setCookie("ww_rid",   r, SESSION_DAYS);
      setCookie("ww_email", e, SESSION_DAYS);
      try {
        localStorage.setItem("ww_token", t);
        localStorage.setItem("ww_rid", r);
        localStorage.setItem("ww_email", e);
        localStorage.setItem(COOKIE_CONSENT_KEY, "1");
      } catch (e) {}
    }
    sessionStorage.setItem("ww_token", t);
    sessionStorage.setItem("ww_rid",   r);
    sessionStorage.setItem("ww_email", e);
    setToken(t); setRestId(r); setEmail(e);
  };

  const handleAuth = (t: string, r: string, e: string) => {
    if (cookieConsent === null) {
      setPendingAuth([t, r, e]);
      setShowBanner(true);
    } else {
      persistSession(t, r, e, cookieConsent);
    }
  };

  const handleAccept = () => {
    setCookie(COOKIE_CONSENT_KEY, "1", 365);
    try { localStorage.setItem(COOKIE_CONSENT_KEY, "1"); } catch (e) {}
    setConsent(true);
    setShowBanner(false);
    if (pendingAuth) {
      persistSession(pendingAuth[0], pendingAuth[1], pendingAuth[2], true);
      setPendingAuth(null);
    }
  };

  const handleDecline = () => {
    setCookie(COOKIE_CONSENT_KEY, "0", 365);
    try { localStorage.setItem(COOKIE_CONSENT_KEY, "0"); } catch (e) {}
    setConsent(false);
    setShowBanner(false);
    if (pendingAuth) {
      persistSession(pendingAuth[0], pendingAuth[1], pendingAuth[2], false);
      setPendingAuth(null);
    }
  };

  const handleLogout = () => {
    delCookie("ww_token"); delCookie("ww_rid"); delCookie("ww_email");
    sessionStorage.removeItem("ww_token");
    sessionStorage.removeItem("ww_rid");
    sessionStorage.removeItem("ww_email");
    try {
      localStorage.removeItem("ww_token");
      localStorage.removeItem("ww_rid");
      localStorage.removeItem("ww_email");
    } catch (e) {}
    setToken(null); setRestId(null); setEmail(null);
  };

  return (
    <>
      {token && restId && email
        ? <Dashboard token={token} restaurantId={restId} email={email} onLogout={handleLogout}/>
        : <AuthScreen onAuth={handleAuth}/>
      }
      {showBanner && (
        <CookieBanner onAccept={handleAccept} onDecline={handleDecline}/>
      )}
    </>
  );
}
