#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""橘鸦 AI 早报 -> 移动优先的静态阅读页。

用法:
    python3 build.py             # 抓取 https://daily.juya.uk/rss.xml 并渲染
    python3 build.py --offline   # 用 .cache/feed.xml 调试渲染
"""
from __future__ import annotations

import html
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
CACHE = ROOT / ".cache" / "feed.xml"
FEED_URL = "https://daily.juya.uk/rss.xml"
UA = "Mozilla/5.0 (compatible; ai-daily/1.0)"
WEEKDAYS = "一二三四五六日"
NUM_RE = re.compile(r"\s*#(\d+)\s*$")
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


# ---------- 抓取 ----------

def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def pick(tag: str, chunk: str) -> str:
    m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), chunk, re.S)
    return html.unescape(m.group(1)) if m else ""


def parse_feed(xml_text: str) -> list[dict]:
    out = []
    for chunk in re.split(r"<item>", xml_text)[1:]:
        out.append({
            "title": TAG_RE.sub("", pick("title", chunk)).strip(),
            "link": pick("link", chunk).strip(),
            "date": pick("pubDate", chunk).strip(),
            "summary": WS_RE.sub(" ", TAG_RE.sub("", pick("description", chunk))).strip(),
            "body": pick("content:encoded", chunk),
        })
    return out


# ---------- 解析单期 ----------

def plain(s: str) -> str:
    return WS_RE.sub(" ", TAG_RE.sub("", s)).strip()


def norm(s: str) -> str:
    return re.sub(r"\s+", "", TAG_RE.sub("", s))


def first_href(s: str) -> str:
    m = re.search(r'href="([^"]+)"', s)
    return m.group(1) if m else ""


def strip_arrows(b: str) -> str:
    b = re.sub(r'<a [^>]*>\s*(?:↗|→|\^)\s*</a>', "", b)
    b = re.sub(r"<code>\s*#\d+\s*</code>", "", b)
    return b.strip()


def drop_dup_lead(body: str, tldr: str) -> str:
    """正文首段常与 blockquote 摘要重复，能对上就丢掉。"""
    key = norm(tldr)[:20]
    if not key:
        return body
    m = re.search(r"<p>.*?</p>", body, re.S)
    if m and key in norm(m.group(0)):
        return body[m.end():].strip()
    return body


def parse_issue(entry: dict) -> dict | None:
    raw = entry["body"]
    m = re.search(r'<img[^>]+src="([^"]+)"', raw)
    cover = m.group(1) if m else ""

    date = ""
    m = re.search(r"/issues/(\d{4}-\d{2}-\d{2})", entry["link"])
    if m:
        date = m.group(1)
    if not date:
        m = re.search(r"(\d{4}-\d{2}-\d{2})", entry["title"])
        date = m.group(1) if m else ""
    if not date:
        return None

    mh = re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.S)
    h1 = plain(mh.group(1)) if mh else ""

    sections = []
    parts = re.split(r"<h2[^>]*>(.*?)</h2>", raw, flags=re.S)
    head = parts[0]
    videos = []
    for href, label in re.findall(r'<a [^>]*href="([^"]+)"[^>]*>([^<]{1,24})</a>', head):
        if any(d in href for d in ("youtube.com", "youtu.be", "bilibili.com")):
            videos.append({"url": href, "label": plain(label)})
    for i in range(1, len(parts), 2):
        name = plain(parts[i])
        if not name or name == "概览":
            continue
        inner = parts[i + 1] if i + 1 < len(parts) else ""
        subs = re.split(r"<h3[^>]*>(.*?)</h3>", inner, flags=re.S)
        items = []
        for j in range(1, len(subs), 2):
            head = subs[j]
            title = plain(head)
            num = ""
            mm = NUM_RE.search(title)
            if mm:
                num, title = mm.group(1), NUM_RE.sub("", title).strip()
            link = first_href(head)
            rest = subs[j + 1] if j + 1 < len(subs) else ""
            mq = re.search(r"<blockquote[^>]*>(.*?)</blockquote>", rest, re.S)
            tldr = mq.group(1).strip() if mq else ""
            body = rest
            if tldr:
                body = rest[:mq.start()] + rest[mq.end():]
                body = drop_dup_lead(body.strip(), tldr)
            body = strip_arrows(body)
            body = re.sub(r"<p>\s*</p>", "", body)
            if not title:
                continue
            items.append({
                "num": num,
                "title": title,
                "link": link or entry["link"],
                "tldr": strip_arrows(tldr),
                "body": body,
            })
        if items:
            sections.append({"name": name, "items": items})

    if not sections:
        return None

    d = datetime.strptime(date, "%Y-%m-%d")
    return {
        "date": date,
        "date_label": "%d.%d" % (d.month, d.day),
        "weekday": WEEKDAYS[d.weekday()],
        "title": h1 or entry["title"] or ("AI 早报 " + date),
        "videos": videos,
        "summary": entry["summary"],
        "cover": cover,
        "link": entry["link"],
        "sections": sections,
    }


# ---------- 页面 ----------

CSS = """
:root{
  --bg:#f6f3ec; --fg:#1b1916; --muted:#6d6559; --line:#e2dbcd;
  --card:#fffdf8; --accent:#b23a22; --accent-soft:#f0e2d8;
  --fs:17px;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --bg:#131211; --fg:#e9e4da; --muted:#8d8578; --line:#2b2825;
    --card:#1a1917; --accent:#e57a5f; --accent-soft:#2e211c;
  }
}
:root[data-theme="dark"]{
  --bg:#131211; --fg:#e9e4da; --muted:#8d8578; --line:#2b2825;
  --card:#1a1917; --accent:#e57a5f; --accent-soft:#2e211c;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html{-webkit-text-size-adjust:100%}
body{
  margin:0;background:var(--bg);color:var(--fg);
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif;
  font-size:var(--fs);line-height:1.78;
  text-rendering:optimizeLegibility;
}
#bar{position:fixed;top:0;left:0;height:2px;background:var(--accent);width:0;z-index:99;transition:width .1s}
.wrap{max-width:720px;margin:0 auto;padding:0 18px 72px}

header.hero{padding:34px 0 6px}
.kicker{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:13px;letter-spacing:.08em}
.kicker b{color:var(--accent);font-weight:600}
header.hero h1{margin:10px 0 12px;font-size:1.72em;line-height:1.25;letter-spacing:-.01em;font-weight:700}
.summary{margin:0;color:var(--muted);font-size:.94em;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.cover{width:100%;max-height:200px;object-fit:cover;border-radius:14px;margin:20px 0 0;display:block;border:1px solid var(--line)}
.meta{display:flex;gap:14px;margin-top:16px;color:var(--muted);font-size:13px;flex-wrap:wrap}
.meta span{white-space:nowrap}
.vids{display:flex;gap:10px;margin-top:16px;flex-wrap:wrap}
.vids a{font-size:13px;text-decoration:none;color:var(--accent);border:1px solid var(--accent);border-radius:999px;padding:5px 12px}
.vids a:active{background:var(--accent-soft)}

.toolbar{position:sticky;top:0;z-index:20;display:flex;gap:8px;align-items:center;
  padding:10px 0;margin-top:6px;background:linear-gradient(var(--bg) 72%,transparent)}
.toolbar button{
  font:inherit;font-size:13px;color:var(--muted);background:var(--card);
  border:1px solid var(--line);border-radius:999px;padding:6px 13px;cursor:pointer;
}
.toolbar button:active{background:var(--accent-soft);color:var(--accent)}
.toolbar .sp{flex:1}

nav.toc{display:flex;gap:8px;overflow-x:auto;padding:2px 0 14px;scrollbar-width:none}
nav.toc::-webkit-scrollbar{display:none}
nav.toc a{
  flex:0 0 auto;font-size:13px;text-decoration:none;color:var(--muted);
  border:1px solid var(--line);border-radius:999px;padding:6px 13px;background:var(--card);
}
nav.toc a.focus{color:var(--accent);border-color:var(--accent);background:var(--accent-soft)}

details.sec{border-top:1px solid var(--line);padding:4px 0}
details.sec>summary{
  list-style:none;cursor:pointer;display:flex;align-items:baseline;gap:12px;
  padding:16px 0;user-select:none;
}
details.sec>summary::-webkit-details-marker{display:none}
.sidx{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums;letter-spacing:.1em;min-width:20px}
.sname{font-size:1.14em;font-weight:650;letter-spacing:.01em}
details.sec.focus .sname{color:var(--accent)}
.scount{font-size:12px;color:var(--muted);margin-left:auto;font-variant-numeric:tabular-nums}
.chev{width:8px;height:8px;border-right:1.6px solid var(--muted);border-bottom:1.6px solid var(--muted);
  transform:rotate(-45deg);transition:transform .2s;margin-left:10px;flex:0 0 auto;align-self:center}
details[open]>summary .chev{transform:rotate(45deg)}
details.sec>summary:active{opacity:.6}

article.item{padding:2px 0 26px}
article.item h3{margin:6px 0 8px;font-size:1.02em;line-height:1.5;font-weight:650}
article.item h3 a{color:var(--fg);text-decoration:none}
.num{
  display:inline-block;font-size:11px;font-variant-numeric:tabular-nums;
  color:var(--accent);background:var(--accent-soft);border-radius:5px;
  padding:1px 6px;margin-right:9px;vertical-align:2px;letter-spacing:.02em;
}
.tldr{
  margin:0 0 12px;padding:12px 14px;background:var(--card);border:1px solid var(--line);
  border-left:3px solid var(--accent);border-radius:0 10px 10px 0;
  font-size:.96em;color:var(--fg);
}
details.more{margin:0}
details.more>summary{list-style:none;cursor:pointer;display:inline-flex;align-items:center;gap:6px;
  font-size:13px;color:var(--accent);user-select:none;padding:2px 0}
details.more>summary::-webkit-details-marker{display:none}
details.more>summary::before{content:"＋";font-size:11px;letter-spacing:0}
details.more[open]>summary::before{content:"−"}
details.more>summary:active{opacity:.55}
details.more>div.prose{margin-top:12px}
.prose p{margin:0 0 14px}
.prose code{
  font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.86em;
  background:var(--accent-soft);color:var(--accent);border-radius:4px;padding:1px 5px;
}
.prose strong{font-weight:650}
.prose img{max-width:100%;height:auto;border-radius:10px;display:block;margin:6px 0 14px;border:1px solid var(--line)}
.prose a{color:var(--accent);text-decoration:none;border-bottom:1px solid var(--accent-soft)}
.src{display:inline-block;font-size:13px;color:var(--muted);text-decoration:none;
  border-bottom:1px dashed var(--line);padding-bottom:1px}
.src:active{color:var(--accent)}

footer{margin-top:10px;padding-top:22px;border-top:1px solid var(--line);
  color:var(--muted);font-size:13px;display:flex;gap:18px;flex-wrap:wrap;align-items:center}
footer a{color:var(--muted);text-decoration:none;border-bottom:1px dashed var(--line)}
footer a:active{color:var(--accent)}

ul.archive{list-style:none;padding:0;margin:18px 0 0}
ul.archive li{padding:13px 0;border-bottom:1px solid var(--line);display:flex;gap:12px;align-items:baseline}
ul.archive a{color:var(--fg);text-decoration:none;flex:1}
ul.archive .d{color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
ul.archive .c{color:var(--muted);font-size:12px}

@media (min-width:760px){
  :root{--fs:18px}
  header.hero h1{font-size:2.1em}
}
"""

JS = """
(function(){
  var root=document.documentElement;
  var saved=localStorage.getItem('aidaily-theme');
  if(saved) root.setAttribute('data-theme',saved);
  var fs=localStorage.getItem('aidaily-fs');
  if(fs) root.style.setProperty('--fs',fs+'px');
  var cur=parseInt(fs||getComputedStyle(root).getPropertyValue('--fs'))||17;
  function setFs(v){
    cur=Math.min(23,Math.max(14,v));
    root.style.setProperty('--fs',cur+'px');
    localStorage.setItem('aidaily-fs',cur);
  }
  document.getElementById('fs-up').onclick=function(){setFs(cur+1)};
  document.getElementById('fs-dn').onclick=function(){setFs(cur-1)};
  document.getElementById('theme').onclick=function(){
    var dark = root.getAttribute('data-theme')==='dark' ||
      (!root.getAttribute('data-theme') && matchMedia('(prefers-color-scheme:dark)').matches);
    var next = dark?'light':'dark';
    root.setAttribute('data-theme',next);
    localStorage.setItem('aidaily-theme',next);
  };
  var bar=document.getElementById('bar');
  function onScroll(){
    var h=document.documentElement;
    var max=h.scrollHeight-h.clientHeight;
    bar.style.width=(max>0?(h.scrollTop/max*100):0)+'%';
  }
  addEventListener('scroll',onScroll,{passive:true});onScroll();
})();
"""


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def render_section(sec: dict, idx: int, focus: set) -> str:
    name = sec["name"]
    is_focus = name in focus
    opened = " open" if is_focus else ""
    cls = "sec focus" if is_focus else "sec"
    rows = []
    for it in sec["items"]:
        badge = '<span class="num">%s</span>' % esc(it["num"]) if it["num"] else ""
        tldr = '<p class="tldr">%s</p>' % it["tldr"] if it["tldr"] else ""
        prose = ""
        if it["body"]:
            if it["tldr"]:
                npar = it["body"].count("<p>")
                label = "展开正文" + ("（%d 段）" % npar if npar > 1 else "")
                prose = ('<details class="more"><summary>%s</summary>'
                         '<div class="prose">%s</div></details>' % (label, it["body"]))
            else:
                prose = '<div class="prose">%s</div>' % it["body"]
        rows.append(
            '<article class="item">'
            '<h3>%s<a href="%s" target="_blank" rel="noopener">%s</a></h3>'
            "%s%s"
            '<a class="src" href="%s" target="_blank" rel="noopener">原文 ↗</a>'
            "</article>"
            % (badge, esc(it["link"]), esc(it["title"]), tldr, prose, esc(it["link"]))
        )
    return (
        '<details class="%s" id="sec-%d"%s>'
        '<summary><span class="sidx">%02d</span><span class="sname">%s</span>'
        '<span class="scount">%d 条</span><span class="chev"></span></summary>'
        "%s</details>"
        % (cls, idx, opened, idx, esc(name), len(sec["items"]), "".join(rows))
    )


def render_page(issue: dict, cfg: dict, prev: dict | None, nxt: dict | None) -> str:
    focus = set(cfg.get("focus_sections") or [])
    total = sum(len(s["items"]) for s in issue["sections"])
    nsec = len(issue["sections"])

    toc = "".join(
        '<a class="%s" href="#sec-%d">%s · %d</a>'
        % ("focus" if s["name"] in focus else "", i, esc(s["name"]), len(s["items"]))
        for i, s in enumerate(issue["sections"], 1)
    )
    body = "".join(
        render_section(s, i, focus) for i, s in enumerate(issue["sections"], 1)
    )
    cover = '<img class="cover" src="%s" alt="" loading="eager">' % esc(issue["cover"]) if issue["cover"] else ""
    summary = '<p class="summary">%s</p>' % esc(issue["summary"]) if issue["summary"] else ""

    video_html = ""
    if issue.get("videos"):
        video_html = '<div class="vids">' + "".join(
            '<a href="%s" target="_blank" rel="noopener">▶ %s</a>' % (esc(v["url"]), esc(v["label"]))
            for v in issue["videos"]
        ) + "</div>"

    nav = []
    if prev:
        nav.append('<a href="%s.html">← %s</a>' % (esc(prev["date"]), esc(prev["date_label"])))
    nav.append('<a href="archive.html">全部往期</a>')
    if nxt:
        nav.append('<a href="%s.html">%s →</a>' % (esc(nxt["date"]), esc(nxt["date_label"])))
    nav.append('<a href="%s" target="_blank" rel="noopener">源站 ↗</a>' % esc(issue["link"]))

    return (
        "<!doctype html>"
        '<html lang="zh-CN"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
        '<meta name="theme-color" content="#f6f3ec" media="(prefers-color-scheme:light)">'
        '<meta name="theme-color" content="#131211" media="(prefers-color-scheme:dark)">'
        "<title>%s %s · %s</title>"
        "<style>%s</style>"
        "</head><body>"
        '<div id="bar"></div>'
        '<div class="wrap">'
        '<header class="hero">'
        '<div class="kicker"><b>%s</b><span>%s</span><span>周%s</span></div>'
        "<h1>%s</h1>"
        "%s%s%s"
        '<div class="meta"><span>%d 条</span><span>%d 节 · 展开 %d 节</span></div>'
        "</header>"
        '<div class="toolbar">'
        '<button id="fs-dn">A−</button><button id="fs-up">A+</button>'
        '<button id="theme">明/暗</button><span class="sp"></span>'
        "</div>"
        '<nav class="toc">%s</nav>'
        "<main>%s</main>"
        "<footer>%s</footer>"
        "</div>"
        "<script>%s</script>"
        "</body></html>"
        % (
            esc(cfg.get("site_title", "AI 早报")),
            esc(issue["date"]),
            esc(issue["summary"][:40]),
            CSS,
            esc(cfg.get("site_title", "AI 早报")),
            esc(issue["date_label"]),
            esc(issue["weekday"]),
            esc(issue["title"]),
            summary,
            cover,
            video_html,
            total,
            nsec,
            len([s for s in issue["sections"] if s["name"] in focus]),
            toc,
            body,
            "".join(nav),
            JS,
        )
    )


def render_archive(issues: list[dict], cfg: dict) -> str:
    rows = []
    for it in issues:
        total = sum(len(s["items"]) for s in it["sections"])
        rows.append(
            '<li><a href="%s.html">%s 周%s</a><span class="c">%d 条</span>'
            '<span class="d">%s</span></li>'
            % (esc(it["date"]), esc(it["date_label"]), esc(it["weekday"]), total, esc(it["date"]))
        )
    return (
        "<!doctype html>"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>全部往期 · %s</title><style>%s</style></head><body>"
        '<div class="wrap"><header class="hero"><div class="kicker"><b>%s</b></div>'
        "<h1>全部往期</h1></header>"
        '<ul class="archive">%s</ul>'
        '<footer><a href="index.html">最新一期 →</a></footer>'
        "</div></body></html>"
        % (esc(cfg.get("site_title", "AI 早报")), CSS, esc(cfg.get("site_title", "AI 早报")), "".join(rows))
    )


# ---------- 主流程 ----------

def load_config() -> dict:
    p = ROOT / "config.json"
    if p.exists():
        return json.loads(p.read_text("utf-8"))
    return {}


def main() -> int:
    offline = "--offline" in sys.argv
    cfg = load_config()

    if offline and CACHE.exists():
        xml_text = CACHE.read_text("utf-8")
        print("[build] 离线模式，使用缓存 %s" % CACHE)
    else:
        xml_text = fetch(FEED_URL)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(xml_text, "utf-8")

    entries = parse_feed(xml_text)
    issues = [i for i in (parse_issue(e) for e in entries) if i]
    issues.sort(key=lambda x: x["date"], reverse=True)
    if not issues:
        print("[build] 没有解析到任何一期", file=sys.stderr)
        return 1

    DIST.mkdir(parents=True, exist_ok=True)
    (DIST / ".nojekyll").write_text("", "utf-8")

    for k, it in enumerate(issues):
        prev_it = issues[k + 1] if k + 1 < len(issues) else None
        next_it = issues[k - 1] if k - 1 >= 0 else None
        page = render_page(it, cfg, prev_it, next_it)
        (DIST / (it["date"] + ".html")).write_text(page, "utf-8")
        if k == 0:
            (DIST / "index.html").write_text(page, "utf-8")

    (DIST / "archive.html").write_text(render_archive(issues, cfg), "utf-8")

    latest = issues[0]
    total = sum(len(s["items"]) for s in latest["sections"])
    meta = {
        "date": latest["date"],
        "title": latest["title"],
        "summary": latest["summary"],
        "cover": latest["cover"],
        "total": total,
        "sections": [{"name": s["name"], "count": len(s["items"])} for s in latest["sections"]],
        "focus": [
            {"name": s["name"], "count": len(s["items"]),
             "items": [{"num": i["num"], "title": i["title"], "tldr": re.sub(r"<[^>]+>", "", i["tldr"])}
                       for i in s["items"]]}
            for s in latest["sections"] if s["name"] in set(cfg.get("focus_sections") or [])
        ],
        "url": "%s/%s.html" % (cfg.get("site_base", "").rstrip("/"), latest["date"]),
    }
    (DIST / "latest.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
    print("[build] %s 共 %d 条 / %d 节 -> %s" % (latest["date"], total, len(latest["sections"]), DIST))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
