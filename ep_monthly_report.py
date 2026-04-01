"""
EP Monthly Report
=================
Gera um relatório mensal detalhado de aprendizagem.
Combina dados da KB + análise Claude + contexto macro.

Uso:
  python ep_monthly_report.py                  # envia por Telegram + guarda em ficheiro
  python ep_monthly_report.py --no-telegram    # só guarda em ficheiro
  python ep_monthly_report.py --print          # imprime no terminal
"""

import os
import json
import argparse
import requests
from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def send(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=15
    )
    return r.status_code == 200


def generate_monthly_report() -> dict:
    """Generate the full monthly report."""
    print("📊 A gerar relatório mensal...")

    # 1. Get KB narrative (full mode)
    narrative = None
    try:
        from knowledge_base import get_kb_narrative
        print("   A analisar KB com Claude...")
        narrative = get_kb_narrative(mode="full")
    except ImportError:
        print("   knowledge_base.py não encontrado")
    except Exception as e:
        print(f"   KB narrative falhou: {e}")

    # 2. Get KB stats
    stats = {}
    try:
        from knowledge_base import get_scanner_adjustments, get_run_history, get_all_insights
        adj   = get_scanner_adjustments()
        runs  = get_run_history()
        stats = {
            "total_trades":  adj.get("total_trades_in_kb", 0),
            "n_runs":        len(runs),
            "min_gap_rec":   adj.get("min_gap_recommended"),
            "min_vol_rec":   adj.get("min_vol_ratio_recommended"),
            "avoided":       adj.get("avoided_tickers", []),
            "gap_mults":     adj.get("gap_multipliers", {}),
            "vol_mults":     adj.get("vol_multipliers", {}),
        }
    except Exception as e:
        print(f"   KB stats falhou: {e}")

    return {
        "date":      date.today().strftime("%B %Y"),
        "generated": datetime.now().strftime("%d/%m/%Y %H:%M"),
        "narrative": narrative,
        "stats":     stats,
    }


def format_report_text(report: dict) -> str:
    """Format report as plain text."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"  EP TRADING SYSTEM — RELATÓRIO MENSAL")
    lines.append(f"  {report['date']} · Gerado em {report['generated']}")
    lines.append("=" * 60)
    lines.append("")

    stats = report.get("stats", {})
    if stats:
        lines.append("ESTADO DA KNOWLEDGE BASE")
        lines.append("-" * 40)
        lines.append(f"Total de trades analisados: {stats.get('total_trades', 0)}")
        lines.append(f"Runs de backtest:           {stats.get('n_runs', 0)}")
        lines.append(f"Gap mínimo recomendado:     {stats.get('min_gap_rec', '—')}%")
        lines.append(f"Vol ratio recomendado:      {stats.get('min_vol_rec', '—')}×")
        if stats.get("avoided"):
            lines.append(f"Tickers a evitar:           {', '.join(stats['avoided'])}")
        lines.append("")
        if stats.get("gap_mults"):
            lines.append("Multiplicadores de Gap:")
            for k, v in stats["gap_mults"].items():
                symbol = "▲" if v > 1 else "▼"
                lines.append(f"  {k.replace('_','-')}%: {symbol} ×{v}")
        lines.append("")

    narrative = report.get("narrative", {})
    if narrative and narrative.get("available") and narrative.get("text"):
        lines.append("ANÁLISE DETALHADA (gerada por Claude)")
        lines.append("-" * 40)
        lines.append(narrative["text"])
        lines.append("")

    lines.append("=" * 60)
    lines.append("Relatório gerado automaticamente pelo EP Trading System")
    lines.append("Apenas para fins educativos. Não é aconselhamento financeiro.")
    lines.append("=" * 60)

    return "\n".join(lines)


def send_report_telegram(report: dict):
    """Send report summary to Telegram (split into sections)."""
    import time
    month = report["date"]
    stats = report.get("stats", {})

    # Header
    send(
        f"📋 *Relatório Mensal EP* · {month}\n"
        f"_{report['generated']}_\n"
        f"{'─' * 28}"
    )
    time.sleep(0.5)

    # Stats summary
    if stats:
        msg = (
            f"📊 *Knowledge Base — Estado Actual*\n\n"
            f"• {stats.get('total_trades',0)} trades analisados\n"
            f"• {stats.get('n_runs',0)} runs de backtest\n"
            f"• Gap recomendado: `{stats.get('min_gap_rec','—')}%`\n"
            f"• Vol recomendado: `{stats.get('min_vol_rec','—')}×`\n"
        )
        if stats.get("avoided"):
            msg += f"• Evitar: `{', '.join(stats['avoided'])}`\n"
        send(msg)
        time.sleep(0.5)

    # Narrative — split into chunks (Telegram limit 4096 chars)
    narrative = report.get("narrative", {})
    if narrative and narrative.get("available") and narrative.get("text"):
        text = narrative["text"]
        # Split by ## sections
        sections = text.split("## ")
        for section in sections:
            if not section.strip():
                continue
            chunk = f"## {section}" if not section.startswith("#") else section
            if len(chunk) > 3800:
                chunk = chunk[:3800] + "..."
            send(chunk)
            time.sleep(0.8)

    # Footer
    send(
        f"{'─' * 28}\n"
        f"_Relatório gerado automaticamente pelo EP System._\n"
        f"_Apenas educativo. Não é aconselhamento financeiro._"
    )
    print("✅ Relatório enviado para Telegram")


def save_report(report: dict, text: str):
    """Save report to file."""
    month_str = date.today().strftime("%Y-%m")
    filename  = f"ep_report_{month_str}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"💾 Relatório guardado em: {filename}")
    return filename


def main():
    parser = argparse.ArgumentParser(description="EP Monthly Report")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Não envia pelo Telegram")
    parser.add_argument("--no-save", action="store_true",
                        help="Não guarda em ficheiro")
    parser.add_argument("--print", dest="print_report", action="store_true",
                        help="Imprime no terminal")
    args = parser.parse_args()

    report    = generate_monthly_report()
    text      = format_report_text(report)

    if args.print_report:
        print(text)

    if not args.no_save:
        save_report(report, text)

    if not args.no_telegram:
        send_report_telegram(report)

    print("\n✅ Relatório mensal completo")


if __name__ == "__main__":
    main()
