#!/usr/bin/env python3
"""Turn data/in/<slot>.json into dashboard JSON, a PNG/PDF image and a Telegram post.

Usage: python scripts/publish.py <slot> [--no-telegram]
Runs inside GitHub Actions; uses no AI. Every number is a quote or simple arithmetic.
"""
import datetime as dt
import hashlib
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "data")
IMG = os.path.join(ROOT, "docs", "img")
SLOT_TITLE = {"pre": "PRE-MARKET", "intra": "INTRADAY OUTLOOK",
              "post": "POST-MARKET", "weekly": "WEEKLY OUTLOOK"}
IDX = [("NSE:NIFTY", "NIFTY 50"), ("NSE:BANKNIFTY", "BANK NIFTY")]
GLOBAL_NAMES = {"NSEIX:NIFTY1!": "GIFT Nifty", "SP:SPX": "S&P 500", "NASDAQ:IXIC": "Nasdaq",
                "DJ:DJI": "Dow", "TVC:NI225": "Nikkei", "TVC:HSI": "Hang Seng",
                "FX_IDC:USDINR": "USD/INR", "NYMEX:BZ1!": "Brent", "TVC:DXY": "DXY",
                "TVC:US10Y": "US 10Y", "TVC:GOLD": "Gold"}


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


def num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def rows_of(block):
    """Accept {"cols":[..],"rows":[[..]]}, a list of dicts, or {symbol: {..}}."""
    if not block:
        return []
    if isinstance(block, dict) and "cols" in block:
        return [dict(zip(block["cols"], r)) for r in block.get("rows", [])]
    if isinstance(block, dict):
        return [dict(v, symbol=k) for k, v in block.items() if isinstance(v, dict)]
    return [r for r in block if isinstance(r, dict)]


def r2(x, n=2):
    return None if x is None else round(x, n)


# ---------- index maths ----------
def gap_bucket(g):
    if g is None:
        return "*"
    if g <= -0.5:
        return "gd2"
    if g <= -0.15:
        return "gd1"
    if g < 0.15:
        return "flat"
    if g < 0.5:
        return "gu1"
    return "gu2"


GAP_TEXT = {"gd2": "gap-down over 0.5%", "gd1": "small gap-down", "flat": "flat open",
            "gu1": "small gap-up", "gu2": "gap-up over 0.5%", "*": "any open"}


def vix_bucket(v):
    if v is None:
        return "*"
    return "lo" if v < 13 else ("mid" if v <= 18 else "hi")


def pivots(h, l, c):
    if None in (h, l, c):
        return {}
    p = (h + l + c) / 3
    return {"R2": r2(p + (h - l)), "R1": r2(2 * p - l), "P": r2(p),
            "S1": r2(2 * p - h), "S2": r2(p - (h - l)),
            "PDH": r2(h), "PDL": r2(l), "PDC": r2(c)}


def lookup(table, keys):
    """First key with n >= 30, else the largest-sample fallback."""
    best = None
    for k in keys:
        row = (table or {}).get(k)
        if row and row.get("n", 0) >= 30:
            return k, row
        if row and (best is None or row.get("n", 0) > best[1].get("n", 0)):
            best = (k, row)
    return best if best else (None, None)


def bias(close, row):
    score = 0
    for key in ("SMA20", "SMA50", "SMA200"):
        m = num(row.get(key))
        if m is not None and close is not None:
            score += 1 if close > m else -1
    rsi = num(row.get("RSI"))
    if rsi is not None:
        score += 1 if rsi > 55 else (-1 if rsi < 45 else 0)
    label = "Bullish" if score >= 2 else ("Bearish" if score <= -2 else "Neutral")
    return score, label


def index_view(slot, sym, name, row, vix, gift, nifty_close, state, rates):
    close, chg = num(row.get("close")), num(row.get("change"))
    hi, lo, op = num(row.get("high")), num(row.get("low")), num(row.get("open"))
    prev_close = close / (1 + chg / 100) if close and chg is not None else None
    score, label = bias(close, row)
    v = {"sym": sym, "name": name, "close": r2(close), "chg": r2(chg), "open": r2(op),
         "high": r2(hi), "low": r2(lo), "rsi": r2(num(row.get("RSI")), 1),
         "sma20": r2(num(row.get("SMA20"))), "sma50": r2(num(row.get("SMA50"))),
         "sma200": r2(num(row.get("SMA200"))), "bias": label, "bias_score": score,
         "probs": [], "levels": {}, "gap": None, "exp": None, "range": None}

    gap = None
    if slot == "pre":           # row holds the last completed session
        v["levels"] = pivots(hi, lo, close)
        ref = close
        if gift and nifty_close:
            gap = (gift / nifty_close - 1) * 100
            v["gap"] = {"pct": r2(gap), "kind": "implied by GIFT Nifty" +
                        ("" if sym == "NSE:NIFTY" else " (Nifty proxy)")}
    elif slot == "intra":       # row holds today's session so far
        v["levels"] = (state.get("prev_day") or {}).get(sym, {})
        ref = prev_close
        gap = num(row.get("gap"))
        if gap is not None:
            v["gap"] = {"pct": r2(gap), "kind": "actual open"}
    else:                       # post / weekly: levels for the next session
        v["levels"] = pivots(hi, lo, close)
        ref = close

    sma20 = num(row.get("SMA20"))
    trend = "*" if None in (ref, sma20) else ("up" if ref > sma20 else "dn")
    vb = vix_bucket(vix)
    if ref and vix:
        days = 5 if slot == "weekly" else 1
        move = ref * vix / 100 * math.sqrt(days / 252)
        v["exp"] = {"lo": r2(ref - move), "hi": r2(ref + move),
                    "pct": r2(move / ref * 100), "horizon": "week" if days == 5 else "day"}

    short = "NIFTY" if sym == "NSE:NIFTY" else "BANKNIFTY"
    if slot == "weekly":
        perf = num(row.get("Perf.W"))
        sign = "*" if perf is None else ("up" if perf > 0 else "dn")
        sma50 = num(row.get("SMA50"))
        wtrend = "*" if None in (close, sma50) else ("up" if close > sma50 else "dn")
        key, hit = lookup(((rates or {}).get("weekly") or {}).get(short),
                          [f"{sign}|{wtrend}|{vb}", f"{sign}|{wtrend}|*", f"{sign}|*|*", "*|*|*"])
        if hit:
            v["setup"] = (f"last week {'up' if sign == 'up' else 'down' if sign == 'dn' else 'any'}, "
                          f"{'above' if wtrend == 'up' else 'below' if wtrend == 'dn' else 'any'} 10-wk avg, "
                          f"VIX {vb if vb != '*' else 'any'}")
            v["probs"] = [{"label": "Next week closes higher", "p": hit["p_up"], "n": hit["n"]}]
            v["range"] = {"med": hit["med"], "lo": hit["p5"], "hi": hit["p95"],
                          "text": "weekly return, median and 5th-95th pct"}
    else:
        gb = gap_bucket(gap)
        key, hit = lookup(((rates or {}).get("daily") or {}).get(short),
                          [f"{gb}|{trend}|{vb}", f"{gb}|{trend}|*", f"{gb}|*|*", "*|*|*"])
        if hit:
            used_gap = key.split("|")[0]
            v["setup"] = (("next session, " if slot == "post" else "") + f"{GAP_TEXT.get(used_gap, 'any open')}, "
                          f"{'above' if trend == 'up' else 'below' if trend == 'dn' else 'any'} 20-DMA, "
                          f"VIX {vb if vb != '*' else 'any'}")
            v["probs"] = [{"label": "Closes above open", "p": hit["p_co"], "n": hit["n"]},
                          {"label": "Closes above prev close", "p": hit["p_cc"], "n": hit["n"]}]
            if hit.get("p_fill") is not None and used_gap not in ("flat", "*"):
                v["probs"].append({"label": "Gap gets filled", "p": hit["p_fill"], "n": hit["n"]})
            v["range"] = {"med": hit["rng_med"], "lo": hit["rng_p10"], "hi": hit["rng_p90"],
                          "text": "day range %, median and 10th-90th pct"}
    return v


# ---------- stock lists ----------
def sym_short(s):
    return str(s or "").split(":")[-1]


def vol_x(r):
    x = num(r.get("vol_ratio_10d"))
    if x is None:
        v, a = num(r.get("volume")), num(r.get("average_volume_10d_calc"))
        x = v / a if v and a else None
    return x


def base_row(r):
    val = num(r.get("Value.Traded"))
    return {"s": sym_short(r.get("symbol") or r.get("name")), "c": r2(num(r.get("close"))),
            "chg": r2(num(r.get("change"))), "x": r2(vol_x(r), 1),
            "cr": r2(val / 1e7, 0) if val else None}


def pct_from(close, ref):
    return None if not close or not ref else (close / ref - 1) * 100


def build_lists(slot, screens):
    lists = {}
    sh = rows_of(screens.get("volume_shockers"))
    if sh:
        out = []
        for r in sh:
            x, chg = vol_x(r), num(r.get("change"))
            c, h, l = num(r.get("close")), num(r.get("high")), num(r.get("low"))
            if x is None or chg is None or x < 3 or chg < 2:
                continue
            pos = (c - l) / (h - l) if None not in (c, h, l) and h > l else None
            if slot != "intra" and pos is not None and pos < 0.67:
                continue
            row = base_row(r)
            row["d52"] = r2(pct_from(c, num(r.get("price_52_week_high"))), 1)
            out.append(row)
        out.sort(key=lambda z: -(z["x"] or 0))
        lists["volume_shockers"] = {"title": "Volume shockers", "rows": out[:15],
                                    "rule": "volume 3x+ of 10-day avg, up 2%+, closing in top third of range"}
    hs = rows_of(screens.get("highs"))
    if hs:
        h52, ath, mbo = [], [], []
        for r in hs:
            c = num(r.get("close"))
            d52 = pct_from(c, num(r.get("price_52_week_high")))
            dath = pct_from(c, num(r.get("High.All")))
            e200, e50 = num(r.get("price_vs_ema200_pct")), num(r.get("price_vs_ema50_pct"))
            row = base_row(r)
            row["d52"], row["dath"] = r2(d52, 1), r2(dath, 1)
            row["w"], row["m"] = r2(num(r.get("Perf.W")), 1), r2(num(r.get("Perf.1M")), 1)
            uptrend = (e200 is None or e200 > 0) and (e50 is None or e50 > 0)
            if d52 is not None and d52 >= -5 and uptrend:
                h52.append(row)
            if dath is not None and dath >= -5:
                ath.append(row)
            h1m, w52 = num(r.get("High.1M")), num(r.get("price_52_week_high"))
            if (h1m and w52 and h1m >= 0.999 * w52 and d52 is not None and d52 >= -3
                    and (num(r.get("Perf.1M")) or 0) > 0):
                mbo.append(row)
        h52.sort(key=lambda z: -(z["d52"] if z["d52"] is not None else -99))
        ath.sort(key=lambda z: -(z["dath"] if z["dath"] is not None else -99))
        mbo.sort(key=lambda z: -(z["m"] or 0))
        lists["high52_zone"] = {"title": "52-week-high zone", "rows": h52[:15],
                                "rule": "within 5% of 52-week high, above 50 and 200 EMA"}
        lists["ath_zone"] = {"title": "All-time-high zone", "rows": ath[:15],
                             "rule": "within 5% of all-time high"}
        if slot == "weekly":
            lists["monthly_bo"] = {"title": "Monthly breakout (12-month high this month)",
                                   "rows": mbo[:15],
                                   "rule": "made a new 12-month high this month and holds within 3% of it"}
    wk = rows_of(screens.get("weekly_volume"))
    if wk:
        out = []
        for r in wk:
            x, chg = num(r.get("relative_volume_10d_calc|1W")), num(r.get("change|1W"))
            if x is None or chg is None or x < 2 or chg <= 0:
                continue
            row = base_row(r)
            row["x"], row["chg"] = r2(x, 1), r2(chg)
            row["d52"] = r2(pct_from(num(r.get("close")), num(r.get("price_52_week_high"))), 1)
            out.append(row)
        out.sort(key=lambda z: -(z["x"] or 0))
        lists["weekly_volume"] = {"title": "Weekly volume shockers", "rows": out[:15],
                                  "rule": "weekly volume 2x+ of average with a positive week"}
    return lists


# ---------- image ----------
def render(view, png, pdf):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyBboxPatch

    BG, CARD, TXT, MUT = "#0e1420", "#182233", "#e8eef7", "#8a9bb3"
    UP, DN, ACC = "#2ecc8f", "#ff6b6b", "#5aa9ff"
    lists = [l for l in view["lists"].values() if l["rows"]][:4]
    list_h = 0
    if lists:
        per = [4.8 + 2.2 * min(8, len(l["rows"])) for l in lists]
        pairs = [max(per[i:i + 2]) for i in range(0, len(per), 2)]
        list_h = sum(pairs) + 2 * len(pairs)
    H = 61.5 + list_h + 9
    fig = plt.figure(figsize=(10.8, 10.8 * H / 100), dpi=110, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(H, 0)
    ax.axis("off")

    def card(x, y, w, h):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=1.2",
                                    fc=CARD, ec="none"))

    def t(x, y, s, size=11, color=TXT, weight="normal", ha="left"):
        ax.text(x, y, str(s), fontsize=size, color=color, fontweight=weight, ha=ha, va="center")

    def col(v):
        return TXT if v is None else (UP if v >= 0 else DN)

    def pc(v):
        return "-" if v is None else f"{v:+.2f}%"

    def n0(v):
        return "-" if v is None else f"{v:,.0f}"

    t(4, 3.6, "NSE DESK", 20, ACC, "bold")
    t(96, 3.0, SLOT_TITLE.get(view["slot"], view["slot"].upper()), 15, TXT, "bold", "right")
    t(96, 5.6, f"{view['date']}  {view.get('time_ist', '')} IST", 10, MUT, ha="right")

    y = 8.5
    for i, iv in enumerate(view["indices"]):
        x = 4 + i * 47
        card(x, y, 45, 35.5)
        t(x + 2, y + 2.8, iv["name"], 12, MUT, "bold")
        bc = UP if iv["bias"] == "Bullish" else (DN if iv["bias"] == "Bearish" else MUT)
        t(x + 43, y + 2.8, iv["bias"], 11, bc, "bold", "right")
        t(x + 2, y + 7, "-" if iv["close"] is None else f"{iv['close']:,.2f}", 21, TXT, "bold")
        t(x + 43, y + 7, pc(iv["chg"]), 14, col(iv["chg"]), "bold", "right")
        yy = y + 11
        if iv.get("gap"):
            t(x + 2, yy, f"Gap {pc(iv['gap']['pct'])}  ({iv['gap']['kind']})", 9.5, col(iv["gap"]["pct"]))
            yy += 2.4
        if iv.get("exp"):
            e = iv["exp"]
            t(x + 2, yy, f"VIX 1-sigma {e['horizon']} range  {n0(e['lo'])} - {n0(e['hi'])}  (+/-{e['pct']}%)", 9.5, MUT)
            yy += 2.4
        lv = iv.get("levels") or {}
        if lv:
            keys = ["S2", "S1", "P", "R1", "R2"]
            for j, k in enumerate(keys):
                cx = x + 4.5 + j * 9
                t(cx, yy + 1.2, k, 8.5, MUT, ha="center")
                t(cx, yy + 3.4, n0(lv.get(k)), 10, DN if k[0] == "S" else (UP if k[0] == "R" else TXT), "bold", "center")
            t(x + 2, yy + 6.2, f"Prev H {n0(lv.get('PDH'))}   L {n0(lv.get('PDL'))}   C {n0(lv.get('PDC'))}", 9, MUT)
            yy += 8.4
        t(x + 2, yy, f"RSI {iv['rsi'] if iv['rsi'] is not None else '-'}   20D {n0(iv['sma20'])}   "
                     f"50D {n0(iv['sma50'])}   200D {n0(iv['sma200'])}", 9, MUT)
        yy += 2.8
        if iv["probs"]:
            t(x + 2, yy, "History: " + iv.get("setup", ""), 8.5, ACC)
            yy += 2.4
            for p in iv["probs"]:
                t(x + 2, yy, p["label"], 9.5)
                ax.add_patch(FancyBboxPatch((x + 21, yy - 0.7), 10, 1.4, boxstyle="round,pad=0,rounding_size=0.5", fc=BG, ec="none"))
                ax.add_patch(FancyBboxPatch((x + 21, yy - 0.7), 10 * p["p"] / 100, 1.4, boxstyle="round,pad=0,rounding_size=0.5",
                                            fc=UP if p["p"] >= 50 else DN, ec="none"))
                t(x + 43, yy, f"{p['p']:.0f}%  n={p['n']}", 9, TXT, "bold", "right")
                yy += 2.5
            if iv.get("range"):
                g = iv["range"]
                t(x + 2, yy, f"{g['text']}: {g['med']}  ({g['lo']} to {g['hi']})", 8.5, MUT)
        else:
            t(x + 2, yy, "Probability table not built yet", 9, MUT)

    y = 46
    card(4, y, 92, 12.5)
    vx = view.get("vix") or {}
    cells = [("India VIX", vx.get("close"), vx.get("chg"))] + \
            [(g["name"], g["close"], g["chg"]) for g in view["globals"]]
    for i, (nm, c, ch) in enumerate(cells[:12]):
        cx, cy = 6 + (i % 6) * 15.2, y + 2.6 + (i // 6) * 6
        t(cx, cy, nm, 8.5, MUT)
        t(cx, cy + 2.3, "-" if c is None else f"{c:,.2f}", 10, TXT, "bold")
        t(cx + 13.2, cy + 2.3, "-" if ch is None else f"{ch:+.1f}%", 8.5, col(ch), ha="right")

    y = 60.5
    for i in range(0, len(lists), 2):
        pair = lists[i:i + 2]
        ph = max(4.8 + 2.2 * min(8, len(l["rows"])) for l in pair)
        for j, l in enumerate(pair):
            x = 4 + j * 47
            card(x, y, 45, ph)
            t(x + 2, y + 2.2, l["title"], 11, ACC, "bold")
            for k, r in enumerate(l["rows"][:8]):
                ry = y + 5 + k * 2.2
                t(x + 2, ry, r["s"][:13], 9.5, TXT, "bold")
                t(x + 25, ry, "-" if r["c"] is None else f"{r['c']:,.1f}", 9.5, TXT, ha="right")
                t(x + 34, ry, "-" if r["chg"] is None else f"{r['chg']:+.1f}%", 9.5, col(r["chg"]), ha="right")
                extra = f"{r['x']}x" if l is view["lists"].get("volume_shockers") or l is view["lists"].get("weekly_volume") \
                    else (f"{r.get('dath')}%" if l is view["lists"].get("ath_zone") else f"{r.get('d52')}%")
                t(x + 43, ry, extra if "None" not in extra else "-", 9.5, MUT, ha="right")
        y += ph + 2
    note = (view.get("note") or "").strip()
    if note:
        import textwrap
        for k, line in enumerate(textwrap.wrap(note, 120)[:2]):
            t(4, y + 1.2 + k * 2, line, 9, TXT)
    t(4, H - 1.6, "Data: TradingView. Probabilities are historical frequencies, not forecasts. Not investment advice.", 8, MUT)
    os.makedirs(os.path.dirname(png), exist_ok=True)
    fig.savefig(png, facecolor=BG)
    fig.savefig(pdf, facecolor=BG)
    plt.close(fig)


# ---------- telegram ----------
def caption(view, url):
    lines = [f"<b>NSE DESK - {SLOT_TITLE.get(view['slot'], view['slot'])}</b>  {view['date']} {view.get('time_ist', '')}"]
    for iv in view["indices"]:
        s = f"<b>{iv['name']}</b> {iv['close']:,.0f} ({iv['chg']:+.2f}%) {iv['bias']}" if iv["close"] and iv["chg"] is not None else iv["name"]
        if iv.get("gap"):
            s += f" | gap {iv['gap']['pct']:+.2f}%"
        if iv["probs"]:
            p = iv["probs"][0]
            s += f" | {p['label'].lower()} {p['p']:.0f}% (n={p['n']})"
        lines.append(s)
    for l in view["lists"].values():
        if l["rows"]:
            lines.append(f"<b>{l['title']}:</b> " + ", ".join(r["s"] for r in l["rows"][:8]))
    if view.get("note"):
        lines.append(view["note"])
    if url:
        lines.append(f'<a href="{url}">Open dashboard</a>')
    out = "\n".join(lines)
    return out if len(out) <= 1000 else out[:990] + "..."


def telegram(view, png, pdf, url):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("telegram: skipped (secrets not set)")
        return False
    import requests
    base = f"https://api.telegram.org/bot{token}/"
    cap = caption(view, url)
    try:
        with open(png, "rb") as fh:
            r = requests.post(base + "sendPhoto", data={"chat_id": chat, "caption": cap, "parse_mode": "HTML"},
                              files={"photo": fh}, timeout=60)
        if not r.ok:   # fallback: plain text if the photo is refused
            print("telegram: photo failed", r.status_code, "- sending text")
            r = requests.post(base + "sendMessage", data={"chat_id": chat, "text": cap, "parse_mode": "HTML",
                                                          "disable_web_page_preview": "true"}, timeout=60)
        if view["slot"] == "weekly":
            with open(pdf, "rb") as fh:
                requests.post(base + "sendDocument", data={"chat_id": chat}, files={"document": fh}, timeout=60)
        print("telegram:", "sent" if r.ok else f"failed {r.status_code}")
        return r.ok
    except Exception as exc:  # never fail the publish because of Telegram
        print("telegram: error", type(exc).__name__)
        return False


def main():
    slot = sys.argv[1]
    src = load(os.path.join(ROOT, "data", "in", f"{slot}.json"))
    if not src:
        sys.exit(f"no input for {slot}")
    rates = load(os.path.join(ROOT, "data", "base_rates.json"), {})
    state = load(os.path.join(OUT, "state.json"), {})
    irows = {r.get("symbol"): r for r in rows_of(src.get("indices"))}
    vixr = irows.get("NSE:INDIAVIX", {})
    vix = num(vixr.get("close"))
    gift = num(irows.get("NSEIX:NIFTY1!", {}).get("close"))
    nifty_close = num(irows.get("NSE:NIFTY", {}).get("close"))
    view = {"slot": slot, "date": src.get("date"), "time_ist": src.get("time_ist"),
            "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "note": src.get("note", ""), "errors": src.get("errors", []),
            "rates_built": (rates or {}).get("built"),
            "vix": {"close": r2(vix), "chg": r2(num(vixr.get("change"))), "regime": vix_bucket(vix)},
            "indices": [index_view(slot, s, n, irows[s], vix, gift, nifty_close, state, rates)
                        for s, n in IDX if s in irows],
            "globals": [{"name": nm, "close": r2(num(irows[s].get("close"))), "chg": r2(num(irows[s].get("change")))}
                        for s, nm in GLOBAL_NAMES.items() if s in irows],
            "lists": build_lists(slot, src.get("screens") or {})}

    digest = hashlib.sha1(json.dumps([src.get("indices"), src.get("screens")], sort_keys=True).encode()).hexdigest()
    duplicate = state.get("hash", {}).get(slot) == digest
    save(os.path.join(OUT, f"{slot}.json"), view)
    if view["date"]:
        save(os.path.join(OUT, "history", f"{view['date']}_{slot}.json"), view)
        dates = sorted(set(state.get("dates", []) + [view["date"]]))[-40:]
        state["dates"] = dates
    if slot == "post":
        state["prev_day"] = {iv["sym"]: iv["levels"] for iv in view["indices"]}
    state.setdefault("hash", {})[slot] = digest
    png, pdf = os.path.join(IMG, f"{slot}.png"), os.path.join(IMG, f"{slot}.pdf")
    render(view, png, pdf)
    url = os.environ.get("DASHBOARD_URL", "")
    if "--no-telegram" in sys.argv:
        print("telegram: disabled by flag")
    elif duplicate:
        print("telegram: skipped (same data as last run - market holiday or re-run)")
    else:
        telegram(view, png, pdf, url)
    save(os.path.join(OUT, "state.json"), state)
    print(f"published {slot} {view['date']}: {len(view['indices'])} indices, "
          + ", ".join(f"{k}={len(v['rows'])}" for k, v in view["lists"].items()))


if __name__ == "__main__":
    main()
