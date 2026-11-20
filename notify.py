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

from delivery_gate import is_automatic, within_window

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
    """飞书消息卡片 2.0：每个分节一个折叠面板，标题层内容直接读，不跳浏览器。"""
    day = meta["date"][5:].replace("-", ".")
    wd = weekday_of(meta["date"])

    elements = [{
        "tag": "markdown",
        "content": "**今日 %d 条 · %d 节**" % (meta["total"], len(meta["sections"])),
    }]

    summary = (meta.get("summary") or "").strip()
    if summary:
        if len(summary) > 110:
            summary = summary[:110] + "…"
        elements.append({"tag": "markdown", "content": summary})

    for sec in meta.get("sections_detail") or []:
        rows = ["**%s** %s" % (it["num"] or "·", it["title"]) for it in sec["items"]]
        elements.append({
            "tag": "collapsible_panel",
            # 自定义机器人不支持卡片回传，给不了"全部展开"按钮，
            # 所以面板一律默认展开，效果等同点过一键展开。
            "expanded": True,
            "header": {
                "title": {"tag": "markdown",
                          "content": "**%s** · %d 条" % (sec["name"], sec["count"])},
                "vertical_align": "center",
                "icon_position": "right",
                "icon_expanded_angle": -180,
            },
            "border": {"color": "grey", "corner_radius": "6px"},
            "vertical_spacing": "4px",
            "elements": [{"tag": "markdown", "content": "\n".join(rows)}],
        })

    elements.append({"tag": "hr"})
    source_url = (meta.get("source_url") or "").strip()
    if source_url:
        # 两个入口：自建页有深色/字号/折叠，源站是原生排版，各有各的用处
        elements.append({
            "tag": "column_set",
            "flex_mode": "bisect",
            "horizontal_spacing": "8px",
            "columns": [
                {
                    "tag": "column", "width": "weighted", "weight": 1,
                    "vertical_align": "center",
                    "elements": [{
                        "tag": "button",
                        "text": {"tag": "plain_text",
                                 "content": "读全文 · %d 条" % meta["total"]},
                        "type": "primary",
                        "width": "fill",
                        "behaviors": [{"type": "open_url", "default_url": meta["url"]}],
                    }],
                },
                {
                    "tag": "column", "width": "weighted", "weight": 1,
                    "vertical_align": "center",
                    "elements": [{
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "原文"},
                        "type": "default",
                        "width": "fill",
                        "behaviors": [{"type": "open_url", "default_url": source_url}],
                    }],
                },
            ],
        })
    else:
        elements.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "读全文 · %d 条" % meta["total"]},
            "type": "primary",
            "width": "fill",
            "behaviors": [{"type": "open_url", "default_url": meta["url"]}],
        })

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {"wide_screen_mode": True, "update_multi": True},
            "header": {
                "template": "orange",
                "title": {"tag": "plain_text",
                          "content": "AI 早报 %s 周%s" % (day, wd)},
            },
            "body": {"elements": elements},
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

    focus = set(meta.get("focus_names") or [])
    used = 0
    for sec in meta.get("sections_detail") or []:
        if sec["name"] not in focus:
            continue
        rows = []
        for it in sec["items"]:
            if used >= MAX_TITLES or len(rows) >= MAX_PER_SECTION:
                break
            rows.append("%d. %s" % (len(rows) + 1, html.escape(strip_tags(it["title"]))))
            used += 1
        if not rows:
            continue
        rest = sec["count"] - len(rows)
        lines.append("")
        lines.append("**%s · %d**" % (sec["name"], sec["count"]))
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
    automated = is_automatic(
        os.environ.get("GITHUB_EVENT_NAME", ""), os.environ.get("AUTOMATIC") == "true",
    )
    force = "--force" in sys.argv and not automated
    dry = "--dry-run" in sys.argv

    if automated and not within_window(datetime.now(TZ)):
        print("[notify] 已离开北京 08:30 至 10:00 的推送窗口，停止推送", file=sys.stderr)
        return 1

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    if not force and meta["date"] != today:
        # 以前这里 return 0：workflow 一片绿、群里一条没有，问题被埋掉。
        # 定时任务已由 wait_for_today.py 等到当天内容，走到这里说明那一环失效了，必须让人看见。
        print(
            "[notify] 最新一期是 %s，不是今天 %s，不推送" % (meta["date"], today),
            file=sys.stderr,
        )
        return 1

    fs_hook = os.environ.get("FEISHU_WEBHOOK", "").strip()
    fs_secret = os.environ.get("FEISHU_SECRET", "").strip()
    dt_hook = os.environ.get("DINGTALK_WEBHOOK", "").strip()
    dt_secret = os.environ.get("DINGTALK_SECRET", "").strip()

    if fs_hook:
        channel, payload = "飞书", build_feishu_card(meta)
    elif dt_hook:
        channel, payload = "钉钉", build_dingtalk_payload(meta)
    else:
        print("[notify] 未配置 FEISHU_WEBHOOK 或 DINGTALK_WEBHOOK，推送失败", file=sys.stderr)
        return 1

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
