"""用北京时间窗口与 GitHub Artifact 成功回执，决定本次是否需要推送。"""

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
START = time(8, 30)
END = time(10, 0)


def within_window(now: datetime) -> bool:
    return START <= now.astimezone(TZ).time() < END


def is_automatic(event: str, automatic: bool = False) -> bool:
    return event == "schedule" or (event == "workflow_dispatch" and automatic)


def already_notified(today: str) -> bool:
    name = "notified-" + today
    query = urllib.parse.urlencode({"name": name, "per_page": 100})
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    repo = os.environ["GITHUB_REPOSITORY"]
    request = urllib.request.Request(
        f"{api}/repos/{repo}/actions/artifacts?{query}",
        headers={
            "Authorization": "Bearer " + os.environ["GITHUB_TOKEN"],
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ai-daily-gate/1.0",
        },
    )
    # 查询失败就终止本次运行，避免把「不知道是否发过」误当成「没发过」。
    with urllib.request.urlopen(request, timeout=20) as response:
        artifacts = json.load(response)["artifacts"]
    return any(not artifact["expired"] for artifact in artifacts)


def decide(now: datetime, *, event: str, force: bool = False, dry: bool = False,
           automatic: bool = False) -> bool:
    automated = is_automatic(event, automatic)
    if automated and not within_window(now):
        print("[gate] 不在北京 08:30（含）至 10:00（不含）的执行窗口，跳过")
        return False
    if not automated and event == "workflow_dispatch" and (force or dry):
        return True
    today = now.astimezone(TZ).strftime("%Y-%m-%d")
    if already_notified(today):
        print(f"[gate] {today} 已推送成功，跳过")
        return False
    return True


def main() -> None:
    now = datetime.now(TZ)
    dry = os.environ.get("DRY", "false") == "true"
    automatic = os.environ.get("AUTOMATIC", "false") == "true"
    run = decide(
        now,
        event=os.environ["GITHUB_EVENT_NAME"],
        force=os.environ.get("FORCE", "false") == "true",
        dry=dry,
        automatic=automatic,
    )
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write(f"run={str(run).lower()}\n")
        output.write(f"record={str(not dry).lower()}\n")
        output.write(f"today={now:%Y-%m-%d}\n")
        output.write(f"automatic={str(is_automatic(os.environ['GITHUB_EVENT_NAME'], automatic)).lower()}\n")


if __name__ == "__main__":
    main()
