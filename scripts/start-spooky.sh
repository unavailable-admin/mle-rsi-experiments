#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
run_dir="${1:-/mnt/afs/yangsen1/runs/dream-rsi-spooky-solver-500-002}"
if [[ ! -f "$run_dir/frozen-config.json" ]]; then
  ./single-task init --run-dir "$run_dir"
fi
# Lock prevents duplicate launcher invocations; the controller also locks its run.
exec 9>"$run_dir/launcher.lock"
flock -n 9 || { echo 'A launcher/controller already owns this run.' >&2; exit 1; }
nohup setsid ./single-task web --run-dir "$run_dir" --port 18768 >"$run_dir/dashboard.log" 2>&1 9>&- </dev/null &
echo "$!" >"$run_dir/dashboard.pid"
nohup setsid ./experiment web --run-dir "$run_dir" --port 18769 >"$run_dir/tree-viewer.log" 2>&1 9>&- </dev/null &
echo "$!" >"$run_dir/tree-viewer.pid"
nohup setsid ./single-task run --run-dir "$run_dir" >>"$run_dir/controller.log" 2>&1 </dev/null &
echo "$!" >"$run_dir/controller.pid"
echo "Controller PID: $(cat "$run_dir/controller.pid")"
echo "Progress: http://127.0.0.1:18768"
echo "Tree/policies: http://127.0.0.1:18769"
echo "Log: $run_dir/controller.log"
echo 'The controller waits for the guarded CPU leases before starting paid work.'
