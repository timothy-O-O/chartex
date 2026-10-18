"""
chartEX - TradingView 風格看圖 + 回測 (PyQt5 + pyqtgraph)
安裝:  pip install PyQt5 pyqtgraph pandas numpy yfinance        (用 IBKR 再加: pip install ib_insync)
執行:  python chartex.py               (或 python chartex.py 工作區.json)
數據源: Yahoo Finance / IBKR (TWS 或 Gateway, port 7497) / 本機 CSV
       - Yahoo / IBKR: 工具列「代號」輸入 (例如 AAPL, 700, GBPUSD), 或者撳右邊「自選」清單
         外匯可以寫 GBPUSD / GBP.USD / GBP/USD / GBPUSD=X, Yahoo 同 IBKR 都會自動認
       - IBKR 連線 (IP / Port / Client ID / 實時或延遲) 喺「設定」改, 有「測試連線」掣
       - CSV: 右邊「CSV」分頁揀資料夾 + 檔案 (MT5 匯出格式等, 見 read_ohlc)
日期:  第 2 行工具列揀日期範圍 (預設 2012-01-01 → 今日), 圖表同回測都只用呢段數據
       Yahoo 分鐘 / 小時數據本身只有最近一段時間 (1m 7 日, 5m-30m 60 日, 1h/4h 730 日)
實時:  「實時」掣 (只支援 IBKR), 每秒更新最後一根 K
回測:  策略 .py 放喺 strategies/ (同本程式同一個位置), 「策略」掣可以剔幾個同時行
"""
import sys, json, os, math, time, traceback, importlib.util
from collections import OrderedDict
import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets as W

HOME = os.path.expanduser("~")
LAST_FILE = os.path.join(HOME, ".chartex_last.json")          # 上次關閉時嘅工作區 (開機自動復原)
CFG_FILE = os.path.join(HOME, ".chartex_settings.json")       # 設定
WL_FILE = os.path.join(HOME, ".chartex_watchlists.json")      # 自選清單

def migrate_old_files():
    """第一次用 chartEX: 自動複製舊版 MyChart 嘅設定 / 工作區 / 自選清單 (舊檔保留唔郁)"""
    import shutil
    for new, old in ((LAST_FILE, ".mychart14_last.json"), (CFG_FILE, ".mychart14_settings.json"),
                     (WL_FILE, ".mychart_watchlists.json")):
        old = os.path.join(HOME, old)
        if not os.path.exists(new) and os.path.exists(old):
            try: shutil.copy2(old, new)
            except OSError: pass
THEMES = {"深色": dict(ui_bg="#1e222d", ui_fg="#d1d4dc", chart_bg="#131722", chart_fg="#d1d4dc", line="#d1d4dc", info="#d1d4dc", grid="#2a2e39"),
          "淺色": dict(ui_bg="#f0f3fa", ui_fg="#131722", chart_bg="#ffffff", chart_fg="#131722", line="#131722", info="#131722", grid="#e0e3eb")}
# up/down = K 線主體顏色; up_b/down_b = 邊框; up_w/down_w = 燭芯 (None = 跟主體顏色)
CFG = dict(THEMES["深色"], font="", up="#26a69a", down="#ef5350", up_b=None, down_b=None, up_w=None, down_w=None,
           body_on=True, border_on=True, wick_on=True, prev_close=False, grid_dash=True, minor_on=True, icol={},
           folder="", tz_off=0, auto_y=True, strat_dir="",
           date_from="2012-01-01", date_to="",
           ib_host="127.0.0.1", ib_port=7497, ib_client=11, ib_mkt=1)   # IBKR 連線 (喺「設定」改); ib_mkt: 1 實時 / 3 延遲          # date_to 留空 = 到今日
COLOR_KEYS = [("ui_bg", "介面背景"), ("ui_fg", "介面文字"), ("chart_bg", "圖表背景"), ("chart_fg", "座標軸 / 文字"),
              ("grid", "每月垂直格線"), ("line", "Line 圖顏色"), ("info", "K 線資訊文字")]
COLORS = ["#f6c343", "#2962ff", "#e040fb", "#00bcd4", "#ff9800", "#8bc34a"]
MAX_CACHE = 3        # 記憶體最多保留幾個 CSV 嘅原始數據 (十幾年 1 分鐘 K 每個約 300 MB)
UR = QtCore.Qt.UserRole
DRAW_COLOR = "#f6c343"   # 畫線顏色 (價格軸標籤同色)
BT_BUY, BT_SELL, BT_WIN, BT_LOSS = "#2962ff", "#ff6d00", "#26a69a", "#ef5350"     # 回測: 買 / 賣 圖示, 賺 / 蝕
STRAT_COLORS = ["#2962ff", "#ff6d00", "#ab47bc", "#00bfa5", "#fdd835", "#ec407a"]   # 多個策略: 每個策略一隻顏色
BT_PLOT_COLORS = ["#ff9800", "#00bcd4", "#e040fb", "#8bc34a", "#ffeb3b", "#90caf9"]
DEFAULT_STRAT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategies")
def strat_dir(): return CFG.get("strat_dir") or DEFAULT_STRAT_DIR

def load_cfg():
    try: CFG.update(json.load(open(CFG_FILE, encoding="utf-8")))
    except Exception: pass
def save_cfg():
    json.dump(CFG, open(CFG_FILE, "w", encoding="utf-8"), indent=1)
def icolor(kind, per):
    ic = CFG["icol"]; return ic.setdefault(f"{kind}|{per}", COLORS[len(ic) % len(COLORS)])
def same_path(a, b):
    return bool(a) and bool(b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

# ---------------------------------------------------------------- 時間週期 (全部由 CSV 原始數據 resample 出嚟)
I, D = "%y-%m-%d %H:%M", "%Y-%m-%d"
TF = {"1m": None, "5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h", "4h": "4h", "1D": "1D", "1W": "W-SUN"}
def tf_rule(tf):
    if tf in TF: return TF[tf]
    return f"{int(tf[:-1])}min"                       # 自訂 N 分鐘
def tf_fmt(tf): return D if tf in ("1D", "1W") else I

# ---------------------------------------------------------------- 指標
def rma(s, n): return s.ewm(alpha=1 / n, adjust=False).mean()
def calc(kind, df, n):
    c = df["close"]
    if kind == "SMA": return c.rolling(n).mean()
    if kind == "EMA": return c.ewm(span=n, adjust=False).mean()
    if kind == "RMA": return rma(c, n)
    if kind == "RSI":
        d = c.diff()
        return 100 - 100 / (1 + rma(d.clip(lower=0), n) / rma(-d.clip(upper=0), n))

# ---------------------------------------------------------------- 讀 CSV + 快取
AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
_RAW = OrderedDict()      # path -> (mtime, 原始 df)
_TFC = {}                 # (path, mtime, tf, 時區偏移) -> resample 後 df

def read_ohlc(path):
    with open(path, encoding="utf-8-sig", errors="ignore") as f: head = f.readline()
    sep = max((",", "\t", ";"), key=head.count)
    df = pd.read_csv(path, sep=sep, encoding="utf-8-sig")
    df.columns = [str(c).strip().strip("<>").strip().lower() for c in df.columns]
    cols = set(df.columns)
    miss = [k for k in ("open", "high", "low", "close") if k not in cols]
    if miss: raise ValueError(f"搵唔到欄位 {miss}\n檔案嘅欄位: {list(df.columns)}")
    if "timestamp" in cols: t = df["timestamp"]
    elif {"date", "time"} <= cols: t = df["date"].astype(str) + " " + df["time"].astype(str)
    else: t = df[next((c for c in ("datetime", "time", "date") if c in cols), df.columns[0])]
    if pd.api.types.is_numeric_dtype(t):
        t = pd.to_datetime(t, unit="ms" if t.max() > 1e11 else "s")            # Unix 時間 (UTC)
    else:
        t = pd.to_datetime(t.astype(str).str.replace(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})", r"\1-\2-\3", regex=True))
    ix = pd.DatetimeIndex(t)
    if ix.tz is not None: ix = ix.tz_convert(None)
    out = pd.DataFrame({k: pd.to_numeric(df[k], errors="coerce").to_numpy(float) for k in ("open", "high", "low", "close")}, index=ix)
    vol = next((c for c in ("tick_volume", "tickvol", "volume", "vol", "real_volume") if c in cols), None)
    out["volume"] = pd.to_numeric(df[vol], errors="coerce").fillna(0).to_numpy(float) if vol else 0.0
    out = out.dropna(subset=["open", "high", "low", "close"])
    if not out.index.is_monotonic_increasing: out = out.sort_index(kind="stable")
    out = out[~out.index.duplicated(keep="last")]
    if out.empty: raise ValueError("檔案冇有效數據")
    return out

def load_raw(path):
    if not os.path.isfile(path): raise FileNotFoundError(f"搵唔到檔案: {path}")
    mt = os.path.getmtime(path); hit = _RAW.get(path)
    if hit and hit[0] == mt: _RAW.move_to_end(path); return mt, hit[1]
    df = read_ohlc(path); _RAW[path] = (mt, df); _RAW.move_to_end(path)
    while len(_RAW) > MAX_CACHE: _RAW.popitem(last=False)
    for k in [k for k in _TFC if k[0] not in _RAW or (k[0] == path and k[1] != mt)]: del _TFC[k]
    return mt, df

def date_range():
    """日期範圍 (兩邊都包埋), 回傳 [d0, d1) 方便切片"""
    d0 = pd.Timestamp(CFG.get("date_from") or "1900-01-01")
    d1 = pd.Timestamp(CFG.get("date_to") or pd.Timestamp.today().normalize()) + pd.Timedelta(days=1)
    return d0, d1

def get_data(path, tf):
    mt, raw = load_raw(path); off = CFG["tz_off"]; d0, d1 = date_range(); key = (path, mt, tf, off, d0, d1)
    if key not in _TFC:
        df = raw if not off else raw.set_axis(raw.index + pd.Timedelta(hours=off))
        a, b = df.index.searchsorted(d0), df.index.searchsorted(d1)          # 先切日期, 再 resample
        if a >= b:
            raise ValueError(f"{d0:%Y-%m-%d} → {d1 - pd.Timedelta(days=1):%Y-%m-%d} 冇數據\n"
                             f"呢個檔案嘅數據範圍: {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}")
        df = df.iloc[a:b]
        rule = tf_rule(tf)
        if rule: df = df.resample(rule, label="left", closed="left").agg(AGG).dropna(subset=["open"])
        _TFC[key] = df
        while len(_TFC) > 24: del _TFC[next(iter(_TFC))]                  # 唔好無限咁儲
    return _TFC[key]

# ---------------------------------------------------------------- 網上數據源 (Yahoo / IBKR)
SRC_YAHOO, SRC_IBKR, SRC_CSV = 0, 1, 2
SRC_NAMES = ["Yahoo Finance", "IBKR", "CSV 檔案"]
# 週期: (Yahoo interval, Yahoo period 或 None = 用日期範圍, IBKR bar size, IBKR duration 或 None = 用日期範圍, resample)
ONLINE = {"1m": ("1m", "7d", "1 min", "2 D", None), "5m": ("5m", "60d", "5 mins", "5 D", None),
          "15m": ("15m", "60d", "15 mins", "2 W", None), "30m": ("30m", "60d", "30 mins", "1 M", None),
          "1h": ("1h", "730d", "1 hour", "1 M", None), "4h": ("1h", "730d", "1 hour", "3 M", "4h"),
          "1D": ("1d", None, "1 day", None, None), "1W": ("1wk", None, "1 week", None, None)}
def online_spec(tf):
    return ONLINE.get(tf) or ("1m", "7d", "1 min", "5 D", f"{int(tf[:-1])}min")        # 自訂 N 分鐘: 由 1 分鐘合成
def tf_minutes(tf):
    if tf[:-1].isdigit() and tf[-1] == "m": return int(tf[:-1])
    if tf[:-1].isdigit() and tf[-1] == "h": return int(tf[:-1]) * 60
    return None
FX_CCY = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "HKD", "CNH", "SGD", "SEK", "NOK", "DKK",
          "MXN", "ZAR", "TRY", "PLN", "HUF", "CZK", "ILS", "KRW"}
def parse_fx(sym):
    """外匯代號 → 'GBPUSD' (接受 GBPUSD / GBP.USD / GBP/USD / GBPUSD=X); 唔係外匯就回傳 None"""
    t = sym.upper().replace("=X", "").replace(".", "").replace("/", "").replace(" ", "").strip()
    return t if len(t) == 6 and t[:3] in FX_CCY and t[3:] in FX_CCY and t[:3] != t[3:] else None
def ib_contract(sym):
    from ib_insync import Stock, Forex
    fx = parse_fx(sym)
    if fx: return Forex(fx)                                              # IDEALPRO 外匯
    s = sym.upper().strip()
    return Stock(s, "SEHK", "HKD") if s.isdigit() else Stock(s, "SMART", "USD")

class YahooSource:
    def fetch(self, sym, tf):
        import yfinance as yf
        if sym.isdigit(): sym = sym.zfill(4) + ".HK"                  # 港股 700 -> 0700.HK
        elif parse_fx(sym): sym = parse_fx(sym) + "=X"                  # 外匯 GBP.USD -> GBPUSD=X
        yi, per = online_spec(tf)[:2]; d0, d1 = date_range()
        if per: df = yf.download(sym, period=per, interval=yi, progress=False, auto_adjust=True)
        else: df = yf.download(sym, start=f"{d0:%Y-%m-%d}", end=f"{d1:%Y-%m-%d}", interval=yi, progress=False, auto_adjust=True)
        if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
        df.columns = [str(x).lower() for x in df.columns]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df[["open", "high", "low", "close", "volume"]].dropna()
        if len(df):                                                   # 除權調整後價格有好多小數, 留 6 位有效數字
            dec = max(2, 6 - math.ceil(math.log10(max(float(df.close.abs().median()), 1e-9))))
            df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].round(dec)
        return df

class IBKRSource:
    ib = None
    @staticmethod
    def connect():
        from ib_insync import IB
        if IBKRSource.ib is None or not IBKRSource.ib.isConnected():
            IBKRSource.ib = IB()
            try: IBKRSource.ib.connect(CFG["ib_host"], int(CFG["ib_port"]), clientId=int(CFG["ib_client"]), timeout=8)
            except Exception as e:
                IBKRSource.ib = None
                raise ConnectionError(f"連唔到 IBKR {CFG['ib_host']}:{CFG['ib_port']} (client {CFG['ib_client']})\n"
                                      f"請檢查 TWS / Gateway 有冇開、API 有冇啟用、Port 啱唔啱 (喺「設定」改)\n{type(e).__name__}: {e}") from None
        return IBKRSource.ib
    @staticmethod
    def disconnect():
        if IBKRSource.ib is not None:
            try: IBKRSource.ib.disconnect()
            except Exception: pass
        IBKRSource.ib = None
    def fetch(self, sym, tf):
        from ib_insync import util
        ib = self.connect(); c = online_spec(tf); dur = c[3]
        if dur is None:                                                  # 日 / 週: 由開始日期計要幾多年
            years = max(1, math.ceil((pd.Timestamp.today() - date_range()[0]).days / 365)); dur = f"{min(years, 30)} Y"
        fx = parse_fx(sym) is not None                                   # 外匯冇成交價, 要用 MIDPOINT; 24 小時所以唔限 RTH
        bars = ib.reqHistoricalData(ib_contract(sym), "", dur, c[2], "MIDPOINT" if fx else "TRADES", not fx, 1)
        df = util.df(bars)
        if df is None or df.empty: raise ValueError("IBKR 冇返數據")
        df["date"] = pd.to_datetime(df["date"]); df = df.set_index("date")
        if df.index.tz is not None: df.index = df.index.tz_localize(None)
        df = df[["open", "high", "low", "close", "volume"]].astype(float)
        df["volume"] = df["volume"].clip(lower=0)                         # MIDPOINT 冇成交量 (-1) → 0
        return df

SOURCES = {SRC_YAHOO: YahooSource(), SRC_IBKR: IBKRSource()}

def get_df(src, sym, tf):
    """所有數據源統一入口: 回傳日期範圍內嘅 K 線"""
    if src == SRC_CSV: return get_data(sym, tf)
    df = SOURCES[src].fetch(sym, tf); rule = online_spec(tf)[4]
    if rule and not df.empty: df = df.resample(rule, label="left", closed="left").agg(AGG).dropna(subset=["open"])
    if df.empty: raise ValueError("冇數據 (請檢查代號 / 數據源 / 週期)")
    d0, d1 = date_range(); out = df[(df.index >= d0) & (df.index < d1)].copy()
    if out.empty:
        raise ValueError(f"{d0:%Y-%m-%d} → {d1 - pd.Timedelta(days=1):%Y-%m-%d} 冇數據\n"
                         f"拎到嘅數據範圍: {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}\n"
                         f"(Yahoo 分鐘 / 小時數據只有最近一段時間, 可以將日期範圍改到最近)")
    return out

# ---------------------------------------------------------------- 圖元件
class DateAxis(pg.AxisItem):
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.dates, self.fmt = pd.DatetimeIndex([]), D
    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            i = int(round(v)); ok = 0 <= i < len(self.dates) and abs(v - i) < 1e-6
            out.append(self.dates[i].strftime(self.fmt) if ok else "")
        return out

def price_decimals(df):
    """由數據推算小數位: GBPUSD 4-5 位, USDJPY 3 位 (最少 2 位, 最多 6 位)"""
    v = df[["open", "high", "low", "close"]].tail(300).values.ravel().astype(float); v = v[np.isfinite(v)]
    tol = 5e-7 * np.maximum(1.0, np.abs(v))
    for d in range(7):
        if len(v) == 0 or np.all(np.abs(v - np.round(v, d)) <= tol): return max(d, 2)
    return 6

class PriceAxis(pg.AxisItem):
    """喺價格軸上面上下拖 = 收窄 / 放大價格範圍"""
    dec = None
    def tickStrings(self, values, scale, spacing):
        if self.dec is None: return super().tickStrings(values, scale, spacing)
        places = max(self.dec, math.ceil(-math.log10(spacing * scale))) if spacing > 0 else self.dec
        return [f"{v * scale:.{places}f}" for v in values]
    def tickValues(self, minVal, maxVal, size):
        levels = super().tickValues(minVal, maxVal, size)
        if not levels: return levels
        sp, vals = levels[0]
        if len(vals) > 6:
            sp *= 2; vals = [v for v in vals if abs(v / sp - round(v / sp)) < 1e-6]
        return [(sp, vals)]
    marks = None                                     # 函數, 回傳 [(價格, 顏色), ...] → 喺價格軸上面畫價格標籤
    def paint(self, p, opt, widget):
        super().paint(p, opt, widget)
        vb = self.linkedView()
        if self.marks is None or vb is None: return
        r = self.mapRectFromParent(self.geometry()); f = self.style.get("tickFont") or self.font(); p.setFont(f)
        fh = QtGui.QFontMetrics(f).height() + 4; dec = self.dec if self.dec is not None else 2
        x0, w = r.left(), r.width()
        for v, col in self.marks():
            if v is None or not np.isfinite(v): continue
            y = self.mapFromScene(vb.mapViewToScene(QtCore.QPointF(0, v))).y()
            if y < r.top() - fh / 2 or y > r.bottom() + fh / 2: continue      # 唔喺可見範圍就唔畫
            y = min(max(y, r.top() + fh / 2), r.bottom() - fh / 2)
            c = pg.mkColor(col); box = QtCore.QRectF(x0, y - fh / 2, w, fh)
            p.setPen(QtCore.Qt.NoPen); p.setBrush(c); p.drawRect(box)
            p.setPen(pg.mkPen("#000" if c.lightness() > 150 else "#fff"))
            p.drawText(box.adjusted(4, 0, 0, 0), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, f"{v:.{dec}f}")
    def mouseDragEvent(self, ev):
        if ev.button() == QtCore.Qt.LeftButton and self.linkedView() is not None:
            ev.accept(); dy = ev.pos().y() - ev.lastPos().y()
            self.linkedView().scaleBy(y=math.exp(dy * 0.01))
        else: super().mouseDragEvent(ev)

def up_flags(df):
    c, o = df.close.to_numpy(), df.open.to_numpy()
    return c >= (np.r_[o[:1], c[:-1]] if CFG["prev_close"] else o)

class MonthGrid(pg.GraphicsObject):
    """每月第一根 K 前面畫一條垂直格線"""
    def __init__(self, xs):
        super().__init__(); self.xs = np.asarray(xs, dtype=float); self.setZValue(-100)
    def viewRangeChanged(self): self.prepareGeometryChange(); self.update()
    def boundingRect(self):
        vr = self.viewRect(); return QtCore.QRectF(vr) if vr is not None else QtCore.QRectF()
    def paint(self, p, *a):
        vr = self.viewRect()
        if vr is None: return
        p.setPen(pg.mkPen(CFG["grid"], width=1, style=QtCore.Qt.DashLine if CFG["grid_dash"] else QtCore.Qt.SolidLine))
        for x in self.xs[(self.xs >= vr.left()) & (self.xs <= vr.right())]:
            p.drawLine(QtCore.QPointF(x, vr.top()), QtCore.QPointF(x, vr.bottom()))

class MinorGrid(pg.GraphicsObject):
    """價格圖: 兩條大橫線之間加 4 條細虛線"""
    def __init__(self, axis):
        super().__init__(); self.axis = axis; self.setZValue(-101)
    def viewRangeChanged(self): self.prepareGeometryChange(); self.update()
    def boundingRect(self):
        vr = self.viewRect(); return QtCore.QRectF(vr) if vr is not None else QtCore.QRectF()
    def paint(self, p, *a):
        vr, vb = self.viewRect(), self.getViewBox()
        if vr is None or vb is None: return
        lo, hi = vb.viewRange()[1]; lv = self.axis.tickValues(lo, hi, self.axis.height())
        if not lv or not lv[0][1] or lv[0][0] <= 0: return
        step = lv[0][0] / 5
        if (hi - lo) / step > 300: return
        c = pg.mkColor(CFG["chart_fg"]); c.setAlpha(60); p.setPen(pg.mkPen(c, width=1, style=QtCore.Qt.DashLine))
        for k in range(math.ceil(lo / step), math.floor(hi / step) + 1):
            if k % 5: p.drawLine(QtCore.QPointF(vr.left(), k * step), QtCore.QPointF(vr.right(), k * step))

class ViewportItem(pg.GraphicsObject):
    """只畫可見範圍; 太密 (每根 K < 2 pixel) 就合併成一組組, 幾百萬根 1 分鐘 K 都唔會卡"""
    def __init__(self, n, y0, y1):
        super().__init__(); self.n = n
        y0 = float(y0) if np.isfinite(y0) else 0.0; y1 = float(y1) if np.isfinite(y1) else 1.0
        self.rect = QtCore.QRectF(-1, y0, n + 1, max(y1 - y0, 1e-9))
    def boundingRect(self): return self.rect
    def viewRangeChanged(self): self.update()
    def visible(self):
        vr, vb = self.viewRect(), self.getViewBox()
        if vr is None or vb is None or self.n == 0: return None
        lo = max(0, int(math.floor(vr.left()))); hi = min(self.n, int(math.ceil(vr.right())) + 1)
        if hi <= lo: return None
        bins = max(int(vb.width() / 2), 50)
        if hi - lo <= bins: return lo, hi, 0
        k = math.ceil((hi - lo) / bins); lo -= lo % k            # 組別對齊 k 嘅倍數, 拖動時唔會閃
        st = np.arange(lo, hi, k); en = np.minimum(st + k, hi) - 1
        return lo, hi, (k, st, en)

class PriceItem(ViewportItem):
    """Candle (主體 / 邊框 / 燭芯 顏色各自設定) 或 OHLC bars"""
    def __init__(self, df, kind, up):
        self.o, self.h, self.l, self.c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        super().__init__(len(df), np.nanmin(self.l), np.nanmax(self.h)); self.kind, self.up = kind, up
    def paint(self, p, *a):
        v = self.visible()
        if v is None: return
        lo, hi, b = v; C = CFG
        if b:                                          # 合併模式: 每組畫一條 高-低 線
            k, st, en = b
            hh = np.maximum.reduceat(self.h[lo:hi], st - lo); ll = np.minimum.reduceat(self.l[lo:hi], st - lo)
            up = self.c[en] >= self.o[st]; xs = (st + en) / 2
            for flag, col in ((True, C["up"]), (False, C["down"])):
                m = up == flag
                if m.any():
                    p.setPen(pg.mkPen(col)); p.drawLines([QtCore.QLineF(x, a_, x, b_) for x, a_, b_ in zip(xs[m], ll[m], hh[m])])
            return
        pens = {}
        def pen(c):
            if c not in pens:
                pens[c] = pg.mkPen(c); pens[c].setCapStyle(QtCore.Qt.FlatCap)
            return pens[c]
        for i in range(lo, hi):
            o, h, l, c, up = self.o[i], self.h[i], self.l[i], self.c[i], self.up[i]
            body = C["up"] if up else C["down"]
            if self.kind == "Candle":
                wick = (C["up_w"] or C["up"]) if up else (C["down_w"] or C["down"])
                bord = (C["up_b"] or C["up"]) if up else (C["down_b"] or C["down"])
                top, bot = max(o, c), min(o, c); eps = 1e-9 * max(1.0, abs(top))
                if C["wick_on"]:
                    p.setPen(pen(wick))
                    if bot - l > eps: p.drawLine(QtCore.QPointF(i, l), QtCore.QPointF(i, bot))
                    if h - top > eps: p.drawLine(QtCore.QPointF(i, top), QtCore.QPointF(i, h))
                if top - bot <= eps:                   # 十字星
                    p.setPen(pen(bord if C["border_on"] else body)); p.drawLine(QtCore.QPointF(i - 0.3, o), QtCore.QPointF(i + 0.3, o))
                    continue
                p.setPen(pen(bord) if C["border_on"] else QtCore.Qt.NoPen)
                p.setBrush(pg.mkBrush(body) if C["body_on"] else QtCore.Qt.NoBrush)
                p.drawRect(QtCore.QRectF(i - 0.3, bot, 0.6, top - bot))
            else:
                p.setPen(pen(body))
                if h - l > 1e-9 * max(1.0, abs(h)): p.drawLine(QtCore.QPointF(i, l), QtCore.QPointF(i, h))
                p.drawLine(QtCore.QPointF(i - 0.3, o), QtCore.QPointF(i, o))
                p.drawLine(QtCore.QPointF(i, c), QtCore.QPointF(i + 0.3, c))

class VolumeItem(ViewportItem):
    def __init__(self, vol, up):
        self.v, self.up = vol, up; super().__init__(len(vol), 0, np.nanmax(vol) if len(vol) else 1)
    def paint(self, p, *a):
        v = self.visible()
        if v is None: return
        lo, hi, b = v; cu, cd = pg.mkColor(CFG["up"]), pg.mkColor(CFG["down"])
        if b:
            k, st, en = b; vv = np.maximum.reduceat(self.v[lo:hi], st - lo); up = self.up[en]
            for x, h, u in zip((st + en) / 2, vv, up):
                if h > 0: p.fillRect(QtCore.QRectF(x - k * 0.35, 0, k * 0.7, h), cu if u else cd)
            return
        for i in range(lo, hi):
            if self.v[i] > 0: p.fillRect(QtCore.QRectF(i - 0.35, 0, 0.7, self.v[i]), cu if self.up[i] else cd)

def fast_plot(plot, x, y, pen):
    """只畫可見範圍 + 自動降採樣 (要加入 plot 之後先設定, 否則 pyqtgraph 會報錯)"""
    it = pg.PlotDataItem(x, y, pen=pen, connect="finite"); plot.addItem(it)
    it.setClipToView(True); it.setDownsampling(auto=True, method="peak"); return it


# ---------------------------------------------------------------- 回測: 指標工具 (策略入面用 ta.xxx)
class TA:
    """策略檔入面用: ta.sma(df.close, 20), ta.crossover(fast, slow), ta.rsi(df.close, 14) ..."""
    @staticmethod
    def sma(s, n): return pd.Series(s).rolling(n).mean()
    @staticmethod
    def ema(s, n): return pd.Series(s).ewm(span=n, adjust=False).mean()
    @staticmethod
    def rma(s, n): return rma(pd.Series(s), n)
    @staticmethod
    def rsi(s, n=14):
        d = pd.Series(s).diff(); return 100 - 100 / (1 + rma(d.clip(lower=0), n) / rma(-d.clip(upper=0), n))
    @staticmethod
    def atr(df, n=14):
        pc = df.close.shift(1)
        tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
        return rma(tr, n)
    @staticmethod
    def highest(s, n): return pd.Series(s).rolling(n).max()
    @staticmethod
    def lowest(s, n): return pd.Series(s).rolling(n).min()
    @staticmethod
    def _pair(a, b):
        a = a if isinstance(a, pd.Series) else pd.Series(np.asarray(a, float))
        b = pd.Series(float(b), index=a.index) if np.isscalar(b) else pd.Series(np.asarray(b, float), index=a.index)
        return a, b
    @staticmethod
    def crossover(a, b):          # a 由下向上穿過 b (b 可以係數字, 例如 30)
        a, b = TA._pair(a, b); return (a > b) & (a.shift(1) <= b.shift(1))
    @staticmethod
    def crossunder(a, b):         # a 由上向下穿過 b
        a, b = TA._pair(a, b); return (a < b) & (a.shift(1) >= b.shift(1))

# ---------------------------------------------------------------- 回測: 引擎
def _flags(x, n):
    if x is None: return np.zeros(n, bool)
    a = np.asarray(pd.Series(x).astype(float), float).ravel()
    if len(a) != n: raise ValueError(f"訊號長度 {len(a)} 同 K 線數 {n} 唔一樣")
    return np.nan_to_num(a) != 0

def to_position(res, n):
    """策略回傳 → 每根 K 收市後嘅目標倉位 (1 = 多, -1 = 空, 0 = 冇倉)"""
    if isinstance(res, dict) and "position" not in res:
        b, s, sh, cv = (_flags(res.get(k), n) for k in ("buy", "sell", "short", "cover"))
        pos = np.zeros(n, int); cur = 0; last = 0
        for i in np.flatnonzero(b | s | sh | cv):
            pos[last:i] = cur
            if b[i]: cur = 1                        # 開多 (有空倉就反手)
            elif sh[i]: cur = -1                    # 開空 (有多倉就反手)
            elif s[i] and cur == 1: cur = 0         # 平多
            elif cv[i] and cur == -1: cur = 0       # 平空
            last = i
        pos[last:] = cur
        return pos
    if isinstance(res, dict): res = res["position"]
    a = np.asarray(pd.Series(res).astype(float), float).ravel()
    if len(a) != n: raise ValueError(f"position 長度 {len(a)} 同 K 線數 {n} 唔一樣")
    return np.sign(np.nan_to_num(a)).astype(int)

def backtest(df, pos, fill="next_open", cost=0.0, pip=0.0001):
    """收市出訊號; fill="next_open" = 下一根 K 開市價成交 (預設, 冇偷看未來), "close" = 訊號 K 收市價成交
       cost = 每次買或賣嘅成本 (價格單位, 例如 0.00008 = 0.8 pip), 一個來回扣兩次"""
    o, c, n = df.open.to_numpy(float), df.close.to_numpy(float), len(df)
    prev = np.r_[0, pos[:-1]]; T = []; cur = 0; ent = None
    for i in np.flatnonzero(pos != prev):
        j = i + 1 if fill == "next_open" else i
        if j >= n: break
        px = o[j] if fill == "next_open" else c[i]
        if cur != 0: T.append((*ent, j, px, False))
        cur = int(pos[i]); ent = (j, px, cur) if cur != 0 else None
    if ent is not None: T.append((*ent, n - 1, c[-1], True))          # 未平倉: 用最後收市價計
    a = np.array(T, dtype=float).reshape(-1, 6)
    t = dict(ei=a[:, 0].astype(int), ep=a[:, 1], dir=a[:, 2].astype(int), xi=a[:, 3].astype(int), xp=a[:, 4],
             open=a[:, 5].astype(bool))
    t["gross"] = (t["xp"] - t["ep"]) * t["dir"] / pip                  # 毛利 (未扣成本), pips
    t["costp"] = np.full(len(a), 2 * cost / pip)                      # 成本 (一個來回), pips
    t["pnl"] = (t["xp"] - t["ep"]) * t["dir"] - 2 * cost; t["pips"] = t["pnl"] / pip      # 淨利
    t["pip"], t["fill"], t["cost"], t["n"] = pip, fill, cost, len(a)
    t["sid"] = np.zeros(len(a), int); calc_stats(t)
    return t

TRADE_KEYS = ("ei", "ep", "dir", "xi", "xp", "open", "gross", "costp", "pnl", "pips", "sid")

def calc_stats(t):
    """統計 + 資金曲線 (已平倉, 按出場時間排; 回撤 = 平倉後累積淨利由高位回落最多幾多)"""
    cm = ~t["open"]; o = np.argsort(t["xi"][cm], kind="stable")
    cl = t["pips"][cm][o]; win, loss = cl[cl > 0], cl[cl <= 0]
    eq = np.cumsum(cl); dd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max() if len(cl) else 0.0
    t["stats"] = dict(trades=len(cl), wins=len(win), winrate=100 * len(win) / len(cl) if len(cl) else 0.0,
                      total=cl.sum(), gross=t["gross"][cm].sum(), costs=t["costp"][cm].sum(), avg=cl.mean() if len(cl) else 0.0,
                      pf=win.sum() / -loss.sum() if loss.sum() < 0 else float("inf") if len(win) else 0.0,
                      mdd=dd, best=cl.max() if len(cl) else 0.0, worst=cl.min() if len(cl) else 0.0,
                      bars=float(np.mean((t["xi"] - t["ei"])[cm])) if len(cl) else 0.0,
                      open=t["pips"][t["open"]].sum(), has_open=bool(t["open"].any()))
    t["equity"], t["eq_x"] = eq, t["xi"][cm][o]

def combine(ts):
    """幾個策略各自獨立持倉, 合埋計 (每個策略 1 單位)"""
    m = {k: np.concatenate([t[k] for t in ts]) for k in TRADE_KEYS}
    o = np.argsort(m["ei"], kind="stable"); m = {k: v[o] for k, v in m.items()}
    m.update(n=len(o), name="合計", names=[t["name"] for t in ts], combined=True,
             secs=sum(t.get("secs", 0) for t in ts), pip=ts[0]["pip"])
    calc_stats(m); return m

def run_strategy(path, df):
    """載入策略檔 (每次重新讀, 改完檔案再撳回測就得) → (回測結果, 疊加線)"""
    spec = importlib.util.spec_from_file_location(f"chartex_strategy_{time.time_ns()}", path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    if not hasattr(mod, "strategy"): raise AttributeError("策略檔要有  def strategy(df, ta):")
    n = len(df); res = mod.strategy(df.copy(), TA)
    pos = to_position(res, n)
    pip = getattr(mod, "PIP", None) or (0.01 if float(df.close.iloc[-1]) > 20 else 0.0001)
    t = backtest(df, pos, getattr(mod, "FILL", "next_open"), float(getattr(mod, "COST", 0.0)), pip)
    t["name"] = os.path.splitext(os.path.basename(path))[0]
    plots = []
    for k, (name, v) in enumerate(((res.get("plots") or {}) if isinstance(res, dict) else {}).items()):
        y = np.asarray(pd.Series(v).astype(float), float).ravel()
        if len(y) != n: raise ValueError(f"plots['{name}'] 長度 {len(y)} 同 K 線數 {n} 唔一樣")
        plots.append((str(name), y, BT_PLOT_COLORS[k % len(BT_PLOT_COLORS)]))
    return t, plots

class TradeLayer(pg.GraphicsObject):
    """買 / 賣 圖示 + 入場到出場嘅虛線 + 每單 pips (只畫可見範圍, 幾千單都唔會卡)"""
    def __init__(self, t, df, color=None, slot=0):
        super().__init__(); self.t = t; self.h, self.l = df.high.to_numpy(float), df.low.to_numpy(float)
        self.color, self.off = color, slot * 12                        # 多個策略: 用策略顏色, 每個策略再離遠 12 pixel
        self.c = df.close.to_numpy(float); n = len(df); self.setZValue(20)
        y0, y1 = float(np.nanmin(self.l)), float(np.nanmax(self.h))
        self.rect = QtCore.QRectF(-1, y0, n + 1, max(y1 - y0, 1e-9))
        self.font = QtGui.QFont(W.QApplication.font()); self.font.setPointSizeF(max(self.font.pointSizeF() * 0.85, 7))
    def boundingRect(self): return self.rect
    def viewRangeChanged(self): self.update()
    def tri(self, p, T, i, up):
        if up: q = T.map(QtCore.QPointF(i, self.l[i])); y = q.y() + 4 + self.off; pts = [(0, 0), (-5, 9), (5, 9)]
        else: q = T.map(QtCore.QPointF(i, self.h[i])); y = q.y() - 4 - self.off; pts = [(0, 0), (-5, -9), (5, -9)]
        p.setPen(QtCore.Qt.NoPen); p.setBrush(pg.mkColor(self.color or (BT_BUY if up else BT_SELL)))
        p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(q.x() + dx, y + dy) for dx, dy in pts])); return q.x(), y
    def paint(self, p, *a):
        vr = self.viewRect(); t = self.t
        if vr is None or not t["n"]: return
        idx = np.flatnonzero((t["xi"] >= vr.left() - 1) & (t["ei"] <= vr.right() + 1))
        if not len(idx): return
        for win in (True, False):                                      # 入場 → 出場 虛線 (綠 = 賺, 紅 = 蝕)
            ii = idx[(t["pips"][idx] > 0) == win]
            if len(ii):
                p.setPen(pg.mkPen(BT_WIN if win else BT_LOSS, width=1, style=QtCore.Qt.DashLine))
                p.drawLines([QtCore.QLineF(t["ei"][k], t["ep"][k], t["xi"][k], t["xp"][k]) for k in ii])
        if len(idx) > 1500: return                                     # 太密就只畫線
        T = p.transform(); p.save(); p.resetTransform(); p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setFont(self.font); fm = QtGui.QFontMetrics(self.font); labels = len(idx) <= 300
        for k in idx:
            d, ei, xi = t["dir"][k], t["ei"][k], t["xi"][k]
            self.tri(p, T, ei, d > 0)                                  # 入場: 買 = 藍色向上 (K 線下面), 賣 = 橙色向下 (K 線上面)
            if t["open"][k]:
                q = T.map(QtCore.QPointF(xi, self.c[xi])); x, y, up = q.x() + 8, q.y(), None
                txt = f"持倉 {t['pips'][k]:+.1f}"
            else:
                x, y = self.tri(p, T, xi, d < 0); up = d < 0          # 出場: 平多 = 賣, 平空 = 買
                txt = f"{t['pips'][k]:+.1f}"
            if not labels: continue
            w, h = fm.horizontalAdvance(txt) + 8, fm.height() + 2
            r = QtCore.QRectF(x - w / 2, (y + 11) if up else (y - 11 - h), w, h) if up is not None else QtCore.QRectF(x, y - h / 2, w, h)
            p.setBrush(pg.mkColor(BT_WIN if t["pips"][k] > 0 else BT_LOSS)); p.drawRoundedRect(r, 3, 3)
            p.setPen(pg.mkPen("#ffffff")); p.drawText(r, QtCore.Qt.AlignCenter, txt); p.setPen(QtCore.Qt.NoPen)
        p.restore()

class TradeModel(QtCore.QAbstractTableModel):
    H = ["#", "策略", "方向", "入場時間", "入場價", "出場時間", "出場價", "毛利", "成本", "淨利", "K 數"]
    def __init__(self):
        super().__init__(); self.t, self.ix, self.dec = None, None, 5
    def set(self, t, ix, dec):
        self.beginResetModel(); self.t, self.ix, self.dec = t, ix, dec; self.endResetModel()
    def rowCount(self, *_): return self.t["n"] if self.t else 0
    def columnCount(self, *_): return len(self.H)
    def headerData(self, s, o, role=QtCore.Qt.DisplayRole):
        if role == QtCore.Qt.DisplayRole and o == QtCore.Qt.Horizontal: return self.H[s]
    def data(self, m, role=QtCore.Qt.DisplayRole):
        t, r, c = self.t, m.row(), m.column()
        if role == QtCore.Qt.DisplayRole:
            ts = lambda i: self.ix[i].strftime("%Y-%m-%d %H:%M")
            return [str(r + 1), (t["names"][t["sid"][r]] if t.get("combined") else t["name"]), "多" if t["dir"][r] > 0 else "空", ts(t["ei"][r]), f"{t['ep'][r]:.{self.dec}f}",
                    ("持倉中 " if t["open"][r] else "") + ts(t["xi"][r]), f"{t['xp'][r]:.{self.dec}f}",
                    f"{t['gross'][r]:+.1f}", f"{-t['costp'][r]:.1f}", f"{t['pips'][r]:+.1f}", str(t["xi"][r] - t["ei"][r])][c]
        if role == QtCore.Qt.ForegroundRole and c in (7, 9):
            v = t["gross"][r] if c == 7 else t["pips"][r]; return QtGui.QBrush(pg.mkColor(BT_WIN if v > 0 else BT_LOSS))
        if role == QtCore.Qt.ForegroundRole and c == 8: return QtGui.QBrush(pg.mkColor("#787b86"))
        if role == QtCore.Qt.ForegroundRole and c == 1 and t.get("combined"):
            return QtGui.QBrush(pg.mkColor(STRAT_COLORS[t["sid"][r] % len(STRAT_COLORS)]))
        if role == QtCore.Qt.TextAlignmentRole and c in (0, 4, 6, 7, 8, 9, 10): return int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

class ChartViewBox(pg.ViewBox):
    """價格圖: 左鍵拖 = 上下左右移動; 滾輪 = 只縮放時間軸
       明顯上下拖 (> 15 pixel) 就發 sigYPanned → 自動熄「自動 Y 軸」, 唔會一放手又彈返"""
    sigYPanned = QtCore.pyqtSignal()
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self._dy = 0.0
    def wheelEvent(self, ev, axis=None): super().wheelEvent(ev, axis=0)
    def mouseDragEvent(self, ev, axis=None):
        if ev.button() == QtCore.Qt.LeftButton:
            if ev.isStart(): self._dy = 0.0
            self._dy += abs(ev.pos().y() - ev.lastPos().y())
            if self._dy > 15 and CFG["auto_y"]: self.sigYPanned.emit()
        super().mouseDragEvent(ev, axis)

# ---------------------------------------------------------------- 單個圖表 (每個 tab 一個)
class ChartPanel(W.QWidget):
    state = QtCore.pyqtSignal()          # 代號 / 週期 / 工具 / 實時 狀態有變
    y_panned = QtCore.pyqtSignal()       # 用家上下拖咗價格圖

    def __init__(self, sym="", tf="1h", inds=None, src=None):
        super().__init__()
        if src is None: src = SRC_CSV if os.path.splitext(sym)[1].lower() in (".csv", ".txt") else SRC_YAHOO
        self.sym, self.tf, self.kind, self.src = sym, tf, "Candle", src
        self.live, self.ticker, self.bucket, self.live_ct = False, None, None, None
        self.timer = QtCore.QTimer(self); self.timer.timeout.connect(self.live_tick)
        self.inds = list(inds) if inds is not None else [("SMA", 20), ("EMA", 50), ("RSI", 14)]
        self.cross, self.df, self.iv, self.dec = True, None, {}, 2
        self.tool, self.pending, self.drawings, self.restore = None, None, [], None
        self.bt_paths, self.bts, self.bt_all, self.bt_plots, self.bt_cache = [], [], None, [], {}   # 回測 (可以幾個策略)
        lay = W.QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0)
        self.axes = [DateAxis("bottom") for _ in range(3)]
        self.vb1 = ChartViewBox()
        self.ws = [pg.PlotWidget(viewBox=self.vb1 if r == 0 else None, axisItems={"bottom": self.axes[r], "right": PriceAxis("right")})
                   for r in range(3)]
        self.p1, self.p2, self.p3 = (w.getPlotItem() for w in self.ws)
        self.split = W.QSplitter(QtCore.Qt.Vertical); self.split.setHandleWidth(6); self.split.setChildrenCollapsible(False)
        for i, (w, f) in enumerate(zip(self.ws, (6, 2, 2))): self.split.addWidget(w); self.split.setStretchFactor(i, f)
        self.split.setSizes([600, 200, 200])
        self.info_lbl = W.QLabel(); self.info_lbl.setTextFormat(QtCore.Qt.RichText); self.info_lbl.setWordWrap(True)
        lay.addWidget(self.info_lbl); lay.addWidget(self.split)
        for p in (self.p1, self.p2, self.p3):
            p.showGrid(x=False, y=True, alpha=0.15); p.showAxis("right"); p.hideAxis("left")
            if p is not self.p1: p.setXLink(self.p1)
            p.getViewBox().setMouseEnabled(x=True, y=p is self.p1)      # 價格圖可以上下拖; 成交量 / RSI 只可以左右
            p.getAxis("right").setWidth(72)
        self.p1.hideAxis("bottom"); self.p2.hideAxis("bottom"); self.p3.setYRange(0, 100, padding=0)
        self.vb1.sigYPanned.connect(self.y_panned.emit)
        self.p1.getAxis("right").marks = self.axis_marks          # 水平線 / 十字游標嘅價格顯示喺右邊價格軸
        pen = pg.mkPen("#787b86", style=QtCore.Qt.DashLine)
        self.vls = [pg.InfiniteLine(angle=90, movable=False, pen=pen) for _ in range(3)]
        self.hl = pg.InfiniteLine(angle=0, movable=False, pen=pen)
        for l in self.vls + [self.hl]: l.hide()
        for w, p in zip(self.ws, (self.p1, self.p2, self.p3)):
            w.scene().sigMouseMoved.connect(lambda pos, p=p: self.on_move(p, pos))
        self.ws[0].scene().sigMouseClicked.connect(self.on_click)
        self.ytimer = QtCore.QTimer(self); self.ytimer.setSingleShot(True); self.ytimer.setInterval(30)
        self.ytimer.timeout.connect(self.reset_view)               # 自動 Y 軸: 拖 / 縮放時間軸後 fit 返高低位
        self.p1.sigXRangeChanged.connect(lambda *_: CFG["auto_y"] and self.ytimer.start())

    def title(self):
        if not self.sym: return "(未揀檔案)" if self.src == SRC_CSV else "(未輸入代號)"
        return os.path.splitext(os.path.basename(self.sym))[0] if self.src == SRC_CSV else self.sym.upper()

    # --- 載入
    def set_symbol(self, s, src=None):
        if src is not None: self.src = src
        self.sym = s.strip(); self.restore = None; self.load()
    def set_src(self, i):
        if i == self.src: return
        was_csv = self.src == SRC_CSV; self.src = i; self.restore = None
        if was_csv != (i == SRC_CSV): self.sym = ""                     # CSV 路徑同股票代號唔通用
        if i != SRC_IBKR and self.live: self.stop_live(True)
        self.load()
    def set_tf(self, k):                 # 轉週期保留畫線 (畫線用時間做錨點)
        self.tf = k
        if self.df is not None: self.restore = dict(drawings=self.drawing_specs())
        self.load()
    def reload(self):                    # 重新讀檔 / 改時區後: 保留畫線同可見範圍
        if self.df is not None: self.restore = self.get_state()
        self.load()
    def load(self):
        if not self.sym:
            win = self.window()
            if isinstance(win, W.QMainWindow):
                win.statusBar().showMessage("請喺右邊「CSV」揀檔案" if self.src == SRC_CSV else "請喺「代號」輸入 (例如 AAPL / 700 / GBPUSD=X), 或者撳右邊「自選」", 8000)
            self.state.emit(); return
        win = self.window(); sb = win.statusBar() if isinstance(win, W.QMainWindow) else None
        if sb:
            sb.showMessage(f"載入 {self.title()} ({self.tf}) …")
            W.QApplication.processEvents(QtCore.QEventLoop.ExcludeUserInputEvents)
        W.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor); t0 = time.time()
        try:
            self.stop_live()
            self.df = get_df(self.src, self.sym, self.tf)
            self.clear_drawings(); self.draw()
            if self.restore: self.apply_restore()
            if self.live: self.start_live()
            if sb:
                df = self.df
                sb.showMessage(f"{self.title()} · {self.tf}:  {len(df):,} 根 K   {df.index[0]:%Y-%m-%d %H:%M} → "
                               f"{df.index[-1]:%Y-%m-%d %H:%M}   ({time.time() - t0:.1f}s)")
        except Exception as e:
            if sb: sb.clearMessage()
            W.QMessageBox.warning(self, "載入失敗", f"{type(e).__name__}: {e}")
        finally:
            W.QApplication.restoreOverrideCursor(); self.state.emit()

    def apply_theme(self, f=None):
        f = f or W.QApplication.font()
        for w in self.ws: w.setBackground(CFG["chart_bg"])
        self.info_lbl.setStyleSheet(f"background:{CFG['chart_bg']};color:{CFG['info']};padding:2px 6px")
        self.info_lbl.setFixedHeight(QtGui.QFontMetrics(f).lineSpacing() * 2 + 10)
        for p in (self.p1, self.p2, self.p3): p.getAxis("right").setWidth(max(72, int(max(f.pointSize(), 9) * 8)))
        for p in (self.p1, self.p2, self.p3):
            for a in ("left", "right", "bottom"):
                ax = p.getAxis(a); ax.setPen(pg.mkPen(CFG["chart_fg"])); ax.setTextPen(pg.mkPen(CFG["chart_fg"])); ax.setStyle(tickFont=f)
        if self.df is not None: self.draw(keep=True)

    # --- 繪圖
    def draw(self, *_, keep=False):
        df = self.df
        if df is None or df.empty: return
        n = len(df); x = np.arange(n, dtype=float); ps = (self.p1, self.p2, self.p3)
        self.dec = price_decimals(df); self.p1.getAxis("right").dec = self.dec
        for a in self.axes: a.dates, a.fmt = df.index, tf_fmt(self.tf)
        for p in ps: p.clear()
        for p, v in zip(ps, self.vls): p.addItem(v, ignoreBounds=True)
        self.p1.addItem(self.hl, ignoreBounds=True)
        for d in self.drawings: self.p1.addItem(d, ignoreBounds=True)
        ym = df.index.year.to_numpy() * 12 + df.index.month.to_numpy()
        st = np.flatnonzero(np.diff(ym) != 0) + 0.5                       # 每月分界
        for p in ps: p.addItem(MonthGrid(st), ignoreBounds=True)
        if CFG["minor_on"]: self.p1.addItem(MinorGrid(self.p1.getAxis("right")), ignoreBounds=True)
        up = up_flags(df)
        if self.kind == "Line": fast_plot(self.p1, x, df.close.to_numpy(float), pg.mkPen(CFG["line"], width=1.5))
        else: self.p1.addItem(PriceItem(df, self.kind, up))
        self.p2.addItem(VolumeItem(df.volume.to_numpy(float), up))
        has_rsi = False; self.iv = {}
        for kind, per in self.inds:
            y = calc(kind, df, per).to_numpy(float); self.iv[(kind, per)] = y; pen = pg.mkPen(icolor(kind, per), width=1.5)
            if kind == "RSI": has_rsi = True; fast_plot(self.p3, x, y, pen)
            else: fast_plot(self.p1, x, y, pen)
        if has_rsi:
            for lv in (30, 70):
                self.p3.addItem(pg.InfiniteLine(pos=lv, angle=0, pen=pg.mkPen("#787b86", style=QtCore.Qt.DashLine)))
        self.ensure_bt(); multi = len(self.bts) > 1
        for name, y, col in self.bt_plots: fast_plot(self.p1, x, y, pg.mkPen(col, width=1.2))
        for t in self.bts:
            if t["n"]: self.p1.addItem(TradeLayer(t, df, t["color"] if multi else None, t["slot"]), ignoreBounds=True)
        self.ws[2].setVisible(has_rsi)
        self.p2.showAxis("bottom", not has_rsi); self.p3.showAxis("bottom", has_rsi)
        for p in ps: p.getViewBox().setLimits(xMin=-50, xMax=n + 300)      # 唔好拖到冇數據嘅地方
        self.info(n - 1)
        if not keep:
            self.p1.setXRange(max(0, n - 150), n + 5, padding=0); self.reset_view()

    def info(self, i):
        df = self.df; i = max(0, min(len(df) - 1, i)); r = df.iloc[i]
        d = self.dec
        txt = (f"{self.title()} {self.tf}  {df.index[i].strftime('%Y-%m-%d %H:%M' if tf_fmt(self.tf) == I else D)}  "
               f"O:{r.open:.{d}f} H:{r.high:.{d}f} L:{r.low:.{d}f} C:{r.close:.{d}f} ({(r.close / r.open - 1) * 100:+.2f}%)  Vol:{int(r.volume):,}")
        for kind, per in self.inds:
            v = self.iv.get((kind, per)); val = v[i] if v is not None else float("nan")
            txt += f"&nbsp;&nbsp;<span style='color:{icolor(kind, per)}'>{kind}({per}) {val:.{2 if kind == 'RSI' else d}f}</span>"
        for name, y, col in self.bt_plots:
            txt += f"&nbsp;&nbsp;<span style='color:{col}'>{name} {y[i]:.{d}f}</span>"
        self.info_lbl.setText(txt)

    # --- 回測 (策略檔: strategies/*.py; 可以同時揀幾個, 各自獨立持倉)
    @property
    def bt(self): return self.bt_all or (self.bts[0] if self.bts else None)
    def run_bt(self, paths):
        self.bt_paths = list(paths); self.bt_cache.clear(); self.draw(keep=True); self.reset_view(); self.state.emit()
    def clear_bt(self):
        self.bt_paths, self.bts, self.bt_all, self.bt_plots = [], [], None, []; self.bt_cache.clear()
        if self.df is not None: self.draw(keep=True); self.reset_view()
        self.state.emit()
    def ensure_bt(self):            # 數據 / 策略檔有變先重新跑 (轉週期會自動用新週期再回測)
        if not self.bt_paths or self.df is None: self.bts, self.bt_all, self.bt_plots = [], None, []; return
        ts, plots, bad = [], [], []
        multi = len(self.bt_paths) > 1
        for path in self.bt_paths:
            try: mt = os.path.getmtime(path)
            except OSError: mt = None
            key = (id(self.df), len(self.df), mt); hit = self.bt_cache.get(path)
            if hit and hit[0] == key: t, pl = hit[1], hit[2]
            else:
                try:
                    t0 = time.time(); t, pl = run_strategy(path, self.df); t["secs"] = time.time() - t0
                except Exception:
                    bad.append((path, traceback.format_exc(limit=-3))); continue
                self.bt_cache[path] = (key, t, pl)
            k = len(ts); t["sid"] = np.full(t["n"], k); t["slot"] = k; t["color"] = STRAT_COLORS[k % len(STRAT_COLORS)]
            ts.append(t)
            for name, y, _ in pl:
                plots.append((f"{t['name']}·{name}" if multi else name, y, BT_PLOT_COLORS[len(plots) % len(BT_PLOT_COLORS)]))
        if bad:
            self.bt_paths = [p for p in self.bt_paths if p not in {b[0] for b in bad}]
            W.QMessageBox.warning(self, "策略出錯", "\n\n".join(f"{os.path.basename(p)}\n{e}" for p, e in bad))
        self.bts, self.bt_plots = ts, plots
        self.bt_all = combine(ts) if len(ts) > 1 else None
    def goto_bars(self, a, b):      # 跳去某一單 (入場到出場, 左右留位)
        pad = max(20, (b - a) * 0.6); self.p1.setXRange(a - pad, b + pad, padding=0); self.reset_view()

    # --- IBKR 實時串流 (每秒更新最後一根 K; 日內週期過咗時間自動開新 K)
    def bucket_of(self, ts):
        m = tf_minutes(self.tf); return ts.floor(f"{m}min") if m else None
    def start_live(self):
        try:
            if self.src != SRC_IBKR: raise RuntimeError("實時串流只支援 IBKR 數據源")
            if self.df is None: raise RuntimeError("請先載入數據")
            ib = IBKRSource.connect(); ib.reqMarketDataType(int(CFG["ib_mkt"]))
            self.live_ct = ib_contract(self.sym); ib.qualifyContracts(self.live_ct)
            self.ticker = ib.reqMktData(self.live_ct, "", False, False)
            self.bucket = self.bucket_of(pd.Timestamp.now(tz="UTC")); self.timer.start(1000); self.live = True
        except Exception as e:
            self.live = False; self.state.emit(); W.QMessageBox.warning(self, "串流失敗", f"{type(e).__name__}: {e}")
    def stop_live(self, user=False):
        self.timer.stop()
        if self.ticker is not None:
            try: IBKRSource.ib.cancelMktData(self.live_ct)
            except Exception: pass
            self.ticker = None
        if user: self.live = False
    def live_tick(self):
        if self.ticker is None or self.df is None: return
        IBKRSource.ib.sleep(0.05)
        px = self.ticker.last
        if not (px == px and px > 0): px = self.ticker.marketPrice()
        if not (px == px and px > 0): return
        df = self.df; bk = self.bucket_of(pd.Timestamp.now(tz="UTC"))
        if bk is not None and bk != self.bucket:
            self.bucket = bk; df.loc[df.index[-1] + pd.Timedelta(minutes=tf_minutes(self.tf))] = [px, px, px, px, 0]
        else:
            i = df.index[-1]
            df.at[i, "high"] = max(df.at[i, "high"], px); df.at[i, "low"] = min(df.at[i, "low"], px); df.at[i, "close"] = px
        self.draw(keep=True)

    # --- 十字游標
    def on_move(self, src, pos):
        if self.df is None or not self.cross or not src.sceneBoundingRect().contains(pos): return
        mp = src.vb.mapSceneToView(pos); x = round(mp.x())
        for v in self.vls: v.setPos(x); v.show()
        self.hl.setVisible(src is self.p1)
        if src is self.p1: self.hl.setPos(mp.y())
        self.info(int(x)); self.update_axis()
    def axis_marks(self):
        out = []
        for d in self.drawings:
            if isinstance(d, pg.LineSegmentROI):                    # 趨勢線: 兩個端點
                out += [(d.mapToParent(q).y(), DRAW_COLOR) for q in d.listPoints()]
            elif d.angle % 180 == 0: out.append((d.value(), DRAW_COLOR))
        if self.cross and self.hl.isVisible(): out.append((self.hl.value(), "#787b86"))
        return out
    def update_axis(self, *_): self.p1.getAxis("right").update()
    def set_cross(self, c):
        self.cross = c
        if not c:
            for l in self.vls + [self.hl]: l.hide()
        self.update_axis()

    # --- 畫線工具 (趨勢線: 點兩下; 水平/垂直線: 點一下; 趨勢線 right-click 可刪除; Esc 取消)
    def set_tool(self, name):
        self.tool, self.pending = name, None
        self.ws[0].setCursor(QtCore.Qt.CrossCursor if name else QtCore.Qt.ArrowCursor); self.state.emit()
    def on_click(self, ev):
        if not self.tool or ev.button() != QtCore.Qt.LeftButton or self.df is None: return
        if not self.p1.sceneBoundingRect().contains(ev.scenePos()): return
        mp = self.p1.vb.mapSceneToView(ev.scenePos()); x, y = mp.x(), mp.y()
        if self.tool == "trend":
            if self.pending is None: self.pending = (x, y); return
            self.add_drawing({"t": "trend", "pts": [list(self.pending), [x, y]]})
        else: self.add_drawing({"t": self.tool, "v": y if self.tool == "hline" else x})
        self.set_tool(None)
    def add_drawing(self, spec):
        pen = pg.mkPen(DRAW_COLOR, width=1.5); t = spec["t"]
        if t == "trend":
            it = pg.LineSegmentROI(spec["pts"], pen=pen, removable=True); it.sigRemoveRequested.connect(self.rm_drawing)
            it.sigRegionChanged.connect(self.update_axis)           # 拖端點 / 成條線時, 價格軸標籤跟住郁
        else:
            it = pg.InfiniteLine(pos=spec["v"], angle=0 if t == "hline" else 90, movable=True, pen=pen)
            it.sigPositionChanged.connect(self.update_axis)       # 拖水平線時, 價格軸上面嘅價格跟住郁
        self.p1.addItem(it, ignoreBounds=True); self.drawings.append(it); self.update_axis()

    # --- 儲存 / 復原 (畫線用日期做錨點, 轉週期 / 改時區後位置仍然正確)
    def x2t(self, x):
        i = min(max(int(math.floor(x)), 0), len(self.df) - 1); return [str(self.df.index[i]), float(x - i)]
    def t2x(self, t):
        return int(self.df.index.get_indexer([pd.Timestamp(t[0])], method="nearest")[0]) + t[1]
    def drawing_specs(self):
        out = []
        for d in self.drawings:
            if isinstance(d, pg.LineSegmentROI):
                out.append({"t": "trend", "pts": [[self.x2t(q.x()), q.y()] for q in (d.mapToParent(p) for p in d.listPoints())]})
            elif d.angle % 180 == 0: out.append({"t": "hline", "v": d.value()})
            else: out.append({"t": "vline", "v": self.x2t(d.value())})
        return out
    def get_state(self):
        s = dict(sym=self.sym, tf=self.tf, kind=self.kind, inds=[list(i) for i in self.inds],
                 cross=self.cross, split=self.split.sizes(), drawings=[], view=None, strategies=self.bt_paths, src=self.src)
        if self.df is not None and len(self.df):
            a, b = self.p1.viewRange()[0]; s["drawings"] = self.drawing_specs(); s["view"] = [b - a, b - len(self.df)]
        elif self.restore: s["drawings"], s["view"] = self.restore.get("drawings", []), self.restore.get("view")
        return s
    def apply_restore(self):
        s, self.restore = self.restore, None
        for d in s.get("drawings", []):
            if d["t"] == "trend": self.add_drawing({"t": "trend", "pts": [[self.t2x(a), y] for a, y in d["pts"]]})
            elif d["t"] == "hline": self.add_drawing(d)
            else: self.add_drawing({"t": "vline", "v": self.t2x(d["v"])})
        if s.get("view"):
            span, off = s["view"]; n = len(self.df); self.p1.setXRange(n + off - span, n + off, padding=0); self.reset_view()
    def rm_drawing(self, it):
        self.p1.removeItem(it); self.drawings.remove(it); self.update_axis()
    def clear_drawings(self, *_):
        for d in self.drawings: self.p1.removeItem(d)
        self.drawings.clear(); self.update_axis()

    # --- 重設圖表: 返去最新 150 根 K + Y 軸 fit 返 (自動 Y 軸由 Main 重新開)
    def reset_chart(self):
        if self.df is None or self.df.empty: return
        n = len(self.df); self.p1.setXRange(max(0, n - 150), n + 5, padding=0); self.reset_view()

    # --- Y 軸 fit 住可見範圍嘅高低位 (保持而家嘅時間範圍)
    def reset_view(self, *_):
        df = self.df
        if df is None or df.empty: return
        a, b = self.p1.viewRange()[0]; lo, hi = max(0, math.ceil(a)), min(len(df) - 1, math.floor(b))
        if hi < lo: return
        sl = slice(lo, hi + 1)
        if self.kind == "Line": c = df.close.to_numpy()[sl]; ys = [c.min(), c.max()]
        else: ys = [df.low.to_numpy()[sl].min(), df.high.to_numpy()[sl].max()]
        for (kind, per), y in self.iv.items():
            if kind != "RSI":
                s = y[sl]; s = s[np.isfinite(s)]
                if s.size: ys += [s.min(), s.max()]
        for _, y, _ in self.bt_plots:
            s = y[sl]; s = s[np.isfinite(s)]
            if s.size: ys += [s.min(), s.max()]
        y0, y1 = min(ys), max(ys); pad = (y1 - y0) * (0.16 if self.bt else 0.08) or abs(y1) * 0.001 or 1   # 有回測就留多啲位畫圖示
        self.p1.setYRange(y0 - pad, y1 + pad, padding=0)
        self.p2.setYRange(0, max(df.volume.to_numpy()[sl].max() * 1.1, 1), padding=0)

# ---------------------------------------------------------------- 設定視窗
class ColorBtn(W.QPushButton):
    def __init__(self, color):
        super().__init__(); self.set(color); self.clicked.connect(self.pick)
    def set(self, c):
        self.c = pg.mkColor(c).name(); self.setText(self.c)
        self.setStyleSheet(f"QPushButton{{background:{self.c};color:{'#000' if pg.mkColor(self.c).lightness() > 128 else '#fff'}}}")
    def pick(self):
        c = W.QColorDialog.getColor(QtGui.QColor(self.c), self.window())
        if c.isValid(): self.set(c.name())

class SettingsDialog(W.QDialog):
    def __init__(self, main):
        super().__init__(main); self.main = main; self.setWindowTitle("設定"); f = W.QFormLayout(self)
        fo = QtGui.QFont()
        if CFG["font"]: fo.fromString(CFG["font"])
        else: fo = W.QApplication.font()
        self.fam = W.QFontComboBox(); self.fam.setCurrentFont(fo)
        self.size = W.QSpinBox(); self.size.setRange(6, 40); self.size.setValue(max(fo.pointSize(), 6))
        self.bold = W.QCheckBox("粗體"); self.bold.setChecked(fo.bold())
        self.ital = W.QCheckBox("斜體"); self.ital.setChecked(fo.italic())
        row = W.QHBoxLayout()
        for w in (self.size, self.bold, self.ital): row.addWidget(w)
        f.addRow("字體", self.fam); f.addRow("大小 / 樣式", row)
        self.theme = W.QComboBox(); self.theme.addItems(["（自訂）"] + list(THEMES)); f.addRow("主題", self.theme)
        self.btn = {k: ColorBtn(CFG[k]) for k, _ in COLOR_KEYS}
        for k, label in COLOR_KEYS: f.addRow(label, self.btn[k])
        self.gd = W.QCheckBox("每月垂直格線用虛線"); self.gd.setChecked(CFG["grid_dash"]); f.addRow(self.gd)
        self.mn = W.QCheckBox("價格圖: 兩條大橫線之間加 4 條細虛線"); self.mn.setChecked(CFG["minor_on"]); f.addRow(self.mn)
        self.pc = W.QCheckBox("K 線顏色基於前一收盤價"); self.pc.setChecked(CFG["prev_close"]); f.addRow(self.pc)
        self.cd = []
        for label, ku, kd, on in (("主體", "up", "down", "body_on"), ("邊框", "up_b", "down_b", "border_on"), ("燭芯", "up_w", "down_w", "wick_on")):
            cb = W.QCheckBox(label); cb.setChecked(CFG[on]); bu = ColorBtn(CFG[ku] or CFG["up"]); bd = ColorBtn(CFG[kd] or CFG["down"])
            row = W.QHBoxLayout(); row.addWidget(bu); row.addWidget(bd); f.addRow(cb, row); self.cd.append((cb, bu, bd, ku, kd, on))
        self.theme.currentTextChanged.connect(lambda t: [self.btn[k].set(v) for k, v in THEMES.get(t, {}).items()])
        self.ibtn = {}
        for kind, per in main.cur().inds:
            if (kind, per) not in self.ibtn:
                self.ibtn[(kind, per)] = ColorBtn(icolor(kind, per)); f.addRow(f"{kind}({per}) 顏色", self.ibtn[(kind, per)])
        f.addRow(W.QLabel("<b>IBKR 連線</b>  (TWS 模擬 7497 / 真實 7496; Gateway 模擬 4002 / 真實 4001)"))
        self.ib_host = W.QLineEdit(str(CFG["ib_host"])); self.ib_host.setPlaceholderText("127.0.0.1 = 本機")
        self.ib_port = W.QSpinBox(); self.ib_port.setRange(1, 65535); self.ib_port.setValue(int(CFG["ib_port"]))
        self.ib_client = W.QSpinBox(); self.ib_client.setRange(0, 9999); self.ib_client.setValue(int(CFG["ib_client"]))
        self.ib_client.setToolTip("同一時間每個連線要用唔同 Client ID")
        self.ib_mkt = W.QComboBox()
        for code, label in ((1, "1 實時 (要有市場數據訂閱)"), (2, "2 收市凍結"), (3, "3 延遲 (免費, 遲 15-20 分鐘)"), (4, "4 延遲凍結")):
            self.ib_mkt.addItem(label, code)
        self.ib_mkt.setCurrentIndex(max(0, self.ib_mkt.findData(int(CFG["ib_mkt"]))))
        row = W.QHBoxLayout(); row.addWidget(self.ib_host); row.addWidget(W.QLabel("Port")); row.addWidget(self.ib_port)
        row.addWidget(W.QLabel("Client ID")); row.addWidget(self.ib_client)
        f.addRow("IP / Port / ID", row)
        tb = W.QPushButton("測試連線"); tb.clicked.connect(self.test_ib)
        row = W.QHBoxLayout(); row.addWidget(self.ib_mkt, 1); row.addWidget(tb); f.addRow("實時數據類型", row)
        bb = W.QDialogButtonBox(W.QDialogButtonBox.Ok | W.QDialogButtonBox.Apply | W.QDialogButtonBox.Cancel)
        bb.accepted.connect(lambda: (self.apply(), self.accept())); bb.rejected.connect(self.reject)
        bb.button(W.QDialogButtonBox.Apply).clicked.connect(self.apply); f.addRow(bb)
    def apply(self):
        fo = QtGui.QFont(self.fam.currentFont().family(), self.size.value())
        fo.setBold(self.bold.isChecked()); fo.setItalic(self.ital.isChecked()); CFG["font"] = fo.toString()
        for k, b in self.btn.items(): CFG[k] = b.c
        for (kind, per), b in self.ibtn.items(): CFG["icol"][f"{kind}|{per}"] = b.c
        CFG["prev_close"] = self.pc.isChecked(); CFG["grid_dash"] = self.gd.isChecked(); CFG["minor_on"] = self.mn.isChecked()
        for cb, bu, bd, ku, kd, on in self.cd: CFG[on] = cb.isChecked(); CFG[ku] = bu.c; CFG[kd] = bd.c
        self.apply_ib()
        save_cfg(); self.main.apply_settings()
    def apply_ib(self):
        new = dict(ib_host=self.ib_host.text().strip() or "127.0.0.1", ib_port=self.ib_port.value(),
                   ib_client=self.ib_client.value(), ib_mkt=self.ib_mkt.currentData())
        conn_changed = any(CFG[k] != new[k] for k in ("ib_host", "ib_port", "ib_client"))
        CFG.update(new)
        if conn_changed:                                                  # 連線設定改咗: 停實時 + 斷開, 下次用新設定重連
            for i in range(self.main.charts.count()): self.main.charts.widget(i).stop_live(True)
            IBKRSource.disconnect(); self.main.sync_toolbar()
    def test_ib(self):
        self.apply_ib(); save_cfg()
        W.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            ib = IBKRSource.connect()
            W.QMessageBox.information(self, "IBKR", f"連線成功 ✓\n{CFG['ib_host']}:{CFG['ib_port']}  client {CFG['ib_client']}\n"
                                                    f"戶口: {', '.join(ib.managedAccounts()) or '-'}")
        except Exception as e:
            W.QMessageBox.warning(self, "IBKR", f"{type(e).__name__}: {e}")
        finally: W.QApplication.restoreOverrideCursor()

# ---------------------------------------------------------------- 主視窗
class Main(W.QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("chartEX"); self.resize(1500, 900)
        self.build_toolbar()
        self.charts = W.QTabWidget(); self.charts.setTabsClosable(True); self.charts.setMovable(True)
        nb = W.QToolButton(); nb.setText("＋ 新圖表"); nb.clicked.connect(lambda: self.new_chart()); self.charts.setCornerWidget(nb)
        self.charts.tabCloseRequested.connect(self.close_chart); self.charts.currentChanged.connect(self.sync_toolbar)
        self.build_files(); self.build_watchlist()
        self.side = W.QTabWidget(); self.side.addTab(self.wl_panel, "自選"); self.side.addTab(self.file_panel, "CSV")
        sp = self.main_split = W.QSplitter(); sp.setHandleWidth(6); sp.setChildrenCollapsible(False)
        sp.addWidget(self.charts); sp.addWidget(self.side)
        sp.setStretchFactor(0, 1); sp.setSizes([1200, 300]); self.setCentralWidget(sp)
        W.QShortcut(QtGui.QKeySequence("Esc"), self, activated=lambda: self.cur().set_tool(None))
        W.QShortcut(QtGui.QKeySequence("Alt+R"), self, activated=self.reset_chart, context=QtCore.Qt.ApplicationShortcut)
        self.statusBar(); self.build_bt_dock(); self.refresh_strats()
        self.ws_path = None; self.build_menu(); self.apply_settings(); self.resume()

    def cur(self): return self.charts.currentWidget()

    # --- 工具列 (2 行)
    def tbtn(self, text, tb, slot, checkable=False):
        b = W.QToolButton(); b.setText(text); b.setCheckable(checkable); b.clicked.connect(slot); tb.addWidget(b); return b
    def build_toolbar(self):
        t1 = self.addToolBar("main"); t1.setObjectName("main"); t1.setMovable(False)
        self.src = W.QComboBox(); self.src.addItems(SRC_NAMES)
        self.src.currentIndexChanged.connect(lambda i: self.cur().set_src(i))
        t1.addWidget(W.QLabel(" 數據源 ")); t1.addWidget(self.src)
        self.edit = W.QLineEdit(); self.edit.setFixedWidth(130); self.edit.setPlaceholderText("代號, Enter")
        self.edit.returnPressed.connect(self.enter_symbol)
        t1.addWidget(W.QLabel("  代號 ")); t1.addWidget(self.edit); t1.addSeparator()
        self.tf_btns = {k: self.tbtn(k, t1, lambda c, k=k: self.cur().set_tf(k), True) for k in TF}
        self.cn = W.QSpinBox(); self.cn.setRange(1, 1440); self.cn.setValue(10); self.cn.setSuffix(" 分")
        t1.addWidget(self.cn); self.tbtn("自訂", t1, lambda: self.cur().set_tf(f"{self.cn.value()}m"))
        t2 = self.addToolBar("tools"); t2.setObjectName("tools"); t2.setMovable(False)
        self.cross_btn = self.tbtn("十字", t2, lambda c: self.cur().set_cross(c), True)
        self.tool_btns = {n: self.tbtn(t, t2, lambda c, n=n: self.cur().set_tool(n if c else None), True)
                          for n, t in (("trend", "趨勢線"), ("hline", "水平線"), ("vline", "垂直線"))}
        self.tbtn("清除線", t2, lambda: self.cur().clear_drawings()); t2.addSeparator()
        self.tbtn("重設圖表", t2, self.reset_chart).setToolTip("返去最新 K 線 + Y 軸自動 (Alt+R)")
        self.autoy_btn = self.tbtn("自動 Y", t2, self.set_auto_y, True); self.autoy_btn.setChecked(CFG["auto_y"])
        self.live_btn = self.tbtn("實時", t2, self.toggle_live, True); self.live_btn.setToolTip("IBKR 實時串流 (每秒更新最後一根 K)")
        self.addToolBarBreak(); t3 = self.addToolBar("backtest"); t3.setObjectName("backtest"); t3.setMovable(False)
        t3.addWidget(W.QLabel(" 日期 ")); self.d0, self.d1 = W.QDateEdit(), W.QDateEdit()
        for w in (self.d0, self.d1): w.setCalendarPopup(True); w.setDisplayFormat("yyyy-MM-dd")
        t3.addWidget(self.d0); t3.addWidget(W.QLabel(" → ")); t3.addWidget(self.d1)
        self.tbtn("重設", t3, self.reset_dates).setToolTip("2012-01-01 → 今日")
        self.date_timer = QtCore.QTimer(self); self.date_timer.setSingleShot(True); self.date_timer.setInterval(800)
        self.date_timer.timeout.connect(self.apply_dates); self.show_dates()
        for w in (self.d0, self.d1): w.dateChanged.connect(lambda _: self.date_timer.start())
        t3.addSeparator()
        t3.addWidget(W.QLabel(" 策略 "))
        self.strat_btn = W.QToolButton(); self.strat_btn.setPopupMode(W.QToolButton.InstantPopup); self.strat_btn.setMinimumWidth(180)
        self.strat_list = W.QListWidget(); self.strat_list.setMinimumWidth(240)
        self.strat_list.itemChanged.connect(self.strat_label)
        self.strat_list.itemClicked.connect(lambda it: it.setCheckState(                         # 撳名都可以剔
            QtCore.Qt.Unchecked if it.checkState() == QtCore.Qt.Checked else QtCore.Qt.Checked) if it.flags() & QtCore.Qt.ItemIsUserCheckable else None)
        m = W.QMenu(self.strat_btn); wa = W.QWidgetAction(m); wa.setDefaultWidget(self.strat_list); m.addAction(wa)
        self.strat_btn.setMenu(m); self.strat_btn.setToolTip("可以剔幾個策略, 同一個圖表同時各自獨立行"); t3.addWidget(self.strat_btn)
        self.tbtn("…", t3, self.pick_strat_dir).setToolTip("選擇策略資料夾")
        self.tbtn("⟳", t3, self.refresh_strats).setToolTip("重新整理策略清單")
        self.edit_btn = W.QToolButton(); self.edit_btn.setText("編輯"); self.edit_btn.setPopupMode(W.QToolButton.InstantPopup)
        self.edit_menu = W.QMenu(self.edit_btn); self.edit_btn.setMenu(self.edit_menu); self.edit_menu.aboutToShow.connect(self.fill_edit_menu)
        self.edit_btn.setToolTip("用預設程式開啟策略檔"); t3.addWidget(self.edit_btn)
        t3.addSeparator(); self.tbtn("▶ 回測", t3, self.run_bt).setToolTip("用目前圖表嘅週期跑剔咗嘅策略 (改完策略檔再撳就會重新讀)")
        self.tbtn("清除回測", t3, lambda: self.cur().clear_bt())
        self.bt_btn = self.tbtn("結果表", t3, lambda c: self.bt_dock.setVisible(c), True)
        t4 = self.addToolBar("chart"); t4.setObjectName("chart"); t4.setMovable(False)     # 同日期 / 策略 同一行
        self.kind = W.QComboBox(); self.kind.addItems(["Candle", "OHLC", "Line"])
        self.kind.currentTextChanged.connect(self.set_kind); t4.addWidget(self.kind); t4.addSeparator()
        self.ind_t = W.QComboBox(); self.ind_t.addItems(["SMA", "EMA", "RMA", "RSI"])
        self.ind_n = W.QSpinBox(); self.ind_n.setRange(1, 500); self.ind_n.setValue(20)
        for w in (self.ind_t, self.ind_n): t4.addWidget(w)
        self.tbtn("加指標", t4, self.add_ind)
        self.del_btn = W.QToolButton(); self.del_btn.setText("刪除指標"); self.del_btn.setPopupMode(W.QToolButton.InstantPopup)
        self.del_menu = W.QMenu(self.del_btn); self.del_btn.setMenu(self.del_menu)
        self.del_menu.aboutToShow.connect(self.fill_del_menu); t4.addWidget(self.del_btn); t4.addSeparator()
        self.tbtn("設定", t4, lambda: SettingsDialog(self).exec_())

    def enter_symbol(self):
        p = self.cur()
        if p.src == SRC_CSV:
            t = self.edit.text().strip().strip('"')
            if os.path.isfile(t): p.set_symbol(t)
            else: self.sync_toolbar()                                     # CSV 模式: 用右邊清單揀檔案
        else: p.set_symbol(self.edit.text())
    def toggle_live(self, c):
        p = self.cur(); p.start_live() if c else p.stop_live(True)
        self.sync_toolbar()
    def set_auto_y(self, c):
        CFG["auto_y"] = bool(c); save_cfg(); self.autoy_btn.setChecked(bool(c))
        if c and self.cur(): self.cur().reset_view()
    def reset_chart(self):
        if self.cur(): self.set_auto_y(True); self.cur().reset_chart()
    def set_kind(self, k):
        p = self.cur(); p.kind = k; p.draw(keep=True); p.reset_view()
    def add_ind(self):
        p = self.cur(); p.inds.append((self.ind_t.currentText(), self.ind_n.value())); p.draw(keep=True); p.reset_view()
    def fill_del_menu(self):
        self.del_menu.clear(); p = self.cur()
        for ind in p.inds:
            a = self.del_menu.addAction(f"{ind[0]}({ind[1]})"); a.triggered.connect(lambda _, ind=ind: self.del_ind(ind))
        if p.inds:
            self.del_menu.addSeparator(); self.del_menu.addAction("全部清除").triggered.connect(lambda _: self.clear_inds())
        else: self.del_menu.addAction("（未有指標）").setEnabled(False)
    def del_ind(self, ind):
        p = self.cur()
        if ind in p.inds: p.inds.remove(ind); p.draw(keep=True); p.reset_view()
    def clear_inds(self):
        p = self.cur(); p.inds.clear(); p.draw(keep=True); p.reset_view()

    # --- 圖表 tab
    def new_chart(self, sym=None, tf=None, src=None):
        c = self.cur()
        if src is None: src = c.src if c else SRC_YAHOO
        if sym is None: sym = c.sym if c and c.src == src else ""
        self.add_panel(ChartPanel(sym, tf or (c.tf if c else "1D"), c.inds if c else None, src))
    def add_panel(self, p):
        self.charts.addTab(p, f"{p.title()} · {p.tf}"); p.state.connect(lambda p=p: self.on_state(p))
        p.y_panned.connect(lambda: self.set_auto_y(False))
        self.charts.setCurrentWidget(p); p.apply_theme(); p.load()
    def close_chart(self, i):
        if self.charts.count() <= 1: return
        p = self.charts.widget(i); p.stop_live(True); self.charts.removeTab(i); p.deleteLater()
    def on_state(self, p):
        i = self.charts.indexOf(p)
        if i >= 0: self.charts.setTabText(i, f"{p.title()} · {p.tf}"); self.charts.setTabToolTip(i, p.sym)
        if p is self.cur(): self.sync_toolbar()
    def sync_toolbar(self, *_):
        p = self.cur()
        if p is None: return
        self.kind.blockSignals(True); self.kind.setCurrentText(p.kind); self.kind.blockSignals(False)
        self.cross_btn.setChecked(p.cross)
        for w in (self.src, self.edit, self.live_btn): w.blockSignals(True)
        self.src.setCurrentIndex(p.src); self.live_btn.setChecked(p.live); self.live_btn.setEnabled(p.src == SRC_IBKR)
        self.edit.setText(p.title() if p.src == SRC_CSV and p.sym else p.sym); self.edit.setToolTip(p.sym)
        self.edit.setPlaceholderText("右邊揀 CSV 檔" if p.src == SRC_CSV else "代號, Enter")
        for w in (self.src, self.edit, self.live_btn): w.blockSignals(False)
        for k, b in self.tf_btns.items(): b.setChecked(k == p.tf)
        for k, b in self.tool_btns.items(): b.setChecked(k == p.tool)
        self.files.blockSignals(True); self.files.clearSelection()
        for i in range(self.files.count()):
            it = self.files.item(i)
            if p.src == SRC_CSV and same_path(it.data(UR), p.sym): self.files.setCurrentItem(it); break
        self.files.blockSignals(False)
        if p.bt_paths: self.set_checked(p.bt_paths)
        self.update_bt_dock()

    # --- 回測: 策略清單 + 結果表 + 資金曲線
    def strat_paths(self, checked_only=True):
        out = []
        for i in range(self.strat_list.count()):
            it = self.strat_list.item(i)
            if it.data(UR) and (not checked_only or it.checkState() == QtCore.Qt.Checked): out.append(it.data(UR))
        return out
    def set_checked(self, paths):
        self.strat_list.blockSignals(True)
        for i in range(self.strat_list.count()):
            it = self.strat_list.item(i)
            if it.data(UR): it.setCheckState(QtCore.Qt.Checked if any(same_path(it.data(UR), q) for q in paths) else QtCore.Qt.Unchecked)
        self.strat_list.blockSignals(False); self.strat_label()
    def strat_label(self, *_):
        names = [os.path.splitext(os.path.basename(q))[0] for q in self.strat_paths()]
        self.strat_btn.setText((" + ".join(names) if len(names) <= 2 else f"{names[0]} + 另外 {len(names) - 1} 個") if names else "(未揀)")
    def refresh_strats(self, *_):
        d = strat_dir(); keep = self.strat_paths(); self.strat_list.blockSignals(True); self.strat_list.clear()
        if os.path.isdir(d):
            for nm in sorted(os.listdir(d), key=str.lower):
                if nm.endswith(".py") and not nm.startswith("_"):
                    it = W.QListWidgetItem(nm[:-3]); it.setData(UR, os.path.join(d, nm)); it.setToolTip(os.path.join(d, nm))
                    it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable); it.setCheckState(QtCore.Qt.Unchecked)
                    self.strat_list.addItem(it)
        if self.strat_list.count() == 0: self.strat_list.addItem(W.QListWidgetItem("(資料夾冇 .py 策略)"))
        self.strat_list.setFixedHeight(min(400, 8 + self.strat_list.count() * (self.strat_list.sizeHintForRow(0) + 2)))
        self.strat_list.blockSignals(False)
        cur = self.cur() if hasattr(self, "charts") else None
        self.set_checked(keep or (cur.bt_paths if cur else []))
        self.strat_btn.setToolTip(f"可以剔幾個策略, 同一個圖表同時各自獨立行\n{d}")
    def pick_strat_dir(self):
        d = W.QFileDialog.getExistingDirectory(self, "選擇策略資料夾", strat_dir())
        if d: CFG["strat_dir"] = d; save_cfg(); self.refresh_strats()
    def fill_edit_menu(self):
        self.edit_menu.clear()
        for q in self.strat_paths(False):
            a = self.edit_menu.addAction(os.path.splitext(os.path.basename(q))[0])
            a.triggered.connect(lambda _, q=q: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(q)))
        if self.edit_menu.isEmpty(): self.edit_menu.addAction("(冇策略)").setEnabled(False)
    def run_bt(self):
        p, paths = self.cur(), self.strat_paths()
        if not paths: W.QMessageBox.information(self, "回測", f"請喺「策略」剔一個或者幾個策略\n(策略 .py 放喺: {strat_dir()})"); return
        if p.df is None: W.QMessageBox.information(self, "回測", "請先開一個 CSV"); return
        W.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try: p.run_bt(paths)
        finally: W.QApplication.restoreOverrideCursor()
        if p.bts and not self.bt_dock.isVisible():
            self.bt_dock.show(); self.resizeDocks([self.bt_dock], [max(240, self.height() // 3)], QtCore.Qt.Vertical)

    def build_bt_dock(self):
        self.bt_dock = W.QDockWidget("回測結果", self); self.bt_dock.setObjectName("bt_dock")
        w = W.QWidget(); lay = W.QVBoxLayout(w); lay.setContentsMargins(4, 2, 4, 4)
        self.bt_sum = W.QLabel("未有回測"); self.bt_sum.setTextFormat(QtCore.Qt.RichText); self.bt_sum.setWordWrap(True)
        self.cmp = W.QTableWidget(0, len(self.CMP_H)); self.cmp.setHorizontalHeaderLabels(self.CMP_H)
        self.cmp.verticalHeader().hide(); self.cmp.setEditTriggers(W.QAbstractItemView.NoEditTriggers)
        self.cmp.setSelectionBehavior(W.QAbstractItemView.SelectRows); self.cmp.setSelectionMode(W.QAbstractItemView.SingleSelection)
        self.cmp.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeToContents)
        self.cmp.itemSelectionChanged.connect(self.show_bt_detail); self.cmp.hide()
        self.bt_model = TradeModel(); self.bt_table = W.QTableView(); self.bt_table.setModel(self.bt_model)
        self.bt_table.setSelectionBehavior(W.QAbstractItemView.SelectRows); self.bt_table.verticalHeader().hide()
        self.bt_table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeToContents)
        self.bt_table.clicked.connect(self.goto_trade)
        self.eq_axis = DateAxis("bottom")
        self.bt_eq = pg.PlotWidget(axisItems={"bottom": self.eq_axis}); self.bt_eq.setMinimumWidth(320)
        self.bt_eq.showGrid(x=True, y=True, alpha=0.2); self.bt_eq.setLabel("left", "累積淨利 (pips)"); self.bt_eq.addLegend(offset=(10, 5))
        left = W.QWidget(); ll = W.QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0); ll.addWidget(self.cmp); ll.addWidget(self.bt_table)
        sp = W.QSplitter(); sp.addWidget(left); sp.addWidget(self.bt_eq); sp.setSizes([800, 450])
        lay.addWidget(self.bt_sum); lay.addWidget(sp); self.bt_dock.setWidget(w)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.bt_dock); self.bt_dock.hide()
        self.bt_dock.visibilityChanged.connect(lambda v: self.bt_btn.setChecked(v))
        self.bt_items, self.bt_sel = [], None

    CMP_H = ["策略", "單數", "勝率", "毛利", "成本", "淨利", "盈利因子", "最大回撤", "最好", "最差", "持倉中"]
    def update_bt_dock(self):
        p = self.cur(); self.bt_eq.clear()
        ts = p.bts if p else []
        self.bt_items = ts + ([p.bt_all] if p and p.bt_all else [])
        if not ts:
            self.cmp.hide(); self.bt_sum.setText("未有回測  (喺「策略」剔一個或者幾個, 撳 ▶ 回測)"); self.bt_model.set(None, None, 5); return
        multi = len(ts) > 1; self.cmp.setVisible(multi)
        self.eq_axis.dates, self.eq_axis.fmt = p.df.index, D
        if multi:                                                       # 比較表: 每個策略一行 + 合計
            self.cmp.blockSignals(True); self.cmp.setRowCount(len(self.bt_items))
            for r, t in enumerate(self.bt_items):
                st = t["stats"]; pf = "∞" if st["pf"] == float("inf") else f"{st['pf']:.2f}"
                vals = [t["name"], str(st["trades"]), f"{st['winrate']:.1f}%", f"{st['gross']:+.1f}", f"-{st['costs']:.1f}",
                        f"{st['total']:+.1f}", pf, f"{st['mdd']:.1f}", f"{st['best']:+.1f}", f"{st['worst']:+.1f}",
                        f"{st['open']:+.1f}" if st["has_open"] else ""]
                for c, v in enumerate(vals):
                    it = W.QTableWidgetItem(v)
                    if c: it.setTextAlignment(int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter))
                    if c == 0:
                        it.setForeground(pg.mkColor(t.get("color", CFG["ui_fg"]) if not t.get("combined") else CFG["ui_fg"]))
                        f = it.font(); f.setBold(bool(t.get("combined"))); it.setFont(f)
                    if c in (3, 5, 10) and v: it.setForeground(pg.mkColor(BT_WIN if float(v) > 0 else BT_LOSS))
                    self.cmp.setItem(r, c, it)
            h = self.cmp.horizontalHeader().height() + sum(self.cmp.rowHeight(r) for r in range(self.cmp.rowCount())) + 4
            self.cmp.setFixedHeight(h); self.cmp.blockSignals(False)
            names = [t["name"] for t in self.bt_items]
            row = names.index(self.bt_sel) if self.bt_sel in names else len(self.bt_items) - 1      # 預設睇合計
            self.cmp.selectRow(row)
        for t in self.bt_items:                                         # 資金曲線: 每個策略 + 合計 (按出場時間)
            if not len(t["equity"]): continue
            x = np.r_[t["eq_x"][0], t["eq_x"]]; y = np.r_[0.0, t["equity"]]
            if t.get("combined"): pen = pg.mkPen(CFG["chart_fg"], width=2.2)
            else: pen = pg.mkPen(t["color"] if multi else "#2962ff", width=1.3)
            self.bt_eq.plot(x, y, pen=pen, name=t["name"] if multi else None)
        self.bt_eq.addItem(pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen("#787b86", style=QtCore.Qt.DashLine)))
        self.show_bt_detail()
    def show_bt_detail(self):
        p = self.cur()
        if not p or not self.bt_items: return
        r = self.cmp.currentRow() if self.cmp.isVisible() and self.cmp.currentRow() >= 0 else len(self.bt_items) - 1
        t = self.bt_items[min(r, len(self.bt_items) - 1)]; self.bt_sel = t["name"]
        st = t["stats"]; col = lambda v: BT_WIN if v > 0 else BT_LOSS
        pf = "∞" if st["pf"] == float("inf") else f"{st['pf']:.2f}"
        head = f"<b>合計</b> ({len(t['names'])} 個策略同時各自獨立持倉)" if t.get("combined") else f"<b>{t['name']}</b>"
        txt = (f"{head} · {p.title()} {p.tf} · 平倉 {st['trades']} 單 · 勝率 {st['winrate']:.1f}% · "
               f"毛利 <span style='color:{col(st['gross'])}'>{st['gross']:+.1f}</span> · 成本 -{st['costs']:.1f} · "
               f"淨利 <b style='color:{col(st['total'])}'>{st['total']:+.1f} pips</b> · 平均 {st['avg']:+.1f} · 盈利因子 {pf} · "
               f"最大回撤 {st['mdd']:.1f} · 最好 {st['best']:+.1f} / 最差 {st['worst']:+.1f} · 平均持倉 {st['bars']:.0f} 根 K")
        if st["has_open"]: txt += f" · 持倉中 <span style='color:{col(st['open'])}'>{st['open']:+.1f}</span>"
        if t.get("combined"):
            txt += (f"<br><span style='color:#787b86'>每個策略各自 1 單位 · 回撤按平倉時間計 (所有策略合埋嘅已實現淨利) · "
                    f"撳上面邊一行就睇邊個策略嘅交易 · 用時 {t.get('secs', 0):.2f}s</span>")
        else:
            fill = "下一根開市價成交" if t["fill"] == "next_open" else "訊號 K 收市價成交"
            txt += (f"<br><span style='color:#787b86'>{fill} · 每邊成本 {t['cost'] / t['pip']:.2f} pip · "
                    f"1 pip = {t['pip']:g} · 用時 {t.get('secs', 0):.2f}s</span>")
        self.bt_sum.setText(txt); self.bt_model.set(t, p.df.index, p.dec)
    def goto_trade(self, m):
        p, t = self.cur(), self.bt_model.t
        if p and t: p.goto_bars(t["ei"][m.row()], t["xi"][m.row()])

    # --- 右邊: 資料夾 + CSV 檔案清單
    def build_files(self):
        self.file_panel = W.QWidget(); lay = W.QVBoxLayout(self.file_panel); lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(W.QLabel("資料夾"))
        row = W.QHBoxLayout()
        self.folder = W.QLineEdit(CFG.get("folder", "")); self.folder.setPlaceholderText("貼上資料夾路徑, Enter 確認")
        self.folder.returnPressed.connect(lambda: self.set_folder(self.folder.text()))
        b = W.QToolButton(); b.setText("…"); b.setToolTip("選擇資料夾"); b.clicked.connect(self.pick_folder)
        r = W.QToolButton(); r.setText("⟳"); r.setToolTip("重新整理清單 + 重新讀取目前檔案"); r.clicked.connect(self.refresh_all)
        for w in (self.folder, b, r): row.addWidget(w)
        lay.addLayout(row)
        self.filt = W.QLineEdit(); self.filt.setPlaceholderText("篩選檔名 (例如 GBP)"); self.filt.textChanged.connect(self.refresh_files)
        self.files = W.QListWidget()
        self.files.itemClicked.connect(lambda it: self.cur().set_symbol(it.data(UR), SRC_CSV))
        self.files.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.files.customContextMenuRequested.connect(self.file_menu)
        tz = W.QHBoxLayout(); tz.addWidget(W.QLabel("時間偏移"))
        self.tz = W.QSpinBox(); self.tz.setRange(-12, 14); self.tz.setSuffix(" 小時"); self.tz.setValue(int(CFG["tz_off"]))
        self.tz.setToolTip("CSV 時間 + 呢個偏移 (例如 UTC 數據想睇香港時間填 8; 影響日線 / 週線點樣切)")
        self.tz_timer = QtCore.QTimer(self); self.tz_timer.setSingleShot(True); self.tz_timer.setInterval(600)
        self.tz_timer.timeout.connect(self.apply_tz); self.tz.valueChanged.connect(lambda _: self.tz_timer.start())
        tz.addWidget(self.tz); tz.addStretch()
        hint = W.QLabel("撳一下 = 目前圖表開\n右鍵 = 新圖表開"); hint.setStyleSheet("color:#787b86")
        lay.addWidget(self.filt); lay.addWidget(self.files); lay.addLayout(tz); lay.addWidget(hint)
        self.refresh_files()
    def pick_folder(self):
        d = W.QFileDialog.getExistingDirectory(self, "選擇資料夾", self.folder.text() or os.path.expanduser("~"))
        if d: self.set_folder(d)
    def set_folder(self, d):
        d = d.strip().strip('"')
        if d and not os.path.isdir(d):
            W.QMessageBox.warning(self, "資料夾", f"搵唔到資料夾:\n{d}"); return
        CFG["folder"] = d; save_cfg(); self.folder.setText(d); self.refresh_files()
    def refresh_files(self, *_):
        self.files.clear(); d = self.folder.text().strip().strip('"'); flt = self.filt.text().strip().lower()
        if not os.path.isdir(d): return
        try: names = sorted(os.listdir(d), key=str.lower)
        except OSError: return
        for nm in names:
            if not nm.lower().endswith((".csv", ".txt")) or (flt and flt not in nm.lower()): continue
            fp = os.path.join(d, nm)
            try: mb = os.path.getsize(fp) / 1e6
            except OSError: continue
            it = W.QListWidgetItem(f"{os.path.splitext(nm)[0]}    {mb:,.1f} MB"); it.setData(UR, fp); it.setToolTip(fp)
            self.files.addItem(it)
        self.sync_toolbar()
    def refresh_all(self):
        self.refresh_files()
        if self.cur() and self.cur().sym: self.cur().reload()         # 檔案有更新 (mtime 變咗) 會重新讀
    def file_menu(self, pos):
        it = self.files.itemAt(pos)
        if it is None: return
        m = W.QMenu(self); a = m.addAction("在新圖表開啟")
        if m.exec_(self.files.mapToGlobal(pos)) is a: self.new_chart(it.data(UR), src=SRC_CSV)
    def reload_all(self):
        for i in range(self.charts.count()):
            if self.charts.widget(i).sym: self.charts.widget(i).reload()
    def apply_tz(self):
        if self.tz.value() == CFG["tz_off"]: return
        CFG["tz_off"] = self.tz.value(); save_cfg(); self.reload_all()

    # --- 日期範圍 (所有圖表 + 回測一齊用)
    def show_dates(self):
        today = QtCore.QDate.currentDate()
        for w, k, dflt in ((self.d0, "date_from", QtCore.QDate(2012, 1, 1)), (self.d1, "date_to", today)):
            d = QtCore.QDate.fromString(CFG.get(k) or "", "yyyy-MM-dd")
            w.blockSignals(True); w.setDate(d if d.isValid() else dflt); w.blockSignals(False)
    def apply_dates(self):
        a, b = self.d0.date(), self.d1.date()
        if a > b:
            self.statusBar().showMessage("開始日期遲過結束日期, 未套用", 5000); return
        new = (a.toString("yyyy-MM-dd"), "" if b == QtCore.QDate.currentDate() else b.toString("yyyy-MM-dd"))
        if new == (CFG.get("date_from"), CFG.get("date_to")): return
        CFG["date_from"], CFG["date_to"] = new; save_cfg(); self.reload_all()
    def reset_dates(self):
        self.date_timer.stop(); CFG["date_from"], CFG["date_to"] = "2012-01-01", ""
        self.show_dates(); save_cfg(); self.reload_all()
    def open_csv(self):
        p, _ = W.QFileDialog.getOpenFileName(self, "開啟 CSV", self.folder.text(), "CSV (*.csv *.txt);;全部檔案 (*)")
        if p:
            self.set_folder(os.path.dirname(p)); self.cur().set_symbol(p, SRC_CSV); self.side.setCurrentWidget(self.file_panel)

    # --- 工作區: 儲存 / 開啟 / 自動復原
    def build_menu(self):
        m = self.menuBar().addMenu("檔案")
        for text, key, fn in (("開啟 CSV 檔…", "Ctrl+Shift+O", self.open_csv), (None, None, None),
                              ("開啟工作區…", "Ctrl+O", self.open_ws), ("儲存工作區", "Ctrl+S", self.save_ws),
                              ("另存新檔…", "Ctrl+Shift+S", lambda: self.save_ws(True))):
            if text is None: m.addSeparator(); continue
            a = m.addAction(text); a.setShortcut(key); a.triggered.connect(lambda _, fn=fn: fn())
    def workspace(self):
        return dict(version=3, cfg=CFG, watchlists=self.wl_data(), side=self.side.currentIndex(), current=self.charts.currentIndex(),
                    charts=[self.charts.widget(i).get_state() for i in range(self.charts.count())],
                    main_split=self.main_split.sizes(), geometry=bytes(self.saveGeometry().toBase64()).decode(),
                    dock2=bytes(self.saveState().toBase64()).decode())
    def write_ws(self, path):
        json.dump(self.workspace(), open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1,
                  default=lambda o: o.item() if hasattr(o, "item") else str(o))
    def save_ws(self, as_new=False):
        if as_new or not self.ws_path:
            p, _ = W.QFileDialog.getSaveFileName(self, "儲存工作區", self.ws_path or "workspace.chartex.json", "chartEX 工作區 (*.json)")
            if not p: return
            self.ws_path = p
        self.write_ws(self.ws_path); self.setWindowTitle(f"chartEX - {os.path.basename(self.ws_path)}")
    def open_ws(self):
        p, _ = W.QFileDialog.getOpenFileName(self, "開啟工作區", "", "chartEX 工作區 (*.json)")
        if p:
            try: self.load_workspace(p)
            except Exception as e: W.QMessageBox.warning(self, "開啟失敗", f"{type(e).__name__}: {e}")
    def load_workspace(self, path, keep_path=True):
        s = json.load(open(path, encoding="utf-8"))
        CFG.update(s.get("cfg", {})); save_cfg(); self.apply_settings()
        self.folder.setText(CFG.get("folder", "")); self.refresh_files()
        self.tz.blockSignals(True); self.tz.setValue(int(CFG["tz_off"])); self.tz.blockSignals(False); self.show_dates()
        self.autoy_btn.setChecked(CFG["auto_y"])
        if s.get("watchlists"): self.set_wl(s["watchlists"]); self.save_wl()
        if "side" in s: self.side.setCurrentIndex(s["side"])
        while self.charts.count():
            p = self.charts.widget(0); p.stop_live(True); self.charts.removeTab(0); p.deleteLater()
        for cs in s.get("charts", []):
            p = ChartPanel(cs.get("sym", ""), cs.get("tf", "1h"), [tuple(i) for i in cs.get("inds", [])], cs.get("src"))
            p.kind, p.cross, p.restore = cs.get("kind", "Candle"), cs.get("cross", True), cs
            paths = cs.get("strategies") or ([cs["strategy"]] if cs.get("strategy") else [])
            p.bt_paths = [q for q in paths if os.path.isfile(q)]
            if cs.get("split"): p.split.setSizes(cs["split"])
            self.add_panel(p)
        if self.charts.count() == 0: self.new_chart("AAPL", "1D", SRC_YAHOO)
        self.charts.setCurrentIndex(min(s.get("current", 0), self.charts.count() - 1))
        if s.get("main_split"): self.main_split.setSizes(s["main_split"])
        if s.get("geometry"): self.restoreGeometry(QtCore.QByteArray.fromBase64(s["geometry"].encode()))
        if s.get("dock2"): self.restoreState(QtCore.QByteArray.fromBase64(s["dock2"].encode()))
        if keep_path: self.ws_path = path; self.setWindowTitle(f"chartEX - {os.path.basename(path)}")
    def resume(self):
        p = sys.argv[1] if len(sys.argv) > 1 else LAST_FILE
        if os.path.exists(p):
            try: self.load_workspace(p, keep_path=(p != LAST_FILE))
            except Exception as e: print("復原失敗:", e)
        if self.charts.count() == 0: self.new_chart("AAPL", "1D", SRC_YAHOO)
    def closeEvent(self, e):
        for i in range(self.charts.count()): self.charts.widget(i).stop_live()
        try: self.write_ws(LAST_FILE)
        except Exception: pass
        super().closeEvent(e)

    # --- 右邊「自選」watchlist (Yahoo / IBKR 代號)
    def build_watchlist(self):
        self.wl_panel = W.QWidget(); lay = W.QVBoxLayout(self.wl_panel); lay.setContentsMargins(4, 4, 4, 4)
        self.tabs = W.QTabWidget(); self.tabs.setTabsClosable(True); self.tabs.setMovable(True)
        nb = W.QToolButton(); nb.setText("+"); nb.setToolTip("新 Watchlist"); nb.clicked.connect(self.new_list); self.tabs.setCornerWidget(nb)
        self.tabs.tabCloseRequested.connect(lambda i: (self.tabs.removeTab(i), self.save_wl()))
        self.tabs.tabBarDoubleClicked.connect(self.rename_list)
        self.add_edit = W.QLineEdit(); self.add_edit.setPlaceholderText("輸入代號, Enter 加入; Del 刪除")
        self.add_edit.returnPressed.connect(self.add_symbol)
        hint = W.QLabel("撳一下 = 目前圖表開\n右鍵 = 新圖表開 · 雙擊分頁改名"); hint.setStyleSheet("color:#787b86")
        lay.addWidget(self.tabs); lay.addWidget(self.add_edit); lay.addWidget(hint)
        data = {"Watchlist 1": ["AAPL", "MSFT", "NVDA", "700", "GBPUSD=X", "USDJPY=X"]}
        if os.path.exists(WL_FILE):
            try: data = json.load(open(WL_FILE, encoding="utf-8"))
            except Exception: pass
        self.set_wl(data)
    def set_wl(self, data):
        while self.tabs.count(): self.tabs.removeTab(0)
        for name, syms in data.items(): self.make_list(name, syms)
    def wl_src(self):                    # 撳自選代號: 目前係 CSV 就轉用 Yahoo
        p = self.cur(); return p.src if p and p.src != SRC_CSV else SRC_YAHOO
    def make_list(self, name, syms=()):
        lw = W.QListWidget(); lw.addItems(list(syms))
        lw.itemClicked.connect(lambda it: self.cur().set_symbol(it.text(), self.wl_src()))
        sc = W.QShortcut(QtGui.QKeySequence("Delete"), lw)
        sc.activated.connect(lambda: ([lw.takeItem(lw.row(i)) for i in lw.selectedItems()], self.save_wl()))
        lw.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        lw.customContextMenuRequested.connect(lambda pos, lw=lw: self.wl_menu(lw, pos))
        self.tabs.setCurrentIndex(self.tabs.addTab(lw, name))
    def wl_menu(self, lw, pos):
        it = lw.itemAt(pos)
        if it is None: return
        m = W.QMenu(self); a = m.addAction("在新圖表開啟")
        if m.exec_(lw.mapToGlobal(pos)) is a: self.new_chart(it.text(), src=self.wl_src())
    def new_list(self):
        n, ok = W.QInputDialog.getText(self, "新 Watchlist", "名稱:")
        if ok and n: self.make_list(n); self.save_wl()
    def rename_list(self, i):
        if i < 0: return
        n, ok = W.QInputDialog.getText(self, "改名", "名稱:", text=self.tabs.tabText(i))
        if ok and n: self.tabs.setTabText(i, n); self.save_wl()
    def add_symbol(self):
        syms = self.add_edit.text().replace(",", " ").upper().split()        # 可一次輸入多個, 用空格或逗號分隔
        if not syms: return
        if self.tabs.count() == 0: self.make_list("Watchlist 1")
        lw = self.tabs.currentWidget()       # 注意: 唔可以寫 "if lw:", 空嘅 QListWidget 會被當成 False
        have = {lw.item(i).text() for i in range(lw.count())}
        for t in syms:
            if t not in have: lw.addItem(t); have.add(t)
        lw.scrollToBottom(); self.add_edit.clear(); self.save_wl()
    def wl_data(self):
        return {self.tabs.tabText(i): [self.tabs.widget(i).item(j).text() for j in range(self.tabs.widget(i).count())]
                for i in range(self.tabs.count())}
    def save_wl(self):
        json.dump(self.wl_data(), open(WL_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # --- 設定 / 主題
    def apply_settings(self):
        c = CFG; f = QtGui.QFont()
        if c["font"]: f.fromString(c["font"])
        else: f = W.QApplication.font()
        W.QApplication.setFont(f)
        bc = pg.mkColor(c["ui_bg"]); bc = (bc.darker(108) if bc.lightness() > 128 else bc.lighter(130)).name()
        W.QApplication.instance().setStyleSheet(
            f"QWidget{{background:{c['ui_bg']};color:{c['ui_fg']}}} "
            f"QLineEdit,QListWidget,QComboBox,QSpinBox,QTabWidget::pane{{background:{c['chart_bg']};border:1px solid {bc}}} "
            f"QPushButton,QToolButton{{background:{bc};padding:4px 8px;border-radius:3px}} "
            f"QSplitter::handle{{background:{bc}}} QToolButton:checked{{background:#2962ff;color:white}} "
            f"QListWidget::item:selected{{background:#2962ff;color:white}}")
        for i in range(self.charts.count()): self.charts.widget(i).apply_theme(f)

if __name__ == "__main__":
    migrate_old_files()
    app = W.QApplication(sys.argv); app.setStyle("Fusion"); app.setApplicationName("chartEX"); load_cfg()
    m = Main(); m.show(); sys.exit(app.exec_())
