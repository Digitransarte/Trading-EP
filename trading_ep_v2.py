import streamlit as st
import requests
import pandas as pd
from datetime import date, timedelta, datetime
import anthropic
from dotenv import load_dotenv
import os
import json
import re
import time
import yfinance as yf
from functools import lru_cache

# ─── KB INTEGRATION ───────────────────────────────────────────────────────────

def load_kb_adjustments() -> dict:
    """
    Load scanner adjustments from the Knowledge Base (if available).
    Returns empty dict with data_available=False when KB is absent or empty.
    """
    try:
        from knowledge_base import get_scanner_adjustments
        return get_scanner_adjustments()
    except Exception:
        return {"data_available": False, "total_trades_in_kb": 0}



# ─── CONFIG ───────────────────────────────────────────────────────────────────

load_dotenv()
POLYGON_KEY    = os.getenv("POLYGON_API_KEY")
ANTHROPIC_KEY  = os.getenv("ANTHROPIC_API_KEY")
WATCHLIST_FILE = "watchlist.json"

st.set_page_config(page_title="EP Scanner", page_icon="⚡", layout="wide")

st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap');

  body, .stApp { background-color: #070b14; color: #c9d1e0; font-family: 'DM Sans', sans-serif; }
  h1, h2, h3  { color: #e8edf5; font-family: 'Space Mono', monospace; letter-spacing: -0.5px; }

  /* Scores */
  .score-high { color: #00e87a; font-size: 1.8em; font-weight: 700; font-family: 'Space Mono', monospace; }
  .score-mid  { color: #f5c842; font-size: 1.8em; font-weight: 700; font-family: 'Space Mono', monospace; }
  .score-low  { color: #ff5e5e; font-size: 1.8em; font-weight: 700; font-family: 'Space Mono', monospace; }

  /* Entry window badges */
  .prime { color: #00e87a; font-weight: 700; font-family: 'Space Mono', monospace; }
  .open  { color: #f5c842; font-weight: 700; font-family: 'Space Mono', monospace; }
  .late  { color: #ff5e5e; font-weight: 700; font-family: 'Space Mono', monospace; }

  /* Tags */
  .tag { display:inline-block; border-radius:3px; padding:2px 8px; font-size:0.72em;
         font-weight:600; margin-right:4px; font-family:'Space Mono', monospace; letter-spacing:0.3px; }
  .tag-ep        { background:#00e87a15; color:#00e87a; border:1px solid #00e87a30; }
  .tag-canslim   { background:#00b4ff15; color:#00b4ff; border:1px solid #00b4ff30; }
  .tag-growth    { background:#a78bfa15; color:#a78bfa; border:1px solid #a78bfa30; }
  .tag-turnaround{ background:#fb923c15; color:#fb923c; border:1px solid #fb923c30; }
  .tag-story     { background:#f472b615; color:#f472b6; border:1px solid #f472b630; }
  .tag-neglect   { background:#34d39915; color:#34d399; border:1px solid #34d39930; }
  .tag-warn      { background:#fbbf2415; color:#fbbf24; border:1px solid #fbbf2430; }

  /* Metric cards */
  .metric-box { background:#0d1422; border:1px solid #1e2d45; border-radius:6px;
                padding:10px 14px; margin:4px 0; }
  .metric-label { color:#667a99; font-size:0.72em; text-transform:uppercase;
                  letter-spacing:1px; font-family:'Space Mono', monospace; }
  .metric-value { color:#e8edf5; font-size:1.05em; font-weight:600; margin-top:2px; }
  .metric-good  { color:#00e87a; }
  .metric-warn  { color:#f5c842; }
  .metric-bad   { color:#ff5e5e; }

  /* MAGNA breakdown */
  .magna-row { display:flex; align-items:center; gap:10px; padding:5px 0;
               border-bottom:1px solid #1e2d45; }
  .magna-letter { font-family:'Space Mono', monospace; font-weight:700;
                  font-size:1.1em; width:24px; color:#00e87a; }
  .magna-label  { color:#667a99; font-size:0.8em; width:160px; }
  .magna-value  { color:#e8edf5; font-size:0.88em; flex:1; }
  .magna-pass   { color:#00e87a; font-size:0.85em; font-family:'Space Mono', monospace; }
  .magna-fail   { color:#ff5e5e; font-size:0.85em; font-family:'Space Mono', monospace; }
  .magna-warn   { color:#f5c842; font-size:0.85em; font-family:'Space Mono', monospace; }

  /* Buttons */
  .stButton>button { background:#00e87a15; color:#00e87a; border:1px solid #00e87a35;
                     border-radius:6px; padding:8px 22px; font-size:13px;
                     font-family:'Space Mono', monospace; transition:all 0.2s; }
  .stButton>button:hover { background:#00e87a25; border-color:#00e87a60; }

  /* Expanders */
  .streamlit-expanderHeader { background:#0d1422 !important; border:1px solid #1e2d45 !important;
                               border-radius:6px !important; font-family:'DM Sans', sans-serif !important; }
  details { border:none !important; }

  /* Dividers */
  hr { border-color:#1e2d45; }
</style>
""", unsafe_allow_html=True)


# ─── SESSION STATE ─────────────────────────────────────────────────────────────

for key, val in [
    ("ep_raw", []), ("canslim_raw", []),
    ("ep_analysis", []), ("canslim_analysis", []),
    ("ep_fundamentals", {}), ("scan_done", False),
]:
    if key not in st.session_state:
        st.session_state[key] = val


# ─── WATCHLIST ────────────────────────────────────────────────────────────────

def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            with open(WATCHLIST_FILE) as f:
                return json.load(f)
        except:
            pass
    return []

def save_watchlist(wl):
    with open(WATCHLIST_FILE, "w") as f:
        json.dump(wl, f, indent=2)

def add_to_watchlist(entry):
    wl = load_watchlist()
    wl = [w for w in wl if w.get("ticker") != entry.get("ticker")]
    entry["added"] = date.today().strftime("%Y-%m-%d")
    wl.insert(0, entry)
    save_watchlist(wl)

def remove_from_watchlist(ticker):
    wl = [w for w in load_watchlist() if w.get("ticker") != ticker]
    save_watchlist(wl)


# ─── HELPERS ──────────────────────────────────────────────────────────────────

# Feriados de mercado NYSE conhecidos (adicionar anualmente)
_NYSE_HOLIDAYS = {
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18",
    "2025-05-26", "2025-06-19", "2025-07-04", "2025-09-01",
    "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
}

def last_trading_day(offset=1):
    """
    Return the Nth previous trading day (Mon-Fri, excluindo feriados NYSE).
    offset=1 -> último dia de trading
    offset=2 -> dia de trading anterior
    """
    d = date.today()
    count = 0
    while count < offset:
        d -= timedelta(days=1)
        if d.weekday() < 5 and d.strftime("%Y-%m-%d") not in _NYSE_HOLIDAYS:
            count += 1
    return d.strftime("%Y-%m-%d")

def is_clean_ticker(ticker):
    if len(ticker) > 5: return False
    if any(c in ticker for c in ['.', '-', '+', '/', ' ']): return False
    if ticker.endswith(('W', 'R', 'U', 'P')) and len(ticker) > 4: return False
    return True

def fmt_large(n):
    if n is None: return "—"
    if n >= 1e9: return f"${n/1e9:.1f}B"
    if n >= 1e6: return f"${n/1e6:.0f}M"
    return f"${n:,.0f}"

def fmt_pct(n):
    if n is None: return "—"
    return f"{n*100:.0f}%"

def fmt_shares(n):
    if n is None: return "—"
    if n >= 1e6: return f"{n/1e6:.1f}M"
    return f"{n:,.0f}"


# ─── POLYGON ──────────────────────────────────────────────────────────────────

# Polygon free tier: 5 chamadas/minuto
POLYGON_CALL_INTERVAL = 13

def polygon_grouped(date_str):
    """Fetch grouped daily data com retry automático em rate limit."""
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
    for attempt in range(4):
        r = requests.get(url, params={"adjusted": "true", "apiKey": POLYGON_KEY}, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if not data.get("results"):
                return {}
            return {item["T"]: item for item in data["results"]}
        elif r.status_code == 429:
            wait = POLYGON_CALL_INTERVAL * (attempt + 1)
            if attempt < 3:
                st.toast(f"⏳ Polygon rate limit — a aguardar {wait}s...", icon="⏳")
                time.sleep(wait)
            else:
                st.error("Polygon rate limit persistente. Aguarda 1 minuto e tenta novamente.")
                return {}
        else:
            st.error(f"Polygon error {r.status_code}: {r.text[:200]}")
            return {}
    return {}

def find_trading_day_with_data(offset=1, max_lookback=7) -> tuple:
    """
    Encontra os dois últimos dias com dados reais no Polygon.
    Salta automaticamente feriados e dias sem dados (ex: Good Friday).
    Retorna (session_date, prev_date).
    """
    found = []
    d = date.today()
    attempts = 0
    while len(found) < 2 and attempts < max_lookback + 5:
        d -= timedelta(days=1)
        attempts += 1
        if d.weekday() >= 5:  # fim de semana
            continue
        if d.strftime("%Y-%m-%d") in _NYSE_HOLIDAYS:
            continue
        found.append(d.strftime("%Y-%m-%d"))
    if len(found) >= 2:
        return found[0], found[1]
    return found[0] if found else None, None

def polygon_history(ticker, days=75):
    """Fetch last N days OHLCV for neglect detection."""
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    url   = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
    r = requests.get(url, params={"adjusted": "true", "limit": 100, "apiKey": POLYGON_KEY}, timeout=15)
    if r.status_code != 200 or not r.json().get("results"):
        return []
    return r.json()["results"]


# ─── YFINANCE FUNDAMENTALS ────────────────────────────────────────────────────

def fetch_fundamentals(ticker: str) -> dict:
    """
    Fetch EP-relevant fundamentals from yfinance.
    Returns a dict with all MAGNA 53 + CAP 10×10 data points.
    Never raises — returns {} on any error.
    """
    try:
        yf_ticker = yf.Ticker(ticker)
        info = yf_ticker.info or {}

        # ── Earnings growth (QoQ) ──────────────────────────────────────────
        # earningsQuarterlyGrowth = most recent quarter YoY change
        earnings_growth = info.get("earningsQuarterlyGrowth")  # e.g. 1.23 = +123%

        # ── Revenue / Sales growth ─────────────────────────────────────────
        revenue_growth = info.get("revenueGrowth")  # TTM YoY

        # ── Float ──────────────────────────────────────────────────────────
        float_shares = info.get("floatShares")       # absolute number
        shares_out   = info.get("sharesOutstanding")

        # ── Market cap ────────────────────────────────────────────────────
        market_cap = info.get("marketCap")

        # ── Short interest ────────────────────────────────────────────────
        short_ratio   = info.get("shortRatio")       # days to cover
        short_pct_float = info.get("shortPercentOfFloat")  # e.g. 0.15 = 15%

        # ── Institutional ownership ───────────────────────────────────────
        inst_pct    = info.get("heldPercentInstitutions")
        # Approximate fund count: if institutions hold X%, how many funds?
        # yfinance doesn't give fund count directly; use institutionsCount if present
        inst_count  = info.get("institutionCount") or info.get("institutionsCount")

        # ── IPO / listing date ────────────────────────────────────────────
        ipo_year = None
        try:
            hist = yf_ticker.history(period="max", auto_adjust=False)
            if not hist.empty:
                ipo_year = hist.index[0].year
        except:
            pass

        # ── Analyst coverage ──────────────────────────────────────────────
        analyst_count    = info.get("numberOfAnalystOpinions")
        recommendation   = info.get("recommendationKey")  # "buy","hold","sell"

        # ── Sector & industry ─────────────────────────────────────────────
        sector   = info.get("sector", "")
        industry = info.get("industry", "")

        # ── Quick EPS validation ──────────────────────────────────────────
        eps_trailing = info.get("trailingEps")
        eps_forward  = info.get("forwardEps")

        # ── RS proxy: variação preço 12 meses (para ONEIL L score) ─────────
        price_change_12m = None
        try:
            hist_12m = yf_ticker.history(period="1y")
            if len(hist_12m) >= 2:
                p_now  = float(hist_12m["Close"].iloc[-1])
                p_year = float(hist_12m["Close"].iloc[0])
                price_change_12m = round((p_now - p_year) / p_year * 100, 1)
        except:
            pass

        return {
            "earnings_growth":     earnings_growth,
            "revenue_growth":      revenue_growth,
            "return_on_equity":    info.get("returnOnEquity"),
            "float_shares":        float_shares,
            "shares_outstanding":  shares_out,
            "market_cap":          market_cap,
            "short_ratio":         short_ratio,
            "short_pct_float":     short_pct_float,
            "inst_pct":            inst_pct,
            "inst_count":          inst_count,
            "ipo_year":            ipo_year,
            "analyst_count":       analyst_count,
            "recommendation":      recommendation,
            "sector":              sector,
            "industry":            industry,
            "eps_trailing":        eps_trailing,
            "eps_forward":         eps_forward,
            "high_52w":            info.get("fiftyTwoWeekHigh"),
            "low_52w":             info.get("fiftyTwoWeekLow"),
            "price_change_12m":    price_change_12m,
        }
    except Exception as e:
        return {"_error": str(e)}


def fetch_fundamentals_batch(tickers: list, progress_cb=None) -> dict:
    """Fetch fundamentals for a list of tickers with rate-limit spacing."""
    results = {}
    for i, ticker in enumerate(tickers):
        results[ticker] = fetch_fundamentals(ticker)
        if progress_cb:
            progress_cb(i + 1, len(tickers), ticker)
        time.sleep(0.3)  # gentle rate limiting
    return results


# ─── NEGLECT DETECTION ───────────────────────────────────────────────────────

def detect_neglect(ticker: str, history_bars: list) -> dict:
    """
    Analyse price history (last 65 days) to detect neglect patterns.
    Returns dict with neglect score and details.
    """
    if not history_bars or len(history_bars) < 10:
        return {"score": 50, "label": "Sem dados", "detail": "Histórico insuficiente"}

    closes = [b["c"] for b in history_bars]
    volumes = [b["v"] for b in history_bars]

    # Price range over 65 days (excluding last bar = EP day)
    pre_ep = closes[:-1]
    if not pre_ep:
        return {"score": 50, "label": "Sem dados", "detail": "Apenas 1 barra"}

    max_price  = max(pre_ep)
    min_price  = min(pre_ep)
    start_price = pre_ep[0]
    end_price   = pre_ep[-1]

    # How much did stock rally before EP? (lower = more neglect)
    pre_rally_pct = (end_price - start_price) / start_price * 100

    # Sideways detection: high/low range < 20% = sideways
    range_pct = (max_price - min_price) / min_price * 100

    # Average volume (excluding last bar)
    avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1)

    # Classify neglect
    neglect_score = 0
    detail_parts  = []

    # Pre-rally below 10% = strong neglect
    if pre_rally_pct <= -10:
        neglect_score += 40
        detail_parts.append(f"Em queda ({pre_rally_pct:+.0f}%)")
    elif pre_rally_pct <= 5:
        neglect_score += 30
        detail_parts.append(f"Lateral ({pre_rally_pct:+.0f}%)")
    elif pre_rally_pct <= 20:
        neglect_score += 15
        detail_parts.append(f"Subida leve ({pre_rally_pct:+.0f}%)")
    else:
        neglect_score += 0
        detail_parts.append(f"Já subiu {pre_rally_pct:+.0f}% ⚠️")

    # Tight range = indifference
    if range_pct < 15:
        neglect_score += 30
        detail_parts.append("Intervalo muito estreito")
    elif range_pct < 25:
        neglect_score += 20
        detail_parts.append("Intervalo estreito")
    elif range_pct < 40:
        neglect_score += 10
    else:
        detail_parts.append("Volátil antes do EP")

    # Low volume = neglected
    ep_vol = volumes[-1] if volumes else 0
    if avg_vol < 200_000:
        neglect_score += 30
        detail_parts.append("Volume muito baixo antes")
    elif avg_vol < 500_000:
        neglect_score += 20
        detail_parts.append("Volume baixo antes")
    elif avg_vol < 1_000_000:
        neglect_score += 10

    neglect_score = min(neglect_score, 100)

    if neglect_score >= 70:
        label = "Alta negligência"
    elif neglect_score >= 40:
        label = "Negligência moderada"
    else:
        label = "Sem negligência clara"

    return {
        "score":         neglect_score,
        "label":         label,
        "pre_rally_pct": round(pre_rally_pct, 1),
        "range_pct":     round(range_pct, 1),
        "avg_vol_pre":   int(avg_vol),
        "detail":        " · ".join(detail_parts),
    }


# ─── MAGNA 53 SCORING ─────────────────────────────────────────────────────────

def magna53_score(raw: dict, fund: dict, neglect: dict, kb_adj: dict = None) -> dict:
    """
    Score EP candidate using MAGNA 53 + CAP 10×10 framework.
    Returns dict with total score, breakdown per criterion, and EP type.
    If kb_adj is provided, applies KB-learned multipliers to the final score.
    """
    breakdown = {}
    total = 0

    gap_pct   = raw.get("gap_pct", 0)
    vol_ratio = raw.get("vol_ratio", 1)
    price     = raw.get("price", 0)
    volume    = raw.get("volume", 0)

    # ── MA — Massive Acceleration ────────────────────────────────────────────
    # Earnings growth OR revenue growth — either one being massive counts
    eg = fund.get("earnings_growth")  # decimal, e.g. 1.0 = +100%
    rg = fund.get("revenue_growth")   # decimal, e.g. 0.39 = +39%

    ma_score = 0
    ma_detail = []

    if eg is not None:
        eg_pct = eg * 100
        if eg_pct >= 200:
            ma_score += 20; ma_detail.append(f"Earnings +{eg_pct:.0f}% ✅")
        elif eg_pct >= 100:
            ma_score += 15; ma_detail.append(f"Earnings +{eg_pct:.0f}% ✅")
        elif eg_pct >= 40:
            ma_score += 8;  ma_detail.append(f"Earnings +{eg_pct:.0f}%")
        else:
            ma_detail.append(f"Earnings +{eg_pct:.0f}% ⚠️")
    else:
        ma_detail.append("Earnings: sem dados")

    if rg is not None:
        rg_pct = rg * 100
        if rg_pct >= 100:
            ma_score += 15; ma_detail.append(f"Revenue +{rg_pct:.0f}% ✅")
        elif rg_pct >= 39:
            ma_score += 10; ma_detail.append(f"Revenue +{rg_pct:.0f}% ✅")
        elif rg_pct >= 10:
            ma_score += 4;  ma_detail.append(f"Revenue +{rg_pct:.0f}%")
        else:
            ma_detail.append(f"Revenue +{rg_pct:.0f}% ⚠️")
    else:
        ma_detail.append("Revenue: sem dados")

    breakdown["MA"] = {"score": ma_score, "max": 35, "detail": " | ".join(ma_detail)}
    total += ma_score

    # ── G — Gap Up ───────────────────────────────────────────────────────────
    # Market is reacting to surprise — must have significant gap
    g_score = 0
    if gap_pct >= 30:    g_score = 20
    elif gap_pct >= 20:  g_score = 17
    elif gap_pct >= 10:  g_score = 13
    elif gap_pct >= 8:   g_score = 10
    elif gap_pct >= 5:   g_score = 6
    else:                g_score = 2

    breakdown["G"] = {
        "score": g_score, "max": 20,
        "detail": f"Gap +{gap_pct:.1f}% {'✅' if gap_pct >= 8 else '⚠️'}"
    }
    total += g_score

    # ── N — Neglect ───────────────────────────────────────────────────────────
    n_score = int(neglect.get("score", 0) * 0.20)  # max 20 pts
    n_score = min(n_score, 20)
    breakdown["N"] = {
        "score": n_score, "max": 20,
        "detail": f"{neglect.get('label','—')} | {neglect.get('detail','')}"
    }
    total += n_score

    # ── A — Acceleration in Sales ────────────────────────────────────────────
    # Already partially captured in MA; here we specifically check revenue trend
    a_score = 0
    if rg is not None:
        rg_pct = rg * 100
        if rg_pct >= 39:   a_score = 10
        elif rg_pct >= 15: a_score = 6
        elif rg_pct >= 5:  a_score = 3
        a_detail = f"Sales growth {rg_pct:.0f}% {'✅' if rg_pct >= 39 else '—'}"
    else:
        a_score = 0
        a_detail = "Sales: sem dados"

    breakdown["A"] = {"score": a_score, "max": 10, "detail": a_detail}
    total += a_score

    # ── 5 — Short Interest ≥ 5 days ──────────────────────────────────────────
    short_ratio = fund.get("short_ratio")
    if short_ratio and short_ratio >= 5:
        five_score = 5
        five_detail = f"Short ratio {short_ratio:.1f}d ✅ (combustível)"
    elif short_ratio and short_ratio >= 2:
        five_score = 3
        five_detail = f"Short ratio {short_ratio:.1f}d"
    else:
        five_score = 0
        five_detail = f"Short ratio {short_ratio:.1f}d" if short_ratio else "Short: sem dados"

    breakdown["5"] = {"score": five_score, "max": 5, "detail": five_detail}
    total += five_score

    # ── 3 — 3+ Analysts raising price targets ────────────────────────────────
    # yfinance doesn't give analyst upgrades directly; use analyst count as proxy
    analyst_count  = fund.get("analyst_count") or 0
    recommendation = fund.get("recommendation", "")

    if analyst_count >= 3 and recommendation in ("buy", "strong_buy"):
        three_score = 5
        three_detail = f"{analyst_count} analistas · {recommendation} ✅"
    elif analyst_count >= 1:
        three_score = 2
        three_detail = f"{analyst_count} analistas · {recommendation}"
    else:
        three_score = 0
        three_detail = "Sem cobertura de analistas (neglect premium)"

    breakdown["3"] = {"score": three_score, "max": 5, "detail": three_detail}
    total += three_score

    # ── CAP — Capitalisation < $10B ──────────────────────────────────────────
    mkt_cap = fund.get("market_cap")
    if mkt_cap:
        if mkt_cap <= 500_000_000:       cap_score = 5; cap_detail = f"{fmt_large(mkt_cap)} ✅ micro"
        elif mkt_cap <= 2_000_000_000:   cap_score = 5; cap_detail = f"{fmt_large(mkt_cap)} ✅ small"
        elif mkt_cap <= 10_000_000_000:  cap_score = 4; cap_detail = f"{fmt_large(mkt_cap)} ✅ mid"
        else:                             cap_score = 1; cap_detail = f"{fmt_large(mkt_cap)} ⚠️ >$10B"
    else:
        cap_score = 2; cap_detail = "Cap: sem dados"

    breakdown["CAP"] = {"score": cap_score, "max": 5, "detail": cap_detail}
    total += cap_score

    # ── 10 — IPO < 10 anos ────────────────────────────────────────────────────
    ipo_year = fund.get("ipo_year")
    current_year = date.today().year
    if ipo_year:
        years_public = current_year - ipo_year
        if years_public <= 5:
            ten_score = 5; ten_detail = f"IPO {ipo_year} ({years_public}a) ✅"
        elif years_public <= 10:
            ten_score = 4; ten_detail = f"IPO {ipo_year} ({years_public}a) ✅"
        elif years_public <= 15:
            ten_score = 2; ten_detail = f"IPO {ipo_year} ({years_public}a)"
        else:
            ten_score = 0; ten_detail = f"IPO {ipo_year} ({years_public}a) ⚠️ old"
    else:
        ten_score = 2; ten_detail = "IPO: sem dados"

    breakdown["10"] = {"score": ten_score, "max": 5, "detail": ten_detail}
    total += ten_score

    # ── Float bonus ───────────────────────────────────────────────────────────
    float_shares = fund.get("float_shares")
    if float_shares:
        if float_shares < 10_000_000:
            float_bonus = 5
            float_detail = f"Float {fmt_shares(float_shares)} ✅ explosivo"
        elif float_shares < 25_000_000:
            float_bonus = 4
            float_detail = f"Float {fmt_shares(float_shares)} ✅ ideal"
        elif float_shares < 100_000_000:
            float_bonus = 2
            float_detail = f"Float {fmt_shares(float_shares)}"
        else:
            float_bonus = 0
            float_detail = f"Float {fmt_shares(float_shares)} ⚠️ alto"
    else:
        float_bonus = 0
        float_detail = "Float: sem dados"

    breakdown["FLOAT"] = {"score": float_bonus, "max": 5, "detail": float_detail}
    total += float_bonus

    # ── Determine EP type ─────────────────────────────────────────────────────
    neg_score    = neglect.get("score", 0)
    pre_rally    = neglect.get("pre_rally_pct", 0)

    if eg is not None and eg >= 1.0 and pre_rally <= -5:
        ep_type = "TURNAROUND"
    elif eg is not None and eg >= 1.0 and rg is not None and rg >= 0.39:
        ep_type = "GROWTH"
    elif neg_score >= 60 and gap_pct >= 15:
        ep_type = "STORY/NEGLECTED"
    elif vol_ratio >= 9:
        ep_type = "9M_EP"
    else:
        ep_type = "STANDARD"

    # ── Apply KB adjustments ──────────────────────────────────────────────────
    kb_applied      = False
    kb_multiplier   = 1.0
    kb_bonus        = 0
    kb_notes        = []

    if kb_adj and kb_adj.get("data_available"):
        # Gap multiplier
        gap_mults = kb_adj.get("gap_multipliers", {})
        if gap_pct < 8:
            mult = gap_mults.get("5_8", 1.0)
        elif gap_pct < 15:
            mult = gap_mults.get("8_15", 1.0)
        elif gap_pct < 25:
            mult = gap_mults.get("15_25", 1.0)
        else:
            mult = gap_mults.get("25_plus", 1.0)
        if mult != 1.0:
            kb_multiplier *= mult
            kb_notes.append(f"gap ×{mult}")
            kb_applied = True

        # Vol multiplier
        vol_mults = kb_adj.get("vol_multipliers", {})
        if vol_ratio < 3:
            mult = vol_mults.get("1_3", 1.0)
        elif vol_ratio < 5:
            mult = vol_mults.get("3_5", 1.0)
        elif vol_ratio < 10:
            mult = vol_mults.get("5_10", 1.0)
        else:
            mult = vol_mults.get("10_plus", 1.0)
        if mult != 1.0:
            kb_multiplier *= mult
            kb_notes.append(f"vol ×{mult}")
            kb_applied = True

        # KB confidence bonus: the more trades in KB, the more we trust it
        kb_trades = kb_adj.get("total_trades_in_kb", 0)
        if kb_trades >= 50:
            kb_bonus = 3
            kb_notes.append(f"+{kb_bonus} (KB:{kb_trades}t)")
            kb_applied = True

    # Apply multiplier to raw total (before capping)
    raw_total   = total
    final_total = min(int(raw_total * kb_multiplier) + kb_bonus, 100)

    return {
        "total":          final_total,
        "raw_total":      min(int(raw_total), 100),
        "kb_multiplier":  round(kb_multiplier, 3),
        "kb_bonus":       kb_bonus,
        "kb_applied":     kb_applied,
        "kb_notes":       " · ".join(kb_notes) if kb_notes else None,
        "breakdown":      breakdown,
        "ep_type":        ep_type,
        "float_shares":   float_shares,
        "neglect":        neglect,
    }


# ─── SCANNERS ─────────────────────────────────────────────────────────────────

def scan_ep(today, prev, min_gap=5.0, min_vol=300_000, min_price=8.0):
    results = []
    for ticker, t in today.items():
        if not is_clean_ticker(ticker): continue
        if ticker not in prev: continue
        p = prev[ticker]
        price    = t.get("c", 0)
        volume   = t.get("v", 0)
        t_open   = t.get("o", 0)
        p_close  = p.get("c", 0)
        if price < min_price or volume < min_vol or p_close == 0: continue
        if price * volume < 5_000_000: continue
        gap_pct   = (t_open - p_close) / p_close * 100
        vol_ratio = volume / max(p.get("v", 1), 1)
        if gap_pct >= min_gap and vol_ratio >= 1.5:
            results.append({
                "ticker":     ticker,
                "price":      round(price, 2),
                "gap_pct":    round(gap_pct, 2),
                "vol_ratio":  round(vol_ratio, 2),
                "volume":     int(volume),
                "prev_close": round(p_close, 2),
            })
    results.sort(key=lambda x: (x["gap_pct"] * 0.5 + x["vol_ratio"] * 0.5), reverse=True)
    return results[:15]


def scan_canslim(today, prev, min_change=1.5, min_price=15.0, min_vol=300_000):
    results = []
    for ticker, t in today.items():
        if not is_clean_ticker(ticker): continue
        if ticker not in prev: continue
        p = prev[ticker]
        price   = t.get("c", 0)
        volume  = t.get("v", 0)
        p_close = p.get("c", 0)
        if price < min_price or volume < min_vol or p_close == 0: continue
        change_pct = (price - p_close) / p_close * 100
        vol_ratio  = volume / max(p.get("v", 1), 1)
        if change_pct >= min_change and vol_ratio >= 1.3:
            score = round((change_pct * 0.4 + vol_ratio * 2.0) * price / 50, 2)
            results.append({
                "ticker":     ticker,
                "price":      round(price, 2),
                "change_pct": round(change_pct, 2),
                "vol_ratio":  round(vol_ratio, 2),
                "volume":     int(volume),
                "score":      score,
                "high_52w":   t.get("h", 0),   # high do dia (proxy; será actualizado com yfinance)
                "low_52w":    t.get("l", 0),
            })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:12]


# ─── ONEIL 70 SCORE ───────────────────────────────────────────────────────────

def oneil70_score(raw: dict, fund: dict) -> dict:
    """
    ONEIL 70 Score — William O'Neil / CANSLIM methodology.
    Scoring baseado em critérios mensuráveis com dados yfinance + Polygon.

    C=20 · A=15 · N=5 · S=10 · L=10 · I=5 · M=5 = 70 pts total
    Breakout setup:  >= 55 pts (rutura 52w high com volume)
    Follow-on setup: >= 50 pts (pullback 5-15% com volume baixo)
    """
    breakdown = {}
    total = 0

    price      = raw.get("price", 0)
    change_pct = raw.get("change_pct", 0)
    vol_ratio  = raw.get("vol_ratio", 1)
    high_52w   = raw.get("high_52w", 0)
    low_52w    = raw.get("low_52w", 0)

    eg  = fund.get("earnings_growth")   # earningsQuarterlyGrowth (decimal)
    rg  = fund.get("revenue_growth")    # revenueGrowth (decimal)
    roe = fund.get("return_on_equity")  # returnOnEquity (decimal)
    fl  = fund.get("float_shares") or 0
    mc  = fund.get("market_cap") or 0
    inst_pct  = fund.get("inst_pct") or 0
    anal_count = fund.get("analyst_count") or 0
    rec       = fund.get("recommendation", "")
    perf_12m  = fund.get("price_change_12m")  # variação 12m vs SPY

    # ── C: Current Quarterly Earnings (20 pts) ────────────────────────────────
    c_score = 0
    c_notes = []
    if eg is not None:
        ep = eg * 100
        if ep >= 100:
            c_score = 20; c_notes.append(f"EPS QoQ +{ep:.0f}% ✅")
        elif ep >= 40:
            c_score = 15; c_notes.append(f"EPS QoQ +{ep:.0f}%")
        elif ep >= 25:
            c_score = 10; c_notes.append(f"EPS QoQ +{ep:.0f}%")
        elif ep >= 0:
            c_score = 4;  c_notes.append(f"EPS QoQ +{ep:.0f}% (fraco)")
        else:
            c_score = 0;  c_notes.append(f"EPS negativo ❌")
    else:
        c_score = 0; c_notes.append("Sem dados EPS")
    breakdown["C"] = {"score": c_score, "max": 20, "notes": " | ".join(c_notes)}
    total += c_score

    # ── A: Annual Earnings & Revenue (15 pts) ─────────────────────────────────
    a_score = 0
    a_notes = []
    if rg is not None:
        rp = rg * 100
        if rp >= 100:
            a_score += 12; a_notes.append(f"Revenue +{rp:.0f}% ✅")
        elif rp >= 40:
            a_score += 9;  a_notes.append(f"Revenue +{rp:.0f}%")
        elif rp >= 25:
            a_score += 6;  a_notes.append(f"Revenue +{rp:.0f}%")
        elif rp >= 10:
            a_score += 3;  a_notes.append(f"Revenue +{rp:.0f}%")
        else:
            a_notes.append(f"Revenue {rp:.0f}%")
    if roe is not None and roe >= 0.17:
        a_score += 3; a_notes.append(f"ROE {roe*100:.0f}% ✅")
    elif roe is not None:
        a_notes.append(f"ROE {roe*100:.0f}%")
    a_score = min(a_score, 15)
    if not a_notes: a_notes.append("Sem dados revenue")
    breakdown["A"] = {"score": a_score, "max": 15, "notes": " | ".join(a_notes)}
    total += a_score

    # ── N: New — catalisador + proximidade 52w high (5 pts) ──────────────────
    n_score = 0
    n_notes = []
    if high_52w > 0 and price > 0:
        pct_from_high = (price / high_52w) * 100
        if pct_from_high >= 95:
            n_score += 3; n_notes.append(f"Perto máx 52s ({pct_from_high:.0f}%) ✅")
        elif pct_from_high >= 85:
            n_score += 1; n_notes.append(f"{pct_from_high:.0f}% do máx 52s")
        else:
            n_notes.append(f"{pct_from_high:.0f}% do máx 52s ❌")
    if change_pct >= 5:
        n_score += 2; n_notes.append(f"Gap/rutura +{change_pct:.1f}%")
    elif change_pct >= 2:
        n_score += 1
    n_score = min(n_score, 5)
    if not n_notes: n_notes.append("Sem dados 52s")
    breakdown["N"] = {"score": n_score, "max": 5, "notes": " | ".join(n_notes)}
    total += n_score

    # ── S: Supply & Demand — volume + float (10 pts) ─────────────────────────
    s_score = 0
    s_notes = []
    # Volume no breakout (O'Neil: ≥40% acima da média)
    if vol_ratio >= 3.0:
        s_score += 7; s_notes.append(f"Vol {vol_ratio:.1f}x ✅")
    elif vol_ratio >= 1.4:
        s_score += 5; s_notes.append(f"Vol {vol_ratio:.1f}x")
    elif vol_ratio >= 1.0:
        s_score += 2; s_notes.append(f"Vol {vol_ratio:.1f}x (fraco)")
    else:
        s_notes.append(f"Vol {vol_ratio:.1f}x ❌")
    # Float baixo amplifica o movimento
    float_m = fl / 1e6 if fl else 0
    if 0 < float_m < 10:
        s_score += 3; s_notes.append(f"Float {float_m:.0f}M ✅")
    elif float_m < 25:
        s_score += 2; s_notes.append(f"Float {float_m:.0f}M")
    elif float_m < 100:
        s_score += 1; s_notes.append(f"Float {float_m:.0f}M")
    else:
        s_notes.append(f"Float {float_m:.0f}M (alto)")
    s_score = min(s_score, 10)
    breakdown["S"] = {"score": s_score, "max": 10, "notes": " | ".join(s_notes)}
    total += s_score

    # ── L: Leader — relative strength (10 pts) ────────────────────────────────
    l_score = 0
    l_notes = []
    if perf_12m is not None:
        # Comparar performance relativa vs SPY (~20% como referência de mercado forte)
        if perf_12m >= 50:
            l_score = 10; l_notes.append(f"RS +{perf_12m:.0f}% (top 10%) ✅")
        elif perf_12m >= 25:
            l_score = 7;  l_notes.append(f"RS +{perf_12m:.0f}% (top 20%)")
        elif perf_12m >= 10:
            l_score = 4;  l_notes.append(f"RS +{perf_12m:.0f}%")
        elif perf_12m >= 0:
            l_score = 2;  l_notes.append(f"RS +{perf_12m:.0f}% (neutro)")
        else:
            l_score = 0;  l_notes.append(f"RS {perf_12m:.0f}% ❌")
    else:
        l_score = 3; l_notes.append("RS não disponível (score base)")
    breakdown["L"] = {"score": l_score, "max": 10, "notes": " | ".join(l_notes)}
    total += l_score

    # ── I: Institutional Sponsorship (5 pts) ──────────────────────────────────
    i_score = 0
    i_notes = []
    if inst_pct >= 0.5:
        i_score += 2; i_notes.append(f"Inst {inst_pct*100:.0f}%")
    elif inst_pct >= 0.2:
        i_score += 1; i_notes.append(f"Inst {inst_pct*100:.0f}%")
    if anal_count >= 5 and rec in ("buy", "strong_buy"):
        i_score += 3; i_notes.append(f"{anal_count} analistas buy ✅")
    elif anal_count >= 3:
        i_score += 2; i_notes.append(f"{anal_count} analistas")
    elif anal_count >= 1:
        i_score += 1; i_notes.append(f"{anal_count} analista(s)")
    else:
        i_notes.append("Sem cobertura")
    i_score = min(i_score, 5)
    breakdown["I"] = {"score": i_score, "max": 5, "notes": " | ".join(i_notes)}
    total += i_score

    # ── M: Market Direction (5 pts) ─────────────────────────────────────────────
    # Macro context removido — score neutro fixo (avalias tu manualmente)
    m_score = 2
    m_notes = ["Avaliar manualmente — ver condições de mercado"]
    breakdown["M"] = {"score": m_score, "max": 5, "notes": " | ".join(m_notes)}
    total += m_score

    total = min(int(total), 70)

    # ── Setup type ────────────────────────────────────────────────────────────
    # Breakout: perto de máx 52s + volume forte
    # Follow-on: pullback 5-15% de máx recente + volume fraco no pullback
    pct_from_high = (price / high_52w * 100) if high_52w > 0 else 0
    if total >= 55 and pct_from_high >= 90 and vol_ratio >= 1.4:
        setup_type = "BREAKOUT"
    elif total >= 50 and pct_from_high >= 75 and pct_from_high < 95 and vol_ratio < 1.5:
        setup_type = "FOLLOW_ON"
    elif total >= 50:
        setup_type = "BREAKOUT"
    else:
        setup_type = "WEAK"

    # Grade
    if total >= 60:   grade = "A"
    elif total >= 50: grade = "B"
    elif total >= 40: grade = "C"
    else:             grade = "D"

    return {
        "total":      total,
        "grade":      grade,
        "setup_type": setup_type,
        "breakdown":  breakdown,
        "pct_from_high": round(pct_from_high, 1),
    }


def render_oneil_breakdown(oneil: dict):
    """Render ONEIL 70 breakdown in Streamlit."""
    breakdown = oneil.get("breakdown", {})
    if not breakdown:
        return
    st.markdown(
        '''<div style="background:#0d1422;border:1px solid #1e2d45;
        border-radius:8px;padding:12px 16px;margin:8px 0">
        <div style="color:#667a99;font-size:0.72em;text-transform:uppercase;
        letter-spacing:1px;margin-bottom:8px;font-family:'Space Mono',monospace">
        ONEIL 70 breakdown</div>''',
        unsafe_allow_html=True
    )
    labels = {
        "C": "Current Earnings",
        "A": "Annual Revenue",
        "N": "New / 52w High",
        "S": "Supply & Demand",
        "L": "Leader (RS)",
        "I": "Institutional",
        "M": "Market",
    }
    for letter, data in breakdown.items():
        score = data.get("score", 0)
        max_s = data.get("max", 10)
        notes = data.get("notes", "")
        pct   = int(score / max_s * 100) if max_s else 0
        color = "#00e87a" if pct >= 70 else "#f5c842" if pct >= 40 else "#ff5e5e"
        st.markdown(
            f'''<div class="magna-row">
            <div class="magna-letter" style="color:{color}">{letter}</div>
            <div class="magna-label">{labels.get(letter, letter)}</div>
            <div class="magna-value">{notes}</div>
            <div class="magna-pass" style="color:{color}">{score}/{max_s}</div>
            </div>''',
            unsafe_allow_html=True
        )
    st.markdown("</div>", unsafe_allow_html=True)


# ─── CLAUDE ANALYSIS ──────────────────────────────────────────────────────────

def parse_json_response(text):
    cleaned = text.replace("```json", "").replace("```", "").strip()
    match = re.search(r'\[[\s\S]*\]', cleaned)
    if match:
        try: return json.loads(match.group(0))
        except:
            try: return json.loads(match.group(0).rsplit(',', 1)[0] + "]")
            except: pass
    return []


def build_ep_context(raw: dict, fund: dict, magna: dict) -> dict:
    """Build a compact context dict for Claude with all EP-relevant data."""
    return {
        "ticker":         raw["ticker"],
        "price":          raw["price"],
        "gap_pct":        raw["gap_pct"],
        "vol_ratio":      raw["vol_ratio"],
        "volume":         raw["volume"],
        "magna_score":    magna["total"],
        "ep_type":        magna["ep_type"],
        "earnings_growth_pct": round((fund.get("earnings_growth") or 0) * 100, 0),
        "revenue_growth_pct":  round((fund.get("revenue_growth") or 0) * 100, 0),
        "float_shares_M":      round((fund.get("float_shares") or 0) / 1e6, 1),
        "market_cap":          fmt_large(fund.get("market_cap")),
        "short_ratio_days":    fund.get("short_ratio"),
        "inst_pct":            fmt_pct(fund.get("inst_pct")),
        "ipo_year":            fund.get("ipo_year"),
        "sector":              fund.get("sector"),
        "industry":            fund.get("industry"),
        "analyst_count":       fund.get("analyst_count"),
        "neglect_score":       magna["neglect"].get("score"),
        "neglect_label":       magna["neglect"].get("label"),
        "pre_rally_65d_pct":   magna["neglect"].get("pre_rally_pct"),
    }


def claude_analyze_ep(candidates: list, fundamentals: dict, magna_scores: dict):
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    contexts = []
    for c in candidates:
        t = c["ticker"]
        fund  = fundamentals.get(t, {})
        magna = magna_scores.get(t, {"total": 0, "ep_type": "STANDARD", "neglect": {}})
        contexts.append(build_ep_context(c, fund, magna))

    tickers = [c["ticker"] for c in candidates]



    prompt = f"""You are an Episodic Pivot expert using Pradeep Bonde's MAGNA 53 + CAP 10×10 methodology.

## EP METHODOLOGY
Episodic Pivots are stocks where a SURPRISE catalyst forces the market to completely re-evaluate the stock.
The best EPs have: massive earnings/revenue acceleration, gap up on extreme volume, prior neglect (stock was ignored), low float.

## MAGNA 53 + CAP 10×10 CHECKLIST
- MA = Massive Acceleration: earnings growth 100%+ QoQ OR revenue 39%+ consecutive quarters
- G  = Gap Up: market reacting to surprise (minimum 8%)
- N  = Neglect: stock trending down or sideways 2-6 months before EP, <100 funds, no analyst coverage
- A  = Acceleration in Sales: revenue growth preferred over earnings (can't be manipulated)
- 5  = Short Interest 5+ days (fuel for short squeeze)
- 3  = 3+ analysts raising price targets (confirms catalyst significance)
- CAP = Market cap < $10B (can double/triple)
- 10  = IPO < 10 years ago (best moves happen in first decade)

## EP TYPES
- GROWTH: company with sustained 39%+ sales growth being discovered
- TURNAROUND: stock beaten down for years, management change, surprise comeback → BIGGEST MOVES
- STORY: narrative-driven (AI, biotech approval, space) → can make 300-400% but less predictable
- 9M_EP: unusual volume spike 9M+ shares → momentum play, hold 3-5 days

## CANDIDATES WITH REAL DATA
{json.dumps(contexts, indent=2)}

## YOUR TASK
For each ticker analyse:
1. Is this a genuine EP or noise? (Real catalyst vs random move)
2. Which EP type fits best?
3. Entry window: PRIME (today/tomorrow), OPEN (2-5 days), LATE (older)
4. What is the thesis in ONE sentence?
5. What are the red flags if any?

## YOUR TASK
For each ticker analyse and explain in PORTUGUESE (European):
1. Is this a genuine EP or noise?
2. Which EP type fits best and WHY?
3. Entry window: PRIME (today/tomorrow), OPEN (2-5 days), LATE (older)
4. Thesis in ONE clear sentence
5. Educational explanation: what makes this interesting or dangerous for a learning trader?
6. Key lesson: what can a trader learn from this specific setup?
7. Red flags if any

Return ONLY raw JSON array for ALL {len(tickers)} tickers: {tickers}
[{{"ticker":"XXXX","ep_score":85,"ep_type":"TURNAROUND","catalyst_type":"Earnings Surprise",
"catalyst_detail":"Q3 EPS bateu +340% YoY, primeiro trimestre lucrativo",
"entry_window":"PRIME","company_name":"Full Name","sector":"Healthcare",
"thesis":"Turnaround clássico negligenciado — 3 anos de perdas revertidos, float baixo amplifica movimento",
"neglect_assessment":"Alta — stock caiu 45% antes do EP, poucos fundos",
"risk_level":"Medium","stop_loss_pct":8,"red_flags":null,
"educational_note":"Este é um exemplo claro de TURNAROUND EP: empresa que estava a ser ignorada pelo mercado, com resultados surpreendentes que forçam uma reavaliação. O float baixo significa que poucos vendedores controlam o preço.",
"key_lesson":"Aprende a distinguir: o catalisador é uma SURPRESA genuína? O mercado estava REALMENTE a ignorar esta empresa? Se sim, o movimento pode ser muito maior do que parece."}}]

ep_score 0-100. Be critical. Always respond with educational_note and key_lesson in Portuguese."""

    for attempt in range(3):
        try:
            msg = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=3000,
                messages=[{"role": "user", "content": prompt}]
            )
            return parse_json_response(msg.content[0].text)
        except Exception as e:
            if "rate_limit" in str(e).lower() and attempt < 2:
                wait = 60 * (attempt + 1)
                st.warning(f"Rate limit — a aguardar {wait}s antes de tentar novamente...")
                time.sleep(wait)
            else:
                st.error(f"Claude EP falhou: {e}")
                return []
    return []


def claude_analyze_canslim(candidates):
    if not ANTHROPIC_KEY:
        return []
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        tickers = [c["ticker"] for c in candidates]
        prompt = f"""You are a CANSLIM expert analyst using William O'Neil's methodology.

Real market data — stocks with strong price + volume:
{json.dumps(candidates, indent=2)}

Score each ticker across all 7 CANSLIM criteria. For unknowns use conservative scores 40-60.
Return ALL {len(tickers)} tickers: {tickers}
Return ONLY raw JSON array:
[{{"ticker":"XXXX","canslim_score":78,"company_name":"Name","sector":"Sector","grade":"B+","C_earnings_growth":"Strong","A_annual_earnings":"Strong","N_new_catalyst":"Desc","S_supply_demand":"Tight float","L_leader":true,"I_institutional":"Increasing","M_market":"Uptrend","thesis":"Setup quality","risk_level":"Medium","stop_loss_pct":8}}]"""

        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}]
        )
        return parse_json_response(msg.content[0].text)
    except Exception as e:
        err = str(e)
        if "credit" in err.lower() or "billing" in err.lower():
            st.warning("⚠️ Créditos API esgotados — análise Claude desactivada. Resultados mostrados com MAGNA score apenas.")
        else:
            st.caption(f"CANSLIM Claude: {err[:100]}")
        return []


# ─── UI HELPERS ───────────────────────────────────────────────────────────────

def score_class(s):
    if isinstance(s, (int, float)):
        if s >= 75: return "score-high"
        if s >= 50: return "score-mid"
        return "score-low"
    return "score-mid"

def window_class(w):
    return {"PRIME": "prime", "OPEN": "open", "LATE": "late"}.get(w, "open")

def ep_type_tag(ep_type):
    mapping = {
        "TURNAROUND":   "tag-turnaround",
        "GROWTH":       "tag-growth",
        "STORY/NEGLECTED": "tag-story",
        "9M_EP":        "tag-ep",
        "STANDARD":     "tag-ep",
    }
    return f'<span class="tag {mapping.get(ep_type, "tag-ep")}">{ep_type}</span>'


def render_magna_breakdown(magna: dict):
    """Render the MAGNA 53 breakdown as a visual table."""
    bd = magna.get("breakdown", {})
    if not bd:
        st.caption("MAGNA breakdown não disponível")
        return

    st.markdown("**MAGNA 53 + CAP 10×10**")
    for key, data in bd.items():
        score  = data.get("score", 0)
        max_s  = data.get("max", 10)
        detail = data.get("detail", "")
        pct    = score / max_s if max_s > 0 else 0

        if pct >= 0.7:   status_class = "magna-pass"
        elif pct >= 0.3: status_class = "magna-warn"
        else:             status_class = "magna-fail"

        st.markdown(
            f'<div class="magna-row">'
            f'<span class="magna-letter">{key}</span>'
            f'<span class="{status_class}">{score}/{max_s}</span>'
            f'<span class="magna-value">{detail}</span>'
            f'</div>',
            unsafe_allow_html=True
        )



def pick_best_candidates(ep_raw: list, ep_analysis: list, magna_scores: dict,
                          ep_fundamentals: dict, macro: dict = None, top_n: int = 2) -> list:
    """
    Select and explain the best EP candidates of the day.
    Only considers ACTIONABLE candidates: not LATE, revenue >= 0, float <= 100M.
    Uses a compact Claude prompt with rate-limit retry to explain the top picks.
    """
    if not ep_raw:
        return []

    analysis_lk = {a["ticker"]: a for a in ep_analysis}

    # ── HARD FILTERS: eliminar candidatos não accionáveis ─────────────────────
    # LATE: já perdeu a janela óptima — não pode ser "melhor do dia"
    # Revenue negativo: invalida critério MA (Pradeep: vendas não podem ser manipuladas)
    # Float > 100M: "EPs com float de 100M+ tendem a ter pullbacks" — fonte primária
    WINDOW_MULTIPLIER = {"PRIME": 1.00, "OPEN": 0.85, "EXTENDED": 0.60, "LATE": 0.0}

    scored = []
    eliminated = []

    for r in ep_raw:
        t    = r["ticker"]
        a    = analysis_lk.get(t, {})
        mag  = magna_scores.get(t, {})
        fund = ep_fundamentals.get(t, {})

        ep_score  = a.get("ep_score") or mag.get("total", 0)
        gap_pct   = r.get("gap_pct", 0)
        vol_ratio = r.get("vol_ratio", 0)
        ep_type   = a.get("ep_type") or mag.get("ep_type", "STANDARD")
        window    = a.get("entry_window", "LATE")
        revenue_g = (fund.get("revenue_growth") or 0)
        float_m   = (fund.get("float_shares") or 0) / 1e6

        win_mult = WINDOW_MULTIPLIER.get(window, 0.0)

        # Hard filter 1: LATE → excluído do ranking accionável
        if win_mult == 0.0:
            eliminated.append({"ticker": t, "reason": "LATE — janela de entrada fechada"})
            continue

        # Hard filter 2: revenue negativo → critério MA inválido
        if revenue_g < 0:
            eliminated.append({"ticker": t, "reason": f"Revenue {revenue_g*100:.0f}% negativo"})
            continue

        # Hard filter 3: float excessivo
        if float_m > 100:
            eliminated.append({"ticker": t, "reason": f"Float {float_m:.0f}M > 100M"})
            continue

        # Score accionável: multiplicativo (janela penaliza o score base)
        type_bonus = {"TURNAROUND": 15, "GROWTH": 10, "STORY/NEGLECTED": 8,
                      "9M_EP": 5, "STANDARD": 0}.get(ep_type, 0)
        gap_bonus  = 10 if gap_pct >= 25 else 5 if gap_pct >= 15 else 0
        vol_bonus  = 10 if vol_ratio >= 10 else 5 if vol_ratio >= 5 else 0

        composite = (ep_score + type_bonus + gap_bonus + vol_bonus) * win_mult

        scored.append({
            "ticker":    t,
            "composite": composite,
            "ep_score":  ep_score,
            "raw":       r,
            "analysis":  a,
            "magna":     mag,
            "fund":      fund,
        })

    # Guardar eliminados na sessão para debug
    if eliminated:
        st.session_state["best_eliminated"] = eliminated

    if not scored:
        st.session_state["no_actionable_candidates"] = True
        return []

    st.session_state["no_actionable_candidates"] = False
    scored.sort(key=lambda x: x["composite"], reverse=True)
    best = scored[:top_n]

    if not ANTHROPIC_KEY:
        return best

    # ── PROMPT COMPACTO: ~300 tokens por candidato para evitar rate limit ─────
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    candidates_compact = []
    for b in best:
        t   = b["ticker"]
        r   = b["raw"]
        a   = b["analysis"]
        f   = b["fund"]
        mag = b["magna"]
        candidates_compact.append({
            "ticker":   t,
            "ep_type":  a.get("ep_type") or mag.get("ep_type", ""),
            "gap":      f'{r.get("gap_pct", 0):.1f}%',
            "vol":      f'{r.get("vol_ratio", 0):.1f}x',
            "window":   a.get("entry_window", ""),
            "catalyst": a.get("catalyst_detail") or a.get("catalyst_type", ""),
            "revenue":  f'{(f.get("revenue_growth") or 0)*100:.0f}%',
            "float_M":  f'{(f.get("float_shares") or 0)/1e6:.1f}M',
            "thesis":   a.get("thesis", ""),
            "flags":    a.get("red_flags", ""),
        })

    prompt = f"""Coach EP (Pradeep Bonde). Candidatos accionáveis de hoje:
{json.dumps(candidates_compact, ensure_ascii=False)}

Para cada um, responde em português europeu APENAS com JSON:
[{{"ticker":"X","confidence":"Alto|Médio|Baixo",
  "why_best":"2-3 frases: porquê é o melhor setup accionável hoje",
  "strengths":["força 1","força 2"],
  "concerns":["risco 1"],
  "action_plan":"entrada: X · stop: Y · vigiar: Z",
  "one_liner":"1 frase para trader iniciante"}}]"""

    # Retry com backoff exponencial para rate limit
    for attempt in range(3):
        try:
            time.sleep(8 + attempt * 10)  # 8s → 18s → 28s
            msg = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}]
            )
            explanations = parse_json_response(msg.content[0].text)
            if explanations:
                exp_lk = {e["ticker"]: e for e in explanations}
                for b in best:
                    b["explanation"] = exp_lk.get(b["ticker"], {})
            break
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            if attempt < 2:
                st.toast(f"⏳ Rate limit — a aguardar {wait}s...", icon="⏳")
                time.sleep(wait)
            else:
                st.warning("Rate limit persistente — candidatos mostrados sem explicação detalhada.")
        except Exception as e:
            st.warning(f"Explicação do melhor candidato falhou: {e}")
            break

    return best


def render_best_candidates(best: list, magna_scores: dict, ep_fundamentals: dict):
    """Render the best candidates section with detailed explanation."""
    st.markdown("## 🏆 Melhor Setup do Dia")
    st.caption("Apenas candidatos accionáveis: sem LATE, sem revenue negativo, float ≤ 100M · Score = MAGNA × multiplicador de janela")

    # ── Sem candidatos accionáveis ────────────────────────────────────────────
    if not best:
        no_actionable = st.session_state.get("no_actionable_candidates", False)
        eliminated    = st.session_state.get("best_eliminated", [])

        st.markdown(
            '''<div style="background:#0d1422;border:1px solid #ff5e5e40;border-radius:10px;
            padding:20px;margin:8px 0;text-align:center">
            <div style="font-size:1.8em;margin-bottom:8px">📭</div>
            <div style="color:#ff5e5e;font-weight:700;font-size:1.1em">Sem candidatos accionáveis hoje</div>
            <div style="color:#667a99;font-size:0.85em;margin-top:6px">
            Todos os candidatos EP detectados foram eliminados por LATE, revenue negativo ou float excessivo.
            </div></div>''',
            unsafe_allow_html=True
        )

        if eliminated:
            with st.expander(f"🔍 Ver {len(eliminated)} candidatos eliminados", expanded=False):
                for e in eliminated:
                    st.markdown(
                        f'<div style="color:#667a99;font-size:0.85em;padding:3px 0">'
                        f'<span style="color:#e8edf5;font-family:monospace">{e["ticker"]}</span>'
                        f' — {e["reason"]}</div>',
                        unsafe_allow_html=True
                    )
        return

    for i, b in enumerate(best):
        t   = b["ticker"]
        a   = b["analysis"]
        exp = b.get("explanation", {})
        r   = b["raw"]
        mag = b["magna"]

        ep_score  = b["ep_score"]
        ep_type   = a.get("ep_type") or mag.get("ep_type", "STANDARD")
        confidence= exp.get("confidence", "—")
        conf_color= {"Alto": "#00e87a", "Médio": "#f5c842", "Baixo": "#ff5e5e"}.get(confidence, "#667a99")

        rank_emoji = ["🥇", "🥈", "🥉"][i] if i < 3 else f"#{i+1}"

        st.markdown(
            f'<div style="background:linear-gradient(135deg,#0d1422,#111827);'
            f'border:1px solid #1e3a5f;border-radius:12px;padding:20px;margin:8px 0">',
            unsafe_allow_html=True
        )

        col_score, col_info = st.columns([1, 3])

        with col_score:
            st.markdown(
                f'<div style="text-align:center">'
                f'<div style="font-size:2em">{rank_emoji}</div>'
                f'<div class="{score_class(ep_score)}" style="margin:8px auto;width:70px">{ep_score}</div>'
                f'<div style="color:{conf_color};font-size:0.8em;font-weight:600">{confidence}</div>'
                f'<div style="color:#667a99;font-size:0.7em">CONFIANÇA</div>'
                f'</div>',
                unsafe_allow_html=True
            )

        with col_info:
            company = a.get("company_name","")
            gap_val = r.get("gap_pct", 0)
            vol_val = r.get("vol_ratio", 0)
            win_val = a.get("entry_window", "—")
            st.markdown(
                f"**{t}** · {company}  \n"
                f"Gap `+{gap_val:.1f}%` · Vol `{vol_val:.1f}x` · "
                f"{ep_type_tag(ep_type)} · {win_val}",
                unsafe_allow_html=True
            )

            if exp.get("one_liner"):
                st.info(f"💬 {exp['one_liner']}")

        st.markdown("</div>", unsafe_allow_html=True)

        # Detailed explanation
        if exp:
            with st.expander(f"📖 Análise detalhada — {t}", expanded=(i==0)):
                if exp.get("why_best"):
                    st.markdown("**Porquê é o melhor setup hoje:**")
                    st.markdown(exp["why_best"])

                col_s, col_c = st.columns(2)
                with col_s:
                    strengths = exp.get("strengths", [])
                    if strengths:
                        st.markdown("**✅ Pontos fortes:**")
                        for s in strengths:
                            st.markdown(f"• {s}")
                with col_c:
                    concerns = exp.get("concerns", [])
                    if concerns:
                        st.markdown("**⚠️ Preocupações:**")
                        for c in concerns:
                            st.markdown(f"• {c}")

                if exp.get("action_plan"):
                    st.markdown("")
                    st.markdown(
                        f'<div style="background:#00e87a10;border-left:3px solid #00e87a;'
                        f'padding:10px 14px;border-radius:4px;margin-top:8px">'
                        f'<span style="color:#00e87a;font-weight:600">📋 PLANO DE ACÇÃO</span><br>'
                        f'<span style="color:#c9d1e0">{exp["action_plan"]}</span></div>',
                        unsafe_allow_html=True
                    )

        if i < len(best) - 1:
            st.divider()

def render_ep_card(raw: dict, analysis: dict, magna: dict, fund: dict, wl_tickers: set):
    """Card EP com plano de trading completo + KB insights + contexto descritivo."""
    a         = analysis or {}
    ticker    = raw["ticker"]
    in_wl     = ticker in wl_tickers
    ep_type   = magna.get("ep_type", "STANDARD") if magna else a.get("ep_type", "STANDARD")
    ep_score  = a.get("ep_score") or (magna.get("total", 0) if magna else 0)
    window    = a.get("entry_window", "PRIME")
    neglect   = magna.get("neglect", {}) if magna else {}
    kb_applied = magna.get("kb_applied", False) if magna else False
    kb_badge   = " 🧠" if kb_applied else ""

    header = (
        f"**{ticker}** · {a.get('company_name', '')}  "
        f"|  Gap +{raw['gap_pct']}%  "
        f"|  Vol {raw['vol_ratio']}×  "
        f"|  MAGNA: {ep_score}/100{kb_badge}"
    )

    with st.expander(header, expanded=False):

        # ── LINHA 1: Score + Tipo + Janela + Watch ────────────────────────────
        c1, c2, c3, c4 = st.columns([1, 2, 2, 1])
        with c1:
            st.markdown(
                f"<div class='{score_class(ep_score)}'>{ep_score}</div>"
                f"<div style='color:#667a99;font-size:0.72em;font-family:Space Mono'>MAGNA</div>",
                unsafe_allow_html=True
            )
            st.markdown(ep_type_tag(ep_type), unsafe_allow_html=True)
        with c2:
            eg  = fund.get("earnings_growth") if fund else None
            rg  = fund.get("revenue_growth") if fund else None
            eg_s = f"{eg*100:+.0f}%" if eg is not None else "—"
            rg_s = f"{rg*100:+.0f}%" if rg is not None else "—"
            eg_c = "metric-good" if eg and eg >= 1.0 else ("metric-warn" if eg and eg >= 0.4 else "metric-bad" if eg and eg < 0 else "")
            rg_c = "metric-good" if rg and rg >= 0.39 else ("metric-warn" if rg and rg >= 0.1 else "")
            st.markdown(
                f"**Preço:** `${raw['price']}` · Gap `+{raw['gap_pct']}%` · Vol `{raw['vol_ratio']}×`  \n"
                f"**EPS QoQ:** <span class='{eg_c}'>{eg_s}</span> · "
                f"**Revenue:** <span class='{rg_c}'>{rg_s}</span>",
                unsafe_allow_html=True
            )
        with c3:
            fl  = fund.get("float_shares") if fund else None
            mc  = fund.get("market_cap") if fund else None
            sr  = fund.get("short_ratio") if fund else None
            fl_c = "metric-good" if fl and fl < 25e6 else ""
            sr_c = "metric-good" if sr and sr >= 5 else ""
            st.markdown(
                f"**Entrada:** <span class='{window_class(window)}'>{window}</span> · "
                f"**Neglect:** {neglect.get('label', '—')}  \n"
                f"**Float:** <span class='{fl_c}'>{fmt_shares(fl)}</span> · "
                f"**Cap:** {fmt_large(mc)} · "
                f"**Short:** <span class='{sr_c}'>{f'{sr:.1f}d' if sr else '—'}</span>",
                unsafe_allow_html=True
            )
        with c4:
            if in_wl:
                if st.button("★ Remove", key=f"rm_ep_{ticker}"):
                    remove_from_watchlist(ticker); st.rerun()
            else:
                if st.button("☆ Watch", key=f"add_ep_{ticker}"):
                    add_to_watchlist({
                        "ticker": ticker, "type": "EP", "score": ep_score,
                        "price": raw["price"], "gap_pct": raw["gap_pct"],
                        "ep_type": ep_type, "catalyst": a.get("catalyst_type", "—"),
                        "thesis": a.get("thesis", ""), "entry_window": window,
                        "neglect": neglect.get("label", ""),
                        "added": date.today().strftime("%Y-%m-%d"),
                    })
                    st.rerun()

        st.divider()

        # ── O QUE É ESTA EMPRESA + CATALISADOR ───────────────────────────────
        company   = a.get("company_name", ticker)
        sector_v  = fund.get("sector", "") if fund else a.get("sector", "")
        catalyst  = a.get("catalyst_type", "")
        cat_detail = a.get("catalyst_detail", "")
        thesis    = a.get("thesis", "")

        col_desc, col_plan = st.columns([1, 1])

        with col_desc:
            st.markdown("##### 🏢 O que é esta empresa")
            if sector_v:
                st.markdown(
                    f'<span class="tag tag-growth">{sector_v}</span>',
                    unsafe_allow_html=True
                )
            if catalyst:
                st.markdown(
                    f'<span class="tag tag-ep">{catalyst}</span> {cat_detail}',
                    unsafe_allow_html=True
                )
            if thesis:
                st.markdown(
                    f'<div style="background:#0d1422;border-left:3px solid #00e87a40;'
                    f'padding:10px 14px;border-radius:4px;margin:8px 0;'
                    f'color:#c9d1e0;font-size:0.9em">{thesis}</div>',
                    unsafe_allow_html=True
                )

            # Explicação das métricas chave
            st.markdown("##### 📖 O que os números significam")

            # MAGNA score
            magna_interp = (
                "Excelente setup — fundamentos fortes + gap + neglect em simultâneo." if ep_score >= 75
                else "Setup razoável — tem pontos fortes mas também limitações." if ep_score >= 50
                else "Setup fraco — considera aguardar melhor oportunidade."
            )
            st.markdown(
                f'<div style="background:#0d1422;border:1px solid #1e2d45;border-radius:6px;'
                f'padding:10px 14px;margin:4px 0">'
                f'<div style="color:#667a99;font-size:0.72em;text-transform:uppercase;'
                f'letter-spacing:1px">MAGNA {ep_score}/100</div>'
                f'<div style="color:#c9d1e0;font-size:0.85em;margin-top:4px">{magna_interp}</div>'
                f'</div>',
                unsafe_allow_html=True
            )

            # Volume
            vol_r = raw.get("vol_ratio", 0)
            vol_interp = (
                f"Volume extremo ({vol_r:.0f}× o normal) — instituições a comprar activamente. "
                "É o sinal mais forte que existe num EP." if vol_r >= 10
                else f"Volume elevado ({vol_r:.0f}× o normal) — interesse real mas não explosivo." if vol_r >= 5
                else f"Volume moderado ({vol_r:.0f}× o normal) — confirma o gap mas sem entusiasmo institucional."
            )
            st.markdown(
                f'<div style="background:#0d1422;border:1px solid #1e2d45;border-radius:6px;'
                f'padding:10px 14px;margin:4px 0">'
                f'<div style="color:#667a99;font-size:0.72em;text-transform:uppercase;'
                f'letter-spacing:1px">VOLUME {vol_r:.1f}×</div>'
                f'<div style="color:#c9d1e0;font-size:0.85em;margin-top:4px">{vol_interp}</div>'
                f'</div>',
                unsafe_allow_html=True
            )

            # Neglect
            nl_score = neglect.get("score", 0)
            nl_label = neglect.get("label", "—")
            nl_interp = (
                "A acção estava completamente ignorada antes de hoje — poucos fundos tinham posição. "
                "Quando uma acção negligenciada reporta uma surpresa, não há vendedores posicionados. "
                "Isto amplifica o movimento." if nl_score >= 70
                else "A acção tinha alguma cobertura mas não era muito seguida. "
                "O efeito surpresa ainda existe mas é menor." if nl_score >= 40
                else "A acção já era bem conhecida — muitos fundos tinham posição. "
                "A surpresa já pode estar parcialmente antecipada."
            )
            st.markdown(
                f'<div style="background:#0d1422;border:1px solid #1e2d45;border-radius:6px;'
                f'padding:10px 14px;margin:4px 0">'
                f'<div style="color:#667a99;font-size:0.72em;text-transform:uppercase;'
                f'letter-spacing:1px">NEGLECT — {nl_label}</div>'
                f'<div style="color:#c9d1e0;font-size:0.85em;margin-top:4px">{nl_interp}</div>'
                f'</div>',
                unsafe_allow_html=True
            )

        with col_plan:
            # ── PLANO DE TRADING ──────────────────────────────────────────────
            st.markdown("##### 📋 Plano de Trading")

            price      = raw.get("price", 0)
            stop_price = magna.get("stop_price", 0) if magna else 0
            if not stop_price:
                stop_pct_val = a.get("stop_loss_pct", 8) / 100
                stop_price   = round(price * (1 - stop_pct_val), 2)
            stop_pct_show = round((price - stop_price) / price * 100, 1) if price > 0 else 8

            t1 = round(price * 1.20, 2)
            t2 = round(price * 1.40, 2)
            t3 = round(price * 1.60, 2)

            rr = round((t1 - price) / max(price - stop_price, 0.01), 1)
            rr_color = "#00e87a" if rr >= 2 else "#f5c842" if rr >= 1.2 else "#ff5e5e"
            rr_note  = "Bom risco/retorno" if rr >= 2 else "Aceitável" if rr >= 1.2 else "⚠️ Risco elevado"

            st.markdown(
                f'<div style="background:#0a1628;border:1px solid #1e3a5f;'
                f'border-radius:8px;padding:14px 16px;margin:4px 0">'
                f'<div style="color:#667a99;font-size:0.72em;letter-spacing:1px;margin-bottom:10px">'
                f'ENTRADA · STOP · TARGETS (Método Pradeep — 4 tranches)</div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Entrada (preço actual)</span>'
                f'<span style="color:#e8edf5;font-weight:600;font-family:monospace">'
                f'${price:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Stop (mínima do gap day)</span>'
                f'<span style="color:#ff5e5e;font-weight:600;font-family:monospace">'
                f'${stop_price:.2f} (-{stop_pct_show:.1f}%)</span></div>'

                f'<div style="border-top:1px solid #1e2d45;margin:8px 0"></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Target 1 (+20%) → vender 25%</span>'
                f'<span style="color:#00e87a;font-family:monospace">${t1:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Target 2 (+40%) → vender 25%</span>'
                f'<span style="color:#00e87a;font-family:monospace">${t2:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Target 3 (+60%) → vender 25%</span>'
                f'<span style="color:#00e87a;font-family:monospace">${t3:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Trailing → manter 25% restantes</span>'
                f'<span style="color:#a78bfa;font-family:monospace">stop móvel</span></div>'

                f'<div style="border-top:1px solid #1e2d45;margin-top:8px;padding-top:8px;'
                f'display:flex;justify-content:space-between">'
                f'<span style="color:#667a99;font-size:0.82em">Risco/Retorno (até T1)</span>'
                f'<span style="color:{rr_color};font-weight:600">1:{rr} — {rr_note}</span>'
                f'</div></div>',
                unsafe_allow_html=True
            )

            # Porquê este stop
            st.markdown(
                f'<div style="background:#ff5e5e10;border-left:3px solid #ff5e5e40;'
                f'padding:8px 12px;border-radius:4px;margin:8px 0;font-size:0.82em;color:#c9d1e0">'
                f'<b>Porquê este stop?</b> Se o preço fechar abaixo de ${stop_price:.2f} (mínima '
                f'do dia do gap), significa que o catalisador não foi suficiente para sustentar '
                f'o movimento. O gap falhou — saímos sem discussão.</div>',
                unsafe_allow_html=True
            )

            # Red flags
            if a.get("red_flags"):
                st.warning(f"⚠️ {a['red_flags']}")

        st.divider()

        # ── KB INSIGHTS — SETUPS SIMILARES ───────────────────────────────────
        try:
            from knowledge_base import get_similar_setups
            gap_v = raw.get("gap_pct", 0)
            kb_sim = get_similar_setups(gap_v, ep_type=ep_type, sector=sector_v)

            if kb_sim.get("available") and kb_sim.get("n", 0) >= 3:
                st.markdown("##### 🧠 O que a KB aprendeu com setups similares")
                wr   = kb_sim["win_rate"]
                avg  = kb_sim["avg_return"]
                n    = kb_sim["n"]
                best_r = kb_sim["best"]
                wr_c = "#00e87a" if wr >= 50 else "#f5c842" if wr >= 35 else "#ff5e5e"

                km1, km2, km3, km4 = st.columns(4)
                km1.metric("Trades similares", n)
                km2.metric("Win Rate", f"{wr}%")
                km3.metric("Avg Return", f"{avg:+.1f}%")
                km4.metric("Melhor resultado", f"+{best_r:.0f}%")

                st.caption(f"Gap similar ({kb_sim['gap_range']}) · {n} trades na KB")

                if kb_sim.get("avg_stop_day"):
                    st.markdown(
                        f'<div style="background:#ff5e5e10;border-left:3px solid #ff5e5e40;'
                        f'padding:8px 12px;border-radius:4px;font-size:0.82em;color:#c9d1e0">'
                        f'Nos trades que activaram o stop, isso aconteceu em média ao <b>dia {kb_sim["avg_stop_day"]:.0f}</b>. '
                        f'Se nos primeiros dias o preço não se mantiver, considera sair cedo.</div>',
                        unsafe_allow_html=True
                    )

                # Exemplos concretos
                examples_win  = kb_sim.get("examples_win", [])
                examples_loss = kb_sim.get("examples_loss", [])
                if examples_win or examples_loss:
                    ex_col1, ex_col2 = st.columns(2)
                    with ex_col1:
                        if examples_win:
                            st.markdown("**✅ Melhores casos:**")
                            for ex in examples_win:
                                st.markdown(
                                    f'<div style="font-size:0.82em;color:#c9d1e0;padding:2px 0">'
                                    f'<span style="color:#00e87a;font-family:monospace">'
                                    f'{ex["ticker"]}</span> '
                                    f'+{ex["total_return_pct"]:.0f}% em {ex["holding_days"]}d</div>',
                                    unsafe_allow_html=True
                                )
                    with ex_col2:
                        if examples_loss:
                            st.markdown("**❌ Casos que falharam:**")
                            for ex in examples_loss:
                                st.markdown(
                                    f'<div style="font-size:0.82em;color:#c9d1e0;padding:2px 0">'
                                    f'<span style="color:#ff5e5e;font-family:monospace">'
                                    f'{ex["ticker"]}</span> '
                                    f'{ex["total_return_pct"]:.0f}% em {ex["holding_days"]}d</div>',
                                    unsafe_allow_html=True
                                )
        except Exception:
            pass

        # ── FORWARD TRACKER — HISTÓRICO DO MESMO TIPO ────────────────────────
        try:
            from ep_forward_tracker import get_tracker_stats
            ft_stats = get_tracker_stats()
            ft_closed = [
                p for p in ft_stats.get("closed", [])
                if p.get("ep_type") == ep_type or p.get("sector") == sector_v
            ]
            if ft_closed:
                st.markdown("##### 🧪 Forward Tracker — candidatos anteriores similares")
                for p in ft_closed[:4]:
                    ret   = p.get("return_pct", 0) or 0
                    emoji = "✅" if p["status"] == "WIN" else "❌"
                    st.markdown(
                        f'<div style="font-size:0.85em;color:#c9d1e0;padding:3px 0">'
                        f'{emoji} <span style="font-family:monospace;color:#e8edf5">'
                        f'{p["ticker"]}</span> '
                        f'<span style="color:{"#00e87a" if ret > 0 else "#ff5e5e"}">'
                        f'{ret:+.1f}%</span> · {p["hold_days"]}d · {p.get("exit_reason","—")}'
                        f'</div>',
                        unsafe_allow_html=True
                    )
        except Exception:
            pass

        # ── MAGNA BREAKDOWN + KB NOTAS ────────────────────────────────────────
        _kb_adj = st.session_state.get("kb_adj", {})
        if _kb_adj.get("data_available"):
            rec_gap = _kb_adj.get("min_gap_recommended")
            rec_vol = _kb_adj.get("min_vol_ratio_recommended")
            gap_pct_v = raw.get("gap_pct", 0)
            vol_rv    = raw.get("vol_ratio", 0)
            _kb_notes = []
            if rec_gap and gap_pct_v < rec_gap:
                _kb_notes.append(f"gap {gap_pct_v:.0f}% abaixo do recomendado ({rec_gap}%)")
            if rec_vol and vol_rv < rec_vol:
                _kb_notes.append(f"vol {vol_rv:.1f}× abaixo do recomendado ({rec_vol}×)")
            if raw.get("ticker") in _kb_adj.get("avoided_tickers", []):
                _kb_notes.append("ticker em blacklist da KB")
            if not _kb_notes:
                _kb_notes.append("gap e vol dentro dos parâmetros recomendados pela KB")
            st.markdown(
                f'<div style="background:#a78bfa10;border-left:2px solid #a78bfa33;'
                f'padding:6px 12px;border-radius:4px;margin:6px 0;font-size:0.82em">'
                f'<span style="color:#a78bfa">🧠 KB diz:</span> '
                f'<span style="color:#c9d1e0">{" · ".join(_kb_notes)}</span></div>',
                unsafe_allow_html=True
            )

        if magna and magna.get("breakdown"):
            with st.expander("📊 MAGNA 53 breakdown detalhado", expanded=False):
                if magna.get("kb_applied") and magna.get("kb_notes"):
                    raw_t = magna.get("raw_total", magna["total"])
                    fin_t = magna["total"]
                    if raw_t != fin_t:
                        st.caption(f"Score base: {raw_t} → ajustado pela KB: {fin_t}")
                render_magna_breakdown(magna)


def render_canslim_card(raw, a, wl_tickers, oneil: dict = None):
    """Card CANSLIM com ONEIL 70 + plano de trading KB-derived + descrição."""
    a      = a or {}
    oneil  = oneil or {}
    ticker = raw["ticker"]
    in_wl  = ticker in wl_tickers

    oneil_total  = oneil.get("total", 0)
    grade        = oneil.get("grade") or a.get("grade", "—")
    setup_type   = oneil.get("setup_type", "")
    display_score = oneil_total if oneil_total else a.get("canslim_score", 0)
    sector       = a.get("sector") or raw.get("sector", "")
    company      = a.get("company_name", ticker)
    pct_high     = oneil.get("pct_from_high", 0)

    setup_label = {"BREAKOUT": "🚀 BREAKOUT", "FOLLOW_ON": "📉→📈 FOLLOW-ON", "WEAK": "⚠️ FRACO"}.get(setup_type, setup_type)
    grade_color = {"A": "#00e87a", "B": "#f5c842", "C": "#fb923c", "D": "#ff5e5e"}.get(grade, "#667a99")

    header = (
        f"**{ticker}** · {company}  "
        f"|  +{raw['change_pct']}%  "
        f"|  Vol {raw['vol_ratio']}×  "
        f"|  ONEIL {oneil_total}/70  "
        f"|  {setup_label}"
    )

    with st.expander(header, expanded=False):

        # ── LINHA 1: Score + métricas + watch ────────────────────────────────
        c1, c2, c3, c4 = st.columns([1, 2, 2, 1])
        with c1:
            st.markdown(
                f"<div class='{score_class(display_score * 100 // 70)}' "
                f"style='font-size:1.6em'>{oneil_total}</div>"
                f"<div style='color:#667a99;font-size:0.72em'>ONEIL 70</div>"
                f"<div style='color:{grade_color};font-weight:700;font-size:1.3em;font-family:monospace'>{grade}</div>",
                unsafe_allow_html=True
            )
            if sector: st.caption(sector)
        with c2:
            st.markdown(
                f"**Preço:** `${raw['price']:.2f}` · Var `+{raw['change_pct']:.1f}%`  \n"
                f"**Vol:** `{raw['vol_ratio']:.1f}×` · **% máx 52s:** `{pct_high:.0f}%`"
            )
        with c3:
            stop_pct = 8
            stop_price = round(raw["price"] * 0.92, 2)
            st.markdown(
                f"**Setup:** {setup_label}  \n"
                f"**Stop O'Neil:** `${stop_price:.2f}` (-{stop_pct}%)"
            )
        with c4:
            if in_wl:
                if st.button("★ Remove", key=f"rm_cs_{ticker}"):
                    remove_from_watchlist(ticker); st.rerun()
            else:
                if st.button("☆ Watch", key=f"add_cs_{ticker}"):
                    add_to_watchlist({
                        "ticker": ticker, "type": "CANSLIM",
                        "score": display_score, "price": raw["price"],
                        "change_pct": raw["change_pct"], "grade": grade,
                        "setup_type": setup_type, "thesis": a.get("thesis", ""),
                        "added": date.today().strftime("%Y-%m-%d"),
                    })
                    st.rerun()

        st.divider()

        col_desc, col_plan = st.columns([1, 1])

        with col_desc:
            # ── DESCRIÇÃO ─────────────────────────────────────────────────────
            st.markdown("##### 🏢 O que é esta empresa")
            if sector:
                st.markdown(f'<span class="tag tag-canslim">{sector}</span>', unsafe_allow_html=True)
            thesis = a.get("thesis", "")
            if thesis:
                st.markdown(
                    f'<div style="background:#0d1422;border-left:3px solid #00b4ff40;'
                    f'padding:10px 14px;border-radius:4px;margin:8px 0;'
                    f'color:#c9d1e0;font-size:0.9em">{thesis}</div>',
                    unsafe_allow_html=True
                )

            # Explicação critérios ONEIL
            st.markdown("##### 📖 O que os critérios ONEIL significam")

            # Grade
            grade_interp = {
                "A": "Empresa líder — fundamentos excelentes, crescimento forte e consistente.",
                "B": "Empresa sólida — bons fundamentos mas com algumas limitações.",
                "C": "Empresa mediana — vale monitorizar mas não é prioritária.",
                "D": "Empresa fraca — evitar ou aguardar melhoria dos fundamentos.",
            }.get(grade, "Sem classificação disponível.")
            st.markdown(
                f'<div style="background:#0d1422;border:1px solid #1e2d45;border-radius:6px;'
                f'padding:10px 14px;margin:4px 0">'
                f'<div style="color:{grade_color};font-size:0.72em;font-weight:700;'
                f'letter-spacing:1px">GRADE {grade}</div>'
                f'<div style="color:#c9d1e0;font-size:0.85em;margin-top:4px">{grade_interp}</div>'
                f'</div>',
                unsafe_allow_html=True
            )

            # Setup type
            setup_interp = {
                "BREAKOUT": (
                    f"A acção está a romper o máximo de 52 semanas (+{pct_high:.0f}% do máximo). "
                    "No método O'Neil, é o momento ideal para entrar — os líderes de mercado "
                    "geralmente continuam a subir depois de romper novas máximas com volume."
                ),
                "FOLLOW_ON": (
                    f"A acção já fez um breakout anteriormente e está a consolidar. "
                    f"Está a {100-pct_high:.0f}% abaixo do máximo recente — uma segunda oportunidade "
                    "de entrada com risco mais baixo que na rutura original."
                ),
                "WEAK": "Setup não cumpre os critérios mínimos ONEIL. Monitorizar mas não entrar ainda.",
            }.get(setup_type, "")
            if setup_interp:
                st.markdown(
                    f'<div style="background:#0d1422;border:1px solid #1e2d45;border-radius:6px;'
                    f'padding:10px 14px;margin:4px 0">'
                    f'<div style="color:#667a99;font-size:0.72em;text-transform:uppercase;'
                    f'letter-spacing:1px">SETUP — {setup_type}</div>'
                    f'<div style="color:#c9d1e0;font-size:0.85em;margin-top:4px">{setup_interp}</div>'
                    f'</div>',
                    unsafe_allow_html=True
                )

        with col_plan:
            # ── PLANO DE TRADING CANSLIM ──────────────────────────────────────
            st.markdown("##### 📋 Plano de Trading (O'Neil)")

            price = raw.get("price", 0)
            stop_p = round(price * 0.92, 2)

            # Targets diferentes por setup
            if setup_type == "FOLLOW_ON":
                t1 = round(price * 1.10, 2)
                t2 = round(price * 1.20, 2)
                t3 = round(price * 1.35, 2)
                t_note = "Follow-on: targets mais conservadores"
            else:
                t1 = round(price * 1.20, 2)
                t2 = round(price * 1.40, 2)
                t3 = round(price * 2.00, 2)
                t_note = "Breakout: segurar para movimento maior"

            rr = round((t1 - price) / max(price - stop_p, 0.01), 1)
            rr_color = "#00e87a" if rr >= 2 else "#f5c842" if rr >= 1.2 else "#ff5e5e"

            st.markdown(
                f'<div style="background:#0a1628;border:1px solid #1e3a5f;'
                f'border-radius:8px;padding:14px 16px;margin:4px 0">'
                f'<div style="color:#667a99;font-size:0.72em;letter-spacing:1px;margin-bottom:10px">'
                f'ENTRADA · STOP · TARGETS — {t_note}</div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Entrada (preço actual)</span>'
                f'<span style="color:#e8edf5;font-weight:600;font-family:monospace">'
                f'${price:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Stop O\'Neil (-8%)</span>'
                f'<span style="color:#ff5e5e;font-weight:600;font-family:monospace">'
                f'${stop_p:.2f}</span></div>'

                f'<div style="border-top:1px solid #1e2d45;margin:8px 0"></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Target 1 → vender 25%</span>'
                f'<span style="color:#00e87a;font-family:monospace">${t1:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Target 2 → vender 25%</span>'
                f'<span style="color:#00e87a;font-family:monospace">${t2:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Target 3 → vender 25%</span>'
                f'<span style="color:#00e87a;font-family:monospace">${t3:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Trailing → últimos 25%</span>'
                f'<span style="color:#a78bfa;font-family:monospace">stop móvel MA50</span></div>'

                f'<div style="border-top:1px solid #1e2d45;margin-top:8px;padding-top:8px;'
                f'display:flex;justify-content:space-between">'
                f'<span style="color:#667a99;font-size:0.82em">Risco/Retorno (até T1)</span>'
                f'<span style="color:{rr_color};font-weight:600">1:{rr}</span>'
                f'</div></div>',
                unsafe_allow_html=True
            )

            # Regra O'Neil de saída
            st.markdown(
                f'<div style="background:#00b4ff10;border-left:3px solid #00b4ff40;'
                f'padding:8px 12px;border-radius:4px;margin:8px 0;font-size:0.82em;color:#c9d1e0">'
                f'<b>Regra O\'Neil:</b> Nunca deixar uma perda ir além de 8%. '
                f'Para líderes fortes (Grade A), o stop pode ser mantido durante '
                f'consolidações normais até à MA50.</div>',
                unsafe_allow_html=True
            )

        # ── FORWARD TRACKER — histórico CANSLIM ──────────────────────────────
        try:
            from ep_forward_tracker import get_tracker_stats
            ft_stats = get_tracker_stats()
            ft_cs = [
                p for p in ft_stats.get("closed", [])
                if p.get("strategy_type") == "CANSLIM"
            ]
            if ft_cs:
                st.divider()
                st.markdown("##### 🧪 Forward Tracker — candidatos CANSLIM anteriores")
                cs_stats = ft_stats.get("canslim_stats", {})
                if cs_stats.get("n", 0) > 0:
                    fk1, fk2, fk3 = st.columns(3)
                    fk1.metric("Trades CANSLIM", cs_stats["n"])
                    fk2.metric("Win Rate", f"{cs_stats['win_rate']}%")
                    fk3.metric("Avg Return", f"{cs_stats['avg_return']:+.1f}%")
                for p in ft_cs[:4]:
                    ret   = p.get("return_pct", 0) or 0
                    emoji = "✅" if p["status"] == "WIN" else "❌"
                    setup_t = p.get("oneil_setup", "—")
                    st.markdown(
                        f'<div style="font-size:0.85em;color:#c9d1e0;padding:3px 0">'
                        f'{emoji} <span style="font-family:monospace;color:#e8edf5">'
                        f'{p["ticker"]}</span> '
                        f'<span style="color:{"#00e87a" if ret > 0 else "#ff5e5e"}">'
                        f'{ret:+.1f}%</span> · {p["hold_days"]}d · {setup_t}'
                        f'</div>',
                        unsafe_allow_html=True
                    )
        except Exception:
            pass

        # ONEIL breakdown
        if oneil.get("breakdown"):
            with st.expander("📊 ONEIL 70 breakdown detalhado", expanded=False):
                render_oneil_breakdown(oneil)


def render_watchlist():
    wl = load_watchlist()
    if not wl:
        st.info("Watchlist vazia. Usa ☆ Watch nos candidatos para guardar.")
        return
    st.markdown(f"**{len(wl)} tickers na watchlist**")
    for item in wl:
        ticker    = item.get("ticker", "?")
        typ       = item.get("type", "")
        ep_type   = item.get("ep_type", "")
        tag_class = "tag-ep" if typ == "EP" else "tag-canslim"
        with st.expander(
            f"**{ticker}**  |  Score: {item.get('score','—')}  |  ${item.get('price','—')}  |  {item.get('added','')}",
            expanded=False
        ):
            c1, c2 = st.columns([4, 1])
            with c1:
                st.markdown(f'<span class="tag {tag_class}">{typ}</span>', unsafe_allow_html=True)
                if ep_type: st.markdown(ep_type_tag(ep_type), unsafe_allow_html=True)
                if item.get("neglect"): st.markdown(f'<span class="tag tag-neglect">{item["neglect"]}</span>', unsafe_allow_html=True)
                if item.get("thesis"): st.caption(item["thesis"])
            with c2:
                if st.button("Remove", key=f"del_{ticker}"):
                    remove_from_watchlist(ticker); st.rerun()


# ─── MAIN ─────────────────────────────────────────────────────────────────────

st.markdown("## ⚡ EP Scanner")
st.markdown(
    '<span class="tag tag-ep">Episodic Pivot</span>'
    '<span class="tag tag-canslim">CANSLIM</span>'
    ' &nbsp; MAGNA 53 (EP) · ONEIL 70 (CANSLIM) · Neglect Detection · Polygon + yfinance + Claude',
    unsafe_allow_html=True
)
st.divider()

with st.sidebar:
    st.markdown("### ⚙️ Configuração")
    scan_type = st.radio("Tipo de scan:", ["EP (Episodic Pivot)", "CANSLIM", "Ambos"])
    st.divider()

    # ── Carregar KB para usar nos sliders ────────────────────────────────────
    _kb = load_kb_adjustments()
    _kb_active    = _kb.get("data_available", False)
    _kb_gap_rec   = _kb.get("min_gap_recommended")    # ex: 20
    _kb_vol_rec   = _kb.get("min_vol_ratio_recommended")  # ex: 10
    _kb_n         = _kb.get("total_trades_in_kb", 0)
    _kb_gap_mults = _kb.get("gap_multipliers", {})
    _kb_vol_mults = _kb.get("vol_multipliers", {})

    # Default inteligente: usa KB se disponível, senão usa valor conservador
    _gap_default = int(_kb_gap_rec) if _kb_gap_rec and _kb_active else 8
    _gap_default = max(8, min(25, _gap_default))  # clamp ao range do slider

    # ── FILTROS EP ───────────────────────────────────────────────────────────
    st.markdown("### 🎚️ Filtros EP")

    # Gap slider com indicador KB
    min_gap = st.slider("Gap mínimo (%)", 3, 25, _gap_default)

    # Feedback inline do gap seleccionado vs KB
    if _kb_active:
        gap_bucket = (
            "25_plus" if min_gap >= 25 else
            "15_25"   if min_gap >= 15 else
            "8_15"    if min_gap >= 8  else "5_8"
        )
        gap_mult  = _kb_gap_mults.get(gap_bucket, 1.0)
        gap_label = {
            "25_plus": ("gap ≥25%", "#00e87a", "Melhor edge — avg +21.9% histórico"),
            "15_25":   ("gap 15-25%", "#f5c842", "Edge moderado — avg +1.4% histórico"),
            "8_15":    ("gap 8-15%", "#ff5e5e", "Fraco — avg +0.3% histórico. KB recomenda subir."),
            "5_8":     ("gap 5-8%", "#ff5e5e", "Sem edge — ruído de mercado"),
        }.get(gap_bucket, ("—", "#667a99", ""))
        color, note = gap_label[1], gap_label[2]

        # Mostrar recomendação KB com cor
        if _kb_gap_rec and min_gap < _kb_gap_rec:
            st.markdown(
                f'<div style="background:#ff5e5e10;border-left:3px solid #ff5e5e40;'
                f'padding:6px 10px;border-radius:4px;font-size:0.78em;margin:-8px 0 8px 0">'
                f'<span style="color:#ff5e5e">⚠️ KB recomenda ≥{_kb_gap_rec}%</span><br>'
                f'<span style="color:#667a99">{note}</span></div>',
                unsafe_allow_html=True
            )
        elif _kb_gap_rec and min_gap >= _kb_gap_rec:
            st.markdown(
                f'<div style="background:#00e87a10;border-left:3px solid #00e87a40;'
                f'padding:6px 10px;border-radius:4px;font-size:0.78em;margin:-8px 0 8px 0">'
                f'<span style="color:#00e87a">✅ Alinhado com KB (≥{_kb_gap_rec}%)</span><br>'
                f'<span style="color:#667a99">{note}</span></div>',
                unsafe_allow_html=True
            )
        else:
            st.caption(note)

        # Mini gráfico de barras por bucket de gap
        if _kb_gap_mults:
            bar_html = '<div style="display:flex;gap:3px;margin:4px 0 10px 0;align-items:flex-end;height:28px">'
            buckets_order = [("5_8","5-8%"), ("8_15","8-15%"), ("15_25","15-25%"), ("25_plus","25%+")]
            for bk, bl in buckets_order:
                m    = _kb_gap_mults.get(bk, 1.0)
                h    = int(m * 18)
                bc   = "#00e87a" if m >= 1.2 else "#f5c842" if m >= 0.9 else "#ff5e5e"
                bold = "font-weight:700;" if bk == gap_bucket else ""
                bar_html += (
                    f'<div style="display:flex;flex-direction:column;align-items:center;flex:1">'
                    f'<div style="background:{bc};height:{h}px;width:100%;border-radius:2px"></div>'
                    f'<div style="color:#667a99;font-size:0.6em;{bold}margin-top:2px">{bl}</div>'
                    f'</div>'
                )
            bar_html += '</div>'
            st.markdown(bar_html, unsafe_allow_html=True)
    else:
        st.caption("KB sem dados — corre o backtest para activar recomendações")

    min_vol_ep   = st.number_input("Volume mínimo EP", value=300_000, step=50_000)
    st.caption("Liquidez mínima para entrar/sair sem dificuldade. 300k é adequado.")

    min_price_ep = st.slider("Preço mínimo EP ($)", 5, 30, 10)
    st.caption("Evita penny stocks. Abaixo de $8 os movimentos são frequentemente artificiais.")

    st.divider()

    # ── FILTROS CANSLIM ──────────────────────────────────────────────────────
    st.markdown("### 🎚️ Filtros CANSLIM")

    min_change = st.slider("Variação mínima (%)", 1, 10, 3)
    # Feedback inline
    if min_change < 3:
        st.markdown(
            '<div style="background:#ff5e5e10;border-left:3px solid #ff5e5e40;'
            'padding:6px 10px;border-radius:4px;font-size:0.78em;margin:-8px 0 8px 0">'
            '<span style="color:#ff5e5e">⚠️ Abaixo de 3% capta muito ruído</span><br>'
            '<span style="color:#667a99">Recomendado: 3-4% para filtrar movimentos sem significado</span></div>',
            unsafe_allow_html=True
        )
    elif min_change >= 3:
        st.markdown(
            '<div style="background:#00e87a10;border-left:3px solid #00e87a40;'
            'padding:6px 10px;border-radius:4px;font-size:0.78em;margin:-8px 0 8px 0">'
            '<span style="color:#00e87a">✅ Bom filtro</span><br>'
            '<span style="color:#667a99">Captura movimentos com intenção real</span></div>',
            unsafe_allow_html=True
        )

    min_price_cs = st.slider("Preço mínimo CANSLIM ($)", 10, 100, 15)
    st.caption("O'Neil recomenda acções acima de $15. Líderes de sector raramente estão abaixo disso.")

    min_vol_cs = st.number_input("Volume mínimo CANSLIM", value=300_000, step=50_000)
    st.caption("Igual ao EP — garante liquidez adequada.")

    st.divider()

    # ── KB STATUS DETALHADO ───────────────────────────────────────────────────
    st.markdown("### 🧠 Knowledge Base")
    if _kb_active:
        _av = len(_kb.get("avoided_tickers", []))
        st.markdown(
            f'<span style="color:#00e87a;font-family:Space Mono;font-size:.8em">'
            f'● ACTIVA · {_kb_n} trades</span>',
            unsafe_allow_html=True
        )

        # Recomendações em destaque
        if _kb_gap_rec or _kb_vol_rec:
            rec_html = '<div style="background:#0d1422;border:1px solid #1e2d45;border-radius:6px;padding:8px 10px;margin:6px 0">'
            rec_html += '<div style="color:#667a99;font-size:0.7em;letter-spacing:1px;margin-bottom:4px">KB RECOMENDA</div>'
            if _kb_gap_rec:
                rec_html += f'<div style="color:#00e87a;font-size:0.85em">Gap ≥ <b>{_kb_gap_rec}%</b></div>'
            if _kb_vol_rec:
                rec_html += f'<div style="color:#00e87a;font-size:0.85em">Volume ≥ <b>{_kb_vol_rec}×</b></div>'
            if _av:
                rec_html += f'<div style="color:#f5c842;font-size:0.82em">⚠️ {_av} tickers em blacklist</div>'
            rec_html += '</div>'
            st.markdown(rec_html, unsafe_allow_html=True)

        # Performance por gap — tabela compacta
        if _kb_gap_mults:
            with st.expander("📊 Performance por gap (KB)", expanded=False):
                perf_data = {
                    "5-8%":  _kb_gap_mults.get("5_8", 1.0),
                    "8-15%": _kb_gap_mults.get("8_15", 1.0),
                    "15-25%":_kb_gap_mults.get("15_25", 1.0),
                    "≥25%":  _kb_gap_mults.get("25_plus", 1.0),
                }
                for bucket, mult in perf_data.items():
                    c = "#00e87a" if mult >= 1.2 else "#f5c842" if mult >= 0.9 else "#ff5e5e"
                    arrow = "▲" if mult >= 1.0 else "▼"
                    st.markdown(
                        f'<div style="display:flex;justify-content:space-between;'
                        f'font-size:0.82em;padding:2px 0;color:#c9d1e0">'
                        f'<span>{bucket}</span>'
                        f'<span style="color:{c}">{arrow} ×{mult}</span></div>',
                        unsafe_allow_html=True
                    )
                st.caption(f"Multiplicadores aplicados ao MAGNA score. Base: {_kb_n} trades.")
    else:
        st.markdown(
            '<span style="color:#667a99;font-family:Space Mono;font-size:.8em">'
            '○ SEM DADOS · corre backtest</span>',
            unsafe_allow_html=True
        )
        st.caption("Após o primeiro backtest, os sliders mostram recomendações automáticas.")

    st.caption(f"📌 Watchlist: {len(load_watchlist())} tickers")

    # MAGNA reference (colapsado)
    with st.expander("📖 MAGNA 53 Ref"):
        st.markdown("""
**MA** Massive Acceleration earnings/revenue  
**G** Gap Up (≥8% surpresa)  
**N** Neglect (lateral/queda 2-6m)  
**A** Aceleração em vendas (≥39%)  
**5** Short interest ≥5 dias  
**3** ≥3 analistas a subir targets  
**CAP** Market cap <$10B  
**10** IPO <10 anos  
""")

tab_scan, tab_watchlist, tab_kb_learn, tab_tracker = st.tabs(["🔍 Scanner", "📌 Watchlist", "🧠 O que aprendi", "🧪 Forward Tracker"])

with tab_scan:
    today_str, prev_str = find_trading_day_with_data()
    if not today_str:
        today_str = last_trading_day(1)
        prev_str  = last_trading_day(2)
    st.caption(f"📅 Sessão: **{today_str}** · Referência: {prev_str} · Polygon EOD + yfinance fundamentais")

    if st.button("▶ RUN SCAN", type="primary"):
        if not POLYGON_KEY or not ANTHROPIC_KEY:
            st.error("Chaves API em falta. Verifica o .env"); st.stop()

        # ── 1. Dados de mercado ──────────────────────────────────────────────
        with st.spinner("A carregar dados de mercado (Polygon)..."):
            today_data = polygon_grouped(today_str)
            # Fallback automático: se sem dados, tentar dias anteriores
            if not today_data:
                st.warning(f"Sem dados para {today_str} (possível feriado) — a tentar dia anterior...")
                for days_back in range(1, 6):
                    candidate = (date.today() - timedelta(days=days_back+1))
                    if candidate.weekday() >= 5: continue
                    cand_str = candidate.strftime("%Y-%m-%d")
                    if cand_str in _NYSE_HOLIDAYS: continue
                    time.sleep(POLYGON_CALL_INTERVAL)
                    today_data = polygon_grouped(cand_str)
                    if today_data:
                        today_str = cand_str
                        st.info(f"A usar dados de {today_str}")
                        break
            time.sleep(POLYGON_CALL_INTERVAL)
            prev_data  = polygon_grouped(prev_str)

        if not today_data:
            st.error("Sem dados de mercado disponíveis. Verifica se é dia útil e se a chave Polygon está correcta.")
            st.stop()

        st.success(f"✅ {len(today_data):,} tickers carregados")

        do_ep      = scan_type in ["EP (Episodic Pivot)", "Ambos"]
        do_canslim = scan_type in ["CANSLIM", "Ambos"]

        # ── KB adjustments ───────────────────────────────────────────────────
        kb_adj = load_kb_adjustments()
        avoided_tickers = set(kb_adj.get("avoided_tickers", []))

        ep_raw      = scan_ep(today_data, prev_data, min_gap, min_vol_ep, min_price_ep) if do_ep else []
        canslim_raw = scan_canslim(today_data, prev_data, min_change, min_price_cs, min_vol_cs) if do_canslim else []

        # Filter KB blacklist from EP candidates
        if avoided_tickers and ep_raw:
            before = len(ep_raw)
            ep_raw = [r for r in ep_raw if r["ticker"] not in avoided_tickers]
            removed = before - len(ep_raw)
            if removed:
                st.caption(f"🧠 KB: {removed} ticker(s) removidos da blacklist")

        # ── 2. Fundamentais yfinance ─────────────────────────────────────────
        ep_fundamentals = {}
        magna_scores    = {}

        if ep_raw:
            ep_tickers = [r["ticker"] for r in ep_raw]
            prog_text  = st.empty()
            prog_bar   = st.progress(0)

            def update_prog(done, total, ticker):
                prog_bar.progress(done / total)
                prog_text.caption(f"yfinance: {ticker} ({done}/{total})")

            ep_fundamentals = fetch_fundamentals_batch(ep_tickers, update_prog)
            prog_bar.empty(); prog_text.empty()

            # ── 3. Neglect detection + MAGNA score ───────────────────────────
            neglect_prog = st.empty()
            for i, r in enumerate(ep_raw):
                t = r["ticker"]
                neglect_prog.caption(f"Neglect detection: {t} ({i+1}/{len(ep_raw)})")
                history = polygon_history(t, days=75)
                time.sleep(0.15)
                neglect = detect_neglect(t, history)
                fund    = ep_fundamentals.get(t, {})
                magna_scores[t] = magna53_score(r, fund, neglect, kb_adj)
            neglect_prog.empty()

            # Re-sort EP candidates by MAGNA score
            ep_raw.sort(key=lambda r: magna_scores.get(r["ticker"], {}).get("total", 0), reverse=True)
            ep_raw = ep_raw[:12]

        # ── 4. Claude analysis ───────────────────────────────────────────────
        ep_analysis      = []
        canslim_analysis = []

        if ep_raw:
            with st.spinner(f"Claude a analisar {min(len(ep_raw),5)} candidatos EP (MAGNA 53)..."):
                ep_analysis = claude_analyze_ep(ep_raw[:5], ep_fundamentals, magna_scores)

        # ── ONEIL 70 score (calculado localmente, sem Claude) ───────────────
        oneil_scores = {}
        cs_fundamentals = {}
        if canslim_raw:
            macro_ctx = st.session_state.get("macro_context")
            cs_prog   = st.empty()
            for i, r in enumerate(canslim_raw[:8]):
                t = r["ticker"]
                cs_prog.caption(f"ONEIL 70: {t} ({i+1}/{min(len(canslim_raw),8)})")
                # Reutilizar fundamentais se já foram carregados (EP)
                fund = ep_fundamentals.get(t) or fetch_fundamentals(t)
                cs_fundamentals[t] = fund
                # Enriquecer raw com dados 52w do yfinance
                r["high_52w"] = fund.get("high_52w") or r.get("high_52w", 0)
                r["low_52w"]  = fund.get("low_52w") or r.get("low_52w", 0)
                oneil_scores[t] = oneil70_score(r, fund)
            cs_prog.empty()
            # Re-ordenar por ONEIL score
            canslim_raw.sort(
                key=lambda r: oneil_scores.get(r["ticker"], {}).get("total", 0),
                reverse=True
            )

        if canslim_raw and ANTHROPIC_KEY:
            try:
                time.sleep(15)  # pausa entre EP e CANSLIM
                with st.spinner(f"Claude a analisar {min(len(canslim_raw),5)} candidatos CANSLIM..."):
                    canslim_analysis = claude_analyze_canslim(canslim_raw[:5])
            except Exception as e:
                st.warning(f"CANSLIM Claude falhou: {str(e)[:80]}")
                canslim_analysis = []

        # ── Save to session state ────────────────────────────────────────────
        st.session_state.ep_raw           = ep_raw
        st.session_state.canslim_raw      = canslim_raw
        st.session_state.ep_analysis      = ep_analysis
        st.session_state.canslim_analysis = canslim_analysis
        st.session_state.ep_fundamentals  = ep_fundamentals
        st.session_state.ep_magna         = magna_scores
        st.session_state.oneil_scores     = oneil_scores
        st.session_state.cs_fundamentals  = cs_fundamentals
        st.session_state.kb_adj           = kb_adj
        st.session_state.scan_done        = True

    # ── Display results ──────────────────────────────────────────────────────
    if st.session_state.scan_done:
        ep_raw           = st.session_state.ep_raw
        canslim_raw      = st.session_state.canslim_raw
        ep_analysis      = st.session_state.ep_analysis
        canslim_analysis = st.session_state.canslim_analysis
        ep_fundamentals  = st.session_state.ep_fundamentals
        magna_scores     = st.session_state.get("ep_magna", {})
        do_ep            = scan_type in ["EP (Episodic Pivot)", "Ambos"]
        do_canslim       = scan_type in ["CANSLIM", "Ambos"]

        def merge(raw_list, al):
            lk = {a["ticker"]: a for a in al}
            return [(r, lk.get(r["ticker"], {})) for r in raw_list]

        wl_tickers = {w["ticker"] for w in load_watchlist()}

        if do_ep:
            st.markdown(f"### ⚡ Episodic Pivot  ({len(ep_raw)} candidatos)")

            # Summary stats — usa o mesmo score que os cards (Claude ep_score ou MAGNA)
            if ep_raw:
                _analysis_lk = {a["ticker"]: a for a in ep_analysis}
                def _display_score(r):
                    _a   = _analysis_lk.get(r["ticker"], {})
                    _mag = magna_scores.get(r["ticker"], {})
                    return _a.get("ep_score") or _mag.get("total", 0)
                scores = [_display_score(r) for r in ep_raw]
                high   = sum(1 for s in scores if s >= 75)
                med    = sum(1 for s in scores if 50 <= s < 75)
                low    = sum(1 for s in scores if s < 50)
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("A+ Setup (≥75)", high)
                m2.metric("B Setup (50-74)", med)
                m3.metric("C Setup (<50)", low)
                m4.metric("Média MAGNA", f"{sum(scores)//max(len(scores),1)}/100")

            if ep_raw:
                # ── Best candidates section ───────────────────────────────────
                with st.spinner("🏆 A seleccionar e analisar o melhor setup..."):
                    best = pick_best_candidates(
                        ep_raw, ep_analysis, magna_scores,
                        ep_fundamentals, top_n=2
                    )
                if best:
                    render_best_candidates(best, magna_scores, ep_fundamentals)
                    st.divider()

                # ── All candidates ────────────────────────────────────────────
                st.markdown("### 📋 Todos os Candidatos")
                merged = merge(ep_raw, ep_analysis)
                for raw, analysis in merged:
                    t     = raw["ticker"]
                    magna = magna_scores.get(t)
                    fund  = ep_fundamentals.get(t, {})
                    render_ep_card(raw, analysis, magna, fund, wl_tickers)
            else:
                st.info("Nenhum candidato EP com os filtros actuais.")

        if do_ep and do_canslim:
            st.divider()

        if do_canslim:
            st.markdown(f"### 📊 CANSLIM  ({len(canslim_raw)} candidatos)")
            if canslim_raw:
                _oneil = st.session_state.get("oneil_scores", {})
                merged = sorted(
                    merge(canslim_raw, canslim_analysis),
                    key=lambda x: _oneil.get(x[0]["ticker"], {}).get("total", 0) or
                                  x[1].get("canslim_score", 0),
                    reverse=True
                )
                for raw, analysis in merged[:10]:
                    render_canslim_card(
                        raw, analysis, wl_tickers,
                        oneil=_oneil.get(raw["ticker"])
                    )
            else:
                st.info("Nenhum candidato CANSLIM com os filtros actuais.")

        st.divider()
        with st.expander("📋 Dados brutos + fundamentais"):
            if ep_raw:
                st.markdown("**EP — Dados de mercado**")
                st.dataframe(pd.DataFrame(ep_raw), use_container_width=True)
            if ep_fundamentals:
                st.markdown("**EP — Fundamentais (yfinance)**")
                fund_df = pd.DataFrame(ep_fundamentals).T
                st.dataframe(fund_df, use_container_width=True)
            if magna_scores:
                st.markdown("**EP — MAGNA 53 Scores**")
                magna_summary = {
                    t: {
                        "total": v.get("total", 0),
                        "ep_type": v.get("ep_type", ""),
                        "neglect_score": v.get("neglect", {}).get("score", 0),
                        "neglect_label": v.get("neglect", {}).get("label", ""),
                    }
                    for t, v in magna_scores.items()
                }
                st.dataframe(pd.DataFrame(magna_summary).T, use_container_width=True)
            if canslim_raw:
                st.markdown("**CANSLIM**")
                st.dataframe(pd.DataFrame(canslim_raw), use_container_width=True)

with tab_watchlist:
    st.markdown("### 📌 Watchlist")
    render_watchlist()

st.divider()
st.caption("⚠️ Apenas para fins informativos. Não é aconselhamento financeiro.")

# ─── TAB: O QUE O SISTEMA APRENDEU ──────────────────────────────────────────

with tab_kb_learn:
    st.markdown("## 🧠 O que o sistema aprendeu")
    st.markdown(
        "Insights derivados automaticamente de **{n} trades** backtestados. "
        "A KB converte padrões numéricos em conhecimento accionável.".format(
            n=load_kb_adjustments().get("total_trades_in_kb", 0)
        )
    )

    _kb_check = load_kb_adjustments()
    if not _kb_check.get("data_available"):
        st.info("KB ainda sem dados — corre o backtester primeiro e guarda os resultados na KB.")
    else:
        col_refresh, col_info = st.columns([1, 3])
        with col_refresh:
            if st.button("🔄 Gerar insights com Claude", type="primary"):
                with st.spinner("Claude a analisar os dados da KB..."):
                    try:
                        from knowledge_base import get_kb_narrative
                        narrative = get_kb_narrative(mode="panel")
                        st.session_state["kb_narrative_panel"] = narrative
                    except Exception as e:
                        st.error(f"Erro: {e}")
        with col_info:
            st.caption("Usa Claude para transformar os dados numéricos em lições compreensíveis.")

        narrative = st.session_state.get("kb_narrative_panel")

        if narrative and narrative.get("available"):
            parsed = narrative.get("parsed", {})

            if parsed.get("headline"):
                st.markdown(
                    f'<div style="background:#0d1422;border:1px solid #1e2d45;border-radius:8px;'
                    f'padding:16px 20px;margin:12px 0">'
                    f'<div style="color:#00e87a;font-size:1.1em;font-weight:600">'
                    f'{parsed["headline"]}</div></div>',
                    unsafe_allow_html=True
                )

            if parsed.get("key_finding"):
                st.info(f"🔑 {parsed['key_finding']}")

            lessons = parsed.get("lessons", [])
            if lessons:
                st.markdown("### 📚 Lições aprendidas")
                for i, lesson in enumerate(lessons):
                    with st.expander(
                        f"**{lesson.get('title', f'Lição {i+1}')}** — `{lesson.get('data','')}`",
                        expanded=(i == 0)
                    ):
                        st.markdown(lesson.get("explanation", ""))

            col_a, col_c = st.columns(2)
            with col_a:
                if parsed.get("action"):
                    st.markdown(
                        f'<div style="background:#00e87a10;border-left:3px solid #00e87a;'
                        f'padding:10px 14px;border-radius:4px">'
                        f'<span style="color:#00e87a;font-weight:600">✅ ACÇÃO</span><br>'
                        f'<span style="color:#c9d1e0">{parsed["action"]}</span></div>',
                        unsafe_allow_html=True
                    )
            with col_c:
                if parsed.get("caution"):
                    st.markdown(
                        f'<div style="background:#ff5e5e10;border-left:3px solid #ff5e5e;'
                        f'padding:10px 14px;border-radius:4px">'
                        f'<span style="color:#ff5e5e;font-weight:600">⚠️ CAUTELA</span><br>'
                        f'<span style="color:#c9d1e0">{parsed["caution"]}</span></div>',
                        unsafe_allow_html=True
                    )

            # Raw data table
            with st.expander("📊 Dados brutos da KB", expanded=False):
                data = narrative.get("data", {})
                if data.get("gap_performance"):
                    st.markdown("**Performance por Gap%:**")
                    import pandas as pd
                    rows = []
                    for label, s in data["gap_performance"].items():
                        rows.append({"Gap": label, "N": s["n"],
                                     "Win Rate": f"{s['win_rate']:.1f}%",
                                     "Avg Return": f"{s['avg_return']:+.1f}%"})
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

                if data.get("vol_performance"):
                    st.markdown("**Performance por Vol Ratio:**")
                    rows = []
                    for label, s in data["vol_performance"].items():
                        rows.append({"Volume": label, "N": s["n"],
                                     "Win Rate": f"{s['win_rate']:.1f}%",
                                     "Avg Return": f"{s['avg_return']:+.1f}%"})
                    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


with tab_tracker:
    st.markdown("## 🧪 Forward Tracker")
    st.caption("Rastreamento em tempo real dos candidatos encontrados pelo scanner · Alimenta a KB automaticamente")
    try:
        from ep_forward_tracker import render_tracker_tab
        render_tracker_tab()
    except ImportError:
        st.info("ep_forward_tracker.py não encontrado. Adiciona o ficheiro ao directório do projecto.")
    except Exception as e:
        st.error(f"Tracker erro: {e}")


if __name__ == "__main__":
    pass