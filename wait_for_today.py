#!/usr/bin/env python3
"""轮询源站，直到当天这一期出现再返回，避免定时器跑在源站发布之前而静默漏推。

08:30 开始每分钟检查，发现当天内容立即返回，10:00 截止。
过了截止时间还没出现就明确失败，让 workflow 变红，而不是像以前那样打印一行就退出 0。
"""

import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

FEED_URL = "https://daily.juya.uk/rss.xml"
TZ = ZoneInfo("Asia/Shanghai")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))
DEADLINE = os.environ.get("DEADLINE", "10:00")


def latest_issue_date(timeout: float = 30) -> str | None:
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "ai-daily-wait/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        xml = resp.read().decode("utf-8", "ignore")
    first = re.search(r"<item>(.*?)</item>", xml, re.S)
    if not first:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})", first.group(1))
    return m.group(1) if m else None


def main() -> int:
    now = datetime.now(TZ)
    today = now.strftime("%Y-%m-%d")
    hh, mm = (int(x) for x in DEADLINE.split(":"))
    deadline = now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    found = None

    def expired() -> int:
        print(
            "[wait] 已到截止 %s（北京），停止等待和推送，源站最新 %s" % (DEADLINE, found),
            file=sys.stderr,
        )
        return 1

    while True:
        now = datetime.now(TZ)
        if now >= deadline:
            return expired()
        try:
            found = latest_issue_date(timeout=min(30, (deadline - now).total_seconds()))
        except Exception as exc:  # noqa: BLE001
            found = None
            print("[wait] 抓取失败，稍后重试：%s" % exc)

        now = datetime.now(TZ)
        if now >= deadline:
            return expired()
        if found == today:
            print("[wait] 源站已发布 %s，继续构建" % today)
            return 0

        remain = max(1, int((deadline - now).total_seconds()))
        print(
            "[wait] 源站最新 %s，等 %s 发布（每 %d 秒重试，截止 %s）"
            % (found, today, POLL_SECONDS, DEADLINE),
            flush=True,
        )
        time.sleep(min(POLL_SECONDS, remain))


if __name__ == "__main__":
    sys.exit(main())
