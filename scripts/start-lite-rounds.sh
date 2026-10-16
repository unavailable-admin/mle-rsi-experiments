#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
run_dir="${1:-/mnt/afs/yangsen1/runs/dream-rsi-lite22-rounds-001}"
if [[ ! -f "$run_dir/frozen-config.json" ]]; then
  echo 'Run not prepared. Initialize with ./lite-rounds init --run-dir PATH first.' >&2
  exit 1
fi
# Serialize launchers; the controller also owns an independent persistent run lock.
exec 9>"$run_dir/launcher.lock"
flock -n 9 || { echo 'Another launcher is active.' >&2; exit 1; }
runner="${MLE_RSI_PYTHON:-/mnt/afs/yangsen1/envs/dream-rsi-mle/bin/python}"
"$runner" - "$run_dir" <<'PY'
import sys
from pathlib import Path
from mle_rsi.common import lock
from mle_rsi.experiment import frozen
root=Path(sys.argv[1]); frozen(root)
try:
    with lock(root/'controller',blocking=False): pass
except BlockingIOError:
    raise SystemExit('Controller is already online. Use the dashboard or ./lite-rounds resume.')
PY
start_viewer() {
  local pidfile="$1" logfile="$2"
  shift 2
  if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    if ! "$runner" - "$(cat "$pidfile")" "$run_dir" <<'PY_CHECK'
import sys
from pathlib import Path
args=Path('/proc',sys.argv[1],'cmdline').read_bytes().split(b'\0')
assert sys.argv[2].encode() in args and b'web' in args, 'Stale viewer PID belongs to another process'
PY_CHECK
    then
      echo 'Viewer PID mismatch; inspect before launching.' >&2
      exit 1
    fi
    return
  fi
  nohup setsid "$@" >>"$logfile" 2>&1 9>&- </dev/null &
  echo "$!" >"$pidfile"
}
start_viewer "$run_dir/dashboard.pid" "$run_dir/dashboard.log" ./lite-rounds web --run-dir "$run_dir" --port 18770
start_viewer "$run_dir/tree-viewer.pid" "$run_dir/tree-viewer.log" ./experiment web --run-dir "$run_dir" --port 18771
"$runner" - "$run_dir" <<'PY_CHECK'
import sys,time
from pathlib import Path
import httpx
root=Path(sys.argv[1]).resolve()
with httpx.Client(trust_env=False,timeout=5) as client:
    for attempt in range(20):
        try:
            data=client.get('http://127.0.0.1:18770/report.json').json()
            if Path(data['run_dir']) != root:
                raise SystemExit('Dashboard port belongs to another run; refusing paid launch')
            response=client.get('http://127.0.0.1:18771/')
            response.raise_for_status()
            for name in ('dashboard.pid','tree-viewer.pid'):
                pid=str(__import__('json').loads((root/name).read_text()))
                args=Path('/proc',pid,'cmdline').read_bytes().split(b'\0')
                assert str(root).encode() in args and b'web' in args
            break
        except (httpx.HTTPError,ValueError,KeyError,FileNotFoundError,AssertionError):
            time.sleep(.5)
    else:
        raise SystemExit('Viewers not ready; inspect dashboard.log/tree-viewer.log. No paid controller launched.')
print('Ready: four 8-CPU slots, GPU0-3, global API cap $150; continuous exploratory rounds.')
PY_CHECK
./lite-rounds resume --run-dir "$run_dir"
nohup setsid ./lite-rounds run --run-dir "$run_dir" >>"$run_dir/controller.log" 2>&1 </dev/null 9>&- &
pid="$!"
echo "$pid" >"$run_dir/launcher-controller.pid"
sleep 2
if ! kill -0 "$pid" 2>/dev/null; then
  echo "Controller exited; inspect $run_dir/controller.log" >&2
  exit 1
fi
echo "Controller PID: $pid (continuous rounds within frozen budget)"
echo 'Dashboard and pause/resume: http://127.0.0.1:18770'
echo 'Trees, candidate code and policies: http://127.0.0.1:18771'
echo "Log: $run_dir/controller.log"
