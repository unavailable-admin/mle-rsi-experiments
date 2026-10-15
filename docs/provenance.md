# 版本来源与成绩对应

主仓库 main 合并远端初始整理版 `1fab911` 与实验分支历史，保留两条历史，不强推替换原 main。v1/v2 tag 保持原指向；更新只影响 main，新实验必须重新初始化。

- `spooky-solver-500-ready-v1`：`e336728d99e42f7d382a7e0042acc5793ce22668`。
- `spooky-solver-500-ready-v2`：`2a4320636b185ccd4ec459770f142756614b8924`。
- `guarded-lite22-12of22-20261014`：从实跑冻结目录复制后提交，98 个运行源码文件与原 `frozen-config.json` 哈希逐一一致；包括实际生效配置及逐题结果。实测 12/22 得牌，22/22 有效。

本次 diversity 合并之前，main/v2 的 Dream-RSI gitlink 为 `6d27aa3f5ae39ae86a924a3794a116f8388a114e`，发布在上游仓库的独立分支 `mle-spooky-solver-500`，tag 为 `mle-spooky-solver-500-runtime-v1`。准确 guarded tag 使用 `mle-guarded-lite22-12of22-20261014` 标记的运行时提交。上游 main 没有被本次整合改动。

rsi-eval 固定 `991d855a8c1cc71ea11a7f1512db54a564e5fd13`。

v2 相对 guarded 的主要运行差异：单题入口和可选并发、原子执行预算、manifest 字典访问提示修正、长祖先路径摘要上限。guarded 配置不启用可选并发；其短轮数也不会触及长路径摘要上限，但提示词修正仍可能影响模型生成。新调用和硬件波动也影响结果，不能承诺重新运行等于 54.5%。

原始运行目录：`/mnt/afs/yangsen1/runs/dream-rsi-gpt-firstbatch-guarded-001`。运行中的 Spooky 使用独立冻结副本，不因本次 main 合并而变化。

## Spooky diversity and public continuation (2026-10-15)

The diversity runtime adds an optional pre-evaluation novelty gate to the pinned
Dream-RSI discovery agent. Its default is disabled, preserving other experiments.
The parent repository supplies AST deduplication, bounded regeneration and batch
roles, plus a sequential queue and explicit public-only continuation preparer.
The submodule change is published on `mle-spooky-diversity`, not upstream main.
The runtime commit is `72e8d10` and the main repository gitlink records it; the v1/v2 tags above retain
their historical runtime identities.

Evidence roots: `/mnt/afs/yangsen1/runs/dream-rsi-spooky-diversity-100-001` and
`/mnt/afs/yangsen1/runs/dream-rsi-spooky-diversity-200-001`. The latter inherits 100
public candidates, continues to 200, and records that the operator previously saw
private evaluations. It is not an independent replication. Final public loss was
0.29498900165030173; independently graded private loss was 0.28661 (bronze).
Experiment artifacts, credentials and raw sessions are not included in Git.

Validation before publishing: repository `python -m pytest -q` passed 46 tests,
with 10 environment-dependent tests skipped. This includes bounded duplicate
regeneration, persistent novelty claims, role assignment and queue safety checks.
Both repository and submodule `git diff --check` passed. The completed live run
provides separate execution evidence; skipped offline tests are not live checks.
