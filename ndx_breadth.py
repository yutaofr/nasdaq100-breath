#!/usr/bin/env python3
"""NDX100 参与度(breadth)：当前成分股中收盘价站上 20/50/100 日均线的占比，输出 YTD，
并生成可直接粘贴进富途牛牛 Python 自编指标的 ndx_breadth_futu.py。

用法: python3 ndx_breadth.py
更新数据: 重新运行，再把 ndx_breadth_futu.py 的内容粘进牛牛指标编辑器。
"""
import json
import re
import sys
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

WINDOWS = (20, 50, 100)
YEAR_START = f"{date.today().year}-01-01"
WARMUP_START = f"{date.today().year - 1}-04-01"  # 留足 100 日均线预热
HERE = Path(__file__).resolve().parent  # 输出固定在脚本所在目录，和运行时的 cwd 无关
OUT_CSV = HERE / "ndx_breadth_ytd.csv"
OUT_INDICATOR = HERE / "ndx_breadth_futu.py"
MIN_TICKERS = 95  # 有数据的成分股少于此数视为下载失败，不覆盖旧文件
MAX_STALE_DAYS = 6  # 最新数据距今超过此天数视为异常（长假也够用）


CONSTITUENTS_CACHE = HERE / "constituents.json"
CACHE_MAX_AGE_DAYS = 30  # 缓存超过此天数且在线源都失败时才算失败


def _from_nasdaq() -> list[str]:
    req = urllib.request.Request(
        "https://api.nasdaq.com/api/quote/list-type/nasdaq100",
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )
    rows = json.load(urllib.request.urlopen(req, timeout=30))["data"]["data"]["rows"]
    return [r["symbol"] for r in rows]


def _from_invesco_qqq() -> list[str]:
    """QQQ 跟踪 NDX100，持仓即成分股（取普通股和 ADR，剔除现金/期货）。"""
    req = urllib.request.Request(
        "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/holdings/fund"
        "?idType=ticker&interval=monthly&productType=ETF",
        headers={"User-Agent": "ndx-breadth-script/1.0"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=30))
    return [h["ticker"].strip() for h in data["holdings"]
            if h.get("securityTypeCode") in ("COM", "ADR", "DRNY") and h.get("ticker")]


def fetch_constituents() -> list[str]:
    """在线源依次尝试 (nasdaq → invesco QQQ 持仓)，成功则刷新缓存；全失败时用本地缓存。"""
    for name, fn in (("nasdaq", _from_nasdaq), ("invesco_qqq", _from_invesco_qqq)):
        try:
            syms = fn()
            if 95 <= len(syms) <= 110:
                syms = [s.replace(".", "-") for s in syms]  # yfinance 用 BRK-B 这种写法
                CONSTITUENTS_CACHE.write_text(json.dumps(
                    {"updated": date.today().isoformat(), "source": name, "tickers": syms}, indent=1))
                print(f"成分股来源: {name}")
                return syms
            print(f"成分股来源 {name} 数量异常: {len(syms)}")
        except Exception as e:  # noqa: BLE001
            print(f"成分股来源 {name} 失败: {e!r}")
    cache = json.loads(CONSTITUENTS_CACHE.read_text())
    age = (date.today() - date.fromisoformat(cache["updated"])).days
    if age > CACHE_MAX_AGE_DAYS:
        sys.exit(f"在线成分股来源全部失败，本地缓存已 {age} 天未更新，请检查")
    print(f"成分股来源: 本地缓存 ({cache['updated']}, {age} 天前)")
    return cache["tickers"]


def compute_breadth(close: pd.DataFrame) -> pd.DataFrame:
    out = {}
    for n in WINDOWS:
        ma = close.rolling(n).mean()
        valid = ma.notna() & close.notna()  # 上市不足 n 日的股票当天不计入分母
        above = (close > ma) & valid
        out[f"pct{n}"] = above.sum(axis=1) / valid.sum(axis=1) * 100
        out[f"n{n}"] = valid.sum(axis=1)
    return pd.DataFrame(out)


def main() -> None:
    tickers = fetch_constituents()
    print(f"成分股 {len(tickers)} 只")
    px = yf.download(tickers, start=WARMUP_START, auto_adjust=True, progress=False, threads=True)["Close"]
    px = px.dropna(how="all")
    missing = [t for t in tickers if t not in px.columns or px[t].dropna().empty]
    if missing:
        print("无数据:", missing)
    if len(tickers) - len(missing) < MIN_TICKERS:
        sys.exit(f"有数据的成分股只有 {len(tickers) - len(missing)} 只，疑似下载失败，保留旧文件")

    breadth = compute_breadth(px).loc[YEAR_START:]
    if breadth.empty or (date.today() - breadth.index[-1].date()).days > MAX_STALE_DAYS:
        sys.exit("最新数据过旧或为空，保留旧文件")
    breadth.round(2).to_csv(OUT_CSV, index_label="date")
    print(breadth.tail(5).round(1).to_string())

    rows = [
        f"    {d.strftime('%Y%m%d')}: ({r.pct20:.1f}, {r.pct50:.1f}, {r.pct100:.1f}),"
        for d, r in breadth.iterrows()
    ]
    asof = breadth.index[-1].strftime("%Y-%m-%d")
    with open(OUT_INDICATOR, "w", encoding="utf-8") as f:
        f.write(INDICATOR_TEMPLATE.replace("@@ASOF@@", asof).replace("@@ROWS@@", "\n".join(rows)))
    print(f"已写入 {OUT_CSV} 和 {OUT_INDICATOR} (数据截至 {asof})")


INDICATOR_TEMPLATE = '''# NDX100 参与度: 当前成分股站上 20/50/100 日均线的占比(%) | 数据截至 @@ASOF@@ (yfinance)
# 仅适用于日K; 数据为预计算内嵌, 需定期重跑 ndx_breadth.py 更新
indicator("NDXB", "NDX Breadth", False, "NDX100 components above MA20/50/100 (%)")
from fdatetime import year, month, day
from ftool import iff

# 日期 YYYYMMDD: (>MA20, >MA50, >MA100)
DATA = {
@@ROWS@@
}


def pick(idx):
    key = year() * 10000 + month() * 100 + day()
    out = float("nan")
    for d, v in DATA.items():
        out = iff(key == d, v[idx], out)
    return out


if __name__ == "__main__":
    plot("MA20", pick(0), color=Color.red)
    plot("MA50", pick(1), color=Color.blue)
    plot("MA100", pick(2), color=Color.green)
    plot("50", 50, color=Color.white)

    output_parameter(MA20=pick(0), MA50=pick(1), MA100=pick(2))
'''

if __name__ == "__main__":
    main()
