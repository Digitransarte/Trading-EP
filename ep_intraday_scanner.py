"""
EP Intraday Scanner
===================
Scan das 17h Lisboa — detecta EPs do DIA ACTUAL via yfinance (delay 15min).
Candidatos têm janela PRIME — ainda há tempo de agir no mesmo dia.

Fluxo:
  1. Carrega ep_prev_closes.json (gerado pelo scan da manhã)
  2. Filtra tickers activos (volume > 500k ontem)
  3. yfinance.download() em batches de 100 tickers
  4. Detecta gaps do dia actual vs fecho de ontem
  5. Calcula MAGNA score + envia para Telegram

Uso:
  python ep_intraday_scanner.py              # scan + Telegram
  python ep_intraday_scanner.py --dry-run    # só imprime, não envia
  python ep_intraday_scanner.py --min-gap 15 # gap mínimo diferente
"""

import os
import json
import time
import argparse
import requests
import warnings
import logging
import yfinance as yf
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

# Suprimir warnings do yfinance (tickers delisted, rate limit noise)
warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

load_dotenv()

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_KEY     = os.getenv("ANTHROPIC_API_KEY")

PREV_CLOSES_FILE  = "ep_prev_closes.json"
BATCH_SIZE        = 100    # tickers por chamada yfinance
MIN_VOLUME        = 300_000
POLYGON_INTERVAL  = 13     # rate limit Polygon free


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def is_clean_ticker(ticker: str) -> bool:
    if len(ticker) > 5: return False
    if any(c in ticker for c in ['.', '-', '+', '/', ' ']): return False
    if ticker.endswith(('W', 'R', 'U', 'P')) and len(ticker) > 4: return False
    return True

def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"[Telegram] {text[:80]}...")
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=15
    )
    return r.status_code == 200

def fmt_large(n):
    if not n: return "—"
    if n >= 1e9: return f"${n/1e9:.1f}B"
    if n >= 1e6: return f"${n/1e6:.0f}M"
    return f"${n:,.0f}"


# ─── CACHE DE CLOSES ──────────────────────────────────────────────────────────

def load_prev_closes() -> dict:
    """
    Carrega closes de ontem gerados pelo scan da manhã.
    Formato: {"AAPL": {"close": 185.2, "volume": 52000000}, ...}
    """
    if not os.path.exists(PREV_CLOSES_FILE):
        print(f"[Intraday] {PREV_CLOSES_FILE} não encontrado.")
        print("  O scan da manhã (ep_daily_runner.py) precisa de correr primeiro.")
        return {}

    try:
        with open(PREV_CLOSES_FILE, "r") as f:
            data = json.load(f)
        # Verificar se o cache é de hoje (o runner da manhã guarda a data)
        cache_date = data.get("_date", "")
        today      = date.today().strftime("%Y-%m-%d")
        if cache_date != today:
            print(f"[Intraday] Cache desactualizado ({cache_date} vs {today}).")
            print("  Corre ep_daily_runner.py primeiro para gerar dados de hoje.")
            return {}
        closes = {k: v for k, v in data.items() if not k.startswith("_")}
        print(f"[Intraday] Cache carregado: {len(closes):,} tickers de {cache_date}")
        return closes
    except Exception as e:
        print(f"[Intraday] Erro ao carregar cache: {e}")
        return {}


# ─── YFINANCE BATCH FETCH ─────────────────────────────────────────────────────

def fetch_intraday_batch(tickers: list) -> dict:
    """
    Fetcha preço actual + volume de uma lista de tickers via yfinance.
    Retorna {ticker: {"price": float, "volume": int, "open": float}}
    """
    if not tickers:
        return {}

    tickers_str = " ".join(tickers)
    try:
        data = yf.download(
            tickers_str,
            period="1d",
            interval="1m",
            progress=False,
            auto_adjust=True,
            threads=True,
        )

        if data.empty:
            return {}

        results = {}

        # yfinance multi-ticker devolve MultiIndex
        if isinstance(data.columns, object) and hasattr(data.columns, 'levels'):
            # MultiIndex: (field, ticker)
            for ticker in tickers:
                try:
                    close_series = data["Close"][ticker].dropna()
                    vol_series   = data["Volume"][ticker].dropna()
                    open_series  = data["Open"][ticker].dropna()
                    if close_series.empty: continue
                    results[ticker] = {
                        "price":  float(close_series.iloc[-1]),
                        "open":   float(open_series.iloc[0]) if not open_series.empty else 0,
                        "volume": int(vol_series.sum()),   # volume acumulado do dia
                    }
                except (KeyError, Exception):
                    continue
        else:
            # Single ticker
            ticker = tickers[0]
            if not data.empty:
                results[ticker] = {
                    "price":  float(data["Close"].iloc[-1]),
                    "open":   float(data["Open"].iloc[0]),
                    "volume": int(data["Volume"].sum()),
                }

        return results

    except Exception as e:
        print(f"[Intraday] yfinance batch erro: {e}")
        return {}


# ─── SCAN INTRADAY ────────────────────────────────────────────────────────────

def run_intraday_scan(
    min_gap: float = 8.0,
    min_vol: int   = MIN_VOLUME,
    min_vol_ratio: float = 3.0,
    min_price: float = 5.0,
    dry_run: bool  = False,
) -> dict:
    """
    Scan principal das 17h.
    Detecta EPs do dia actual com janela PRIME.
    """
    today    = date.today().strftime("%Y-%m-%d")
    now_str  = datetime.now().strftime("%H:%M")

    print(f"\n{'='*50}")
    print(f"  EP Intraday Scanner — {today} {now_str}")
    print(f"  Gap >= {min_gap}% · Vol ratio >= {min_vol_ratio}× · PRIME window")
    print(f"{'='*50}\n")

    # 1. Carregar closes de ontem
    prev_closes = load_prev_closes()
    if not prev_closes:
        return {"error": "Cache de closes não disponível", "candidates": []}

    # 2. Filtrar tickers activos (volume > 500k ontem → ~2000-3000 tickers)
    active_tickers = [
        t for t, v in prev_closes.items()
        if v.get("volume", 0) >= 500_000
        and is_clean_ticker(t)
        and v.get("close", 0) >= min_price
    ]
    print(f"[1/3] {len(active_tickers):,} tickers activos (vol >= 500k ontem)")

    # 3. Fetch intraday em batches
    print(f"[2/3] A fazer fetch yfinance em batches de {BATCH_SIZE}...")
    intraday_data = {}
    n_batches = (len(active_tickers) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(active_tickers), BATCH_SIZE):
        batch   = active_tickers[i:i + BATCH_SIZE]
        batch_n = i // BATCH_SIZE + 1
        print(f"      Batch {batch_n}/{n_batches} ({len(batch)} tickers)...", end="\r")
        result  = fetch_intraday_batch(batch)
        intraday_data.update(result)
        time.sleep(1.5)  # pausa entre batches (evitar rate limit yfinance)

    print(f"\n      {len(intraday_data):,} tickers com dados intraday")

    # 4. Detectar gaps
    print(f"[3/3] A detectar gaps do dia actual...")
    candidates = []

    for ticker, intra in intraday_data.items():
        prev = prev_closes.get(ticker, {})
        if not prev:
            continue

        prev_close = prev.get("close", 0)
        prev_vol   = prev.get("volume", 1)
        price      = intra.get("price", 0)
        today_open = intra.get("open", 0)
        today_vol  = intra.get("volume", 0)

        if prev_close <= 0 or price <= 0 or today_open <= 0:
            continue

        # Gap calculado na abertura vs fecho de ontem
        gap_pct   = (today_open - prev_close) / prev_close * 100
        vol_ratio = today_vol / max(prev_vol, 1)

        if gap_pct >= min_gap and vol_ratio >= min_vol_ratio and today_vol >= min_vol:
            candidates.append({
                "ticker":     ticker,
                "price":      round(price, 2),
                "gap_pct":    round(gap_pct, 2),
                "vol_ratio":  round(vol_ratio, 2),
                "volume":     int(today_vol),
                "prev_close": round(prev_close, 2),
                "ep_low":     round(min(today_open, price), 2),  # proxy stop
                "entry_window": "PRIME",  # gap do dia actual = entrada válida hoje
            })

    # Ordenar por gap × vol_ratio
    candidates.sort(key=lambda x: x["gap_pct"] * 0.5 + x["vol_ratio"] * 0.5, reverse=True)
    candidates = candidates[:12]

    print(f"      {len(candidates)} candidatos PRIME detectados")

    if not candidates:
        return {
            "session_date": today,
            "scan_type":    "INTRADAY_PRIME",
            "candidates":   [],
            "n_checked":    len(intraday_data),
        }

    # 5. Fundamentais + MAGNA score para top candidatos
    from ep_scanner_headless import fetch_fundamentals, detect_neglect, fetch_history, magna53_score
    from ep_forward_tracker import save_candidates

    print(f"\nA calcular MAGNA score para top {min(len(candidates), 6)}...")
    fundamentals = {}
    magna_scores = {}

    for i, r in enumerate(candidates[:6]):
        t = r["ticker"]
        print(f"  {t} ({i+1}/{min(len(candidates),6)})...")
        fundamentals[t] = fetch_fundamentals(t)
        history = fetch_history(t, days=75)
        neglect = detect_neglect(history)
        magna_scores[t] = magna53_score(r, fundamentals[t], neglect)
        r["neglect"]    = neglect
        time.sleep(POLYGON_INTERVAL)  # rate limit Polygon

    # Enriquecer candidatos com dados finais
    final = []
    for r in candidates[:6]:
        t    = r["ticker"]
        mag  = magna_scores.get(t, {"total": 0, "ep_type": "STANDARD"})
        fund = fundamentals.get(t, {})

        stop_price  = r.get("ep_low", 0)
        entry_price = r["price"]
        stop_pct    = round((entry_price - stop_price) / entry_price * 100, 1) if stop_price > 0 else 8

        final.append({
            "ticker":        t,
            "price":         entry_price,
            "gap_pct":       r["gap_pct"],
            "vol_ratio":     r["vol_ratio"],
            "volume":        r["volume"],
            "magna_score":   mag["total"],
            "ep_type":       mag["ep_type"],
            "entry_window":  "PRIME",
            "stop_price":    stop_price,
            "stop_pct":      stop_pct,
            "prev_close":    r["prev_close"],
            "float_m":       round((fund.get("float_shares") or 0) / 1e6, 1),
            "market_cap":    fmt_large(fund.get("market_cap")),
            "earnings_pct":  round((fund.get("earnings_growth") or 0) * 100),
            "revenue_pct":   round((fund.get("revenue_growth") or 0) * 100),
            "neglect_label": r.get("neglect", {}).get("label", "—"),
            "sector":        fund.get("sector", ""),
            "catalyst":      "Gap intraday",
            "thesis":        f"Gap +{r['gap_pct']:.1f}% com volume {r['vol_ratio']:.1f}x no dia actual",
        })

    # 6. Registar no Forward Tracker
    if not dry_run and final:
        saved = save_candidates(final, scan_date=today)
        print(f"[Tracker] {saved} candidatos PRIME registados")

    # 7. Notificar Telegram
    if not dry_run:
        _notify_intraday(final, today, len(intraday_data))

    return {
        "session_date": today,
        "scan_type":    "INTRADAY_PRIME",
        "candidates":   final,
        "n_checked":    len(intraday_data),
    }


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def _notify_intraday(candidates: list, today: str, n_checked: int):
    """Envia candidatos PRIME para Telegram."""
    now_str = datetime.now().strftime("%H:%M")

    if not candidates:
        send_telegram(
            f"⚡ *EP Intraday* · {today} {now_str}\n"
            f"🔭 {n_checked:,} tickers verificados\n\n"
            f"😴 Sem candidatos PRIME hoje."
        )
        return

    # Header
    send_telegram(
        f"⚡ *EP Intraday PRIME* · {today} {now_str}\n"
        f"🔭 {n_checked:,} tickers · 🟢 {len(candidates)} candidatos PRIME\n"
        f"_Janela de entrada: hoje até ao fecho (16h ET)_\n"
        f"{'─' * 28}"
    )
    time.sleep(0.5)

    # Candidatos
    for i, c in enumerate(candidates[:5], 1):
        score  = c.get("magna_score", 0)
        emoji  = "🏆" if score >= 75 else "✅" if score >= 50 else "⚠️"
        stop_p = c.get("stop_price", 0)
        stop_pct = c.get("stop_pct", 8)

        msg = (
            f"{i}. *{c['ticker']}* ⚡ `{c['ep_type']}`\n"
            f"{emoji} MAGNA: `{score}/100`\n\n"
            f"💰 Preço: `${c['price']:.2f}`\n"
            f"📊 Gap: `+{c['gap_pct']:.1f}%` · Vol: `{c['vol_ratio']:.1f}×`\n"
            f"📈 EPS: `{c.get('earnings_pct',0):+.0f}%` · Revenue: `{c.get('revenue_pct',0):+.0f}%`\n"
            f"🔢 Float: `{c.get('float_m',0):.1f}M` · Cap: `{c.get('market_cap','—')}`\n\n"
            f"🟢 Entrada: *PRIME* (hoje)\n"
            f"🛑 Stop: `${stop_p:.2f}` (-{stop_pct:.1f}%)\n"
            f"🌑 Neglect: _{c.get('neglect_label','—')}_"
        )
        send_telegram(msg)
        time.sleep(0.5)

    send_telegram(
        f"{'─' * 28}\n"
        f"_Dados com delay ~15min (yfinance). Verifica preço actual antes de entrar._\n"
        f"_Stop = mínima do gap day · Método Pradeep Bonde_"
    )


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="EP Intraday Scanner (17h Lisboa)")
    parser.add_argument("--dry-run",       action="store_true",
                        help="Não envia Telegram nem regista no tracker")
    parser.add_argument("--min-gap",       type=float, default=8.0)
    parser.add_argument("--min-vol-ratio", type=float, default=3.0)
    parser.add_argument("--min-price",     type=float, default=5.0)
    args = parser.parse_args()

    result = run_intraday_scan(
        min_gap=args.min_gap,
        min_vol_ratio=args.min_vol_ratio,
        min_price=args.min_price,
        dry_run=args.dry_run,
    )

    print(f"\n{'='*50}")
    print(f"  Scan intraday completo")
    print(f"  Candidatos PRIME: {len(result.get('candidates', []))}")
    print(f"  Tickers verificados: {result.get('n_checked', 0):,}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    main()
