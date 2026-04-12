"""
EP Forward Tracker
==================
Rastreia em tempo real os candidatos encontrados pelo scanner.
Diferente do backtesting — aqui os dados chegam dia a dia, prospectivamente.

Fluxo:
  1. Scanner encontra candidatos → save_candidates() regista-os
  2. Diariamente, update_positions() actualiza preços (usa dados já carregados)
  3. Quando uma posição fecha → save_to_kb() alimenta a Knowledge Base
  4. Streamlit tab + Telegram mostram o estado actual

Regras de saída:
  - STOPPED:  preço fecha abaixo do stop_price (mínima do gap day)
  - WIN/LOSS: após max_hold_days (20 por defeito), fecha com o resultado
  - MANUAL:   saída manual via Streamlit

Base de dados: ep_forward_tracker.db (separada da KB)
"""

import os
import json
import sqlite3
import requests
import time
from datetime import date, datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
POLYGON_KEY = os.getenv("POLYGON_API_KEY")

TRACKER_DB   = "ep_forward_tracker.db"
MAX_HOLD_EP      = 20   # Pradeep: 20+ dias = 95% WR na KB
MAX_HOLD_CANSLIM = 40   # O'Neil: segurar líderes 6-8 semanas
MAX_HOLD_DAYS    = MAX_HOLD_EP  # alias para compatibilidade


# ─── SCHEMA ───────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS forward_tests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT NOT NULL,
    scan_date       TEXT NOT NULL,        -- data em que o scanner encontrou
    entry_price     REAL,                 -- preço no dia do scan
    gap_pct         REAL,
    vol_ratio       REAL,
    magna_score     INTEGER,
    strategy_type   TEXT DEFAULT 'EP',   -- EP / CANSLIM
    ep_type         TEXT,
    oneil_score     INTEGER,
    oneil_grade     TEXT,
    oneil_setup     TEXT,
    entry_window    TEXT,
    stop_price      REAL,                 -- âncora: EP=mínima gap day, CANSLIM=7-8% abaixo base
    stop_pct        REAL,
    prev_close      REAL,                 -- fecho pré-EP (pivot original)
    catalyst        TEXT,
    thesis          TEXT,
    sector          TEXT,
    float_m         REAL,
    -- tracking
    status          TEXT DEFAULT 'OPEN',  -- OPEN/WIN/LOSS/STOPPED/EXPIRED
    current_price   REAL,
    max_price       REAL,                 -- máximo desde a entrada
    min_price       REAL,                 -- mínimo desde a entrada
    last_updated    TEXT,
    exit_price      REAL,
    exit_date       TEXT,
    return_pct      REAL,
    hold_days       INTEGER DEFAULT 0,
    exit_reason     TEXT,                 -- STOP_HIT/EXPIRED/MANUAL
    kb_saved        INTEGER DEFAULT 0,    -- 1 quando guardado na KB
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_ft_status ON forward_tests(status);
CREATE INDEX IF NOT EXISTS idx_ft_ticker ON forward_tests(ticker);
CREATE INDEX IF NOT EXISTS idx_ft_scan_date ON forward_tests(scan_date);
"""


def get_conn():
    conn = sqlite3.connect(TRACKER_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ─── SAVE CANDIDATES ──────────────────────────────────────────────────────────

def save_candidates(candidates: list, scan_date: str = None) -> int:
    """
    Guarda candidatos do scanner no tracker.
    Ignora duplicados (mesmo ticker + scan_date).
    Retorna número de novos candidatos guardados.
    """
    if not candidates:
        return 0

    today = scan_date or date.today().strftime("%Y-%m-%d")
    conn  = get_conn()
    saved = 0

    for c in candidates:
        ticker = c.get("ticker", "")
        if not ticker:
            continue

        # Verificar se já existe para este scan_date
        exists = conn.execute(
            "SELECT id FROM forward_tests WHERE ticker=? AND scan_date=?",
            (ticker, today)
        ).fetchone()
        if exists:
            continue

        entry_price = c.get("price", 0)
        stop_price  = c.get("stop_price", 0)

        strategy = c.get("strategy_type", "EP")
        if strategy == "CANSLIM" and not stop_price:
            stop_price = round(entry_price * 0.92, 2)

        conn.execute("""
            INSERT INTO forward_tests
            (ticker, scan_date, entry_price, gap_pct, vol_ratio, magna_score,
             strategy_type, ep_type, oneil_score, oneil_grade, oneil_setup,
             entry_window, stop_price, stop_pct, prev_close,
             catalyst, thesis, sector, float_m,
             status, current_price, max_price, min_price, last_updated)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                    'OPEN',?,?,?,?)
        """, (
            ticker, today,
            entry_price,
            c.get("gap_pct", 0),
            c.get("vol_ratio", 0),
            c.get("magna_score", 0),
            strategy,
            c.get("ep_type", "STANDARD"),
            c.get("oneil_score", 0),
            c.get("oneil_grade", ""),
            c.get("oneil_setup", ""),
            c.get("entry_window", "PRIME"),
            stop_price,
            c.get("stop_pct", 8),
            c.get("prev_close", 0),
            c.get("catalyst", ""),
            c.get("thesis", ""),
            c.get("sector", ""),
            c.get("float_m", 0),
            entry_price, entry_price, entry_price, today,
        ))
        saved += 1

    conn.commit()
    conn.close()
    print(f"[Tracker] {saved} novos candidatos registados para {today}")
    return saved


# ─── UPDATE POSITIONS ─────────────────────────────────────────────────────────

def update_positions(today_data: dict = None, today_str: str = None) -> dict:
    """
    Actualiza preços de todas as posições OPEN.
    today_data: dict {ticker: {c, h, l, v, ...}} do Polygon grouped
                Se None, faz fetch independente (mais lento).
    Retorna sumário de posições fechadas hoje.
    """
    today = today_str or date.today().strftime("%Y-%m-%d")
    conn  = get_conn()

    open_positions = conn.execute(
        "SELECT * FROM forward_tests WHERE status='OPEN'"
    ).fetchall()

    if not open_positions:
        conn.close()
        return {"updated": 0, "closed": [], "open": 0}

    closed_today = []
    updated      = 0

    for pos in open_positions:
        ticker      = pos["ticker"]
        entry_price = pos["entry_price"] or 1
        stop_price  = pos["stop_price"] or 0
        scan_date   = pos["scan_date"]

        # Calcular dias em aberto
        try:
            scan_dt   = datetime.strptime(scan_date, "%Y-%m-%d").date()
            hold_days = (date.today() - scan_dt).days
        except:
            hold_days = 0

        strategy = pos["strategy_type"] if "strategy_type" in pos.keys() else "EP"
        max_hold = MAX_HOLD_CANSLIM if strategy == "CANSLIM" else MAX_HOLD_EP

        # Obter preço actual
        current_price = _get_price(ticker, today_data)
        if current_price is None or current_price <= 0:
            continue  # sem dados hoje (feriado, delisted, etc.)

        # Actualizar high/low
        new_max = max(pos["max_price"] or entry_price, current_price)
        new_min = min(pos["min_price"] or entry_price, current_price)

        # Calcular retorno actual
        return_pct = (current_price - entry_price) / entry_price * 100

        # ── Verificar condições de saída ─────────────────────────────────────

        status      = "OPEN"
        exit_reason = None

        # 1. Stop atingido (preço fecha abaixo da mínima do gap day)
        if stop_price > 0 and current_price < stop_price:
            status      = "LOSS" if return_pct < 0 else "WIN"
            exit_reason = "STOP_HIT"

        # 2. Expirado (20 dias) — fecha com o resultado actual
        elif hold_days >= max_hold:
            status      = "WIN" if return_pct > 0 else "LOSS"
            exit_reason = "EXPIRED_20D"

        # Actualizar na DB
        if status != "OPEN":
            conn.execute("""
                UPDATE forward_tests SET
                    status=?, current_price=?, max_price=?, min_price=?,
                    return_pct=?, hold_days=?, exit_price=?, exit_date=?,
                    exit_reason=?, last_updated=?
                WHERE id=?
            """, (status, current_price, new_max, new_min,
                  round(return_pct, 2), hold_days,
                  current_price, today, exit_reason, today,
                  pos["id"]))

            closed_today.append({
                "ticker":      ticker,
                "status":      status,
                "return_pct":  round(return_pct, 2),
                "hold_days":   hold_days,
                "exit_reason": exit_reason,
                "entry_price": entry_price,
                "exit_price":  current_price,
                "magna_score": pos["magna_score"],
                "ep_type":     pos["ep_type"],
                "gap_pct":     pos["gap_pct"],
                "vol_ratio":   pos["vol_ratio"],
                "stop_price":  stop_price,
            })
        else:
            conn.execute("""
                UPDATE forward_tests SET
                    current_price=?, max_price=?, min_price=?,
                    return_pct=?, hold_days=?, last_updated=?
                WHERE id=?
            """, (current_price, new_max, new_min,
                  round(return_pct, 2), hold_days, today,
                  pos["id"]))
        updated += 1

    conn.commit()

    # Contar ainda abertas
    still_open = conn.execute(
        "SELECT COUNT(*) FROM forward_tests WHERE status='OPEN'"
    ).fetchone()[0]

    conn.close()

    # Alimentar KB com posições fechadas
    if closed_today:
        _save_closed_to_kb(closed_today)

    return {
        "updated":  updated,
        "closed":   closed_today,
        "open":     still_open,
    }


def _get_price(ticker: str, today_data: dict = None) -> float:
    """Obtém preço de fecho do ticker. Usa today_data se disponível."""
    if today_data and ticker in today_data:
        return today_data[ticker].get("c", 0)

    # Fallback: yfinance (sem custo API)
    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except:
        pass
    return None


# ─── SAVE CLOSED TO KB ────────────────────────────────────────────────────────

def _save_closed_to_kb(closed: list):
    """
    Guarda posições fechadas na Knowledge Base (trade_log).
    Usa run_id=None para distinguir de backtests automáticos.
    """
    try:
        kb_db = "ep_knowledge_base.db"
        if not os.path.exists(kb_db):
            return

        conn = sqlite3.connect(kb_db)

        for c in closed:
            # Verificar se já foi guardado
            exists = conn.execute(
                "SELECT id FROM forward_tests WHERE ticker=? AND ep_date=?",
                (c["ticker"], c.get("scan_date", ""))
            ).fetchone() if False else None  # trade_log não tem este campo

            conn.execute("""
                INSERT OR IGNORE INTO trade_log
                (run_id, ticker, ep_date, entry_date, entry_price,
                 gap_pct, vol_ratio, stop_pct,
                 exit_date, exit_reason, holding_days,
                 max_gain_pct, total_return_pct, result)
                VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                c["ticker"],
                c.get("scan_date", ""),          # ep_date = scan_date
                c.get("scan_date", ""),           # entry_date
                c.get("entry_price", 0),
                c.get("gap_pct", 0),
                c.get("vol_ratio", 0),
                c.get("stop_pct", 8),
                c.get("exit_date", ""),
                c.get("exit_reason", ""),
                c.get("hold_days", 0),
                max(c.get("return_pct", 0), 0),  # max_gain = return se positivo
                c.get("return_pct", 0),
                c["status"],  # WIN ou LOSS
            ))

        conn.commit()
        conn.close()
        print(f"[Tracker→KB] {len(closed)} trades guardados na KB")

        # Marcar como saved no tracker
        tracker_conn = get_conn()
        for c in closed:
            tracker_conn.execute(
                "UPDATE forward_tests SET kb_saved=1 WHERE ticker=? AND status!=?",
                (c["ticker"], "OPEN")
            )
        tracker_conn.commit()
        tracker_conn.close()

    except Exception as e:
        print(f"[Tracker→KB] Falhou: {e}")


# ─── STATS ────────────────────────────────────────────────────────────────────

def get_tracker_stats() -> dict:
    """Retorna estatísticas completas do tracker."""
    conn = get_conn()

    open_pos = conn.execute(
        "SELECT * FROM forward_tests WHERE status='OPEN' ORDER BY scan_date DESC"
    ).fetchall()

    closed_pos = conn.execute(
        "SELECT * FROM forward_tests WHERE status!='OPEN' ORDER BY exit_date DESC"
    ).fetchall()

    # Agregados
    total_closed = len(closed_pos)
    wins  = sum(1 for p in closed_pos if p["status"] == "WIN")
    losses = total_closed - wins
    win_rate = round(wins / total_closed * 100, 1) if total_closed > 0 else 0

    returns = [p["return_pct"] for p in closed_pos if p["return_pct"] is not None]
    avg_return  = round(sum(returns) / len(returns), 2) if returns else 0
    avg_win     = round(sum(r for r in returns if r > 0) / max(wins, 1), 2)
    avg_loss    = round(sum(r for r in returns if r < 0) / max(losses, 1), 2)
    best_trade  = max(returns) if returns else 0
    worst_trade = min(returns) if returns else 0

    # Performance por MAGNA score tier
    tiers = {"A+ (>=75)": [], "B (50-74)": [], "C (<50)": []}
    for p in closed_pos:
        s = p["magna_score"] or 0
        r = p["return_pct"] or 0
        if s >= 75:   tiers["A+ (>=75)"].append(r)
        elif s >= 50: tiers["B (50-74)"].append(r)
        else:         tiers["C (<50)"].append(r)

    tier_stats = {}
    for label, rets in tiers.items():
        if rets:
            w = sum(1 for r in rets if r > 0)
            tier_stats[label] = {
                "n": len(rets),
                "win_rate": round(w / len(rets) * 100, 1),
                "avg_return": round(sum(rets) / len(rets), 2),
            }

    conn.close()

    def _calc_stats(positions):
        if not positions: return {"n": 0, "win_rate": 0, "avg_return": 0}
        w    = sum(1 for p in positions if p["status"] == "WIN")
        rets = [p["return_pct"] for p in positions if p["return_pct"] is not None]
        return {
            "n":          len(positions),
            "win_rate":   round(w / len(positions) * 100, 1),
            "avg_return": round(sum(rets) / len(rets), 2) if rets else 0,
        }

    ep_closed = [p for p in closed_pos if (p["strategy_type"] if "strategy_type" in p.keys() else "EP") == "EP"]
    cs_closed = [p for p in closed_pos if (p["strategy_type"] if "strategy_type" in p.keys() else "EP") == "CANSLIM"]

    return {
        "open":          [dict(p) for p in open_pos],
        "closed":        [dict(p) for p in closed_pos[:20]],
        "total_tracked": total_closed + len(open_pos),
        "total_closed":  total_closed,
        "total_open":    len(open_pos),
        "wins":          wins,
        "losses":        losses,
        "win_rate":      win_rate,
        "avg_return":    avg_return,
        "avg_win":       avg_win,
        "avg_loss":      avg_loss,
        "best_trade":    round(best_trade, 2),
        "worst_trade":   round(worst_trade, 2),
        "tier_stats":    tier_stats,
        "ep_stats":      _calc_stats(ep_closed),
        "canslim_stats": _calc_stats(cs_closed),
    }


# ─── TELEGRAM FORMAT ──────────────────────────────────────────────────────────

def format_tracker_telegram(stats: dict) -> list[str]:
    """Gera mensagens Telegram para o estado do tracker. Retorna lista de strings."""
    messages = []
    open_pos = stats.get("open", [])
    closed   = stats.get("closed", [])

    # ── Header de estatísticas ────────────────────────────────────────────────
    total_c = stats.get("total_closed", 0)
    if total_c > 0:
        wr  = stats.get("win_rate", 0)
        avg = stats.get("avg_return", 0)
        wr_emoji = "🟢" if wr >= 50 else "🟡" if wr >= 35 else "🔴"

        msg = (
            f"📊 *Forward Tracker* · {stats.get('total_open',0)} abertas\n"
            f"{'─' * 28}\n"
            f"{wr_emoji} WR: `{wr}%` ({stats.get('wins',0)}W / {stats.get('losses',0)}L)"
            f" · Avg: `{avg:+.1f}%`\n"
            f"🏆 Melhor: `{stats.get('best_trade',0):+.1f}%` "
            f"· Pior: `{stats.get('worst_trade',0):+.1f}%`"
        )
        messages.append(msg)

    # ── Posições abertas ──────────────────────────────────────────────────────
    if open_pos:
        lines = ["📂 *Posições Abertas:*\n"]
        for p in open_pos[:6]:
            ret    = p.get("return_pct", 0) or 0
            days   = p.get("hold_days", 0) or 0
            remain = max(MAX_HOLD_DAYS - days, 0)
            emoji  = "🟢" if ret > 5 else "🟡" if ret > 0 else "🔴"
            lines.append(
                f"{emoji} *{p['ticker']}* `{ret:+.1f}%` "
                f"· D{days} · {remain}d restantes"
            )
        messages.append("\n".join(lines))

    # ── Fechadas recentemente (últimas 24h) ───────────────────────────────────
    today = date.today().strftime("%Y-%m-%d")
    recent_closed = [p for p in closed if p.get("exit_date") == today]
    if recent_closed:
        lines = ["🔒 *Fechadas hoje:*\n"]
        for p in recent_closed:
            ret    = p.get("return_pct", 0) or 0
            reason = p.get("exit_reason", "")
            emoji  = "✅" if p["status"] == "WIN" else "❌"
            lines.append(
                f"{emoji} *{p['ticker']}* `{ret:+.1f}%` "
                f"({p['hold_days']}d · {reason})"
            )
        messages.append("\n".join(lines))

    return messages


# ─── STREAMLIT RENDERER ───────────────────────────────────────────────────────

def render_tracker_tab():
    """
    Renderiza a tab do Forward Tracker no Streamlit.
    Chama directamente dentro de trading_ep_v2.py.
    """
    import streamlit as st

    stats = get_tracker_stats()

    if stats["total_tracked"] == 0:
        st.info("📭 Sem candidatos rastreados ainda. O tracker começa a registar automaticamente a partir do próximo scan.")
        return

    # ── Métricas de topo ──────────────────────────────────────────────────────
    total_c = stats["total_closed"]
    if total_c > 0:
        m1, m2, m3, m4, m5 = st.columns(5)
        wr = stats["win_rate"]
        m1.metric("Win Rate", f"{wr}%",
                  delta=f"{stats['wins']}W / {stats['losses']}L")
        m2.metric("Avg Return", f"{stats['avg_return']:+.1f}%")
        m3.metric("Avg Win",    f"{stats['avg_win']:+.1f}%")
        m4.metric("Avg Loss",   f"{stats['avg_loss']:+.1f}%")
        m5.metric("Total",      f"{total_c} trades",
                  delta=f"{stats['total_open']} abertas")

        # Performance por tier MAGNA
        tier_stats = stats.get("tier_stats", {})
        if tier_stats:
            st.markdown("##### Performance por MAGNA Score")
            cols = st.columns(len(tier_stats))
            for col, (label, ts) in zip(cols, tier_stats.items()):
                wr_color = "normal" if ts["win_rate"] >= 50 else "inverse"
                col.metric(
                    label,
                    f"WR {ts['win_rate']}%",
                    delta=f"avg {ts['avg_return']:+.1f}% · n={ts['n']}",
                    delta_color=wr_color,
                )

    st.divider()

    # ── Posições abertas ──────────────────────────────────────────────────────
    open_pos = stats["open"]
    if open_pos:
        st.markdown(f"#### 📂 Posições Abertas ({len(open_pos)})")
        for p in open_pos:
            ret    = p.get("return_pct", 0) or 0
            days   = p.get("hold_days", 0) or 0
            remain = max(MAX_HOLD_DAYS - days, 0)
            score  = p.get("magna_score", 0) or 0

            ret_color = "#00e87a" if ret > 5 else "#f5c842" if ret > 0 else "#ff5e5e"
            bar_pct   = min(abs(ret) / 30 * 100, 100)

            with st.expander(
                f"**{p['ticker']}** · {ret:+.1f}% · D{days} · MAGNA {score}",
                expanded=False
            ):
                c1, c2, c3 = st.columns(3)
                c1.markdown(
                    f"**Entrada:** `${p.get('entry_price', 0):.2f}`  \n"
                    f"**Actual:** `${p.get('current_price', 0):.2f}`  \n"
                    f"**Máximo:** `${p.get('max_price', 0):.2f}`"
                )
                c2.markdown(
                    f"**Gap:** `+{p.get('gap_pct', 0):.1f}%`  \n"
                    f"**Vol:** `{p.get('vol_ratio', 0):.1f}x`  \n"
                    f"**Tipo:** `{p.get('ep_type', '—')}`"
                )
                c3.markdown(
                    f"**Stop:** `${p.get('stop_price', 0):.2f}`  \n"
                    f"**Dias restantes:** `{remain}d`  \n"
                    f"**Scan:** `{p.get('scan_date', '—')}`"
                )

                # Barra de progresso de retorno
                st.markdown(
                    f'<div style="background:#1e2d45;border-radius:4px;height:6px;margin:8px 0">'
                    f'<div style="background:{ret_color};width:{bar_pct}%;height:6px;border-radius:4px"></div>'
                    f'</div>',
                    unsafe_allow_html=True
                )

                if p.get("thesis"):
                    st.caption(f"💬 {p['thesis']}")

                # Saída manual
                if st.button(f"🔒 Fechar manualmente", key=f"close_{p['id']}"):
                    _close_manual(p["id"], p.get("current_price", 0))
                    st.rerun()

    # ── Posições fechadas ─────────────────────────────────────────────────────
    closed = stats["closed"]
    if closed:
        st.divider()
        st.markdown(f"#### 🔒 Histórico ({min(len(closed), 20)} mais recentes)")

        for p in closed:
            ret    = p.get("return_pct", 0) or 0
            status = p.get("status", "")
            emoji  = "✅" if status == "WIN" else "❌"
            reason_map = {
                "STOP_HIT":   "Stop atingido",
                "EXPIRED_20D": "20 dias expirados",
                "MANUAL":     "Saída manual",
            }
            reason = reason_map.get(p.get("exit_reason", ""), p.get("exit_reason", ""))

            with st.expander(
                f"{emoji} **{p['ticker']}** · {ret:+.1f}% · {p.get('hold_days', 0)}d · {reason}",
                expanded=False
            ):
                c1, c2 = st.columns(2)
                c1.markdown(
                    f"**Entrada:** `${p.get('entry_price', 0):.2f}` ({p.get('scan_date', '—')})  \n"
                    f"**Saída:** `${p.get('exit_price', 0):.2f}` ({p.get('exit_date', '—')})  \n"
                    f"**Retorno:** `{ret:+.1f}%`"
                )
                c2.markdown(
                    f"**Gap:** `+{p.get('gap_pct', 0):.1f}%` · "
                    f"**Vol:** `{p.get('vol_ratio', 0):.1f}x`  \n"
                    f"**MAGNA:** `{p.get('magna_score', 0)}` · "
                    f"**Tipo:** `{p.get('ep_type', '—')}`  \n"
                    f"**Stop:** `${p.get('stop_price', 0):.2f}`"
                )
                if p.get("thesis"):
                    st.caption(f"💬 {p['thesis']}")


def _close_manual(position_id: int, current_price: float):
    """Fecha uma posição manualmente."""
    today = date.today().strftime("%Y-%m-%d")
    conn  = get_conn()

    pos = conn.execute(
        "SELECT * FROM forward_tests WHERE id=?", (position_id,)
    ).fetchone()

    if pos:
        entry  = pos["entry_price"] or 1
        ret    = (current_price - entry) / entry * 100
        status = "WIN" if ret > 0 else "LOSS"

        conn.execute("""
            UPDATE forward_tests SET
                status=?, exit_price=?, exit_date=?,
                return_pct=?, exit_reason='MANUAL', last_updated=?
            WHERE id=?
        """, (status, current_price, today, round(ret, 2), today, position_id))
        conn.commit()

        # Alimentar KB
        _save_closed_to_kb([{
            "ticker":      pos["ticker"],
            "scan_date":   pos["scan_date"],
            "status":      status,
            "return_pct":  round(ret, 2),
            "hold_days":   pos["hold_days"],
            "exit_reason": "MANUAL",
            "exit_date":   today,
            "entry_price": entry,
            "exit_price":  current_price,
            "gap_pct":     pos["gap_pct"],
            "vol_ratio":   pos["vol_ratio"],
            "stop_pct":    pos["stop_pct"],
        }])

    conn.close()


# ─── ENTRY POINT (teste) ──────────────────────────────────────────────────────

if __name__ == "__main__":
    print("EP Forward Tracker — estado actual:")
    stats = get_tracker_stats()
    print(f"  Abertas:  {stats['total_open']}")
    print(f"  Fechadas: {stats['total_closed']}")
    if stats["total_closed"] > 0:
        print(f"  Win Rate: {stats['win_rate']}%")
        print(f"  Avg Ret:  {stats['avg_return']:+.1f}%")
