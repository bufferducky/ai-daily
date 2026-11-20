# 154 VPS 日报触发器

用已有的 Bash、curl、jq、flock 和 crond 运行，不依赖宿主机 Python。北京时间每天 08:30—09:55 每 5 分钟检查一次，触发工作流时传入 `automatic=true`。

| VPS 路径 | 用途 |
|---|---|
| `/opt/ai-daily-trigger/trigger.sh` | 查询回执、运行状态并请求启动 |
| `/opt/ai-daily-trigger/run.sh` | 本地文件锁，避免脚本重入 |
| `/etc/cron.d/ai-daily-trigger` | 明确设置 `CRON_TZ=Asia/Shanghai` 的定时任务 |
| `/etc/ai-daily-trigger/github.curl` | GitHub 认证配置，仅 root 可读，不入库 |
| `/var/lib/ai-daily-trigger/` | 当天成功缓存、最近请求时间和锁文件 |
| `/var/log/ai-daily-trigger.log` | 每次检查和请求的结果 |
| `/etc/logrotate.d/ai-daily-trigger` | 日志每天轮转，保留 7 份 |

每次先查当天 `notified-YYYY-MM-DD` 成功回执，确认后缓存当天日期；当天剩余检查直接跳过。没有回执时查询最近 100 次工作流运行，只要有尚未结束的任务就不重复启动。没有任务才 POST 到 `daily.yml` 的 workflow dispatch 接口。请求已受理后保留 2 分钟缓冲，避免 GitHub 运行列表同步延迟造成重复请求。

API 查询或解析失败时脚本退出失败，下一轮再试。成功回执由 Actions 发送通知后保存；VPS 的 HTTP 请求成功不等于通知成功。

## 检查

```bash
ssh 154-vps 'sudo /opt/ai-daily-trigger/run.sh --check-only'
ssh 154-vps 'sudo tail -n 30 /var/log/ai-daily-trigger.log'
ssh 154-vps 'sudo cat /etc/cron.d/ai-daily-trigger'
```

`--check-only` 可以在窗口外执行，只查询 GitHub，不触发工作流，也不更新本地成功缓存。

## 安装与更新

将此目录的 `trigger.sh`、`run.sh` 安装到 `/opt/ai-daily-trigger/`，root 所有、权限 0755；安装 cron 为 `/etc/cron.d/ai-daily-trigger`（0644），logrotate 为 `/etc/logrotate.d/ai-daily-trigger`（0644）。脚本中的 GitHub 仓库默认值为 `bufferducky/ai-daily`。

首次安装须创建 `/etc/ai-daily-trigger/` 和 `/var/lib/ai-daily-trigger/`（root 所有、0700）。`github.curl` 格式为 `header = "Authorization: Bearer <凭据>"`，权限 0600；凭据需能读取该仓库 Actions 运行、Artifacts，并触发工作流。飞书 Webhook 仍只保存在 GitHub Secrets，不复制到 VPS。

更新脚本和凭据后执行只读检查。crond 每次读取 `/etc/cron.d`，不需要重启其他服务。

## 停用

```bash
sudo mv /etc/cron.d/ai-daily-trigger /etc/ai-daily-trigger/cron.disabled
```

这只停用 VPS 触发器。GitHub cron 的备用入口仍可触发，但不保证准时。
