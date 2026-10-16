# Mixed scheduling and medal retirement

This branch starts from `f04f6ed236962cbb162004050ab00b4a8c22d098`, plus the previously uncommitted Lite22 rounds and 5090 repair implementation. It contains the full prerequisite implementation, not only the nine files changed for mixed scheduling and retirement.

GPU candidates normally receive four physical CPU cores and one exclusive GPU. CPU candidates receive eight cores; per-task four/eight-core overrides are supported. The shared pool admits at most sixteen executions, honors configured CPU/GPU IDs, prefers GPU-local CPU blocks, and accumulates blocks for aged eight-core CPU requests. Grading uses eight cores and has concurrency one. API concurrency remains five.

After each complete wave, a valid official private medal freezes that task's winning checkpoint. Frozen tasks receive no further search calls. At migration, all earlier medal checkpoints are considered, even if a later score regressed. Subsequent waves carry frozen medals forward while retaining all 22 tasks in the medal-rate denominator. This is explicitly exploratory private-feedback allocation, not independent blind evaluation.

## Clean checkout on another host

```bash
git clone --recurse-submodules --branch feat/mixed-scheduling-medal-retirement https://github.com/unavailable-admin/mle-rsi-experiments.git
cd mle-rsi-experiments
./scripts/bootstrap.sh
```

`bootstrap.sh` applies the reviewed dependency patches in `patches/runtime/`. The patch tool verifies pinned commits and before/after file hashes, is idempotent, and rejects conflicting edits. The dirty dependency state is represented by these patches; no unpublished submodule commit is required.

If you already have a controller environment, run `python3 scripts/apply-runtime-patches.py` after initializing submodules instead of reinstalling dependencies. A Git bundle can also be cloned locally, then initialize submodules and apply patches.

On the 4090 host, copy a suitable base configuration to an ignored `*.local.json`. Set CPU IDs to one hardware thread per allocated physical core, split `cpu_groups` into disjoint groups of four, keep the available GPU IDs, and add:

```json
{
  "execution": {
    "mixed_resources": {"max_slots": 16, "task_cpu_cores": {}}
  },
  "round_robin": {
    "retire_on_medal": {"from_round": 1, "basis": "official_private_medal", "exploratory": true}
  }
}
```

These are fields to merge into a complete configuration. Also adapt `host_name`, CPU/GPU affinity, image, Python environment, models, data and credentials-file paths. Never reuse the 5090 host's `/opt/...` paths on another host. Do not overwrite a running experiment's frozen checkout. A new experiment or an audited boundary migration is required.

## Manual handoff on the existing 5090 host

Read-only checks (does not start the handoff):

```bash
/opt/mle-rsi-fixes/controller-env/bin/python /mnt/afs/yangsen1/projects/mle-rsi-mixed-medal/ops-5090/start-handoff.py
```

Explicitly start the background handoff and show its status:

```bash
/opt/mle-rsi-fixes/controller-env/bin/python /mnt/afs/yangsen1/projects/mle-rsi-mixed-medal/ops-5090/start-handoff.py --start
```

The program waits for the current round-four controller, compares fixed public candidates at eight/four CPU cores without LLM calls or private grading, applies eight-core exceptions when needed, copies history and the cumulative budget to a separate run, starts the three viewers, and starts round five. Only remaining non-medaled tasks enter search. Failure leaves evidence and does not silently retry potentially charged work. Duplicate launches are rejected. Disconnecting or pressing Ctrl-C stops the terminal display, not the background supervisor.

The existing deployment is `/opt/mle-rsi-next`, with destination `/opt/mle-rsi-next/runs/lite22-medal-001`. Shared source is available on AFS; this handoff does not move the active experiment or automatically switch its storage location. `ops-5090` is a machine-specific operations snapshot, not the 4090 installation entrypoint. Deployment source remains the sealed `/opt/mle-rsi-next/code` copy.

Status and logs:

```bash
cat /opt/mle-rsi-next/evidence/handoff.json
tail -f /opt/mle-rsi-next/evidence/handoff.log
```

After completion, run `/opt/mle-rsi-fixes/controller-env/bin/python /opt/mle-rsi-next/audit_completion.py` to verify original and inherited records, budget, retirement evidence, runtime allocation and monitor endpoints. It expects at least one completed new candidate, so an immediate post-launch audit may still report that condition as pending.

New monitoring ports are 18970 (results), 18971 (trees), and 18972 (resources). Old monitoring remains at 18870–18872.
