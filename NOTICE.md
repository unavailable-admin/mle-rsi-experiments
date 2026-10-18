# Source provenance

This implementation retains Dream-RSI as a Git submodule based on
`3365538768/wsl-dream-rsi@3ccf88a209604795f75ec2e0f2f40352dc80b620`.
Its original AGPL-3.0-or-later license and copyright files remain in that repository.

The following MLE files derive from the existing local rsi-eval integration at
`991d855a8c1cc71ea11a7f1512db54a564e5fd13`, including the documented runtime
fixes in the original development workspace:

| New file | Source |
|---|---|
| `src/mle_rsi/containers.py` | `third_party/rsi-eval/src/bench_container.py` |
| `src/mle_rsi/process.py` | Measurement and process-cleanup responsibilities from `src/bench_resources.py`; rewritten as async execution |
| `src/mle_rsi/grade_worker.py` | `src/mle_grade.py`, retained wrapper |

The source files had no per-file copyright/license notices beyond their module
documentation; that documentation is retained or its provenance recorded here.
No additional upstream license is inferred by this notice.

Public validation, three-fold protocols, candidate runtime and task contract
content derive from the existing `mle-rsi-experiments` workspace based on
`7f948d582d5600f07219a10f371d0b3ad5bf73bc`, including its October 17–18 uncommitted
APTOS and recovery fixes. The source workspace and running experiment were read
only. Original functionality and historical entrypoints remain available there;
`legacy/` preserves the committed prior implementation for audit, not execution.

No FML/ALE/suite implementation is imported into the active package. The active
package has no rsi-eval import or import-time sys.path modification.
