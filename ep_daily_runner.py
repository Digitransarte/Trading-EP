name: EP Daily Scanner

"on":
  schedule:
    - cron: '30 22 * * 1-5'
  workflow_dispatch:
    inputs:
      min_score:
        description: 'Score minimo para notificar (0-100)'
        required: false
        default: '50'
      min_gap:
        description: 'Gap minimo (%)'
        required: false
        default: '8'
      use_claude:
        description: 'Usar Claude para analise? (true/false)'
        required: false
        default: 'true'

jobs:
  ep_scan:
    name: EP Daily Scan
    runs-on: ubuntu-latest
    timeout-minutes: 30

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run EP Scanner
        env:
          POLYGON_API_KEY:    ${{ secrets.POLYGON_API_KEY }}
          ANTHROPIC_API_KEY:  ${{ secrets.ANTHROPIC_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID:   ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          MIN_SCORE="${{ github.event.inputs.min_score || '50' }}"
          MIN_GAP="${{ github.event.inputs.min_gap || '8' }}"
          USE_CLAUDE="${{ github.event.inputs.use_claude || 'true' }}"

          if [ "$USE_CLAUDE" = "false" ]; then
            python ep_daily_runner.py \
              --min-score "$MIN_SCORE" \
              --min-gap "$MIN_GAP" \
              --no-claude \
              --output-json results.json
          else
            python ep_daily_runner.py \
              --min-score "$MIN_SCORE" \
              --min-gap "$MIN_GAP" \
              --output-json results.json
          fi

      - name: Upload scan results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: ep-scan-${{ github.run_id }}
          path: results.json
          retention-days: 30
