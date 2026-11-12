#!/usr/bin/env python3
"""NSE Desk: fetch free market data, analyse it, draw posters, post to Telegram. No AI.

Usage: python scripts/desk.py <pre|intra|post|weekly> [--synthetic] [--force] [--no-telegram]
"""
import datetime as dt
import json
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import base_rates as br  # noqa: E402
import publish as pb  # noqa: E402

ROOT, OUT, IMG = pb.ROOT, pb.OUT, pb.IMG
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
TITLE = {"pre": "PRE-MARKET PLAN", "intra": "INTRADAY OUTLOOK", "post": "POST-MARKET REVIEW", "weekly": "WEEKLY OUTLOOK"}
INDICES = {"NIFTY": ("^NSEI", "NIFTY 50"), "BANKNIFTY": ("^NSEBANK", "BANK NIFTY"), "SENSEX": ("^BSESN", "SENSEX")}
VIX = "^INDIAVIX"
GLOBALS = {"S&P 500": "^GSPC", "Nasdaq": "^IXIC", "Dow": "^DJI", "Nikkei": "^N225", "Hang Seng": "^HSI",
           "USD/INR": "INR=X", "Brent": "BZ=F", "DXY": "DX-Y.NYB", "US 10Y": "^TNX", "Gold": "GC=F"}
SECTORS = {"IT": "^CNXIT", "Auto": "^CNXAUTO", "Pharma": "^CNXPHARMA", "FMCG": "^CNXFMCG", "Metal": "^CNXMETAL",
           "Realty": "^CNXREALTY", "Energy": "^CNXENERGY", "PSU Bank": "^CNXPSUBANK", "Media": "^CNXMEDIA",
           "Infra": "^CNXINFRA", "Fin Services": "NIFTY_FIN_SERVICE.NS", "Pvt Bank": "NIFTY_PVT_BANK.NS",
           "Healthcare": "NIFTY_HEALTHCARE.NS", "Cons Durables": "NIFTY_CONSR_DURBL.NS", "Oil & Gas": "NIFTY_OIL_AND_GAS.NS",
           "PSE": "^CNXPSE", "Midcap 100": "NIFTY_MIDCAP_100.NS", "Smallcap 100": "^CNXSC", "Next 50": "^NSMIDCP"}
N50 = ("RELIANCE TCS HDFCBANK ICICIBANK INFY BHARTIARTL SBIN ITC LT HINDUNILVR KOTAKBANK AXISBANK BAJFINANCE MARUTI "
       "SUNPHARMA HCLTECH M&M TITAN NTPC ULTRACEMCO ONGC POWERGRID ADANIENT ADANIPORTS ASIANPAINT BAJAJFINSV BAJAJ-AUTO "
       "COALINDIA WIPRO NESTLEIND JSWSTEEL TATASTEEL GRASIM TECHM HINDALCO DRREDDY CIPLA EICHERMOT APOLLOHOSP BEL TRENT "
       "SHRIRAMFIN SBILIFE HDFCLIFE TATACONSUM HEROMOTOCO INDUSINDBK BRITANNIA DIVISLAB JIOFIN").split()
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
      "Accept": "application/json,text/html,*/*", "Accept-Language": "en-US,en;q=0.9"}
r2, num = pb.r2, pb.num


# ---------------- fetch ----------------
def dl(tickers, **kw):
    import yfinance as yf
    out = {}
    try:
        df = yf.download(list(tickers), auto_adjust=False, progress=False, group_by="ticker", threads=True, **kw)
    except Exception as exc:
        print("download failed:", type(exc).__name__)
        return out
    for t in tickers:
        try:
            x = df[t].dropna(subset=["Close"])
            if len(x):
                out[t] = x
        except Exception:
            pass
    return out


def universe():
    path = os.path.join(ROOT, "data", "universe.json")
    try:
        import requests
        r = requests.get("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv", headers=UA, timeout=30)
        syms = [ln.split(",")[2].strip() for ln in r.text.splitlines()[1:] if ln.count(",") >= 4]
        if len(syms) > 300:
            pb.save(path, syms)
            return syms, "Nifty 500"
    except Exception as exc:
        print("universe download failed:", type(exc).__name__)
    cached = pb.load(path)
    return (cached, "Nifty 500 (cached list)") if cached else (list(N50), "Nifty 50 (fallback list)")


def nse_session():
    import requests
    s = requests.Session()
    s.headers.update(UA)
    s.get("https://www.nseindia.com/option-chain", timeout=20)
    return s


def option_chain(s, symbol):
    """NSE public option chain. Returns None when NSE refuses (common from cloud servers)."""
    data = None
    try:
        r = s.get(f"https://www.nseindia.com/api/option-chain-indices?symbol={symbol}", timeout=20)
        data = r.json()
        if not (data.get("records") or {}).get("data"):
            data = None
    except Exception:
        data = None
    if data is None:
        try:
            info = s.get(f"https://www.nseindia.com/api/option-chain-contract-info?symbol={symbol}", timeout=20).json()
            exp = info["expiryDates"][0]
            data = s.get(f"https://www.nseindia.com/api/option-chain-v3?type=Indices&symbol={symbol}&expiry={exp}", timeout=20).json()
        except Exception as exc:
            print(f"option chain {symbol} unavailable:", type(exc).__name__)
            return None
    try:
        rec = data["records"]
        spot = float(rec["underlyingValue"])
        exp = rec["expiryDates"][0]
        rows = []
        for it in rec["data"]:
            if it.get("expiryDate", it.get("expiryDates")) not in (exp, None):
                continue
            ce, pe = it.get("CE") or {}, it.get("PE") or {}
            rows.append({"k": float(it["strikePrice"]), "coi": ce.get("openInterest", 0) or 0,
                         "cch": ce.get("changeinOpenInterest", 0) or 0, "civ": ce.get("impliedVolatility", 0) or 0,
                         "poi": pe.get("openInterest", 0) or 0, "pch": pe.get("changeinOpenInterest", 0) or 0,
                         "piv": pe.get("impliedVolatility", 0) or 0})
        if not rows:
            return None
        rows.sort(key=lambda z: z["k"])
        tc, tp = sum(z["coi"] for z in rows), sum(z["poi"] for z in rows)
        pain = min(rows, key=lambda a: sum(max(0, a["k"] - z["k"]) * z["coi"] + max(0, z["k"] - a["k"]) * z["poi"] for z in rows))
        atm = min(range(len(rows)), key=lambda i: abs(rows[i]["k"] - spot))
        near = rows[max(0, atm - 4): atm + 5]
        return {"symbol": symbol, "expiry": exp, "spot": spot, "pcr": r2(tp / tc) if tc else None, "max_pain": pain["k"],
                "atm": rows[atm]["k"], "atm_iv": r2((rows[atm]["civ"] + rows[atm]["piv"]) / 2),
                "call_walls": [z["k"] for z in sorted(rows, key=lambda z: -z["coi"])[:3]],
                "put_walls": [z["k"] for z in sorted(rows, key=lambda z: -z["poi"])[:3]], "strikes": near, "source": "NSE"}
    except Exception as exc:
        print(f"option chain {symbol} parse failed:", type(exc).__name__)
        return None


def broker_option_chain(symbol):
    """Hook for a broker API (Upstox, Dhan, Fyers, Zerodha). Return the same dict as option_chain()."""
    return None


def fii_dii(s):
    try:
        rows = s.get("https://www.nseindia.com/api/fiidiiTradeReact", timeout=20).json()
        return [{"cat": x["category"].split("/")[0].strip(), "date": x["date"], "net": float(x["netValue"])} for x in rows]
    except Exception as exc:
        print("FII/DII unavailable:", type(exc).__name__)
        return None


def headlines():
    try:
        import requests
        import xml.etree.ElementTree as ET
        r = requests.get("https://news.google.com/rss/search?q=nifty+sensex+stock+market+when:1d&hl=en-IN&gl=IN&ceid=IN:en",
                         headers=UA, timeout=20)
        items = ET.fromstring(r.content).findall(".//item")[:5]
        return [{"title": i.findtext("title", ""), "link": i.findtext("link", "")} for i in items]
    except Exception as exc:
        print("headlines unavailable:", type(exc).__name__)
        return []


def fundamentals(symbols):
    import yfinance as yf
    out = {}
    for s in symbols[:8]:
        try:
            i = yf.Ticker(s + ".NS").info
            out[s] = {"mcap": r2((i.get("marketCap") or 0) / 1e7, 0) or None, "pe": r2(i.get("trailingPE"), 1),
                      "roe": r2((i.get("returnOnEquity") or 0) * 100, 1) if i.get("returnOnEquity") is not None else None,
                      "margin": r2((i.get("profitMargins") or 0) * 100, 1) if i.get("profitMargins") is not None else None,
                      "de": r2((i.get("debtToEquity") or 0) / 100, 2) if i.get("debtToEquity") is not None else None,
                      "growth": r2((i.get("revenueGrowth") or 0) * 100, 1) if i.get("revenueGrowth") is not None else None}
        except Exception:
            pass
    return out


def collect(slot):
    raw = {"errors": []}
    idx = [v[0] for v in INDICES.values()] + [VIX]
    d = dl(idx + list(SECTORS.values()), period="2y", interval="1d")
    raw["daily"] = {k: d.get(v[0]) for k, v in INDICES.items()}
    raw["vix"] = d.get(VIX)
    raw["sectors"] = {k: d[v] for k, v in SECTORS.items() if v in d}
    raw["intra"] = {tf: {k: x.get(v[0]) for k, v in INDICES.items()} for tf, x in
                    (("5m", dl(idx[:3], period="5d", interval="5m")), ("15m", dl(idx[:3], period="1mo", interval="15m")),
                     ("1h", dl(idx[:3], period="3mo", interval="60m")))}
    g = dl(list(GLOBALS.values()), period="1mo", interval="1d")
    raw["globals"] = {k: g[v] for k, v in GLOBALS.items() if v in g}
    syms, raw["universe"] = universe()
    st = dl([s + ".NS" for s in syms], period="1y", interval="1d")
    raw["stocks"] = {k[:-3]: v for k, v in st.items()}
    if slot in ("post", "weekly"):
        mo = dl([s + ".NS" for s in syms], period="max", interval="1mo")
        raw["monthly"] = {k[:-3]: v for k, v in mo.items()}
    try:
        s = nse_session()
        raw["oc"] = {k: broker_option_chain(k) or option_chain(s, k) for k in ("NIFTY", "BANKNIFTY")}
        raw["fii"] = fii_dii(s)
    except Exception as exc:
        print("NSE site unavailable:", type(exc).__name__)
        raw["oc"], raw["fii"] = {}, None
    raw["news"] = headlines()
    for k in INDICES:
        if raw["daily"].get(k) is None:
            raw["errors"].append(f"{k} prices unavailable")
    if not raw["stocks"]:
        raw["errors"].append("stock prices unavailable")
    return raw


def synthetic_raw():
    f = br.synthetic
    cut = lambda d: d[d.index <= pd.Timestamp.today().normalize()].iloc[-500:]  # noqa: E731
    raw = {"errors": [], "daily": {"NIFTY": cut(f(1, 24000)), "BANKNIFTY": cut(f(2, 55000, 0.012)), "SENSEX": cut(f(3, 80000))},
           "vix": cut(f(4, 15, 0.04)), "sectors": {k: cut(f(10 + i, 20000)) for i, k in enumerate(SECTORS)},
           "globals": {k: cut(f(40 + i, 5000)) for i, k in enumerate(GLOBALS)}, "universe": "synthetic",
           "stocks": {}, "monthly": {}, "intra": {}, "news": [{"title": "Sample headline for layout test", "link": ""}],
           "fii": [{"cat": "FII", "date": "today", "net": -1250.5}, {"cat": "DII", "date": "today", "net": 2210.0}]}
    rng = np.random.default_rng(7)
    for i, s in enumerate(list(N50) + [f"TEST{i}" for i in range(60)]):
        d = cut(f(100 + i, 100 + 30 * i, 0.02)).iloc[-250:].copy()
        d["Volume"] = rng.integers(2e5, 4e6, len(d)).astype(float)
        if i % 9 == 0:
            d.iloc[-1, d.columns.get_loc("Volume")] *= 9
            d.iloc[-1, d.columns.get_loc("Close")] = d["High"].iloc[-1] = d["Close"].iloc[-2] * 1.06
        raw["stocks"][s] = d
        raw["monthly"][s] = d.resample("MS").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    for tf in ("5m", "15m", "1h"):
        raw["intra"][tf] = {k: v.iloc[-200:] for k, v in raw["daily"].items()}
    nf = float(raw["daily"]["NIFTY"]["Close"].iloc[-1])
    atm = round(nf / 50) * 50
    raw["oc"] = {"NIFTY": {"symbol": "NIFTY", "expiry": "sample", "spot": nf, "pcr": 0.92, "max_pain": atm, "atm": atm, "atm_iv": 13.4,
                           "call_walls": [atm + 200, atm + 100, atm + 300], "put_walls": [atm - 200, atm - 100, atm - 300], "source": "sample",
                           "strikes": [{"k": atm + 50 * j, "coi": 90000 + 9000 * j, "cch": 4000 * j, "poi": 90000 - 9000 * j,
                                        "pch": -3000 * j, "civ": 13, "piv": 14} for j in range(-4, 5)]}}
    return raw


# ---------------- analysis ----------------
def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def trend(c):
    if c is None or len(c) < 55:
        return "n/a"
    e20, e50, last = c.ewm(span=20).mean().iloc[-1], c.ewm(span=50).mean().iloc[-1], c.iloc[-1]
    if last > e20 > e50:
        return "Bullish"
    if last < e20 < e50:
        return "Bearish"
    return "Improving" if last > e20 else ("Weakening" if last < e20 else "Neutral")


def pct(a, b):
    return None if not a or not b else (a / b - 1) * 100


def lastdate(d):
    return pd.Timestamp(d.index[-1]).date()


def index_view(slot, key, name, d, raw, vix, rates, today):
    part = lastdate(d) == today and slot in ("pre", "intra")       # today's bar is still forming
    if slot == "pre" and part:
        d = d.iloc[:-1]
        part = False
    ref = d.iloc[-2] if part else d.iloc[-1]                        # last completed session -> levels
    cur, prev = d.iloc[-1], d.iloc[-2]
    c = d["Close"]
    done = c.iloc[:-1] if part else c
    close, chg = float(cur["Close"]), pct(float(cur["Close"]), float(prev["Close"]))
    h, l, pc_ = float(ref["High"]), float(ref["Low"]), float(ref["Close"])
    p = (h + l + pc_) / 3
    lv = {"R3": h + 2 * (p - l), "R2": p + (h - l), "R1": 2 * p - l, "P": p, "S1": 2 * p - h, "S2": p - (h - l), "S3": l - 2 * (h - p)}
    lv = {k: r2(v) for k, v in lv.items()}
    sma = {n: float(done.rolling(n).mean().iloc[-1]) if len(done) >= n else None for n in (20, 50, 200)}
    rs = float(rsi(done).iloc[-1])
    score = sum((1 if close > m else -1) for m in sma.values() if m) + (1 if rs > 55 else -1 if rs < 45 else 0)
    wk = d.iloc[-5:]
    hi52, lo52 = float(d["High"].iloc[-250:].max()), float(d["Low"].iloc[-250:].min())
    weekly = c.resample("W-FRI").last().dropna()
    def icl(tf):
        x = raw["intra"].get(tf, {}).get(key)
        return None if x is None else x["Close"]
    mtf = [("Weekly", trend(weekly)), ("Daily", trend(done)), ("1 Hour", trend(icl("1h"))),
           ("15 Min", trend(icl("15m"))), ("5 Min", trend(icl("5m")))]
    ladder = [(lv[k], k) for k in lv] + [(h, "Prev High"), (l, "Prev Low"), (float(wk["High"].max()), "Week High"),
                                         (float(wk["Low"].min()), "Week Low"), (hi52, "52W High")] + \
             [(v, f"{n}-DMA") for n, v in sma.items() if v]
    oc = (raw.get("oc") or {}).get(key)
    if oc:
        ladder += [(oc["call_walls"][0], "Top Call OI"), (oc["put_walls"][0], "Top Put OI"), (oc["max_pain"], "Max Pain")]
    ladder = sorted([(r2(v), n) for v, n in ladder if v and abs(v / close - 1) < 0.06], reverse=True)
    v = {"key": key, "name": name, "close": r2(close), "chg": r2(chg), "pts": r2(close - float(prev["Close"])),
         "open": r2(float(cur["Open"])), "high": r2(float(cur["High"])), "low": r2(float(cur["Low"])),
         "prev_close": r2(float(prev["Close"])), "rsi": r2(rs, 1), "sma": {str(k): r2(x) for k, x in sma.items()},
         "hi52": r2(hi52), "lo52": r2(lo52), "week_hi": r2(float(wk["High"].max())), "week_lo": r2(float(wk["Low"].min())),
         "week_chg": r2(pct(close, float(c.iloc[-6])) if len(c) > 6 else None),
         "bias": "Bullish" if score >= 2 else "Bearish" if score <= -2 else "Neutral", "score": score,
         "mtf": [{"tf": a, "trend": b} for a, b in mtf], "levels": lv, "ladder": [{"v": a, "n": b} for a, b in ladder],
         "candles": [[str(pd.Timestamp(i).date()), r2(float(x.Open)), r2(float(x.High)), r2(float(x.Low)), r2(float(x.Close))]
                     for i, x in d.iloc[-30:].iterrows()], "probs": [], "gap": None}
    gap = None
    if slot in ("intra", "post") and lastdate(d) == today:
        gap = pct(float(cur["Open"]), float(prev["Close"]))
        v["gap"] = r2(gap)
    days = 5 if slot == "weekly" else 1
    base = float(prev["Close"]) if part else close
    if vix:
        mv = base * vix / 100 * math.sqrt(days / 252)
        v["exp"] = {"lo": r2(base - mv), "hi": r2(base + mv), "pct": r2(mv / base * 100), "horizon": "week" if days == 5 else "day"}
    v["scenarios"] = {"bull": {"trigger": lv["R1"], "targets": [lv["R2"], lv["R3"]], "invalid": lv["P"]},
                      "bear": {"trigger": lv["S1"], "targets": [lv["S2"], lv["S3"]], "invalid": lv["P"]},
                      "range": [lv["S1"], lv["R1"]]}
    tr = "up" if sma[20] and base > sma[20] else "dn"
    vb = pb.vix_bucket(vix)
    if slot == "weekly":
        sign = "up" if (v["week_chg"] or 0) > 0 else "dn"
        wt = "up" if len(weekly) >= 10 and weekly.iloc[-1] > weekly.rolling(10).mean().iloc[-1] else "dn"
        k, hit = pb.lookup(((rates or {}).get("weekly") or {}).get(key), [f"{sign}|{wt}|{vb}", f"{sign}|{wt}|*", f"{sign}|*|*", "*|*|*"])
        if hit:
            v["setup"] = f"last week {'up' if sign == 'up' else 'down'}, {'above' if wt == 'up' else 'below'} 10-week avg, VIX {vb}"
            v["probs"] = [{"label": "Next week closes higher", "p": hit["p_up"], "n": hit["n"]}]
            v["range"] = {"text": "weekly return % (median, 5th-95th pct)", "med": hit["med"], "lo": hit["p5"], "hi": hit["p95"]}
    else:
        gb = pb.gap_bucket(gap) if slot == "intra" else "*"
        k, hit = pb.lookup(((rates or {}).get("daily") or {}).get(key), [f"{gb}|{tr}|{vb}", f"{gb}|{tr}|*", f"{gb}|*|*", "*|*|*"])
        if hit:
            g0 = k.split("|")[0]
            v["setup"] = f"{'next session, ' if slot != 'intra' else ''}{pb.GAP_TEXT.get(g0, 'any open')}, {'above' if tr == 'up' else 'below'} 20-DMA, VIX {vb}"
            v["probs"] = [{"label": "Closes above open", "p": hit["p_co"], "n": hit["n"]},
                          {"label": "Closes above prev close", "p": hit["p_cc"], "n": hit["n"]}]
            if g0 not in ("flat", "*"):
                v["probs"].append({"label": "Gap gets filled", "p": hit["p_fill"], "n": hit["n"]})
            v["range"] = {"text": "day range % (median, 10th-90th pct)", "med": hit["rng_med"], "lo": hit["rng_p10"], "hi": hit["rng_p90"]}
    return v


def vix_signal(mkt, vx):
    """Market move + VIX move matrix."""
    if mkt is None or vx is None:
        return None
    up, dn, vu, vd = mkt > 0.2, mkt < -0.2, vx > 2, vx < -2
    if up and vd:
        return {"sentiment": "Bullish", "check": "Long build-up in futures"}
    if up and vu:
        return {"sentiment": "Bearish divergence", "check": "Long build-up or hedging"}
    if dn and vu:
        return {"sentiment": "Bearish", "check": "Short build-up in futures"}
    if dn and not vu and not vd:
        return {"sentiment": "Potential bullish reversal", "check": "Long unwinding"}
    if up or dn:
        return {"sentiment": "Mixed", "check": "Wait for futures OI confirmation"}
    return {"sentiment": "No clear signal", "check": "Do not rely on VIX"}


def stock_metrics(raw, slot):
    rows = []
    for s, d in raw["stocks"].items():
        if len(d) < 60:
            continue
        c, v = d["Close"], d["Volume"]
        close, prev = float(c.iloc[-1]), float(c.iloc[-2])
        avg = float(v.iloc[-21:-1].mean())
        hi, lo = float(d["High"].iloc[-1]), float(d["Low"].iloc[-1])
        hi52 = float(d["High"].max())
        w_avg = float(v.iloc[-55:-5].mean()) * 5
        m = raw.get("monthly", {}).get(s)
        ath = max(float(m["High"].max()), hi52) if m is not None and len(m) else None
        prior12 = float(m["High"].iloc[-13:-1].max()) if m is not None and len(m) >= 14 else None
        rows.append({"s": s, "c": r2(close), "chg": r2(pct(close, prev)), "x": r2(float(v.iloc[-1]) / avg, 1) if avg else None,
                     "cr": r2(close * float(v.iloc[-1]) / 1e7, 0), "pos": (close - lo) / (hi - lo) if hi > lo else 1,
                     "d52": r2(pct(close, hi52), 1), "dath": r2(pct(close, ath), 1) if ath else None,
                     "w": r2(pct(close, float(c.iloc[-6])), 1), "m": r2(pct(close, float(c.iloc[-22])), 1),
                     "wx": r2(float(v.iloc[-5:].sum()) / w_avg, 1) if w_avg else None,
                     "a50": close > float(c.iloc[-50:].mean()), "a200": close > float(c.iloc[-200:].mean()) if len(c) >= 200 else None,
                     "mbo": bool(prior12 and close > prior12), "nh": hi >= hi52 * 0.999, "nl": lo <= float(d["Low"].min()) * 1.001})
    liquid = [r for r in rows if (r["cr"] or 0) >= 10 and (r["c"] or 0) >= 50]
    keep = ("s", "c", "chg", "x", "cr", "d52", "dath", "w", "m")
    cut = lambda rs, n=15: [{k: r[k] for k in keep} for r in rs[:n]]  # noqa: E731
    L = {}
    sh = sorted([r for r in liquid if (r["x"] or 0) >= 3 and (r["chg"] or 0) >= 2 and (slot == "intra" or r["pos"] >= 0.67)], key=lambda r: -r["x"])
    L["volume_shockers"] = {"title": "Volume shockers", "rows": cut(sh), "rule": "volume 3x+ of 20-day average, up 2%+, close in top third of day range"}
    h52 = sorted([r for r in liquid if r["d52"] is not None and r["d52"] >= -5 and r["a50"] and r["a200"]], key=lambda r: (-r["d52"], -r["cr"]))
    L["high52_zone"] = {"title": "52-week-high zone", "rows": cut(h52), "rule": "within 5% of 52-week high, above 50 and 200 DMA"}
    if raw.get("monthly"):
        ath = sorted([r for r in liquid if r["dath"] is not None and r["dath"] >= -5], key=lambda r: (-r["dath"], -r["cr"]))
        L["ath_zone"] = {"title": "All-time-high zone", "rows": cut(ath), "rule": "within 5% of all-time high"}
        mbo = sorted([r for r in liquid if r["mbo"]], key=lambda r: -(r["m"] or 0))
        L["monthly_bo"] = {"title": "Monthly breakout", "rows": cut(mbo), "rule": "price above the highest high of the prior 12 months (confirmed at month-end close)"}
    if slot == "weekly":
        wk = sorted([r for r in liquid if (r["wx"] or 0) >= 2 and (r["w"] or 0) > 0], key=lambda r: -r["wx"])
        for r in wk:
            r["x"], r["chg"] = r["wx"], r["w"]
        L["weekly_volume"] = {"title": "Weekly volume shockers", "rows": cut(wk), "rule": "5-day volume 2x+ of average with a positive week"}
    n = max(1, len(rows))
    a200 = [r for r in rows if r["a200"] is not None]
    breadth = {"adv": sum(1 for r in rows if (r["chg"] or 0) > 0), "dec": sum(1 for r in rows if (r["chg"] or 0) < 0),
               "a50": r2(sum(r["a50"] for r in rows) / n * 100, 0), "a200": r2(sum(r["a200"] for r in a200) / max(1, len(a200)) * 100, 0),
               "nh": sum(r["nh"] for r in rows), "nl": sum(r["nl"] for r in rows), "n": len(rows)}
    fld = "w" if slot == "weekly" else "chg"
    n50 = sorted([r for r in rows if r["s"] in N50 and r[fld] is not None], key=lambda r: -r[fld])
    movers = {"gainers": [{"s": r["s"], "chg": r[fld]} for r in n50[:5]], "losers": [{"s": r["s"], "chg": r[fld]} for r in n50[-5:][::-1]]}
    trendmap = {r["s"]: bool(r["a50"] and r["a200"]) for r in rows}
    return L, breadth, movers, trendmap


def tag(f, up):
    q = (f.get("roe") or 0) >= 15 and (f.get("margin") or 0) >= 8
    cheap = f.get("pe") is not None and 0 < f["pe"] <= 30
    if q and up:
        return "Strong performer" if cheap else "Strong, getting expensive"
    if up:
        return "Momentum only"
    return "Quality, weak trend" if q else "Weak"


def sector_view(raw):
    out = []
    for k, d in raw["sectors"].items():
        c = d["Close"]
        if len(c) < 30:
            continue
        hi, lo = float(d["High"].iloc[-250:].max()), float(d["Low"].iloc[-250:].min())
        out.append({"name": k, "close": r2(float(c.iloc[-1])), "d": r2(pct(float(c.iloc[-1]), float(c.iloc[-2]))),
                    "w": r2(pct(float(c.iloc[-1]), float(c.iloc[-6]))), "m": r2(pct(float(c.iloc[-1]), float(c.iloc[-22]))),
                    "rsi": r2(float(rsi(c).iloc[-1]), 0), "pos": r2((float(c.iloc[-1]) - lo) / (hi - lo) * 100, 0) if hi > lo else None,
                    "trend": trend(c)})
    return out


def analyse(slot, raw, rates, today):
    vx = raw.get("vix")
    vix = float(vx["Close"].iloc[-1]) if vx is not None and len(vx) else None
    vchg = pct(vix, float(vx["Close"].iloc[-2])) if vix and len(vx) > 1 else None
    view = {"slot": slot, "date": str(today), "time_ist": dt.datetime.now(IST).strftime("%H:%M"),
            "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), "errors": raw["errors"],
            "rates_built": (rates or {}).get("built"), "universe": raw.get("universe"),
            "vix": {"close": r2(vix), "chg": r2(vchg), "regime": pb.vix_bucket(vix)},
            "indices": [index_view(slot, k, n, raw["daily"][k], raw, vix, rates, today) for k, (_, n) in INDICES.items()
                        if raw["daily"].get(k) is not None and len(raw["daily"][k]) > 30],
            "globals": [{"name": k, "close": r2(float(d["Close"].iloc[-1])), "chg": r2(pct(float(d["Close"].iloc[-1]), float(d["Close"].iloc[-2])))}
                        for k, d in raw["globals"].items() if len(d) > 1],
            "sectors": sector_view(raw), "oc": {k: v for k, v in (raw.get("oc") or {}).items() if v}, "fii": raw.get("fii"),
            "news": raw.get("news") or []}
    nf = next((i for i in view["indices"] if i["key"] == "NIFTY"), None)
    view["vix_signal"] = vix_signal(nf["chg"] if nf else None, vchg)
    view["lists"], view["breadth"], view["movers"], view["_trend"] = stock_metrics(raw, slot) if raw["stocks"] else ({}, None, None, {})
    return view


# ---------------- drawing ----------------
BG, CARD, TXT, MUT, UP, DN, ACC, AMB = "#0b1220", "#162033", "#e8eef7", "#8a9bb3", "#2ecc8f", "#ff6b6b", "#5aa9ff", "#f5b942"


class Poster:
    def __init__(self, view, title, H):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyBboxPatch, Rectangle
        self.plt, self.Box, self.Rect, self.H = plt, FancyBboxPatch, Rectangle, H
        self.fig = plt.figure(figsize=(12, 12 * H / 100), dpi=105, facecolor=BG)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, 100)
        self.ax.set_ylim(H, 0)
        self.ax.axis("off")
        self.t(2, 2.6, "NSE DESK", 19, ACC, "bold")
        self.t(17, 2.6, title, 15, TXT, "bold")
        self.t(98, 2.6, f"{view['date']}  {view['time_ist']} IST", 10.5, MUT, ha="right")
        self.t(2, H - 1.2, "Data: Yahoo Finance, NSE. Levels and scenarios are rule-based reference levels; probabilities are historical "
                           "frequencies. Not investment advice.", 7.5, MUT)

    def t(self, x, y, s, size=9, color=TXT, weight="normal", ha="left"):
        self.ax.text(x, y, str(s), fontsize=size, color=color, fontweight=weight, ha=ha, va="center", clip_on=True)

    def card(self, x, y, w, h, title=None, color=ACC, fc=CARD):
        self.ax.add_patch(self.Box((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.8", fc=fc, ec="none"))
        if title:
            self.t(x + 1.2, y + 1.7, title, 10, color, "bold")

    def bar(self, x, y, w, frac, color, h=1.0):
        self.ax.add_patch(self.Rect((x, y - h / 2), w, h, fc=BG, ec="none"))
        self.ax.add_patch(self.Rect((x, y - h / 2), w * max(0, min(1, frac)), h, fc=color, ec="none"))

    def table(self, x, y, widths, head, rows, size=8.5, rh=1.75, colors=None):
        cx = [x + sum(widths[:i]) for i in range(len(widths))]
        for i, hd in enumerate(head):
            self.t(cx[i] + (0 if i == 0 else widths[i] - 0.4), y, hd, size - 1, MUT, ha="left" if i == 0 else "right")
        for j, r in enumerate(rows):
            for i, cell in enumerate(r):
                col = (colors[j][i] if colors and colors[j][i] else TXT)
                self.t(cx[i] + (0 if i == 0 else widths[i] - 0.4), y + rh * (j + 1), cell, size, col, "bold" if i == 0 else "normal",
                       "left" if i == 0 else "right")

    def candles(self, x, y, w, h, cs, lines=()):
        lo, hi = min(c[3] for c in cs), max(c[2] for c in cs)
        for v, _ in lines:
            lo, hi = min(lo, v), max(hi, v)
        sy = lambda v: y + h - (v - lo) / (hi - lo or 1) * h  # noqa: E731
        step = w / len(cs)
        for v, col in lines:
            self.ax.plot([x, x + w], [sy(v), sy(v)], color=col, lw=0.7, ls="--")
            self.t(x + w + 0.3, sy(v), f"{v:,.0f}", 7, col)
        for i, (_, o, hh, ll, c) in enumerate(cs):
            col, xc = (UP if c >= o else DN), x + step * (i + 0.5)
            self.ax.plot([xc, xc], [sy(hh), sy(ll)], color=col, lw=0.8)
            self.ax.add_patch(self.Rect((xc - step * 0.32, min(sy(o), sy(c))), step * 0.64, max(abs(sy(o) - sy(c)), 0.12), fc=col, ec="none"))

    def save(self, name):
        os.makedirs(IMG, exist_ok=True)
        path = os.path.join(IMG, name)
        self.fig.savefig(path, facecolor=BG)
        return path


def col(v):
    return TXT if v is None else (UP if v >= 0 else DN)


def tcol(s):
    return UP if s in ("Bullish", "Improving") else DN if s in ("Bearish", "Weakening") else AMB


def f0(v):
    return "-" if v is None else f"{v:,.0f}"


def fp(v, d=2):
    return "-" if v is None else f"{v:+.{d}f}%"


def lakh(v):
    return "-" if v is None else f"{v / 1e5:.2f}L" if abs(v) >= 1e5 else f"{v:,.0f}"


def poster_index(view):
    P = Poster(view, TITLE[view["slot"]] + " - INDEX DASHBOARD", 113)
    ivs = view["indices"]
    w = 96 / max(1, len(ivs)) - 1
    for i, iv in enumerate(ivs):
        x, y = 2 + i * (w + 1.5), 5
        P.card(x, y, w, 73)
        P.t(x + 1.2, y + 2, iv["name"], 11.5, MUT, "bold")
        P.t(x + w - 1.2, y + 2, iv["bias"], 11, tcol(iv["bias"]), "bold", "right")
        P.t(x + 1.2, y + 5.6, f"{iv['close']:,.2f}", 20, TXT, "bold")
        P.t(x + w - 1.2, y + 5, fp(iv["chg"]), 13, col(iv["chg"]), "bold", "right")
        P.t(x + w - 1.2, y + 7.2, f"{iv['pts']:+,.0f} pts", 8.5, col(iv["chg"]), ha="right")
        yy = y + 9.4
        P.t(x + 1.2, yy, f"O {f0(iv['open'])}  H {f0(iv['high'])}  L {f0(iv['low'])}  Prev {f0(iv['prev_close'])}"
            + (f"  Gap {fp(iv['gap'])}" if iv.get("gap") is not None else ""), 8, MUT)
        e = iv.get("exp")
        if e:
            P.t(x + 1.2, yy + 1.9, f"VIX 1-sigma {e['horizon']} range {f0(e['lo'])} - {f0(e['hi'])} (+/-{e['pct']}%)", 8, MUT)
        yy += 4.4
        P.t(x + 1.2, yy, "MULTI-TIMEFRAME TREND", 8.5, ACC, "bold")
        for j, m in enumerate(iv["mtf"]):
            cx = x + 1.2 + j * (w - 2.4) / 5
            P.t(cx, yy + 1.9, m["tf"], 7.5, MUT)
            P.t(cx, yy + 3.6, m["trend"], 8, tcol(m["trend"]), "bold")
        yy += 6.2
        P.t(x + 1.2, yy, "KEY LEVELS LADDER", 8.5, ACC, "bold")
        lad = iv["ladder"]
        above = [z for z in lad if z["v"] > iv["close"]][-7:]
        below = [z for z in lad if z["v"] <= iv["close"]][:7]
        for z in above:
            yy += 1.75
            P.t(x + 1.2, yy, f"{z['v']:,.0f}", 9, DN, "bold")
            P.t(x + 9, yy, z["n"], 8.5, MUT)
            P.t(x + w - 1.2, yy, fp(pct(z["v"], iv["close"]), 1), 8, MUT, ha="right")
        yy += 1.9
        P.ax.add_patch(P.Rect((x + 0.6, yy - 0.85), w - 1.2, 1.7, fc="#22406e", ec="none"))
        P.t(x + 1.2, yy, f"{iv['close']:,.0f}", 9.5, TXT, "bold")
        P.t(x + 9, yy, "LAST PRICE", 8.5, TXT, "bold")
        for z in below:
            yy += 1.75
            P.t(x + 1.2, yy, f"{z['v']:,.0f}", 9, UP, "bold")
            P.t(x + 9, yy, z["n"], 8.5, MUT)
            P.t(x + w - 1.2, yy, fp(pct(z["v"], iv["close"]), 1), 8, MUT, ha="right")
        yy = y + 51
        sc = iv["scenarios"]
        P.t(x + 1.2, yy, "SCENARIO MAP (rule-based)", 8.5, ACC, "bold")
        P.t(x + 1.2, yy + 1.9, f"Bull: above {f0(sc['bull']['trigger'])} -> {f0(sc['bull']['targets'][0])} / {f0(sc['bull']['targets'][1])}", 8.5, UP)
        P.t(x + 1.2, yy + 3.6, f"Bear: below {f0(sc['bear']['trigger'])} -> {f0(sc['bear']['targets'][0])} / {f0(sc['bear']['targets'][1])}", 8.5, DN)
        P.t(x + 1.2, yy + 5.3, f"Range: {f0(sc['range'][0])} - {f0(sc['range'][1])}   Pivot (invalidation) {f0(sc['bull']['invalid'])}", 8.5, AMB)
        yy += 8
        P.t(x + 1.2, yy, "PROBABILITIES (history)", 8.5, ACC, "bold")
        if iv["probs"]:
            P.t(x + 1.2, yy + 1.8, iv.get("setup", ""), 7.5, MUT)
            for j, p in enumerate(iv["probs"]):
                py = yy + 3.8 + j * 2
                P.t(x + 1.2, py, p["label"], 8.5)
                P.bar(x + w * 0.55, py, w * 0.2, p["p"] / 100, UP if p["p"] >= 50 else DN)
                P.t(x + w - 1.2, py, f"{p['p']:.0f}%  n={p['n']}", 8.5, TXT, "bold", "right")
            g = iv.get("range")
            if g:
                P.t(x + 1.2, yy + 3.8 + len(iv["probs"]) * 2, f"{g['text']}: {g['med']} ({g['lo']} to {g['hi']})", 7.5, MUT)
        else:
            P.t(x + 1.2, yy + 2, "Probability tables not built yet", 8.5, MUT)
        P.t(x + 1.2, y + 71, f"RSI {iv['rsi']}  20D {f0(iv['sma']['20'])}  50D {f0(iv['sma']['50'])}  200D {f0(iv['sma']['200'])}  "
                             f"52W {f0(iv['lo52'])}-{f0(iv['hi52'])}", 7.5, MUT)
    y = 79.5
    P.card(2, y, 31, 14, "VIX + MARKET SIGNAL")
    vx, sg = view["vix"], view.get("vix_signal")
    P.t(3.2, y + 4.4, f"India VIX {vx['close'] if vx['close'] is not None else '-'}", 12, TXT, "bold")
    P.t(31.8, y + 4.4, fp(vx["chg"], 1), 11, col(-(vx["chg"] or 0)), "bold", "right")
    if sg:
        P.t(3.2, y + 7.4, sg["sentiment"], 11, tcol("Bullish" if sg["sentiment"].startswith("Bull") or "reversal" in sg["sentiment"] else
                                                   "Bearish" if sg["sentiment"].startswith("Bear") else "x"), "bold")
        P.t(3.2, y + 9.8, "Check in futures: " + sg["check"], 8.5, MUT)
    P.t(3.2, y + 12.2, f"Regime: {vx['regime']} (lo <13, mid 13-18, hi >18)", 7.5, MUT)
    P.card(34.5, y, 31, 14, "BREADTH + FLOWS")
    b, fi = view.get("breadth"), view.get("fii")
    if b:
        P.t(35.7, y + 4.2, f"Advances {b['adv']}  Declines {b['dec']}  ({view.get('universe')})", 8.5)
        P.bar(35.7, y + 6, 28.6, b["adv"] / max(1, b["adv"] + b["dec"]), UP, 0.8)
        P.t(35.7, y + 8, f"Above 50-DMA {b['a50']:.0f}%   Above 200-DMA {b['a200']:.0f}%   52W highs {b['nh']}  lows {b['nl']}", 8, MUT)
    if fi:
        P.t(35.7, y + 10.6, "   ".join(f"{z['cat']} {z['net']:+,.0f} cr" for z in fi[:2]), 9.5, TXT, "bold")
        P.t(35.7, y + 12.4, f"Cash market, {fi[0]['date']}", 7.5, MUT)
    else:
        P.t(35.7, y + 11, "FII/DII: NSE feed unavailable this run", 8, MUT)
    P.card(67, y, 31, 14, "GLOBAL CUES")
    for j, g in enumerate(view["globals"][:10]):
        gx, gy = 68.2 + (j % 2) * 15, y + 4 + (j // 2) * 2
        P.t(gx, gy, g["name"], 8, MUT)
        P.t(gx + 9.6, gy, f"{g['close']:,.1f}" if g["close"] is not None else "-", 8, TXT, ha="right")
        P.t(gx + 13.8, gy, fp(g["chg"], 1), 8, col(g["chg"]), ha="right")
    y = 95
    oc = (view.get("oc") or {}).get("NIFTY")
    P.card(2, y, 47, 15, "NIFTY OPTION CHAIN" + (f" ({oc['expiry']}, {oc['source']})" if oc else ""))
    if oc:
        P.t(3.2, y + 4, f"PCR {oc['pcr']}   Max pain {f0(oc['max_pain'])}   ATM {f0(oc['atm'])} IV {oc['atm_iv']}", 9, TXT, "bold")
        P.t(3.2, y + 5.9, f"Call walls {', '.join(f0(k) for k in oc['call_walls'])}   Put walls {', '.join(f0(k) for k in oc['put_walls'])}", 8, MUT)
        st = oc["strikes"][1:-1] if len(oc["strikes"]) > 5 else oc["strikes"]
        st = st[len(st) // 2 - 2: len(st) // 2 + 3]
        P.table(3.2, y + 7.6, [8, 9, 9, 9, 9], ["Strike", "Call OI", "Chg", "Put OI", "Chg"],
                [[f0(z["k"]), lakh(z["coi"]), lakh(z["cch"]), lakh(z["poi"]), lakh(z["pch"])] for z in st], 8, 1.35,
                [[None, None, col(-z["cch"]), None, col(z["pch"])] for z in st])
    else:
        P.t(3.2, y + 6, "Option chain unavailable this run (NSE refused the request).", 8.5, MUT)
        P.t(3.2, y + 8, "Add a broker API key to make this panel reliable.", 8.5, MUT)
    P.card(50.5, y, 47.5, 15, "HEADLINES")
    for j, n in enumerate(view["news"][:5]):
        P.t(51.7, y + 4 + j * 2.2, "- " + n["title"][:92], 7.8, TXT)
    if not view["news"]:
        P.t(51.7, y + 6, "No headlines fetched this run.", 8.5, MUT)
    return P.save(f"{view['slot']}_1.png"), P


def poster_market(view):
    secs = sorted(view["sectors"], key=lambda s: -((s["w"] if view["slot"] == "weekly" else s["d"]) or 0))
    rows_n = max(1, math.ceil(len(secs) / 5))
    wk = view["slot"] == "weekly"
    H = 9 + rows_n * 9.5 + (30 if wk else 0) + 17 + 3
    P = Poster(view, TITLE[view["slot"]] + " - SECTORS & MOVERS", H)
    fld = "w" if wk else "d"
    for i, s in enumerate(secs):
        x, y = 2 + (i % 5) * 19.3, 5 + (i // 5) * 9.5
        v = s[fld] or 0
        a = min(1, abs(v) / (4 if wk else 2))
        base = (46, 204, 143) if v >= 0 else (255, 107, 107)
        fc = "#%02x%02x%02x" % tuple(int(22 + (c - 22) * (0.18 + 0.5 * a)) for c in base)
        P.card(x, y, 18.6, 8.8, fc=fc)
        P.t(x + 1, y + 1.6, s["name"], 9.5, TXT, "bold")
        P.t(x + 17.6, y + 1.6, s["trend"], 7.5, TXT, ha="right")
        P.t(x + 1, y + 4, f"{s['close']:,.0f}", 12, TXT, "bold")
        P.t(x + 17.6, y + 4, fp(s[fld]), 11, TXT, "bold", "right")
        P.t(x + 1, y + 6.2, f"D {fp(s['d'], 1)}  W {fp(s['w'], 1)}  M {fp(s['m'], 1)}  RSI {s['rsi']:.0f}", 7.2, TXT)
        if s["pos"] is not None:
            P.bar(x + 1, y + 7.8, 16.6, s["pos"] / 100, TXT, 0.35)
    y = 5 + rows_n * 9.5 + 0.5
    if wk:
        for i, iv in enumerate(view["indices"]):
            x = 2 + i * 32.5
            P.card(x, y, 31.5, 29, f"{iv['name']} - last 30 sessions")
            P.candles(x + 1.2, y + 4, 26, 21, iv["candles"], [(iv["week_hi"], DN), (iv["week_lo"], UP)])
            P.t(x + 1.2, y + 27, f"Week {fp(iv['week_chg'])}   High {f0(iv['week_hi'])}   Low {f0(iv['week_lo'])}", 8.5, col(iv["week_chg"]), "bold")
        y += 30
    mv = view.get("movers") or {"gainers": [], "losers": []}
    for i, (ttl, key, c) in enumerate((("NIFTY 50 TOP GAINERS", "gainers", UP), ("NIFTY 50 TOP LOSERS", "losers", DN))):
        x = 2 + i * 24.5
        P.card(x, y, 23.5, 16, ttl, c)
        for j, r in enumerate(mv[key]):
            P.t(x + 1.2, y + 4.4 + j * 2.2, r["s"], 9.5, TXT, "bold")
            P.t(x + 22.3, y + 4.4 + j * 2.2, fp(r["chg"]), 9.5, c, "bold", "right")
    P.card(51, y, 47, 16, "SECTOR RANKING (" + ("week" if wk else "day") + ")")
    top = secs[:10]
    mx = max([abs(s[fld] or 0) for s in top] + [0.5])
    for j, s in enumerate(top):
        yy = y + 4 + j * 1.2
        P.t(52.2, yy, s["name"], 7.5)
        v = s[fld] or 0
        P.ax.add_patch(P.Rect((74 if v >= 0 else 74 - 10 * abs(v) / mx, yy - 0.4), 10 * abs(v) / mx, 0.8, fc=UP if v >= 0 else DN, ec="none"))
        P.t(96.8, yy, fp(v), 7.5, col(v), ha="right")
    return P.save(f"{view['slot']}_2.png"), P


def poster_stocks(view):
    lists = [(k, l) for k, l in view["lists"].items() if l["rows"]]
    fund = view.get("fund") or {}
    H = 8 + sum(5.5 + 1.75 * min(10, len(l["rows"])) for _, l in lists[0::2]) + (7 + 1.9 * len(fund) if fund else 0) + 4
    if len(lists) > 1:
        H = 8 + sum(max(5.5 + 1.75 * min(10, len(a[1]["rows"])), 5.5 + 1.75 * min(10, len(b[1]["rows"])) if b else 0)
                    for a, b in zip(lists[0::2], lists[1::2] + [None])) + (7 + 1.9 * len(fund) if fund else 0) + 4
    P = Poster(view, TITLE[view["slot"]] + " - STOCKS TO WATCH", max(H, 30))
    y = 5
    if not lists:
        P.t(2, 10, "No stocks matched the screens in this run.", 11, MUT)
    for a, b in zip(lists[0::2], lists[1::2] + [None]):
        ph = max(5.5 + 1.75 * min(10, len(z[1]["rows"])) for z in (a, b) if z)
        for i, z in enumerate((a, b)):
            if not z:
                continue
            k, l = z
            x = 2 + i * 48.5
            P.card(x, y, 47.5, ph, f"{l['title'].upper()}  ({len(l['rows'])})")
            last = ("vs ATH", "dath") if k == "ath_zone" else ("1M %", "m") if k == "monthly_bo" else ("vs 52wH", "d52")
            rows = l["rows"][:10]
            P.table(x + 1.2, y + 3.6, [13, 8.5, 7.5, 6.5, 6, 6], ["Stock", "Close", "Chg %", "Vol x", "Rs cr", last[0]],
                    [[r["s"][:14], f"{r['c']:,.1f}", fp(r["chg"], 1), f"{r['x']}x" if r["x"] is not None else "-", f0(r["cr"]),
                      fp(r.get(last[1]), 1)] for r in rows], 8.3, 1.75, [[None, None, col(r["chg"]), None, MUT, MUT] for r in rows])
        y += ph + 1
    if fund:
        P.card(2, y, 96, 5 + 1.9 * len(fund), "FUNDAMENTAL SNAPSHOT - volume shockers (tag is rule-based: ROE, margin, PE, trend)")
        rows = [[s, f0(f["mcap"]), f["pe"] if f["pe"] is not None else "-", fp(f["roe"], 1) if f["roe"] is not None else "-",
                 fp(f["margin"], 1) if f["margin"] is not None else "-", f["de"] if f["de"] is not None else "-",
                 fp(f["growth"], 1) if f["growth"] is not None else "-", f["tag"]] for s, f in fund.items()]
        P.table(3.2, y + 3.4, [14, 11, 8, 9, 10, 8, 11, 22], ["Stock", "MCap cr", "PE", "ROE", "Margin", "D/E", "Sales YoY", "Tag"], rows, 8.5, 1.9,
                [[None] * 7 + [UP if "Strong" in r[7] else DN if r[7] == "Weak" else AMB] for r in rows])
    return P.save(f"{view['slot']}_3.png"), P


# ---------------- telegram ----------------
def caption(view, url):
    L = [f"<b>NSE DESK - {TITLE[view['slot']]}</b>  {view['date']} {view['time_ist']} IST"]
    for iv in view["indices"]:
        s = f"<b>{iv['name']}</b> {iv['close']:,.0f} ({iv['chg']:+.2f}%) {iv['bias']} | S1 {f0(iv['levels']['S1'])} P {f0(iv['levels']['P'])} R1 {f0(iv['levels']['R1'])}"
        if iv["probs"]:
            s += f" | {iv['probs'][0]['label'].lower()} {iv['probs'][0]['p']:.0f}%"
        L.append(s)
    if view.get("vix_signal"):
        L.append(f"VIX {view['vix']['close']} ({fp(view['vix']['chg'], 1)}): {view['vix_signal']['sentiment']}")
    for l in view["lists"].values():
        if l["rows"]:
            L.append(f"<b>{l['title']}:</b> " + ", ".join(r["s"] for r in l["rows"][:6]))
    if url:
        L.append(f'<a href="{url}">Open dashboard</a>')
    out = "\n".join(L)
    return out if len(out) <= 1000 else out[:990] + "..."


def send(view, pngs, pdf, url):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("telegram: skipped (secrets not set)")
        return
    import requests
    base, cap = f"https://api.telegram.org/bot{token}/", caption(view, url)
    try:
        files = {f"p{i}": open(p, "rb") for i, p in enumerate(pngs)}
        media = [{"type": "photo", "media": f"attach://p{i}", **({"caption": cap, "parse_mode": "HTML"} if i == 0 else {})} for i in range(len(pngs))]
        r = requests.post(base + "sendMediaGroup", data={"chat_id": chat, "media": json.dumps(media)}, files=files, timeout=90)
        if not r.ok:
            print("telegram: album failed", r.status_code, "- sending text")
            r = requests.post(base + "sendMessage", data={"chat_id": chat, "text": cap, "parse_mode": "HTML", "disable_web_page_preview": "true"}, timeout=60)
        if view["slot"] == "weekly" and pdf:
            requests.post(base + "sendDocument", data={"chat_id": chat}, files={"document": open(pdf, "rb")}, timeout=90)
        print("telegram:", "sent" if r.ok else f"failed {r.status_code}")
    except Exception as exc:
        print("telegram: error", type(exc).__name__)


def rates_fresh(fake):
    path = os.path.join(ROOT, "data", "base_rates.json")
    rates = pb.load(path, {})
    try:
        age = (dt.date.today() - dt.date.fromisoformat(rates.get("built", "2000-01-01"))).days
    except ValueError:
        age = 999
    if fake:
        data = {k: br.synthetic(i + 1) for i, k in enumerate(INDICES)}
        vix = pd.Series(np.abs(np.random.default_rng(3).normal(16, 4, 3600)), index=data["NIFTY"].index)
    elif age <= 6 and all(k in (rates.get("daily") or {}) for k in INDICES):
        return rates
    else:
        data = {k: br.fetch(v[0]) for k, v in INDICES.items()}
        vd = br.fetch(VIX)
        vix = vd["Close"] if vd is not None else pd.Series(dtype=float)
    data = {k: v for k, v in data.items() if v is not None}
    if not data:
        return rates
    new = {"built": dt.date.today().isoformat(), "synthetic": fake, "daily": {k: br.daily_table(d, vix) for k, d in data.items()},
           "weekly": {k: br.weekly_table(d, vix) for k, d in data.items()},
           "years": {k: [str(d.index[0].date()), str(d.index[-1].date())] for k, d in data.items()}}
    if not fake:
        pb.save(path, new)
    return new


def main():
    slot = sys.argv[1]
    fake, force = "--synthetic" in sys.argv, "--force" in sys.argv
    now = dt.datetime.now(IST)
    today = now.date()
    cfg = pb.load(os.path.join(ROOT, "config", "screens.json"), {})
    if str(today) in (cfg.get("holidays") or []) and not force:
        print("holiday - nothing to do")
        return
    raw = synthetic_raw() if fake else collect(slot)
    nf = raw["daily"].get("NIFTY")
    if nf is None:
        sys.exit("no index data - aborting")
    if fake:
        today = lastdate(nf)
    if slot in ("intra", "post") and lastdate(nf) != today and not force:
        print(f"no NSE session today ({today}); last session {lastdate(nf)} - nothing to do")
        return
    rates = rates_fresh(fake)
    view = analyse(slot, raw, rates, today)
    tm = view.pop("_trend")
    sh = [r["s"] for r in (view["lists"].get("volume_shockers") or {"rows": []})["rows"]][:8]
    if fake:
        view["fund"] = {s: {"mcap": 12000, "pe": 28.5, "roe": 17.2, "margin": 11.0, "de": 0.3, "growth": 14.0} for s in sh[:4]}
    else:
        view["fund"] = fundamentals(sh) if slot in ("post", "weekly") else {}
    for s, f in view["fund"].items():
        f["tag"] = tag(f, tm.get(s, False))
    outs = [poster_index(view), poster_market(view), poster_stocks(view)]
    pngs = [o[0] for o in outs]
    pdf = os.path.join(IMG, f"{slot}.pdf")
    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(pdf) as pp:
        for _, P in outs:
            pp.savefig(P.fig, facecolor=BG)
            P.plt.close(P.fig)
    view["posters"] = [os.path.basename(p) for p in pngs]
    if fake:
        print("synthetic run ok:", pngs)
        return
    state = pb.load(os.path.join(OUT, "state.json"), {})
    pb.save(os.path.join(OUT, f"{slot}.json"), view)
    pb.save(os.path.join(OUT, "history", f"{view['date']}_{slot}.json"), view)
    state["dates"] = sorted(set(state.get("dates", []) + [view["date"]]))[-40:]
    pb.save(os.path.join(OUT, "state.json"), state)
    if "--no-telegram" not in sys.argv:
        send(view, pngs, pdf, os.environ.get("DASHBOARD_URL", ""))
    print(f"published {slot} {view['date']}: {len(view['indices'])} indices, {len(view['sectors'])} sectors, "
          + ", ".join(f"{k}={len(v['rows'])}" for k, v in view["lists"].items()) + f", errors={view['errors']}")


if __name__ == "__main__":
    main()
