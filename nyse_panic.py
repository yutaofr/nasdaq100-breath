#!/usr/bin/env python3
"""NYSE 恐慌指标图 ($NYLOW:$NYHGH 等)，复刻 StockCharts 的四层布局。

Panels (top to bottom):
  1. $NYADV:$NYDEC and $NYDEC:$NYADV ratios (bars)
  2. SPY daily with MA(377) and EMA(377)
  3. $NYLOW:$NYHGH ratio  <- the "panic to hope" indicator
  4. McClellan Summation ($NYSI, shaded) and Oscillator ($NYMO, line)

Data: NYSE breadth is computed from every NYSE-listed common stock's daily
bars (Nasdaq public API), cached on disk and updated incrementally. WSJ's
Markets Diary gives the official NYSE counts for the latest sessions; they
are appended to official.csv every run so an official history accumulates.

Usage:
  python3 nyse_panic.py              # update data, draw chart, print summary
  python3 nyse_panic.py --no-update  # redraw from cache only
  python3 nyse_panic.py --discord    # also post summary + chart to Discord (if data date changed)
"""
import argparse, concurrent.futures as cf, datetime as dt, json, os, sys, time
import numpy as np, pandas as pd, requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache_nyse")   # ~90 MB per-stock bars, not committed
OUT = HERE
UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
NASDAQ = "https://api.nasdaq.com/api"
WSJ = ("https://www.wsj.com/market-data/stocks/marketsdiary?id=%7B%22application%22%3A%22WSJ%22%2C"
       "%22marketsDiaryType%22%3A%22diaries%22%7D&type=mdc_marketsdiary")
HISTORY_START = "2023-01-01"    # 1y look-back for 52w highs/lows + ~2y of chart
CHART_MONTHS = 19               # same window as the screenshot
LOOKBACK = 252                  # trading days in 52 weeks
SAFE_LEVEL = 5                  # post: "once the ratio drops below 5, it's safe again"
BAD_NAME = ("Warrant", " Units", " Rights")   # NYSE counts include preferreds & funds, so keep them


def get(url, tries=4):
    for i in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 200:
                return r.json()
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1.5 * (i + 1))
    return None


def num(s):
    return float(str(s).replace("$", "").replace(",", "")) if s not in (None, "", "N/A") else np.nan


def history(sym, frm, to, assetclass="stocks"):
    j = get(f"{NASDAQ}/quote/{sym}/historical?assetclass={assetclass}&fromdate={frm}&todate={to}&limit=9999")
    rows = (((j or {}).get("data") or {}).get("tradesTable") or {}).get("rows") or []
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y")
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].map(num)
    return df.set_index("date").sort_index()[["open", "high", "low", "close"]]


def universe():
    j = get(f"{NASDAQ}/screener/stocks?tableonly=true&limit=10000&exchange=nyse")
    rows = j["data"]["table"]["rows"]
    return sorted(r["symbol"].strip().replace("/", ".") for r in rows
                  if not any(b in r["name"] for b in BAD_NAME))


def update_symbol(sym, today):
    path = os.path.join(CACHE, "stocks", f"{sym}.csv")
    sym = requests.utils.quote(sym)
    old = pd.read_csv(path, index_col=0, parse_dates=True) if os.path.exists(path) else None
    frm = HISTORY_START if old is None or old.empty else (old.index[-1] - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    new = history(sym, frm, today)
    if new is None:
        return sym, old is not None
    df = new if old is None else pd.concat([old[old.index < new.index[0]], new])
    df.to_csv(path)
    return sym, True


def update_all(workers=8):
    os.makedirs(os.path.join(CACHE, "stocks"), exist_ok=True)
    today = dt.date.today().isoformat()
    syms = universe()
    with open(os.path.join(CACHE, "universe.json"), "w") as f:
        json.dump(syms, f)
    ok = 0
    with cf.ThreadPoolExecutor(workers) as ex:
        for i, (s, good) in enumerate(ex.map(lambda s: update_symbol(s, today), syms), 1):
            ok += good
            if i % 250 == 0:
                print(f"  {i}/{len(syms)} symbols", file=sys.stderr)
    print(f"stocks updated: {ok}/{len(syms)}", file=sys.stderr)
    spy = history("SPY", "2021-01-01", today, "etf")
    if spy is not None:
        spy.to_csv(os.path.join(CACHE, "SPY.csv"))
    save_official()


def save_official():
    """Append WSJ's official NYSE diary (previous close is a finished session)."""
    j = get(WSJ)
    if not j:
        return None
    nyse = next(s for s in j["data"]["instrumentSets"] if s["headerFields"][0]["label"] == "NYSE")
    v = {i["id"]: i for i in nyse["instruments"]}
    snap = {k: {c: num(v[k][c]) for c in ("latestClose", "previousClose", "weekAgo")}
            for k in ("advances", "declines", "newhighs", "newlows")}
    snap["timestamp"] = j["data"]["timestamp"]
    with open(os.path.join(CACHE, "wsj_latest.json"), "w") as f:
        json.dump(snap, f, indent=1)
    return snap


def compute_breadth():
    syms = json.load(open(os.path.join(CACHE, "universe.json")))
    adv = dec = hi = lo = None
    for s in syms:
        p = os.path.join(CACHE, "stocks", f"{s}.csv")
        if not os.path.exists(p):
            continue
        d = pd.read_csv(p, index_col=0, parse_dates=True)
        d = d[~d.index.duplicated(keep="last")]
        if len(d) < 2:
            continue
        chg = d["close"].diff()
        # 52-week high/low vs the prior 252 sessions; needs a full year of history
        prior_hi = d["high"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).max()
        prior_lo = d["low"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).min()
        parts = [(chg > 0), (chg < 0), (d["high"] > prior_hi), (d["low"] < prior_lo)]
        parts = [x.astype(int) for x in parts]
        if adv is None:
            adv, dec, hi, lo = parts
        else:
            adv, dec, hi, lo = (a.add(b, fill_value=0) for a, b in zip((adv, dec, hi, lo), parts))
    b = pd.DataFrame({"adv": adv, "dec": dec, "highs": hi, "lows": lo}).iloc[1:]
    b = b[(b.adv + b.dec) > 500]      # drop half-populated dates (holidays, partial data)
    b["adv_dec"] = b.adv / b.dec.clip(lower=1)
    b["dec_adv"] = b.dec / b.adv.clip(lower=1)
    b["low_high"] = b.lows / b.highs.clip(lower=1)
    # StockCharts $NYMO/$NYSI use ratio-adjusted net advances (RANA)
    rana = 1000 * (b.adv - b.dec) / (b.adv + b.dec)
    b["nymo"] = rana.ewm(span=19, adjust=False).mean() - rana.ewm(span=39, adjust=False).mean()
    b["nysi"] = b["nymo"].cumsum()
    b.to_csv(os.path.join(OUT, "nyse_panic.csv"))
    return b


def draw(b, spy):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt, matplotlib.dates as mdates

    spy = spy.copy()
    spy["ma"] = spy.close.rolling(377).mean()
    spy["ema"] = spy.close.ewm(span=377, adjust=False).mean()
    start = b.index[-1] - pd.DateOffset(months=CHART_MONTHS)
    b, spy = b[b.index >= start], spy[spy.index >= start]
    nysi_base = b.nysi.iloc[0]
    b = b.assign(nysi=b.nysi - nysi_base + b.nymo.iloc[0])   # start summation inside the window

    fig, ax = plt.subplots(4, 1, figsize=(16, 13), sharex=True,
                           gridspec_kw={"height_ratios": [1.3, 4, 1.4, 1.2], "hspace": 0.04})
    green, red = "#2e7d32", "#c62828"
    last = b.iloc[-1]
    ax[0].bar(b.index, b.adv_dec.clip(upper=9), color=green, width=0.8, label=f"\\$NYADV:\\$NYDEC {last.adv_dec:.2f}")
    ax[0].bar(b.index, b.dec_adv.clip(upper=9), color=red, width=0.8, alpha=0.85,
              label=f"\\$NYDEC:\\$NYADV {last.dec_adv:.2f}")
    ax[0].set_ylim(0, 9.5)

    up = spy.close >= spy.open
    ax[1].vlines(spy.index, spy.low, spy.high, color="#444", lw=0.7)
    for m, c in ((up, "white"), (~up, red)):
        ax[1].bar(spy.index[m], (spy.close - spy.open)[m].abs().clip(lower=0.05),
                  bottom=np.minimum(spy.open, spy.close)[m], color=c, edgecolor="#222", width=0.7, lw=0.5)
    ax[1].plot(spy.index, spy.ma, color="#1a237e", lw=2, label=f"MA(377) {spy.ma.iloc[-1]:.2f}")
    ax[1].plot(spy.index, spy.ema, color="#1a237e", lw=1, ls=":", label=f"EMA(377) {spy.ema.iloc[-1]:.2f}")
    s = spy.iloc[-1]
    ax[1].plot([], [], " ", label=f"SPY (Daily) {s.close:.2f}  O {s.open:.2f} H {s.high:.2f} L {s.low:.2f}")

    ax[2].plot(b.index, b.low_high, color=red, lw=1.1, label=f"\\$NYLOW:\\$NYHGH {last.low_high:.2f}")
    ax[2].axhline(SAFE_LEVEL, color="#888", ls="--", lw=0.8, label=f"safe < {SAFE_LEVEL}")
    wp = os.path.join(CACHE, "wsj_latest.json")
    if os.path.exists(wp):
        w = json.load(open(wp))
        r = w["newlows"]["latestClose"] / max(w["newhighs"]["latestClose"], 1)
        ax[2].scatter([b.index[-1] + pd.Timedelta(days=2)], [r], color="black", zorder=5, s=18,
                      label=f"official NYSE (WSJ) latest {r:.1f}")
    ax[2].set_ylim(bottom=0)

    ax[3].fill_between(b.index, 0, b.nysi / 2.5, where=b.nysi >= 0, color="#9e9e9e", alpha=0.6, step="mid")
    ax[3].fill_between(b.index, 0, b.nysi / 2.5, where=b.nysi < 0, color="#e8a0b0", alpha=0.7, step="mid")
    ax[3].plot(b.index, b.nymo, color="black", lw=1, label=f"\\$NYMO {last.nymo:.2f}")
    ax[3].plot([], [], color="#9e9e9e", lw=6, label=f"\\$NYSI {last.nysi:.2f} (shaded, ÷2.5)")
    ax[3].axhline(0, color="#1a237e", lw=0.6)

    for a in ax:
        a.grid(color="#ddd", lw=0.5)
        a.yaxis.tick_right()
        a.legend(loc="upper left", fontsize=8, frameon=False)
    ax[3].xaxis.set_major_locator(mdates.MonthLocator())
    ax[3].xaxis.set_major_formatter(mdates.DateFormatter("%b\n%y"))
    fig.suptitle(f"NYSE breadth panic chart — data through {b.index[-1]:%Y-%m-%d} "
                 "(breadth computed from NYSE-listed constituents, Nasdaq data)", fontsize=11, x=0.01, y=0.92, ha="left")
    path = os.path.join(OUT, "nyse_panic.png")
    fig.savefig(path, dpi=110, bbox_inches="tight")
    return path


def summary(b):
    last, prev = b.iloc[-1], b.iloc[-2]
    rank = (b.low_high >= last.low_high).mean() * 100
    spikes = b.index[(b.low_high >= 20) & (b.low_high.shift(1) < 20)]
    lines = [
        f"Date {b.index[-1]:%Y-%m-%d} (computed from {len(json.load(open(os.path.join(CACHE, 'universe.json'))))} NYSE stocks)",
        f"  Adv {last.adv:.0f}  Dec {last.dec:.0f}  ADV:DEC {last.adv_dec:.2f}  DEC:ADV {last.dec_adv:.2f}",
        f"  New highs {last.highs:.0f}  New lows {last.lows:.0f}  LOW:HIGH {last.low_high:.1f} "
        f"(prev {prev.low_high:.1f}; higher than {100 - rank:.1f}% of days since {b.index[0]:%Y-%m})",
        f"  NYMO {last.nymo:.1f}  NYSI {last.nysi:.0f}",
        f"  Signal: " + ("PANIC — LOW:HIGH spike in progress, wait for it to fall below 5" if last.low_high >= SAFE_LEVEL
                         else "calm — LOW:HIGH below 5 (the post's 'safe' zone)"),
        f"  Past spikes >=20: " + ", ".join(f"{d:%Y-%m-%d}" for d in spikes[-8:]),
    ]
    p = os.path.join(CACHE, "wsj_latest.json")
    if os.path.exists(p):
        w = json.load(open(p))
        f = lambda k: w[k]["latestClose"]
        lines.append(f"Official NYSE (WSJ, {w['timestamp']}, latest; intraday if market open): "
                     f"adv {f('advances'):.0f} dec {f('declines'):.0f} highs {f('newhighs'):.0f} "
                     f"lows {f('newlows'):.0f} LOW:HIGH {f('newlows') / max(f('newhighs'), 1):.1f}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-update", action="store_true", help="skip downloading, use cache")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--discord", action="store_true", help="post to Discord when the data date changes")
    a = ap.parse_args()
    csv = os.path.join(OUT, "nyse_panic.csv")
    before = pd.read_csv(csv, index_col=0).index[-1] if os.path.exists(csv) else None
    if not a.no_update:
        update_all(a.workers)
    b = compute_breadth()
    spy = pd.read_csv(os.path.join(CACHE, "SPY.csv"), index_col=0, parse_dates=True)
    if b.index[-1] < pd.Timestamp.today().normalize() - pd.Timedelta(days=5):
        sys.exit(f"stale data: latest date {b.index[-1]:%Y-%m-%d}")
    text = summary(b)
    png = draw(b, spy)
    print(text)
    print("chart:", png)
    if a.discord and f"{b.index[-1]:%Y-%m-%d}" != before:
        from pathlib import Path
        from notify_discord import post, webhook_url
        if not webhook_url():
            sys.exit("no Discord webhook configured")
        post(webhook_url(), "**NYSE 恐慌指标** (新低/新高比)\n```\n" + text + "\n```", Path(png))
        print("posted to Discord")


if __name__ == "__main__":
    main()
