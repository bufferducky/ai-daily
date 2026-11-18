#!/usr/bin/env python3
"""轮询源站，直到当天这一期出现再返回，避免定时器跑在源站发布之前而静默漏推。

源站实际发布时间在 08:20~10:05 之间漂移，定时任务无法押中某一个时刻，
所以这里不猜时间：抢在最早可能发布之前启动，然后等它出现。
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
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "300"))
DEADLINE = os.environ.get("DEADLINE", "10:30")


def latest_issue_date() -> str | None:
    req = urllib.request.Request(FEED_URL, headers={"User-Agent": "ai-daily-wait/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
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

    while True:
        try:
            found = latest_issue_date()
        except Exception as exc:  # noqa: BLE001
            found = None
            print("[wait] 抓取失败，稍后重试：%s" % exc)

        now = datetime.now(TZ)
        if found == today:
            print("[wait] 源站已发布 %s，继续构建" % today)
            return 0

        if now >= deadline:
            print(
                "[wait] 已过截止 %s（北京），源站最新仍是 %s，今天这一期没等到，放弃推送"
                % (DEADLINE, found),
                file=sys.stderr,
            )
            return 1

        remain = max(1, int((deadline - now).total_seconds()))
        print(
            "[wait] 源站最新 %s，等 %s 发布（每 %d 秒重试，截止 %s）"
            % (found, today, POLL_SECONDS, DEADLINE)
        )
        time.sleep(min(POLL_SECONDS, remain))


if __name__ == "__main__":
    sys.exit(main())
