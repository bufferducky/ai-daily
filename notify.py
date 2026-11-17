#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把最新一期推送到群机器人。支持飞书 / 钉钉，按环境变量自动选择。

环境变量:
    FEISHU_WEBHOOK      飞书自定义机器人 Webhook
    FEISHU_SECRET       飞书签名校验密钥（开启了"签名校验"才需要）
    DINGTALK_WEBHOOK    钉钉自定义机器人 Webhook
    DINGTALK_SECRET     钉钉加签密钥 SECxxxx

优先级：配了飞书就发飞书，否则发钉钉。

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
import re
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
WEEKDAYS = "一二三四五六日"

# 卡片是"扫一眼"用的，不是阅读器：控制标题总条数，避免长篇
MAX_PER_SECTION = 3
MAX_TITLES = 9


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def weekday_of(date: str) -> str:
    try:
        return WEEKDAYS[datetime.strptime(date, "%Y-%m-%d").weekday()]
    except ValueError:
        return ""


# ---------- 飞书 ----------

def feishu_sign(secret: str) -> dict:
    """飞书：key = "{timestamp}\n{secret}"，msg = 空；时间戳用秒。"""
    ts = str(int(time.time()))
    key = "%s\n%s" % (ts, secret)
    digest = hmac.new(key.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return {"timestamp": ts, "sign": base64.b64encode(digest).decode("utf-8")}


def build_feishu_card(meta: dict) -> dict:
    """飞书消息卡片：标题栏 + 分区 + 按钮。"""
    day = meta["date"][5:].replace("-", ".")
    wd = weekday_of(meta["date"])
    focus = meta.get("focus") or []

    elements = [{
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": "**今日 %d 条 · %d 节**" % (meta["total"], len(meta["sections"])),
        },
    }]

    used = 0
    for sec in focus:
        rows = []
        for it in sec["items"]:
            if used >= MAX_TITLES or len(rows) >= MAX_PER_SECTION:
                break
            rows.append("%d. %s" % (len(rows) + 1, strip_tags(it["title"])))
            used += 1
        if not rows:
            continue
        rest = len(sec["items"]) - len(rows)
        content = "**%s · %d 条**\n%s" % (sec["name"], len(sec["items"]), "\n".join(rows))
        if rest > 0:
            content += "\n<font color='grey'>另 %d 条在全文</font>" % rest
        elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})

    others = [s["name"] for s in meta["sections"] if s["name"] not in {f["name"] for f in focus}]
    if others:
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md",
                     "content": "<font color='grey'>已收起：%s</font>" % "、".join(others)},
        })

    elements.append({
        "tag": "action",
        "actions": [{
            "tag": "button",
            "text": {"tag": "plain_text", "content": "阅读全文 · %d 条" % meta["total"]},
            "type": "primary",
            "url": meta["url"],
        }],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text", "content": "AI 早报 %s 周%s" % (day, wd)},
            },
            "elements": elements,
        },
    }


def post_feishu(webhook: str, secret: str, payload: dict) -> dict:
    body = dict(payload)
    if secret:
        body.update(feishu_sign(secret))
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook, data=data, headers={"Content-Type": "application/json; charset=utf-8"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8", "replace"))
    # 新版返回 code，旧版返回 StatusCode
    code = result.get("code", result.get("StatusCode"))
    if code != 0:
        raise RuntimeError("飞书返回 %s" % result)
    return result


# ---------- 钉钉（保留，按需切回） ----------

def dingtalk_sign(webhook: str, secret: str) -> str:
    """钉钉：key = secret，msg = "{timestamp}\n{secret}"；时间戳用毫秒。"""
    if not secret:
        return webhook
    ts = str(round(time.time() * 1000))
    to_sign = "%s\n%s" % (ts, secret)
    digest = hmac.new(secret.encode("utf-8"), to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    sep = "&" if "?" in webhook else "?"
    return "%s%stimestamp=%s&sign=%s" % (webhook, sep, ts, sign)


def build_dingtalk_payload(meta: dict) -> dict:
    day = meta["date"][5:].replace("-", ".")
    lines = []
    if meta.get("cover"):
        lines.append("![cover](%s)" % meta["cover"])
        lines.append("")
    lines.append("**今日 %d 条 · %d 节**" % (meta["total"], len(meta["sections"])))

    used = 0
    for sec in meta.get("focus") or []:
        rows = []
        for it in sec["items"]:
            if used >= MAX_TITLES or len(rows) >= MAX_PER_SECTION:
                break
            rows.append("%d. %s" % (len(rows) + 1, html.escape(strip_tags(it["title"]))))
            used += 1
        if not rows:
            continue
        rest = len(sec["items"]) - len(rows)
        lines.append("")
        lines.append("**%s · %d**" % (sec["name"], len(sec["items"])))
        lines.extend(rows)
        if rest > 0:
            lines.append("> 另 %d 条在全文" % rest)

    return {
        "msgtype": "actionCard",
        "actionCard": {
            "title": "AI 早报 %s" % day,
            "text": "\n".join(lines),
            "btnOrientation": "0",
            "singleTitle": "阅读全文 · %d 条" % meta["total"],
            "singleURL": meta["url"],
        },
    }


def post_dingtalk(webhook: str, secret: str, payload: dict) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        dingtalk_sign(webhook, secret), data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        result = json.loads(resp.read().decode("utf-8", "replace"))
    if result.get("errcode") != 0:
        raise RuntimeError("钉钉返回 %s" % result)
    return result


# ---------- 入口 ----------

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

    fs_hook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    fs_secret = os.environ.get("FEISHU_SECRET", "").strip()
    dt_hook = os.environ.get("DINGTALK_WEBHOOK", "").strip()
    dt_secret = os.environ.get("DINGTALK_SECRET", "").strip()

    if fs_hook:
        channel, payload = "飞书", build_feishu_card(meta)
    elif dt_hook:
        channel, payload = "钉钉", build_dingtalk_payload(meta)
    else:
        print("[notify] 未配置 FEISHU_WEBHOOK 或 DINGTALK_WEBHOOK，跳过推送")
        return 0

    if dry:
        print("channel=%s" % channel)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    try:
        if channel == "飞书":
            post_feishu(fs_hook, fs_secret, payload)
        else:
            post_dingtalk(dt_hook, dt_secret, payload)
    except Exception as exc:  # noqa: BLE001
        print("[notify] %s 推送失败: %s" % (channel, exc), file=sys.stderr)
        return 2

    print("[notify] 已推送到%s %s（%d 条）" % (channel, meta["date"], meta["total"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
