"""
RSI 超賣反彈 (只做多) —— 「buy / sell 訊號寫法」例子

規則: RSI 由下向上穿過 30 → 買入; RSI 升穿 70 或者 跌穿止蝕位 (入場前 N 根最低位) → 賣出
格式說明睇 ema_cross.py 最頂嘅註解
"""
N_RSI, LOW, HIGH = 14, 30, 70
STOP_LOOKBACK = 20
COST = 0.00005

def strategy(df, ta):
    rsi = ta.rsi(df.close, N_RSI)
    buy = ta.crossover(rsi, LOW)
    stop = ta.lowest(df.low, STOP_LOOKBACK).shift(1)          # 前 20 根最低位 (唔包今根, 冇偷看)
    sell = ta.crossover(rsi, HIGH) | (df.close < stop)
    return dict(buy=buy, sell=sell, plots={f"止蝕參考 Low{STOP_LOOKBACK}": stop})
