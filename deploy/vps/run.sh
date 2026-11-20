#!/bin/bash
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
exec /usr/bin/flock -n /var/lib/ai-daily-trigger/trigger.lock \
    /bin/bash /opt/ai-daily-trigger/trigger.sh "$@"
