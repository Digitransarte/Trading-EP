"""
EP Daily Runner
===============
Ponto de entrada para a automação diária.
Corre pelo GitHub Actions ou manualmente.

Uso:
  python ep_daily_runner.py                    # scan + Telegram
  python ep_daily_runner.py --test-telegram    # só testa Telegram
  python ep_daily_runner.py --no-claude        # sem análise Claude (mais rápido)
  python ep_daily_runner.py --min-score 60     # só envia candidatos ≥ 60
"""

import sys
import os
import json
import argparse
from datetime import datetime
from ep_scanner_headless import run_scan
from ep_notifier_telegram import notify, send_test_message
from ep_forward_tracker import save_candidates, update_positions, format_tracker_telegram

PREV_CLOSES_FILE = "ep_prev_closes.json"


def save_prev_closes(scan_result: dict):
    """
    Guarda closes do dia anterior em cache para o scan intraday das 17h.
    O scan intraday usa este ficheiro para calcular gaps do dia actual.
    """
    import json
    from datetime import date

    # Os dados "today" do scan da manhã são na realidade EOD de ontem
    session_date = scan_result.get("session_date", "")
    raw_data     = scan_result.get("_raw_today_data", {})

    if not raw_data:
        return 0

    cache = {"_date": date.today().strftime("%Y-%m-%d")}
    for ticker, bar in raw_data.items():
        vol = bar.get("v", 0)
        if vol >= 300_000:  # só tickers com volume relevante
            cache[ticker] = {
                "close":  round(bar.get("c", 0), 4),
                "volume": int(vol),
            }

    with open(PREV_CLOSES_FILE, "w") as f:
        json.dump(cache, f)

    print(f"[Cache] {len(cache)-1:,} closes guardados em {PREV_CLOSES_FILE}")
    return len(cache) - 1


def main():
    parser = argparse.ArgumentParser(description="EP Daily Scanner")
    parser.add_argument("--test-telegram", action="store_true",
                        help="Envia mensagem de teste ao Telegram e sai")
    parser.add_argument("--no-claude", action="store_true",
                        help="Salta análise Claude (mais rápido, sem custo API)")
    parser.add_argument("--min-score", type=int, default=50,
                        help="Score mínimo para notificar (default: 50)")
    parser.add_argument("--min-gap", type=float, default=8.0,
                        help="Gap mínimo %% (default: 8.0)")
    parser.add_argument("--min-vol-ratio", type=float, default=3.0,
                        help="Volume ratio mínimo (default: 3.0)")
    parser.add_argument("--max-candidates", type=int, default=8,
                        help="Máximo de candidatos a analisar (default: 8)")
    parser.add_argument("--output-json", type=str, default=None,
                        help="Guardar resultados em ficheiro JSON")
    args = parser.parse_args()

    # ── Test mode ─────────────────────────────────────────────────────────────
    if args.test_telegram:
        print("A testar Telegram...")
        ok = send_test_message()
        sys.exit(0 if ok else 1)

    # ── Full scan ─────────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  EP Scanner — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*50}")
    print(f"  Gap mínimo:       {args.min_gap}%")
    print(f"  Vol ratio mínimo: {args.min_vol_ratio}×")
    print(f"  Max candidatos:   {args.max_candidates}")
    print(f"  Score mínimo:     {args.min_score}")
    print(f"  Análise Claude:   {'Não' if args.no_claude else 'Sim'}")
    print(f"{'='*50}\n")

    result = run_scan(
        min_gap=args.min_gap,
        min_vol_ratio=args.min_vol_ratio,
        max_candidates=args.max_candidates,
        use_claude=not args.no_claude,
    )

    # ── Guardar cache para scan intraday das 17h ─────────────────────────────
    save_prev_closes(result)

    # ── Save JSON (optional) ──────────────────────────────────────────────────
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Resultados guardados em: {args.output_json}")

    # ── Telegram notification ─────────────────────────────────────────────────
    print(f"\nA notificar via Telegram (min_score={args.min_score})...")
    notify(result, min_score=args.min_score)

    # ── Forward Tracker ───────────────────────────────────────────────────────
    candidates        = result.get("candidates", [])
    canslim_candidates = result.get("canslim", [])

    if candidates or canslim_candidates:
        print("\n[Tracker] A registar candidatos...")

        # EP candidates
        top_ep = [c for c in candidates if c.get("magna_score", 0) >= args.min_score]
        for c in top_ep:
            c["strategy_type"] = "EP"

        # CANSLIM candidates — threshold baseado em score raw
        top_cs = [c for c in canslim_candidates if c.get("score", 0) >= 10]
        for c in top_cs:
            c["strategy_type"] = "CANSLIM"
            # Mapear campos para formato do tracker
            if "change_pct" in c and "gap_pct" not in c:
                c["gap_pct"] = c["change_pct"]
            if "magna_score" not in c:
                c["magna_score"] = int(c.get("oneil_score", 0))
            if "stop_price" not in c:
                c["stop_price"] = round(c.get("price", 0) * 0.92, 2)
            if "stop_pct" not in c:
                c["stop_pct"] = 8.0

        all_top = top_ep + top_cs
        saved = save_candidates(all_top, scan_date=result.get("session_date"))
        if saved:
            print(f"[Tracker] {saved} novos candidatos registados (EP: {len(top_ep)} · CANSLIM: {len(top_cs)})")

        # Actualizar posições abertas com dados de hoje
        print("[Tracker] A actualizar posições abertas...")
        from ep_scanner_headless import fetch_grouped
        today_data = fetch_grouped(result.get("session_date", ""))
        tracker_update = update_positions(today_data, result.get("session_date"))

        # Enviar update do tracker para Telegram se houver fechamentos
        if tracker_update.get("closed") or tracker_update.get("open", 0) > 0:
            from ep_forward_tracker import get_tracker_stats
            tracker_stats = get_tracker_stats()
            tracker_msgs  = format_tracker_telegram(tracker_stats)
            import requests as _req
            from dotenv import load_dotenv as _ldenv
            _ldenv()
            token   = os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            for msg in tracker_msgs:
                if token and chat_id and msg.strip():
                    _req.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
                        timeout=15
                    )
                    import time as _t; _t.sleep(0.4)
            print(f"[Tracker] {tracker_update['open']} abertas · {len(tracker_update['closed'])} fechadas hoje")

    # ── EP Pullback Monitor ──────────────────────────────────────────────────
    print("\n[Monitor] A actualizar monitorização EP...")
    try:
        from ep_pullback_monitor import add_to_monitor, update_monitor, notify_signals
        from ep_scanner_headless import fetch_grouped as _fg

        # Adicionar novos EPs qualificados à monitorização
        candidates_ep = result.get("candidates", [])
        added = 0
        for c in candidates_ep:
            if add_to_monitor(c, scan_date=result.get("session_date")):
                added += 1
        if added:
            print(f"[Monitor] {added} novos EPs adicionados à monitorização")

        # Actualizar todos os candidatos em monitorização
        today_data_mon = _fg(result.get("session_date", ""))
        mon_update = update_monitor(today_data_mon, result.get("session_date"))

        print(f"[Monitor] {mon_update['updated']} actualizados · "
              f"{len(mon_update['signals'])} sinais · "
              f"{mon_update['expired']} expirados")

        # Enviar alertas de entrada
        if mon_update["signals"]:
            notify_signals(mon_update["signals"])
            print(f"[Monitor] {len(mon_update['signals'])} alertas enviados")

    except Exception as _me:
        print(f"[Monitor] Erro: {_me}")

    # ── Weekly digest (sextas-feiras) ────────────────────────────────────────
    from datetime import date
    if date.today().weekday() == 4:  # 4 = sexta-feira
        print("\nÉ sexta-feira — a enviar digest semanal...")
        try:
            from ep_weekly_digest import send_weekly_digest
            send_weekly_digest()
        except Exception as e:
            print(f"Digest falhou: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    candidates = result.get("candidates", [])
    filtered   = [c for c in candidates if c.get("magna_score", 0) >= args.min_score]

    print(f"\n{'='*50}")
    print(f"  ✅ Scan completo")
    print(f"  Sessão:      {result.get('session_date', '—')}")
    print(f"  Universo:    {result.get('n_universe', 0):,} tickers")
    print(f"  Candidatos:  {len(candidates)} detectados · {len(filtered)} notificados")
    print(f"{'='*50}\n")

    sys.exit(0)


if __name__ == "__main__":
    main()
