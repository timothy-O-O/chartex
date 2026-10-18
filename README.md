# chartEX

TradingView 風格嘅 Python 看圖 + 回測程式 (PyQt5 + pyqtgraph)。

## 功能

- **數據源**: Yahoo Finance / IBKR (TWS 或 IB Gateway) / 本機 CSV (例如 MT5 匯出嘅 1 分鐘 K)
- **圖表**: Candle / OHLC / Line, 1m 至 1W 週期 + 自訂 N 分鐘, 成交量, SMA / EMA / RMA / RSI
- **畫線**: 趨勢線、水平線、垂直線, 價格軸顯示價位
- **自選清單**: 多個 watchlist 分頁
- **實時**: IBKR 實時串流 (股票 + 外匯)
- **回測**: 策略寫成 Python 檔, 可以同時行幾個策略; 圖上顯示買賣位、每單 pips、比較表、資金曲線
- 幾百萬根 1 分鐘 K 都唔會卡 (只畫可見範圍)

## 安裝

```bash
pip install -r requirements.txt
pip install ib_insync          # 用 IBKR 先需要
python chartex.py
```

## 回測策略

將策略 `.py` 放入 `strategies/` 資料夾, 每個檔要有:

```python
def strategy(df, ta):
    fast, slow = ta.ema(df.close, 20), ta.ema(df.close, 50)
    pos = (fast > slow).astype(int) - (fast < slow).astype(int)    # 1 = 多, -1 = 空, 0 = 冇倉
    return dict(position=pos, plots={"EMA20": fast, "EMA50": slow})
```

或者用訊號寫法: `return dict(buy=..., sell=..., short=..., cover=...)`。
可選設定: `FILL = "next_open"` (預設, 下一根開市價成交)、`COST` (每邊成本)、`PIP`。
詳細說明睇 `strategies/ema_cross.py` 最頂嘅註解。

## IBKR

喺「設定」填 IP / Port / Client ID, 撳「測試連線」。TWS 要開 API (Global Configuration → API → Settings → Enable ActiveX and Socket Clients)。

| | Port |
|---|---|
| TWS 模擬 / 真實 | 7497 / 7496 |
| Gateway 模擬 / 真實 | 4002 / 4001 |

## 設定檔

設定、工作區、自選清單存喺用戶資料夾 (`~/.chartex_settings.json`, `~/.chartex_last.json`, `~/.chartex_watchlists.json`), 唔喺 repo 入面。
第一次執行會自動複製舊版 MyChart 嘅設定、工作區同自選清單 (舊檔保留唔郁)。

## 免責聲明

只供學習及研究用途, 唔係投資建議。回測結果唔代表將來表現。
