# NDX100 参与度

当前纳指100成分股中，收盘价站上 20 / 50 / 100 日均线的占比（%），YTD。每个交易日自动更新并发到 Discord。

## 文件

| 文件 | 作用 |
| --- | --- |
| `ndx_breadth.py` | 取成分股（见下方“成分股来源”）+ 行情（yfinance），计算占比，输出 csv 和牛牛指标 |
| `constituents.json` | 成分股本地缓存（每次在线取成功时刷新） |
| `notify_discord.py` | 把最新数值和 YTD 图发到 Discord（webhook） |
| `run_daily.sh` | 每日入口：更新数据 → 数据日期有变化才发 Discord；取数失败发失败提醒 |
| `ndx_breadth_ytd.csv` / `.png` | 最新数据 / 最新图 |
| `ndx_breadth_futu.py` | 牛牛 Python 自编指标（可选，不用牛牛可忽略；数据内嵌，更新后需手动重新粘贴） |
| `run.log` | 运行日志 |
| `.discord_webhook` | webhook 地址（权限 600，不要分享/提交） |

## 成分股来源

每次运行按顺序尝试以下来源，第一个成功的被采用：

1. **纳斯达克官方接口** `api.nasdaq.com/api/quote/list-type/nasdaq100`。近期对脚本类请求返回 403，多数情况下会失败。
2. **Invesco QQQ 持仓接口**（QQQ 跟踪 NDX100）。取持仓中的普通股（`COM`）和 ADR（`ADR`、`DRNY`），剔除现金、期货等。目前实际使用的是这个来源。

在线来源取到的名单须通过数量检查（95–110 只），然后写入 `constituents.json`，记录更新日期和来源。

3. **本地缓存** `constituents.json`：两个在线来源都失败时使用。若缓存距上次更新超过 30 天，脚本报错退出，保留旧数据并发失败提醒。

`run.log` 会记录每次实际使用的来源，例如 `成分股来源: invesco_qqq` 或 `成分股来源: 本地缓存 (日期, N 天前)`，可据此判断是否在用缓存。

指数成分的调入/调出会在下一次成功取数时自动体现。

## 每日自动更新

launchd 任务 `com.nasdaq100-breadth.daily`（`~/Library/LaunchAgents/`）：周二至周六 07:00 本机时间（美股周一至周五收盘之后）运行 `run_daily.sh`。电脑睡眠错过的，唤醒后补跑。

- 数据日期有变化 → 发 Discord（三个数值、较前一日变化、YTD 图）。
- 下载失败、有数据的成分股不足 95 只、或最新数据过旧 → 不覆盖旧文件，发失败提醒，原因见 `run.log`。
- 美股休市日数据无变化，不发消息。

```bash
# 停用 / 启用
launchctl bootout gui/$(id -u)/com.nasdaq100-breadth.daily
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nasdaq100-breadth.daily.plist

# 手动更新并发送
./run_daily.sh
# 只看消息不发送
python3 notify_discord.py --dry-run
```

## 已知限制

- **成分股是当前名单**：不含历史调入/调出，早期数据有幸存者偏差。
- **成分股依赖第三方接口**：Nasdaq 或 Invesco 的接口格式或访问策略变化时，脚本会退回缓存；缓存超过 30 天才会停止更新。
- **只含当年（YTD）**：每年年初窗口自动从 1 月 1 日起算。
- **上市不足 100 天的股票**不计入 100 日线的分母（目前约 2 只）；GOOG 与 GOOGL 各算一只。
- **数据来自 yfinance**（非官方接口），偶尔缺数据或延迟；脚本会拒绝明显异常的结果。
- **Mac 需开机/唤醒**才会运行；牛牛指标无法联网或读文件，只能内嵌数据后手动粘贴。
