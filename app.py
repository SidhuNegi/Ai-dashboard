"""
AI Market Pulse: Nifty 50 & Sensex Terminal
Dark fintech UI + live NSE option chain + yfinance + Google News RSS + Gemini
Breaking-news alert engine (30s polling, toast + glowing banner, per-headline AI impact).

Run:  streamlit run app.py
Secret (.streamlit/secrets.toml):  GEMINI_API_KEY = "your-key"
Optional secret:                   GEMINI_MODEL   = "gemini-2.5-flash"
"""
import calendar
import json
import re
import time
from html import escape as _esc

import feedparser
import google.generativeai as genai
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# ==========================================
# CONFIG
# ==========================================
st.set_page_config(page_title="AI Market Pulse: Nifty & Sensex", page_icon="⚡", layout="wide")

NEWS_POLL = "30s"                      # RSS polling interval for the alert engine
MAX_HEADLINES = 10                     # headlines shown in the list
PREFERRED_MODEL = "gemini-1.5-flash"   # tried first; auto-falls back if Google has retired it
UP, DN, AMB, MUTED, ACCENT = "#2DD4A0", "#FF6B6B", "#F5B84B", "#8B98A9", "#5B8CFF"

TICKERS = {
    "Nifty 50": "^NSEI", "Sensex": "^BSESN", "India VIX": "^INDIAVIX", "S&P 500": "^GSPC",
    "Nasdaq": "^IXIC", "Brent Crude": "BZ=F", "Gold": "GC=F", "USD/INR": "INR=X",
}
RSS_URLS = [
    "https://news.google.com/rss/search?q=Indian+Stock+Market+Nifty+Sensex+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=Global+Market+Wall+Street+Crude+Oil+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
    "https://news.google.com/rss/search?q=RBI+Fed+inflation+FII+rupee+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
]
BADGES = {
    "BULLISH": ("🟢 BULLISH", UP, "rgba(45,212,160,.12)"),
    "BEARISH": ("🔴 BEARISH", DN, "rgba(255,107,107,.12)"),
    "NEUTRAL": ("⚪ NEUTRAL", MUTED, "rgba(139,152,169,.14)"),
}

# ==========================================
# SESSION STATE
# ==========================================
for _k, _v in {
    "seen_headlines": set(), "classified": {}, "alerts": [], "news_items": [], "new_marks": {},
    "pending_alerts": set(), "news_primed": False, "news_err": None, "last_poll": None,
    "last_oi": {}, "market": [], "oi_status": "waiting", "gemini_model": None,
    "dead_models": set(), "verdict": None,
}.items():
    st.session_state.setdefault(_k, _v)
ss = st.session_state

# ==========================================
# STYLE
# ==========================================
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
html, body, .stApp, [data-testid="stAppViewContainer"] { background:#0B0F14; color:#E6EDF3; font-family:'IBM Plex Sans',system-ui,sans-serif; }
[data-testid="stHeader"] { background:transparent; }
#MainMenu, footer { visibility:hidden; }
.block-container { max-width:1360px; padding:24px 32px 56px; }
[data-testid="stSidebar"] { background:#0E141B; border-right:1px solid #1F2A37; }
[data-testid="stSidebar"] label, [data-testid="stSidebar"] p { font-family:'IBM Plex Sans',sans-serif; color:#E6EDF3; }
[data-testid="stVerticalBlockBorderWrapper"] { background:#111821; border:1px solid #1F2A37 !important; border-radius:12px; }
div[role="radiogroup"] { gap:8px; }
div[role="radiogroup"] > label { flex:1; justify-content:center; align-items:center; min-height:44px; border:1px solid #1F2A37; border-radius:8px; background:#0B0F14; padding:0 10px; }
div[role="radiogroup"] > label > div:first-child { display:none; }
div[role="radiogroup"] > label:has(input:checked) { background:#5B8CFF; border-color:#5B8CFF; }
div[role="radiogroup"] > label:has(input:checked) p { color:#0B0F14; font-weight:600; }
.stButton > button { min-height:48px; border-radius:10px; border:0; background:#5B8CFF; color:#0B0F14; font-weight:600; }
.stButton > button p { color:#0B0F14; font-weight:600; font-size:15px; }
.stButton > button:hover { background:#7DA3FF; color:#0B0F14; }
.mp { font-family:'IBM Plex Sans',system-ui,sans-serif; color:#E6EDF3; }
.mono { font-family:'IBM Plex Mono',monospace; }
.card { background:#111821; border:1px solid #1F2A37; border-radius:12px; padding:20px; }
.lbl { font-size:12px; color:#8B98A9; }
.cap { font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:#8B98A9; }
.val { font-family:'IBM Plex Mono',monospace; font-size:32px; font-weight:500; margin:8px 0 6px; }
.sub { font-size:13px; font-weight:500; }
.h2 { font-size:15px; font-weight:600; margin:0 0 12px; }
.grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:16px; }
.mcard { background:#111821; border:1px solid #1F2A37; border-radius:12px; padding:16px; display:flex; flex-direction:column; gap:8px; }
.row { display:flex; justify-content:space-between; font-size:12px; color:#8B98A9; }
.head { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:16px; margin-bottom:20px; }
.pill { display:inline-flex; align-items:center; gap:10px; padding:8px 14px; border:1px solid #1F2A37; border-radius:999px; background:#111821; font-family:'IBM Plex Mono',monospace; font-size:12px; color:#8B98A9; }
.dot { width:8px; height:8px; border-radius:50%; display:inline-block; }
.live { animation:pulse 1.6s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:.35; } }
.strikes { display:flex; gap:10px; overflow-x:auto; padding-bottom:4px; }
.strike { flex:1 0 52px; display:flex; flex-direction:column; align-items:center; gap:8px; }
.bars { display:flex; align-items:flex-end; gap:4px; height:150px; border-bottom:1px solid #1F2A37; }
.bar { width:14px; border-radius:3px 3px 0 0; }
.slab { font-family:'IBM Plex Mono',monospace; font-size:11px; }
.banner { border:1px solid var(--c); border-radius:12px; padding:14px 18px; background:#111821; margin-bottom:12px; animation:glow 1.5s ease-in-out infinite; }
@keyframes glow { 0%,100% { box-shadow:0 0 0 rgba(0,0,0,0); } 50% { box-shadow:0 0 26px var(--g); } }
.news { display:flex; flex-wrap:wrap; gap:12px; padding:12px 0; border-top:1px solid #1F2A37; }
.chip { display:inline-block; font-size:11px; font-weight:600; padding:2px 8px; border-radius:999px; }
.badge { font-size:12px; font-weight:600; padding:5px 10px; border-radius:8px; white-space:nowrap; align-self:flex-start; }
.news a { color:#E6EDF3; text-decoration:none; }
.news a:hover { color:#5B8CFF; }
.gauge { position:relative; height:8px; border-radius:4px; background:#1F2A37; margin:14px 0 6px; }
.knob { position:absolute; top:-3px; width:4px; height:14px; border-radius:2px; background:#E6EDF3; }
</style>
"""


def H(s):
    """Collapse an HTML template to one line so Markdown never treats it as a code block."""
    return "".join(line.strip() for line in s.strip().splitlines())


def md(s):
    st.markdown(H(s), unsafe_allow_html=True)


def esc(s):
    return _esc(str(s)).replace("$", "&#36;")  # stop Streamlit reading $...$ as LaTeX


def fmt(n):
    return f"{int(n):,}"


def ago(ts):
    if not ts:
        return ""
    d = max(0, int(time.time() - ts))
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


def spark_path(vals, w=100, h=30):
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1
    return " ".join(
        ("L" if i else "M") + f"{i * w / (len(vals) - 1):.1f} {h - 2 - (v - lo) / rng * (h - 4):.1f}"
        for i, v in enumerate(vals)
    )


# ==========================================
# 1. SECRET / API KEY
# ==========================================
def get_api_key():
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:
        pass
    return st.session_state.get("api_key_input") or None


# ==========================================
# 2. LIVE NSE OPTION CHAIN (real data only)
# ==========================================
@st.cache_data(ttl=10, show_spinner=False)
def get_live_nse_oi(symbol="NIFTY"):
    url = f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Referer": "https://www.nseindia.com/option-chain",
    }
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=6)  # cookies
        resp = session.get(url, headers=headers, timeout=6)
        if resp.status_code != 200:
            return {"status": "ERROR", "error": f"NSE returned HTTP {resp.status_code} (cloud IPs are often blocked)"}
        data = resp.json()
        filt = data["filtered"]
        tot_ce, tot_pe = filt["CE"]["totOI"], filt["PE"]["totOI"]
        pcr = round(tot_pe / tot_ce, 2) if tot_ce > 0 else 0

        rows = []
        for item in filt["data"]:
            rows.append({
                "Strike": item.get("strikePrice"),
                "CE_OI": item.get("CE", {}).get("openInterest", 0),
                "CE_Chg_OI": item.get("CE", {}).get("changeinOpenInterest", 0),
                "PE_OI": item.get("PE", {}).get("openInterest", 0),
                "PE_Chg_OI": item.get("PE", {}).get("changeinOpenInterest", 0),
            })
        df = pd.DataFrame(rows).dropna(subset=["Strike"]).sort_values("Strike").reset_index(drop=True)
        max_ce = int(df.loc[df["CE_OI"].idxmax(), "Strike"])
        max_pe = int(df.loc[df["PE_OI"].idxmax(), "Strike"])

        spot = data.get("records", {}).get("underlyingValue") or (max_ce + max_pe) / 2
        atm_i = int((df["Strike"] - spot).abs().idxmin())
        window = df.iloc[max(0, atm_i - 4): atm_i + 5]  # 9 strikes around ATM

        return {
            "status": "LIVE", "pcr": pcr, "max_ce_strike": max_ce, "max_pe_strike": max_pe,
            "ce_chg_sum": int(df["CE_Chg_OI"].sum()), "pe_chg_sum": int(df["PE_Chg_OI"].sum()),
            "spot": float(spot), "strikes": window.to_dict("records"), "ts": time.time(),
        }
    except Exception as e:
        return {"status": "ERROR", "error": f"{type(e).__name__}: {str(e)[:90]}"}


# ==========================================
# 3. GLOBAL & DOMESTIC MARKET DATA (yfinance)
# ==========================================
@st.cache_data(ttl=15, show_spinner=False)
def get_market_data():
    out = []
    for name, symbol in TICKERS.items():
        try:
            closes = yf.Ticker(symbol).history(period="1mo", interval="1d")["Close"].dropna()
            if len(closes) >= 2:
                cur, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
                out.append({
                    "name": name, "price": cur, "chg": (cur - prev) / prev * 100,
                    "bullish": cur > float(closes.tail(5).mean()),  # same 5-day SMA rule as before
                    "spark": [float(x) for x in closes.tail(20)],
                })
        except Exception:
            pass
    return out


# ==========================================
# 4. GOOGLE NEWS RSS
# ==========================================
@st.cache_data(ttl=25, show_spinner=False)
def fetch_market_news():
    items, seen = [], set()
    for url in RSS_URLS:
        try:
            feed = feedparser.parse(url)
        except Exception:
            continue
        for e in feed.entries[:10]:
            title = (e.get("title") or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            head, _, src = title.rpartition(" - ")
            ts = calendar.timegm(e.published_parsed) if e.get("published_parsed") else 0
            items.append({
                "title": title, "headline": head or title, "source": src if head else "Google News",
                "link": e.get("link", "#"), "ts": ts,
            })
    items.sort(key=lambda x: x["ts"], reverse=True)
    return items


# ==========================================
# 5. GEMINI (auto-falls back if a model is retired)
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def discover_flash_models(key):
    genai.configure(api_key=key)
    names = []
    for m in genai.list_models():
        n = m.name.replace("models/", "")
        if "generateContent" in m.supported_generation_methods and "flash" in n \
                and not re.search(r"image|tts|live|audio|embedding|thinking|8b", n):
            names.append(n)

    def rank(n):
        v = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return (bool(re.search("preview|exp", n)), "lite" in n, -(float(v.group(1)) if v else 0))

    return sorted(names, key=rank)


def call_gemini(prompt, json_mode=False):
    key = get_api_key()
    if not key:
        raise RuntimeError("Gemini API key missing")
    genai.configure(api_key=key)

    cands = [ss.gemini_model] if ss.gemini_model else []
    try:
        if "GEMINI_MODEL" in st.secrets:
            cands.append(st.secrets["GEMINI_MODEL"])
    except Exception:
        pass
    cands.append(PREFERRED_MODEL)
    try:
        cands += discover_flash_models(key)
    except Exception:
        pass
    cands = [m for i, m in enumerate(cands) if m and m not in cands[:i] and m not in ss.dead_models]

    cfg = {"temperature": 0.3}
    if json_mode:
        cfg["response_mime_type"] = "application/json"
    last = None
    for name in cands:
        try:
            resp = genai.GenerativeModel(name).generate_content(prompt, generation_config=cfg)
            ss.gemini_model = name
            return resp.text
        except Exception as e:
            last = e
            if "404" in str(e) or "not found" in str(e).lower():
                ss.dead_models.add(name)  # retired model: never try it again this session
    raise RuntimeError(f"Gemini failed: {str(last)[:120]}")


def classify_headlines(items):
    lines = "\n".join(f"{i}. {it['headline']}" for i, it in enumerate(items))
    prompt = f"""You are an Indian equity and options analyst. For each headline decide its likely
impact on NIFTY 50 / SENSEX over the next session.

Return ONLY a JSON array, one object per headline:
[{{"id": 0, "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL", "reason": "one line, max 22 words, how and why it moves Nifty/Sensex or options flow"}}]

HEADLINES:
{lines}"""
    raw = call_gemini(prompt, json_mode=True)
    arr = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
    out = {}
    for row in arr:
        i = int(row["id"])
        s = str(row.get("sentiment", "NEUTRAL")).upper()
        out[items[i]["title"]] = {
            "sentiment": s if s in BADGES else "NEUTRAL",
            "reason": str(row.get("reason", "")).strip(),
        }
    return out


# ==========================================
# 6. ALERTS (toast + glowing banner)
# ==========================================
def raise_alert(item, cls):
    up = cls["sentiment"] == "BULLISH"
    direction = "UPSIDE" if up else "DOWNSIDE"
    ss.alerts.insert(0, {"headline": item["headline"], "direction": direction, "reason": cls["reason"], "ts": time.time()})
    ss.alerts = ss.alerts[:10]
    st.toast(f"**{direction}** · {item['headline']}\n\n{cls['reason']}", icon="🟢" if up else "🔴")


@st.fragment(run_every="3s")
def alert_banner():
    live = [a for a in ss.alerts if time.time() - a["ts"] < 90][:3]
    if not live:
        return
    html = ""
    for a in live:
        up = a["direction"] == "UPSIDE"
        c, g = (UP, "rgba(45,212,160,.55)") if up else (DN, "rgba(255,107,107,.55)")
        html += (
            f'<div class="banner mp" style="--c:{c};--g:{g}">'
            f'<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:6px">'
            f'<span class="chip" style="background:{c};color:#0B0F14">BREAKING · {a["direction"]}</span>'
            f'<span class="mono" style="font-size:12px;color:{MUTED}">{ago(a["ts"])}</span></div>'
            f'<div style="font-size:16px;font-weight:600;margin-bottom:4px">{esc(a["headline"])}</div>'
            f'<div style="font-size:13px;color:#C5CED9">Why it moves Nifty/Sensex: {esc(a["reason"])}</div></div>'
        )
    md(html)


# ==========================================
# 7. LIVE TERMINAL (header, option chain, OI bars, global cues)
# ==========================================
def metric_card(label, value, sub, color):
    return (f'<div class="card"><div class="lbl">{label}</div><div class="val">{value}</div>'
            f'<div class="sub" style="color:{color}">{sub}</div></div>')


def market_card(m):
    up = m["chg"] >= 0
    c = UP if up else DN
    tc = UP if m["bullish"] else DN
    return (
        f'<div class="mcard"><div class="row"><span>{esc(m["name"])}</span>'
        f'<span style="color:{tc}">{"Bullish" if m["bullish"] else "Bearish"}</span></div>'
        f'<div style="display:flex;justify-content:space-between;align-items:flex-end;gap:8px"><div>'
        f'<div class="mono" style="font-size:20px;font-weight:500">{m["price"]:,.2f}</div>'
        f'<div class="mono" style="font-size:12px;color:{c};margin-top:2px">{"▲ +" if up else "▼ "}{m["chg"]:.2f}%</div></div>'
        f'<svg width="84" height="30" viewBox="0 0 100 30" aria-hidden="true"><path d="{spark_path(m["spark"])}" '
        f'fill="none" stroke="{c}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></svg></div></div>'
    )


def oi_chart(oi, index_name):
    rows = oi["strikes"]
    mx = max(max(r["CE_OI"], r["PE_OI"]) for r in rows) or 1
    cols = ""
    for r in rows:
        hot = r["Strike"] in (oi["max_ce_strike"], oi["max_pe_strike"])
        cols += (
            f'<div class="strike"><div class="bars">'
            f'<div class="bar" style="background:{DN};height:{round(r["CE_OI"] / mx * 150)}px"></div>'
            f'<div class="bar" style="background:{UP};height:{round(r["PE_OI"] / mx * 150)}px"></div></div>'
            f'<div class="slab" style="color:{"#E6EDF3" if hot else MUTED}">{int(r["Strike"])}</div></div>'
        )
    return (
        f'<div class="card" style="margin:24px 0"><div style="display:flex;flex-wrap:wrap;justify-content:space-between;gap:12px;margin-bottom:20px">'
        f'<div style="font-size:15px;font-weight:600">{index_name} open interest by strike · spot {oi["spot"]:,.0f}</div>'
        f'<div style="display:flex;gap:16px;font-size:12px;color:{MUTED}">'
        f'<span><span style="display:inline-block;width:10px;height:10px;background:{DN};border-radius:2px;margin-right:6px"></span>Call OI</span>'
        f'<span><span style="display:inline-block;width:10px;height:10px;background:{UP};border-radius:2px;margin-right:6px"></span>Put OI</span></div></div>'
        f'<div class="strikes">{cols}</div></div>'
    )


def _live_terminal():  # wrapped as a fragment below, once refresh_rate is known
    t0 = time.perf_counter()
    market = get_market_data()
    oi = get_live_nse_oi(index_choice)
    latency = round((time.perf_counter() - t0) * 1000)

    if oi.get("status") == "LIVE":
        ss.last_oi[index_choice] = oi
        ss.oi_status = "LIVE"
    elif index_choice in ss.last_oi:
        oi = dict(ss.last_oi[index_choice], status="STALE")
        ss.oi_status = "STALE"
    else:
        ss.oi_status = "UNAVAILABLE"
    if market:
        ss.market = market

    md(f"""
    <div class="head mp"><div>
    <h1 style="margin:0;font-size:28px;font-weight:600;letter-spacing:-.01em">AI Market Pulse</h1>
    <p style="margin:6px 0 0;font-size:14px;color:{MUTED}">Nifty 50 &amp; Sensex terminal: live option chain, global cues and AI news sentiment.</p></div>
    <div class="pill"><span class="dot live" style="background:{UP if oi.get('status') == 'LIVE' else AMB}"></span>
    <span>{oi.get('status', 'N/A')} · refresh {refresh_rate}s · fetch {latency} ms</span></div></div>
    """)

    if oi.get("status") in ("LIVE", "STALE"):
        pcr = oi["pcr"]
        bias, bc = ("Bullish", UP) if pcr > 1.05 else (("Bearish", DN) if pcr < 0.90 else ("Neutral", AMB))
        put_flow = oi["pe_chg_sum"] > oi["ce_chg_sum"]
        stale = f' · stale {ago(oi["ts"])}' if oi["status"] == "STALE" else ""
        md('<div class="grid mp">' + "".join([
            metric_card(f"{index_choice} Live PCR", f"{pcr:.2f}", f"{bias} · {oi['status']}{stale}", bc),
            metric_card("Put support (max PE OI)", f"{oi['max_pe_strike']} PE", f"Net PE chg {fmt(oi['pe_chg_sum'])}", UP),
            metric_card("Call hurdle (max CE OI)", f"{oi['max_ce_strike']} CE", f"Net CE chg {fmt(oi['ce_chg_sum'])}", DN),
            f'<div class="card"><div class="lbl">Intraday OI flow</div>'
            f'<div style="font-size:20px;font-weight:600;margin:14px 0 10px">{"Put Writing" if put_flow else "Call Writing"}</div>'
            f'<div class="sub" style="color:{UP if put_flow else DN}">{"▲ Fresh puts (upside)" if put_flow else "▼ Fresh calls (downside)"}</div></div>',
        ]) + "</div>")
        md(f'<div class="mp">{oi_chart(oi, index_choice)}</div>')
    else:
        md(f'<div class="card mp" style="margin-bottom:24px"><div class="h2">Option chain unavailable</div>'
           f'<div style="font-size:13px;color:{MUTED}">{esc(oi.get("error", "No data"))}. Retrying automatically.</div></div>')

    cues = "".join(market_card(m) for m in market) or f'<div class="lbl">Yahoo Finance returned no data.</div>'
    md(f'<div class="mp"><div class="h2">Global and domestic cues</div><div class="grid">{cues}</div></div>')


# ==========================================
# 8. BREAKING NEWS ALERT ENGINE
# ==========================================
def news_row(i, it):
    cls = ss.classified.get(it["title"])
    if cls:
        label, color, bg = BADGES[cls["sentiment"]]
        reason = cls["reason"] or "No reason returned."
        badge = f'<span class="badge" style="color:{color};background:{bg}">{label}</span>'
        why = f'<div style="font-size:13px;color:#C5CED9;margin-top:6px"><span style="color:{MUTED}">Market impact:</span> {esc(reason)}</div>'
    else:
        badge = f'<span class="badge" style="color:{MUTED};background:rgba(139,152,169,.14)">… ANALYZING</span>'
        why = f'<div style="font-size:13px;color:{MUTED};margin-top:6px">Waiting for Gemini impact analysis.</div>'
    new = ""
    if time.time() - ss.new_marks.get(it["title"], 0) < 120:
        new = f'<span class="chip" style="background:{ACCENT};color:#0B0F14;margin-left:8px">NEW</span>'
    return (
        f'<div class="news"><span class="mono" style="color:{MUTED};min-width:22px">{i}</span>'
        f'<div style="flex:1 1 260px;min-width:0"><a href="{esc(it["link"])}" target="_blank" style="font-size:14px;line-height:1.45">{esc(it["headline"])}</a>{new}'
        f'<div class="mono" style="font-size:11px;color:{MUTED};margin-top:4px">{esc(it["source"])} · {ago(it["ts"])}</div>{why}</div>{badge}</div>'
    )


@st.fragment(run_every=NEWS_POLL)
def news_engine():
    items = fetch_market_news()
    ss.last_poll = time.strftime("%H:%M:%S")
    primed = ss.news_primed  # first poll only seeds seen_headlines (no alert flood)

    new_items = [it for it in items if it["title"] not in ss.seen_headlines]
    ss.seen_headlines.update(it["title"] for it in items)
    shown = items[:MAX_HEADLINES]
    ss.news_items = shown
    current = {it["title"] for it in items}
    ss.pending_alerts = {t for t in ss.pending_alerts if t in current}
    if primed:
        for it in new_items:
            ss.new_marks[it["title"]] = time.time()
            ss.pending_alerts.add(it["title"])

    # classify: pending alerts first, then new items, then whatever is shown but unclassified
    todo = []
    for it in [x for x in items if x["title"] in ss.pending_alerts] + new_items + shown:
        if it["title"] not in ss.classified and it not in todo:
            todo.append(it)
    todo = todo[:12]

    ss.news_err = None
    if todo:
        if not get_api_key():
            ss.news_err = "Add GEMINI_API_KEY to enable impact analysis and alerts."
        else:
            try:
                ss.classified.update(classify_headlines(todo))
            except Exception as e:
                ss.news_err = f"AI analysis retrying next poll: {str(e)[:110]}"

    for it in items:  # fire alerts for market-moving brand-new headlines
        t = it["title"]
        if t in ss.pending_alerts and t in ss.classified:
            ss.pending_alerts.discard(t)
            if ss.classified[t]["sentiment"] != "NEUTRAL":
                raise_alert(it, ss.classified[t])
    ss.news_primed = True

    err = f'<span style="color:{AMB};font-size:12px">{esc(ss.news_err)}</span>' if ss.news_err else ""
    rows = "".join(news_row(i, it) for i, it in enumerate(shown, 1)) or \
        f'<div style="color:{MUTED};font-size:13px;padding:12px 0">No headlines returned by Google News RSS.</div>'
    md(f"""
    <div class="mp"><div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:10px;margin-bottom:6px">
    <div class="h2" style="margin:0">Breaking news alert engine · 24h</div>
    <div class="pill"><span class="dot live" style="background:{UP}"></span>polling {NEWS_POLL} · last {ss.last_poll} · {len(ss.seen_headlines)} tracked</div></div>
    <div style="margin-bottom:6px">{err}</div>{rows}</div>
    """)


# ==========================================
# 9. AI DIRECTIONAL VERDICT (real Gemini call)
# ==========================================
def run_verdict():
    if not get_api_key():
        st.error("API key missing. Add `GEMINI_API_KEY` in Streamlit secrets.")
        return
    oi = ss.last_oi.get(index_choice)
    if oi:
        oi_txt = (f"PCR {oi['pcr']}; Max PE OI support {oi['max_pe_strike']}; Max CE OI resistance {oi['max_ce_strike']}; "
                  f"Net PE OI change {oi['pe_chg_sum']}; Net CE OI change {oi['ce_chg_sum']}; spot {oi['spot']:.0f}")
    else:
        oi_txt = "Option chain unavailable (NSE blocked or offline)."
    mkt_txt = "\n".join(f"{m['name']}: {m['price']:.2f} ({m['chg']:+.2f}%)" for m in ss.market) or "N/A"
    news_txt = "\n".join(
        f"- {it['headline']} [{ss.classified.get(it['title'], {}).get('sentiment', 'UNSCORED')}]" for it in ss.news_items
    ) or "N/A"
    prompt = f"""You are an elite Indian Stock Market Quantitative and Options Strategist.
Analyze the real-time option chain, global cues and headlines to predict the immediate direction for NIFTY 50 and SENSEX.

LIVE OPTION CHAIN ({index_choice}): {oi_txt}

LIVE GLOBAL & MACRO DATA:
{mkt_txt}

LATEST HEADLINES (with AI sentiment):
{news_txt}

Return ONLY JSON:
{{"score": <integer -100 to 100>, "bias": "UPSIDE" | "DOWNSIDE" | "SIDEWAYS",
"confluence": "2-3 crisp sentences on how option writers (PCR/OI walls) and global cues (US markets, crude, VIX) align right now",
"plan": "scalper execution plan with exact levels: buy on dips, sell on rise, or avoid theta decay"}}"""
    with st.spinner("Processing live OI, macro cues and news sentiment..."):
        t0 = time.perf_counter()
        try:
            raw = call_gemini(prompt, json_mode=True)
            data = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            ss.verdict = {
                "score": max(-100, min(100, int(data["score"]))), "bias": str(data["bias"]).upper(),
                "confluence": data["confluence"], "plan": data["plan"],
                "ms": round((time.perf_counter() - t0) * 1000), "model": ss.gemini_model, "index": index_choice,
            }
        except Exception as e:
            ss.verdict = None
            st.error(f"Execution Error: {e}")


def verdict_html(v):
    c = {"UPSIDE": UP, "DOWNSIDE": DN}.get(v["bias"], AMB)
    label = {"UPSIDE": "UPSIDE (Bullish)", "DOWNSIDE": "DOWNSIDE (Bearish)"}.get(v["bias"], "SIDEWAYS (Range-bound)")
    return f"""
    <div class="mp" style="margin-top:16px">
    <div style="display:flex;align-items:baseline;justify-content:space-between;gap:12px">
    <div class="mono" style="font-size:40px;font-weight:500;color:{c}">{v['score']:+d}</div>
    <div style="font-size:13px;font-weight:600;color:{c}">{label}</div></div>
    <div class="gauge"><div class="knob" style="left:{(v['score'] + 100) / 2}%"></div></div>
    <div class="mono" style="display:flex;justify-content:space-between;font-size:11px;color:{MUTED}"><span>-100</span><span>0</span><span>+100</span></div>
    <div style="font-size:13px;line-height:1.55;margin-top:14px"><span style="color:{MUTED}">OI and global confluence. </span>{esc(v['confluence'])}</div>
    <div style="font-size:13px;line-height:1.55;margin-top:12px;padding:12px;border-radius:8px;background:#0B0F14;border:1px solid #1F2A37"><span style="color:{MUTED}">Scalper plan. </span>{esc(v['plan'])}</div>
    <div style="font-size:12px;color:{MUTED};margin-top:10px">{v['index']} · generated in {v['ms']:,} ms · {esc(v['model'])}</div></div>
    """


def verdict_panel():
    md('<div class="h2 mp">Instant AI directional verdict</div>')
    if st.button("Generate live market verdict", use_container_width=True):
        run_verdict()
    if ss.verdict:
        md(verdict_html(ss.verdict))
    else:
        md(f'<div class="mp" style="font-size:13px;color:{MUTED};line-height:1.5;margin-top:12px">'
           f'Combines the live option chain, global cues and scored headlines into a sentiment score, bias and scalper plan.</div>')


# ==========================================
# 10. SIDEBAR
# ==========================================
@st.fragment(run_every="10s")
def sidebar_status():
    def line(ok, text, warn=False):
        c = UP if ok else (AMB if warn else DN)
        return f'<div style="display:flex;align-items:center;gap:10px;font-size:13px;margin:8px 0"><span class="dot" style="background:{c}"></span>{text}</div>'
    nse = ss.oi_status
    md('<div class="mp"><div class="cap" style="margin-bottom:6px">System status</div>' + "".join([
        line(bool(get_api_key()), "API key loaded" if get_api_key() else "API key missing"),
        line(nse == "LIVE", f"NSE option chain: {nse}", warn=nse in ("STALE", "waiting")),
        line(bool(ss.market), "Yahoo Finance feed"),
        line(bool(ss.gemini_model), f"Gemini: {esc(ss.gemini_model)}" if ss.gemini_model else "Gemini: not called yet", warn=True),
    ]) + "</div>")


md(CSS)
with st.sidebar:
    sidebar_status()
    st.markdown("---")
    st.caption("PRIMARY INDEX")
    index_choice = st.radio("Primary index", ["NIFTY", "BANKNIFTY"], horizontal=True, label_visibility="collapsed")
    st.caption("REFRESH (SECONDS)")
    refresh_rate = st.radio("Refresh", [5, 15, 30, 60], index=1, horizontal=True, label_visibility="collapsed")
    if not get_api_key():
        st.text_input("Fallback: enter Gemini API key", type="password", key="api_key_input")
    st.caption("Engine: NSE JSON + Yahoo Finance + Google News RSS + Gemini")

live_terminal = st.fragment(run_every=f"{refresh_rate}s")(_live_terminal)

alert_banner()
live_terminal()
left, right = st.columns([1.25, 1], gap="large")
with left:
    with st.container(border=True):
        news_engine()
with right:
    with st.container(border=True):
        verdict_panel()
