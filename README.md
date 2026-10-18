# chartEX

A TradingView-style charting and backtesting app in Python (PyQt5 + pyqtgraph).

> The user interface is available in **English** and **Traditional Chinese (Cantonese)**.
> Switch in Settings (設定) → Language (takes effect after restart). Default for new installs: English.

On first start chartEX opens AAPL (daily) from Yahoo Finance, so it works without an IBKR connection.
If a restored workspace contains IBKR charts and TWS / Gateway is not running, those charts are left unloaded
(no error pop-ups); start TWS and press Enter in the Symbol box to load them.

## Features

- **Data sources**: Yahoo Finance / IBKR (TWS or IB Gateway) / local CSV (e.g. 1-minute bars exported from MT5)
- **Charts**: Candle / OHLC / Line, 1m to 1W timeframes + custom N-minute, volume, SMA / EMA / RMA / RSI
- **Drawing tools**: trend lines, horizontal and vertical lines, with prices shown on the price axis
- **Watchlists**: multiple watchlist tabs
- **Live data**: IBKR real-time streaming (stocks + FX)
- **Backtesting**: strategies are plain Python files; run several at once, with buy/sell markers on the chart, pips per trade, a comparison table and equity curves
- Handles millions of 1-minute bars smoothly (only the visible range is drawn)

## Installation

```bash
pip install -r requirements.txt
pip install ib_insync          # only needed for IBKR
python chartex.py
```

## Writing strategies

Put strategy `.py` files in the `strategies/` folder. Each file needs:

```python
def strategy(df, ta):
    fast, slow = ta.ema(df.close, 20), ta.ema(df.close, 50)
    pos = (fast > slow).astype(int) - (fast < slow).astype(int)    # 1 = long, -1 = short, 0 = flat
    return dict(position=pos, plots={"EMA20": fast, "EMA50": slow})
```

Or use the signal style: `return dict(buy=..., sell=..., short=..., cover=...)`.

Optional settings: `FILL = "next_open"` (default: filled at the next bar's open), `COST` (cost per side), `PIP`.
See the header of `strategies/ema_cross.py` for details.

## IBKR

Enter IP / Port / Client ID in Settings and press Test connection.
Enable the API in TWS: Global Configuration → API → Settings → Enable ActiveX and Socket Clients.

| | Port |
|---|---|
| TWS paper / live | 7497 / 7496 |
| Gateway paper / live | 4002 / 4001 |

## Settings files

Settings, workspace and watchlists are stored in your home folder
(`~/.chartex_settings.json`, `~/.chartex_last.json`, `~/.chartex_watchlists.json`), not in the repo.
On first run, settings, workspace and watchlists from the older MyChart version are copied automatically (the old files are left untouched).

## Disclaimer

For learning and research only. Not investment advice. Backtest results do not guarantee future performance.
