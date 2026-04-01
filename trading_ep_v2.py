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

def load_macro_context(sectors: list = None) -> dict:
    """Load macro context (cached per session to avoid repeated API calls)."""
    try:
        from ep_macro_context import get_macro_context
        return get_macro_context(sectors)
    except Exception as e:
        return {"_source": "unavailable", "summary_pt": f"Macro não disponível: {e}"}

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

def last_trading_day(offset=1):
    """
    Return the Nth previous trading day (Mon-Fri) counting back from today.
    offset=1 -> last trading day (e.g. Friday when today is Monday)
    offset=2 -> trading day before that (e.g. Thursday when today is Monday)
    """
    d = date.today()
    count = 0
    while count < offset:
        d -= timedelta(days=1)
        if d.weekday() < 5:   # 0=Mon ... 4=Fri
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

def polygon_grouped(date_str):
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
    r = requests.get(url, params={"adjusted": "true", "apiKey": POLYGON_KEY}, timeout=30)
    if r.status_code != 200:
        st.error(f"Polygon error {r.status_code}: {r.text[:200]}")
        return {}
    data = r.json()
    if not data.get("results"):
        return {}
    return {item["T"]: item for item in data["results"]}

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

        return {
            "earnings_growth":     earnings_growth,
            "revenue_growth":      revenue_growth,
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
            })
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:12]


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

    # Add macro context to prompt if available
    macro_ctx = ""
    try:
        macro_data = st.session_state.get("macro_context")
        if macro_data and macro_data.get("_source") not in ("unavailable", "fallback"):
            from ep_macro_context import macro_to_prompt_context
            macro_ctx = macro_to_prompt_context(macro_data)
    except Exception:
        pass

    prompt = f"""You are an Episodic Pivot expert using Pradeep Bonde's MAGNA 53 + CAP 10×10 methodology.

{macro_ctx}

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
    Uses Claude to provide a detailed explanation of why each was chosen.
    """
    if not ep_raw:
        return []

    # Build composite score for ranking
    analysis_lk = {a["ticker"]: a for a in ep_analysis}

    scored = []
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

        # Type bonus
        type_bonus = {"TURNAROUND": 15, "GROWTH": 10, "STORY/NEGLECTED": 8,
                      "9M_EP": 5, "STANDARD": 0}.get(ep_type, 0)
        # Window bonus
        win_bonus  = {"PRIME": 10, "OPEN": 5, "LATE": 0}.get(window, 0)
        # Gap bonus (>25% gets extra)
        gap_bonus  = 10 if gap_pct >= 25 else 5 if gap_pct >= 15 else 0
        # Vol bonus (>10x gets extra)
        vol_bonus  = 10 if vol_ratio >= 10 else 5 if vol_ratio >= 5 else 0

        composite = ep_score + type_bonus + win_bonus + gap_bonus + vol_bonus

        scored.append({
            "ticker":    t,
            "composite": composite,
            "ep_score":  ep_score,
            "raw":       r,
            "analysis":  a,
            "magna":     mag,
            "fund":      fund,
        })

    scored.sort(key=lambda x: x["composite"], reverse=True)
    best = scored[:top_n]

    if not best or not ANTHROPIC_KEY:
        return best

    # Build Claude prompt for detailed explanation
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    macro_ctx = ""
    if macro and macro.get("_source") not in ("unavailable", "fallback", None):
        try:
            from ep_macro_context import macro_to_prompt_context
            macro_ctx = macro_to_prompt_context(macro)
        except:
            pass

    candidates_data = []
    for b in best:
        t    = b["ticker"]
        r    = b["raw"]
        a    = b["analysis"]
        f    = b["fund"]
        mag  = b["magna"]
        candidates_data.append({
            "ticker":       t,
            "ep_score":     b["ep_score"],
            "composite":    b["composite"],
            "ep_type":      a.get("ep_type") or mag.get("ep_type", ""),
            "gap_pct":      r.get("gap_pct"),
            "vol_ratio":    r.get("vol_ratio"),
            "entry_window": a.get("entry_window"),
            "thesis":       a.get("thesis", ""),
            "catalyst":     a.get("catalyst_type", ""),
            "catalyst_detail": a.get("catalyst_detail", ""),
            "red_flags":    a.get("red_flags"),
            "earnings_pct": round((f.get("earnings_growth") or 0) * 100),
            "revenue_pct":  round((f.get("revenue_growth") or 0) * 100),
            "float_M":      round((f.get("float_shares") or 0) / 1e6, 1),
            "market_cap":   f.get("market_cap"),
            "sector":       f.get("sector", ""),
            "neglect":      mag.get("neglect", {}).get("label", ""),
            "magna_breakdown": {k: v.get("score",0) for k,v in mag.get("breakdown",{}).items()},
        })

    prompt = f"""És um coach de trading especializado em Episodic Pivots (Pradeep Bonde).

{macro_ctx}

Estes são os melhores candidatos EP detectados hoje pelo scanner:
{json.dumps(candidates_data, indent=2, ensure_ascii=False)}

Para CADA candidato, explica em português europeu (tom directo, educativo, como um mentor):

1. PORQUÊ é o melhor setup de hoje — quais os critérios mais fortes
2. O que torna este setup especialmente interessante (ou preocupante)  
3. Como o contexto macro afecta este candidato especificamente
4. Plano concreto: entrada, stop, o que vigiar
5. Nível de confiança (Alto/Médio/Baixo) e porquê

Responde APENAS com JSON:
[{{
  "ticker": "XXXX",
  "rank": 1,
  "confidence": "Alto|Médio|Baixo",
  "why_best": "explicação detalhada em 3-4 frases de porque é o melhor setup",
  "strengths": ["ponto forte 1", "ponto forte 2", "ponto forte 3"],
  "concerns": ["preocupação 1"],
  "macro_alignment": "como o contexto macro afecta este candidato",
  "action_plan": "entrada: X · stop: Y · vigiar: Z",
  "one_liner": "resumo em 1 frase que um trader principiante consegue entender"
}}]"""

    try:
        time.sleep(15)  # avoid rate limit
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        explanations = parse_json_response(msg.content[0].text)
        exp_lk = {e["ticker"]: e for e in explanations}
        for b in best:
            b["explanation"] = exp_lk.get(b["ticker"], {})
    except Exception as e:
        st.warning(f"Explicação do melhor candidato falhou: {e}")

    return best


def render_best_candidates(best: list, magna_scores: dict, ep_fundamentals: dict):
    """Render the best candidates section with detailed explanation."""
    if not best:
        return

    st.markdown("## 🏆 Melhor Setup do Dia")
    st.caption("Selecção automática baseada em score composto: MAGNA + tipo EP + janela de entrada + gap + volume")

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

                if exp.get("macro_alignment"):
                    st.markdown("")
                    st.markdown("**🌍 Alinhamento macro:**")
                    st.markdown(
                        f'<div style="background:#1e3a5f20;border-left:3px solid #3b82f6;'
                        f'padding:10px 14px;border-radius:4px">{exp["macro_alignment"]}</div>',
                        unsafe_allow_html=True
                    )

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
    a = analysis or {}
    ticker    = raw["ticker"]
    in_wl     = ticker in wl_tickers
    ep_type   = magna.get("ep_type", "STANDARD") if magna else a.get("ep_type", "STANDARD")
    ep_score  = a.get("ep_score") or magna.get("total", 0) if magna else 0
    window    = a.get("entry_window", "PRIME")
    neglect   = magna.get("neglect", {}) if magna else {}

    # KB influence metadata
    kb_applied    = magna.get("kb_applied", False) if magna else False
    kb_notes      = magna.get("kb_notes") if magna else None
    raw_total     = magna.get("raw_total", ep_score) if magna else ep_score
    kb_multiplier = magna.get("kb_multiplier", 1.0) if magna else 1.0

    # KB badge for header
    kb_badge = " 🧠" if kb_applied else ""

    header = (
        f"**{ticker}**"
        f"  ·  {a.get('company_name', '')}"
        f"  |  Gap +{raw['gap_pct']}%"
        f"  |  Vol {raw['vol_ratio']}×"
        f"  |  MAGNA: {ep_score}/100{kb_badge}"
    )

    with st.expander(header, expanded=False):
        c1, c2, c3, c4 = st.columns([1, 2, 2, 1])

        with c1:
            # Show raw vs KB-adjusted score
            if kb_applied and raw_total != ep_score:
                st.markdown(
                    f"<div class='{score_class(ep_score)}'>{ep_score}</div>"
                    f"<div style='color:#667a99;font-size:0.65em;font-family:Space Mono'>"
                    f"MAGNA+KB</div>"
                    f"<div style='color:#667a99;font-size:0.65em'>"
                    f"base:{raw_total} ×{kb_multiplier}</div>",
                    unsafe_allow_html=True
                )
            else:
                st.markdown(
                    f"<div class='{score_class(ep_score)}'>{ep_score}</div>"
                    f"<div style='color:#667a99;font-size:0.72em;font-family:Space Mono'>EP SCORE</div>",
                    unsafe_allow_html=True
                )
            st.markdown(ep_type_tag(ep_type), unsafe_allow_html=True)
            # KB badge
            if kb_applied:
                st.markdown(
                    '<span class="tag" style="background:#a78bfa15;color:#a78bfa;'
                    'border:1px solid #a78bfa30;font-size:0.65em">🧠 KB</span>',
                    unsafe_allow_html=True
                )

        with c2:
            st.markdown(
                f"<div class='metric-box'>"
                f"<div class='metric-label'>Preço / Gap / Vol</div>"
                f"<div class='metric-value'>${raw['price']} · +{raw['gap_pct']}% · {raw['vol_ratio']}×</div>"
                f"</div>",
                unsafe_allow_html=True
            )
            if fund:
                eg = fund.get("earnings_growth")
                rg = fund.get("revenue_growth")
                eg_str = f"+{eg*100:.0f}%" if eg else "—"
                rg_str = f"+{rg*100:.0f}%" if rg else "—"
                eg_cls = "metric-good" if eg and eg >= 1.0 else ("metric-warn" if eg else "")
                rg_cls = "metric-good" if rg and rg >= 0.39 else ("metric-warn" if rg else "")
                st.markdown(
                    f"<div class='metric-box'>"
                    f"<div class='metric-label'>Earnings QoQ · Revenue YoY</div>"
                    f"<div class='metric-value'>"
                    f"<span class='{eg_cls}'>{eg_str}</span>"
                    f" · "
                    f"<span class='{rg_cls}'>{rg_str}</span>"
                    f"</div></div>",
                    unsafe_allow_html=True
                )

        with c3:
            st.markdown(
                f"<div class='metric-box'>"
                f"<div class='metric-label'>Entry · Stop · Risk</div>"
                f"<div class='metric-value'>"
                f"<span class='{window_class(window)}'>{window}</span>"
                f" · -{a.get('stop_loss_pct', 8)}% · {a.get('risk_level', '—')}"
                f"</div></div>",
                unsafe_allow_html=True
            )
            if fund:
                fl = fund.get("float_shares")
                mc = fund.get("market_cap")
                sr = fund.get("short_ratio")
                fl_cls = "metric-good" if fl and fl < 25e6 else ""
                mc_cls = "metric-good" if mc and mc < 10e9 else "metric-warn"
                sr_cls = "metric-good" if sr and sr >= 5 else ""
                st.markdown(
                    f"<div class='metric-box'>"
                    f"<div class='metric-label'>Float · Market Cap · Short</div>"
                    f"<div class='metric-value'>"
                    f"<span class='{fl_cls}'>{fmt_shares(fl)}</span>"
                    f" · <span class='{mc_cls}'>{fmt_large(mc)}</span>"
                    f" · <span class='{sr_cls}'>{f'{sr:.1f}d' if sr else chr(8212)}</span>"
                    f"</div></div>",
                    unsafe_allow_html=True
                )

        with c4:
            # Neglect badge
            nl_score = neglect.get("score", 0)
            nl_cls = "metric-good" if nl_score >= 70 else ("metric-warn" if nl_score >= 40 else "metric-bad")
            st.markdown(
                f"<div class='metric-box'>"
                f"<div class='metric-label'>Neglect</div>"
                f"<div class='metric-value'><span class='{nl_cls}'>{neglect.get('label', '—')}</span></div>"
                f"<div style='font-size:0.75em;color:#667a99'>{neglect.get('pre_rally_pct', '—')}% pré-EP</div>"
                f"</div>",
                unsafe_allow_html=True
            )
            if in_wl:
                if st.button("★ Remove", key=f"rm_ep_{ticker}"):
                    remove_from_watchlist(ticker); st.rerun()
            else:
                if st.button("☆ Watch", key=f"add_ep_{ticker}"):
                    add_to_watchlist({
                        "ticker": ticker, "type": "EP", "score": ep_score,
                        "price": raw["price"], "gap_pct": raw["gap_pct"],
                        "ep_type": ep_type,
                        "catalyst": a.get("catalyst_type", "—"),
                        "thesis": a.get("thesis", ""),
                        "entry_window": window,
                        "neglect": neglect.get("label", "")
                    })
                    st.rerun()

        # Thesis + catalyst
        if a.get("catalyst_type"):
            st.markdown(
                f'<span class="tag tag-ep">{a.get("catalyst_type")}</span> '
                f'{a.get("catalyst_detail", "")}',
                unsafe_allow_html=True
            )
        if a.get("thesis"):
            st.info(a["thesis"])
        if a.get("red_flags"):
            st.warning(f"⚠️ {a['red_flags']}")

        # KB card context
        _kb_adj = st.session_state.get("kb_adj", {})
        if _kb_adj.get("data_available"):
            rec_gap = _kb_adj.get("min_gap_recommended")
            rec_vol = _kb_adj.get("min_vol_ratio_recommended")
            gap_pct = raw.get("gap_pct", 0)
            vol_r   = raw.get("vol_ratio", 0)
            _kb_notes = []
            if rec_gap and gap_pct < rec_gap:
                _kb_notes.append(f"gap {gap_pct:.0f}% abaixo do recomendado ({rec_gap}%)")
            if rec_vol and vol_r < rec_vol:
                _kb_notes.append(f"vol {vol_r:.1f}× abaixo do recomendado ({rec_vol}×)")
            if raw.get("ticker") in _kb_adj.get("avoided_tickers", []):
                _kb_notes.append("ticker em blacklist da KB")
            if not _kb_notes:
                _kb_notes.append(f"gap e vol dentro dos parâmetros recomendados pela KB")
            st.markdown(
                f'<div style="background:#a78bfa10;border-left:2px solid #a78bfa33;'
                f'padding:6px 12px;border-radius:4px;margin:6px 0;font-size:0.82em">'
                f'<span style="color:#a78bfa">🧠 KB diz:</span> '
                f'<span style="color:#c9d1e0">{" · ".join(_kb_notes)}</span></div>',
                unsafe_allow_html=True
            )

        # Educational section
        edu_note  = a.get("educational_note")
        key_lesson = a.get("key_lesson")
        if edu_note or key_lesson:
            with st.expander("📚 Aprender com este setup", expanded=False):
                if edu_note:
                    st.markdown("**O que está a acontecer aqui:**")
                    st.markdown(edu_note)
                if key_lesson:
                    st.markdown("")
                    st.markdown(
                        f'<div style="background:#00e87a10;border-left:3px solid #00e87a;'
                        f'padding:10px 14px;border-radius:4px;margin-top:8px;">'
                        f'<span style="color:#00e87a;font-weight:600;font-size:0.85em">💡 LIÇÃO</span><br>'
                        f'<span style="color:#c9d1e0">{key_lesson}</span></div>',
                        unsafe_allow_html=True
                    )

        # MAGNA breakdown (collapsible)
        if magna and magna.get("breakdown"):
            with st.expander("📊 MAGNA 53 breakdown", expanded=False):
                # KB influence summary
                if magna.get("kb_applied") and magna.get("kb_notes"):
                    st.markdown(
                        f'<span class="tag" style="background:#a78bfa15;color:#a78bfa;'
                        f'border:1px solid #a78bfa30">🧠 KB ajustes: {magna["kb_notes"]}</span>',
                        unsafe_allow_html=True
                    )
                    raw_t = magna.get("raw_total", magna["total"])
                    fin_t = magna["total"]
                    if raw_t != fin_t:
                        st.caption(f"Score base: {raw_t} → ajustado: {fin_t}")
                render_magna_breakdown(magna)


def render_canslim_card(raw, a, wl_tickers):
    a = a or {}
    score  = a.get("canslim_score", "—")
    ticker = raw["ticker"]
    in_wl  = ticker in wl_tickers

    with st.expander(
        f"**{ticker}** · {a.get('company_name', ticker)}  |  +{raw['change_pct']}%  |  Vol {raw['vol_ratio']}×  |  Grade: {a.get('grade','—')}",
        expanded=False
    ):
        c1, c2, c3, c4 = st.columns([1, 2, 2, 1])
        with c1:
            st.markdown(f"<div class='{score_class(score)}'>{score}</div>CANSLIM", unsafe_allow_html=True)
            if a.get("sector"): st.caption(a["sector"])
        with c2:
            st.markdown(f"**Price:** ${raw['price']}<br>**Change:** +{raw['change_pct']}%<br>**Vol:** {raw['vol_ratio']}×", unsafe_allow_html=True)
        with c3:
            st.markdown(f"**Risk:** {a.get('risk_level','—')}<br>**Stop:** -{a.get('stop_loss_pct','—')}%<br>**Inst:** {a.get('I_institutional','—')}", unsafe_allow_html=True)
        with c4:
            if in_wl:
                if st.button("★ Remove", key=f"rm_cs_{ticker}"):
                    remove_from_watchlist(ticker); st.rerun()
            else:
                if st.button("☆ Watch", key=f"add_cs_{ticker}"):
                    add_to_watchlist({"ticker": ticker, "type": "CANSLIM", "score": score,
                                      "price": raw["price"], "change_pct": raw["change_pct"],
                                      "grade": a.get("grade", "—"), "thesis": a.get("thesis", "")})
                    st.rerun()
        cols = st.columns(4)
        for i, (label, key) in enumerate([
            ("C – Earnings Q", "C_earnings_growth"), ("A – Annual", "A_annual_earnings"),
            ("N – Catalyst", "N_new_catalyst"), ("L – Leader", "L_leader")
        ]):
            with cols[i]:
                st.markdown(f"**{label}**<br>{a.get(key, '—')}", unsafe_allow_html=True)
        if a.get("thesis"): st.info(a["thesis"])


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
    ' &nbsp; MAGNA 53 · CAP 10×10 · Neglect Detection · Polygon + yfinance + Claude',
    unsafe_allow_html=True
)
st.divider()

with st.sidebar:
    st.markdown("### ⚙️ Configuração")
    scan_type = st.radio("Tipo de scan:", ["EP (Episodic Pivot)", "CANSLIM", "Ambos"])
    st.divider()
    st.markdown("### 🎚️ Filtros EP")
    min_gap      = st.slider("Gap mínimo (%)", 3, 25, 8)
    min_vol_ep   = st.number_input("Volume mínimo EP", value=300_000, step=50_000)
    min_price_ep = st.slider("Preço mínimo EP ($)", 5, 30, 8)
    st.markdown("### 🎚️ Filtros CANSLIM")
    min_change   = st.slider("Variação mínima (%)", 1, 10, 2)
    min_price_cs = st.slider("Preço mínimo CANSLIM ($)", 10, 100, 15)
    min_vol_cs   = st.number_input("Volume mínimo CANSLIM", value=300_000, step=50_000)
    st.divider()

    # MAGNA reference card
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

    # KB status
    st.divider()
    st.markdown("### 🧠 Knowledge Base")
    _kb = load_kb_adjustments()
    if _kb.get("data_available"):
        _n  = _kb.get("total_trades_in_kb", 0)
        _gr = _kb.get("min_gap_recommended")
        _vr = _kb.get("min_vol_ratio_recommended")
        _av = len(_kb.get("avoided_tickers", []))
        st.markdown(
            f'<span style="color:#00e87a;font-family:Space Mono;font-size:.8em">'
            f'● ACTIVA · {_n} trades</span>',
            unsafe_allow_html=True
        )
        if _gr: st.caption(f"Gap rec: ≥{_gr}%")
        if _vr: st.caption(f"Vol rec: ≥{_vr}×")
        if _av: st.caption(f"Blacklist: {_av} tickers")
    else:
        st.markdown(
            '<span style="color:#667a99;font-family:Space Mono;font-size:.8em">'
            '○ SEM DADOS · corre backtest</span>',
            unsafe_allow_html=True
        )
    st.divider()
    st.markdown("### 🌍 Macro Context")
    use_macro = st.toggle("Análise macro (web search)", value=True, key="use_macro_toggle")
    if use_macro:
        st.caption("Pesquisa contexto actual antes do scan")
    else:
        st.caption("Desactivado — evita rate limit")

    st.caption(f"📌 Watchlist: {len(load_watchlist())} tickers")

tab_scan, tab_watchlist, tab_kb_learn = st.tabs(["🔍 Scanner", "📌 Watchlist", "🧠 O que aprendi"])

with tab_scan:
    today_str = last_trading_day(1)
    prev_str  = last_trading_day(2)
    st.caption(f"📅 Sessão: **{today_str}** · Referência: {prev_str} · Polygon EOD + yfinance fundamentais")

    if st.button("▶ RUN SCAN", type="primary"):
        if not POLYGON_KEY or not ANTHROPIC_KEY:
            st.error("Chaves API em falta. Verifica o .env"); st.stop()

        # ── 1. Dados de mercado ──────────────────────────────────────────────
        with st.spinner("A carregar dados de mercado (Polygon)..."):
            today_data = polygon_grouped(today_str)
            time.sleep(12)
            prev_data  = polygon_grouped(prev_str)

        if not today_data:
            st.error(f"Sem dados para {today_str}."); st.stop()

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

        # Pausa para evitar rate limit após o macro context (30k tokens/min)
        if st.session_state.get("macro_context") and st.session_state.get("use_macro_toggle", True):
            time.sleep(30)

        if ep_raw:
            with st.spinner(f"Claude a analisar {min(len(ep_raw),5)} candidatos EP (MAGNA 53)..."):
                ep_analysis = claude_analyze_ep(ep_raw[:5], ep_fundamentals, magna_scores)

        if canslim_raw:
            time.sleep(15)  # pausa entre EP e CANSLIM
            with st.spinner(f"Claude a analisar {min(len(canslim_raw),5)} candidatos CANSLIM..."):
                canslim_analysis = claude_analyze_canslim(canslim_raw[:5])

        # ── Save to session state ────────────────────────────────────────────
        st.session_state.ep_raw           = ep_raw
        st.session_state.canslim_raw      = canslim_raw
        st.session_state.ep_analysis      = ep_analysis
        st.session_state.canslim_analysis = canslim_analysis
        st.session_state.ep_fundamentals  = ep_fundamentals
        st.session_state.ep_magna         = magna_scores
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

            # ── Macro context panel ────────────────────────────────────────────────────────────────
            macro = st.session_state.get("macro_context")
            if macro is None and st.session_state.get("use_macro_toggle", True):
                sectors = list({f.get("sector","") for f in ep_fundamentals.values() if f.get("sector")})
                with st.spinner("🌍 A obter contexto macro actual..."):
                    macro = load_macro_context(sectors[:5])
                st.session_state["macro_context"] = macro

            if macro and macro.get("_source") not in ("unavailable", None):
                try:
                    from ep_macro_context import format_macro_streamlit
                    m = format_macro_streamlit(macro)
                    sentiment  = m["sentiment"]
                    impact     = m["impact"]
                    sent_color = {"RISK_ON": "#00e87a", "RISK_OFF": "#ff5e5e", "NEUTRAL": "#f5c842"}.get(sentiment, "#667a99")
                    imp_color  = {"FAVÓRÁVEL": "#00e87a", "DESFAVORÁVEL": "#ff5e5e", "NEUTRO": "#f5c842"}.get(impact, "#667a99")
                    with st.expander("🌍 Contexto Macroeconómico Actual", expanded=True):
                        mc1, mc2, mc3, mc4 = st.columns(4)
                        mc1.markdown(f"<div style='color:{sent_color};font-weight:600'>{sentiment}</div><div style='color:#667a99;font-size:0.75em'>SENTIMENTO</div>", unsafe_allow_html=True)
                        mc2.markdown(f"<div style='color:{imp_color};font-weight:600'>{impact}</div><div style='color:#667a99;font-size:0.75em'>IMPACTO EPs</div>", unsafe_allow_html=True)
                        mc3.markdown(f"<div style='color:#c9d1e0;font-weight:600'>{m['trend']}</div><div style='color:#667a99;font-size:0.75em'>MERCADO</div>", unsafe_allow_html=True)
                        mc4.markdown(f"<div style='color:#c9d1e0;font-weight:600'>{m['fed_stance']}</div><div style='color:#667a99;font-size:0.75em'>FED</div>", unsafe_allow_html=True)
                        if m.get("summary"):
                            st.markdown(f"_{m['summary']}_")
                        if m.get("caution"):
                            st.warning(f"⚠️ {m['caution']}")
                        if m.get("geo_factors"):
                            st.caption("Factores geopolíticos: " + " · ".join(m["geo_factors"]))
                except Exception as e:
                    st.caption(f"Macro: {e}")

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
                macro_ctx = st.session_state.get("macro_context")
                with st.spinner("🏆 A seleccionar e analisar o melhor setup..."):
                    best = pick_best_candidates(
                        ep_raw, ep_analysis, magna_scores,
                        ep_fundamentals, macro_ctx, top_n=2
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
                merged = sorted(
                    merge(canslim_raw, canslim_analysis),
                    key=lambda x: x[1].get("canslim_score", 0), reverse=True
                )
                for raw, analysis in merged[:10]:
                    render_canslim_card(raw, analysis, wl_tickers)
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


if __name__ == "__main__":
    pass