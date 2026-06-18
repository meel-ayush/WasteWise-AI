"use client";
import React, { useState, useEffect, useRef, useCallback } from "react";

const LANGUAGES = [
  { code:"en-US", label:"🇺🇸 English",    ttsLang:"en" },
  { code:"ms-MY", label:"🇲🇾 Bahasa Melayu", ttsLang:"ms" },
  { code:"zh-CN", label:"🇨🇳 中文 (普通话)", ttsLang:"zh" },
  { code:"ta-IN", label:"🇮🇳 தமிழ்",        ttsLang:"ta" },
  { code:"hi-IN", label:"🇮🇳 हिंदी",         ttsLang:"hi" },
  { code:"ar-SA", label:"🇸🇦 العربية",       ttsLang:"ar" },
  { code:"fr-FR", label:"🇫🇷 Français",      ttsLang:"fr" },
  { code:"es-ES", label:"🇪🇸 Español",       ttsLang:"es" },
];

interface VoicePanelProps {
  onTranscript: (text: string) => void;
  speakText?: string;
  onSpeakDone?: () => void;
}

export default function VoicePanel({ onTranscript, speakText, onSpeakDone }: VoicePanelProps) {
  const [open, setOpen]           = useState(false);
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking]   = useState(false);
  const [transcript, setTranscript] = useState("");
  const [langIdx, setLangIdx]     = useState(0);
  const [voices, setVoices]       = useState<SpeechSynthesisVoice[]>([]);
  const [voiceIdx, setVoiceIdx]   = useState(0);
  const [supported, setSupported] = useState(true);
  const recogRef = useRef<any>(null);
  const lang = LANGUAGES[langIdx];

  useEffect(() => {
    if (typeof window === "undefined") return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR || !window.speechSynthesis) { setSupported(false); return; }

    const loadVoices = () => {
      const all = window.speechSynthesis.getVoices();
      const matching = all.filter(v => v.lang.startsWith(lang.ttsLang) || v.lang.startsWith(lang.code.split("-")[0]));
      setVoices(matching.length > 0 ? matching : all.slice(0, 10));
      setVoiceIdx(0);
    };
    loadVoices();
    window.speechSynthesis.onvoiceschanged = loadVoices;
    return () => { window.speechSynthesis.onvoiceschanged = null; };
  }, [langIdx]);

  
  useEffect(() => {
    if (!speakText || typeof window === "undefined" || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(speakText);
    if (voices[voiceIdx]) utt.voice = voices[voiceIdx];
    utt.lang = lang.code;
    utt.rate = 0.95;
    utt.onstart = () => setSpeaking(true);
    utt.onend   = () => { setSpeaking(false); onSpeakDone?.(); };
    utt.onerror = () => { setSpeaking(false); onSpeakDone?.(); };
    window.speechSynthesis.speak(utt);
  }, [speakText]);

  const startListening = useCallback(() => {
    if (typeof window === "undefined") return;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) return;
    window.speechSynthesis?.cancel();
    const recog = new SR();
    recogRef.current = recog;
    recog.lang = lang.code;
    recog.continuous = false;
    recog.interimResults = true;
    recog.onresult = (e: any) => {
      const t = Array.from(e.results as any[]).map((r:any)=>r[0].transcript).join("");
      setTranscript(t);
      if (e.results[e.results.length-1].isFinal) {
        onTranscript(t);
        setListening(false);
      }
    };
    recog.onerror = () => setListening(false);
    recog.onend   = () => setListening(false);
    recog.start();
    setListening(true);
    setTranscript("");
  }, [lang, onTranscript]);

  const stopListening = useCallback(() => {
    recogRef.current?.stop();
    setListening(false);
  }, []);

  const stopSpeaking = useCallback(() => {
    window.speechSynthesis?.cancel();
    setSpeaking(false);
  }, []);

  if (!supported) return null;

  return (
    <>
      {}
      <button
        id="voice-panel-toggle"
        onClick={() => setOpen(o => !o)}
        title="Voice Interface"
        style={{
          position:"fixed", bottom:"28px", right:"28px", zIndex:900,
          width:"56px", height:"56px", borderRadius:"50%",
          background: listening ? "#ef4444" : speaking ? "#f59e0b" : "var(--green)",
          border:"none", cursor:"pointer", boxShadow:"0 4px 20px rgba(0,0,0,0.4)",
          display:"flex", alignItems:"center", justifyContent:"center",
          fontSize:"1.5rem", transition:"all 0.2s",
          animation: listening ? "pulse 1s infinite" : "none",
        }}
      >
        {listening ? "🔴" : speaking ? "🔊" : "🎤"}
      </button>

      {}
      {open && (
        <div style={{
          position:"fixed", bottom:"96px", right:"28px", zIndex:900,
          width:"320px", background:"var(--card)", border:"1px solid var(--bdr)",
          borderRadius:"16px", padding:"20px", boxShadow:"0 8px 40px rgba(0,0,0,0.5)",
        }}>
          <div style={{display:"flex",justifyContent:"space-between",alignItems:"center",marginBottom:"16px"}}>
            <h3 style={{margin:0,fontSize:"0.95rem",fontWeight:700}}>🎤 Voice Interface</h3>
            <button onClick={()=>setOpen(false)} style={{background:"transparent",border:"none",color:"var(--txt3)",cursor:"pointer",fontSize:"1rem"}}>✕</button>
          </div>

          {}
          <label style={{fontSize:"0.78rem",color:"var(--txt3)",display:"block",marginBottom:"4px"}}>Language / Bahasa</label>
          <select
            id="voice-language-select"
            value={langIdx}
            onChange={e=>setLangIdx(Number(e.target.value))}
            style={{width:"100%",padding:"8px 10px",borderRadius:"8px",background:"var(--input)",color:"var(--txt)",border:"1px solid var(--bdr)",fontSize:"0.88rem",cursor:"pointer",marginBottom:"12px"}}
          >
            {LANGUAGES.map((l,i)=>(
              <option key={l.code} value={i}>{l.label}</option>
            ))}
          </select>

          {}
          {voices.length > 0 && (
            <>
              <label style={{fontSize:"0.78rem",color:"var(--txt3)",display:"block",marginBottom:"4px"}}>TTS Voice</label>
              <select
                id="voice-tts-select"
                value={voiceIdx}
                onChange={e=>setVoiceIdx(Number(e.target.value))}
                style={{width:"100%",padding:"8px 10px",borderRadius:"8px",background:"var(--input)",color:"var(--txt)",border:"1px solid var(--bdr)",fontSize:"0.82rem",cursor:"pointer",marginBottom:"14px"}}
              >
                {voices.map((v,i)=>(
                  <option key={i} value={i}>{v.name} ({v.lang})</option>
                ))}
              </select>
            </>
          )}

          {}
          {transcript && (
            <div style={{background:"rgba(16,185,129,0.08)",border:"1px solid rgba(16,185,129,0.2)",borderRadius:"8px",padding:"10px",marginBottom:"12px",fontSize:"0.85rem",color:"var(--txt)",minHeight:"36px"}}>
              {transcript}
            </div>
          )}

          {}
          <div style={{display:"flex",gap:"8px"}}>
            <button
              id="voice-mic-btn"
              onClick={listening ? stopListening : startListening}
              style={{
                flex:1, padding:"12px", borderRadius:"9px", border:"none",
                background: listening ? "#ef4444" : "var(--green)",
                color:"#fff", fontWeight:700, cursor:"pointer", fontSize:"0.88rem",
                animation: listening ? "pulse 1s infinite" : "none",
              }}
            >
              {listening ? "⏹ Stop" : "🎤 Speak"}
            </button>
            {speaking && (
              <button onClick={stopSpeaking} style={{padding:"12px 14px",borderRadius:"9px",border:"none",background:"#f59e0b",color:"#fff",fontWeight:700,cursor:"pointer",fontSize:"0.88rem"}}>
                ⏹ Stop
              </button>
            )}
          </div>

          <p style={{margin:"10px 0 0",fontSize:"0.74rem",color:"var(--txt3)",textAlign:"center"}}>
            {listening ? `🔴 Listening in ${lang.label}…` : speaking ? "🔊 Speaking…" : `Click 🎤 to speak in ${lang.label}`}
          </p>

          <div style={{marginTop:"12px",paddingTop:"12px",borderTop:"1px solid var(--bdr)"}}>
            <p style={{margin:0,fontSize:"0.73rem",color:"var(--txt3)"}}>
              💡 Tip: Your speech fills the input box automatically. Press 🔊 on any forecast to hear it spoken aloud.
            </p>
          </div>
        </div>
      )}

      <style>{`
        @keyframes pulse {
          0%,100%{transform:scale(1);opacity:1}
          50%{transform:scale(1.08);opacity:0.85}
        }
      `}</style>
    </>
  );
}
