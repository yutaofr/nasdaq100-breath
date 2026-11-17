#!/usr/bin/env python3
"""周线级别事件研究：参与度(10/20/50周) + RSI + 一目 + GMMA，标的 ^NDX。
周 K = 周五收盘（W-FRI）；参数同牛牛默认（RSI 6/12，一目 9/26/52，GMMA 3-15 / 30-60）。
"""
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import backtest_combo as bc

HERE = Path(__file__).resolve().parent
HZ = (4, 13, 26, 52)      # 周
COOL = 4                  # 同一信号两次触发至少间隔 4 周
MIN_VALID = 60


def weekly_breadth(px: pd.DataFrame) -> pd.DataFrame:
    w = px.resample("W-FRI").last()
    out = {}
    for n in (10, 20, 50):
        ma = w.rolling(n).mean()
        valid = ma.notna() & w.notna()
        out[f"wb{n}"] = ((w > ma) & valid).sum(axis=1) / valid.sum(axis=1) * 100
        out[f"v{n}"] = valid.sum(axis=1)
    return pd.DataFrame(out)


def first_triggers(cond: pd.Series) -> list:
    last, out = -10**9, []
    for i, hit in enumerate(cond.fillna(False).values):
        if hit and i - last >= COOL:
            out.append(cond.index[i]); last = i
    return out


def fstats(c: pd.Series, dates: list) -> dict:
    res, pos = {}, c.index
    for h in HZ:
        r = np.array([c.iloc[pos.get_loc(d) + h] / c.loc[d] - 1 for d in dates if pos.get_loc(d) + h < len(c)])
        res[h] = (len(r), r.mean() * 100, np.median(r) * 100, (r > 0).mean() * 100) if len(r) else (0, np.nan, np.nan, np.nan)
    mae = [c.iloc[pos.get_loc(d): pos.get_loc(d) + 14].min() / c.loc[d] - 1 for d in dates if pos.get_loc(d) + 1 < len(c)]
    res["mae"] = (np.mean(mae) * 100, np.min(mae) * 100) if mae else (np.nan, np.nan)
    return res


def row(name, c, dates):
    s = fstats(c, dates)
    cells = " | ".join(f"{s[h][1]:.1f}% / {s[h][3]:.0f}%" if s[h][0] else "—" for h in HZ)
    return f"| {name} | {len(dates)} | {cells} | {s['mae'][0]:.1f}% / {s['mae'][1]:.1f}% |"


def main():
    px, _ = bt.load_prices()
    wb = weekly_breadth(px).loc["2010-01-01":]
    o = bc.ndx_ohlc()
    wk = pd.DataFrame({"Open": o.Open.resample("W-FRI").first(), "High": o.High.resample("W-FRI").max(),
                       "Low": o.Low.resample("W-FRI").min(), "Close": o.Close.resample("W-FRI").last()}).dropna()
    wk = wk.reindex(wb.index.union(wk.index)).loc["2010-01-01":]
    wb = wb.reindex(wk.index)
    c = wk.Close
    ok = wb.v10 >= MIN_VALID

    r6, r12 = bc.rsi(c, 6), bc.rsi(c, 12)
    ich = bc.ichimoku(wk)
    gs = pd.concat([c.ewm(span=n, adjust=False).mean() for n in bc.SHORT], axis=1).mean(axis=1)
    gl = pd.concat([c.ewm(span=n, adjust=False).mean() for n in bc.LONG], axis=1).mean(axis=1)
    cloud = c < ich.cloud_bot
    valid_ich = ich.cloud_bot.notna()

    L = [f"周线样本 {wk.index[0]:%Y-%m-%d} ~ {wk.index[-1]:%Y-%m-%d}，{len(wk)} 周；一目云需 78 周预热，云相关信号从约 2011 年起。",
         "", "参与度分布（周线，10/20/50周线上方占比）分位数：",
         "```", wb[["wb10", "wb20", "wb50"]].describe(percentiles=[.05, .1, .2, .5]).round(1).to_string(), "```", ""]

    sig = {
        "参与度 wb10≤20%": ok & (wb.wb10 <= 20),
        "参与度 wb10≤30%": ok & (wb.wb10 <= 30),
        "参与度 wb20≤30%": ok & (wb.wb20 <= 30),
        "参与度 wb20≤40%": ok & (wb.wb20 <= 40),
        "参与度 wb10≤30 且 wb20≤40": ok & (wb.wb10 <= 30) & (wb.wb20 <= 40),
        "【对照】周RSI6<30": r6 < 30,
        "【对照】周RSI12<40": r12 < 40,
        "【对照】周线跌出云下": valid_ich & cloud,
        "【对照】云下 且 周RSI12<40": valid_ich & cloud & (r12 < 40),
        "★ 三者：wb10≤30&wb20≤40 且 云下 且 周RSI12<40": valid_ich & ok & (wb.wb10 <= 30) & (wb.wb20 <= 40) & cloud & (r12 < 40),
        "参与度便宜 且 周RSI12<40": ok & (wb.wb10 <= 30) & (wb.wb20 <= 40) & (r12 < 40),
        "参与度便宜 且 云下": valid_ich & ok & (wb.wb10 <= 30) & (wb.wb20 <= 40) & cloud,
        "近期(4周)便宜 → 周收盘站上基准线": (ok & (wb.wb10 <= 30) & (wb.wb20 <= 40)).rolling(4, min_periods=1).max().astype(bool) & bc.cross_up(c, ich.kijun),
        "近期(4周)便宜 → GMMA短组上穿长组": (ok & (wb.wb10 <= 30) & (wb.wb20 <= 40)).rolling(8, min_periods=1).max().astype(bool) & bc.cross_up(gs, gl),
    }
    base = {h: (c.shift(-h) / c - 1).dropna() for h in HZ}
    L += ["| 信号 | 次数 | " + " | ".join(f"{h}周 均值/胜率" for h in HZ) + " | 13周内最大浮亏 均值/最差 |", "|" + "---|" * (3 + len(HZ)),
          f"| **基准：任意一周** | {len(c)} | " + " | ".join(f"{base[h].mean()*100:.1f}% / {(base[h]>0).mean()*100:.0f}%" for h in HZ) + " | — |"]
    ev = {}
    for k, v in sig.items():
        ev[k] = first_triggers(v)
        L.append(row(k, c, ev[k]) if ev[k] else f"| {k} | 0 | — | — | — | — | — |")

    # 拆分：云下且RSI 的触发里，参与度便宜 vs 不便宜
    b = ev["【对照】云下 且 周RSI12<40"]
    cheap = ok & (wb.wb10 <= 30) & (wb.wb20 <= 40)
    L += ["", "## 拆分：『云下 且 周RSI12<40』触发周，参与度便宜 vs 不便宜",
          "| 分组 | 次数 | " + " | ".join(f"{h}周 均值/胜率" for h in HZ) + " | 13周内最大浮亏 均值/最差 |", "|" + "---|" * (3 + len(HZ)),
          row("参与度便宜", c, [d for d in b if cheap.loc[d]]), row("参与度不便宜", c, [d for d in b if not cheap.loc[d]])]
    L += ["", "## 触发日期（周五）"] + [f"- {k}: " + (", ".join(x.strftime('%Y-%m-%d') for x in v) or "无") for k, v in ev.items()]
    last = wk.index[-1]
    L += ["", f"## 当前周线状态（{last:%Y-%m-%d}）",
          f"- 参与度 wb10/wb20/wb50: {wb.wb10.iloc[-1]:.1f} / {wb.wb20.iloc[-1]:.1f} / {wb.wb50.iloc[-1]:.1f}",
          f"- 周RSI 6/12: {r6.iloc[-1]:.1f} / {r12.iloc[-1]:.1f}",
          f"- 周收盘 {c.iloc[-1]:.0f} | Kijun {ich.kijun.iloc[-1]:.0f} | 云 {ich.cloud_bot.iloc[-1]:.0f}~{ich.cloud_top.iloc[-1]:.0f}",
          f"- GMMA 短组均值 {gs.iloc[-1]:.0f} vs 长组均值 {gl.iloc[-1]:.0f}"]
    out = "\n".join(L)
    (HERE / "backtest_weekly_report.md").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
