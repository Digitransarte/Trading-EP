"""
EP Weekly Digest
================
Envia um resumo semanal de aprendizagem para o Telegram.
Inclui: candidatos da semana, lições aprendidas, conceito da semana.

Corre às sextas-feiras (adicionado ao ep_daily_runner.py ou separado).

Uso:
  python ep_weekly_digest.py
"""

import os
import json
import sqlite3
import requests
import anthropic
from datetime import date, timedelta, datetime
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_KEY    = os.getenv("ANTHROPIC_API_KEY")
KB_DB            = "ep_knowledge_base.db"


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────

def send(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
        timeout=15
    )
    return r.status_code == 200


# ─── KB DATA ──────────────────────────────────────────────────────────────────

def get_weekly_stats() -> dict:
    """Get trade stats from the last 7 days of backtest data."""
    if not os.path.exists(KB_DB):
        return {}

    week_ago = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")
    conn     = sqlite3.connect(KB_DB)

    # Recent runs
    runs = conn.execute(
        "SELECT id, start_date, end_date, win_rate, avg_return, n_trades "
        "FROM backtest_runs ORDER BY run_date DESC LIMIT 5"
    ).fetchall()

    # Overall stats from KB
    total_trades = conn.execute(
        "SELECT COUNT(*) FROM trade_log WHERE result IN ('WIN','LOSS')"
    ).fetchone()[0]

    wins = conn.execute(
        "SELECT COUNT(*) FROM trade_log WHERE result='WIN'"
    ).fetchone()[0]

    # Best gap range
    gap_rows = conn.execute(
        "SELECT gap_pct, result, total_return_pct FROM trade_log "
        "WHERE result IN ('WIN','LOSS') ORDER BY ROWID DESC LIMIT 500"
    ).fetchall()

    # Performance by gap bucket
    buckets = {"8-15%": [], "15-25%": [], "25%+": []}
    for gap, result, ret in gap_rows:
        if gap < 15:   buckets["8-15%"].append(ret)
        elif gap < 25: buckets["15-25%"].append(ret)
        else:          buckets["25%+"].append(ret)

    bucket_stats = {}
    for label, rets in buckets.items():
        if rets:
            w = sum(1 for r in rets if r > 0)
            bucket_stats[label] = {
                "n": len(rets),
                "win_rate": round(w / len(rets) * 100, 1),
                "avg_return": round(sum(rets) / len(rets), 1)
            }

    conn.close()

    return {
        "total_trades": total_trades,
        "overall_win_rate": round(wins / max(total_trades, 1) * 100, 1),
        "recent_runs": runs,
        "gap_performance": bucket_stats,
    }


# ─── CONCEPT OF THE WEEK ──────────────────────────────────────────────────────

CONCEPTS = [
    {
        "title": "O que é um Episodic Pivot?",
        "body": (
            "Um EP acontece quando uma *surpresa* obriga o mercado a rever completamente o valor de uma empresa. "
            "Não é uma subida normal — é uma *ruptura* com o passado.\n\n"
            "Exemplos de catalisadores EP genuínos:\n"
            "• Earnings que batem estimativas em 200%+\n"
            "• Aprovação FDA inesperada\n"
            "• Contrato enorme anunciado\n"
            "• Turnaround depois de anos de perdas\n\n"
            "_A chave: o mercado foi APANHADO DE SURPRESA. Isso é o que cria o gap e o volume extremo._"
        )
    },
    {
        "title": "Porquê o Volume é tão importante?",
        "body": (
            "O volume é a *pegada dos institucionais*. Quando vês 10× o volume normal, significa que "
            "fundos e gestores de capital estão a comprar — não traders individuais.\n\n"
            "Os dados da nossa KB com 1680 trades confirmam:\n"
            "• Vol 3-5×: WR 38%, avg +0.1% _(ruído)_\n"
            "• Vol 5-10×: WR 40%, avg +2.2%\n"
            "• Vol 10×+: WR 46%, avg +21.5% ✅\n\n"
            "_Regra prática: se o volume não for pelo menos 5× a média, ignora o sinal._"
        )
    },
    {
        "title": "O conceito de Neglect (Negligência)",
        "body": (
            "O Pradeep diz: *'Os melhores EPs vêm de stocks que ninguém conhece.'*\n\n"
            "Neglect significa que antes do EP a acção estava:\n"
            "• A cair ou lateral há 2-6 meses\n"
            "• Com menos de 100 fundos institucionais\n"
            "• Sem cobertura de analistas\n"
            "• Ignorada pelos media financeiros\n\n"
            "Porquê é importante? Porque quando uma empresa negligenciada reporta resultados "
            "surpreendentes, *não há vendedores posicionados*. O movimento é muito mais limpo.\n\n"
            "_Contrasta com uma Apple ou Microsoft — toda a gente já tem posição, não há surpresa._"
        )
    },
    {
        "title": "Stop Loss: onde colocar?",
        "body": (
            "No método EP, o stop não é calculado em percentagem arbitrária — é ancorado ao *pivot original*.\n\n"
            "A lógica:\n"
            "• O EP cria um novo nível de preço\n"
            "• A mínima do dia do EP é o suporte natural\n"
            "• Se o preço fechar abaixo do fecho pré-EP, o sinal falhou\n\n"
            "Exemplo prático (AGX):\n"
            "• Fecho pré-EP: ~$430\n"
            "• Gap para ~$565 na sexta\n"
            "• Stop real: $430 (não $565 × 0.92)\n\n"
            "_Isto significa que podes aguentar pullbacks normais sem ser parado desnecessariamente._"
        )
    },
    {
        "title": "As 4 Tranches de Saída",
        "body": (
            "O Pradeep não vende tudo de uma vez — usa 4 saídas parciais:\n\n"
            "• *Tranche 1* (+10%): vende 25% — garante lucro\n"
            "• *Tranche 2* (+20%): vende mais 25% — reduz risco\n"
            "• *Tranche 3* (+30%): vende mais 25% — deixa correr\n"
            "• *Tranche 4* (+40%): vende os últimos 25%\n\n"
            "Porquê? Porque nunca sabes quando o movimento vai parar. "
            "Vendendo em tranches, *nunca vendes tudo no pior momento*.\n\n"
            "_Os dados da KB mostram que os trades que chegam a 20+ dias têm WR de 95%. "
            "As tranches dão-te a paciência para esperar._"
        )
    },
    {
        "title": "TURNAROUND vs GROWTH EP",
        "body": (
            "*TURNAROUND*: empresa que estava a perder dinheiro há anos e de repente reverte.\n"
            "→ Maior potencial de movimento (200-500%+)\n"
            "→ Mais raro e mais difícil de identificar\n"
            "→ Requer queda prévia significativa (neglect alto)\n\n"
            "*GROWTH*: empresa com crescimento consistente de 39%+ em vendas.\n"
            "→ Mais frequente e mais previsível\n"
            "→ Movimento mais gradual mas mais sustentado\n"
            "→ Pradeep prefere crescimento de *revenue* (não manipulável)\n\n"
            "_Na nossa KB, os TURNAROUND têm maior avg win (+52%) mas menor win rate (36%). "
            "Os GROWTH têm win rate mais alto mas menor upside médio._"
        )
    },
    {
        "title": "Porquê o Gap mínimo de 20%?",
        "body": (
            "Os dados de 1680 trades no nosso sistema respondem claramente:\n\n"
            "• Gap 8-15%: WR 36%, avg return *+0.0%* ⚠️\n"
            "• Gap 15-25%: WR 40%, avg return *+1.1%*\n"
            "• Gap 25%+: WR 47%, avg return *+21.9%* ✅\n\n"
            "Um gap de 8% pode ser ruído de mercado. Um gap de 25% é quase sempre "
            "uma surpresa genuína que força repositionamento institucional.\n\n"
            "_O nosso scanner detecta a partir de 8% mas a KB penaliza automaticamente "
            "gaps pequenos. O edge real está nos 25%+._"
        )
    },
]

def get_concept_of_week() -> dict:
    """Rotate concepts weekly based on week number."""
    week_num = date.today().isocalendar()[1]
    return CONCEPTS[week_num % len(CONCEPTS)]


# ─── CLAUDE WEEKLY SUMMARY ────────────────────────────────────────────────────

def generate_weekly_summary(stats: dict) -> str:
    """Use Claude to generate a personalised weekly learning summary."""
    if not ANTHROPIC_KEY:
        return None

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    prompt = f"""És um coach de trading especializado no método Episodic Pivot (Pradeep Bonde).

Dados da semana do sistema de backtesting:
{json.dumps(stats, indent=2)}

Escreve um resumo semanal de aprendizagem em português europeu. Tom: educativo, directo, como um mentor experiente.

Inclui:
1. Uma observação sobre os dados desta semana (2-3 frases)
2. O padrão mais importante que os dados revelam
3. Uma pergunta para o trader reflectir

Máximo 150 palavras. Sem formatação markdown complexa. Usa linguagem simples."""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()
    except:
        return None


# ─── MAIN DIGEST ──────────────────────────────────────────────────────────────

def send_weekly_digest():
    """Build and send the weekly learning digest."""
    today    = date.today()
    week_num = today.isocalendar()[1]

    print(f"A construir digest semanal (semana {week_num})...")

    # Header
    send(
        f"📚 *Digest Semanal EP* · Semana {week_num}\n"
        f"_{today.strftime('%d/%m/%Y')}_ · Sistema de Aprendizagem Contínua\n"
        f"{'─' * 28}"
    )

    import time
    time.sleep(0.5)

    # KB Stats
    stats = get_weekly_stats()
    if stats:
        total = stats.get("total_trades", 0)
        wr    = stats.get("overall_win_rate", 0)
        gap_p = stats.get("gap_performance", {})

        stats_msg = (
            f"📊 *Estado da Knowledge Base*\n\n"
            f"• {total} trades analisados\n"
            f"• Win rate global: {wr}%\n"
        )

        if gap_p:
            stats_msg += f"\n*Performance por Gap:*\n"
            for label, s in gap_p.items():
                emoji = "✅" if s["avg_return"] > 5 else "⚠️" if s["avg_return"] > 0 else "❌"
                stats_msg += f"{emoji} Gap {label}: WR {s['win_rate']}% · avg {s['avg_return']:+.1f}%\n"

        send(stats_msg)
        time.sleep(0.5)

    # KB narrative — 3 key lessons
    try:
        from knowledge_base import get_kb_narrative
        narrative = get_kb_narrative(mode="summary")
        if narrative.get("available") and narrative.get("text"):
            send(f"📚 *Lições da Knowledge Base:*\n\n{narrative['text']}")
            time.sleep(0.5)
    except Exception as e:
        print(f"KB narrative falhou: {e}")

    # Claude weekly summary
    if stats:
        summary = generate_weekly_summary(stats)
        if summary:
            send(f"🤖 *Análise da semana:*\n\n{summary}")
            time.sleep(0.5)

    # Concept of the week
    concept = get_concept_of_week()
    send(
        f"💡 *Conceito da Semana:*\n"
        f"*{concept['title']}*\n\n"
        f"{concept['body']}"
    )
    time.sleep(0.5)

    # Footer
    send(
        f"{'─' * 28}\n"
        f"_Corre `streamlit run knowledge_base.py` para ver todos os insights._\n"
        f"_Digest gerado automaticamente pelo sistema EP._"
    )

    print("✅ Digest semanal enviado")


if __name__ == "__main__":
    send_weekly_digest()
