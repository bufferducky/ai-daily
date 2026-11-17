#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把最新一期推送到钉钉群机器人（actionCard）。

环境变量:
    DINGTALK_WEBHOOK   自定义机器人 Webhook 地址（必填）
    DINGTALK_SECRET    加签密钥 SECxxxx（可选，开了加签才需要）

用法:
    python3 notify.py             # 只在最新一期是今天时推送
    python3 notify.py --force     # 无条件推送（测试用）
    python3 notify.py --dry-run   # 只打印 payload，不发送
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LATEST = ROOT / "dist" / "latest.json"
TZ = ZoneInfo("Asia/Shanghai")
MAX_PER_SECTION = 3
MAX_TITLES = 10


def strip_tags(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", s or "").strip()


def signed_url(webhook: str, secret: str) -> str:
    if not secret:
        return webhook
    ts = str(round(time.time() * 1000))
    to_sign = "%s\n%s" % (ts, secret)
    digest = hmac.new(secret.encode("utf-8"), to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in webhook else "?"
    return "%s%stimestamp=%s&sign=%s" % (webhook, sep, ts, sign)


def build_payload(meta: dict) -> dict:
    today = meta["date"][5:].replace("-", ".")
    lines = []
    if meta.get("cover"):
        lines.append("![cover](%s)" % meta["cover"])
        lines.append("")

    sections = meta.get("focus") or []
    used = 0
    for sec in sections:
        rows = []
        for it in sec["items"]:
            if used >= MAX_TITLES or len(rows) >= MAX_PER_SECTION:
                break
            rows.append("%d. %s" % (len(rows) + 1, html.escape(strip_tags(it["title"]))))
            used += 1
        if not rows:
            continue
        rest = len(sec["items"]) - len(rows)
        tail = "，另 %d 条见全文" % rest if rest > 0 else ""
        lines.append("")
        lines.append("#### %s · %d 条%s" % (sec["name"], len(sec["items"]), ""))
        lines.extend(rows)
        if rest > 0:
            lines.append("> 余下 %d 条在全文里" % rest)

    if not sections:
        lines.append("")
        lines.append(meta.get("summary", "")[:180])

    lines.append("")
    lines.append("**今日 %d 条 · %d 节**" % (meta["total"], len(meta["sections"])))

    return {
        "msgtype": "actionCard",
        "actionCard": {
            "title": "AI 早报 %s" % today,
            "text": "\n".join(lines),
            "btnOrientation": "0",
            "singleTitle": "阅读全文 · %d 条" % meta["total"],
            "singleURL": meta["url"],
        },
    }


def post(url: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main() -> int:
    if not LATEST.exists():
        print("[notify] 找不到 dist/latest.json，先跑 build.py", file=sys.stderr)
        return 1
    meta = json.loads(LATEST.read_text("utf-8"))
    force = "--force" in sys.argv
    dry = "--dry-run" in sys.argv

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if not force and meta["date"] != today:
        print("[notify] 最新一期是 %s，今天 %s 还没有新内容，跳过推送" % (meta["date"], today))
        return 0

    payload = build_payload(meta)
    if dry:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    webhook = os.environ.get("DINGTALK_WEBHOOK", "").strip()
    secret = os.environ.get("DINGTALK_SECRET", "").strip()
    if not webhook:
        print("[notify] 未配置 DINGTALK_WEBHOOK，跳过推送")
        return 0

    result = post(signed_url(webhook, secret), payload)
    if result.get("errcode") != 0:
        print("[notify] 推送失败: %s" % result, file=sys.stderr)
        return 2
    print("[notify] 已推送 %s（%d 条）" % (meta["date"], meta["total"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
