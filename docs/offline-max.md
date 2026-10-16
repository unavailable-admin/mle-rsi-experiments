# Offline-max: 5090 round four → 4090 half-machine

Base: `2df6a98`, including mixed CPU/GPU scheduling and private-medal retirement.
This is an exploratory cross-host continuation, not a controlled ablation or blind evaluation.
The source experiment and its fourth-round results remain unchanged.

## Resource and search settings

- GPU 0–3; physical CPU 0–31, with CPU sibling leases also protecting 64–95.
- GPU jobs use four cores; CPU jobs and grading use eight. At most eight scheduler
  slots; the four GPUs and 32 cores bound simultaneous default-size jobs to six.
  Each candidate retains the 32 GiB container memory limit. There is no aggregate
  run-wide memory cgroup. Four task loops bound concurrent offline work.
- Keep all 22 tasks in the medal-rate denominator; retire tasks with an earlier
  sealed official private medal and concentrate new work on the remainder.
- Keep 25 new candidates per active task per online round.
- Five offline policy versions (incumbent + four revisions); up to 48 parameter
  trials on four tuning seeds disjoint from four evaluation seeds.
- Replay request budget `min(N, max(25, ceil(0.7*N)))`; virtual/wasted requests
  count too. The node budget overrides legacy K2 and uses per-slot work scoring.
- Counterfactual: four episodes, first eight decision positions, three alternative
  first-parent choices; virtual model calibrated, depth one, two children/parent.
- Preserve parent model identities, API caps, per-task caps, accumulated spending,
  execution/GPU hours and original seven-day admission deadline. No budget reset.

## Commands after the 5090 fourth round finishes

The exporter requires a stopped controller, exactly four completed rounds and
100 candidate slots for every task, all four sealed grading reports, and no
pending/running/uncertain ledger entries. It does not stop the 5090 experiment or
wait for it. A live controller, incomplete round or already-started fifth round
is rejected. The original handoff to another 5090 run must not race this export.

On **5090**, export once to shared AFS (no model calls):

```bash
cd /mnt/afs/yangsen1/projects/mle-rsi-offline-max
MLE_RSI_PYTHON=/opt/mle-rsi-fixes/controller-env/bin/python ./offline-max export \
  --source /opt/mle-rsi-fixes/runs/lite22-001 \
  --destination /mnt/afs/yangsen1/runs/code-releases/lite22-5090-round4
```

This copies immutable public-search artifacts, SQLite snapshots, prepared public
validation data, sealed round submissions and cumulative accounting. Candidate
execution scratch is omitted. The export has a complete SHA256 manifest and is
published only after copying succeeds. An interrupted `.partial` export is never
accepted as ready; inspect it and use another destination rather than overwrite.

On **4090**, start the independent run:

```bash
cd /mnt/afs/yangsen1/projects/mle-rsi-offline-max
./offline-max start
```

Defaults:

- Source: `/mnt/afs/yangsen1/runs/code-releases/lite22-5090-round4`
- New run: `/mnt/afs/yangsen1/runs/dream-rsi-offline-max-001`
- Host template: `configs/offline-max-4090-half.json`

`start` verifies resources, prepares and seals a separate run, starts the three
viewers from this exact code directory, checks their identity, then launches the
background controller. Disconnecting the terminal does not stop the controller.
Repeated start while the controller is alive is rejected. A failed partial
preparation has no ready marker and cannot start paid work.

Each unretired task MUST finish the separately namespaced `offline-max-r0004-*`
refresh before it can admit its fifth-round candidates. The initial refresh wave completes before any task starts new online work.
A failed task is blocked for inspection; healthy tasks may then continue. Historical round-four summaries are retained. Requests,
policy tuning and counterfactual evidence are durably cached; a resume does not
repeat completed refresh work. If tuning/counterfactual did not actually run,
the task is blocked before admitting online work.

The complete history uses its original evaluation IDs. The new search/runtime
fingerprint is stored separately and frozen on first construction; validation
manifest, metric, task contract and description must match the imported source.
Historical absolute diagnostic paths may point at the original host; sealed
round submissions remain available for an inherited incumbent on the new host.

## Monitor and control

```bash
ssh -N -L 19070:127.0.0.1:19070 -L 19071:127.0.0.1:19071 \
  -L 19072:127.0.0.1:19072 <4090-SSH-alias>
```

- Results/control: http://127.0.0.1:19070/
- Trees/policies/replays: http://127.0.0.1:19071/
- Resources: http://127.0.0.1:19072/

```bash
tail -f /mnt/afs/yangsen1/runs/dream-rsi-offline-max-001/controller.log
./lite-rounds stop --run-dir /mnt/afs/yangsen1/runs/dream-rsi-offline-max-001
```

`task-status/*.json` shows `offline_refresh` while refreshing. Successful refresh
receipts are under `offline-max/*.json`, including the selected policy, actual
trial counts, counterfactual count and unchanged pre-online candidate count.
Stop/pause drains already-started offline work and admitted batches. Inspect
blocked-task evidence; never blindly retry an uncertain paid request.

## Local dependency setup

The current 4090 deployment is already prepared at
`/mnt/afs/yangsen1/envs/mle-offline-max`. It is a Python 3.11 overlay with read-only
mounts of `mle-gemini-four` and `mle-lite-agent`, retaining the local PyTorch 2.2.0
CUDA 12.1 environment. Additional pinned packages are scikit-image 0.25.2,
imageio 2.37.0 and tifffile 2025.6.11. The older environments were not modified.
This host template is not a generic installer for another machine. The native
readiness probe imports the advertised libraries, loads local model weights,
runs CUDA forward/backward, and scores a fixed public Leaf submission.

Validation before launch:

```bash
./offline-max check
# Optional repeat of native probes in a NEW verification-only directory:
/mnt/afs/yangsen1/envs/dream-rsi-mle/bin/python scripts/verify_offline_max.py /path/to/new-readiness-run
```

## Verification evidence (2026-10-16)

- Full suite on a clean local clone with pinned submodules and restored patches:
  76 passed, 13 skipped. The skipped tests require other ML environments.
- Native 4090 verification:
  `/mnt/afs/yangsen1/runs/offline-max-native-readiness-003/readiness.json`.
  Four valid four-core GPU candidates used GPU 0, 1, 2, 3; one valid eight-core
  CPU candidate used no GPU. No paid model call or private grading occurred.
- Full offline settings, using a synthetic 100-candidate fourth-round history:
  `/mnt/afs/yangsen1/runs/offline-max-full-readiness-001/verification.json`.
  Five versions evaluated, four tuning reports with `[48, 48, 48, 48]` trials,
  four counterfactual reports; candidate count stayed 100. Identical mock policy
  sources correctly reuse cached replay episodes. This verifies orchestration,
  not real search quality or medal gains.
- The results, tree and resource viewers returned successful responses and the
  expected run identity on ports 19070–19072. Verification viewers were stopped.
- Authenticated model-list GET succeeded; no generation request was made.
- The actual 5090 fourth-round export is not yet imported. It is checked by the
  export/start commands when the user decides the source boundary is ready.
