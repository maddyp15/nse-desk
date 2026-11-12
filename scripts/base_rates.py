#!/usr/bin/env python3
"""Build data/base_rates.json: how often each index setup led to each outcome.

Sources, tried in order: yfinance, Yahoo chart API. On failure the old file is kept.
Usage: python scripts/base_rates.py [--synthetic]   (synthetic = offline self-test)
"""
import datetime as dt
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "base_rates.json")
TICKERS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "VIX": "^INDIAVIX"}


def fetch_yf(t):
    import yfinance as yf
    df = yf.download(t, period="15y", interval="1d", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df[["Open", "High", "Low", "Close"]].dropna()


def fetch_chart(t):
    import requests
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{t}"
    r = requests.get(url, params={"range": "15y", "interval": "1d"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=40)
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({"Open": q["open"], "High": q["high"], "Low": q["low"], "Close": q["close"]},
                      index=pd.to_datetime(res["timestamp"], unit="s").normalize())
    return df.dropna()


def fetch(t):
    for fn in (fetch_yf, fetch_chart):
        try:
            df = fn(t)
            if len(df) > 500:
                return df
        except Exception as exc:
            print(f"{fn.__name__}({t}) failed: {type(exc).__name__}")
    return None


def synthetic(seed, start=10000.0, vol=0.01):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2012-01-02", periods=3600)
    ret = rng.normal(0.0004, vol, len(idx))
    close = start * np.exp(np.cumsum(ret))
    gap = rng.normal(0, vol / 3, len(idx))
    op = np.r_[start, close[:-1]] * (1 + gap)
    hi = np.maximum(op, close) * (1 + np.abs(rng.normal(0, vol / 3, len(idx))))
    lo = np.minimum(op, close) * (1 - np.abs(rng.normal(0, vol / 3, len(idx))))
    return pd.DataFrame({"Open": op, "High": hi, "Low": lo, "Close": close}, index=idx)


def gap_bucket(g):
    return np.select([g <= -0.5, g <= -0.15, g < 0.15, g < 0.5], ["gd2", "gd1", "flat", "gu1"], "gu2")


def vix_bucket(v):
    return np.where(v.isna(), "na", np.where(v < 13, "lo", np.where(v <= 18, "mid", "hi")))


def daily_table(df, vix):
    d = df.copy()
    d["pc"] = d["Close"].shift(1)
    d["gap"] = (d["Open"] / d["pc"] - 1) * 100
    d["trend"] = np.where(d["pc"] > d["Close"].rolling(20).mean().shift(1), "up", "dn")
    d["vix"] = vix_bucket(vix.reindex(d.index).ffill().shift(1))
    d = d.dropna(subset=["pc", "gap"]).iloc[20:]
    d["gb"] = gap_bucket(d["gap"])
    d["co"] = d["Close"] > d["Open"]
    d["cc"] = d["Close"] > d["pc"]
    d["fill"] = np.where(d["gap"] > 0, d["Low"] <= d["pc"], d["High"] >= d["pc"])
    d["rng"] = (d["High"] - d["Low"]) / d["pc"] * 100

    def stat(g):
        return {"n": int(len(g)), "p_co": round(g["co"].mean() * 100, 1),
                "p_cc": round(g["cc"].mean() * 100, 1), "p_fill": round(g["fill"].mean() * 100, 1),
                "rng_med": round(g["rng"].median(), 2), "rng_p10": round(g["rng"].quantile(0.1), 2),
                "rng_p90": round(g["rng"].quantile(0.9), 2)}

    out = {"*|*|*": stat(d)}
    for gb, g1 in d.groupby("gb"):
        out[f"{gb}|*|*"] = stat(g1)
        for tr, g2 in g1.groupby("trend"):
            out[f"{gb}|{tr}|*"] = stat(g2)
            for vb, g3 in g2[g2["vix"] != "na"].groupby("vix"):
                out[f"{gb}|{tr}|{vb}"] = stat(g3)
    for tr, g2 in d.groupby("trend"):          # used when the next gap is unknown (post-market)
        out[f"*|{tr}|*"] = stat(g2)
        for vb, g3 in g2[g2["vix"] != "na"].groupby("vix"):
            out[f"*|{tr}|{vb}"] = stat(g3)
    return out


def weekly_table(df, vix):
    w = df["Close"].resample("W-FRI").last().dropna().to_frame("c")
    w["ret"] = w["c"].pct_change() * 100
    w["nxt"] = w["ret"].shift(-1)
    w["sign"] = np.where(w["ret"] > 0, "up", "dn")
    w["trend"] = np.where(w["c"] > w["c"].rolling(10).mean(), "up", "dn")
    w["vix"] = vix_bucket(vix.resample("W-FRI").last().reindex(w.index).ffill())
    w = w.iloc[10:].dropna(subset=["ret", "nxt"])

    def stat(g):
        return {"n": int(len(g)), "p_up": round((g["nxt"] > 0).mean() * 100, 1),
                "med": round(g["nxt"].median(), 2), "p5": round(g["nxt"].quantile(0.05), 2),
                "p95": round(g["nxt"].quantile(0.95), 2)}

    out = {"*|*|*": stat(w)}
    for s, g1 in w.groupby("sign"):
        out[f"{s}|*|*"] = stat(g1)
        for tr, g2 in g1.groupby("trend"):
            out[f"{s}|{tr}|*"] = stat(g2)
            for vb, g3 in g2[g2["vix"] != "na"].groupby("vix"):
                out[f"{s}|{tr}|{vb}"] = stat(g3)
    return out


def main():
    fake = "--synthetic" in sys.argv
    if fake:
        data = {"NIFTY": synthetic(1), "BANKNIFTY": synthetic(2, 20000, 0.013)}
        vix = pd.Series(np.abs(np.random.default_rng(3).normal(16, 4, 3600)), index=data["NIFTY"].index)
    else:
        data = {k: fetch(TICKERS[k]) for k in ("NIFTY", "BANKNIFTY")}
        vdf = fetch(TICKERS["VIX"])
        vix = vdf["Close"] if vdf is not None else pd.Series(dtype=float)
    data = {k: v for k, v in data.items() if v is not None}
    if not data:
        print("no price history available; keeping the existing base_rates.json")
        return 0
    out = {"built": dt.date.today().isoformat(), "synthetic": fake, "daily": {}, "weekly": {},
           "years": {k: [str(v.index[0].date()), str(v.index[-1].date())] for k, v in data.items()}}
    for k, df in data.items():
        out["daily"][k] = daily_table(df, vix)
        out["weekly"][k] = weekly_table(df, vix)
    path = OUT if not fake else os.path.join(ROOT, "data", "base_rates.synthetic.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, separators=(",", ":"))
    print("wrote", os.path.relpath(path, ROOT), {k: len(v) for k, v in out["daily"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
