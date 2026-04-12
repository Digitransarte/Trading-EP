"""
EP Knowledge Base
=================
Aprende com cada backtest e expõe insights para o scanner.

Arquitectura:
  - SQLite local (sem custos, sem dependências externas)
  - 3 tabelas: backtest_runs, trade_log, kb_insights
  - Insights derivados automaticamente de cada run
  - API simples para o scanner consumir os insights

Fluxo:
  Backtest → save_run() → derive_insights() → scanner usa get_scanner_adjustments()
"""

import streamlit as st
import sqlite3
import json
import os
import numpy as np
import pandas as pd
from datetime import datetime, date
from typing import Optional

KB_DB = "ep_knowledge_base.db"


# ─── SCHEMA ───────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date    TEXT NOT NULL,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    params      TEXT NOT NULL,   -- JSON: scan + trade params
    metrics     TEXT NOT NULL,   -- JSON: summary metrics
    n_events    INTEGER,
    n_trades    INTEGER,
    win_rate    REAL,
    profit_factor REAL,
    avg_return  REAL,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS trade_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES backtest_runs(id),
    ticker          TEXT,
    ep_date         TEXT,
    entry_date      TEXT,
    entry_price     REAL,
    gap_pct         REAL,
    vol_ratio       REAL,
    ep_volume       INTEGER,
    stop_pct        REAL,
    exit_date       TEXT,
    exit_reason     TEXT,
    holding_days    INTEGER,
    tranches_hit    INTEGER,
    max_gain_pct    REAL,
    total_return_pct REAL,
    result          TEXT          -- WIN / LOSS
);

CREATE TABLE IF NOT EXISTS kb_insights (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    category    TEXT NOT NULL,   -- gap_range / vol_range / holding / general
    key         TEXT NOT NULL,   -- e.g. "gap_10_20"
    value       TEXT NOT NULL,   -- JSON with stats
    confidence  REAL,            -- 0-1, based on sample size
    n_samples   INTEGER,
    description TEXT
);
"""


# ─── INIT ─────────────────────────────────────────────────────────────────────

def init_kb():
    conn = sqlite3.connect(KB_DB)
    for stmt in SCHEMA.strip().split(";\n\n"):
        if stmt.strip():
            conn.execute(stmt)
    conn.commit()
    conn.close()


# ─── SAVE BACKTEST RUN ────────────────────────────────────────────────────────

def save_run(
    start_date: str,
    end_date: str,
    params: dict,
    metrics: dict,
    trades: list,
    notes: str = ""
) -> int:
    """
    Persist a complete backtest run to the KB.
    Returns the run_id.
    """
    conn = sqlite3.connect(KB_DB)

    # Insert run summary
    cur = conn.execute(
        """INSERT INTO backtest_runs
           (run_date, start_date, end_date, params, metrics,
            n_events, n_trades, win_rate, profit_factor, avg_return, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now().isoformat(),
            start_date, end_date,
            json.dumps(params),
            json.dumps(metrics),
            metrics.get("total_trades", 0),
            len(trades),
            metrics.get("win_rate", 0),
            metrics.get("profit_factor", 0),
            metrics.get("avg_return_pct", 0),
            notes,
        )
    )
    run_id = cur.lastrowid

    # Insert individual trades
    valid = [t for t in trades if t.get("result") in ("WIN", "LOSS")]
    for t in valid:
        conn.execute(
            """INSERT INTO trade_log
               (run_id, ticker, ep_date, entry_date, entry_price,
                gap_pct, vol_ratio, ep_volume, stop_pct,
                exit_date, exit_reason, holding_days,
                tranches_hit, max_gain_pct, total_return_pct, result)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                t.get("ticker"), t.get("ep_date"), t.get("entry_date"),
                t.get("entry_price"), t.get("gap_pct"), t.get("vol_ratio"),
                t.get("ep_volume"), t.get("stop_pct"),
                t.get("exit_date"), t.get("exit_reason"),
                t.get("holding_days"), t.get("tranches_hit"),
                t.get("max_gain_pct"), t.get("total_return_pct"),
                t.get("result"),
            )
        )

    conn.commit()
    conn.close()
    return run_id


# ─── DERIVE INSIGHTS ──────────────────────────────────────────────────────────

def derive_insights(run_id: Optional[int] = None):
    """
    Analyse all trades in the KB (or just one run) and derive insights.
    Replaces old insights with fresh ones.
    """
    conn = sqlite3.connect(KB_DB)

    # Load trades (all or filtered by run)
    query = "SELECT * FROM trade_log WHERE result IN ('WIN','LOSS')"
    if run_id:
        query += f" AND run_id = {run_id}"

    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        return []

    insights = []
    now = datetime.now().isoformat()

    # ── 1. Gap range performance ──────────────────────────────────────────────
    gap_bins = [
        ("gap_5_8",   5,  8,  "Gap 5-8%"),
        ("gap_8_15",  8,  15, "Gap 8-15%"),
        ("gap_15_25", 15, 25, "Gap 15-25%"),
        ("gap_25_plus",25,999,"Gap 25%+"),
    ]
    for key, lo, hi, label in gap_bins:
        sub = df[(df["gap_pct"] >= lo) & (df["gap_pct"] < hi)]
        if len(sub) < 3:
            continue
        wins = (sub["result"] == "WIN").sum()
        stats = {
            "win_rate":    round(wins / len(sub) * 100, 1),
            "avg_return":  round(sub["total_return_pct"].mean(), 2),
            "avg_win":     round(sub[sub["result"]=="WIN"]["total_return_pct"].mean(), 2) if wins else 0,
            "avg_loss":    round(sub[sub["result"]=="LOSS"]["total_return_pct"].mean(), 2) if (len(sub)-wins) else 0,
            "n":           len(sub),
            "label":       label,
        }
        confidence = min(len(sub) / 30, 1.0)  # 30 trades = full confidence
        insights.append({
            "category": "gap_range", "key": key,
            "value": stats, "confidence": confidence,
            "n_samples": len(sub),
            "description": f"{label}: {stats['win_rate']}% win rate · avg {stats['avg_return']:+.1f}%",
            "created_at": now,
        })

    # ── 2. Volume ratio performance ───────────────────────────────────────────
    vol_bins = [
        ("vol_1_3",   1.5, 3,   "Vol 1.5-3×"),
        ("vol_3_5",   3,   5,   "Vol 3-5×"),
        ("vol_5_10",  5,   10,  "Vol 5-10×"),
        ("vol_10plus",10,  9999,"Vol 10×+"),
    ]
    for key, lo, hi, label in vol_bins:
        sub = df[(df["vol_ratio"] >= lo) & (df["vol_ratio"] < hi)]
        if len(sub) < 3:
            continue
        wins = (sub["result"] == "WIN").sum()
        stats = {
            "win_rate":   round(wins / len(sub) * 100, 1),
            "avg_return": round(sub["total_return_pct"].mean(), 2),
            "n":          len(sub),
            "label":      label,
        }
        confidence = min(len(sub) / 30, 1.0)
        insights.append({
            "category": "vol_range", "key": key,
            "value": stats, "confidence": confidence,
            "n_samples": len(sub),
            "description": f"{label}: {stats['win_rate']}% win rate · avg {stats['avg_return']:+.1f}%",
            "created_at": now,
        })

    # ── 3. Holding period sweet spot ──────────────────────────────────────────
    hold_bins = [
        ("hold_1_5",   1,  5,  "1-5 dias"),
        ("hold_6_10",  6,  10, "6-10 dias"),
        ("hold_11_20", 11, 20, "11-20 dias"),
        ("hold_20plus",20, 999,"20+ dias"),
    ]
    for key, lo, hi, label in hold_bins:
        sub = df[(df["holding_days"] >= lo) & (df["holding_days"] < hi)]
        if len(sub) < 3:
            continue
        wins = (sub["result"] == "WIN").sum()
        stats = {
            "win_rate":   round(wins / len(sub) * 100, 1),
            "avg_return": round(sub["total_return_pct"].mean(), 2),
            "n":          len(sub),
            "label":      label,
        }
        confidence = min(len(sub) / 20, 1.0)
        insights.append({
            "category": "holding", "key": key,
            "value": stats, "confidence": confidence,
            "n_samples": len(sub),
            "description": f"{label}: {stats['win_rate']}% win rate · avg {stats['avg_return']:+.1f}%",
            "created_at": now,
        })

    # ── 4. Stop size analysis ─────────────────────────────────────────────────
    stop_bins = [
        ("stop_tight",  0, 5,  "Stop <5%"),
        ("stop_medium", 5, 10, "Stop 5-10%"),
        ("stop_wide",   10, 99,"Stop >10%"),
    ]
    for key, lo, hi, label in stop_bins:
        sub = df[(df["stop_pct"] >= lo) & (df["stop_pct"] < hi)]
        if len(sub) < 3:
            continue
        wins = (sub["result"] == "WIN").sum()
        stopped = (sub["exit_reason"].str.contains("STOP", na=False)).sum()
        stats = {
            "win_rate":     round(wins / len(sub) * 100, 1),
            "avg_return":   round(sub["total_return_pct"].mean(), 2),
            "stopped_out_pct": round(stopped / len(sub) * 100, 1),
            "n":            len(sub),
            "label":        label,
        }
        confidence = min(len(sub) / 20, 1.0)
        insights.append({
            "category": "stop_size", "key": key,
            "value": stats, "confidence": confidence,
            "n_samples": len(sub),
            "description": f"{label}: {stats['win_rate']}% win rate · {stats['stopped_out_pct']}% stop outs",
            "created_at": now,
        })

    # ── 5. Best & worst tickers (recurrence) ──────────────────────────────────
    ticker_stats = (
        df.groupby("ticker")
        .agg(
            n=("result", "count"),
            wins=("result", lambda x: (x=="WIN").sum()),
            avg_ret=("total_return_pct", "mean"),
        )
        .reset_index()
    )
    ticker_stats["win_rate"] = ticker_stats["wins"] / ticker_stats["n"] * 100
    ticker_stats = ticker_stats[ticker_stats["n"] >= 2]  # min 2 appearances

    if not ticker_stats.empty:
        best_tickers = ticker_stats.nlargest(5, "avg_ret")[
            ["ticker", "n", "win_rate", "avg_ret"]
        ].to_dict("records")
        worst_tickers = ticker_stats.nsmallest(5, "avg_ret")[
            ["ticker", "n", "win_rate", "avg_ret"]
        ].to_dict("records")

        insights.append({
            "category": "ticker_performance", "key": "best_tickers",
            "value": {"tickers": best_tickers},
            "confidence": min(len(ticker_stats) / 20, 1.0),
            "n_samples": len(ticker_stats),
            "description": f"Top tickers por retorno médio ({len(best_tickers)} stocks)",
            "created_at": now,
        })
        insights.append({
            "category": "ticker_performance", "key": "worst_tickers",
            "value": {"tickers": worst_tickers},
            "confidence": min(len(ticker_stats) / 20, 1.0),
            "n_samples": len(ticker_stats),
            "description": f"Piores tickers por retorno médio ({len(worst_tickers)} stocks)",
            "created_at": now,
        })

    # ── 6. Macro insight: optimal thresholds ──────────────────────────────────
    # Find gap% threshold where performance improves most
    gap_thresholds = [6, 8, 10, 12, 15, 20]
    threshold_stats = []
    for thresh in gap_thresholds:
        sub = df[df["gap_pct"] >= thresh]
        if len(sub) < 5:
            continue
        wins = (sub["result"] == "WIN").sum()
        threshold_stats.append({
            "min_gap": thresh,
            "n": len(sub),
            "win_rate": round(wins / len(sub) * 100, 1),
            "avg_return": round(sub["total_return_pct"].mean(), 2),
        })

    if threshold_stats:
        # Best threshold = highest avg_return with n >= 5
        best_thresh = max(threshold_stats, key=lambda x: x["avg_return"])
        insights.append({
            "category": "optimal_params", "key": "best_gap_threshold",
            "value": {
                "recommended_min_gap": best_thresh["min_gap"],
                "all_thresholds": threshold_stats,
            },
            "confidence": min(len(df) / 50, 1.0),
            "n_samples": len(df),
            "description": f"Gap mínimo recomendado: {best_thresh['min_gap']}% (avg {best_thresh['avg_return']:+.1f}%)",
            "created_at": now,
        })

    # Vol threshold
    vol_thresholds = [2, 3, 5, 7, 10]
    vol_threshold_stats = []
    for thresh in vol_thresholds:
        sub = df[df["vol_ratio"] >= thresh]
        if len(sub) < 5:
            continue
        wins = (sub["result"] == "WIN").sum()
        vol_threshold_stats.append({
            "min_vol_ratio": thresh,
            "n": len(sub),
            "win_rate": round(wins / len(sub) * 100, 1),
            "avg_return": round(sub["total_return_pct"].mean(), 2),
        })

    if vol_threshold_stats:
        best_vol = max(vol_threshold_stats, key=lambda x: x["avg_return"])
        insights.append({
            "category": "optimal_params", "key": "best_vol_threshold",
            "value": {
                "recommended_min_vol_ratio": best_vol["min_vol_ratio"],
                "all_thresholds": vol_threshold_stats,
            },
            "confidence": min(len(df) / 50, 1.0),
            "n_samples": len(df),
            "description": f"Vol ratio mínimo recomendado: {best_vol['min_vol_ratio']}× (avg {best_vol['avg_return']:+.1f}%)",
            "created_at": now,
        })

    # ── Persist insights ──────────────────────────────────────────────────────
    conn = sqlite3.connect(KB_DB)
    # Clear old insights of same categories (replace with fresh)
    categories = list({i["category"] for i in insights})
    for cat in categories:
        conn.execute("DELETE FROM kb_insights WHERE category=?", (cat,))

    for ins in insights:
        conn.execute(
            """INSERT INTO kb_insights
               (created_at, category, key, value, confidence, n_samples, description)
               VALUES (?,?,?,?,?,?,?)""",
            (
                ins["created_at"], ins["category"], ins["key"],
                json.dumps(ins["value"]), ins["confidence"],
                ins["n_samples"], ins["description"],
            )
        )
    conn.commit()
    conn.close()

    return insights


# ─── QUERY KB ─────────────────────────────────────────────────────────────────

def get_all_insights() -> list:
    conn = sqlite3.connect(KB_DB)
    rows = conn.execute(
        "SELECT category, key, value, confidence, n_samples, description, created_at "
        "FROM kb_insights ORDER BY category, confidence DESC"
    ).fetchall()
    conn.close()
    return [
        {
            "category": r[0], "key": r[1],
            "value": json.loads(r[2]),
            "confidence": r[3], "n_samples": r[4],
            "description": r[5], "created_at": r[6],
        }
        for r in rows
    ]


def get_scanner_adjustments() -> dict:
    """
    Returns a dict that the scanner can use to adjust MAGNA scores.
    Based on KB insights — only uses high-confidence insights (>0.4).

    Format:
    {
      "min_gap_recommended": 10,
      "min_vol_ratio_recommended": 5,
      "gap_multipliers": {"5_8": 0.7, "8_15": 1.0, "15_25": 1.2, "25_plus": 1.3},
      "vol_multipliers": {...},
      "avoided_tickers": ["XXXX", ...],
      "data_available": True/False,
      "total_trades_in_kb": N,
    }
    """
    conn = sqlite3.connect(KB_DB)
    total_trades = conn.execute(
        "SELECT COUNT(*) FROM trade_log WHERE result IN ('WIN','LOSS')"
    ).fetchone()[0]

    insights_raw = conn.execute(
        "SELECT category, key, value, confidence FROM kb_insights WHERE confidence >= 0.3"
    ).fetchall()
    conn.close()

    if not insights_raw:
        return {"data_available": False, "total_trades_in_kb": total_trades}

    insights = {
        (r[0], r[1]): {"value": json.loads(r[2]), "confidence": r[3]}
        for r in insights_raw
    }

    # Build gap multipliers
    gap_multipliers = {}
    gap_map = {
        "gap_5_8":    "5_8",
        "gap_8_15":   "8_15",
        "gap_15_25":  "15_25",
        "gap_25_plus":"25_plus",
    }
    gap_returns = {}
    for kb_key, label in gap_map.items():
        ins = insights.get(("gap_range", kb_key))
        if ins:
            avg_ret = ins["value"].get("avg_return", 0)
            gap_returns[label] = avg_ret

    if gap_returns:
        max_ret = max(gap_returns.values()) if gap_returns else 1
        baseline = np.mean(list(gap_returns.values()))
        for label, ret in gap_returns.items():
            # Multiplier: 0.5 to 1.5 range based on relative performance
            mult = 1.0 + (ret - baseline) / max(abs(baseline) + 0.01, 10) * 0.5
            gap_multipliers[label] = round(max(0.5, min(1.5, mult)), 2)

    # Build vol multipliers
    vol_multipliers = {}
    vol_map = {
        "vol_1_3":    "1_3",
        "vol_3_5":    "3_5",
        "vol_5_10":   "5_10",
        "vol_10plus": "10_plus",
    }
    vol_returns = {}
    for kb_key, label in vol_map.items():
        ins = insights.get(("vol_range", kb_key))
        if ins:
            avg_ret = ins["value"].get("avg_return", 0)
            vol_returns[label] = avg_ret

    if vol_returns:
        baseline = np.mean(list(vol_returns.values()))
        for label, ret in vol_returns.items():
            mult = 1.0 + (ret - baseline) / max(abs(baseline) + 0.01, 10) * 0.5
            vol_multipliers[label] = round(max(0.5, min(1.5, mult)), 2)

    # Recommended thresholds
    min_gap_rec = None
    ins_gap = insights.get(("optimal_params", "best_gap_threshold"))
    if ins_gap and ins_gap["confidence"] >= 0.4:
        min_gap_rec = ins_gap["value"].get("recommended_min_gap")

    min_vol_rec = None
    ins_vol = insights.get(("optimal_params", "best_vol_threshold"))
    if ins_vol and ins_vol["confidence"] >= 0.4:
        min_vol_rec = ins_vol["value"].get("recommended_min_vol_ratio")

    # Tickers to avoid (worst performers, multiple appearances)
    avoided_tickers = []
    ins_worst = insights.get(("ticker_performance", "worst_tickers"))
    if ins_worst:
        avoided_tickers = [
            t["ticker"] for t in ins_worst["value"].get("tickers", [])
            if t.get("avg_ret", 0) < -5 and t.get("n", 0) >= 3
        ]

    return {
        "data_available":            True,
        "total_trades_in_kb":        total_trades,
        "min_gap_recommended":       min_gap_rec,
        "min_vol_ratio_recommended": min_vol_rec,
        "gap_multipliers":           gap_multipliers,
        "vol_multipliers":           vol_multipliers,
        "avoided_tickers":           avoided_tickers,
    }


def get_run_history() -> list:
    conn = sqlite3.connect(KB_DB)
    rows = conn.execute(
        "SELECT id, run_date, start_date, end_date, n_trades, "
        "win_rate, profit_factor, avg_return, notes "
        "FROM backtest_runs ORDER BY run_date DESC"
    ).fetchall()
    conn.close()
    return [
        {
            "id": r[0], "run_date": r[1][:10],
            "start_date": r[2], "end_date": r[3],
            "n_trades": r[4], "win_rate": r[5],
            "profit_factor": r[6], "avg_return": r[7],
            "notes": r[8],
        }
        for r in rows
    ]


def delete_run(run_id: int):
    conn = sqlite3.connect(KB_DB)
    conn.execute("DELETE FROM trade_log WHERE run_id=?", (run_id,))
    conn.execute("DELETE FROM backtest_runs WHERE id=?", (run_id,))
    conn.commit()
    conn.close()


# ─── STREAMLIT UI ─────────────────────────────────────────────────────────────



def get_similar_setups(gap_pct: float, ep_type: str = None,
                       sector: str = None, n: int = 20) -> dict:
    """
    Consulta a KB para encontrar trades similares ao candidato actual.
    Usado para mostrar "o que aconteceu em casos parecidos".

    Retorna estatísticas e exemplos concretos.
    """
    try:
        conn = sqlite3.connect(KB_DB)
        conn.row_factory = sqlite3.Row

        # Gap bucket: ±5% do gap actual
        gap_lo = max(gap_pct * 0.7, 8)
        gap_hi = gap_pct * 1.4

        # Query base por gap
        rows = conn.execute(
            """SELECT ticker, gap_pct, vol_ratio, result, total_return_pct,
                      holding_days, exit_reason, ep_date
               FROM trade_log
               WHERE result IN ('WIN','LOSS')
               AND gap_pct >= ? AND gap_pct <= ?
               ORDER BY ep_date DESC LIMIT ?""",
            (gap_lo, gap_hi, n * 3)
        ).fetchall()

        conn.close()

        if not rows:
            return {"available": False, "n": 0}

        trades = [dict(r) for r in rows]
        total  = len(trades)
        wins   = sum(1 for t in trades if t["result"] == "WIN")
        rets   = [t["total_return_pct"] for t in trades if t["total_return_pct"] is not None]

        avg_ret  = round(sum(rets) / len(rets), 1) if rets else 0
        win_rate = round(wins / total * 100, 1) if total else 0
        avg_win  = round(sum(r for r in rets if r > 0) / max(wins, 1), 1)
        avg_loss = round(sum(r for r in rets if r <= 0) / max(total - wins, 1), 1)
        best     = max(rets) if rets else 0
        worst    = min(rets) if rets else 0

        # Dias médios até stop
        stopped = [t for t in trades if t.get("exit_reason") == "STOP_HIT"]
        avg_stop_day = round(
            sum(t["holding_days"] for t in stopped if t["holding_days"]) / len(stopped), 1
        ) if stopped else None

        # Exemplos mais recentes (top 3 wins + top 3 losses)
        top_wins   = sorted([t for t in trades if t["result"] == "WIN"],
                            key=lambda x: x["total_return_pct"] or 0, reverse=True)[:3]
        top_losses = sorted([t for t in trades if t["result"] == "LOSS"],
                            key=lambda x: x["total_return_pct"] or 0)[:2]

        return {
            "available":    True,
            "n":            total,
            "win_rate":     win_rate,
            "avg_return":   avg_ret,
            "avg_win":      avg_win,
            "avg_loss":     avg_loss,
            "best":         round(best, 1),
            "worst":        round(worst, 1),
            "avg_stop_day": avg_stop_day,
            "gap_range":    f"{gap_lo:.0f}-{gap_hi:.0f}%",
            "examples_win":  top_wins,
            "examples_loss": top_losses,
        }

    except Exception as e:
        return {"available": False, "error": str(e)}

def render_kb_page():
    st.set_page_config(page_title="EP Knowledge Base", page_icon="🧠", layout="wide")

    st.markdown("""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;500;600&display=swap');
      body, .stApp { background:#070b14; color:#c9d1e0; font-family:'DM Sans',sans-serif; }
      h1,h2,h3 { color:#e8edf5; font-family:'Space Mono',monospace; }
      .insight-card { background:#0d1422; border:1px solid #1e2d45; border-radius:8px;
                      padding:14px 18px; margin:6px 0; }
      .insight-cat  { color:#667a99; font-size:0.7em; text-transform:uppercase;
                      letter-spacing:1px; font-family:'Space Mono',monospace; }
      .insight-desc { color:#e8edf5; font-size:0.95em; margin-top:4px; }
      .conf-bar     { height:4px; border-radius:2px; background:#1e2d45; margin-top:8px; }
      .conf-fill    { height:4px; border-radius:2px; }
      .green  { color:#00e87a; }
      .yellow { color:#f5c842; }
      .red    { color:#ff5e5e; }
      .tag { display:inline-block; border-radius:3px; padding:2px 8px; font-size:0.72em;
             font-weight:600; margin:2px; font-family:'Space Mono',monospace; }
      .tag-rec  { background:#00e87a15; color:#00e87a; border:1px solid #00e87a30; }
      .tag-warn { background:#f5c84215; color:#f5c842; border:1px solid #f5c84230; }
      .tag-bad  { background:#ff5e5e15; color:#ff5e5e; border:1px solid #ff5e5e30; }
      .stButton>button { background:#00e87a15; color:#00e87a; border:1px solid #00e87a35;
                         border-radius:6px; padding:8px 22px; font-size:13px;
                         font-family:'Space Mono',monospace; }
      .stButton>button:hover { background:#00e87a25; }
    </style>
    """, unsafe_allow_html=True)

    init_kb()

    st.markdown("## 🧠 EP Knowledge Base")
    st.markdown("O sistema aprende com cada backtest e melhora automaticamente os critérios do scanner.")
    st.divider()

    adj = get_scanner_adjustments()
    total_trades = adj.get("total_trades_in_kb", 0)

    # ── Status banner ──────────────────────────────────────────────────────────
    if not adj.get("data_available"):
        st.info(
            "📭 Knowledge Base vazia — corre o Backtesting Engine primeiro e guarda os resultados aqui."
        )
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Trades na KB", total_trades)
        c2.metric(
            "Gap recomendado",
            f"{adj.get('min_gap_recommended', '—')}%" if adj.get('min_gap_recommended') else "dados insuf."
        )
        c3.metric(
            "Vol ratio rec.",
            f"{adj.get('min_vol_ratio_recommended', '—')}×" if adj.get('min_vol_ratio_recommended') else "dados insuf."
        )
        avoided = adj.get("avoided_tickers", [])
        c4.metric("Tickers a evitar", len(avoided))

    st.divider()

    tabs = st.tabs(["📥 Importar Backtest", "💡 Insights", "⚙️ Ajustes Scanner", "📋 Histórico de Runs"])

    # ── TAB 1: Import ──────────────────────────────────────────────────────────
    with tabs[0]:
        st.markdown("### Importar resultados de backtest")
        st.markdown(
            "Após correr o **Backtesting Engine**, copia os dados para aqui. "
            "A KB aprende automaticamente com cada run."
        )

        col_form, col_info = st.columns([2, 1])

        with col_form:
            with st.form("import_form"):
                st.markdown("**Dados do backtest**")
                start_d = st.text_input("Data início (YYYY-MM-DD)", placeholder="2024-01-01")
                end_d   = st.text_input("Data fim (YYYY-MM-DD)", placeholder="2024-06-30")
                notes   = st.text_area("Notas (opcional)", placeholder="Gap ≥ 8%, Vol 3×, Stop 8%...")

                st.markdown("**Colar JSON dos resultados**")
                st.caption(
                    "No Backtesting Engine, abre 'Dados brutos' e copia o JSON completo "
                    "do session_state. Em alternativa, usa a integração directa abaixo."
                )
                json_input = st.text_area(
                    "JSON do backtest (metrics + trades)",
                    height=150,
                    placeholder='{"metrics": {...}, "trades": [...], "params": {...}}'
                )

                submitted = st.form_submit_button("💾 Guardar na KB")

            if submitted:
                if not json_input.strip():
                    st.error("JSON vazio")
                else:
                    try:
                        data = json.loads(json_input)
                        metrics = data.get("metrics", {})
                        trades  = data.get("trades", [])
                        params  = data.get("params", {})

                        if not metrics or not trades:
                            st.error("JSON inválido — precisa de 'metrics' e 'trades'")
                        else:
                            run_id = save_run(start_d, end_d, params, metrics, trades, notes)
                            insights = derive_insights()
                            st.success(f"✅ Run #{run_id} guardado · {len(insights)} insights derivados")
                            st.rerun()
                    except json.JSONDecodeError as e:
                        st.error(f"JSON inválido: {e}")

        with col_info:
            st.markdown("**Como integrar directamente**")
            st.code("""
# No backtest_ep.py, após correr:
from knowledge_base import save_run, derive_insights

run_id = save_run(
    start_date = str(start_date),
    end_date   = str(end_date),
    params     = {**scan_params, **trade_params},
    metrics    = results["metrics"],
    trades     = results["trades"],
    notes      = "Run automático"
)
derive_insights()
            """, language="python")

            st.markdown("**Formato do JSON**")
            st.json({
                "metrics": {
                    "win_rate": 55.0,
                    "profit_factor": 1.4,
                    "avg_return_pct": 3.2,
                    "total_trades": 40,
                },
                "trades": [
                    {
                        "ticker": "AAPL",
                        "ep_date": "2024-03-15",
                        "gap_pct": 12.5,
                        "vol_ratio": 4.2,
                        "total_return_pct": 18.3,
                        "result": "WIN",
                        "holding_days": 8,
                    }
                ],
                "params": {"min_gap": 8, "stop_pct": 8},
            }, expanded=False)

    # ── TAB 2: Insights ────────────────────────────────────────────────────────
    with tabs[1]:
        st.markdown("### Insights derivados dos backtests")

        insights = get_all_insights()
        if not insights:
            st.info("Sem insights ainda — importa pelo menos um backtest.")
        else:
            # Group by category
            categories = {}
            for ins in insights:
                cat = ins["category"]
                categories.setdefault(cat, []).append(ins)

            cat_labels = {
                "gap_range":          "📊 Performance por Gap%",
                "vol_range":          "📈 Performance por Vol Ratio",
                "holding":            "⏱️ Holding Period óptimo",
                "stop_size":          "🛑 Análise de Stops",
                "ticker_performance": "🎯 Performance por Ticker",
                "optimal_params":     "⚙️ Parâmetros óptimos recomendados",
            }

            for cat, label in cat_labels.items():
                cat_insights = categories.get(cat, [])
                if not cat_insights:
                    continue

                st.markdown(f"#### {label}")

                if cat in ("gap_range", "vol_range", "holding", "stop_size"):
                    # Table view
                    rows = []
                    for ins in cat_insights:
                        v = ins["value"]
                        conf_pct = int(ins["confidence"] * 100)
                        conf_str = f"{'█' * (conf_pct // 10)}{'░' * (10 - conf_pct // 10)} {conf_pct}%"
                        rows.append({
                            "Label":        v.get("label", ins["key"]),
                            "N":            ins["n_samples"],
                            "Win Rate":     f"{v.get('win_rate', 0):.1f}%",
                            "Avg Return":   f"{v.get('avg_return', 0):+.1f}%",
                            "Avg Win":      f"{v.get('avg_win', 0):+.1f}%" if v.get("avg_win") else "—",
                            "Avg Loss":     f"{v.get('avg_loss', 0):.1f}%" if v.get("avg_loss") else "—",
                            "Confiança":    conf_str,
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

                elif cat == "ticker_performance":
                    col_b, col_w = st.columns(2)
                    for ins in cat_insights:
                        tickers = ins["value"].get("tickers", [])
                        if ins["key"] == "best_tickers":
                            with col_b:
                                st.markdown("🏆 **Melhores tickers**")
                                for t in tickers:
                                    wr = t.get("win_rate", 0)
                                    ar = t.get("avg_ret", 0)
                                    color = "green" if ar > 0 else "red"
                                    st.markdown(
                                        f'<span class="tag tag-rec">{t["ticker"]}</span> '
                                        f'{t["n"]}× · WR {wr:.0f}% · '
                                        f'<span class="{color}">{ar:+.1f}%</span>',
                                        unsafe_allow_html=True
                                    )
                        else:
                            with col_w:
                                st.markdown("⚠️ **Piores tickers**")
                                for t in tickers:
                                    wr = t.get("win_rate", 0)
                                    ar = t.get("avg_ret", 0)
                                    st.markdown(
                                        f'<span class="tag tag-bad">{t["ticker"]}</span> '
                                        f'{t["n"]}× · WR {wr:.0f}% · '
                                        f'<span class="red">{ar:+.1f}%</span>',
                                        unsafe_allow_html=True
                                    )

                elif cat == "optimal_params":
                    for ins in cat_insights:
                        v = ins["value"]
                        conf = ins["confidence"]
                        conf_tag = "tag-rec" if conf >= 0.7 else "tag-warn" if conf >= 0.4 else "tag-bad"

                        if ins["key"] == "best_gap_threshold":
                            rec = v.get("recommended_min_gap")
                            thresholds = v.get("all_thresholds", [])
                            st.markdown(
                                f'Gap mínimo recomendado: <span class="tag {conf_tag}">{rec}%</span> '
                                f'(confiança {int(conf*100)}%)',
                                unsafe_allow_html=True
                            )
                            if thresholds:
                                tdf = pd.DataFrame(thresholds)
                                st.bar_chart(
                                    tdf.set_index("min_gap")[["win_rate", "avg_return"]],
                                    height=180
                                )

                        elif ins["key"] == "best_vol_threshold":
                            rec = v.get("recommended_min_vol_ratio")
                            thresholds = v.get("all_thresholds", [])
                            st.markdown(
                                f'Vol ratio mínimo recomendado: <span class="tag {conf_tag}">{rec}×</span> '
                                f'(confiança {int(conf*100)}%)',
                                unsafe_allow_html=True
                            )
                            if thresholds:
                                tdf = pd.DataFrame(thresholds)
                                st.bar_chart(
                                    tdf.set_index("min_vol_ratio")[["win_rate", "avg_return"]],
                                    height=180
                                )

                st.markdown("")

    # ── TAB 3: Scanner adjustments ─────────────────────────────────────────────
    with tabs[2]:
        st.markdown("### Ajustes automáticos para o Scanner")
        st.markdown(
            "Estes valores são calculados automaticamente pela KB e podem ser "
            "usados para melhorar o MAGNA score no scanner principal."
        )

        adj = get_scanner_adjustments()

        if not adj.get("data_available"):
            st.info("KB vazia — sem ajustes disponíveis.")
        else:
            col_rec, col_mult = st.columns(2)

            with col_rec:
                st.markdown("#### Thresholds recomendados")
                gap_rec = adj.get("min_gap_recommended")
                vol_rec = adj.get("min_vol_ratio_recommended")

                if gap_rec:
                    st.markdown(
                        f'Gap mínimo: <span class="tag tag-rec">{gap_rec}%</span>',
                        unsafe_allow_html=True
                    )
                else:
                    st.caption("Gap: dados insuficientes")

                if vol_rec:
                    st.markdown(
                        f'Vol ratio mínimo: <span class="tag tag-rec">{vol_rec}×</span>',
                        unsafe_allow_html=True
                    )
                else:
                    st.caption("Vol ratio: dados insuficientes")

                avoided = adj.get("avoided_tickers", [])
                if avoided:
                    st.markdown("#### Tickers a evitar")
                    for t in avoided:
                        st.markdown(
                            f'<span class="tag tag-bad">{t}</span>',
                            unsafe_allow_html=True
                        )
                else:
                    st.caption("Nenhum ticker a evitar (dados insuficientes ou todos ok)")

            with col_mult:
                st.markdown("#### Multiplicadores de score")
                st.caption("Aplicados ao MAGNA score no scanner. >1 = bonus, <1 = penalização")

                gap_mult = adj.get("gap_multipliers", {})
                if gap_mult:
                    st.markdown("**Gap multipliers:**")
                    for k, v in gap_mult.items():
                        color = "green" if v > 1 else "red" if v < 1 else "neutral"
                        st.markdown(
                            f'Gap {k.replace("_","-")}%: '
                            f'<span class="{color}">×{v}</span>',
                            unsafe_allow_html=True
                        )

                vol_mult = adj.get("vol_multipliers", {})
                if vol_mult:
                    st.markdown("**Vol multipliers:**")
                    for k, v in vol_mult.items():
                        color = "green" if v > 1 else "red" if v < 1 else "neutral"
                        st.markdown(
                            f'Vol {k.replace("_","-")}×: '
                            f'<span class="{color}">×{v}</span>',
                            unsafe_allow_html=True
                        )

            st.divider()
            st.markdown("#### JSON para copiar para o scanner")
            st.code(
                f"KB_ADJUSTMENTS = {json.dumps(adj, indent=2)}",
                language="python"
            )

    # ── TAB 4: Run history ─────────────────────────────────────────────────────
    with tabs[3]:
        st.markdown("### Histórico de runs")
        runs = get_run_history()

        if not runs:
            st.info("Sem runs guardados ainda.")
        else:
            for run in runs:
                wr = run["win_rate"] or 0
                pf = run["profit_factor"] or 0
                ar = run["avg_return"] or 0
                wr_color = "green" if wr >= 50 else "red"
                pf_color = "green" if pf >= 1.5 else "yellow" if pf >= 1 else "red"
                ar_color = "green" if ar > 0 else "red"

                with st.expander(
                    f"Run #{run['id']} · {run['run_date']} · "
                    f"{run['start_date']} → {run['end_date']} · "
                    f"{run['n_trades']} trades",
                    expanded=False
                ):
                    c1, c2, c3, c4 = st.columns(4)
                    c1.markdown(
                        f"<span class='{wr_color}'>**{wr:.1f}%**</span> win rate",
                        unsafe_allow_html=True
                    )
                    c2.markdown(
                        f"<span class='{pf_color}'>**{pf:.2f}**</span> profit factor",
                        unsafe_allow_html=True
                    )
                    c3.markdown(
                        f"<span class='{ar_color}'>**{ar:+.1f}%**</span> avg return",
                        unsafe_allow_html=True
                    )
                    c4.markdown(f"**{run['n_trades']}** trades")

                    if run.get("notes"):
                        st.caption(f"📝 {run['notes']}")

                    if st.button("🗑️ Apagar run", key=f"del_run_{run['id']}"):
                        delete_run(run["id"])
                        derive_insights()  # re-derive without deleted run
                        st.rerun()


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    render_kb_page()
