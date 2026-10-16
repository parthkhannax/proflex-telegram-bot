# Proflex Live Sector Rotation Dashboard

Real-time sector-ETF performance vs SPY, for a dedicated second-screen tab.

## Run (easiest)
```
~/Desktop/claude-work/sector-dashboard/start.sh
```
That's it — it kills any old instance, installs deps if needed, starts the server, and opens the dashboard in your browser automatically.

## Run (manual)
```
cd ~/Desktop/claude-work/sector-dashboard
pip install -r requirements.txt
python3 server.py
```
Then open http://localhost:5050. Leave it open — it self-refreshes every 90 seconds.

## Stop
```
pkill -f "python3 server.py"
```

## Structure
- `tickers.py` — the ~55-ticker universe + the 6-ticker "Frontier" watchlist (DRAM, NLR, NASA, HUMN, EUV, GRID). Edit this to add/remove tickers.
- `server.py` — Flask backend, polls yfinance in a background thread, serves `/api/sectors`.
- `index.html` — dashboard UI (Proflex brand, Chart.js), polls the backend and re-renders.

## Views
1. Top 5 outperformers / underperformers vs SPX (1-Day, with 1-Week overlay)
2. Frontier watchlist (fixed basket)
3. Full sortable table of all tickers
