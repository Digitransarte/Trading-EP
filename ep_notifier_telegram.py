"""
EP Telegram Notifier
====================
Envia os candidatos EP do dia para o Telegram.

Configuração necessária (variáveis de ambiente ou .env):
  TELEGRAM_BOT_TOKEN  — token do bot (via @BotFather)
  TELEGRAM_CHAT_ID    — ID do chat/canal onde enviar

Como obter o CHAT_ID:
  1. Envia qualquer mensagem ao teu bot
  2. Abre: https://api.telegram.org/bot<TOKEN>/getUpdates
  3. Copia o campo "chat.id"
"""

import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WINDOW_EMOJI = {"PRIME": "🟢", "OPEN": "🟡", "LATE": "🔴"}
TYPE_EMOJI   = {
    "TURNAROUND":      "🔄",
    "GROWTH":          "📈",
    "STORY/NEGLECTED": "💡",
    "9M_EP":           "💥",
    "STANDARD":        "⚡",
}


def score_bar(score: int) -> str:
    """Visual score bar: ████░░░░░░ 72"""
    filled = score // 10
    return "█" * filled + "░" * (10 - filled) + f" {score}"


def format_candidate(c: dict, rank: int) -> str:
    """Format one EP candidate as a Telegram message block."""
    score   = c.get("magna_score", 0)
    ep_type = c.get("ep_type", "STANDARD")
    window  = c.get("entry_window", "PRIME")
    ticker  = c.get("ticker", "?")

    # Score bar with emoji
    if score >= 75:   score_emoji = "🏆"
    elif score >= 50: score_emoji = "✅"
    else:             score_emoji = "⚠️"

    lines = [
        f"{rank}. *{ticker}* {TYPE_EMOJI.get(ep_type, '⚡')} `{ep_type}`",
        f"{score_emoji} `{score_bar(score)}`",
        f"",
        f"💰 Preço: `${c.get('price', 0):.2f}`",
        f"📊 Gap: `+{c.get('gap_pct', 0):.1f}%` · Vol: `{c.get('vol_ratio', 0):.1f}×`",
    ]

    # Fundamentals
    eg = c.get("earnings_pct", 0)
    rg = c.get("revenue_pct", 0)
    if eg or rg:
        lines.append(f"📈 EPS: `{eg:+.0f}%` · Revenue: `{rg:+.0f}%`")

    # Float / Market cap
    fl = c.get("float_M", 0)
    mc = c.get("market_cap", "—")
    if fl or mc != "—":
        fl_str = f"{fl:.1f}M" if fl else "—"
        lines.append(f"🔢 Float: `{fl_str}` · Cap: `{mc}`")

    # Catalyst
    catalyst = c.get("catalyst", "")
    detail   = c.get("catalyst_detail", "")
    if catalyst and catalyst != "—":
        lines.append(f"")
        lines.append(f"🎯 *{catalyst}*")
        if detail:
            lines.append(f"_{detail}_")

    # Thesis
    thesis = c.get("thesis", "")
    if thesis:
        lines.append(f"")
        lines.append(f"💬 {thesis}")

    # Red flags
    red_flags = c.get("red_flags")
    if red_flags:
        lines.append(f"")
        lines.append(f"⚠️ _{red_flags}_")

    # Entry
    neglect = c.get("neglect_label", "")
    sector  = c.get("sector", "")
    lines.append(f"")
    lines.append(
        f"🕐 Entrada: {WINDOW_EMOJI.get(window, '⬜')} *{window}* "
        f"· Stop: `-{c.get('stop_loss_pct', 8)}%`"
    )
    if neglect and neglect != "—":
        lines.append(f"🌑 Neglect: _{neglect}_")
    if sector:
        lines.append(f"🏭 Sector: _{sector}_")

    return "\n".join(lines)


def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a message via Telegram Bot API."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados")
        return False

    url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       text,
        "parse_mode": parse_mode,
    }
    r = requests.post(url, json=data, timeout=15)
    if r.status_code == 200:
        return True
    else:
        print(f"Telegram error {r.status_code}: {r.text[:200]}")
        return False



def format_canslim_candidate(c: dict, rank: int) -> str:
    """Format one CANSLIM candidate as a Telegram message block."""
    ticker     = c.get("ticker", "?")
    change_pct = c.get("change_pct", 0)
    vol_ratio  = c.get("vol_ratio", 0)
    price      = c.get("price", 0)
    score      = c.get("score", 0)

    lines = [
        f"{rank}. *{ticker}* 📊 `CANSLIM`",
        f"",
        f"💰 Preço: `${price:.2f}`",
        f"📈 Variação: `+{change_pct:.1f}%` · Vol: `{vol_ratio:.1f}×`",
        f"⭐ Score: `{score:.1f}`",
    ]
    return "\n".join(lines)

def notify(scan_result: dict, min_score: int = 50) -> bool:
    """
    Send EP scan results to Telegram.
    Only sends candidates with magna_score >= min_score.
    Returns True if at least one message was sent.
    """
    candidates = scan_result.get("candidates", [])
    session    = scan_result.get("session_date", "—")
    n_universe = scan_result.get("n_universe", 0)
    error      = scan_result.get("error")

    # Error case
    if error:
        send_message(f"⚡ *EP Scanner* — {datetime.now().strftime('%d/%m/%Y')}\n\n❌ {error}")
        return False

    # Filter by min score
    top = [c for c in candidates if c.get("magna_score", 0) >= min_score]
    top.sort(key=lambda c: c.get("magna_score", 0), reverse=True)

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    # ── Header message ────────────────────────────────────────────────────────
    if not top:
        header = (
            f"⚡ *EP Scanner* · {now_str}\n"
            f"📅 Sessão: `{session}`\n"
            f"🔭 {n_universe:,} tickers analisados\n\n"
            f"😴 Sem candidatos EP com score ≥ {min_score} hoje.\n"
            f"_Continua a monitorizar..._"
        )
        send_message(header)
        return True

    n_aplus = sum(1 for c in top if c.get("magna_score", 0) >= 75)
    n_b     = sum(1 for c in top if 50 <= c.get("magna_score", 0) < 75)

    header = (
        f"⚡ *EP Scanner* · {now_str}\n"
        f"📅 Sessão: `{session}`\n"
        f"🔭 {n_universe:,} tickers · {len(top)} candidatos\n"
        f"🏆 A+ Setup: {n_aplus} · ✅ B Setup: {n_b}\n"
        f"{'─' * 28}"
    )
    send_message(header)

    # ── Individual candidate messages ─────────────────────────────────────────
    sent = 0
    for i, c in enumerate(top[:5], 1):   # max 5 candidates
        text = format_candidate(c, i)
        if send_message(text):
            sent += 1
        import time
        time.sleep(0.5)  # avoid Telegram flood limit

    # ── Footer ────────────────────────────────────────────────────────────────
    # ── CANSLIM section ──────────────────────────────────────────────────────
    canslim = scan_result.get("canslim", [])
    if canslim:
        cs_header = (
            f"📊 *CANSLIM* · {len(canslim)} candidatos\n"
            f"{'─' * 28}"
        )
        send_message(cs_header)
        for i, c in enumerate(canslim[:5], 1):
            send_message(format_canslim_candidate(c, i))
            import time as _time
            _time.sleep(0.4)

    footer = (
        f"{'─' * 28}\n"
        f"_⚠️ Apenas informativo. Não é aconselhamento financeiro._\n"
        f"_Scores MAGNA 53 + CAP 10×10 · Pradeep Bonde / Stockbee_"
    )
    send_message(footer)

    return sent > 0


def send_test_message() -> bool:
    """Send a test message to verify Telegram configuration."""
    return send_message(
        "✅ *EP Scanner* — Telegram configurado correctamente!\n"
        "_Esta é uma mensagem de teste._"
    )


if __name__ == "__main__":
    # Quick test
    print("A enviar mensagem de teste...")
    if send_test_message():
        print("✅ Telegram OK")
    else:
        print("❌ Falhou — verifica TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID")
