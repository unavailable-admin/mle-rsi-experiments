# Spooky 单题 solver 转移实验

目标：在 `spooky-author-identification` 上用累计搜索树进行 20 轮 online + offline，每轮 5 批 × 5 个候选，共 500 个候选名额。搜索策略每轮提出一次修订，与 incumbent 比较（M=2）；offline 用已有 public 历史 replay，不另训练 25 个模型。K2=25、4 个 replay scenario。模型维持 discovery gpt-6-sol medium / developer gpt-6.1-sol high。

检查点为 25、50、100、200、300、500。每个检查点按宿主独立重算的固定 public validation 选 solver，复制 `solver.py` 和当时产生的 `submission.csv`。全部 20 轮 online/offline 完成后，统一对这些固定 submission 做 private grading，**不重新开始 final discovery，不按 private 分数重新选 solver**。最终交付是 500 检查点。没有合格候选则明确记录 no_submission。

与跨数据集 Lasso 转移不同，这里转移的是同一竞赛 public 上选出的方案到 private test；这验证单题搜索收益，不能证明跨任务泛化。候选可以在输出独立验证预测后用全部 public training refit，但宿主没有自动代写或强制执行 refit；导出的 solver/submission 保留真实候选行为。真实本地 readiness 基线不会注入搜索树。

## 启动

在冻结的准备目录执行：

```bash
cd /mnt/afs/yangsen1/projects/mle-rsi-spooky-longrun
bash scripts/start-spooky.sh
```

脚本启动后台控制器、学习曲线页面、原生树/策略页面。CPU0–39 及其物理核 sibling（CPU64–103） 使用与 guarded 相同的锁；guarded 释放后才启动 paid 工作和 48h 时钟。其他任务仍应遵守物理核分配，不能只看瞬时 CPU 利用率。不要用普通 `./experiment run`，它是旧的 dev→freeze-policy→final 流程。

- 进度和曲线：`http://127.0.0.1:18768`，每 30 秒刷新；显示有效候选、不同源码/提交数、debug 成功数、成本和检查点分数。
- 树、每次候选代码、报错和策略：`http://127.0.0.1:18769`。
- 若在本地电脑浏览，用自己的服务器 SSH 别名：`ssh -N -L 18768:127.0.0.1:18768 -L 18769:127.0.0.1:18769 <服务器SSH别名>`。

```bash
tail -f /mnt/afs/yangsen1/runs/dream-rsi-spooky-solver-500-002/controller.log
./single-task status --run-dir /mnt/afs/yangsen1/runs/dream-rsi-spooky-solver-500-002
```

## 资源和停止条件

每个候选最多 900 秒，8 logical CPU / 32 GiB，无 GPU。API 最多 5 路并发，候选训练 5 路并发；CPU 分组为 0–7、8–15、16–23、24–31、32–39，总计 40 CPU、最多 160 GiB。整批完成后仍按原候选顺序提交搜索树，不按完成速度改变选择。并发总耗时尚未实测，粗略预留 8–16h，慢候选和 offline 会影响加速比。API 计费及未结算预留共同受 $20 上限限制；累计候选执行 40h；搜索总墙钟 48h（不含排队和最终 private grading）。这些是硬停止上限，不能保证预算内完成 500 个候选。没有新增付费生成试运行。

搜索提前停止时保留树和已有检查点，但不自动进行 private grading。源文件、验证资产和导出检查点有哈希保护。正常完整结束后再次执行 run 不会追加候选或重发已缓存请求。对中途丢失的 API 响应、执行状态未知的容器、已建立但未完成的 grading 目录，系统会停止并要求核实，不能删除账本或随意重跑。CLI 报错详见 controller.log / single-status.json。

## 产物和解释

运行目录内：`single-report.html` / `single-report.json` 是可离线保存的报告（`status` 会刷新）；`checkpoints/0500/solver.py` 为最终方案，配套 `submission.csv`、`selected.json`、`official-result.json`；`search-complete.json` 封存搜索完成时的检查点哈希。

曲线蓝绿表示 public 历史最优 log loss，橙色为搜索全部结束后评测的 private 检查点。奖牌阈值仅适用于 private。不能把反复使用 public validation 后的改善直接解释为 private 提升，也不要从事后 private 曲线挑最优检查点当最终结果。本实验为单 seed 探索，不是 22 题得牌率或 AIDE/Lasso 的严格复现。

v2 的并发验证记录见 `readiness.json`：模拟 HTTP 并发、CPU lane 隔离、原子执行预算和恢复保护，以及五个真实容器同时运行的检查（为避免干扰 guarded，容器检查每路仅分配一个空闲 CPU；正式配置每路 8 CPU）。mock 结果不表示真实性能；readiness 基线也不是本实验的初始成绩。

## 固定版本来源

本实验代码以主仓库 `mle-rsi-experiments` 的 annotated tag
`spooky-solver-500-ready-v2` 为版本锚点，包含单题入口及其 guarded runtime 依赖。
Dream-RSI 子模块的对应 tag 为 `mle-spooky-solver-500-runtime-v1`；实际子模块
commit 由主仓库 gitlink 固定。运行副本由此 tag 在本机重建，不是新的研发主仓库。
后续开发仍在 `mle-rsi-experiments` 进行，不修改已初始化的运行副本。

本次 tag 和提交仅保存在本机，未推送远端。运行目录的 `version-provenance.json`
记录主仓库和子模块 commit、源码哈希核对结果以及归档位置。

v2 保留 v1 tag，不覆盖原版本。v1 未启动的运行目录 001 及旧源码副本归档保留；新并发运行目录为 002。Dream-RSI 子模块仍沿用已验证的 runtime-v1 提交，本次修改位于 MLE 适配层。
