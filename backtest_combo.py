#!/usr/bin/env python3
"""参与度 + RSI / GMMA / 一目均衡表 的组合事件研究（纳指100 ^NDX，2010 起）。

参数取牛牛默认：RSI 6/12/24（Wilder）、GMMA 短组 3-15、长组 30-60、一目 9/26/52。
用法: python3 backtest_combo.py   (依赖 backtest.py 的价格缓存)
"""
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

import backtest as bt

HERE = Path(__file__).resolve().parent
CACHE_OHLC = HERE / "cache_ndx_ohlc.csv"
SHORT = (3, 5, 8, 10, 12, 15)
LONG = (30, 35, 40, 45, 50, 60)
LOOKBACK = 10  # "近期出现过便宜/超卖" 的回看窗口（交易日）


def ndx_ohlc() -> pd.DataFrame:
    if CACHE_OHLC.exists():
        return pd.read_csv(CACHE_OHLC, index_col=0, parse_dates=True)
    df = yf.download("^NDX", start=bt.WARMUP, auto_adjust=True, progress=False)
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df[["Open", "High", "Low", "Close"]].dropna()
    df.to_csv(CACHE_OHLC)
    return df


def rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    hi, lo = df.High, df.Low
    tenkan = (hi.rolling(9).max() + lo.rolling(9).min()) / 2
    kijun = (hi.rolling(26).max() + lo.rolling(26).min()) / 2
    a = ((tenkan + kijun) / 2).shift(26)
    b = ((hi.rolling(52).max() + lo.rolling(52).min()) / 2).shift(26)
    return pd.DataFrame({"tenkan": tenkan, "kijun": kijun, "cloud_top": pd.concat([a, b], axis=1).max(axis=1), "cloud_bot": pd.concat([a, b], axis=1).min(axis=1)})


def recent(cond: pd.Series, n: int = LOOKBACK) -> pd.Series:
    return cond.rolling(n, min_periods=1).max().astype(bool)


def cross_up(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a > b) & (a.shift(1) <= b.shift(1))


def main() -> None:
    px, _ = bt.load_prices()
    br = bt.breadth(px).loc[bt.START :]
    ohlc = ndx_ohlc().loc[bt.START :].reindex(br.index).ffill()
    c = ohlc.Close
    ok = br["v20"] >= bt.MIN_VALID

    r6, r12 = rsi(c, 6), rsi(c, 12)
    ich = ichimoku(ohlc)
    short = pd.concat([c.ewm(span=n, adjust=False).mean() for n in SHORT], axis=1)
    long_ = pd.concat([c.ewm(span=n, adjust=False).mean() for n in LONG], axis=1)
    gm_s, gm_l = short.mean(axis=1), long_.mean(axis=1)

    cheap = ok & (br.b20 <= 20) & (br.b50 <= 30)  # 前一轮的"便宜"定义
    cheap_strict = ok & (br.b50 <= 20)
    oversold = r6 < 30
    below_cloud = c < ich.cloud_bot

    sig = {
        "【对照】RSI6<30 单独": oversold,
        "【对照】跌出云下 且 RSI6<30": below_cloud & oversold,
        "A 便宜(b20≤20&b50≤30)": cheap,
        "B 便宜 且 当天 RSI6<30": cheap & oversold,
        "C 便宜 且 RSI12<35": cheap & (r12 < 35),
        "D 近期便宜 → 收盘重新站上基准线(Kijun)": recent(cheap) & cross_up(c, ich.kijun),
        "E 近期便宜 → 转换线上穿基准线(Tenkan>Kijun)": recent(cheap) & cross_up(ich.tenkan, ich.kijun),
        "F 近期便宜 → 收盘站上云顶": recent(cheap) & cross_up(c, ich.cloud_top),
        "G 近期便宜 → GMMA 短组均值上穿长组": recent(cheap, 20) & cross_up(gm_s, gm_l),
        "H 近期便宜 且 近期 RSI6<30 → 站上 Kijun": recent(cheap) & recent(oversold) & cross_up(c, ich.kijun),
        "I 严格便宜(b50≤20) → 站上 Kijun": recent(cheap_strict) & cross_up(c, ich.kijun),
    }

    base = {h: (c.shift(-h) / c - 1).dropna() for h in bt.HORIZONS}
    lines = [f"区间 {br.index[0]:%Y-%m-%d} ~ {br.index[-1]:%Y-%m-%d}；信号间隔 ≥{bt.COOLDOWN} 交易日；『近期』= 过去 {LOOKBACK} 个交易日内（G 为 20）。", ""]
    lines += ["| 信号 | 次数 | 20日 均值/胜率 | 60日 均值/中位/胜率 | 120日 均值/胜率 | 60日内最大浮亏 均值/最差 |", "|---|---|---|---|---|---|"]
    lines.append(
        f"| **基准：任意一天** | {len(c)} | {base[20].mean()*100:.1f}% / {(base[20]>0).mean()*100:.0f}% | "
        f"{base[60].mean()*100:.1f}% / {base[60].median()*100:.1f}% / {(base[60]>0).mean()*100:.0f}% | {base[120].mean()*100:.1f}% / {(base[120]>0).mean()*100:.0f}% | — |"
    )
    events = {}
    for name, cond in sig.items():
        d = bt.first_triggers(cond.fillna(False))
        events[name] = d
        s = bt.forward_stats(c, d)
        lines.append(
            f"| {name} | {len(d)} | {s[20][1]:.1f}% / {s[20][3]:.0f}% | {s[60][1]:.1f}% / {s[60][2]:.1f}% / {s[60][3]:.0f}% | "
            f"{s[120][1]:.1f}% / {s[120][3]:.0f}% | {s['mae60'][0]:.1f}% / {s['mae60'][1]:.1f}% |"
        )
    lines += ["", "## 触发日期"] + [f"- {k}: " + (", ".join(x.strftime("%Y-%m-%d") for x in v) or "无") for k, v in events.items()]

    # 当前状态
    last = br.index[-1]
    lines += ["", f"## 当前状态（{last:%Y-%m-%d}）",
              f"- 参与度 b20/b50/b100: {br.b20.iloc[-1]:.1f} / {br.b50.iloc[-1]:.1f} / {br.b100.iloc[-1]:.1f}",
              f"- RSI 6/12: {r6.iloc[-1]:.1f} / {r12.iloc[-1]:.1f}",
              f"- 收盘 {c.iloc[-1]:.0f} | Kijun {ich.kijun.iloc[-1]:.0f} | 云 {ich.cloud_bot.iloc[-1]:.0f}~{ich.cloud_top.iloc[-1]:.0f}",
              f"- GMMA 短组均值 {gm_s.iloc[-1]:.0f} vs 长组均值 {gm_l.iloc[-1]:.0f}"]
    report = "\n".join(lines)
    (HERE / "backtest_combo_report.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
