"""
Tracker JSON Sync
=================
Sincronização bidireccional entre SQLite (buffer) e JSONs (fonte de verdade).

Uso típico (no daily runner, na cloud):
    from tracker_json_sync import load_from_json, export_to_json

    load_from_json()        # JSON → DB (inicio do run)
    # ... scanner, save_candidates, update_positions ...
    export_to_json()        # DB → JSON (fim do run)

Rationale:
    Runners GitHub Actions são efemeros — a DB SQLite desaparece a cada run.
    Os JSONs vivem no repo e sao commitados de volta, preservando estado entre runs.
"""

import os
import json
import sqlite3
from datetime import date, datetime, timezone

from ep_forward_tracker import TRACKER_DB, SCHEMA

OPEN_JSON   = "tracker_open.json"
CLOSED_JSON = "tracker_closed.json"


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _read_json_or_empty(path: str) -> dict:
    """Le JSON; se nao existir ou for invalido, devolve estrutura vazia."""
    if not os.path.exists(path):
        return {"_meta": {}, "positions": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "positions" not in data:
            data["positions"] = []
        return data
    except (json.JSONDecodeError, IOError) as e:
        print(f"[sync] Aviso: {path} ilegivel ({e}); a assumir vazio")
        return {"_meta": {}, "positions": []}


def _insert_position(conn, p: dict) -> None:
    """Insere uma posicao na DB preservando o ID do JSON."""
    conn.execute("""
        INSERT INTO forward_tests (
            id, ticker, scan_date, entry_price, gap_pct, vol_ratio, magna_score,
            strategy_type, ep_type, oneil_score, oneil_grade, oneil_setup,
            entry_window, stop_price, stop_pct, prev_close,
            catalyst, thesis, sector, float_m,
            status, current_price, max_price, min_price, last_updated,
            exit_price, exit_date, return_pct, hold_days, exit_reason,
            kb_saved, notes
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?
        )
    """, (
        p.get("id"),
        p.get("ticker"),
        p.get("scan_date"),
        p.get("entry_price"),
        p.get("gap_pct"),
        p.get("vol_ratio"),
        p.get("magna_score", 0),
        p.get("strategy_type", "EP"),
        p.get("ep_type"),
        p.get("oneil_score", 0),
        p.get("oneil_grade"),
        p.get("oneil_setup"),
        p.get("entry_window"),
        p.get("stop_price"),
        p.get("stop_pct"),
        p.get("prev_close"),
        p.get("catalyst"),
        p.get("thesis"),
        p.get("sector"),
        p.get("float_m"),
        p.get("status", "OPEN"),
        p.get("current_price"),
        p.get("max_price"),
        p.get("min_price"),
        p.get("last_updated"),
        p.get("exit_price"),
        p.get("exit_date"),
        p.get("return_pct"),
        p.get("hold_days", 0),
        p.get("exit_reason"),
        p.get("kb_saved", 0),
        p.get("notes"),
    ))


def _row_to_dict(row) -> dict:
    """Converte sqlite3.Row para dict limpo."""
    d = {}
    for key in row.keys():
        val = row[key]
        # Nao incluir valores None em campos opcionais (reduz ruido no JSON)
        if val is not None:
            d[key] = val
    # Garantir campos essenciais presentes (mesmo a None)
    for essential in ("id", "ticker", "scan_date", "entry_price", "status"):
        if essential not in d:
            d[essential] = row[essential] if essential in row.keys() else None
    return d


# ─── LOAD (JSON → DB) ─────────────────────────────────────────────────────────

def load_from_json(open_path: str = OPEN_JSON, closed_path: str = CLOSED_JSON) -> dict:
    """
    Recria a DB a partir dos JSONs.
    Apaga estado anterior (DROP + CREATE).
    """
    open_doc   = _read_json_or_empty(open_path)
    closed_doc = _read_json_or_empty(closed_path)

    open_positions   = open_doc.get("positions", [])
    closed_positions = closed_doc.get("positions", [])

    conn = sqlite3.connect(TRACKER_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript("DROP TABLE IF EXISTS forward_tests;")
    conn.executescript(SCHEMA)

    for p in open_positions + closed_positions:
        try:
            _insert_position(conn, p)
        except Exception as e:
            print(f"[sync] Erro ao inserir {p.get('ticker', '?')}: {e}")

    conn.commit()
    conn.close()

    print(f"[sync] JSON -> DB: {len(open_positions)} abertas, {len(closed_positions)} fechadas")
    return {
        "loaded_open":   len(open_positions),
        "loaded_closed": len(closed_positions),
    }


# ─── EXPORT (DB → JSON) ───────────────────────────────────────────────────────

def export_to_json(open_path: str = OPEN_JSON, closed_path: str = CLOSED_JSON,
                   session_date: str = None) -> dict:
    """
    Escreve os JSONs a partir da DB.
    Posicoes com status='OPEN' vao para tracker_open.json,
    restantes para tracker_closed.json.
    """
    conn = sqlite3.connect(TRACKER_DB)
    conn.row_factory = sqlite3.Row
    # Garantir schema (caso a DB nao exista ainda)
    conn.executescript(SCHEMA)
    rows = conn.execute("SELECT * FROM forward_tests ORDER BY id").fetchall()
    conn.close()

    open_positions   = [_row_to_dict(r) for r in rows if r["status"] == "OPEN"]
    closed_positions = [_row_to_dict(r) for r in rows if r["status"] != "OPEN"]

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    session = session_date or date.today().strftime("%Y-%m-%d")

    open_doc = {
        "_meta": {
            "updated_at":   now_iso,
            "last_session": session,
            "version":      1,
        },
        "positions": open_positions,
    }
    closed_doc = {
        "_meta": {
            "updated_at": now_iso,
            "version":    1,
        },
        "positions": closed_positions,
    }

    with open(open_path, "w", encoding="utf-8") as f:
        json.dump(open_doc, f, indent=2, ensure_ascii=False)
    with open(closed_path, "w", encoding="utf-8") as f:
        json.dump(closed_doc, f, indent=2, ensure_ascii=False)

    print(f"[sync] DB -> JSON: {len(open_positions)} abertas, {len(closed_positions)} fechadas")
    return {
        "exported_open":   len(open_positions),
        "exported_closed": len(closed_positions),
    }


# ─── CLI (teste manual) ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "load":
        load_from_json()
    elif cmd == "export":
        export_to_json()
    else:
        print("Uso: python tracker_json_sync.py [load|export]")
