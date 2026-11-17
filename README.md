# AI 早报 · 私人阅读页

把 [橘鸦 AI 早报](https://daily.juya.uk) 的每日内容渲染成**移动端优先**的阅读页，并在每天早上推一条钉钉卡片。

不再依赖微信，也不再和钉钉的富文本限制较劲：**钉钉只负责叫你看，阅读交给网页。**

## 为什么这么做

- decohack 的 `/feed/` 只有 ProductHunt 版块，AI 日报没有独立 RSS；上游 `daily.juya.uk/rss.xml` 有全文 HTML，所以直接取上游。
- 钉钉自定义机器人的 markdown 是子集：表格/代码块渲染不出来、长消息会折叠。所以只推「标题 + 一句话」，全文走网页。
- 上游每期每条自带 `blockquote` 一句话摘要，正好用来做三级折叠的中间层。

## 阅读页做了什么

- 三级递进：**分节折叠 → 每条一句话 → 展开正文**
- 关注节默认展开，其余收起（`config.json` 里改）
- 移动优先、暗色模式、字号可调（都存在 localStorage）
- 顶部目录 chips、阅读进度条、封面图、视频版入口
- 往期归档页，每期固定 URL

## 本地跑

```bash
python3 build.py          # 抓取 + 生成 dist/
python3 build.py --offline  # 用 .cache 调试渲染
python3 notify.py --dry-run --force   # 只看要推的内容，不发送
open dist/index.html
```

## 配置

`config.json`：

| 字段 | 说明 |
|---|---|
| `site_base` | 部署后的站点根地址，用于拼推送里的链接 |
| `focus_sections` | 默认展开的分节，可选项：要闻 / 模型发布 / 开发生态 / 产品应用 / 技术与洞察 / 行业动态 / 前瞻与传闻 |
| `default_font_size` | 初始字号（页面里还能再调） |

## 推送通道

按环境变量自动选择：**配了飞书就发飞书，否则发钉钉。**

### 飞书（当前使用）

用**卡片 2.0 + 折叠面板**，7 个分节直接在卡片里展开阅读，不用跳浏览器：

- 关注节默认展开，其余收起，每个面板里是带序号的条目标题
- 底部「读全文」按钮才跳网页（全文 24 条 + 图片塞不进卡片，深读也需要排版和归档）
- 只有「读全文」这一个动作会离开飞书

配置：仓库 Settings → Secrets → Actions

| secret | 说明 |
|---|---|
| `FEISHU_WEBHOOK` | 群机器人 Webhook，`https://open.feishu.cn/open-apis/bot/v2/hook/...` |
| `FEISHU_SECRET` | 开启了「签名校验」才需要 |

加签算法是 `HMAC-SHA256(key="{timestamp}\n{secret}", msg=空)`，时间戳用**秒**——和钉钉正好相反（钉钉 key 是 secret、时间戳用毫秒），两套实现都在 `notify.py` 里。

### 钉钉（保留备用）

配 `DINGTALK_WEBHOOK` / `DINGTALK_SECRET` 即可切回，用 `actionCard` 类型。

### 幂等

只有最新一期是**当天**才会推，避免上游没更新时把昨天的内容再推一遍。手动测试用 `workflow_dispatch` 勾 `force_notify`；只想看要发什么就勾 `dry_run`。

### 卡片 2.0 的坑

`note`、`action` 这些 1.0 组件在 schema 2.0 里**已被移除**，用了会返回 `ErrCode 200861 unsupported tag`。摘要用 `markdown`，按钮用独立的 `button` 元素 + `behaviors: [{type: "open_url"}]`。

## 定时

GitHub Actions，每天 `01:00 UTC`（北京时间 09:00）抓取并部署到 GitHub Pages。
上游通常在 08:47 左右发布，09:00 抓取是安全的。注意 GitHub 的 cron 在高峰期可能延迟几分钟到半小时。

## 目录

```
build.py            抓取 RSS → 解析 → 渲染静态页
notify.py           读 dist/latest.json → 推钉钉 actionCard
config.json         站点与关注分节
.github/workflows/daily.yml   定时 + Pages 部署
dist/               生成物（不入库）
```
