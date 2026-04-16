"""
EP Pullback Monitor
===================
Monitoriza candidatos EP após o gap day e identifica
a janela ideal de entrada no pullback.

Metodologia Pradeep Bonde:
  - O EP dispara com gap + volume extremo (catalisador)
  - Nos dias seguintes o preço recua (pullback saudável)
  - A entrada ideal acontece quando:
      1. Preço recuou 20-50% do movimento do gap
      2. Volume diário caiu para <50% do gap day
      3. Preço ainda acima da mínima do gap day (stop anchor)
      4. Dias após EP: entre 2 e 12
  - Stop: mínima do gap day (inalterado desde o gap)

Tabela: ep_monitor
  - Guarda candidatos EP qualificados
  - Actualizado diariamente pelo ep_daily_runner
  - Status: MONITORING → ENTRY_SIGNAL / INVALIDATED / EXPIRED
"""

import os
import sqlite3
import json
import time
import requests
from datetime import date, datetime, timedelta
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

MONITOR_DB = "ep_forward_tracker.db"   # mesma DB do tracker

# ─── SCHEMA ADICIONAL ─────────────────────────────────────────────────────────

MONITOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS ep_monitor (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    gap_date            TEXT NOT NULL,       -- data do gap day
    gap_price_open      REAL,                -- abertura do gap day
    gap_price_close     REAL,                -- fecho do gap day
    gap_price_high      REAL,                -- máximo do gap day
    gap_price_low       REAL,                -- STOP ANCHOR (mínima gap day)
    gap_pct             REAL,                -- % do gap
    gap_volume          INTEGER,             -- volume do gap day
    prev_close          REAL,                -- fecho pré-EP
    magna_score         INTEGER,
    ep_type             TEXT,
    sector              TEXT,
    catalyst            TEXT,
    -- estado actual
    status              TEXT DEFAULT 'MONITORING',
    -- MONITORING: a acompanhar
    -- ENTRY_SIGNAL: condições cumpridas → alerta enviado
    -- ENTERED: trader entrou (manual)
    -- INVALIDATED: stop violado antes da entrada
    -- EXPIRED: passou 12 dias sem sinal
    -- tracking diário
    current_price       REAL,
    current_volume      INTEGER,
    days_since_gap      INTEGER DEFAULT 0,
    pullback_pct        REAL,               -- % recuo desde o pico
    vol_ratio_today     REAL,               -- vol hoje / vol gap day
    conditions_met      TEXT,               -- JSON: quais condições cumpridas
    signal_date         TEXT,               -- quando o sinal foi dado
    signal_price        REAL,               -- preço no dia do sinal
    signal_quality      TEXT,               -- STRONG / MODERATE / WEAK
    last_updated        TEXT,
    -- resultado (preenchido se entrou)
    entry_price_actual  REAL,
    entry_date_actual   TEXT,
    exit_price          REAL,
    exit_date           TEXT,
    return_pct          REAL,
    notes               TEXT
);

CREATE INDEX IF NOT EXISTS idx_mon_status ON ep_monitor(status);
CREATE INDEX IF NOT EXISTS idx_mon_ticker ON ep_monitor(ticker);
CREATE INDEX IF NOT EXISTS idx_mon_gap_date ON ep_monitor(gap_date);
"""

# ─── CONDIÇÕES DE ENTRADA IDEAL ───────────────────────────────────────────────

# Baseadas na metodologia Pradeep Bonde
# Serão calibradas pela KB à medida que os dados acumulam

ENTRY_CONDITIONS = {
    "days_min":           2,      # mínimo 2 dias após o gap
    "days_max":           12,     # máximo 12 dias (depois perde momentum)
    "pullback_min_pct":   15,     # recuou pelo menos 15% do pico
    "pullback_max_pct":   60,     # não recuou mais de 60% (sinal fraco)
    "vol_dry_threshold":  0.50,   # volume hoje < 50% do gap day
    "stop_buffer_pct":    5,      # preço pelo menos 5% acima do stop
}


# ─── DB ───────────────────────────────────────────────────────────────────────

def get_monitor_conn():
    conn = sqlite3.connect(MONITOR_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(MONITOR_SCHEMA)
    return conn


# ─── ADICIONAR CANDIDATO ──────────────────────────────────────────────────────

def add_to_monitor(candidate: dict, scan_date: str = None) -> bool:
    """
    Adiciona um candidato EP à lista de monitorização.
    Só adiciona se:
      - MAGNA score >= 50
      - Gap >= 15%
      - Vol ratio >= 5×
      - Ainda não está em monitorização
    """
    ticker     = candidate.get("ticker", "")
    gap_pct    = candidate.get("gap_pct", 0)
    vol_ratio  = candidate.get("vol_ratio", 0)
    magna      = candidate.get("magna_score", 0)
    price      = candidate.get("price", 0)
    stop_price = candidate.get("stop_price", 0)
    prev_close = candidate.get("prev_close", 0)

    if not ticker or gap_pct < 15 or vol_ratio < 5 or magna < 50:
        return False

    today = scan_date or date.today().strftime("%Y-%m-%d")

    conn = get_monitor_conn()

    # Verificar se já existe em monitorização activa
    existing = conn.execute(
        "SELECT id FROM ep_monitor WHERE ticker=? AND status='MONITORING' "
        "AND gap_date >= date('now', '-15 days')",
        (ticker,)
    ).fetchone()

    if existing:
        conn.close()
        return False

    # Estimar gap_day_low (stop anchor): usa stop_price ou prev_close + buffer
    gap_low = stop_price or round(prev_close * 1.02, 2)

    conn.execute("""
        INSERT INTO ep_monitor (
            ticker, gap_date, gap_price_open, gap_price_close,
            gap_price_high, gap_price_low, gap_pct, gap_volume,
            prev_close, magna_score, ep_type, sector, catalyst,
            status, current_price, days_since_gap, last_updated
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,
                  'MONITORING',?,0,?)
    """, (
        ticker, today,
        price, price, price, gap_low,
        gap_pct, int(candidate.get("volume", 0)),
        prev_close, magna,
        candidate.get("ep_type", "STANDARD"),
        candidate.get("sector", ""),
        candidate.get("catalyst", ""),
        price, today,
    ))

    conn.commit()
    conn.close()
    return True


# ─── ACTUALIZAR MONITORIZAÇÃO ─────────────────────────────────────────────────

def update_monitor(today_data: dict = None, today_str: str = None) -> dict:
    """
    Actualiza todos os candidatos em MONITORING.
    Para cada um:
      - Obtém preço e volume actuais
      - Calcula pullback % e vol ratio
      - Verifica condições de entrada
      - Muda status se necessário

    Retorna lista de novos sinais gerados.
    """
    today  = today_str or date.today().strftime("%Y-%m-%d")
    conn   = get_monitor_conn()
    active = conn.execute(
        "SELECT * FROM ep_monitor WHERE status='MONITORING'"
    ).fetchall()

    if not active:
        conn.close()
        return {"signals": [], "updated": 0, "expired": 0, "invalidated": 0}

    new_signals  = []
    updated      = 0
    expired      = 0
    invalidated  = 0

    for m in active:
        ticker     = m["ticker"]
        gap_date   = m["gap_date"]
        gap_high   = m["gap_price_high"] or m["gap_price_close"] or 1
        gap_low    = m["gap_price_low"] or 0
        gap_vol    = m["gap_volume"] or 1
        prev_close = m["prev_close"] or 0

        # Dias desde o gap
        try:
            gap_dt    = datetime.strptime(gap_date, "%Y-%m-%d").date()
            days_since = (date.today() - gap_dt).days
        except:
            days_since = 0

        # Expirado
        if days_since > ENTRY_CONDITIONS["days_max"]:
            conn.execute(
                "UPDATE ep_monitor SET status='EXPIRED', last_updated=? WHERE id=?",
                (today, m["id"])
            )
            expired += 1
            continue

        # Obter preço e volume actuais
        current_price, current_vol = _get_price_vol(ticker, today_data)
        if not current_price:
            continue

        # Pullback % desde o pico do gap day
        pullback_pct = round((gap_high - current_price) / gap_high * 100, 1)

        # Vol ratio hoje vs gap day
        vol_ratio_today = round(current_vol / max(gap_vol, 1), 2) if current_vol else 1.0

        # Gap ainda intacto? (preço acima do stop anchor)
        gap_intact = current_price > gap_low
        pct_above_stop = round((current_price - gap_low) / gap_low * 100, 1) if gap_low else 0

        # Verificar condições de entrada
        conds = _check_entry_conditions(
            days_since, pullback_pct, vol_ratio_today,
            pct_above_stop, gap_intact
        )

        # Invalidado: stop violado
        if not gap_intact and days_since >= 1:
            conn.execute(
                "UPDATE ep_monitor SET status='INVALIDATED', current_price=?, "
                "days_since_gap=?, last_updated=? WHERE id=?",
                (round(current_price, 2), days_since, today, m["id"])
            )
            invalidated += 1
            continue

        # Novo sinal de entrada?
        new_status = "MONITORING"
        signal_quality = None
        if conds["all_met"] and m["status"] == "MONITORING":
            new_status    = "ENTRY_SIGNAL"
            signal_quality = conds["quality"]
            new_signals.append({
                "ticker":        ticker,
                "current_price": round(current_price, 2),
                "gap_date":      gap_date,
                "gap_pct":       m["gap_pct"],
                "pullback_pct":  pullback_pct,
                "vol_ratio_today": vol_ratio_today,
                "days_since_gap": days_since,
                "stop_price":    round(gap_low, 2),
                "pct_above_stop": pct_above_stop,
                "magna_score":   m["magna_score"],
                "ep_type":       m["ep_type"],
                "sector":        m["sector"],
                "signal_quality": signal_quality,
                "conditions":    conds,
            })

        # Actualizar na DB
        conn.execute("""
            UPDATE ep_monitor SET
                current_price=?, current_volume=?, days_since_gap=?,
                pullback_pct=?, vol_ratio_today=?,
                conditions_met=?, status=?,
                signal_date=?, signal_price=?, signal_quality=?,
                last_updated=?
            WHERE id=?
        """, (
            round(current_price, 2),
            int(current_vol) if current_vol else None,
            days_since, pullback_pct, vol_ratio_today,
            json.dumps(conds),
            new_status,
            today if new_status == "ENTRY_SIGNAL" else m["signal_date"],
            round(current_price, 2) if new_status == "ENTRY_SIGNAL" else m["signal_price"],
            signal_quality or m["signal_quality"],
            today, m["id"]
        ))
        updated += 1

    conn.commit()
    conn.close()

    return {
        "signals":    new_signals,
        "updated":    updated,
        "expired":    expired,
        "invalidated": invalidated,
    }


# ─── VERIFICAR CONDIÇÕES ──────────────────────────────────────────────────────

def _check_entry_conditions(
    days: int, pullback_pct: float, vol_today_ratio: float,
    pct_above_stop: float, gap_intact: bool
) -> dict:
    """
    Verifica cada condição de entrada individualmente.
    Retorna dict com estado de cada condição e qualidade do sinal.
    """
    c = ENTRY_CONDITIONS

    cond_days     = c["days_min"] <= days <= c["days_max"]
    cond_pullback = c["pullback_min_pct"] <= pullback_pct <= c["pullback_max_pct"]
    cond_vol_dry  = vol_today_ratio < c["vol_dry_threshold"]
    cond_intact   = gap_intact and pct_above_stop >= c["stop_buffer_pct"]

    met = [cond_days, cond_pullback, cond_vol_dry, cond_intact]
    n_met = sum(met)
    all_met = all(met)

    # Qualidade do sinal
    if all_met:
        if vol_today_ratio < 0.30 and c["pullback_min_pct"] <= pullback_pct <= 40:
            quality = "STRONG"
        elif vol_today_ratio < 0.40:
            quality = "MODERATE"
        else:
            quality = "WEAK"
    else:
        quality = None

    return {
        "all_met":        all_met,
        "n_met":          n_met,
        "quality":        quality,
        "cond_days":      cond_days,
        "cond_pullback":  cond_pullback,
        "cond_vol_dry":   cond_vol_dry,
        "cond_intact":    cond_intact,
        "details": {
            "days":          days,
            "pullback_pct":  pullback_pct,
            "vol_ratio":     vol_today_ratio,
            "pct_above_stop": pct_above_stop,
        }
    }


# ─── GET PRICE + VOLUME ───────────────────────────────────────────────────────

def _get_price_vol(ticker: str, today_data: dict = None):
    """Obtém preço e volume actuais."""
    if today_data and ticker in today_data:
        bar = today_data[ticker]
        return bar.get("c", 0), bar.get("v", 0)

    try:
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period="2d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1]), int(hist["Volume"].iloc[-1])
    except:
        pass
    return None, None


# ─── GET MONITOR STATUS ───────────────────────────────────────────────────────

def get_monitor_status() -> dict:
    """Retorna estado completo da monitorização para o Streamlit."""
    conn = get_monitor_conn()

    monitoring = conn.execute(
        "SELECT * FROM ep_monitor WHERE status='MONITORING' ORDER BY magna_score DESC"
    ).fetchall()

    signals = conn.execute(
        "SELECT * FROM ep_monitor WHERE status='ENTRY_SIGNAL' ORDER BY signal_date DESC"
    ).fetchall()

    recent_expired = conn.execute(
        "SELECT * FROM ep_monitor WHERE status IN ('EXPIRED','INVALIDATED') "
        "ORDER BY last_updated DESC LIMIT 10"
    ).fetchall()

    conn.close()

    return {
        "monitoring":       [dict(m) for m in monitoring],
        "signals":          [dict(s) for s in signals],
        "recent_expired":   [dict(e) for e in recent_expired],
        "n_monitoring":     len(monitoring),
        "n_signals":        len(signals),
    }


# ─── TELEGRAM ALERTS ─────────────────────────────────────────────────────────

def notify_signals(signals: list):
    """Envia alertas de entrada para o Telegram."""
    if not signals:
        return

    token   = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return

    def send(text):
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=15
            )
            time.sleep(0.5)
        except:
            pass

    quality_emoji = {"STRONG": "🔥", "MODERATE": "✅", "WEAK": "⚠️"}

    send(
        f"📡 *EP Pullback Monitor* · {date.today().strftime('%Y-%m-%d')}\n"
        f"🎯 {len(signals)} sinal{'is' if len(signals)>1 else ''} de entrada detectado{'s' if len(signals)>1 else ''}"
    )

    for s in signals:
        emoji  = quality_emoji.get(s["signal_quality"], "✅")
        ticker = s["ticker"]
        price  = s["current_price"]
        stop   = s["stop_price"]
        stop_pct = round((price - stop) / price * 100, 1)
        t1     = round(price * 1.20, 2)
        t2     = round(price * 1.40, 2)
        rr     = round((t1 - price) / max(price - stop, 0.01), 1)

        conds = s["conditions"]
        days  = s["days_since_gap"]
        pb    = s["pullback_pct"]
        vol_r = s["vol_ratio_today"]

        msg = (
            f"{emoji} *{ticker}* · {s.get('ep_type','EP')} · MAGNA {s['magna_score']}\n\n"
            f"💰 Preço entrada: `${price:.2f}`\n"
            f"📊 Gap original: `+{s['gap_pct']:.1f}%` ({s['gap_date']})\n"
            f"📉 Pullback: `{pb:.1f}%` em {days} dias\n"
            f"📦 Volume hoje: `{vol_r:.2f}×` do gap day (seco)\n\n"
            f"🛑 Stop: `${stop:.2f}` (-{stop_pct:.1f}%) ← mínima gap day\n"
            f"🎯 T1: `${t1:.2f}` (+20%) · T2: `${t2:.2f}` (+40%)\n"
            f"⚖️ R/R: `1:{rr}`\n\n"
            f"🌑 Sector: _{s.get('sector', '—')}_\n"
            f"📋 Sinal: *{s['signal_quality']}*"
        )
        send(msg)


# ─── RENDER STREAMLIT ─────────────────────────────────────────────────────────

def render_monitor_tab():
    """Renderiza a tab de monitorização no Streamlit."""
    try:
        import streamlit as st
    except ImportError:
        return

    st.markdown("## 📡 EP Pullback Monitor")
    st.markdown(
        "Candidatos EP em monitorização · Identifica a janela ideal de entrada no pullback"
    )

    status = get_monitor_status()
    n_mon = status["n_monitoring"]
    n_sig = status["n_signals"]

    # Métricas de topo
    m1, m2, m3 = st.columns(3)
    m1.metric("Em Monitorização", n_mon)
    m2.metric("Sinais Activos", n_sig,
              delta="entrada detectada" if n_sig > 0 else None)
    m3.metric("Expirados/Invalidados", len(status["recent_expired"]))

    # ── SINAIS ACTIVOS ────────────────────────────────────────────────────────
    if status["signals"]:
        st.divider()
        st.markdown("### 🔥 Sinais de Entrada Activos")
        for s in status["signals"]:
            _render_signal_card(s)

    # ── EM MONITORIZAÇÃO ──────────────────────────────────────────────────────
    st.divider()
    st.markdown(f"### 👁️ Em Monitorização ({n_mon})")

    if not status["monitoring"]:
        st.info(
            "Nenhum candidato em monitorização.\n\n"
            "Os candidatos EP com MAGNA ≥ 50, gap ≥ 15% e vol ≥ 5× são "
            "automaticamente adicionados pelo scan diário."
        )
    else:
        for m in status["monitoring"]:
            _render_monitor_card(m)

    # ── EXPIRADOS / INVALIDADOS ───────────────────────────────────────────────
    if status["recent_expired"]:
        with st.expander(f"📁 Recentes expirados/invalidados ({len(status['recent_expired'])})"):
            for e in status["recent_expired"]:
                status_icon = "⏱️" if e["status"] == "EXPIRED" else "❌"
                st.markdown(
                    f'{status_icon} **{e["ticker"]}** · gap {e["gap_date"]} '
                    f'· {e["status"]} · D{e["days_since_gap"]}'
                )

    st.caption("⚠️ Apenas para fins informativos. Não é aconselhamento financeiro.")


def _render_signal_card(s: dict):
    """Renderiza um card de sinal de entrada."""
    try:
        import streamlit as st
    except:
        return

    ticker  = s["ticker"]
    price   = s["current_price"] or 0
    stop    = s["gap_price_low"] or 0
    gap_pct = s["gap_pct"] or 0
    pb_pct  = s["pullback_pct"] or 0
    days    = s["days_since_gap"] or 0
    magna   = s["magna_score"] or 0
    quality = s["signal_quality"] or "MODERATE"
    vol_r   = s["vol_ratio_today"] or 0

    q_color = {"STRONG": "#00e87a", "MODERATE": "#f5c842", "WEAK": "#fb923c"}.get(quality, "#667a99")
    stop_pct = round((price - stop) / price * 100, 1) if price > 0 else 8
    t1 = round(price * 1.20, 2)
    t2 = round(price * 1.40, 2)
    t3 = round(price * 1.60, 2)
    rr = round((t1 - price) / max(price - stop, 0.01), 1)
    rr_color = "#00e87a" if rr >= 2 else "#f5c842" if rr >= 1.2 else "#ff5e5e"

    with st.expander(
        f"🔥 **{ticker}** · Sinal {quality} · Entrada: ${price:.2f} · MAGNA {magna}",
        expanded=True
    ):
        col_info, col_plan = st.columns(2)

        with col_info:
            st.markdown("##### 📊 Contexto do Sinal")
            st.markdown(
                f'<div style="background:#0d1422;border:1px solid #1e2d45;'
                f'border-radius:8px;padding:14px">'

                f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
                f'<span style="color:#667a99">Gap original</span>'
                f'<span style="color:#00e87a;font-family:monospace">+{gap_pct:.1f}%</span></div>'

                f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
                f'<span style="color:#667a99">Data do gap</span>'
                f'<span style="font-family:monospace">{s["gap_date"]}</span></div>'

                f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
                f'<span style="color:#667a99">Pullback desde pico</span>'
                f'<span style="color:#f5c842;font-family:monospace">-{pb_pct:.1f}%</span></div>'

                f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
                f'<span style="color:#667a99">Dias após EP</span>'
                f'<span style="font-family:monospace">D{days}</span></div>'

                f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
                f'<span style="color:#667a99">Volume hoje</span>'
                f'<span style="color:#00e87a;font-family:monospace">'
                f'{vol_r:.2f}× do gap day (SECO)</span></div>'

                f'<div style="border-top:1px solid #1e2d45;margin-top:8px;padding-top:8px">'
                f'<span style="color:{q_color};font-weight:700">● Sinal {quality}</span>'
                f'</div></div>',
                unsafe_allow_html=True
            )

            # Explicação
            explain = {
                "STRONG": (
                    f"O preço recuou {pb_pct:.0f}% desde o pico do gap day, com volume "
                    f"a secar para apenas {vol_r:.0%} do normal. O gap está intacto. "
                    "São as condições ideais que Pradeep descreve — "
                    "pullback com volume seco dentro do gap."
                ),
                "MODERATE": (
                    f"Pullback de {pb_pct:.0f}% com volume moderado ({vol_r:.0%} do gap day). "
                    "As condições estão cumpridas mas o sinal não é perfeito. "
                    "Vale monitorizar mais um dia para confirmar."
                ),
                "WEAK": (
                    f"As condições mínimas estão cumpridas mas o sinal é fraco. "
                    "Volume ainda elevado ou pullback fora da zona ideal. "
                    "Considera aguardar confirmação adicional."
                ),
            }.get(quality, "")
            st.info(explain)

        with col_plan:
            st.markdown("##### 📋 Plano de Trading")
            st.markdown(
                f'<div style="background:#0a1628;border:1px solid #1e3a5f;'
                f'border-radius:8px;padding:14px">'
                f'<div style="color:#667a99;font-size:0.72em;letter-spacing:1px;margin-bottom:10px">'
                f'PULLBACK ENTRY — Método Pradeep Bonde</div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Entrada (pullback)</span>'
                f'<span style="color:#e8edf5;font-weight:600;font-family:monospace">${price:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Stop (mínima gap day — fixo)</span>'
                f'<span style="color:#ff5e5e;font-weight:600;font-family:monospace">'
                f'${stop:.2f} (-{stop_pct:.1f}%)</span></div>'

                f'<div style="border-top:1px solid #1e2d45;margin:8px 0"></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">T1 (+20%) → vender 25%</span>'
                f'<span style="color:#00e87a;font-family:monospace">${t1:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">T2 (+40%) → vender 25%</span>'
                f'<span style="color:#00e87a;font-family:monospace">${t2:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">T3 (+60%) → vender 25%</span>'
                f'<span style="color:#00e87a;font-family:monospace">${t3:.2f}</span></div>'

                f'<div style="display:flex;justify-content:space-between;margin:6px 0">'
                f'<span style="color:#667a99;font-size:0.85em">Trailing → 25% restantes</span>'
                f'<span style="color:#a78bfa;font-family:monospace">stop móvel</span></div>'

                f'<div style="border-top:1px solid #1e2d45;margin-top:8px;padding-top:8px;'
                f'display:flex;justify-content:space-between">'
                f'<span style="color:#667a99;font-size:0.82em">R/R (entrada pullback vs T1)</span>'
                f'<span style="color:{rr_color};font-weight:600">1:{rr}</span>'
                f'</div></div>',
                unsafe_allow_html=True
            )

            st.markdown(
                f'<div style="background:#00e87a10;border-left:3px solid #00e87a40;'
                f'padding:8px 12px;border-radius:4px;margin:8px 0;font-size:0.82em;color:#c9d1e0">'
                f'<b>Vantagem do pullback:</b> Entraste a ${price:.2f} em vez de ${round(s.get("gap_price_high", price*1.1), 2):.2f} '
                f'(pico do gap). O stop é o mesmo — risco menor, mesmo potencial.</div>',
                unsafe_allow_html=True
            )


def _render_monitor_card(m: dict):
    """Renderiza um card de candidato em monitorização."""
    try:
        import streamlit as st
    except:
        return

    ticker  = m["ticker"]
    magna   = m["magna_score"] or 0
    days    = m["days_since_gap"] or 0
    pb_pct  = m["pullback_pct"] or 0
    vol_r   = m["vol_ratio_today"] or 1.0
    gap_pct = m["gap_pct"] or 0
    price   = m["current_price"] or 0
    stop    = m["gap_price_low"] or 0
    ep_type = m["ep_type"] or ""
    sector  = m["sector"] or ""

    # Progresso das condições
    try:
        conds = json.loads(m["conditions_met"]) if m["conditions_met"] else {}
    except:
        conds = {}

    n_met   = conds.get("n_met", 0)
    details = conds.get("details", {})

    # Barra de progresso visual
    progress_color = "#00e87a" if n_met == 4 else "#f5c842" if n_met >= 2 else "#ff5e5e"
    days_remaining = max(0, ENTRY_CONDITIONS["days_max"] - days)

    # Condições individuais
    def cond_icon(met): return "✅" if met else "⏳"

    with st.expander(
        f"👁️ **{ticker}** · D{days}/{ENTRY_CONDITIONS['days_max']} · "
        f"{n_met}/4 condições · MAGNA {magna}",
        expanded=False
    ):
        col1, col2 = st.columns([1, 1])

        with col1:
            st.markdown("##### Estado das Condições")
            st.markdown(
                f'<div style="background:#0d1422;border:1px solid #1e2d45;'
                f'border-radius:8px;padding:14px">'

                f'<div style="margin-bottom:10px">'
                f'<div style="display:flex;justify-content:space-between;margin-bottom:4px">'
                f'<span style="color:#667a99;font-size:0.8em">Progresso</span>'
                f'<span style="color:{progress_color};font-weight:700">{n_met}/4 condições</span>'
                f'</div>'
                f'<div style="background:#1e2d45;border-radius:4px;height:6px">'
                f'<div style="background:{progress_color};width:{n_met*25}%;height:6px;'
                f'border-radius:4px"></div></div></div>'

                f'<div style="font-size:0.85em">'

                f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
                f'<span>{cond_icon(conds.get("cond_days"))} Dias após EP</span>'
                f'<span style="font-family:monospace;color:#c9d1e0">D{days} '
                f'(mín {ENTRY_CONDITIONS["days_min"]})</span></div>'

                f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
                f'<span>{cond_icon(conds.get("cond_pullback"))} Pullback</span>'
                f'<span style="font-family:monospace;color:#c9d1e0">'
                f'-{pb_pct:.1f}% '
                f'(zona: {ENTRY_CONDITIONS["pullback_min_pct"]}-{ENTRY_CONDITIONS["pullback_max_pct"]}%)'
                f'</span></div>'

                f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
                f'<span>{cond_icon(conds.get("cond_vol_dry"))} Volume seco</span>'
                f'<span style="font-family:monospace;color:#c9d1e0">'
                f'{vol_r:.2f}× '
                f'(precisa <{ENTRY_CONDITIONS["vol_dry_threshold"]:.0%})</span></div>'

                f'<div style="display:flex;justify-content:space-between;padding:4px 0">'
                f'<span>{cond_icon(conds.get("cond_intact"))} Gap intacto</span>'
                f'<span style="font-family:monospace;color:#c9d1e0">'
                f'${price:.2f} vs stop ${stop:.2f}</span></div>'

                f'</div>'
                f'<div style="border-top:1px solid #1e2d45;margin-top:8px;padding-top:8px;'
                f'color:#667a99;font-size:0.8em">⏱️ {days_remaining} dias restantes</div>'
                f'</div>',
                unsafe_allow_html=True
            )

        with col2:
            st.markdown("##### Dados do EP")
            st.markdown(
                f'**Sector:** {sector}  \n'
                f'**Tipo EP:** `{ep_type}`  \n'
                f'**Gap day:** `{m["gap_date"]}`  \n'
                f'**Gap %:** `+{gap_pct:.1f}%`  \n'
                f'**Preço actual:** `${price:.2f}`  \n'
                f'**Stop anchor:** `${stop:.2f}`'
            )

            # O que falta para o sinal
            missing = []
            if not conds.get("cond_days"):
                missing.append(f"Aguardar D{ENTRY_CONDITIONS['days_min']} (hoje é D{days})")
            if not conds.get("cond_pullback"):
                if pb_pct < ENTRY_CONDITIONS["pullback_min_pct"]:
                    missing.append(f"Pullback insuficiente ({pb_pct:.1f}% — precisa ≥{ENTRY_CONDITIONS['pullback_min_pct']}%)")
                else:
                    missing.append(f"Pullback excessivo ({pb_pct:.1f}%)")
            if not conds.get("cond_vol_dry"):
                missing.append(f"Volume ainda elevado ({vol_r:.2f}× — precisa <{ENTRY_CONDITIONS['vol_dry_threshold']:.0%})")
            if not conds.get("cond_intact"):
                missing.append("Preço próximo do stop — gap em risco")

            if missing:
                st.markdown("**O que falta:**")
                for msg in missing:
                    st.markdown(
                        f'<div style="background:#f5c84210;border-left:2px solid #f5c84240;'
                        f'padding:4px 10px;border-radius:3px;font-size:0.82em;margin:2px 0;'
                        f'color:#c9d1e0">{msg}</div>',
                        unsafe_allow_html=True
                    )
            else:
                st.success("Todas as condições cumpridas — sinal gerado!")
