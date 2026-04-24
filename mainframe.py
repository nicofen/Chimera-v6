import { useState, useEffect, useRef, useCallback } from "react";

const WS_URL = "ws://localhost:8765/ws/state";

const SECTORS = ["CRYPTO", "STOCKS", "FOREX", "FUTURES"];
const SYMBOLS = {
  CRYPTO:  ["BTC/USD", "ETH/USD", "SOL/USD"],
  STOCKS:  ["GME", "AMC", "TSLA", "BBBY"],
  FOREX:   ["EUR/USD", "GBP/USD", "USD/JPY"],
  FUTURES: ["ES1!", "NQ1!"],
};
const NEWS_SENTIMENTS = ["bullish", "bearish", "neutral"];
const VETO_REASONS = [
  "FOMC rate decision detected in FinancialJuice feed",
  "NFP release imminent — suppressing all signals",
  "CPI print detected — 10-min cool-down active",
];
const HEADLINES = [
  "Fed's Waller: 'not in a hurry' to cut rates further",
  "US 10Y yield rises 4bp after stronger-than-expected ISM",
  "Whale Alert: 2,400 BTC moved to Binance ($142M)",
  "GME short interest climbs to 24.3% per Finviz screen",
  "Solana DEX volume 24h: $4.2B — memecoin rotation active",
  "S&P 500 Value Area low: 5,612 tested at open",
  "USD/JPY rejects 20-EMA for third consecutive session",
  "Stocktwits GME mentions +340% Z-score in last 2 hours",
  "BTC exchange netflow: +$180M inflow last 4 hours",
];

function rand(min, max) { return Math.random() * (max - min) + min; }
function randInt(min, max) { return Math.floor(rand(min, max + 1)); }
function pick(arr) { return arr[randInt(0, arr.length - 1)]; }
function fmt(n, d = 2) { return Number(n).toFixed(d); }
function fmtCcy(n) { return (n >= 0 ? "+" : "") + "$" + Math.abs(n).toFixed(2); }

function makeSim() {
  const sector = pick(SECTORS);
  const entry  = rand(10, 500);
  const atr    = entry * rand(0.005, 0.025);
  const side   = Math.random() > 0.3 ? "buy" : "sell";
  const stop   = side === "buy" ? entry - atr * 2 : entry + atr * 2;
  const qty    = rand(1, 20);
  const cur    = entry + (side === "buy" ? rand(-atr, atr*2) : rand(-atr*2, atr));
  const pnl    = (side === "buy" ? cur - entry : entry - cur) * qty;
  const risk   = Math.abs(entry - stop) * qty;
  return {
    symbol: pick(SYMBOLS[sector]), sector, side,
    qty: fmt(qty, 2), entry_price: fmt(entry, 4),
    stop_price: fmt(stop, 4), take_profit: fmt(side==="buy"?entry+atr*3:entry-atr*3, 4),
    unrealised_pnl: pnl, r_multiple: risk > 0 ? pnl/risk : 0,
    kelly_fraction: rand(0.05, 0.22), sp_score: sector==="STOCKS" ? rand(0.55,0.95) : null,
    status: "filled", client_order_id: Math.random().toString(36).slice(2,10),
    _atr: atr,
  };
}

function makeSignal() {
  const sector = pick(SECTORS);
  return {
    sector, symbol: pick(SYMBOLS[sector]),
    direction: Math.random() > 0.4 ? "long" : "short",
    adx: rand(26, 48), sp_score: sector==="STOCKS" ? rand(0.58,0.94) : null,
    confidence: rand(0.52, 0.95),
    timestamp: new Date().toISOString(),
    _key: Math.random(),
  };
}

const css = `
  @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&display=swap');
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --bg0:#0d0e0f;--bg1:#131415;--bg2:#1a1b1d;--bg3:#212224;
    --border:#2a2b2d;--border2:#343537;--dim:#4a4b4e;--muted:#6b6c70;
    --text:#c8c9cc;--bright:#e8e9ec;
    --amber:#f0a030;--green:#22c55e;--green2:#4ade80;
    --red:#ef4444;--red2:#f87171;--cyan:#06b6d4;
    --mono:'IBM Plex Mono',monospace;
  }
  body{background:var(--bg0);color:var(--text);font-family:var(--mono)}
  .dash{min-height:100vh;display:flex;flex-direction:column}
  .topbar{display:flex;align-items:center;justify-content:space-between;padding:0 20px;height:44px;border-bottom:1px solid var(--border);background:var(--bg1);position:sticky;top:0;z-index:100}
  .topbar-left{display:flex;align-items:center;gap:16px}
  .logo{font-size:13px;font-weight:600;color:var(--amber);letter-spacing:.12em}
  .badge{font-size:9px;font-weight:600;letter-spacing:.12em;padding:2px 7px;border-radius:2px}
  .badge-paper{background:#0d1f0d;color:var(--green);border:1px solid #1a3a1a}
  .badge-live-ws{background:#0d1f0d;color:var(--green);border:1px solid #1a3a1a}
  .badge-sim{background:#1f1a0d;color:var(--amber);border:1px solid #3a2a0d}
  .badge-err{background:#1f0d0d;color:var(--red);border:1px solid #3a1a1a}
  .clock{font-size:11px;color:var(--muted)}
  .eq-row{display:flex;gap:24px}
  .eq-item{text-align:right}
  .eq-label{font-size:9px;color:var(--dim);letter-spacing:.1em;text-transform:uppercase}
  .eq-val{font-size:13px;font-weight:500;color:var(--bright)}
  .eq-val.pos{color:var(--green)}.eq-val.neg{color:var(--red)}.eq-val.amb{color:var(--amber)}
  .veto{background:#1a0505;border-bottom:1px solid var(--red);padding:7px 20px;display:flex;align-items:center;gap:12px;animation:vp 2s ease-in-out infinite}
  @keyframes vp{0%,100%{opacity:1}50%{opacity:.7}}
  .veto-dot{width:8px;height:8px;border-radius:50%;background:var(--red);flex-shrink:0;animation:vp 1s ease-in-out infinite}
  .veto-lbl{font-size:9px;font-weight:600;color:var(--red);letter-spacing:.18em}
  .veto-txt{font-size:11px;color:#f87171}
  .veto-cd{margin-left:auto;font-size:10px;color:var(--red);opacity:.7}
  .body{display:grid;grid-template-columns:1fr 310px;flex:1}
  .main{border-right:1px solid var(--border);display:flex;flex-direction:column}
  .sidebar{display:flex;flex-direction:column}
  .sec{border-bottom:1px solid var(--border)}
  .sec-hdr{display:flex;align-items:center;justify-content:space-between;padding:8px 16px;background:var(--bg1);border-bottom:1px solid var(--border)}
  .sec-ttl{font-size:9px;font-weight:600;letter-spacing:.18em;color:var(--dim);text-transform:uppercase}
  .sec-meta{font-size:9px;color:var(--dim)}
  .metrics{display:grid;grid-template-columns:repeat(6,1fr);border-bottom:1px solid var(--border)}
  .mcard{padding:12px 16px;border-right:1px solid var(--border);position:relative;overflow:hidden}
  .mcard:last-child{border-right:none}
  .mlbl{font-size:8px;color:var(--dim);letter-spacing:.14em;text-transform:uppercase;margin-bottom:4px}
  .mval{font-size:18px;font-weight:500;color:var(--bright);line-height:1}
  .mval.pos{color:var(--green2)}.mval.neg{color:var(--red2)}.mval.amb{color:var(--amber)}
  .msub{font-size:9px;color:var(--muted);margin-top:3px}
  .flash{position:absolute;inset:0;pointer-events:none;background:rgba(240,160,48,.08);animation:fi .4s ease-out forwards}
  @keyframes fi{0%{opacity:1}100%{opacity:0}}
  .pt{width:100%;border-collapse:collapse;font-size:11px}
  .pt th{font-size:8px;font-weight:500;letter-spacing:.12em;color:var(--dim);text-transform:uppercase;text-align:left;padding:6px 12px;border-bottom:1px solid var(--border);background:var(--bg1)}
  .pt th:not(:first-child){text-align:right}
  .pt td{padding:8px 12px;border-bottom:1px solid var(--border);color:var(--text);vertical-align:middle}
  .pt td:not(:first-child){text-align:right}
  .pt tr:hover td{background:var(--bg2)}
  .sym{font-weight:600;color:var(--bright)}
  .sp{font-size:8px;font-weight:600;letter-spacing:.1em;padding:1px 5px;border-radius:2px;margin-left:4px}
  .sp-crypto{background:#1a1a2a;color:#818cf8;border:1px solid #2a2a3a}
  .sp-stocks{background:#1a2a1a;color:var(--green);border:1px solid #2a3a2a}
  .sp-forex{background:#2a2a1a;color:var(--amber);border:1px solid #3a3a2a}
  .sp-futures{background:#2a1a2a;color:#e879f9;border:1px solid #3a2a3a}
  .long{color:var(--green2);font-weight:500}.short{color:var(--red2);font-weight:500}
  .pp{color:var(--green2)}.pn{color:var(--red2)}
  .sb-wrap{display:flex;align-items:center;gap:6px;justify-content:flex-end}
  .sb-track{width:48px;height:3px;background:var(--bg3);border-radius:1px;overflow:hidden}
  .sb-fill{height:100%;border-radius:1px;background:var(--green);transition:width .4s}
  .sb-fill.d{background:var(--red)}
  .sf{flex:1;overflow-y:auto;max-height:260px}
  .sr{display:grid;grid-template-columns:50px 60px 72px 52px 52px 1fr;align-items:center;padding:5px 12px;border-bottom:1px solid var(--border);font-size:10px;gap:4px;animation:si .25s ease-out}
  @keyframes si{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
  .sts{color:var(--dim);font-size:9px}.ssym{color:var(--bright);font-weight:500}
  .sdl{color:var(--green);font-weight:600;font-size:9px}.sds{color:var(--red);font-weight:600;font-size:9px}
  .sc{color:var(--amber)}.ssp{color:var(--cyan)}.sadx{color:var(--muted);font-size:9px}
  .sv{opacity:.35}
  .sent-row{display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid var(--border)}
  .sdot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
  .s-bullish .sdot{background:var(--green);box-shadow:0 0 6px var(--green)}
  .s-bearish .sdot{background:var(--red);box-shadow:0 0 6px var(--red)}
  .s-neutral .sdot{background:var(--muted)}
  .slbl{font-size:11px;font-weight:500;text-transform:uppercase;letter-spacing:.1em}
  .s-bullish .slbl{color:var(--green2)}.s-bearish .slbl{color:var(--red2)}.s-neutral .slbl{color:var(--muted)}
  .sci{margin-left:auto;font-size:10px;color:var(--amber)}
  .cib{width:60px;height:3px;background:var(--bg3);border-radius:1px;overflow:hidden}
  .cif{height:100%;border-radius:1px;transition:width .5s}
  .s-bullish .cif{background:var(--green)}.s-bearish .cif{background:var(--red)}.s-neutral .cif{background:var(--muted)}
  .hl{padding:7px 16px;border-bottom:1px solid var(--border);font-size:10px;line-height:1.4;color:var(--muted);animation:si .3s ease-out}
  .hl:hover{background:var(--bg2);color:var(--text)}
  .hlts{font-size:8px;color:var(--dim);margin-bottom:2px}
  .kp{padding:12px 16px;border-bottom:1px solid var(--border)}
  .kttl{font-size:9px;color:var(--dim);letter-spacing:.14em;text-transform:uppercase;margin-bottom:8px}
  .kr{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
  .kk{font-size:10px;color:var(--muted)}.kv{font-size:11px;font-weight:500;color:var(--bright)}
  .kv.amb{color:var(--amber)}.kv.grn{color:var(--green)}
  .ag{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--border)}
  .ac{background:var(--bg1);padding:10px 14px;display:flex;flex-direction:column;gap:3px}
  .an{font-size:8px;letter-spacing:.16em;color:var(--dim);text-transform:uppercase}
  .as{display:flex;align-items:center;gap:6px}
  .ad{width:6px;height:6px;border-radius:50%;flex-shrink:0}
  .ad.ok{background:var(--green);box-shadow:0 0 4px var(--green)}
  .ad.warn{background:var(--amber);box-shadow:0 0 4px var(--amber)}
  .ad.err{background:var(--red);box-shadow:0 0 4px var(--red)}
  .at{font-size:10px;color:var(--text)}.am{font-size:9px;color:var(--dim)}
  .cb{display:flex;gap:8px;padding:10px 16px;border-top:1px solid var(--border);background:var(--bg1);flex-wrap:wrap;margin-top:auto}
  .btn{font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.1em;text-transform:uppercase;padding:5px 12px;border-radius:2px;cursor:pointer;border:1px solid var(--border2);background:var(--bg2);color:var(--text);transition:all .15s}
  .btn:hover{background:var(--bg3);border-color:var(--dim);color:var(--bright)}
  .btn.d{border-color:#3a2020;color:var(--red)}.btn.d:hover{background:#1a0808;border-color:var(--red)}
  .btn.a{border-color:#3a2a10;color:var(--amber)}.btn.a:hover{background:#1a1408;border-color:var(--amber)}
  ::-webkit-scrollbar{width:4px}::-webkit-scrollbar-track{background:var(--bg0)}::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}
  @media(max-width:900px){.body{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(3,1fr)}.main{border-right:none}}
`;

const DEFAULT_AGENTS = [
  {name:"DataAgent",status:"ok",meta:"WS connected"},
  {name:"NewsAgent",status:"ok",meta:"polling 30s"},
  {name:"StrategyAgent",status:"ok",meta:"15s cycle"},
  {name:"RiskAgent",status:"ok",meta:"Kelly n=34"},
  {name:"OrderManager",status:"ok",meta:"paper mode"},
  {name:"APIServer",status:"ok",meta:"ws://…:8765"},
];

function useChimeraWS(setSig) {
  const [wsStatus, setWsStatus] = useState("connecting");
  const [wsState,  setWsState]  = useState(null);
  const ws = useRef(null);
  const retry = useRef(null);

  const connect = useCallback(() => {
    try {
      const sock = new WebSocket(WS_URL);
      ws.current = sock;
      sock.onopen  = () => { setWsStatus("live"); if (retry.current) { clearTimeout(retry.current); retry.current = null; } };
      sock.onmessage = (e) => {
        const msg = JSON.parse(e.data);
        if (msg.type === "ping") { sock.send("ping"); return; }
        if (msg.type === "pong") return;
        setWsState(prev => {
          if (msg.type === "snapshot" || !prev) return msg;
          const m = { ...prev };
          for (const k of Object.keys(msg)) { if (k !== "type" && k !== "ts") m[k] = msg[k]; }
          m.ts = msg.ts;
          return m;
        });
        if (msg.signals) setSig(msg.signals);
      };
      sock.onerror  = () => setWsStatus("error");
      sock.onclose  = () => { setWsStatus("sim"); retry.current = setTimeout(connect, 5000); };
    } catch { setWsStatus("sim"); }
  }, [setSig]);

  useEffect(() => { connect(); return () => { ws.current?.close(); if (retry.current) clearTimeout(retry.current); }; }, [connect]);
  return { wsStatus, wsState };
}

export default function ChimeraDashboard() {
  const [clock, setClock] = useState(new Date());
  const [flashKey, setFlashKey] = useState(0);

  const [simPos, setSimPos]   = useState(() => Array.from({length:randInt(1,2)}, makeSim));
  const [simSig, setSimSig]   = useState(() => Array.from({length:6}, makeSignal));
  const [simNews, setSimNews] = useState({sentiment:"neutral", confidence:0.54, veto_active:false, veto_reason:"", multiplier:0.54});
  const [simHdl, setSimHdl]   = useState(HEADLINES.slice(0,5));
  const [vetoCd, setVetoCd]   = useState(0);
  const [simEq]               = useState(125430.82);

  const { wsStatus, wsState } = useChimeraWS(useCallback(sigs => {
    setSimSig(sigs); setFlashKey(k => k+1);
  }, []));

  const isLive = wsStatus === "live" && wsState;
  const pos   = isLive ? (wsState.positions || []) : simPos;
  const sigs  = isLive ? (wsState.signals   || []) : simSig;
  const news  = isLive ? (wsState.news      || {}) : simNews;
  const eq    = isLive ? (wsState.equity    || 0)  : simEq;
  const agts  = isLive ? (wsState.agents    || DEFAULT_AGENTS) : DEFAULT_AGENTS;

  const vetoActive = news.veto_active || false;
  const vetoReason = news.veto_reason || "";

  useEffect(() => { const t = setInterval(() => setClock(new Date()), 1000); return () => clearInterval(t); }, []);

  useEffect(() => {
    if (isLive) return;
    const t = setInterval(() => {
      setSimPos(p => p.map(x => {
        const a = x._atr, e = parseFloat(x.entry_price);
        const cur = e + (x.side==="buy" ? rand(-a, a*2.5) : rand(-a*2.5, a));
        const pnl = (x.side==="buy" ? cur-e : e-cur) * parseFloat(x.qty);
        const risk = Math.abs(e - parseFloat(x.stop_price)) * parseFloat(x.qty);
        return {...x, unrealised_pnl:pnl, r_multiple: risk>0?pnl/risk:0};
      }));
    }, 1800);
    return () => clearInterval(t);
  }, [isLive]);

  useEffect(() => {
    if (isLive || vetoActive) return;
    const t = setInterval(() => {
      setSimSig(p => [makeSignal(), ...p].slice(0,18));
      setFlashKey(k => k+1);
    }, rand(4000, 9000));
    return () => clearInterval(t);
  }, [isLive, vetoActive]);

  useEffect(() => {
    if (isLive) return;
    const t = setInterval(() => {
      setSimNews(n => n.veto_active ? n : {...n, sentiment:pick(NEWS_SENTIMENTS), confidence:rand(0.42,0.91)});
      setSimHdl(p => [pick(HEADLINES), ...p].slice(0,8));
    }, 12000);
    return () => clearInterval(t);
  }, [isLive]);

  useEffect(() => {
    if (!vetoActive) return;
    const t = setInterval(() => setVetoCd(c => {
      if (c <= 1) { setSimNews(n => ({...n, veto_active:false, veto_reason:""})); return 0; }
      return c - 1;
    }), 1000);
    return () => clearInterval(t);
  }, [vetoActive]);

  const triggerVeto = () => { setSimNews(n => ({...n, veto_active:true, veto_reason:pick(VETO_REASONS)})); setVetoCd(600); };
  const clearVeto   = () => { setSimNews(n => ({...n, veto_active:false, veto_reason:""})); setVetoCd(0); };

  const totalPnl = pos.reduce((s,p) => s + (p.unrealised_pnl||0), 0);
  const dailyPnl = totalPnl + 482.15;
  const winCnt   = pos.filter(p => (p.unrealised_pnl||0) > 0).length;

  const wsCls  = wsStatus==="live" ? "badge-live-ws" : wsStatus==="error" ? "badge-err" : "badge-sim";
  const wsTxt  = wsStatus==="live" ? "WS LIVE" : wsStatus==="error" ? "WS ERR" : "SIMULATED";

  return (
    <>
      <style>{css}</style>
      <div className="dash">
        <div className="topbar">
          <div className="topbar-left">
            <span className="logo">PROJECT CHIMERA</span>
            <span className="badge badge-paper">PAPER</span>
            <span className={`badge ${wsCls}`}>{wsTxt}</span>
            <span className="clock">{clock.toISOString().replace("T"," ").slice(0,19)} UTC</span>
          </div>
          <div className="eq-row">
            {[
              {l:"Equity",     v:`$${Number(eq).toLocaleString("en-US",{minimumFractionDigits:2})}`, c:""},
              {l:"Open P&L",   v:fmtCcy(totalPnl), c:totalPnl>=0?"pos":"neg"},
              {l:"Day P&L",    v:fmtCcy(dailyPnl), c:dailyPnl>=0?"pos":"neg"},
              {l:"Positions",  v:`${pos.length} / 5`, c:"amb"},
            ].map((x,i) => (
              <div className="eq-item" key={i}>
                <div className="eq-label">{x.l}</div>
                <div className={`eq-val ${x.c}`}>{x.v}</div>
              </div>
            ))}
          </div>
        </div>

        {vetoActive && (
          <div className="veto">
            <div className="veto-dot"/>
            <span className="veto-lbl">VETO ACTIVE</span>
            <span className="veto-txt">{vetoReason}</span>
            {vetoCd > 0 && <span className="veto-cd">{Math.floor(vetoCd/60)}:{String(vetoCd%60).padStart(2,"0")}</span>}
          </div>
        )}

        <div className="body">
          <div className="main">
            <div className="metrics">
              {[
                {l:"Open positions", v:pos.length,        s:`${winCnt} profitable`, c:"amb"},
                {l:"Day P&L",        v:fmtCcy(dailyPnl),  s:"since 00:00 UTC",   c:dailyPnl>=0?"pos":"neg"},
                {l:"Win rate",       v:"64%",   s:"last 50 trades",   c:"pos"},
                {l:"Avg R",          v:"+1.42R", s:"closed trades",   c:"pos"},
                {l:"Kelly f*",       v:"11.4%",  s:"current estimate",c:"amb"},
                {l:"Veto",  v:vetoActive?"ACTIVE":"CLEAR", s:vetoActive?vetoReason.slice(0,22)+"…":"nominal", c:vetoActive?"neg":"pos"},
              ].map((m,i) => (
                <div className="mcard" key={i}>
                  {i===0 && <div className="flash" key={flashKey}/>}
                  <div className="mlbl">{m.l}</div>
                  <div className={`mval ${m.c}`}>{m.v}</div>
                  <div className="msub">{m.s}</div>
                </div>
              ))}
            </div>

            <div className="sec">
              <div className="sec-hdr">
                <span className="sec-ttl">Open positions</span>
                <span className="sec-meta">{pos.length} active</span>
              </div>
              {pos.length === 0
                ? <div style={{padding:"20px 16px",fontSize:11,color:"var(--dim)",textAlign:"center"}}>No open positions — awaiting signals</div>
                : <table className="pt">
                    <thead><tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Stop</th><th>P&amp;L</th><th>R</th><th>Kelly</th></tr></thead>
                    <tbody>
                      {pos.map((p,i) => {
                        const e = parseFloat(p.entry_price)||0, s = parseFloat(p.stop_price)||0;
                        const pnl = p.unrealised_pnl||0, risk = Math.abs(e-s);
                        const prog = risk>0 ? Math.min(1, Math.max(0, (Math.abs(e - (e + pnl/(parseFloat(p.qty)||1))) / (risk*3)))) : 0.3;
                        const sec = (p.sector||"").toLowerCase();
                        return (
                          <tr key={p.client_order_id||i}>
                            <td><span className="sym">{p.symbol}</span><span className={`sp sp-${sec}`}>{sec.slice(0,3).toUpperCase()}</span></td>
                            <td><span className={p.side==="buy"?"long":"short"}>{(p.side||"").toUpperCase()}</span></td>
                            <td>{fmt(p.qty,2)}</td>
                            <td>{fmt(p.entry_price,4)}</td>
                            <td>
                              <div className="sb-wrap">
                                <span style={{color:"var(--dim)"}}>{fmt(p.stop_price,4)}</span>
                                <div className="sb-track"><div className={`sb-fill ${pnl < -risk*parseFloat(p.qty||1)*0.5?"d":""}`} style={{width:`${Math.round(prog*100)}%`}}/></div>
                              </div>
                            </td>
                            <td className={pnl>=0?"pp":"pn"}>{fmtCcy(pnl)}</td>
                            <td style={{color:(p.r_multiple||0)>=0?"var(--green)":"var(--red)"}}>{((p.r_multiple||0)>=0?"+":"")+fmt(p.r_multiple||0,2)}R</td>
                            <td style={{color:"var(--amber)"}}>{fmt(p.kelly_fraction||0,3)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
              }
            </div>

            <div className="sec" style={{flex:1}}>
              <div className="sec-hdr">
                <span className="sec-ttl">Signal feed</span>
                <span className="sec-meta" style={{color:vetoActive?"var(--red)":"var(--dim)"}}>{vetoActive?"SUPPRESSED":"live"}</span>
              </div>
              <div style={{display:"grid",gridTemplateColumns:"50px 60px 72px 52px 52px 1fr",padding:"4px 12px",borderBottom:"1px solid var(--border)"}}>
                {["TIME","SECTOR","SYMBOL","DIR","CONF","ADX / Sp"].map((h,i)=>(
                  <div key={i} style={{fontSize:8,color:"var(--dim)",letterSpacing:"0.12em",textTransform:"uppercase"}}>{h}</div>
                ))}
              </div>
              <div className="sf">
                {sigs.map((s,i) => (
                  <div key={s._key||i} className={`sr ${vetoActive?"sv":""}`}>
                    <span className="sts">{(s.timestamp||"").slice(11,19)||"--:--:--"}</span>
                    <span><span className={`sp sp-${(s.sector||"").toLowerCase()}`}>{(s.sector||"").slice(0,3)}</span></span>
                    <span className="ssym">{s.symbol}</span>
                    <span className={s.direction==="long"?"sdl":"sds"}>{(s.direction||"").toUpperCase()}</span>
                    <span className="sc">{fmt(s.confidence||0,2)}</span>
                    <span className="sadx">ADX {fmt(s.adx||0,1)}{s.sp_score?<span className="ssp">  Sp {fmt(s.sp_score,2)}</span>:null}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="sidebar">
            <div className="sec">
              <div className="sec-hdr"><span className="sec-ttl">Agent status</span></div>
              <div className="ag">
                {agts.map((a,i) => (
                  <div className="ac" key={i}>
                    <div className="an">{a.name}</div>
                    <div className="as">
                      <div className={`ad ${vetoActive&&a.name==="NewsAgent"?"warn":a.status}`}/>
                      <span className="at">{vetoActive&&a.name==="NewsAgent"?"veto":a.status}</span>
                    </div>
                    <div className="am">{a.meta||""}</div>
                  </div>
                ))}
              </div>
            </div>

            <div className="sec">
              <div className="sec-hdr"><span className="sec-ttl">News agent</span></div>
              <div className={`sent-row s-${news.sentiment||"neutral"}`}>
                <div className="sdot"/>
                <span className="slbl">{news.sentiment||"neutral"}</span>
                <div className="cib"><div className="cif" style={{width:`${Math.round((news.confidence||0.5)*100)}%`}}/></div>
                <span className="sci">CI {fmt(news.confidence||0,2)}</span>
              </div>
              <div style={{maxHeight:180,overflowY:"auto"}}>
                {(isLive ? [] : simHdl).slice(0,6).map((h,i) => (
                  <div className="hl" key={i}>
                    <div className="hlts">{new Date(Date.now()-i*120000).toISOString().slice(11,16)} UTC</div>
                    {h}
                  </div>
                ))}
                {isLive && <div style={{padding:"12px 16px",fontSize:10,color:"var(--dim)"}}>Headlines via live NewsAgent</div>}
              </div>
            </div>

            <div className="kp">
              <div className="kttl">Risk parameters</div>
              {[
                {k:"Win rate (n=34)", v:"64.7%",   c:"grn"},
                {k:"Avg win (R)",     v:"+1.82R",  c:"grn"},
                {k:"Avg loss (R)",    v:"−0.98R",  c:""},
                {k:"Kelly f*",        v:"11.4%",   c:"amb"},
                {k:"Base risk",       v:"1.0%",    c:""},
                {k:"Eff. risk",       v:`${fmt((news.multiplier||0.54)*1,2)}×`, c:"amb"},
                {k:"Max positions",   v:"5 slots", c:""},
                {k:"Daily loss cap",  v:"5% / $6,272", c:""},
              ].map((r,i) => (
                <div className="kr" key={i}>
                  <span className="kk">{r.k}</span>
                  <span className={`kv ${r.c}`}>{r.v}</span>
                </div>
              ))}
            </div>

            <div className="cb">
              {!isLive && <button className="btn a" onClick={triggerVeto}>Trigger veto</button>}
              {!isLive && vetoActive && <button className="btn" onClick={clearVeto}>Clear veto</button>}
              {isLive && <span style={{fontSize:10,color:"var(--green)"}}>Connected to mainframe</span>}
              <button className="btn d" style={{marginLeft:"auto"}}>Flatten all</button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
