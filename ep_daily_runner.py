name: EP Daily Scanner

on:
  schedule:
    - cron: '30 22 * * 1-5'
  workflow_dispatch:
    inputs:
      min_score:
        description: 'Score minimo'
        required: false
        default: '50'
      use_claude:
        description: 'Usar Claude (true/false)'
        required: false
        default: 'true'

jobs:
  ep_scan:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - name: Run Scanner
        env:
          POLYGON_API_KEY: ${{ secrets.POLYGON_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: python ep_daily_runner.py --min-score ${{ github.event.inputs.min_score || '50' }}
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: ep-scan-${{ github.run_id }}
          path: results.json
          retention-days: 30
