"""
EP Scanner Headless
===================
Versão sem Streamlit do scanner EP — para GitHub Actions e automação.

Corre: python ep_scanner_headless.py
Devolve: lista de candidatos EP com MAGNA score + análise Claude
"""

import os
import json
import time
import re
import requests
import yfinance as yf
from datetime import date, timedelta, datetime
from dotenv import load_dotenv

load_dotenv()

POLYGON_KEY   = os.getenv("POLYGON_API_KEY")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def last_n_trading_days(n: int) -> list:
    """Return last N trading day strings, most recent first."""
    days = []
    d = date.today()
    while len(days) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            days.append(d.strftime("%Y-%m-%d"))
    return days

def is_clean_ticker(ticker: str) -> bool:
    if len(ticker) > 5: return False
    if any(c in ticker for c in ['.', '-', '+', '/', ' ']): return False
    if ticker.endswith(('W', 'R', 'U', 'P')) and len(ticker) > 4: return False
    return True

def fmt_large(n):
    if n is None: return "—"
    if n >= 1e9:  return f"${n/1e9:.1f}B"
    if n >= 1e6:  return f"${n/1e6:.0f}M"
    return f"${n:,.0f}"

def fmt_shares(n):
    if n is None: return "—"
    if n >= 1e6:  return f"{n/1e6:.1f}M"
    return f"{n:,.0f}"


# ─── POLYGON ──────────────────────────────────────────────────────────────────

def fetch_grouped(date_str: str) -> dict:
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
    r = requests.get(url, params={"adjusted": "true", "apiKey": POLYGON_KEY}, timeout=30)
    if r.status_code != 200 or not r.json().get("results"):
        return {}
    return {item["T"]: item for item in r.json()["results"]}

def fetch_history(ticker: str, days: int = 75) -> list:
    end   = date.today().strftime("%Y-%m-%d")
    start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    url   = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
    r = requests.get(url, params={"adjusted": "true", "limit": 100, "apiKey": POLYGON_KEY}, timeout=15)
    if r.status_code != 200 or not r.json().get("results"):
        return []
    return r.json()["results"]


# ─── YFINANCE FUNDAMENTALS ────────────────────────────────────────────────────

def fetch_fundamentals(ticker: str) -> dict:
    try:
        info = yf.Ticker(ticker).info or {}
        ipo_year = None
        try:
            hist = yf.Ticker(ticker).history(period="max", auto_adjust=False)
            if not hist.empty:
                ipo_year = hist.index[0].year
        except:
            pass
        return {
            "earnings_growth": info.get("earningsQuarterlyGrowth"),
            "revenue_growth":  info.get("revenueGrowth"),
            "float_shares":    info.get("floatShares"),
            "market_cap":      info.get("marketCap"),
            "short_ratio":     info.get("shortRatio"),
            "inst_pct":        info.get("heldPercentInstitutions"),
            "analyst_count":   info.get("numberOfAnalystOpinions"),
            "recommendation":  info.get("recommendationKey"),
            "sector":          info.get("sector", ""),
            "industry":        info.get("industry", ""),
            "ipo_year":        ipo_year,
        }
    except:
        return {}


# ─── NEGLECT DETECTION ────────────────────────────────────────────────────────

def detect_neglect(history: list) -> dict:
    if not history or len(history) < 10:
        return {"score": 50, "label": "Sem dados", "pre_rally_pct": 0}

    closes  = [b["c"] for b in history]
    volumes = [b["v"] for b in history]
    pre_ep  = closes[:-1]

    if not pre_ep:
        return {"score": 50, "label": "Sem dados", "pre_rally_pct": 0}

    pre_rally_pct = (pre_ep[-1] - pre_ep[0]) / pre_ep[0] * 100
    range_pct     = (max(pre_ep) - min(pre_ep)) / min(pre_ep) * 100
    avg_vol       = sum(volumes[:-1]) / max(len(volumes) - 1, 1)

    score = 0
    if pre_rally_pct <= -10: score += 40
    elif pre_rally_pct <= 5: score += 30
    elif pre_rally_pct <= 20: score += 15

    if range_pct < 15:   score += 30
    elif range_pct < 25: score += 20
    elif range_pct < 40: score += 10

    if avg_vol < 200_000:   score += 30
    elif avg_vol < 500_000: score += 20
    elif avg_vol < 1_000_000: score += 10

    score = min(score, 100)
    label = "Alta negligência" if score >= 70 else "Moderada" if score >= 40 else "Sem negligência"

    return {"score": score, "label": label, "pre_rally_pct": round(pre_rally_pct, 1)}


# ─── MAGNA 53 SCORE ───────────────────────────────────────────────────────────

def magna53_score(raw: dict, fund: dict, neglect: dict) -> dict:
    total = 0
    gap_pct   = raw.get("gap_pct", 0)
    vol_ratio = raw.get("vol_ratio", 1)
    eg = fund.get("earnings_growth")
    rg = fund.get("revenue_growth")

    # MA
    ma = 0
    if eg is not None:
        ep = eg * 100
        if ep >= 200: ma += 20
        elif ep >= 100: ma += 15
        elif ep >= 40:  ma += 8
    if rg is not None:
        rp = rg * 100
        if rp >= 100: ma += 15
        elif rp >= 39: ma += 10
        elif rp >= 10: ma += 4
    total += min(ma, 35)

    # G
    if gap_pct >= 30:    total += 20
    elif gap_pct >= 20:  total += 17
    elif gap_pct >= 10:  total += 13
    elif gap_pct >= 8:   total += 10
    elif gap_pct >= 5:   total += 6
    else:                total += 2

    # N
    total += int(neglect.get("score", 0) * 0.20)

    # A
    if rg is not None:
        rp = rg * 100
        if rp >= 39:   total += 10
        elif rp >= 15: total += 6
        elif rp >= 5:  total += 3

    # 5 (short interest)
    sr = fund.get("short_ratio")
    if sr and sr >= 5:    total += 5
    elif sr and sr >= 2:  total += 3

    # 3 (analysts)
    ac = fund.get("analyst_count") or 0
    rec = fund.get("recommendation", "")
    if ac >= 3 and rec in ("buy", "strong_buy"): total += 5
    elif ac >= 1: total += 2

    # CAP
    mc = fund.get("market_cap")
    if mc:
        if mc <= 500_000_000:      total += 5
        elif mc <= 2_000_000_000:  total += 5
        elif mc <= 10_000_000_000: total += 4
        else:                       total += 1
    else: total += 2

    # 10 (IPO age)
    ipo = fund.get("ipo_year")
    if ipo:
        age = date.today().year - ipo
        if age <= 5:    total += 5
        elif age <= 10: total += 4
        elif age <= 15: total += 2

    # Float bonus
    fl = fund.get("float_shares")
    if fl:
        if fl < 10_000_000:    total += 5
        elif fl < 25_000_000:  total += 4
        elif fl < 100_000_000: total += 2

    # EP type
    neg_score  = neglect.get("score", 0)
    pre_rally  = neglect.get("pre_rally_pct", 0)
    if eg and eg >= 1.0 and pre_rally <= -5:
        ep_type = "TURNAROUND"
    elif eg and eg >= 1.0 and rg and rg >= 0.39:
        ep_type = "GROWTH"
    elif neg_score >= 60 and gap_pct >= 15:
        ep_type = "STORY/NEGLECTED"
    elif vol_ratio >= 9:
        ep_type = "9M_EP"
    else:
        ep_type = "STANDARD"

    return {"total": min(int(total), 100), "ep_type": ep_type}


# ─── EP SCAN ──────────────────────────────────────────────────────────────────

def scan_ep(today: dict, prev: dict, min_gap=8.0, min_vol=300_000,
            min_price=8.0, min_vol_ratio=3.0) -> list:
    results = []
    for ticker, t in today.items():
        if not is_clean_ticker(ticker): continue
        if ticker not in prev: continue
        p       = prev[ticker]
        price   = t.get("c", 0)
        volume  = t.get("v", 0)
        t_open  = t.get("o", 0)
        t_low   = t.get("l", 0)
        p_close = p.get("c", 0)
        if price < min_price or volume < min_vol or p_close == 0: continue
        if price * volume < 5_000_000: continue
        gap_pct   = (t_open - p_close) / p_close * 100
        vol_ratio = volume / max(p.get("v", 1), 1)
        if gap_pct >= min_gap and vol_ratio >= min_vol_ratio:
            results.append({
                "ticker":    ticker,
                "price":     round(price, 2),
                "gap_pct":   round(gap_pct, 2),
                "vol_ratio": round(vol_ratio, 2),
                "volume":    int(volume),
                "ep_low":    round(t_low, 2),
                "prev_close":round(p_close, 2),
            })
    results.sort(key=lambda x: (x["gap_pct"] * 0.5 + x["vol_ratio"] * 0.5), reverse=True)
    return results[:15]



# ─── CANSLIM SCAN ─────────────────────────────────────────────────────────────

def scan_canslim(today: dict, prev: dict, min_change=2.0, min_vol=300_000,
                 min_price=15.0, min_vol_ratio=1.3) -> list:
    """
    CANSLIM scanner: stocks with strong price + volume on the day.
    Pure price/volume — no fundamentals needed.
    """
    results = []
    for ticker, t in today.items():
        if not is_clean_ticker(ticker): continue
        if ticker not in prev: continue
        p       = prev[ticker]
        price   = t.get("c", 0)
        volume  = t.get("v", 0)
        p_close = p.get("c", 0)
        if price < min_price or volume < min_vol or p_close == 0: continue
        change_pct = (price - p_close) / p_close * 100
        vol_ratio  = volume / max(p.get("v", 1), 1)
        if change_pct >= min_change and vol_ratio >= min_vol_ratio:
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
    return results[:10]

# ─── CLAUDE ANALYSIS ──────────────────────────────────────────────────────────

def parse_json(text: str) -> list:
    cleaned = text.replace("```json", "").replace("```", "").strip()
    m = re.search(r'\[[\s\S]*\]', cleaned)
    if m:
        try:    return json.loads(m.group(0))
        except: pass
    return []

def claude_analyze(candidates: list, fundamentals: dict, magna_scores: dict) -> list:
    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    contexts = []
    for c in candidates:
        t    = c["ticker"]
        f    = fundamentals.get(t, {})
        mag  = magna_scores.get(t, {"total": 0, "ep_type": "STANDARD"})
        eg   = f.get("earnings_growth")
        rg   = f.get("revenue_growth")
        contexts.append({
            "ticker":      t,
            "price":       c["price"],
            "gap_pct":     c["gap_pct"],
            "vol_ratio":   c["vol_ratio"],
            "magna_score": mag["total"],
            "ep_type":     mag["ep_type"],
            "earnings_growth_pct": round((eg or 0) * 100),
            "revenue_growth_pct":  round((rg or 0) * 100),
            "float_M":     round((f.get("float_shares") or 0) / 1e6, 1),
            "market_cap":  fmt_large(f.get("market_cap")),
            "short_ratio": f.get("short_ratio"),
            "sector":      f.get("sector", ""),
            "ipo_year":    f.get("ipo_year"),
        })

    tickers = [c["ticker"] for c in candidates]
    prompt = f"""You are an Episodic Pivot expert (Pradeep Bonde / Stockbee methodology).

Analyse these EP candidates and classify each:
{json.dumps(contexts, indent=2)}

MAGNA 53 criteria: MA=massive acceleration earnings/sales, G=gap up surprise,
N=neglect (sideways/down 2-6m, <100 funds), A=sales acceleration 39%+,
5=short interest 5+ days, 3=3+ analysts raising targets,
CAP=market cap <$10B, 10=IPO <10 years.

EP types: GROWTH (sustained 39%+ sales), TURNAROUND (years of decline reversed → biggest moves),
STORY (narrative-driven, AI/biotech/space), 9M_EP (volume spike 9M+ shares).

For ALL {len(tickers)} tickers return ONLY raw JSON array:
[{{"ticker":"X","ep_score":75,"ep_type":"TURNAROUND","catalyst_type":"Earnings Surprise",
"catalyst_detail":"specific event","entry_window":"PRIME","company_name":"Full Name",
"thesis":"one sentence why","risk_level":"Medium","stop_loss_pct":8,"red_flags":null}}]

entry_window: PRIME=today/tomorrow, OPEN=2-5 days, LATE=older.
Be critical. ep_score 0-100."""

    msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return parse_json(msg.content[0].text)


# ─── MAIN RUNNER ──────────────────────────────────────────────────────────────

def run_scan(min_gap=8.0, min_vol=300_000, min_price=8.0, min_vol_ratio=3.0,
             max_candidates=8, use_claude=True,
             run_canslim=True, min_change_cs=2.0, min_price_cs=15.0) -> dict:
    """
    Full EP scan pipeline. Returns dict with candidates and metadata.
    """
    trading_days = last_n_trading_days(2)
    session_date = trading_days[0]
    prev_date    = trading_days[1]

    print(f"[EP Scanner] Sessão: {session_date} · Referência: {prev_date}")

    # 1. Market data
    print("[1/4] A carregar dados Polygon...")
    today_data = fetch_grouped(session_date)
    time.sleep(13)  # rate limit 5/min
    prev_data  = fetch_grouped(prev_date)

    if not today_data:
        return {"error": f"Sem dados Polygon para {session_date}", "candidates": []}

    print(f"      {len(today_data):,} tickers carregados")

    # 2. EP scan
    print("[2/4] A aplicar filtros EP...")
    raw_candidates = scan_ep(today_data, prev_data, min_gap, min_vol, min_price, min_vol_ratio)
    print(f"      {len(raw_candidates)} candidatos detectados")

    # CANSLIM scan (runs regardless of EP results)
    canslim_raw = []
    if run_canslim:
        canslim_raw = scan_canslim(today_data, prev_data, min_change_cs, min_vol, min_price_cs)
        print(f"      CANSLIM: {len(canslim_raw)} candidatos")

    if not raw_candidates and not canslim_raw:
        return {
            "session_date": session_date,
            "candidates": [],
            "canslim": [],
            "n_universe": len(today_data),
        }

    # 3. Fundamentals + MAGNA score
    print("[3/4] A buscar fundamentais (yfinance) + MAGNA score...")
    fundamentals = {}
    magna_scores = {}

    for i, r in enumerate(raw_candidates[:max_candidates]):
        t = r["ticker"]
        print(f"      {t} ({i+1}/{min(len(raw_candidates), max_candidates)})")
        fundamentals[t] = fetch_fundamentals(t)

        history  = fetch_history(t, days=75)
        time.sleep(0.15)
        neglect  = detect_neglect(history)
        magna_scores[t] = magna53_score(r, fundamentals[t], neglect)
        r["neglect"] = neglect
        time.sleep(0.3)

    # Re-sort by MAGNA score
    top = raw_candidates[:max_candidates]
    top.sort(key=lambda r: magna_scores.get(r["ticker"], {}).get("total", 0), reverse=True)

    # 4. Claude analysis
    analysis_map = {}
    if use_claude and ANTHROPIC_KEY and top:
        print("[4/4] A analisar com Claude...")
        try:
            analysis = claude_analyze(top, fundamentals, magna_scores)
            analysis_map = {a["ticker"]: a for a in analysis}
        except Exception as e:
            print(f"      Claude falhou: {e}")

    # Build final candidates
    final = []
    for r in top:
        t    = r["ticker"]
        a    = analysis_map.get(t, {})
        mag  = magna_scores.get(t, {"total": 0, "ep_type": "STANDARD"})
        fund = fundamentals.get(t, {})

        # ── Stop-loss ancorado ao pivot EP (Pradeep Bonde) ────────────────────
        # Stop = mínima do dia do EP (ep_low)
        # Lógica: se o preço fechar abaixo da mínima do gap day, o sinal falhou
        # Fallback: prev_close (fecho pré-EP) para gaps muito grandes
        ep_low    = r.get("ep_low", 0)
        prev_close = r.get("prev_close", 0)
        price     = r["price"]

        # Stop primário: mínima do dia do gap
        # Stop secundário: prev_close (âncora original do pivot)
        stop_price = ep_low if ep_low > 0 else prev_close
        stop_pct_from_price = round((price - stop_price) / price * 100, 1) if stop_price > 0 else a.get("stop_loss_pct", 8)

        # Detecção biotech: sector Healthcare sem earnings recorrentes
        sector = fund.get("sector", "")
        earnings_pct = round((fund.get("earnings_growth") or 0) * 100)
        revenue_pct  = round((fund.get("revenue_growth") or 0) * 100)
        is_biotech_speculative = (
            sector in ("Healthcare", "Biotechnology") and
            earnings_pct == 0 and revenue_pct < 0
        )

        final.append({
            "ticker":          t,
            "price":           price,
            "gap_pct":         r["gap_pct"],
            "vol_ratio":       r["vol_ratio"],
            "volume":          r["volume"],
            "magna_score":     a.get("ep_score") or mag["total"],
            "ep_type":         a.get("ep_type") or mag["ep_type"],
            "catalyst":        a.get("catalyst_type", "—"),
            "catalyst_detail": a.get("catalyst_detail", ""),
            "entry_window":    a.get("entry_window", "PRIME"),
            "thesis":          a.get("thesis", ""),
            "red_flags":       a.get("red_flags"),
            "risk_level":      a.get("risk_level", "—"),
            # Stop-loss ancorado ao EP pivot (não percentagem arbitrária)
            "stop_price":      round(stop_price, 2),
            "stop_pct":        stop_pct_from_price,
            "prev_close":      round(prev_close, 2),
            "ep_low":          round(ep_low, 2),
            "float_M":         round((fund.get("float_shares") or 0) / 1e6, 1),
            "market_cap":      fmt_large(fund.get("market_cap")),
            "earnings_pct":    earnings_pct,
            "revenue_pct":     revenue_pct,
            "neglect_label":   r.get("neglect", {}).get("label", "—"),
            "sector":          sector,
            "is_biotech_spec": is_biotech_speculative,
        })

    # Build CANSLIM final list
    canslim_final = []
    for r in canslim_raw[:8]:
        canslim_final.append({
            "ticker":     r["ticker"],
            "price":      r["price"],
            "change_pct": r["change_pct"],
            "vol_ratio":  r["vol_ratio"],
            "volume":     r["volume"],
            "score":      r["score"],
        })

    print(f"\n✅ Scan completo — {len(final)} EP · {len(canslim_final)} CANSLIM")
    return {
        "session_date": session_date,
        "prev_date":    prev_date,
        "n_universe":   len(today_data),
        "candidates":   final,
        "canslim":      canslim_final,
    }


if __name__ == "__main__":
    result = run_scan()
    print(json.dumps(result, indent=2, ensure_ascii=False))
