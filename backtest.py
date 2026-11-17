#!/usr/bin/env python3
"""参与度低位 → 纳指100 后续收益 的事件研究。

用当前成分股回算 2010 起的历史参与度（有幸存者偏差，见输出末尾说明）。
用法: python3 backtest.py   (首次需下载约 15 年行情，约 1-2 分钟)
"""
import json
import urllib.request
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf

HERE = Path(__file__).resolve().parent
START, WARMUP = "2010-01-01", "2009-01-01"
MIN_VALID = 60  # 当天有效成分股少于此数不产生信号（早年上市公司少）
HORIZONS = (5, 20, 60, 120)  # 交易日
COOLDOWN = 20  # 同一信号两次触发至少间隔的交易日，避免一波下跌重复计数
CACHE_PX, CACHE_NDX = HERE / "cache_prices.csv", HERE / "cache_ndx.csv"


def constituents() -> list[str]:
    req = urllib.request.Request(
        "https://api.nasdaq.com/api/quote/list-type/nasdaq100",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    rows = json.load(urllib.request.urlopen(req, timeout=30))["data"]["data"]["rows"]
    return [r["symbol"].replace(".", "-") for r in rows]


def load_prices() -> tuple[pd.DataFrame, pd.Series]:
    if CACHE_PX.exists() and CACHE_NDX.exists():
        return (pd.read_csv(CACHE_PX, index_col=0, parse_dates=True),
                pd.read_csv(CACHE_NDX, index_col=0, parse_dates=True).squeeze("columns"))
    tk = constituents()
    px = yf.download(tk, start=WARMUP, auto_adjust=True, progress=False, threads=True)["Close"].dropna(how="all")
    ndx = yf.download("^NDX", start=WARMUP, auto_adjust=True, progress=False)["Close"].squeeze()
    px.to_csv(CACHE_PX)
    ndx.to_csv(CACHE_NDX)
    return px, ndx


def breadth(px: pd.DataFrame) -> pd.DataFrame:
    out = {}
    for n in (20, 50, 100):
        ma = px.rolling(n).mean()
        valid = ma.notna() & px.notna()
        out[f"b{n}"] = ((px > ma) & valid).sum(axis=1) / valid.sum(axis=1) * 100
        out[f"v{n}"] = valid.sum(axis=1)
    return pd.DataFrame(out)


def first_triggers(cond: pd.Series) -> list[pd.Timestamp]:
    """条件首次成立的日子；两次触发之间至少间隔 COOLDOWN 个交易日。"""
    idx, last, out = cond.index, -10**9, []
    for i, hit in enumerate(cond.values):
        if hit and i - last >= COOLDOWN:
            out.append(idx[i])
            last = i
    return out


def forward_stats(ndx: pd.Series, dates: list[pd.Timestamp]) -> dict:
    res = {}
    pos = ndx.index
    for h in HORIZONS:
        rets = []
        for d in dates:
            i = pos.get_loc(d)
            if i + h < len(ndx):
                rets.append(ndx.iloc[i + h] / ndx.iloc[i] - 1)
        r = np.array(rets)
        res[h] = (len(r), r.mean() * 100 if len(r) else np.nan, np.median(r) * 100 if len(r) else np.nan, (r > 0).mean() * 100 if len(r) else np.nan)
    # 触发后 60 日内的最大回撤（从触发日收盘算起的最低点）
    mae = []
    for d in dates:
        i = pos.get_loc(d)
        w = ndx.iloc[i : i + 61]
        if len(w) > 1:
            mae.append(w.min() / w.iloc[0] - 1)
    res["mae60"] = (np.mean(mae) * 100 if mae else np.nan, np.min(mae) * 100 if mae else np.nan)
    return res


def main() -> None:
    px, ndx = load_prices()
    br = breadth(px).loc[START:]
    ndx = ndx.loc[START:].reindex(br.index).ffill()
    ok = br["v20"] >= MIN_VALID

    # 无条件基准：任意一天买入
    base = {h: (ndx.shift(-h) / ndx - 1).dropna() for h in HORIZONS}

    signals = {
        "b20 ≤ 10%": ok & (br.b20 <= 10),
        "b20 ≤ 20%": ok & (br.b20 <= 20),
        "b50 ≤ 20%": ok & (br.b50 <= 20),
        "b50 ≤ 30%": ok & (br.b50 <= 30),
        "b100 ≤ 30%": ok & (br.b100 <= 30),
        "b100 ≤ 40%": ok & (br.b100 <= 40),
        "b20≤20 且 b50≤30": ok & (br.b20 <= 20) & (br.b50 <= 30),
        "b20≤20 且 b50≤30 且 b100≤40": ok & (br.b20 <= 20) & (br.b50 <= 30) & (br.b100 <= 40),
    }
    # 反弹确认：b20 曾跌破 15%，之后首次重新站上 30%
    washed = (br.b20.rolling(10).min() <= 15) & (br.b20 > 30) & (br.b20.shift(1) <= 30)
    signals["b20 洗盘(≤15%)后回升>30%"] = ok & washed

    lines = [f"样本区间 {br.index[0]:%Y-%m-%d} ~ {br.index[-1]:%Y-%m-%d}，共 {len(br)} 个交易日；信号间隔 ≥{COOLDOWN} 交易日。", ""]
    hdr = "| 信号 | 次数 | " + " | ".join(f"{h}日 均值/中位/胜率" for h in HORIZONS) + " | 60日内最大浮亏 均值/最差 |"
    lines += [hdr, "|" + "---|" * (3 + len(HORIZONS))]
    b = " | ".join(f"{base[h].mean()*100:.1f}% / {base[h].median()*100:.1f}% / {(base[h]>0).mean()*100:.0f}%" for h in HORIZONS)
    lines.append(f"| **基准：任意一天买入** | {len(br)} | {b} | — |")
    events = {}
    for name, cond in signals.items():
        d = first_triggers(cond)
        events[name] = d
        s = forward_stats(ndx, d)
        cells = " | ".join(f"{s[h][1]:.1f}% / {s[h][2]:.1f}% / {s[h][3]:.0f}% (n={s[h][0]})" for h in HORIZONS)
        lines.append(f"| {name} | {len(d)} | {cells} | {s['mae60'][0]:.1f}% / {s['mae60'][1]:.1f}% |")

    lines += ["", "## 触发日期"]
    for name, d in events.items():
        lines.append(f"- {name}: " + (", ".join(x.strftime("%Y-%m-%d") for x in d) or "无"))

    report = "\n".join(lines)
    (HERE / "backtest_report.md").write_text(report, encoding="utf-8")
    print(report)

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 6.5), dpi=120, sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    a1.semilogy(ndx.index, ndx.values, color="#222", linewidth=1)
    for x in events["b20 ≤ 10%"]:
        a1.axvline(x, color="#e5484d", alpha=0.5, linewidth=1)
    for x in events["b50 ≤ 20%"]:
        a1.axvline(x, color="#3e63dd", alpha=0.35, linewidth=1, linestyle="--")
    a1.set_title("NDX with signals: red = b20<=10%, blue dashed = b50<=20%")
    a2.plot(br.index, br.b20, color="#e5484d", linewidth=0.8, label="b20")
    a2.plot(br.index, br.b50, color="#3e63dd", linewidth=0.8, label="b50")
    a2.axhline(20, color="#888", linestyle=":")
    a2.legend(loc="upper right", ncol=2, frameon=False)
    a2.set_ylim(0, 100)
    fig.tight_layout()
    fig.savefig(HERE / "backtest_chart.png")


if __name__ == "__main__":
    main()
