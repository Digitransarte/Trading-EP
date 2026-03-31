"""
EP Macro Context
================
Obtém contexto macroeconómico actual via web search + Claude.
Integra-se com o scanner EP e o notificador Telegram.

Uso:
  from ep_macro_context import get_macro_context
  macro = get_macro_context()
  # macro["summary"]      → resumo em português
  # macro["market"]       → condições de mercado
  # macro["fed"]          → política monetária
  # macro["geopolitical"] → contexto geopolítico
  # macro["sentiment"]    → sentimento (RISK_ON / RISK_OFF / NEUTRAL)
  # macro["ep_impact"]    → impacto esperado nos EPs hoje
"""

import os
import json
import anthropic
from datetime import date
from dotenv import load_dotenv

load_dotenv()
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")


# ─── MACRO FETCH ──────────────────────────────────────────────────────────────

def get_macro_context(sectors: list = None) -> dict:
    """
    Fetch current macro context using Claude with web search.
    Returns structured dict with market conditions and EP impact assessment.

    sectors: list of sectors from EP candidates (e.g. ["Technology", "Healthcare"])
    """
    if not ANTHROPIC_KEY:
        return _empty_context("Chave Anthropic não configurada")

    client   = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    today    = date.today().strftime("%d de %B de %Y")
    sectors_str = ", ".join(sectors) if sectors else "Technology, Healthcare, Industrials"

    prompt = f"""Hoje é {today}. És um analista macro sénior especializado em mercados americanos.

Faz pesquisas web para obter informação ACTUAL sobre:
1. Estado do mercado americano hoje (S&P 500, Nasdaq, VIX, tendência)
2. Política monetária da Fed (última decisão, próxima reunião, expectativas)
3. Contexto geopolítico relevante para os sectores: {sectors_str}
4. Sentimento geral do mercado (Fear & Greed Index ou equivalente)
5. Notícias macro importantes desta semana que afectam estes sectores

Depois de pesquisar, responde APENAS com JSON válido neste formato exacto:
{{
  "date": "{today}",
  "market": {{
    "trend": "BULLISH|BEARISH|SIDEWAYS",
    "sp500_status": "descrição em 1 frase do estado actual",
    "vix_level": "baixo|moderado|elevado|extremo",
    "key_observation": "observação mais importante em 1 frase"
  }},
  "fed": {{
    "stance": "HAWKISH|DOVISH|NEUTRAL",
    "last_decision": "descrição breve",
    "next_meeting": "data aproximada",
    "impact_on_growth_stocks": "positivo|neutro|negativo"
  }},
  "geopolitical": {{
    "risk_level": "baixo|moderado|elevado",
    "key_factors": ["factor 1", "factor 2"],
    "sector_impact": {{}}
  }},
  "sentiment": "RISK_ON|RISK_OFF|NEUTRAL",
  "sentiment_detail": "explicação em 1 frase",
  "ep_impact": {{
    "overall": "FAVORÁVEL|NEUTRO|DESFAVORÁVEL",
    "reasoning": "porque é que as condições actuais favorecem ou não os EPs",
    "caution": "o que os traders devem ter em conta hoje"
  }},
  "summary_pt": "resumo em 3-4 frases em português europeu, tom directo e educativo"
}}"""

    try:
        msg = client.messages.create(
            model   = "claude-sonnet-4-20250514",
            max_tokens = 1500,
            tools   = [{"type": "web_search_20250305", "name": "web_search"}],
            messages = [{"role": "user", "content": prompt}]
        )

        # Extract text from response (may contain tool use blocks)
        text = ""
        for block in msg.content:
            if hasattr(block, "text"):
                text += block.text

        # Parse JSON
        import re
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            data = json.loads(m.group(0))
            data["_source"] = "web_search"
            return data

        return _empty_context("Não foi possível parsear resposta")

    except Exception as e:
        print(f"[Macro] Erro: {e}")
        return _empty_context(str(e))


def _empty_context(reason: str = "") -> dict:
    """Fallback context when web search fails."""
    return {
        "date":      date.today().strftime("%d/%m/%Y"),
        "market":    {"trend": "UNKNOWN", "sp500_status": "Dados não disponíveis",
                      "vix_level": "—", "key_observation": "—"},
        "fed":       {"stance": "UNKNOWN", "last_decision": "—",
                      "next_meeting": "—", "impact_on_growth_stocks": "neutro"},
        "geopolitical": {"risk_level": "—", "key_factors": [], "sector_impact": {}},
        "sentiment": "NEUTRAL",
        "sentiment_detail": "Dados macro não disponíveis",
        "ep_impact": {"overall": "NEUTRO", "reasoning": reason, "caution": ""},
        "summary_pt": "Contexto macro não disponível neste momento.",
        "_source":   "fallback",
    }


# ─── FORMATTERS ───────────────────────────────────────────────────────────────

def format_macro_telegram(macro: dict) -> str:
    """Format macro context for Telegram message."""
    sentiment = macro.get("sentiment", "NEUTRAL")
    impact    = macro.get("ep_impact", {}).get("overall", "NEUTRO")
    market    = macro.get("market", {})
    fed       = macro.get("fed", {})
    geo       = macro.get("geopolitical", {})

    # Emojis
    sentiment_emoji = {"RISK_ON": "🟢", "RISK_OFF": "🔴", "NEUTRAL": "🟡"}.get(sentiment, "⬜")
    impact_emoji    = {"FAVORÁVEL": "✅", "NEUTRO": "⚠️", "DESFAVORÁVEL": "❌"}.get(impact, "⬜")
    trend_emoji     = {"BULLISH": "📈", "BEARISH": "📉", "SIDEWAYS": "➡️"}.get(
                       market.get("trend", ""), "📊")
    fed_emoji       = {"HAWKISH": "🦅", "DOVISH": "🕊️", "NEUTRAL": "⚖️"}.get(
                       fed.get("stance", ""), "🏦")
    geo_emoji       = {"baixo": "🟢", "moderado": "🟡", "elevado": "🔴"}.get(
                       geo.get("risk_level", ""), "⬜")

    lines = [
        f"🌍 *Contexto Macro* · {macro.get('date', '')}",
        f"",
        f"{trend_emoji} Mercado: `{market.get('sp500_status', '—')}`",
        f"{fed_emoji} Fed: `{fed.get('stance', '—')}` · {fed.get('last_decision', '—')}",
        f"{geo_emoji} Geopolítico: `{geo.get('risk_level', '—')}`",
        f"",
        f"{sentiment_emoji} Sentimento: *{sentiment}*",
        f"_{macro.get('sentiment_detail', '')}_",
        f"",
        f"{impact_emoji} Impacto nos EPs: *{impact}*",
        f"_{macro.get('ep_impact', {}).get('reasoning', '')}_",
    ]

    caution = macro.get("ep_impact", {}).get("caution", "")
    if caution:
        lines.append(f"")
        lines.append(f"⚠️ _{caution}_")

    return "\n".join(lines)


def format_macro_streamlit(macro: dict) -> dict:
    """Return structured data for Streamlit display."""
    return {
        "sentiment":       macro.get("sentiment", "NEUTRAL"),
        "impact":          macro.get("ep_impact", {}).get("overall", "NEUTRO"),
        "trend":           macro.get("market", {}).get("trend", "UNKNOWN"),
        "fed_stance":      macro.get("fed", {}).get("stance", "UNKNOWN"),
        "geo_risk":        macro.get("geopolitical", {}).get("risk_level", "—"),
        "summary":         macro.get("summary_pt", ""),
        "reasoning":       macro.get("ep_impact", {}).get("reasoning", ""),
        "caution":         macro.get("ep_impact", {}).get("caution", ""),
        "key_observation": macro.get("market", {}).get("key_observation", ""),
        "geo_factors":     macro.get("geopolitical", {}).get("key_factors", []),
        "source":          macro.get("_source", "unknown"),
    }


# ─── MACRO → EP PROMPT CONTEXT ────────────────────────────────────────────────

def macro_to_prompt_context(macro: dict) -> str:
    """
    Convert macro context to a string for inclusion in Claude EP analysis prompt.
    """
    m  = macro.get("market", {})
    f  = macro.get("fed", {})
    g  = macro.get("geopolitical", {})
    ep = macro.get("ep_impact", {})

    factors = g.get("key_factors", [])
    factors_str = "\n".join(f"  - {x}" for x in factors) if factors else "  - Sem dados"

    return f"""## CONTEXTO MACROECONÓMICO ACTUAL ({macro.get('date', 'hoje')})
- Mercado: {m.get('trend', '?')} · {m.get('sp500_status', '?')}
- VIX: {m.get('vix_level', '?')}
- Fed: {f.get('stance', '?')} · Impacto em growth stocks: {f.get('impact_on_growth_stocks', '?')}
- Risco geopolítico: {g.get('risk_level', '?')}
- Factores geopolíticos relevantes:
{factors_str}
- Sentimento: {macro.get('sentiment', '?')} — {macro.get('sentiment_detail', '')}
- Avaliação para EPs hoje: {ep.get('overall', '?')}
- Atenção: {ep.get('caution', 'nenhuma observação especial')}

Considera este contexto ao avaliar cada candidato EP. 
Em ambiente RISK_OFF ou Fed HAWKISH, os EPs têm menor probabilidade de follow-through.
Em RISK_ON com Fed DOVISH, os EPs tendem a ter movimentos maiores e mais sustentados."""


# ─── ENTRY POINT ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("A obter contexto macro actual...")
    macro = get_macro_context(sectors=["Technology", "Healthcare", "Industrials"])
    print("\n── RESULTADO ──────────────────────────────")
    print(f"Sentimento: {macro.get('sentiment')}")
    print(f"Impacto EPs: {macro.get('ep_impact', {}).get('overall')}")
    print(f"\nResumo:\n{macro.get('summary_pt')}")
    print(f"\nTelegram preview:\n{format_macro_telegram(macro)}")
