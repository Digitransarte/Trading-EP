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
import json
import argparse
from datetime import datetime
from ep_scanner_headless import run_scan
from ep_notifier_telegram import notify, send_test_message


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

    # ── Save JSON (optional) ──────────────────────────────────────────────────
    if args.output_json:
        with open(args.output_json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Resultados guardados em: {args.output_json}")

    # ── Telegram notification ─────────────────────────────────────────────────
    print(f"\nA notificar via Telegram (min_score={args.min_score})...")
    notify(result, min_score=args.min_score)

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
