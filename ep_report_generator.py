"""
EP Report Generator
===================
Gera um ficheiro HTML estático com os resultados do scan diário.
Publicado automaticamente no GitHub Pages.

Uso:
  python ep_report_generator.py --input results.json --output docs/index.html
"""

import os
import json
import argparse
from datetime import datetime, date

def score_color(score: int) -> str:
    if score >= 75: return "#00e87a"
    if score >= 50: return "#f5c842"
    return "#ff5e5e"

def window_color(window: str) -> str:
    return {"PRIME": "#00e87a", "OPEN": "#f5c842", "LATE": "#ff5e5e"}.get(window, "#667a99")

def ep_type_badge(ep_type: str) -> str:
    colors = {
        "TURNAROUND":      ("#fb923c", "#fb923c20"),
        "GROWTH":          ("#a78bfa", "#a78bfa20"),
        "STORY/NEGLECTED": ("#f472b6", "#f472b620"),
        "9M_EP":           ("#00e87a", "#00e87a20"),
        "STANDARD":        ("#00b4ff", "#00b4ff20"),
    }
    c, bg = colors.get(ep_type, ("#667a99", "#66799920"))
    return f'<span style="background:{bg};color:{c};border:1px solid {c}40;border-radius:3px;padding:2px 8px;font-size:0.75em;font-weight:600;font-family:monospace">{ep_type}</span>'

def fmt_large(n):
    if not n: return "—"
    try:
        n = float(n)
        if n >= 1e9: return f"${n/1e9:.1f}B"
        if n >= 1e6: return f"${n/1e6:.0f}M"
        return f"${n:,.0f}"
    except: return str(n)

def render_ep_candidate(c: dict, rank: int) -> str:
    score      = c.get("magna_score", 0)
    ep_type    = c.get("ep_type", "STANDARD")
    window     = c.get("entry_window", "PRIME")
    ticker     = c.get("ticker", "?")
    price      = c.get("price", 0)
    gap        = c.get("gap_pct", 0)
    vol        = c.get("vol_ratio", 0)
    stop_price = c.get("stop_price", 0)
    stop_pct   = c.get("stop_pct", 8)
    prev_close = c.get("prev_close", 0)
    eps        = c.get("earnings_pct", 0)
    rev        = c.get("revenue_pct", 0)
    float_m    = c.get("float_m", 0)
    mktcap     = c.get("market_cap", "—")
    neglect    = c.get("neglect_label", "—")
    sector     = c.get("sector", "")
    catalyst   = c.get("catalyst", "")
    thesis     = c.get("thesis", "")
    red_flags  = c.get("red_flags", "")

    # Trading plan
    t1 = round(price * 1.20, 2)
    t2 = round(price * 1.40, 2)
    t3 = round(price * 1.60, 2)
    rr = round((t1 - price) / max(price - stop_price, 0.01), 1) if stop_price else "—"
    rr_color = "#00e87a" if isinstance(rr, float) and rr >= 2 else "#f5c842" if isinstance(rr, float) and rr >= 1.2 else "#ff5e5e"

    rank_emoji = ["🥇","🥈","🥉"][rank-1] if rank <= 3 else f"#{rank}"

    return f'''
    <div class="card" id="ep-{ticker}">
        <div class="card-header">
            <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
                <span style="font-size:1.5em">{rank_emoji}</span>
                <span class="ticker">{ticker}</span>
                {ep_type_badge(ep_type)}
                <span style="color:{window_color(window)};font-weight:700;font-family:monospace">{window}</span>
                <span style="color:#667a99;font-size:0.85em">{sector}</span>
            </div>
            <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-top:8px">
                <span class="score" style="color:{score_color(score)}">{score}<span style="font-size:0.5em;color:#667a99">/100</span></span>
                <span class="metric">Gap <b style="color:#00e87a">+{gap:.1f}%</b></span>
                <span class="metric">Vol <b style="color:#00b4ff">{vol:.1f}×</b></span>
                <span class="metric">Preço <b>${price:.2f}</b></span>
            </div>
        </div>

        <div class="card-body">
            <div class="grid-2">
                <!-- Informação da empresa -->
                <div>
                    <div class="section-title">📊 Fundamentais</div>
                    <div class="metric-grid">
                        <div class="metric-box">
                            <div class="metric-label">EPS QoQ</div>
                            <div class="metric-value" style="color:{'#00e87a' if eps > 0 else '#ff5e5e'}">{eps:+.0f}%</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Revenue YoY</div>
                            <div class="metric-value" style="color:{'#00e87a' if rev > 0 else '#ff5e5e'}">{rev:+.0f}%</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Float</div>
                            <div class="metric-value" style="color:{'#00e87a' if float_m < 25 else '#667a99'}">{float_m:.1f}M</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Market Cap</div>
                            <div class="metric-value">{mktcap}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Neglect</div>
                            <div class="metric-value">{neglect}</div>
                        </div>
                        <div class="metric-box">
                            <div class="metric-label">Prev Close</div>
                            <div class="metric-value">${prev_close:.2f}</div>
                        </div>
                    </div>
                    {f'<div class="catalyst-box"><b>{catalyst}</b></div>' if catalyst and catalyst != "—" else ""}
                    {f'<div class="thesis-box">{thesis}</div>' if thesis else ""}
                    {f'<div class="warning-box">⚠️ {red_flags}</div>' if red_flags else ""}
                </div>

                <!-- Plano de trading -->
                <div>
                    <div class="section-title">📋 Plano de Trading</div>
                    <div class="trading-plan">
                        <div class="plan-row">
                            <span class="plan-label">Entrada (preço actual)</span>
                            <span class="plan-value">${price:.2f}</span>
                        </div>
                        <div class="plan-row">
                            <span class="plan-label">Stop (mínima gap day)</span>
                            <span class="plan-value stop">${stop_price:.2f} (-{stop_pct:.1f}%)</span>
                        </div>
                        <div class="plan-divider"></div>
                        <div class="plan-row">
                            <span class="plan-label">Target 1 (+20%) → 25%</span>
                            <span class="plan-value target">${t1:.2f}</span>
                        </div>
                        <div class="plan-row">
                            <span class="plan-label">Target 2 (+40%) → 25%</span>
                            <span class="plan-value target">${t2:.2f}</span>
                        </div>
                        <div class="plan-row">
                            <span class="plan-label">Target 3 (+60%) → 25%</span>
                            <span class="plan-value target">${t3:.2f}</span>
                        </div>
                        <div class="plan-row">
                            <span class="plan-label">Trailing → 25% restantes</span>
                            <span class="plan-value" style="color:#a78bfa">stop móvel</span>
                        </div>
                        <div class="plan-divider"></div>
                        <div class="plan-row">
                            <span class="plan-label">Risco/Retorno (até T1)</span>
                            <span class="plan-value" style="color:{rr_color}">1:{rr}</span>
                        </div>
                    </div>
                    <div class="stop-note">
                        Se o preço fechar abaixo de ${stop_price:.2f}, o catalisador falhou — sair sem discussão.
                    </div>
                </div>
            </div>
        </div>
    </div>'''

def render_canslim_candidate(c: dict, rank: int) -> str:
    ticker     = c.get("ticker", "?")
    price      = c.get("price", 0)
    change     = c.get("change_pct", 0)
    vol        = c.get("vol_ratio", 0)
    score      = c.get("score", 0)

    rank_emoji = ["🥇","🥈","🥉"][rank-1] if rank <= 3 else f"#{rank}"
    stop_p     = round(price * 0.92, 2)
    t1         = round(price * 1.20, 2)
    t2         = round(price * 1.40, 2)

    return f'''
    <div class="card canslim-card" id="cs-{ticker}">
        <div class="card-header">
            <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
                <span style="font-size:1.5em">{rank_emoji}</span>
                <span class="ticker">{ticker}</span>
                <span style="background:#00b4ff15;color:#00b4ff;border:1px solid #00b4ff30;border-radius:3px;padding:2px 8px;font-size:0.75em;font-weight:600;font-family:monospace">CANSLIM</span>
            </div>
            <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-top:8px">
                <span class="metric">Preço <b>${price:.2f}</b></span>
                <span class="metric">Var <b style="color:#00e87a">+{change:.1f}%</b></span>
                <span class="metric">Vol <b style="color:#00b4ff">{vol:.1f}×</b></span>
                <span class="metric">Score <b>{score:.1f}</b></span>
            </div>
        </div>
        <div class="card-body">
            <div class="trading-plan" style="max-width:320px">
                <div class="plan-row">
                    <span class="plan-label">Stop O'Neil (-8%)</span>
                    <span class="plan-value stop">${stop_p:.2f}</span>
                </div>
                <div class="plan-row">
                    <span class="plan-label">Target 1 (+20%)</span>
                    <span class="plan-value target">${t1:.2f}</span>
                </div>
                <div class="plan-row">
                    <span class="plan-label">Target 2 (+40%)</span>
                    <span class="plan-value target">${t2:.2f}</span>
                </div>
            </div>
        </div>
    </div>'''

def generate_html(scan_result: dict, output_path: str):
    candidates = scan_result.get("candidates", [])
    canslim    = scan_result.get("canslim", [])
    session    = scan_result.get("session_date", "—")
    n_universe = scan_result.get("n_universe", 0)
    generated  = datetime.now().strftime("%d/%m/%Y %H:%M")
    scan_type  = scan_result.get("scan_type", "EOD")

    # Stats
    a_plus = sum(1 for c in candidates if c.get("magna_score", 0) >= 75)
    b_set  = sum(1 for c in candidates if 50 <= c.get("magna_score", 0) < 75)
    avg_score = int(sum(c.get("magna_score",0) for c in candidates) / max(len(candidates),1))

    ep_cards     = "".join(render_ep_candidate(c, i+1) for i, c in enumerate(candidates[:10]))
    canslim_cards = "".join(render_canslim_candidate(c, i+1) for i, c in enumerate(canslim[:8]))

    no_ep_msg = '''<div style="background:#0d1422;border:1px solid #1e2d45;border-radius:8px;
        padding:24px;text-align:center;color:#667a99">
        <div style="font-size:2em;margin-bottom:8px">📭</div>
        <div>Sem candidatos EP com os filtros actuais.</div>
        <div style="font-size:0.85em;margin-top:4px">Gap ≥20% · Vol ≥10× · MAGNA ≥50</div>
        </div>''' if not candidates else ep_cards

    html = f'''<!DOCTYPE html>
<html lang="pt">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>EP Scanner — {session}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: #070b14;
    color: #c9d1e0;
    font-family: 'Segoe UI', system-ui, sans-serif;
    line-height: 1.5;
    padding: 16px;
  }}
  .header {{
    background: linear-gradient(135deg, #0d1422, #111827);
    border: 1px solid #1e3a5f;
    border-radius: 12px;
    padding: 20px 24px;
    margin-bottom: 20px;
  }}
  .header h1 {{
    color: #e8edf5;
    font-size: 1.6em;
    font-family: monospace;
    display: flex;
    align-items: center;
    gap: 10px;
  }}
  .header-meta {{
    display: flex;
    gap: 20px;
    flex-wrap: wrap;
    margin-top: 10px;
    font-size: 0.85em;
    color: #667a99;
  }}
  .stat-box {{
    background: #0d1422;
    border: 1px solid #1e2d45;
    border-radius: 8px;
    padding: 12px 20px;
    text-align: center;
  }}
  .stat-value {{ font-size: 1.8em; font-weight: 700; font-family: monospace; }}
  .stat-label {{ font-size: 0.72em; color: #667a99; text-transform: uppercase; letter-spacing: 1px; }}
  .stats-row {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 10px;
    margin-bottom: 20px;
  }}
  .section-header {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 24px 0 12px 0;
    font-size: 1.1em;
    font-weight: 700;
    color: #e8edf5;
  }}
  .card {{
    background: linear-gradient(135deg, #0d1422, #111827);
    border: 1px solid #1e3a5f;
    border-radius: 12px;
    margin-bottom: 16px;
    overflow: hidden;
  }}
  .canslim-card {{ border-color: #00b4ff30; }}
  .card-header {{
    padding: 16px 20px;
    border-bottom: 1px solid #1e2d45;
  }}
  .card-body {{ padding: 16px 20px; }}
  .ticker {{
    font-size: 1.4em;
    font-weight: 700;
    font-family: monospace;
    color: #e8edf5;
  }}
  .score {{
    font-size: 2em;
    font-weight: 700;
    font-family: monospace;
  }}
  .metric {{ font-size: 0.9em; color: #c9d1e0; }}
  .section-title {{
    color: #667a99;
    font-size: 0.75em;
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 10px;
    font-weight: 600;
  }}
  .metric-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 6px;
    margin-bottom: 12px;
  }}
  .metric-box {{
    background: #070b14;
    border: 1px solid #1e2d45;
    border-radius: 6px;
    padding: 8px 10px;
  }}
  .metric-label {{ font-size: 0.68em; color: #667a99; text-transform: uppercase; letter-spacing: 0.5px; }}
  .metric-value {{ font-size: 0.95em; font-weight: 600; margin-top: 2px; }}
  .catalyst-box {{
    background: #00e87a10;
    border-left: 3px solid #00e87a40;
    padding: 8px 12px;
    border-radius: 4px;
    margin: 8px 0;
    font-size: 0.85em;
  }}
  .thesis-box {{
    background: #1e2d4520;
    border-left: 3px solid #3b82f640;
    padding: 8px 12px;
    border-radius: 4px;
    margin: 8px 0;
    font-size: 0.85em;
    color: #c9d1e0;
  }}
  .warning-box {{
    background: #f5c84210;
    border-left: 3px solid #f5c84240;
    padding: 8px 12px;
    border-radius: 4px;
    margin: 8px 0;
    font-size: 0.85em;
    color: #f5c842;
  }}
  .trading-plan {{
    background: #070b14;
    border: 1px solid #1e3a5f;
    border-radius: 8px;
    padding: 14px;
  }}
  .plan-row {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 5px 0;
    font-size: 0.85em;
  }}
  .plan-label {{ color: #667a99; }}
  .plan-value {{ font-weight: 600; font-family: monospace; color: #e8edf5; }}
  .plan-value.stop {{ color: #ff5e5e; }}
  .plan-value.target {{ color: #00e87a; }}
  .plan-divider {{ border-top: 1px solid #1e2d45; margin: 6px 0; }}
  .stop-note {{
    background: #ff5e5e10;
    border-left: 3px solid #ff5e5e30;
    padding: 8px 12px;
    border-radius: 4px;
    margin-top: 10px;
    font-size: 0.78em;
    color: #c9d1e0;
  }}
  .grid-2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
  }}
  .footer {{
    text-align: center;
    color: #667a99;
    font-size: 0.8em;
    margin-top: 32px;
    padding: 16px;
    border-top: 1px solid #1e2d45;
  }}
  .badge {{
    display: inline-block;
    border-radius: 3px;
    padding: 2px 8px;
    font-size: 0.75em;
    font-weight: 600;
    font-family: monospace;
  }}
  @media (max-width: 640px) {{
    .grid-2 {{ grid-template-columns: 1fr; }}
    .metric-grid {{ grid-template-columns: repeat(2, 1fr); }}
    .stats-row {{ grid-template-columns: repeat(2, 1fr); }}
  }}
</style>
</head>
<body>

<div class="header">
  <h1>⚡ EP Scanner</h1>
  <div class="header-meta">
    <span>📅 Sessão: <b style="color:#e8edf5">{session}</b></span>
    <span>🔭 {n_universe:,} tickers</span>
    <span>🕐 Gerado: {generated}</span>
    <span style="color:{'#00e87a' if scan_type=='INTRADAY_PRIME' else '#f5c842'}">
      {'🟢 PRIME (intraday)' if scan_type=='INTRADAY_PRIME' else '🟡 OPEN (EOD)'}
    </span>
  </div>
</div>

<div class="stats-row">
  <div class="stat-box">
    <div class="stat-value" style="color:#00e87a">{a_plus}</div>
    <div class="stat-label">A+ Setup (≥75)</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#f5c842">{b_set}</div>
    <div class="stat-label">B Setup (50-74)</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#c9d1e0">{len(candidates)}</div>
    <div class="stat-label">Total EP</div>
  </div>
  <div class="stat-box">
    <div class="stat-value" style="color:#00b4ff">{len(canslim)}</div>
    <div class="stat-label">CANSLIM</div>
  </div>
</div>

<div class="section-header">⚡ Episodic Pivot ({len(candidates)} candidatos)</div>
{no_ep_msg}

{"" if not canslim else f'<div class="section-header">📊 CANSLIM ({len(canslim)} candidatos)</div>' + canslim_cards}

<div class="footer">
  <p>⚠️ Apenas para fins informativos. Não é aconselhamento financeiro.</p>
  <p style="margin-top:4px">EP Scanner · MAGNA 53 + ONEIL 70 · Pradeep Bonde / William O'Neil</p>
</div>

</body>
</html>'''

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[Report] HTML gerado: {output_path}")
    return output_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",  default="results.json")
    parser.add_argument("--output", default="docs/index.html")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[Report] {args.input} não encontrado — a gerar página vazia")
        scan_result = {"candidates": [], "canslim": [], "session_date": date.today().strftime("%Y-%m-%d"), "n_universe": 0}
    else:
        with open(args.input, encoding="utf-8") as f:
            scan_result = json.load(f)

    generate_html(scan_result, args.output)


if __name__ == "__main__":
    main()
