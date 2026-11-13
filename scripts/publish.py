#!/usr/bin/env python3
"""NSE Desk: fetch market data, compute dashboards, render PNG/PDF, post to Telegram.

Usage: python scripts/publish.py <pre|intra|post|weekly> [--synthetic] [--no-telegram] [--force]
Runs inside GitHub Actions on a timer. Uses no AI: every number is a quote or arithmetic.
"""
import datetime as dt
import hashlib
import json
import math
import os
import sys
import time

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data")
IMG = os.path.join(ROOT, "docs", "img")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TITLE = {"pre": "PRE-MARKET PLAN", "intra": "INTRADAY OUTLOOK", "post": "POST-MARKET REVIEW",
         "weekly": "WEEKLY REVIEW & NEXT-WEEK PLAN"}
INDEX = {"NIFTY": ("^NSEI", "NIFTY 50"), "BANKNIFTY": ("^NSEBANK", "BANK NIFTY"), "SENSEX": ("^BSESN", "SENSEX")}
VIX = "^INDIAVIX"
GLOBAL = {"S&P 500": "^GSPC", "Nasdaq": "^IXIC", "Dow": "^DJI", "Nikkei": "^N225", "Hang Seng": "^HSI",
          "USD/INR": "INR=X", "Brent": "BZ=F", "Gold": "GC=F", "DXY": "DX-Y.NYB", "US 10Y": "^TNX"}
SECTORS = {"IT": "^CNXIT", "Bank": "^NSEBANK", "Pvt Bank": "NIFTY_PVT_BANK.NS", "PSU Bank": "^CNXPSUBANK",
           "Fin Services": "NIFTY_FIN_SERVICE.NS", "Auto": "^CNXAUTO", "FMCG": "^CNXFMCG", "Pharma": "^CNXPHARMA",
           "Healthcare": "NIFTY_HEALTHCARE.NS", "Metal": "^CNXMETAL", "Realty": "^CNXREALTY", "Energy": "^CNXENERGY",
           "Oil & Gas": "NIFTY_OIL_AND_GAS.NS", "Media": "^CNXMEDIA", "Infra": "^CNXINFRA", "PSE": "^CNXPSE",
           "Cons Durables": "NIFTY_CONSR_DURBL.NS", "Midcap 100": "NIFTY_MIDCAP_100.NS", "Smallcap 100": "^CNXSC",
           "Nifty 500": "^CRSLDX", "Next 50": "^NSMIDCP"}
UNIVERSE_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
FALLBACK = ("RELIANCE TCS HDFCBANK ICICIBANK INFY SBIN BHARTIARTL ITC LT KOTAKBANK AXISBANK HINDUNILVR BAJFINANCE "
            "MARUTI SUNPHARMA TITAN ULTRACEMCO ASIANPAINT NTPC M&M TATASTEEL POWERGRID HCLTECH WIPRO ADANIENT "
            "ADANIPORTS ONGC COALINDIA JSWSTEEL BAJAJFINSV NESTLEIND TECHM GRASIM HINDALCO DRREDDY CIPLA EICHERMOT "
            "BRITANNIA APOLLOHOSP DIVISLAB HEROMOTOCO BAJAJ-AUTO TATACONSUM INDUSINDBK SBILIFE HDFCLIFE BPCL "
            "SHRIRAMFIN TRENT BEL HAL DLF ADANIGREEN ADANIPOWER AMBUJACEM BANKBARODA PNB CANBK CHOLAFIN DABUR GAIL "
            "GODREJCP HAVELLS ICICIPRULI ICICIGI IOC IRCTC JINDALSTEL LICI LODHA NAUKRI PIDILITIND PFC RECLTD SIEMENS "
            "SRF TVSMOTOR TORNTPHARM VEDL VBL ZYDUSLIFE ABB BOSCHLTD COLPAL MARICO MUTHOOTFIN PERSISTENT POLYCAB "
            "DIXON CUMMINSIND INDHOTEL LUPIN AUROPHARMA BHEL SAIL NMDC TATAPOWER KALYANKJIL FEDERALBNK IDFCFIRSTB "
            "UNIONBANK JIOFIN MAXHEALTH INDIGO SUZLON IRFC RVNL").split()


# A timer-started run is only useful inside its slot's window (IST minutes of the day).
# GitHub's timer can start a run hours late; outside the window the run publishes nothing.
SLOT_WINDOW = {"pre": (7 * 60 + 30, 9 * 60 + 14), "intra": (9 * 60 + 45, 11 * 60 + 30), "post": (15 * 60 + 45, 23 * 60 + 59)}


def now_ist():
    return dt.datetime.now(IST)


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, separators=(",", ":"), ensure_ascii=False)


def r2(x, n=2):
    try:
        x = float(x)
        return round(x, n) if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


# ---------------- data source ----------------
def synth(t, interval):
    rng = np.random.default_rng(int(hashlib.md5(t.encode()).hexdigest()[:8], 16))
    today = pd.Timestamp(now_ist().date())
    if interval in ("1d", "1mo"):
        idx, vol = pd.bdate_range(end=today, periods=520), 0.013
    else:
        step = int(interval[:-1])
        days = pd.bdate_range(end=today, periods=5 if step < 60 else 22)
        idx = pd.DatetimeIndex(np.concatenate([pd.date_range(d + pd.Timedelta("9h15m"), d + pd.Timedelta("15h25m"),
                                                             freq=f"{step}min").values for d in days]))
        vol = 0.013 / math.sqrt(375 / step)
    n = len(idx)
    base = 15.0 if t == VIX else float(rng.uniform(80, 3000))
    c = base * np.exp(np.cumsum(rng.normal(0.0005 if t != VIX else 0, vol, n)))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, vol / 4, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 3, n)))
    lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 3, n)))
    v = rng.integers(200000, 6000000, n).astype(float)
    if rng.integers(0, 6) == 0:
        v[-1] *= 5
        c[-1] = h[-1] = max(h[-1], c[-2] * 1.05)
    return pd.DataFrame({"Open": o, "High": h, "Low": lo, "Close": c, "Volume": v}, index=idx)


class Source:
    def __init__(self, fake):
        self.fake, self.errors = fake, []

    def many(self, tickers, period="2y", interval="1d"):
        tickers = list(dict.fromkeys(tickers))
        if self.fake:
            return {t: synth(t, interval) for t in tickers}
        import yfinance as yf
        out = {}
        for i in range(0, len(tickers), 100):
            chunk, df = tickers[i:i + 100], None
            for k in range(3):
                try:
                    df = yf.download(chunk, period=period, interval=interval, group_by="ticker",
                                     auto_adjust=False, progress=False, threads=True)
                    if df is not None and len(df):
                        break
                except Exception as exc:
                    print("download failed:", type(exc).__name__)
                time.sleep(4 * (k + 1))
            if df is None or not len(df):
                continue
            for t in chunk:
                try:
                    x = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                    x = x[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"]).copy()
                    idx = pd.DatetimeIndex(x.index)
                    if idx.tz is not None:
                        idx = idx.tz_convert("Asia/Kolkata").tz_localize(None)
                    x.index = idx
                    if len(x):
                        out[t] = x
                except (KeyError, TypeError):
                    pass
        return out


def universe(fake):
    path = os.path.join(ROOT, "config", "universe.csv")
    if not fake:
        try:
            import io
            import requests
            r = requests.get(UNIVERSE_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            u = pd.read_csv(io.StringIO(r.text))
            u = u[[c for c in ("Symbol", "Industry") if c in u.columns]].dropna(subset=["Symbol"])
            if len(u) > 300:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                u.to_csv(path, index=False)
        except Exception as exc:
            print("universe refresh failed:", type(exc).__name__)
    try:
        u = pd.read_csv(path)
        syms = u["Symbol"].astype(str).str.strip().tolist()
        if len(syms) > 50:
            return syms, "Nifty 500", (dict(zip(syms, u["Industry"].astype(str))) if "Industry" in u.columns else {})
    except Exception:
        pass
    return FALLBACK, "built-in large-cap list", {}


# ---------------- indicators ----------------
def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def trend_tag(c):
    if c is None or len(c) < 30:
        return None
    e20, e50 = c.ewm(span=20).mean().iloc[-1], c.ewm(span=50).mean().iloc[-1]
    r, last = rsi(c).iloc[-1], c.iloc[-1]
    s = int(last > e20) + int(e20 > e50) + int(r > 55) - int(last < e20) - int(e20 < e50) - int(r < 45)
    return {"tag": "Bullish" if s >= 2 else ("Bearish" if s <= -2 else "Neutral"), "rsi": r2(r, 0),
            "vs20": r2((last / e20 - 1) * 100)}


def pivots(h, l, c):
    p = (h + l + c) / 3
    return {"R3": r2(h + 2 * (p - l)), "R2": r2(p + (h - l)), "R1": r2(2 * p - l), "P": r2(p),
            "S1": r2(2 * p - h), "S2": r2(p - (h - l)), "S3": r2(l - 2 * (h - p))}


def gap_bucket(g):
    if g is None:
        return "*"
    return "gd2" if g <= -0.5 else "gd1" if g <= -0.15 else "flat" if g < 0.15 else "gu1" if g < 0.5 else "gu2"


GAP_TEXT = {"gd2": "gap-down over 0.5%", "gd1": "small gap-down", "flat": "flat open",
            "gu1": "small gap-up", "gu2": "gap-up over 0.5%", "*": "any open"}


def vix_bucket(v):
    return "*" if v is None else ("lo" if v < 13 else ("mid" if v <= 18 else "hi"))


def lookup(table, keys):
    best = None
    for k in keys:
        row = (table or {}).get(k)
        if row and row.get("n", 0) >= 30:
            return k, row
        if row and (best is None or row["n"] > best[1]["n"]):
            best = (k, row)
    return best if best else (None, None)


def sentiment(mkt, vixc):
    if mkt is None or vixc is None:
        return "No read", "VIX or index change unavailable"
    up, dn = mkt > 0.2, mkt < -0.2
    vup, vdn = vixc > 2, vixc < -2
    if up and vdn:
        return "Bullish", "Market rising with VIX falling: healthy advance"
    if up and vup:
        return "Bearish divergence", "Market rising but VIX also rising: hedging underneath, be careful with longs"
    if dn and vup:
        return "Bearish", "Market falling with VIX rising: fear is expanding"
    if dn and not vup:
        return "Possible reversal", "Market falling but VIX not rising: selling lacks fear, watch for a base"
    return "No clear signal", "Sideways market or mixed VIX: do not lean on VIX today"


# ---------------- index view ----------------
def index_view(slot, key, name, d, intr, vix, vixchg, rates, today):
    c = d["Close"]
    cur, prev = d.iloc[-1], d.iloc[-2]
    cur_date = d.index[-1].date()
    wk = d.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
    chg = (cur.Close / prev.Close - 1) * 100
    sma = {n: c.rolling(n).mean().iloc[-1] for n in (20, 50, 200)}
    rs = rsi(c).iloc[-1]
    score = sum((1 if cur.Close > m else -1) for m in sma.values() if m == m) + (1 if rs > 55 else -1 if rs < 45 else 0)
    v = {"key": key, "name": name, "date": str(cur_date), "close": r2(cur.Close), "chg": r2(chg),
         "open": r2(cur.Open), "high": r2(cur.High), "low": r2(cur.Low), "rsi": r2(rs, 1),
         "sma20": r2(sma[20]), "sma50": r2(sma[50]), "sma200": r2(sma[200]),
         "hi52": r2(d["High"].iloc[-252:].max()), "lo52": r2(d["Low"].iloc[-252:].min()),
         "w": r2((c.iloc[-1] / c.iloc[-6] - 1) * 100), "m": r2((c.iloc[-1] / c.iloc[-22] - 1) * 100),
         "bias": "Bullish" if score >= 2 else ("Bearish" if score <= -2 else "Neutral"), "bias_score": int(score),
         "candles": [[str(i.date()), r2(o), r2(h), r2(l), r2(x)] for i, o, h, l, x in
                     zip(d.index[-40:], d.Open.iloc[-40:], d.High.iloc[-40:], d.Low.iloc[-40:], d.Close.iloc[-40:])],
         "stale": slot in ("intra", "post") and cur_date != today, "probs": [], "gap": None}
    gap = None
    if slot == "intra" and cur_date == today:
        base, ref = prev, prev.Close
        gap = (cur.Open / prev.Close - 1) * 100
        v["gap"] = {"pct": r2(gap), "kind": "actual open"}
        o5 = (intr.get("5m") if intr else None)
        if o5 is not None:
            t5 = o5[o5.index.date == today].iloc[:3]
            if len(t5):
                v["or"] = {"hi": r2(t5.High.max()), "lo": r2(t5.Low.min()), "text": "opening 15-min range"}
    elif slot == "weekly":
        base, ref = None, cur.Close
    else:
        base, ref = cur, cur.Close
    if slot == "weekly":
        lv = pivots(wk.High.iloc[-1], wk.Low.iloc[-1], wk.Close.iloc[-1])
        lv.update({"PH": r2(wk.High.iloc[-1]), "PL": r2(wk.Low.iloc[-1]), "PC": r2(wk.Close.iloc[-1])})
        v["week"] = {"chg": r2((wk.Close.iloc[-1] / wk.Close.iloc[-2] - 1) * 100), "hi": lv["PH"], "lo": lv["PL"],
                     "days": [[str(i.date()), r2(x), r2(p)] for i, x, p in
                              zip(d.index[-5:], c.iloc[-5:], c.pct_change().iloc[-5:] * 100)]}
    else:
        lv = pivots(base.High, base.Low, base.Close)
        lv.update({"PH": r2(base.High), "PL": r2(base.Low), "PC": r2(base.Close)})
    v["levels"] = lv
    if vix:
        days = 5 if slot == "weekly" else 1
        mv = ref * vix / 100 * math.sqrt(days / 252)
        v["exp"] = {"lo": r2(ref - mv), "hi": r2(ref + mv), "pct": r2(mv / ref * 100),
                    "horizon": "week" if days == 5 else "day"}
    lad = [(k, lv[k]) for k in ("R3", "R2", "R1", "P", "S1", "S2", "S3")]
    lad += [("Prev High" if slot != "weekly" else "Week High", lv["PH"]),
            ("Prev Low" if slot != "weekly" else "Week Low", lv["PL"]),
            ("20-DMA", v["sma20"]), ("50-DMA", v["sma50"]), ("200-DMA", v["sma200"]), ("52w High", v["hi52"])]
    if v.get("exp"):
        lad += [("VIX range high", v["exp"]["hi"]), ("VIX range low", v["exp"]["lo"])]
    lad = [(k, x) for k, x in lad if x is not None and abs(x / cur.Close - 1) < 0.08] + [("LAST", v["close"])]
    v["ladder"] = [{"k": k, "v": x} for k, x in sorted(lad, key=lambda z: -z[1])]
    unit = "weekly close" if slot == "weekly" else "15-min close"
    v["scenarios"] = [
        {"name": "Bullish breakout", "trigger": f"{unit} above {lv['R1']:,.0f}", "targets": f"{lv['R2']:,.0f} / {lv['R3']:,.0f}",
         "invalid": f"back below {lv['P']:,.0f}"},
        {"name": "Bearish breakdown", "trigger": f"{unit} below {lv['S1']:,.0f}", "targets": f"{lv['S2']:,.0f} / {lv['S3']:,.0f}",
         "invalid": f"back above {lv['P']:,.0f}"},
        {"name": "Range / no trade", "trigger": f"between {lv['S1']:,.0f} and {lv['R1']:,.0f}",
         "targets": f"pivot {lv['P']:,.0f}", "invalid": "wait for a break of either side"}]
    mt = {"Weekly": trend_tag(wk.Close), "Daily": trend_tag(c)}
    for lab, k in (("1 Hour", "60m"), ("15 Min", "15m"), ("5 Min", "5m")):
        mt[lab] = trend_tag(intr[k].Close) if intr and intr.get(k) is not None else None
    v["mtf"] = [{"tf": k, **x} for k, x in mt.items() if x]
    vb = vix_bucket(vix)
    if slot == "weekly":
        sign = "up" if v["week"]["chg"] > 0 else "dn"
        wt = "up" if cur.Close > wk.Close.rolling(10).mean().iloc[-1] else "dn"
        k, hit = lookup(((rates or {}).get("weekly") or {}).get(key),
                        [f"{sign}|{wt}|{vb}", f"{sign}|{wt}|*", f"{sign}|*|*", "*|*|*"])
        if hit:
            v["setup"] = f"last week {'up' if sign == 'up' else 'down'}, {'above' if wt == 'up' else 'below'} 10-week average, VIX {vb}"
            v["probs"] = [{"label": "Next week closes higher", "p": hit["p_up"], "n": hit["n"]}]
            v["range"] = {"text": "next-week return %: median (5th to 95th percentile)", "med": hit["med"],
                          "lo": hit["p5"], "hi": hit["p95"]}
    else:
        tr = "up" if ref > sma[20] else "dn"
        gb = gap_bucket(gap)
        k, hit = lookup(((rates or {}).get("daily") or {}).get(key),
                        [f"{gb}|{tr}|{vb}", f"{gb}|{tr}|*", f"{gb}|*|*", "*|*|*"])
        if hit:
            ug = k.split("|")[0]
            v["setup"] = (("next session, " if slot in ("post", "pre") else "") + f"{GAP_TEXT[ug]}, "
                          f"{'above' if tr == 'up' else 'below'} 20-DMA, VIX {vb}")
            v["probs"] = [{"label": "Closes above open", "p": hit["p_co"], "n": hit["n"]},
                          {"label": "Closes above prev close", "p": hit["p_cc"], "n": hit["n"]}]
            if ug not in ("flat", "*"):
                v["probs"].append({"label": "Gap gets filled", "p": hit["p_fill"], "n": hit["n"]})
            v["range"] = {"text": "day range %: median (10th to 90th percentile)", "med": hit["rng_med"],
                          "lo": hit["rng_p10"], "hi": hit["rng_p90"]}
    return v


# ---------------- sectors and stocks ----------------
def sector_rows(data):
    rows = []
    for name, t in SECTORS.items():
        d = data.get(t)
        if d is None or len(d) < 60:
            continue
        c = d["Close"]
        rs = rsi(c).iloc[-1]
        w, m = (c.iloc[-1] / c.iloc[-6] - 1) * 100, (c.iloc[-1] / c.iloc[-22] - 1) * 100
        above50 = c.iloc[-1] > c.rolling(50).mean().iloc[-1]
        bias = "Leading" if (w > 0 and rs > 55 and above50) else ("Lagging" if (w < 0 and rs < 45) else "Neutral")
        rows.append({"name": name, "close": r2(c.iloc[-1]), "chg": r2((c.iloc[-1] / c.iloc[-2] - 1) * 100),
                     "w": r2(w), "m": r2(m), "rsi": r2(rs, 0), "bias": bias,
                     "d52": r2((c.iloc[-1] / d["High"].iloc[-252:].max() - 1) * 100, 1)})
    return rows


def stock_rows(data, ath_cache):
    rows = []
    for t, d in data.items():
        if len(d) < 120:
            continue
        c, h, l, vol = d["Close"], d["High"], d["Low"], d["Volume"]
        last, prev = c.iloc[-1], c.iloc[-2]
        val20 = (c * vol).iloc[-21:-1].mean() / 1e7
        if last < 50 or not val20 or val20 < 10:
            continue
        avg10 = vol.iloc[-11:-1].mean()
        hi52 = h.iloc[-252:].max()
        sym = t[:-3]
        ath = max(float(ath_cache.get(sym, 0) or 0), float(h.max()))
        mas = [c.ewm(span=n).mean().iloc[-1] for n in (5, 10, 20, 50, 100, 200)] + \
              [c.rolling(n).mean().iloc[-1] for n in (20, 50, 200)]
        ma = int(sum(1 for m in mas if m == m and last > m))
        rs = rsi(c).iloc[-1]
        wkv = vol.resample("W-FRI").sum()
        wkc = c.resample("W-FRI").last().dropna()
        mo = d.resample("MS").agg({"High": "max", "Close": "last"}).dropna()
        prior12 = mo["High"].iloc[-13:-1].max() if len(mo) >= 8 else np.nan
        mbo = bool(len(mo) >= 8 and mo["Close"].iloc[-1] > prior12 and mo["Close"].iloc[-2] <= prior12)
        rng = h.iloc[-1] - l.iloc[-1]
        q = quality(d, last, prev, rng, rs, mas, val20, hi52)
        tag = ("Overbought" if rs > 75 else "Strong trend" if (ma >= 8 and rs >= 55) else
               "Recovering" if ma >= 5 else "Weak")
        rows.append({"s": sym, "c": r2(last), "chg": r2((last / prev - 1) * 100),
                     "x": r2(vol.iloc[-1] / avg10, 1) if avg10 else None, "cr": r2(last * vol.iloc[-1] / 1e7, 0),
                     "pos": r2((last - l.iloc[-1]) / rng, 2) if rng > 0 else None,
                     "d52": r2((last / hi52 - 1) * 100, 1), "dath": r2((last / ath - 1) * 100, 1),
                     "rsi": r2(rs, 0), "ma": ma, "tag": tag,
                     "w": r2((wkc.iloc[-1] / wkc.iloc[-2] - 1) * 100, 1) if len(wkc) > 2 else None,
                     "m": r2((last / c.iloc[-22] - 1) * 100, 1),
                     "wx": r2(wkv.iloc[-1] / wkv.iloc[-11:-1].mean(), 1) if len(wkv) > 11 and wkv.iloc[-11:-1].mean() else None,
                     "a50": bool(last > mas[7]) if mas[7] == mas[7] else None,
                     "a200": bool(last > mas[8]) if mas[8] == mas[8] else None,
                     "mbo": mbo, "pm": r2((last / prior12 - 1) * 100, 1) if prior12 == prior12 else None,
                     "r63": r2((last / c.iloc[-64] - 1) * 100, 1) if len(c) > 64 else None, **q})
    return rank_quality(rows)


def quality(d, last, prev, rng, rs, mas, val20, hi52):
    """Price-volume quality of today's bar: is the volume doing real work, or is it churn / supply?

    Uses effort-versus-result (range against ATR), close location, upper wick, pocket pivot,
    20-day breakout, trend, up/down volume balance, extension and liquidity. Relative strength
    is added afterwards in rank_quality, once every stock's 3-month return is known.
    """
    o, h, l, c, v = d["Open"], d["High"], d["Low"], d["Close"], d["Volume"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.iloc[-15:-1].mean()
    pos = (last - l.iloc[-1]) / rng if rng > 0 else 0.5
    wick = (h.iloc[-1] - max(o.iloc[-1], last)) / rng if rng > 0 else 0.0
    effort = v.iloc[-1] / v.iloc[-11:-1].mean() if v.iloc[-11:-1].mean() else 0.0
    result = rng / atr if atr else 1.0
    up_day = last > prev
    prior = d.iloc[-11:-1]
    down_vol = prior["Volume"][prior["Close"] < prior["Close"].shift().fillna(prior["Open"])]
    pocket = bool(up_day and (down_vol.empty or v.iloc[-1] > down_vol.max()))
    breakout = bool(last > h.iloc[-21:-1].max())
    ch = c.diff().iloc[-50:]
    vv = v.iloc[-50:]
    dn = vv[ch < 0].sum()
    udvr = vv[ch > 0].sum() / dn if dn else 2.0
    ext = (last - mas[6]) / atr if atr and mas[6] == mas[6] else 0.0
    spikes = int((v.iloc[-10:] > 2 * v.iloc[-60:-10].mean()).sum()) if len(v) > 60 else 0
    gap_fade = bool(o.iloc[-1] > prev * 1.03 and last < o.iloc[-1])
    a50, a200 = last > mas[7], (last > mas[8]) if mas[8] == mas[8] else None
    score, good, bad = 50, [], []

    def add(pts, cond, text, bucket):
        nonlocal score
        if cond:
            score += pts
            bucket.append(text)

    add(12, pos >= 0.75, "strong close", good)
    add(-15, pos < 0.5, "weak close", bad)
    add(-12, wick > 0.4, "long upper wick (supply)", bad)
    add(10, result >= 1.2, "wide range", good)
    add(-12, effort >= 3 and result < 0.8, "churn: big volume, small range", bad)
    add(10, breakout, "20-day breakout", good)
    add(6, hi52 and last >= 0.98 * hi52, "at 52-week high", good)
    add(8, pocket, "pocket pivot", good)
    add(8, bool(a50 and a200), "uptrend", good)
    add(-10, a200 is False, "below 200-DMA", bad)
    add(6, udvr >= 1.3, "accumulation", good)
    add(-6, udvr < 0.8, "distribution in last 50 days", bad)
    add(-10, ext > 3, "overextended", bad)
    add(-6, rs > 80, "RSI above 80", bad)
    add(-8, gap_fade, "gap faded", bad)
    add(-6, val20 < 25, "thin liquidity", bad)
    add(-6, spikes >= 3 and ext > 2, "repeat spikes: possible climax", bad)
    return {"score": score, "good": good, "bad": bad}


def rank_quality(rows):
    """Add relative strength (3-month return percentile in the universe), then grade each stock."""
    vals = sorted(r["r63"] for r in rows if r["r63"] is not None)
    for r in rows:
        if r["r63"] is not None and vals:
            pct = 100 * sum(1 for x in vals if x <= r["r63"]) / len(vals)
            r["rs"] = round(pct)
            if pct >= 80:
                r["score"] += 8
                r["good"].append("relative strength leader")
            elif pct < 30:
                r["score"] -= 6
                r["bad"].append("weak relative strength")
        else:
            r["rs"] = None
        # A caution caps the grade: one caution cannot be an A, two or more cannot be a B.
        cap = 100 if not r["bad"] else 74 if len(r["bad"]) == 1 else 59
        r["score"] = int(max(0, min(cap, r["score"])))
        r["grade"] = "A" if r["score"] >= 75 else "B" if r["score"] >= 60 else "C" if r["score"] >= 45 else "D"
        r["note"] = ("Caution: " + ", ".join(r["bad"][:2])) if r["bad"] else ", ".join(r["good"][:2])
    return rows


def pick(rows, cols):
    return [{k: r.get(k) for k in cols} for r in rows]


def build_lists(slot, rows):
    L, top = {}, 15
    base = ["s", "c", "chg", "x", "cr", "d52", "rsi", "ma", "tag"]
    rated = ["s", "c", "chg", "x", "cr", "rs", "score", "grade", "note"]
    if slot != "weekly":
        sh = [r for r in rows if (r["x"] or 0) >= 3 and (r["chg"] or 0) >= 2 and
              (slot == "intra" or (r["pos"] or 0) >= 0.67)]
        sh = [r for r in rows if (r["x"] or 0) >= 3 and (r["chg"] or 0) >= 2]
        sh.sort(key=lambda r: (-r["score"], -r["x"]))
        L["volume_shockers"] = {"title": "Volume shockers", "rows": pick(sh[:top], rated),
                                "rule": "3x+ volume and up 2%+, ranked by a 0-100 price-volume quality score; cautions flag likely false volume"}
    else:
        ws = [r for r in rows if (r["wx"] or 0) >= 2 and (r["w"] or 0) > 0]
        ws.sort(key=lambda r: (-r["score"], -r["wx"]))
        L["weekly_volume"] = {"title": "Weekly volume shockers",
                              "rule": "2x+ weekly volume in a positive week, ranked by the 0-100 price-volume quality score",
                              "rows": [dict(p, x=r["wx"], chg=r["w"]) for p, r in zip(pick(ws[:top], rated), ws[:top])]}
    if slot in ("post", "weekly", "pre"):
        h52 = [r for r in rows if r["d52"] is not None and r["d52"] >= -5 and r["a50"] and r["a200"]]
        h52.sort(key=lambda r: (-r["d52"], -(r["cr"] or 0)))
        L["high52_zone"] = {"title": "52-week-high zone", "rows": pick(h52[:top], base),
                            "rule": "within 5% of the 52-week high and above the 50 and 200-day averages"}
        ath = [r for r in rows if r["dath"] is not None and r["dath"] >= -5]
        ath.sort(key=lambda r: (-r["dath"], -(r["cr"] or 0)))
        L["ath_zone"] = {"title": "All-time-high zone", "rule": "within 5% of the all-time high",
                         "rows": pick(ath[:top], ["s", "c", "chg", "x", "cr", "dath", "rsi", "ma", "tag"])}
    if slot == "weekly":
        mb = [r for r in rows if r["mbo"]]
        mb.sort(key=lambda r: -(r["pm"] or 0))
        L["monthly_bo"] = {"title": "Monthly breakout stocks", "rows": pick(mb[:top], ["s", "c", "m", "pm", "cr", "rsi", "ma", "tag"]),
                           "rule": "this month's close is above the highest high of the prior 12 months (in progress until month end)"}
    return L


def breadth(rows):
    n = len(rows)
    if not n:
        return None
    adv = sum(1 for r in rows if (r["chg"] or 0) > 0)
    liq = [r for r in rows if (r["cr"] or 0) >= 50]
    liq.sort(key=lambda r: -(r["chg"] or 0))
    return {"n": n, "adv": adv, "dec": n - adv,
            "a50": r2(100 * sum(1 for r in rows if r["a50"]) / n, 0), "a200": r2(100 * sum(1 for r in rows if r["a200"]) / n, 0),
            "nh": sum(1 for r in rows if (r["d52"] or -9) >= -0.5), "near_low": sum(1 for r in rows if r["rsi"] is not None and r["rsi"] < 30),
            "gainers": pick(liq[:5], ["s", "c", "chg"]), "losers": pick(liq[-5:][::-1], ["s", "c", "chg"])}


def industry_rows(rows, indmap):
    groups = {}
    for r in rows:
        k = indmap.get(r["s"])
        if k and k != "nan":
            groups.setdefault(k, []).append(r)
    out = []
    for k, rs in groups.items():
        if len(rs) < 4:
            continue

        def avg(f):
            vals = [r[f] for r in rs if r[f] is not None]
            return round(float(np.mean(vals)), 1) if vals else 0.0

        ws = sorted(rs, key=lambda r: -(r["w"] or 0))
        a50 = 100 * sum(1 for r in rs if r["a50"]) / len(rs)
        w, rs_avg = avg("w"), avg("rsi")
        out.append({"name": k, "n": len(rs), "chg": avg("chg"), "w": w, "m": avg("m"), "rsi": round(rs_avg), "a50": round(a50),
                    "best": ws[0]["s"], "best_w": ws[0]["w"], "worst": ws[-1]["s"], "worst_w": ws[-1]["w"],
                    "bias": "Leading" if (w > 0 and a50 >= 60 and rs_avg > 55) else ("Lagging" if (w < 0 and a50 < 40) else "Neutral")})
    return sorted(out, key=lambda z: -z["w"])


# ---------------- rendering ----------------
BG, CARD, TXT, MUT = "#dbe3f0", "#ffffff", "#162033", "#5a687f"
UP, DN, ACC, AMB = "#0b8f61", "#cf3f38", "#2f5fd0", "#b37405"
EDGE, TRACK, HILITE, BAND = "#c4cfe1", "#d9e0ec", "#ffe7a8", "#1b3260"
# One colour per data segment: the pill names the segment, the tint fills its card.
SEG = {"levels": ("#2f5fd0", "#ffffff"), "scen": ("#0f8a8a", "#f4f8fb"), "odds": ("#6b4fc9", "#efeafc"),
       "chart": ("#1b3260", "#ffffff"), "mtf": ("#1f7fb8", "#e4f0fb"), "glob": ("#b0680f", "#fcf1df"),
       "vix": ("#4a55c7", "#e8ecfd"), "breadth": ("#0b8f61", "#e6f5ee"), "sector": ("#2f5fd0", "#ffffff"),
       "shock": ("#c2410c", "#fff6ef"), "high52": ("#0b8f61", "#f2faf6"), "ath": ("#6b4fc9", "#f5f2fd"),
       "mbo": ("#1f7fb8", "#eef6fc")}
HEAD = {"pre": "PRE-MARKET", "intra": "INTRADAY", "post": "POST-MARKET REVIEW", "weekly": "WEEKLY REVIEW"}
SUB_INDEX = {"pre": "INDEX PLAN FOR TODAY", "intra": "INDEX OUTLOOK", "post": "INDEX PLAN FOR NEXT SESSION",
             "weekly": "INDEX NEXT-WEEK PLAN"}
SUB_STOCKS = {"pre": "STOCKS TO WATCH TODAY", "intra": "EARLY VOLUME SHOCKERS", "post": "STOCKS TO WATCH",
              "weekly": "STOCKS FOR NEXT WEEK"}


class Page:
    def __init__(self, H, view, sub):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        self.plt, self.H = plt, H
        self.fig = plt.figure(figsize=(10.8, 10.8 * H / 100), dpi=110, facecolor=BG)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, 100)
        self.ax.set_ylim(H, 0)
        self.ax.axis("off")
        self.rect(0, 0, 100, 7.2, BAND)
        self.rect(0, 7.2, 100, 0.18, "#f2b233")
        self.t(4, 3.7, "MADDY DESK", 21, "#ffffff", "bold")
        self.t(96, 2.9, f"{HEAD[view['slot']]}  |  {sub}", 12.5, "#ffffff", "bold", "right")
        self.t(96, 5.5, f"{view['date']}   {view['time_ist']} IST", 9.5, "#c5d3ee", ha="right")
        self.t(4, H - 1.5, "Source: Yahoo Finance (index quotes can lag ~15 min). Probabilities are historical "
                           "frequencies, not forecasts. Not investment advice.", 7.5, MUT)

    def card(self, x, y, w, h, fc=CARD, ec=EDGE):
        from matplotlib.patches import FancyBboxPatch
        self.ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.9", fc=fc, ec=ec, lw=1))

    def pill(self, x, y, text, seg):
        """Segment label: a filled rounded tag in the segment's colour."""
        from matplotlib.patches import FancyBboxPatch
        w = len(text) * 0.74 + 2.2
        self.ax.add_patch(FancyBboxPatch((x, y - 1.05), w, 2.1, boxstyle="round,pad=0,rounding_size=0.7", fc=SEG[seg][0], ec="none"))
        self.t(x + 1.1, y, text, 8.6, "#ffffff", "bold")

    def rect(self, x, y, w, h, fc):
        from matplotlib.patches import Rectangle
        self.ax.add_patch(Rectangle((x, y), w, h, fc=fc, ec="none"))

    def t(self, x, y, s, size=10, color=TXT, weight="normal", ha="left"):
        self.ax.text(x, y, str(s), fontsize=size, color=color, fontweight=weight, ha=ha, va="center")


def col(v):
    return TXT if v is None else (UP if v >= 0 else DN)


def pc(v, d=2):
    return "-" if v is None else f"{v:+.{d}f}%"


def n0(v):
    return "-" if v is None else f"{v:,.0f}"


def tagcol(s):
    return UP if s in ("Bullish", "Leading") else DN if s in ("Bearish", "Lagging", "Bearish divergence") else AMB


def page_index(view):
    idx = view["indices"]
    main = idx[:2]
    lad_n = max(len(i["ladder"]) for i in main)
    H = 64 + lad_n * 1.9 + 30 + 4
    p = Page(H, view, SUB_INDEX[view["slot"]])
    n = len(idx)
    w = (92 - 2 * (n - 1)) / n
    for k, iv in enumerate(idx):
        x = 4 + k * (w + 2)
        p.card(x, 8, w, 11.5)
        p.rect(x + 0.9, 8, w - 1.8, 0.45, tagcol(iv["bias"]))
        p.t(x + 1.5, 10.3, iv["name"], 10.5, MUT, "bold")
        p.t(x + w - 1.5, 10.3, iv["bias"], 10, tagcol(iv["bias"]), "bold", "right")
        p.t(x + 1.5, 14, f"{iv['close']:,.2f}", 18.5, TXT, "bold")
        p.t(x + w - 1.5, 14, pc(iv["chg"]), 12, col(iv["chg"]), "bold", "right")
        p.t(x + 1.5, 16.8, f"RSI {iv['rsi']} | 1W {pc(iv['w'], 1)} | 1M {pc(iv['m'], 1)}", 7.2, MUT)
        p.t(x + 1.5, 18.4, f"52w {n0(iv['lo52'])}", 6.8, MUT)
        p.t(x + w - 1.5, 18.4, n0(iv["hi52"]), 6.8, MUT, ha="right")
        bx, bw = x + 10.5, w - 20
        p.rect(bx, 18.2, bw, 0.45, TRACK)
        frac = (iv["close"] - iv["lo52"]) / max(iv["hi52"] - iv["lo52"], 1e-9)
        p.rect(bx + bw * min(max(frac, 0), 1) - 0.25, 17.85, 0.5, 1.15, ACC)
    vx, s = view["vix"], view["sentiment"]
    p.card(4, 21, 92, 6, fc=SEG["vix"][1])
    p.t(5.5, 23, "INDIA VIX", 8.5, MUT, "bold")
    p.t(5.5, 25.3, f"{vx['close']}" if vx["close"] else "-", 13, TXT, "bold")
    p.t(15, 25.3, pc(vx["chg"], 1), 10, col(-(vx["chg"] or 0)))
    p.t(24, 23, "MARKET + VIX READ", 8.5, MUT, "bold")
    p.t(24, 25.3, s[0], 12, tagcol(s[0]) if s[0] != "Possible reversal" else AMB, "bold")
    p.t(44, 25.3, s[1], 8.8, TXT)
    y0 = 29
    for k, iv in enumerate(main):
        x = 4 + k * 47
        hh = 4 + lad_n * 1.9
        p.card(x, y0, 45, hh, fc=SEG["levels"][1])
        p.pill(x + 1.3, y0 + 2, f"{iv['name']} KEY LEVELS", "levels")
        if iv.get("gap"):
            p.t(x + 43.5, y0 + 2, f"Gap {pc(iv['gap']['pct'])}", 9, col(iv["gap"]["pct"]), "bold", "right")
        for j, r in enumerate(iv["ladder"]):
            yy = y0 + 4.6 + j * 1.9
            last = r["k"] == "LAST"
            c = AMB if last else (DN if r["v"] > iv["close"] else UP)
            if last:
                p.rect(x + 1, yy - 0.9, 43, 1.8, HILITE)
            p.t(x + 2, yy, r["k"], 8.8, c, "bold" if last or r["k"][0] in "RSP" and len(r["k"]) <= 2 else "normal")
            p.t(x + 30, yy, f"{r['v']:,.2f}", 9, TXT if not last else AMB, "bold", "right")
            p.t(x + 43.5, yy, pc((r["v"] / iv["close"] - 1) * 100), 8.2, MUT, ha="right")
        y1 = y0 + hh + 1.5
        p.card(x, y1, 45, 15, fc=SEG["scen"][1])
        p.pill(x + 1.3, y1 + 2, "SCENARIOS", "scen")
        for j, tint in enumerate(("#dff3e9", "#fbe3e1", "#fdf0cf")):
            p.rect(x + 1, y1 + 3.5 + j * 3.6, 43, 3.3, tint)
        for j, sc in enumerate(iv["scenarios"]):
            yy = y1 + 4.6 + j * 3.6
            p.t(x + 2, yy, sc["name"], 9, (UP, DN, AMB)[j], "bold")
            p.t(x + 16, yy, sc["trigger"], 8.6, TXT)
            p.t(x + 2, yy + 1.7, f"targets {sc['targets']}   |   invalid: {sc['invalid']}", 8, MUT)
        y2 = y1 + 16.5
        p.card(x, y2, 45, 13.5, fc=SEG["odds"][1])
        p.pill(x + 1.3, y2 + 2, "HISTORICAL ODDS", "odds")
        if iv["probs"]:
            p.t(x + 1.5, y2 + 4.2, iv["setup"], 7.8, MUT)
            for j, pr in enumerate(iv["probs"]):
                yy = y2 + 6.6 + j * 2.2
                p.t(x + 2, yy, pr["label"], 8.8)
                p.rect(x + 21, yy - 0.6, 10, 1.2, TRACK)
                p.rect(x + 21, yy - 0.6, 10 * pr["p"] / 100, 1.2, UP if pr["p"] >= 50 else DN)
                p.t(x + 43.5, yy, f"{pr['p']:.0f}%   n={pr['n']}", 8.8, TXT, "bold", "right")
            if iv.get("range"):
                g = iv["range"]
                p.t(x + 1.5, y2 + 12.3, f"{g['text']}: {g['med']} ({g['lo']} to {g['hi']})", 7.6, MUT)
        else:
            p.t(x + 2, y2 + 6, "Probability table not built yet", 8.8, MUT)
        if iv.get("exp"):
            e = iv["exp"]
            p.t(x + 43.5, y2 + 2, f"VIX 1-sigma {e['horizon']}: {n0(e['lo'])}-{n0(e['hi'])}", 8, MUT, ha="right")
    y3 = y0 + 4 + lad_n * 1.9 + 33
    p.card(4, y3, 45, 26, fc=SEG["chart"][1])
    p.pill(5.3, y3 + 2, f"{main[0]['name']} LAST 40 SESSIONS", "chart")
    cs = main[0]["candles"]
    lo, hi = min(c[3] for c in cs), max(c[2] for c in cs)
    fy = lambda v: y3 + 24.5 - (v - lo) / (hi - lo + 1e-9) * 20
    for nm, colr in (("R1", DN), ("P", MUT), ("S1", UP)):
        lvl = main[0]["levels"][nm]
        if lo <= lvl <= hi:
            p.ax.plot([6, 47.5], [fy(lvl)] * 2, color=colr, lw=0.6, ls="--")
            p.t(47.6, fy(lvl) - 0.7, nm, 7, colr, ha="right")
    for j, (_, o, h, l, c) in enumerate(cs):
        cx = 6.5 + j * (40.5 / len(cs))
        cc = UP if c >= o else DN
        p.ax.plot([cx + 0.35, cx + 0.35], [fy(h), fy(l)], color=cc, lw=0.7)
        p.rect(cx, min(fy(o), fy(c)), 0.7, max(abs(fy(o) - fy(c)), 0.12), cc)
    p.card(51, y3, 45, 12, fc=SEG["mtf"][1])
    p.pill(52.3, y3 + 2, "MULTI-TIMEFRAME", "mtf")
    for k, iv in enumerate(main):
        p.t(72 + k * 12, y3 + 2, iv["name"], 8, MUT, "bold")
    tfs = [m["tf"] for m in main[0]["mtf"]]
    for j, tf in enumerate(tfs):
        yy = y3 + 4.3 + j * 1.5
        p.t(52.5, yy, tf, 8.5, MUT)
        for k, iv in enumerate(main):
            m = next((z for z in iv["mtf"] if z["tf"] == tf), None)
            if m:
                p.t(72 + k * 12, yy, f"{m['tag']}  ({m['rsi']:.0f})", 8.5, tagcol(m["tag"]), "bold")
    p.card(51, y3 + 13.5, 45, 12.5, fc=SEG["glob"][1])
    p.pill(52.3, y3 + 15.5, "GLOBAL CUES", "glob")
    for j, g in enumerate(view["globals"][:10]):
        gx, gy = 52.5 + (j % 2) * 22, y3 + 17.8 + (j // 2) * 1.75
        p.t(gx, gy, g["name"], 8.3, MUT)
        p.t(gx + 14.5, gy, f"{g['close']:,.2f}", 8.3, TXT, "bold", "right")
        p.t(gx + 20, gy, pc(g["chg"], 1), 8.3, col(g["chg"]), ha="right")
    return p


def page_market(view):
    sec, b = view["sectors"], view.get("breadth")
    rows = (len(sec) + 3) // 4
    H = 10 + rows * 9.5 + 26
    p = Page(H, view, "SECTORS AND BREADTH" + (" THIS WEEK" if view["slot"] == "weekly" else ""))
    key = "w" if view["slot"] == "weekly" else "chg"
    sec = sorted(sec, key=lambda s: -(s[key] or 0))
    for j, s in enumerate(sec):
        x, y = 4 + (j % 4) * 23.25, 8 + (j // 4) * 9.5
        v = s[key] or 0
        a = min(abs(v) / 3, 1)
        base = (0x2e, 0xcc, 0x8f) if v >= 0 else (0xff, 0x6b, 0x6b)
        base = (0x0d, 0x9b, 0x6a) if v >= 0 else (0xd8, 0x48, 0x3f)
        bg = "#%02x%02x%02x" % tuple(int(255 + (c - 255) * (0.10 + 0.38 * a)) for c in base)
        p.card(x, y, 22.25, 8.5, fc=bg)
        p.t(x + 1.2, y + 1.9, s["name"], 9.5, TXT, "bold")
        p.t(x + 21, y + 1.9, s["bias"], 8, tagcol(s["bias"]), "bold", "right")
        p.t(x + 1.2, y + 4.6, pc(s[key]), 13, TXT, "bold")
        p.t(x + 21, y + 4.6, n0(s["close"]), 9, TXT, ha="right")
        p.t(x + 1.2, y + 7.1, f"1D {pc(s['chg'], 1)}  1W {pc(s['w'], 1)}  1M {pc(s['m'], 1)}  RSI {s['rsi']:.0f}", 6.4, TXT)
    y = 8 + rows * 9.5 + 1
    if b:
        p.card(4, y, 45, 21, fc=SEG["breadth"][1])
        p.pill(5.3, y + 2, "MARKET BREADTH", "breadth")
        p.t(47.5, y + 2, f"{b['n']} liquid stocks, {view['universe']}", 7.6, MUT, ha="right")
        tot = b["adv"] + b["dec"]
        p.rect(5.5, y + 4.2, 42 * b["adv"] / tot, 1.6, UP)
        p.rect(5.5 + 42 * b["adv"] / tot, y + 4.2, 42 * b["dec"] / tot, 1.6, DN)
        p.t(5.5, y + 7.3, f"Advances {b['adv']}", 9.5, UP, "bold")
        p.t(47.5, y + 7.3, f"Declines {b['dec']}", 9.5, DN, "bold", "right")
        for j, (lab, val) in enumerate((("Above 50-day average", f"{b['a50']:.0f}%"), ("Above 200-day average", f"{b['a200']:.0f}%"),
                                        ("At or within 0.5% of 52-week high", b["nh"]), ("RSI below 30 (oversold)", b["near_low"]))):
            p.t(5.5, y + 10.4 + j * 2.4, lab, 9, MUT)
            p.t(47.5, y + 10.4 + j * 2.4, val, 9.5, TXT, "bold", "right")
        for k, (ttl, key2, cc) in enumerate((("TOP GAINERS", "gainers", UP), ("TOP LOSERS", "losers", DN))):
            x = 51 + k * 23.25
            p.card(x, y, 21.75, 21, fc="#e6f5ee" if k == 0 else "#fbe7e5")
            p.t(x + 1.2, y + 2, ttl, 9.5, cc, "bold")
            for j, r in enumerate(b[key2]):
                p.t(x + 1.2, y + 5.2 + j * 3, r["s"][:11], 9, TXT, "bold")
                p.t(x + 20.6, y + 5.2 + j * 3, pc(r["chg"], 1), 9, col(r["chg"]), "bold", "right")
                p.t(x + 1.2, y + 6.6 + j * 3, f"{r['c']:,.1f}", 7.6, MUT)
    return p


def page_industries(view):
    rows = view["industries"][:24]
    H = 15 + 2.3 * len(rows) + 5
    p = Page(H, view, "SECTOR ANALYSIS BY INDUSTRY")
    p.card(4, 8, 92, 7 + 2.3 * len(rows))
    p.pill(5.3, 10, "RANKED BY 1-WEEK CHANGE", "sector")
    p.t(94.5, 10, "Nifty 500 stocks, equal-weight averages, ranked by 1-week change", 7.6, MUT, ha="right")
    for lab, x, ha in (("Industry", 5.5, "left"), ("Stocks", 38, "right"), ("1D", 44.5, "right"), ("1W", 51, "right"),
                       ("1M", 57.5, "right"), ("RSI", 62, "right"), (">50DMA", 69, "right"), ("Best of week", 71, "left"),
                       ("Worst of week", 81.5, "left"), ("View", 94.5, "right")):
        p.t(x, 12.4, lab, 7.6, MUT, ha=ha)
    for j, r in enumerate(rows):
        y = 14.7 + j * 2.3
        p.t(5.5, y, r["name"][:32], 8.2, TXT, "bold")
        p.t(38, y, r["n"], 8.2, MUT, ha="right")
        for k, x in (("chg", 44.5), ("w", 51), ("m", 57.5)):
            p.t(x, y, pc(r[k], 1), 8.4, col(r[k]), "bold" if k == "w" else "normal", "right")
        p.t(62, y, r["rsi"], 8.2, TXT, ha="right")
        p.t(69, y, f"{r['a50']}%", 8.2, UP if r["a50"] >= 60 else DN if r["a50"] < 40 else AMB, ha="right")
        p.t(71, y, f"{r['best'][:9]} {pc(r['best_w'], 0)}", 7.4, UP)
        p.t(81.5, y, f"{r['worst'][:9]} {pc(r['worst_w'], 0)}", 7.4, DN)
        p.t(94.5, y, r["bias"], 7.8, tagcol(r["bias"]), "bold", "right")
    return p


LCOL = {"s": ("Stock", 0), "c": ("Close", 22), "chg": ("Chg%", 31), "m": ("1M%", 31), "x": ("Vol x", 39), "pm": ("vs 12M high", 42),
        "cr": ("Rs cr", 48), "d52": ("vs 52wH", 58), "dath": ("vs ATH", 58), "rsi": ("RSI", 65), "ma": ("MAs", 72), "tag": ("Read", 92),
        "rs": ("RS", 53), "score": ("Score", 63), "grade": ("", 66), "note": ("What the volume says", 92)}
GRADE_COL = {"A": UP, "B": ACC, "C": AMB, "D": DN}


def page_stocks(view):
    lists = [l for l in view["lists"].values() if l["rows"]]
    if not lists:
        return None
    H = 9 + sum(6 + 2.1 * min(12, len(l["rows"])) + 1.5 for l in lists) + 3
    p = Page(H, view, SUB_STOCKS[view["slot"]])
    y = 8
    for l in lists:
        rows = l["rows"][:12]
        hh = 6 + 2.1 * len(rows)
        seg = ("shock" if "shockers" in l["title"].lower() else "high52" if "52" in l["title"] else
               "ath" if "All-time" in l["title"] else "mbo")
        p.card(4, y, 92, hh, fc=SEG[seg][1])
        p.pill(5.3, y + 1.9, l["title"].upper(), seg)
        p.t(94.5, y + 1.9, l["rule"], 7.6, MUT, ha="right")
        cols = [c for c in LCOL if c in rows[0]]
        for c in cols:
            lab, off = LCOL[c]
            p.t(5.5 + off * 0.96, y + 4.2, lab, 7.8, MUT, ha="left" if c == "s" else "right")
        for j, r in enumerate(rows):
            yy = y + 6.3 + j * 2.1
            for c in cols:
                off, v = LCOL[c][1], r.get(c)
                if c == "s":
                    p.t(5.5, yy, v, 9, TXT, "bold")
                elif c == "tag":
                    p.t(5.5 + off * 0.96, yy, v, 8.5, UP if v == "Strong trend" else AMB if v in ("Overbought", "Recovering") else DN, "bold", "right")
                elif c == "ma":
                    p.t(5.5 + off * 0.96, yy, f"{v}/9", 8.8, UP if v >= 7 else AMB if v >= 5 else DN, ha="right")
                elif c in ("chg", "m", "pm", "d52", "dath"):
                    p.t(5.5 + off * 0.96, yy, pc(v, 1), 8.8, col(v) if c in ("chg", "m", "pm") else MUT, ha="right")
                elif c == "score":
                    p.rect(5.5 + (off - 8) * 0.96, yy - 0.45, 5, 0.9, TRACK)
                    p.rect(5.5 + (off - 8) * 0.96, yy - 0.45, 5 * v / 100, 0.9, GRADE_COL[r["grade"]])
                    p.t(5.5 + off * 0.96, yy, v, 8.8, TXT, "bold", "right")
                elif c == "grade":
                    p.t(5.5 + off * 0.96, yy, v, 9.5, GRADE_COL[v], "bold", "right")
                elif c == "note":
                    parts = str(v).split(", ")
                    short = parts[0] + (f"  +{len(parts) - 1} more" if len(parts) > 1 and str(v).startswith("Caution") else "")
                    p.t(5.5 + off * 0.96, yy, short[:46], 7.6, DN if str(v).startswith("Caution") else UP, ha="right")
                elif c == "x":
                    p.t(5.5 + off * 0.96, yy, f"{v}x" if v is not None else "-", 8.8, AMB, "bold", "right")
                else:
                    p.t(5.5 + off * 0.96, yy, "-" if v is None else f"{v:,.1f}" if c == "c" else f"{v:,.0f}", 8.8, TXT, ha="right")
        y += hh + 1.5
    return p


def render(view, slot):
    from matplotlib.backends.backend_pdf import PdfPages
    os.makedirs(IMG, exist_ok=True)
    pages = [page_index(view)]
    if view["sectors"]:
        pages.append(page_market(view))
    if view.get("industries"):
        pages.append(page_industries(view))
    ps = page_stocks(view)
    if ps:
        pages.append(ps)
    paths = []
    with PdfPages(os.path.join(IMG, f"{slot}.pdf")) as pdf:
        for i, pg in enumerate(pages, 1):
            path = os.path.join(IMG, f"{slot}_{i}.png")
            pg.fig.savefig(path, facecolor=BG)
            pdf.savefig(pg.fig, facecolor=BG)
            pg.plt.close(pg.fig)
            paths.append(path)
    return paths


# ---------------- telegram ----------------
def caption(view, url):
    out = [f"<b>MADDY DESK | {HEAD[view['slot']]}</b>  {view['date']} {view['time_ist']}"]
    for iv in view["indices"][:2]:
        s = f"<b>{iv['name']}</b> {iv['close']:,.0f} ({iv['chg']:+.2f}%) {iv['bias']}"
        if iv.get("gap"):
            s += f" | gap {iv['gap']['pct']:+.2f}%"
        s += f" | R1 {iv['levels']['R1']:,.0f} S1 {iv['levels']['S1']:,.0f}"
        if iv["probs"]:
            s += f" | {iv['probs'][0]['label'].lower()} {iv['probs'][0]['p']:.0f}% (n={iv['probs'][0]['n']})"
        out.append(s)
    out.append(f"VIX {view['vix']['close']} | {view['sentiment'][0]}")
    for l in view["lists"].values():
        if l["rows"]:
            out.append(f"<b>{l['title']}:</b> " + ", ".join(r["s"] for r in l["rows"][:8]))
    if url:
        out.append(f'<a href="{url}">Open dashboard</a>')
    txt = "\n".join(out)
    return txt if len(txt) <= 1000 else txt[:990] + "..."


def telegram(view, paths, pdf, url):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("telegram: skipped (secrets not set)")
        return False
    import requests
    base, cap = f"https://api.telegram.org/bot{token}/", caption(view, url)
    try:
        files = {f"p{i}": open(p, "rb") for i, p in enumerate(paths)}
        media = [{"type": "photo", "media": f"attach://p{i}"} for i in range(len(paths))]
        media[0].update({"caption": cap, "parse_mode": "HTML"})
        r = requests.post(base + "sendMediaGroup", data={"chat_id": chat, "media": json.dumps(media)}, files=files, timeout=90)
        if not r.ok:
            print("telegram: album failed", r.status_code, "- sending text")
            r = requests.post(base + "sendMessage", data={"chat_id": chat, "text": cap, "parse_mode": "HTML",
                                                          "disable_web_page_preview": "true"}, timeout=60)
        if view["slot"] == "weekly":
            with open(pdf, "rb") as fh:
                requests.post(base + "sendDocument", data={"chat_id": chat}, files={"document": ("nse-desk-weekly.pdf", fh)}, timeout=90)
        print("telegram:", "sent" if r.ok else f"failed {r.status_code}")
        return bool(r.ok)
    except Exception as exc:
        print("telegram: error", type(exc).__name__)
        return False


# ---------------- main ----------------
def main():
    slot, fake = sys.argv[1], "--synthetic" in sys.argv
    now, today = now_ist(), now_ist().date()
    cfg = load(os.path.join(ROOT, "config", "holidays.json"), {"holidays": []})
    if str(today) in cfg.get("holidays", []) and "--force" not in sys.argv:
        print("holiday: nothing published")
        return
    if os.environ.get("TRIGGER") in ("schedule", "push") and slot in SLOT_WINDOW:
        lo, hi = SLOT_WINDOW[slot]
        if not lo <= now.hour * 60 + now.minute <= hi:
            print(f"{slot} timer started late at {now:%H:%M} IST, outside its window: nothing published")
            return
    src = Source(fake)
    rates = load(os.path.join(ROOT, "data", "base_rates.json"), {})
    state = load(os.path.join(OUT, "state.json"), {})
    # Each slot has a backup timer; the second run of a day stops here once the first has posted.
    if state.get("sent", {}).get(slot) == str(today) and "--force" not in sys.argv and not fake:
        print(f"{slot} already posted today: nothing to do")
        return
    errors = []
    itk = [v[0] for v in INDEX.values()]
    daily = src.many(itk + [VIX] + list(GLOBAL.values()) + list(SECTORS.values()), "2y", "1d")
    if "^NSEI" not in daily:
        print("no index data from the source; keeping the last published dashboard")
        sys.exit(0 if "--force" not in sys.argv else 1)
    intr = {}
    if slot != "weekly":
        for iv, per in (("5m", "5d"), ("15m", "5d"), ("60m", "1mo")):
            got = src.many(itk[:2], per, iv)
            for t in itk[:2]:
                intr.setdefault(t, {})[iv] = got.get(t)
    vd = daily.get(VIX)
    vix = float(vd.Close.iloc[-1]) if vd is not None else None
    vixchg = float((vd.Close.iloc[-1] / vd.Close.iloc[-2] - 1) * 100) if vd is not None and len(vd) > 1 else None
    if vix is None:
        errors.append("India VIX unavailable")
    indices = [index_view(slot, k, nm, daily[t], intr.get(t), vix, vixchg, rates, today)
               for k, (t, nm) in INDEX.items() if t in daily and len(daily[t]) > 210]
    stale = any(i["stale"] for i in indices[:1])
    if stale and "--force" not in sys.argv and not fake:
        print(f"no bar for {today} yet (holiday or delayed data): nothing published")
        return
    syms, uni, indmap = universe(fake)
    lists, br, inds = {}, None, []
    if slot != "pre" or True:
        sd = src.many([s + ".NS" for s in syms], "2y", "1d")
        ath_path = os.path.join(ROOT, "data", "ath.json")
        ath = load(ath_path, {})
        if slot == "weekly" or not ath:
            mx = src.many([s + ".NS" for s in syms], "max", "1mo")
            for t, d in mx.items():
                ath[t[:-3]] = r2(max(float(d["High"].max()), float(ath.get(t[:-3], 0) or 0)))
            if mx:
                save(ath_path, ath)
        rows = stock_rows(sd, ath)
        if not rows:
            errors.append("stock data unavailable")
        if len(sd) < 0.8 * len(syms):
            errors.append(f"stock data for {len(sd)} of {len(syms)} symbols")
        lists, br, inds = build_lists(slot, rows), breadth(rows), industry_rows(rows, indmap)
    glob = []
    for nm, t in GLOBAL.items():
        d = daily.get(t)
        if d is not None and len(d) > 1:
            glob.append({"name": nm, "close": r2(d.Close.iloc[-1]), "chg": r2((d.Close.iloc[-1] / d.Close.iloc[-2] - 1) * 100),
                         "date": str(d.index[-1].date())})
    missing = [n for n, t in SECTORS.items() if t not in daily]
    if missing:
        print("sector tickers without data:", ", ".join(missing))
    view = {"slot": slot, "date": str(today), "time_ist": now.strftime("%H:%M"), "synthetic": fake,
            "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "rates_built": (rates or {}).get("built"), "universe": uni, "errors": errors,
            "vix": {"close": r2(vix), "chg": r2(vixchg), "regime": vix_bucket(vix)},
            "sentiment": list(sentiment(indices[0]["chg"], vixchg)),
            "indices": indices, "globals": glob, "sectors": sector_rows(daily), "breadth": br, "industries": inds, "lists": lists}
    digest = hashlib.sha1(json.dumps([[i["close"], i["high"], i["low"]] for i in indices]).encode()).hexdigest()
    dup = state.get("hash", {}).get(slot) == digest
    save(os.path.join(OUT, f"{slot}.json"), view)
    save(os.path.join(OUT, "history", f"{view['date']}_{slot}.json"), view)
    state["dates"] = sorted(set(state.get("dates", []) + [view["date"]]))[-40:]
    state.setdefault("hash", {})[slot] = digest
    paths = render(view, slot)
    if "--no-telegram" in sys.argv:
        print("telegram: disabled by flag")
    elif dup and "--force" not in sys.argv:
        print("telegram: skipped (index values unchanged since the last run)")
    elif telegram(view, paths, os.path.join(IMG, f"{slot}.pdf"), os.environ.get("DASHBOARD_URL", "")):
        state.setdefault("sent", {})[slot] = view["date"]
    save(os.path.join(OUT, "state.json"), state)
    print(f"published {slot} {view['date']} {view['time_ist']}: {len(indices)} indices, {len(view['sectors'])} sectors, "
          f"{br['n'] if br else 0} stocks, " + ", ".join(f"{k}={len(v['rows'])}" for k, v in lists.items())
          + (f" | errors: {errors}" if errors else ""))


if __name__ == "__main__":
    main()
