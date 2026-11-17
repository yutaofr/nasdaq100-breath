#!/usr/bin/env python3
"""把最新的 NDX100 参与度发到 Discord (webhook)。

webhook 地址从环境变量 DISCORD_WEBHOOK_URL 或同目录的 .discord_webhook 文件读取（不要写进代码/提交到 git）。
用法:
  python3 notify_discord.py            # 发送最新数据 + 图
  python3 notify_discord.py --dry-run  # 只在本地生成消息和图，不发送
  python3 notify_discord.py --error "原因"  # 发送失败提醒
"""
import json
import os
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests

HERE = Path(__file__).resolve().parent
CSV = HERE / "ndx_breadth_ytd.csv"
PNG = HERE / "ndx_breadth_ytd.png"
WEBHOOK_FILE = HERE / ".discord_webhook"
COLORS = {"pct20": "#e5484d", "pct50": "#3e63dd", "pct100": "#30a46c"}
LABELS = {"pct20": "> MA20", "pct50": "> MA50", "pct100": "> MA100"}


def webhook_url() -> str:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url and WEBHOOK_FILE.exists():
        url = WEBHOOK_FILE.read_text().strip()
    return url


def build_chart(df: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(9, 4), dpi=130)
    for col in COLORS:
        ax.plot(df.index, df[col], color=COLORS[col], label=LABELS[col], linewidth=1.4)
    ax.axhline(50, color="#888", linewidth=0.8, linestyle="--")
    ax.set_ylim(0, 100)
    ax.set_ylabel("% of NDX100 constituents")
    ax.set_title(f"NDX100 breadth YTD (as of {df.index[-1]:%Y-%m-%d})")
    ax.grid(alpha=0.25)
    ax.legend(loc="upper right", ncol=3, frameon=False)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(PNG)
    plt.close(fig)


def build_message(df: pd.DataFrame) -> str:
    last, prev = df.iloc[-1], df.iloc[-2]
    lines = [f"**NDX100 参与度** · {df.index[-1]:%Y-%m-%d}（站上均线的成分股占比）"]
    for col, n in (("pct20", 20), ("pct50", 50), ("pct100", 100)):
        d = last[col] - prev[col]
        arrow = "▲" if d > 0 else "▼" if d < 0 else "—"
        lines.append(f"`{n:>3}日` **{last[col]:.1f}%**  {arrow} {abs(d):.1f}pt  （样本 {int(last[f'n{n}'])} 只）")
    return "\n".join(lines)


def post(url: str, content: str, png: Path | None) -> None:
    payload = {"content": content}
    if png:
        with open(png, "rb") as f:
            r = requests.post(url, data={"payload_json": json.dumps(payload)}, files={"file": (png.name, f, "image/png")}, timeout=30)
    else:
        r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()


def main() -> None:
    args = sys.argv[1:]
    dry = "--dry-run" in args
    if "--error" in args:
        content, png = f"⚠️ NDX100 参与度更新失败：{args[args.index('--error') + 1]}", None
    else:
        df = pd.read_csv(CSV, index_col="date", parse_dates=True)
        build_chart(df)
        content, png = build_message(df), PNG
    if dry:
        print(content, f"\n[图: {png}]" if png else "")
        return
    url = webhook_url()
    if not url:
        sys.exit("未找到 webhook：设置 DISCORD_WEBHOOK_URL 或写入 .discord_webhook")
    post(url, content, png)
    print("已发送到 Discord")


if __name__ == "__main__":
    main()
