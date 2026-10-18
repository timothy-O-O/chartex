"""
EMA 交叉 (多空都做) —— 「position 寫法」例子

規則: 快線 EMA 喺慢線上面 = 持多; 喺下面 = 持空 (交叉時自動反手)

===== 策略檔寫法 (所有策略都一樣) =====
def strategy(df, ta):
    df  = 目前圖表週期嘅 K 線 (pandas DataFrame, index 係時間)
          欄位: df.open, df.high, df.low, df.close, df.volume
    ta  = 指標工具: ta.sma(s, n)  ta.ema(s, n)  ta.rma(s, n)  ta.rsi(s, n)  ta.atr(df, n)
                    ta.highest(s, n)  ta.lowest(s, n)  ta.crossover(a, b)  ta.crossunder(a, b)
                    (crossover / crossunder 嘅 b 可以係數字, 例如 ta.crossover(rsi, 30))

    回傳 (二揀一):
      A) dict(position=...)   每根 K 收市後想持嘅倉: 1 = 多, -1 = 空, 0 = 冇倉
      B) dict(buy=..., sell=..., short=..., cover=...)   True/False 訊號
         buy = 開多 (有空倉會先反手), sell = 平多, short = 開空 (有多倉會先反手), cover = 平空
         唔用嘅可以唔寫, 例如只做多就淨係 buy + sell
    另外可以加 plots={"名": series, ...} → 疊加喺價格圖 (例如你用嚟計嘅均線)

可選設定 (寫喺檔案最上面):
    FILL = "next_open"   訊號 K 收市出訊號, 下一根 K 開市價成交 (預設, 避免偷看未來)
           "close"       用訊號 K 收市價成交
    COST = 0.00008       每次買 / 賣嘅成本, 價格單位 (呢度 = 0.8 pip; 一個來回扣兩次)
    PIP  = 0.0001        1 pip 等於幾多 (唔寫就自動: 價格 > 20 當 0.01, 否則 0.0001)
"""
FAST, SLOW = 20, 50
FILL = "next_open"
COST = 0.00005          # 0.5 pip 每邊 (USDJPY 要改 0.005)

def strategy(df, ta):
    fast, slow = ta.ema(df.close, FAST), ta.ema(df.close, SLOW)
    pos = (fast > slow).astype(int) - (fast < slow).astype(int)
    pos.iloc[:SLOW] = 0                                         # 慢線未穩定之前唔入場
    return dict(position=pos, plots={f"EMA{FAST}": fast, f"EMA{SLOW}": slow})
