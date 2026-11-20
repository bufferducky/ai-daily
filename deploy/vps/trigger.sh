#!/bin/bash
# 只负责唤醒 GitHub Actions；实际轮询、通知和去重在工作流中完成。
set -Eeuo pipefail
umask 077

trap 'printf "[trigger] 执行失败，行号 %s；本次未确认完成，等待下次重试\n" "$LINENO" >&2' ERR

log() {
    printf '[%s] %s\n' "$(TZ=Asia/Shanghai date '+%F %T%z')" "$*"
}

CHECK_ONLY=false
if [[ ${1:-} == --check-only ]]; then
    CHECK_ONLY=true
elif [[ $# -ne 0 ]]; then
    printf '用法: trigger.sh [--check-only]\n' >&2
    exit 2
fi

TODAY=$(TZ=Asia/Shanghai date +%F)
HHMM=$(TZ=Asia/Shanghai date +%H%M)
if ! "$CHECK_ONLY" && [[ $HHMM < 0830 || $HHMM > 0959 ]]; then
    log '不在北京时间 08:30—10:00 窗口内，跳过'
    exit 0
fi

STATE_DIR=${AI_DAILY_STATE_DIR:-/var/lib/ai-daily-trigger}
CONFIG_FILE=${AI_DAILY_CONFIG_FILE:-/etc/ai-daily-trigger/github.curl}
API_URL=${AI_DAILY_API_URL:-https://api.github.com}
REPOSITORY=${AI_DAILY_REPOSITORY:-bufferducky/ai-daily}
BASE="$API_URL/repos/$REPOSITORY"

if ! "$CHECK_ONLY" && [[ -f "$STATE_DIR/notified.date" ]] &&
    [[ $(cat "$STATE_DIR/notified.date") == "$TODAY" ]]; then
    log "$TODAY 已确认推送成功，当天停止触发"
    exit 0
fi

api() {
    curl --disable --silent --show-error --fail --connect-timeout 10 --max-time 30 \
        --config "$CONFIG_FILE" \
        --header 'Accept: application/vnd.github+json' \
        --header 'X-GitHub-Api-Version: 2022-11-28' \
        --header 'User-Agent: ai-daily-vps-trigger/1.0' "$@"
}

# API 或 JSON 解析失败都会退出，绝不把查询失败当成未发送。
ARTIFACTS=$(api "$BASE/actions/artifacts?name=notified-$TODAY&per_page=100")
RECEIPTS=$(jq -er '[.artifacts[] | select(.expired == false)] | length' <<< "$ARTIFACTS")
if (( RECEIPTS > 0 )); then
    if ! "$CHECK_ONLY"; then
        printf '%s\n' "$TODAY" > "$STATE_DIR/notified.date.tmp"
        mv "$STATE_DIR/notified.date.tmp" "$STATE_DIR/notified.date"
    fi
    log "$TODAY GitHub 已有推送成功回执，当天停止触发"
    exit 0
fi

RUNS=$(api "$BASE/actions/workflows/daily.yml/runs?branch=main&per_page=100")
ACTIVE=$(jq -er '[.workflow_runs[] | select(.status != "completed")] | length' <<< "$RUNS")
if (( ACTIVE > 0 )); then
    log "已有 $ACTIVE 个任务排队或执行中，本次不重复启动"
    exit 0
fi

if "$CHECK_ONLY"; then
    log '只读检查成功：无当天回执、无执行中任务；正常窗口内将触发 automatic=true'
    exit 0
fi

# 窗口检查后 API 可能耗时，发请求前再检查一次。
HHMM=$(TZ=Asia/Shanghai date +%H%M)
if [[ $HHMM < 0830 || $HHMM > 0959 ]]; then
    log '查询完成时已离开执行窗口，停止触发'
    exit 0
fi

NOW=$(date +%s)
if [[ -f "$STATE_DIR/last-dispatch" ]]; then
    LAST=$(cat "$STATE_DIR/last-dispatch")
    if (( NOW - LAST < 120 )); then
        log '最近2分钟已请求启动，等待 GitHub 状态同步'
        exit 0
    fi
fi

api --request POST --header 'Content-Type: application/json' \
    --data '{"ref":"main","inputs":{"automatic":true,"force_notify":false,"dry_run":false}}' \
    "$BASE/actions/workflows/daily.yml/dispatches" > /dev/null
printf '%s\n' "$NOW" > "$STATE_DIR/last-dispatch.tmp"
mv "$STATE_DIR/last-dispatch.tmp" "$STATE_DIR/last-dispatch"
log 'GitHub 已受理 automatic=true 启动请求；等待工作流发送成功并保存回执'
