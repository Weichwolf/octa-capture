#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
arguments=()
if [[ ${1:-} == --write ]]; then
    arguments+=(--write)
    shift
fi
if (( $# > 1 )); then
    echo 'Usage: tools/check-gui.sh [--write] [private-server-log]' >&2
    exit 2
fi
server_log=${1:-build/gui/server.log}
umask 077
mkdir -p build/gui
browser_profile=$(mktemp -d "$PWD/build/gui/browser-profile-XXXXXX")
browser_pid=
cleanup() {
    if [[ -n $browser_pid ]]; then
        kill -TERM -- "-$browser_pid" 2>/dev/null || true
        wait "$browser_pid" 2>/dev/null || true
        for _attempt in {1..100}; do
            if ! kill -0 -- "-$browser_pid" 2>/dev/null; then
                break
            fi
            sleep 0.05
        done
        kill -KILL -- "-$browser_pid" 2>/dev/null || true
    fi
    rm -rf -- "$browser_profile"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# This disposable test browser has no user profile or credentials. Production
# launches use the normal browser and keep its sandbox enabled.
setsid --wait chromium --headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage \
    --no-first-run --no-default-browser-check --disable-background-networking \
    --disable-component-update --disable-sync --disable-extensions \
    --remote-debugging-port=0 --remote-debugging-address=127.0.0.1 \
    --user-data-dir="$browser_profile" about:blank > "$browser_profile/browser.log" 2>&1 &
browser_pid=$!
for _attempt in {1..100}; do
    if [[ -s $browser_profile/DevToolsActivePort ]]; then
        break
    fi
    if ! kill -0 "$browser_pid" 2>/dev/null; then
        cat "$browser_profile/browser.log" >&2
        exit 1
    fi
    sleep 0.05
done
node test/GuiBrowser.mjs "$server_log" "$browser_profile/DevToolsActivePort" "${arguments[@]}"
