#!/usr/bin/env python3
"""补测：参与度便宜 + 跌出一目云下 + RSI6<30 三者同时成立，并拆分看参与度的增量贡献。"""
from pathlib import Path

import numpy as np
import pandas as pd

import backtest as bt
import backtest_combo as bc

HERE = Path(__file__).resolve().parent


def stats(c, dates):
    s = bt.forward_stats(c, dates)
    return s


def row(name, c, dates):
    s = stats(c, dates)
    return (f"| {name} | {len(dates)} | {s[20][1]:.1f}% / {s[20][3]:.0f}% | {s[60][1]:.1f}% / {s[60][2]:.1f}% / {s[60][3]:.0f}% | "
            f"{s[120][1]:.1f}% / {s[120][3]:.0f}% | {s['mae60'][0]:.1f}% / {s['mae60'][1]:.1f}% |")


def main():
    px, _ = bt.load_prices()
    br = bt.breadth(px).loc[bt.START:]
    ohlc = bc.ndx_ohlc().loc[bt.START:].reindex(br.index).ffill()
    c = ohlc.Close
    ok = br["v20"] >= bt.MIN_VALID
    r6 = bc.rsi(c, 6)
    ich = bc.ichimoku(ohlc)
    cloud = c < ich.cloud_bot
    os_ = r6 < 30
    cheap = ok & (br.b20 <= 20) & (br.b50 <= 30)
    cheap2 = ok & (br.b50 <= 20)

    base_sig = cloud & os_                       # 不看参与度的基础信号
    trig = lambda s: bt.first_triggers(s.fillna(False))

    sets = {
        "云下 且 RSI6<30（不看参与度）": trig(base_sig),
        "★ 三者：便宜 且 云下 且 RSI6<30": trig(base_sig & cheap),
        "★ 三者（严格便宜 b50≤20）": trig(base_sig & cheap2),
        "便宜 且 云下（不要求 RSI）": trig(cheap & cloud),
    }
    # 拆分：基础信号的那些日子里，参与度便宜 vs 不便宜
    base_dates = sets["云下 且 RSI6<30（不看参与度）"]
    with_b = [d for d in base_dates if cheap.loc[d]]
    without_b = [d for d in base_dates if not cheap.loc[d]]
    sets["　拆分①：基础信号日 且 参与度便宜"] = with_b
    sets["　拆分②：基础信号日 但 参与度不便宜"] = without_b

    base = {h: (c.shift(-h) / c - 1).dropna() for h in bt.HORIZONS}
    L = ["| 信号 | 次数 | 20日 均值/胜率 | 60日 均值/中位/胜率 | 120日 均值/胜率 | 60日内最大浮亏 均值/最差 |", "|---|---|---|---|---|---|"]
    L.append(f"| **基准：任意一天** | {len(c)} | {base[20].mean()*100:.1f}% / {(base[20]>0).mean()*100:.0f}% | "
             f"{base[60].mean()*100:.1f}% / {base[60].median()*100:.1f}% / {(base[60]>0).mean()*100:.0f}% | {base[120].mean()*100:.1f}% / {(base[120]>0).mean()*100:.0f}% | — |")
    for k, v in sets.items():
        L.append(row(k, c, v))
    L += ["", "## 触发日期"] + [f"- {k}: " + (", ".join(x.strftime('%Y-%m-%d') for x in v) or "无") for k, v in sets.items()]

    # 逐事件：三者信号的 60 日收益，看是否被个别年份主导
    ev = sets["★ 三者：便宜 且 云下 且 RSI6<30"]
    L += ["", "## 三者信号逐事件（触发日 → 20/60/120 日收益，60 日内最大浮亏）"]
    for d in ev:
        i = c.index.get_loc(d)
        f = lambda h: f"{(c.iloc[i+h]/c.iloc[i]-1)*100:+.1f}%" if i + h < len(c) else "—"
        w = c.iloc[i:i+61]
        L.append(f"- {d:%Y-%m-%d}  b20={br.b20.loc[d]:.0f} b50={br.b50.loc[d]:.0f} RSI6={r6.loc[d]:.0f} | {f(20)} / {f(60)} / {f(120)} | 浮亏 {(w.min()/w.iloc[0]-1)*100:.1f}%")
    out = "\n".join(L)
    (HERE / "backtest_triple_report.md").write_text(out, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
