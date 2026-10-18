"""
Donchian 突破 (多空都做, 帶 ATR 止蝕) —— 逐根 K 用 loop 寫嘅例子
有啲規則 (例如移動止蝕、記住入場價) 用 for loop 寫最直觀, 照樣回傳 position 就得

規則: 收市破 N 根最高位 → 開多; 破 N 根最低位 → 開空
      持倉時如果收市反方向行咗 2 x ATR (由入場價計) → 平倉
"""
N, ATR_N, ATR_MULT = 55, 14, 2.0
COST = 0.00005

def strategy(df, ta):
    hi = ta.highest(df.high, N).shift(1)          # 前 N 根 (唔包今根)
    lo = ta.lowest(df.low, N).shift(1)
    atr = ta.atr(df, ATR_N)
    c, H, L, A = df.close.to_numpy(), hi.to_numpy(), lo.to_numpy(), atr.to_numpy()
    pos = [0] * len(df); cur = 0; entry = 0.0
    for i in range(len(df)):
        if cur == 1 and c[i] < entry - ATR_MULT * A[i]: cur = 0
        if cur == -1 and c[i] > entry + ATR_MULT * A[i]: cur = 0
        if c[i] > H[i] and cur != 1: cur, entry = 1, c[i]
        elif c[i] < L[i] and cur != -1: cur, entry = -1, c[i]
        pos[i] = cur
    return dict(position=pos, plots={f"High{N}": hi, f"Low{N}": lo})
