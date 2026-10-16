"""
AI Market Pulse: Nifty 50 & BankNifty Terminal
Dark fintech UI + INDstocks live option chain + yfinance + Google News RSS + Gemini
Breaking-news alert engine (30s polling, toast + glowing banner, per-headline AI impact).

Run:  streamlit run app.py
Secrets (.streamlit/secrets.toml):
    GEMINI_API_KEY        = "your-gemini-key"
    INDSTOCKS_ACCESS_TOKEN = "token from indstocks.com/app/api-trading"
Optional:
    INDSTOCKS_BANKNIFTY_ID = "SECURITY_ID of the BANK NIFTY index (auto-looked-up if omitted)"
    GEMINI_MODEL           = "gemini-2.5-flash"
"""
import calendar
import io
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
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
.news.high-impact { border:1px solid var(--hc); border-radius:12px; padding:12px 14px; margin:8px 0; background:rgba(17,24,33,.9); animation:hiPulse 2.2s ease-in-out infinite; }
@keyframes hiPulse { 0%,100% { box-shadow:0 0 6px var(--hg); } 50% { box-shadow:0 0 24px var(--hg); } }
.ev { display:flex; flex-wrap:wrap; gap:12px; padding:12px 0; border-top:1px solid #1F2A37; }
.today { animation:todayGlow 1.8s ease-in-out infinite; }
@keyframes todayGlow { 0%,100% { box-shadow:0 0 4px rgba(91,140,255,.4); } 50% { box-shadow:0 0 16px rgba(91,140,255,.9); } }
@media (prefers-reduced-motion: reduce) { .news.high-impact, .today, .banner, .live { animation:none; } }
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
# 1. SECRETS & KEYS
# ==========================================
IND_TOKEN_NAMES = ("INDSTOCKS_ACCESS_TOKEN", "INDSTOCKS_TOKEN", "INDSTOCKS_AUTH_TOKEN", "INDSTOCKS_API_KEY")


def _secret(*names):
    try:
        for n in names:
            if n in st.secrets and st.secrets[n]:
                return st.secrets[n]
    except Exception:
        pass
    return None


def get_gemini_key():
    return _secret("GEMINI_API_KEY") or ss.get("saved_gemini_key")


def get_indstocks_token():
    return _secret(*IND_TOKEN_NAMES) or ss.get("saved_indstocks_token")


# ==========================================
# 2. INDSTOCKS LIVE OPTION CHAIN (official endpoints)
#    GET /market/instruments/expiries  -> nearest expiry
#    GET /market/option-chain          -> strikes{} with ce/pe oi + previous_oi
# ==========================================
INDSTOCKS_BASE = "https://api.indstocks.com"
NIFTY_SCRIP = "40000001"   # SECURITY_ID of the NIFTY 50 index (INDstocks docs)
STRIKE_COUNT = 25          # strikes per side of ATM used for PCR / OI walls


def _ind_headers(token):
    token = token.strip()
    if token.lower().startswith("bearer "):   # INDstocks expects the raw token
        token = token[7:].strip()
    return {"Authorization": token, "Accept": "application/json"}


def _ind_error(resp):
    try:
        j = resp.json()
        detail = j.get("debug_info") or j.get("message") or j.get("error") or ""
    except Exception:
        detail = resp.text[:80]
    return f"INDstocks HTTP {resp.status_code}: {str(detail)[:100]}"


@st.cache_data(ttl=3600, show_spinner=False)
def resolve_index_scrip(symbol, token):
    """Returns (security_id, error). NIFTY is fixed; BANKNIFTY is looked up in the instruments master."""
    if symbol == "NIFTY":
        return NIFTY_SCRIP, None
    override = _secret("INDSTOCKS_BANKNIFTY_ID")
    if override:
        return str(override), None
    try:
        resp = requests.get(f"{INDSTOCKS_BASE}/market/instruments", params={"source": "index"},
                            headers=_ind_headers(token), timeout=15)
        if resp.status_code != 200:
            return None, _ind_error(resp)
        df = pd.read_csv(io.StringIO(resp.text))
        id_col = {c.upper(): c for c in df.columns}.get("SECURITY_ID")
        if not id_col:
            return None, "Instruments file has no SECURITY_ID column"
        wanted = {"NIFTY BANK", "BANKNIFTY", "NIFTY BANK INDEX"}
        for c in df.columns:
            hit = df[df[c].astype(str).str.strip().str.upper().isin(wanted)]
            if not hit.empty:
                return str(int(hit.iloc[0][id_col])), None
        return None, "BANKNIFTY not found in instruments file; set INDSTOCKS_BANKNIFTY_ID in secrets"
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:90]}"


@st.cache_data(ttl=600, show_spinner=False)
def get_nearest_expiry(symbol, token):
    """Returns (YYYY-MM-DD, error). The option-chain endpoint requires an explicit expiry."""
    try:
        resp = requests.get(f"{INDSTOCKS_BASE}/market/instruments/expiries",
                            params={"underlying": symbol, "segment": "DERIVATIVE"},
                            headers=_ind_headers(token), timeout=8)
        if resp.status_code != 200:
            return None, _ind_error(resp)
        today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date().isoformat()
        upcoming = sorted(d for d in (resp.json().get("data") or []) if d >= today)
        return (upcoming[0], None) if upcoming else (None, "No upcoming expiry returned")
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:90]}"


@st.cache_data(ttl=8, show_spinner=False)
def get_live_indstocks_oi(symbol, token):
    if not token:
        return {"status": "ERROR", "error": "INDstocks access token missing"}
    try:
        scrip, err = resolve_index_scrip(symbol, token)
        if err:
            return {"status": "ERROR", "error": err}
        expiry, err = get_nearest_expiry(symbol, token)
        if err:
            return {"status": "ERROR", "error": err}

        resp = None
        for count in (STRIKE_COUNT, None):  # retry with the API default if the wider ladder is rejected
            params = {"exchange": "NSE", "segment": "INDEX", "underlying-scrip": scrip, "expiry": expiry}
            if count:
                params["strike_count"] = count
            resp = requests.get(f"{INDSTOCKS_BASE}/market/option-chain", params=params,
                                headers=_ind_headers(token), timeout=8)
            if resp.status_code != 400:
                break
        if resp.status_code != 200:
            return {"status": "ERROR", "error": _ind_error(resp)}

        data = resp.json().get("data") or {}
        rows = []
        for strike, leg in (data.get("strikes") or {}).items():
            ce, pe = leg.get("ce") or {}, leg.get("pe") or {}
            ce_oi, pe_oi = ce.get("oi") or 0, pe.get("oi") or 0
            rows.append({
                "Strike": float(strike), "CE_OI": ce_oi, "PE_OI": pe_oi,
                "CE_Chg_OI": ce_oi - (ce.get("previous_oi") or 0),   # day-over-day OI change
                "PE_Chg_OI": pe_oi - (pe.get("previous_oi") or 0),
            })
        if not rows:
            return {"status": "ERROR", "error": "INDstocks returned an empty option chain"}

        df = pd.DataFrame(rows).sort_values("Strike").reset_index(drop=True)  # strikes{} is unordered
        tot_ce, tot_pe = df["CE_OI"].sum(), df["PE_OI"].sum()
        max_ce = int(df.loc[df["CE_OI"].idxmax(), "Strike"])
        max_pe = int(df.loc[df["PE_OI"].idxmax(), "Strike"])
        spot = float(data.get("underlying_ltp") or (max_ce + max_pe) / 2)
        atm_i = int((df["Strike"] - spot).abs().idxmin())
        window = df.iloc[max(0, atm_i - 4): atm_i + 5]  # 9 strikes around ATM

        return {
            "status": "LIVE", "pcr": round(float(tot_pe / tot_ce), 2) if tot_ce > 0 else 0.0,
            "max_ce_strike": max_ce, "max_pe_strike": max_pe,
            "ce_chg_sum": int(df["CE_Chg_OI"].sum()), "pe_chg_sum": int(df["PE_Chg_OI"].sum()),
            "spot": spot, "expiry": data.get("expiry") or expiry,
            "strikes": window.to_dict("records"), "ts": time.time(),
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
    key = get_gemini_key()
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
[{{"id": 0, "sentiment": "BULLISH" | "BEARISH" | "NEUTRAL", "impact": "HIGH" | "MEDIUM" | "LOW", "reason": "one line, max 22 words, how and why it moves Nifty/Sensex or options flow"}}]

Impact rubric:
- HIGH: likely to move Nifty/Sensex by roughly 0.5% or more, or reprice index options (central-bank decisions, major policy or tariff shocks, large FII/DII flow shifts, geopolitical or crude shocks, news from index heavyweights).
- MEDIUM: clearly affects a major sector or a few heavyweights, limited index-level effect.
- LOW: minor, routine or already priced-in news.

HEADLINES:
{lines}"""
    raw = call_gemini(prompt, json_mode=True)
    arr = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
    out = {}
    for row in arr:
        i = int(row["id"])
        s = str(row.get("sentiment", "NEUTRAL")).upper()
        imp = str(row.get("impact", "LOW")).upper()
        out[items[i]["title"]] = {
            "sentiment": s if s in BADGES else "NEUTRAL",
            "impact": imp if imp in ("HIGH", "MEDIUM", "LOW") else "LOW",
            "reason": str(row.get("reason", "")).strip(),
        }
    return out


# ==========================================
# 6. ALERTS (toast + glowing banner)
# ==========================================
def raise_alert(item, cls):
    up = cls["sentiment"] == "BULLISH"
    direction = "UPSIDE" if up else "DOWNSIDE"
    impact = cls.get("impact", "LOW")
    ss.alerts.insert(0, {"headline": item["headline"], "direction": direction, "impact": impact,
                         "reason": cls["reason"], "ts": time.time()})
    ss.alerts = ss.alerts[:10]
    hot = "🔥 HIGH IMPACT · " if impact == "HIGH" else ""
    st.toast(f"**{hot}{direction}** · {item['headline']}\n\n{cls['reason']}", icon="🟢" if up else "🔴")


@st.fragment(run_every="3s")
def alert_banner():
    live = [a for a in ss.alerts if time.time() - a["ts"] < 90][:3]
    if not live:
        return
    html = ""
    for a in live:
        up = a["direction"] == "UPSIDE"
        hot_chip = (f'<span class="chip" style="background:{AMB};color:#0B0F14">🔥 HIGH IMPACT</span>'
                    if a.get("impact") == "HIGH" else "")
        c, g = (UP, "rgba(45,212,160,.55)") if up else (DN, "rgba(255,107,107,.55)")
        html += (
            f'<div class="banner mp" style="--c:{c};--g:{g}">'
            f'<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-bottom:6px">'
            f'<span class="chip" style="background:{c};color:#0B0F14">BREAKING · {a["direction"]}</span>{hot_chip}'
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
        f'<div style="font-size:15px;font-weight:600">{index_name} INDstocks OI distribution · exp {oi.get("expiry", "")} · spot {oi["spot"]:,.0f}</div>'
        f'<div style="display:flex;gap:16px;font-size:12px;color:{MUTED}">'
        f'<span><span style="display:inline-block;width:10px;height:10px;background:{DN};border-radius:2px;margin-right:6px"></span>Call OI</span>'
        f'<span><span style="display:inline-block;width:10px;height:10px;background:{UP};border-radius:2px;margin-right:6px"></span>Put OI</span></div></div>'
        f'<div class="strikes">{cols}</div></div>'
    )


def _live_terminal():  # wrapped as a fragment below, once refresh_rate is known
    t0 = time.perf_counter()
    market = get_market_data()
    oi = get_live_indstocks_oi(index_choice, get_indstocks_token())
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
    <p style="margin:6px 0 0;font-size:14px;color:{MUTED}">INDstocks Option Chain &bull; yfinance Macro &bull; Gemini Intelligence</p></div>
    <div class="pill"><span class="dot live" style="background:{UP if oi.get('status') == 'LIVE' else AMB}"></span>
    <span>INDstocks: {oi.get('status', 'N/A')} · refresh {refresh_rate}s · fetch {latency} ms</span></div></div>
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
        md(f'<div class="card mp" style="margin-bottom:24px"><div class="h2">INDstocks feed notice</div>'
           f'<div style="font-size:13px;color:{MUTED}">{esc(oi.get("error", "No data"))}. Check the access token and your IP whitelist.</div></div>')

    cues = "".join(market_card(m) for m in market) or f'<div class="lbl">Yahoo Finance returned no data.</div>'
    md(f'<div class="mp"><div class="h2">Global and domestic cues</div><div class="grid">{cues}</div></div>')


# ==========================================
# 8. BREAKING NEWS ALERT ENGINE
# ==========================================
GLOW = {
    "BULLISH": (UP, "rgba(45,212,160,.5)"),
    "BEARISH": (DN, "rgba(255,107,107,.5)"),
    "NEUTRAL": (AMB, "rgba(245,184,75,.5)"),
}


def news_row(i, it):
    cls = ss.classified.get(it["title"])
    high = bool(cls and cls.get("impact") == "HIGH")
    impact_txt, row_cls, row_style = "", "news", ""
    if cls:
        label, color, bg = BADGES[cls["sentiment"]]
        reason = cls["reason"] or "No reason returned."
        badge = f'<span class="badge" style="color:{color};background:{bg}">{label}</span>'
        if high:
            hc, hg = GLOW[cls["sentiment"]]
            badge = f'<span class="badge" style="color:#0B0F14;background:{AMB}">🔥 HIGH IMPACT</span>' + badge
            row_cls, row_style = "news high-impact", f' style="--hc:{hc};--hg:{hg}"'
        else:
            impact_txt = f' · {cls.get("impact", "LOW")} impact'
        why = f'<div style="font-size:13px;color:#C5CED9;margin-top:6px"><span style="color:{MUTED}">Market impact:</span> {esc(reason)}</div>'
    else:
        badge = f'<span class="badge" style="color:{MUTED};background:rgba(139,152,169,.14)">… ANALYZING</span>'
        why = f'<div style="font-size:13px;color:{MUTED};margin-top:6px">Waiting for Gemini impact analysis.</div>'
    new = ""
    if time.time() - ss.new_marks.get(it["title"], 0) < 120:
        new = f'<span class="chip" style="background:{ACCENT};color:#0B0F14;margin-left:8px">NEW</span>'
    return (
        f'<div class="{row_cls}"{row_style}><span class="mono" style="color:{MUTED};min-width:22px">{i}</span>'
        f'<div style="flex:1 1 260px;min-width:0"><a href="{esc(it["link"])}" target="_blank" style="font-size:14px;line-height:1.45">{esc(it["headline"])}</a>{new}'
        f'<div class="mono" style="font-size:11px;color:{MUTED};margin-top:4px">{esc(it["source"])} · {ago(it["ts"])}{impact_txt}</div>{why}</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:6px;align-self:flex-start">{badge}</div></div>'
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
        if not get_gemini_key():
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
    if not get_gemini_key():
        st.error("API key missing. Add `GEMINI_API_KEY` in Streamlit secrets.")
        return
    oi = ss.last_oi.get(index_choice)
    if oi:
        oi_txt = (f"PCR {oi['pcr']}; Max PE OI support {oi['max_pe_strike']}; Max CE OI resistance {oi['max_ce_strike']}; "
                  f"Net PE OI change {oi['pe_chg_sum']}; Net CE OI change {oi['ce_chg_sum']}; spot {oi['spot']:.0f}")
    else:
        oi_txt = "Option chain unavailable."
    mkt_txt = "\n".join(f"{m['name']}: {m['price']:.2f} ({m['chg']:+.2f}%)" for m in ss.market) or "N/A"
    news_txt = "\n".join(
        f"- {it['headline']} [{ss.classified.get(it['title'], {}).get('sentiment', 'UNSCORED')} / {ss.classified.get(it['title'], {}).get('impact', '?')} impact]" for it in ss.news_items
    ) or "N/A"
    prompt = f"""You are an elite Indian Stock Market Quantitative and Options Strategist.
Analyze the real-time option chain, global cues and headlines to predict the immediate direction for NIFTY 50 and SENSEX.

LIVE INDSTOCKS OPTION CHAIN ({index_choice}): {oi_txt}

LIVE GLOBAL & MACRO DATA:
{mkt_txt}

LATEST HEADLINES (with AI sentiment / impact):
{news_txt}

Return ONLY JSON:
{{"score": <integer -100 to 100>, "bias": "UPSIDE" | "DOWNSIDE" | "SIDEWAYS",
"confluence": "2-3 crisp sentences on how option writers (PCR/OI walls) and global cues (US markets, crude, VIX) align right now",
"plan": "scalper execution plan with exact levels: buy on dips, sell on rise, or avoid theta decay"}}"""
    with st.spinner("Processing live INDstocks OI, macro cues and news sentiment..."):
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
           f'Combines the INDstocks option chain, global cues and scored headlines into an instant bias and scalp plan.</div>')


# ==========================================
# 10. UPCOMING MARKET CATALYSTS (countdown calendar)
#     Dates from the published RBI MPC, Federal Reserve and BLS schedules (checked 16 Oct 2026).
#     Budget, India CPI and F&O expiries are expected / rule-based. Update SCHEDULED_EVENTS yearly.
# ==========================================
IST = timezone(timedelta(hours=5, minutes=30))

SCHEDULED_EVENTS = [
    # (date, kind, title, time, impact, status, analysis)
    (date(2026, 10, 28), "FED", "US Fed FOMC rate decision", "~23:30 IST", "HIGH", "SCHEDULED",
     "Rate-path and press-conference tone drive the dollar, Treasury yields and FII flows. A hawkish surprise pressures IT and Banks and lifts India VIX; the reaction lands at the next Indian open, so overnight short straddles carry gap risk."),
    (date(2026, 11, 10), "CPI", "US CPI inflation (October data)", "19:00 IST", "HIGH", "SCHEDULED",
     "A hot print lifts yields and the dollar and triggers FII selling in IT and Banks; a cool print revives rate-cut hopes and supports Nifty. It lands after Indian hours, so GIFT Nifty and the next open absorb the move."),
    (date(2026, 12, 4), "RBI", "RBI MPC rate decision", "Decision day", "HIGH", "SCHEDULED",
     "Rate decision and stance set the tone for the next cycle. A hawkish tilt hurts rate-sensitive Banks, NBFCs and Realty, while a dovish tone helps Bank Nifty. Options usually carry an IV bump into the announcement and an IV crush right after."),
    (date(2026, 12, 9), "FED", "US Fed FOMC + dot plot", "~00:30 IST (Dec 10)", "HIGH", "SCHEDULED",
     "Includes the projections and dot plot, so yield, dollar and VIX reactions tend to be larger than at a standard meeting. Banks and IT are the most FII-sensitive; expect a gap risk at the next Indian open."),
    (date(2026, 12, 10), "CPI", "US CPI inflation (November data)", "19:00 IST", "HIGH", "SCHEDULED",
     "The last CPI before the January FOMC, so a surprise reprices Fed expectations quickly. Hot prints are negative for IT and Banks via yields and FII flows; cool prints are supportive."),
    (date(2027, 1, 27), "FED", "US Fed FOMC rate decision", "~00:30 IST (Jan 28)", "HIGH", "SCHEDULED",
     "First Fed decision of 2027 and the last big macro event before the Union Budget. Hawkish guidance strengthens the dollar and pressures FII-heavy sectors; expect IV to build into the decision."),
    (date(2027, 2, 1), "BUDGET", "Union Budget FY28", "11:00 IST", "HIGH", "EXPECTED",
     "Fiscal deficit, capex and tax changes (STT, capital gains) can re-rate sectors within minutes. Budget day typically carries elevated event IV, so ATM straddles price a large move and punish sellers on a surprise."),
    (date(2027, 2, 5), "RBI", "RBI MPC rate decision", "Decision day", "HIGH", "SCHEDULED",
     "First policy after the Union Budget, so fiscal and borrowing signals feed into the rate view. Watch Banks, NBFCs and Realty, and the post-event IV crush in short-dated options."),
]
INDIA_CPI_NOTE = ("Print versus RBI's comfort band steers rate expectations: above-band is negative for Banks and NBFCs, "
                  "below-band eases pressure. Released after market close, so the reaction comes at the next session.")
WEEKLY_EXPIRY_NOTE = ("Peak gamma and theta: sellers defend OI walls and premiums decay fast, but pin risk and sudden gamma "
                      "spikes hit late buyers. Straddle premiums collapse into the close, so avoid fresh long options late in the session.")
MONTHLY_EXPIRY_NOTE = ("Index and stock F&O all expire together; rollover and OI unwinding widen intraday swings. "
                       "Watch the max-OI strikes on the option chain for pinning, and expect IV to compress after expiry.")
KIND_LABEL = {"RBI": "RBI MPC", "FED": "US FED", "CPI": "INFLATION", "BUDGET": "BUDGET", "EXPIRY": "F&O EXPIRY"}


def _next_weekday(d, weekday):
    return d + timedelta(days=(weekday - d.weekday()) % 7)


def _last_weekday_of_month(y, m, weekday):
    last = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def build_events(today, horizon=120):
    end = today + timedelta(days=horizon)
    evs = [dict(date=d, kind=k, title=t, time=tm, impact=im, status=stt, note=n)
           for (d, k, t, tm, im, stt, n) in SCHEDULED_EVENTS if today <= d <= end]
    y, m, monthly = today.year, today.month, set()
    for _ in range(6):
        exp = _last_weekday_of_month(y, m, 1)  # last Tuesday (NSE monthly F&O)
        if today <= exp <= end:
            monthly.add(exp)
            evs.append(dict(date=exp, kind="EXPIRY", title="Monthly F&O expiry (Nifty, Bank Nifty, stocks)", time="Last Tuesday",
                            impact="HIGH", status="RULE-BASED", note=MONTHLY_EXPIRY_NOTE))
        cpi = date(y, m, 12)  # MoSPI usually publishes on the 12th; weekends roll to Monday
        cpi += timedelta(days=(7 - cpi.weekday()) % 7) if cpi.weekday() >= 5 else timedelta(0)
        if today <= cpi <= end:
            evs.append(dict(date=cpi, kind="CPI", title="India CPI inflation (MoSPI)", time="~16:00 IST",
                            impact="MEDIUM", status="EXPECTED", note=INDIA_CPI_NOTE))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    wk = _next_weekday(today, 1)  # next Nifty weekly expiry (Tuesday); today counts
    if wk <= end and wk not in monthly:
        evs.append(dict(date=wk, kind="EXPIRY", title="Nifty weekly expiry", time="Tuesday",
                        impact="MEDIUM", status="RULE-BASED", note=WEEKLY_EXPIRY_NOTE))
    evs.sort(key=lambda e: (e["date"], e["impact"] != "HIGH"))
    return evs


def event_row(ev, today):
    d = (ev["date"] - today).days
    label = "TODAY" if d == 0 else f"In {d} day{'s' if d != 1 else ''}"
    c = ACCENT if d == 0 else (AMB if d <= 7 else MUTED)
    cd_bg = "rgba(91,140,255,.16)" if d == 0 else "rgba(139,152,169,.12)"
    hi = ev["impact"] == "HIGH"
    imp = (f'<span class="chip" style="color:{AMB};background:rgba(245,184,75,.14)">HIGH</span>' if hi else
           f'<span class="chip" style="color:{MUTED};background:rgba(139,152,169,.14)">MEDIUM</span>')
    meta = " · ".join(x for x in [ev["date"].strftime("%a %d %b %Y"), ev["time"], KIND_LABEL[ev["kind"]], ev["status"]] if x)
    return (
        f'<div class="ev"><div style="flex:0 0 92px"><span class="chip{" today" if d == 0 else ""}" '
        f'style="color:{c};background:{cd_bg}">{label}</span></div>'
        f'<div style="flex:1 1 220px;min-width:0"><div style="font-size:14px;font-weight:600">{esc(ev["title"])}</div>'
        f'<div class="mono" style="font-size:11px;color:{MUTED};margin:3px 0 6px">{esc(meta)}</div>'
        f'<div style="font-size:13px;line-height:1.5;color:#C5CED9">{esc(ev["note"])}</div></div>{imp}</div>'
    )


@st.fragment(run_every="300s")
def catalyst_calendar():
    today = datetime.now(IST).date()
    show_all = st.toggle("Show full calendar (next 120 days)", key="cal_all")
    evs = build_events(today)
    evs = evs if show_all else evs[:6]
    rows = "".join(event_row(e, today) for e in evs) or \
        f'<div style="color:{MUTED};font-size:13px;padding:12px 0">No catalysts in the next 120 days.</div>'
    md(f"""
    <div class="mp"><div class="h2" style="margin:0 0 2px">Upcoming market catalysts</div>
    <div style="font-size:12px;color:{MUTED};margin-bottom:6px">Macro and F&amp;O events that can move Nifty, Sensex and option premiums.</div>{rows}
    <div style="font-size:11px;color:{MUTED};line-height:1.5;margin-top:8px;border-top:1px solid #1F2A37;padding-top:8px">
    RBI, Fed and US CPI dates follow the published schedules (checked 16 Oct 2026). Budget date, India CPI and expiries are expected or
    rule-based; an exchange holiday moves an expiry to the prior session. Verify before trading.</div></div>
    """)


# ==========================================
# 11. SIDEBAR
# ==========================================
@st.fragment(run_every="10s")
def sidebar_status():
    def line(ok, text, warn=False):
        c = UP if ok else (AMB if warn else DN)
        return f'<div style="display:flex;align-items:center;gap:10px;font-size:13px;margin:8px 0"><span class="dot" style="background:{c}"></span>{text}</div>'
    nse = ss.oi_status
    md('<div class="mp"><div class="cap" style="margin-bottom:6px">System status</div>' + "".join([
        line(bool(get_gemini_key()), "Gemini key active" if get_gemini_key() else "Gemini key missing"),
        line(bool(get_indstocks_token()), "INDstocks token loaded" if get_indstocks_token() else "INDstocks token missing"),
        line(nse == "LIVE", f"INDstocks OI: {nse}", warn=nse in ("STALE", "waiting")),
        line(bool(ss.market), "Macro feeds (yfinance)"),
        line(bool(ss.gemini_model), f"Gemini model: {esc(ss.gemini_model)}" if ss.gemini_model else "Gemini model: not called yet", warn=True),
    ]) + "</div>")


md(CSS)
with st.sidebar:
    sidebar_status()
    st.markdown("---")
    st.caption("PRIMARY INDEX")
    index_choice = st.radio("Primary index", ["NIFTY", "BANKNIFTY"], horizontal=True, label_visibility="collapsed")
    st.caption("REFRESH (SECONDS)")
    refresh_rate = st.radio("Refresh", [5, 15, 30, 60], index=1, horizontal=True, label_visibility="collapsed")
    # Inputs are always rendered while no secret exists; values are copied to non-widget keys so they
    # survive reruns (Streamlit discards the state of a widget that stops being rendered).
    if not _secret(*IND_TOKEN_NAMES):
        v = st.text_input("INDstocks access token", type="password", key="indstocks_token_input")
        if v:
            ss["saved_indstocks_token"] = v
    if not _secret("GEMINI_API_KEY"):
        v = st.text_input("Gemini API key", type="password", key="gemini_key_input")
        if v:
            ss["saved_gemini_key"] = v
    st.caption("Engine: INDstocks API + Yahoo Finance + Google News RSS + Gemini")

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
    with st.container(border=True):
        catalyst_calendar()
