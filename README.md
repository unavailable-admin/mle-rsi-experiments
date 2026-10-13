# Dream-RSI × MLE-bench 首轮实验

主仓库：`/mnt/afs/yangsen1/projects/mle-rsi-experiments`。
Dream-RSI 是本仓库的 submodule，在霖睿 `3ccf88a209604795f75ec2e0f2f40352dc80b620` 上建立 `mle-task-adapter` 分支；rsi-eval 固定在 `991d855a8c1cc71ea11a7f1512db54a564e5fd13`。适配提交目前只在本地，尚未推送远端。

## 启动

当前机器已安装独立 Python 3.12 环境 `/mnt/afs/yangsen1/envs/dream-rsi-mle`；依赖版本见 `requirements.lock`。配置引用本机 MLE 数据、Docker 镜像、训练环境和只读模型缓存，跨机器需先适配路径。

```bash
cd /mnt/afs/yangsen1/projects/mle-rsi-experiments
./experiment preflight
RUN=/mnt/afs/yangsen1/runs/dream-rsi-mle-gemini-pilot-001
./experiment init --run-dir "$RUN"
./experiment dev --run-dir "$RUN" 2>&1 | tee "$RUN/development.log"
```

建议在 tmux 中执行训练。另开终端启动可视化：

```bash
cd /mnt/afs/yangsen1/projects/mle-rsi-experiments
./experiment web --run-dir /mnt/afs/yangsen1/runs/dream-rsi-mle-gemini-pilot-001 --port 18766
```

查看服务器 `http://127.0.0.1:18766`。本地电脑可先运行 `ssh -N -L 18766:127.0.0.1:18766 用户名@服务器`，再打开本地相同地址。界面来自霖睿仓库，已适配 MLE 指标和执行诊断：选择 campaign 后查看搜索树、候选代码、公共验证指标、stdout、容器资源记录、策略修改及 replay。根节点是未训练占位节点，不代表模型成绩。

四题完整开发结束后，先检查界面和状态，再由你启动冻结与最终评估：

```bash
./experiment status --run-dir "$RUN"
./experiment freeze --run-dir "$RUN"
./experiment final --run-dir "$RUN" 2>&1 | tee "$RUN/final.log"
```

也可在 init 后用 `./experiment run --run-dir "$RUN"` 一次执行开发、冻结和全部最终评估。首次更建议分阶段执行。`final --tasks leaf-classification` 可先验证一个最终任务，再运行不带 `--tasks` 的 final 补齐全部任务。已完成任务不会重跑。

## 实验定义与预算

- Gemini 3.1 Pro Preview 同时生成解法和修改搜索策略，原生 Gemini 接口、HIGH thinking。密钥只由宿主进程读取，不进入候选容器。
- 练习四题：leaf、spooky、denoising、taxi。每题两轮原生 online + offline；汇总四题公共验证历史，用 replay 平均值选择一个通用策略。
- 最终 Lite22 固定策略，仅运行在线解法搜索，不再修改策略。最终私有成绩仅用于报告，不返回搜索或策略选择。
- 同一题开发和最终评估合计 API 上限 $5、GPU 分配时间上限 2h；另设候选累计墙钟 2h 上限。开发阶段先分配 $2.5 / 40min，剩余留给最终阶段。22 题最多 $110 / 44 GPU h，失败尝试计费，不自动重置预算。
- 每个候选最多 15min；四个任务并行，CPU 64–71 分为四组，每题2核；题内训练及模型请求串行。GPU 仅从 4–7 中租用空闲卡。避免占用另一组 MLEvolve 的 GPU 0–3。
- 请求前按保守价格预留费用，按供应商账单结算。账单未知则阻止继续调用，绝不自动重发。金额预留可能导致提前停止；完整开发未结束时拒绝自动冻结，需检查日志后调整新实验设计。
- init 冻结配置与代码哈希；修改代码后必须新建 run。新目录不意味着获准重新花费已消耗预算，请保留并核对旧账单。

## 解释结果的边界

这是基于霖睿 v26 的 MLE 迁移试验，保留累计搜索树、任意节点父节点选择、best-first、虚拟 replay；不是 Dream-RSI 论文逐项复现。首轮缩短轮数，未接入额外超参数和反事实调优。

候选自报公共验证分数；目前检查指标名称、方向、样本标识、固定划分及输出文件，但尚未对所有 22 题统一独立重算公共指标。因此适合先跑通和观察，暂不作为严格可发表 baseline。最终使用官方 grader 评估已选 submission，搜索容器只看到 public 数据。汇报开发4题、其余18题、全部22题；18题已有其他 baseline 观察，不能称为从未接触的盲测。

## 验证与产物

真实 CPU 容器的 leaf 冒烟测试使用预写 sklearn 解法与模拟 LLM，验证训练、产物、online/offline、冻结和最终阶段调度；mock/smoke 模式绝不计入官方成绩。Gemini transport 使用模拟 HTTP 测试，尚未发起真实付费 Gemini 请求，GPU 训练及完整 22 题未验证。

```bash
/mnt/afs/yangsen1/envs/dream-rsi-mle/bin/python -m pytest tests -q
```

每个 run 保存 `frozen-config.json`、`budget.json`、`campaigns/`、`api/`、`outcomes/`、`policy-freeze.json`、`frozen-policy.py` 和 `summary.json`。`status` 的分组完成数指最终评估完成数，开发进度见后面的逐任务状态。页面用于观察，逐笔预算以 `budget.json` 为准。

## 代理连接修复（2026-10-13）

本机 TeamO 连接设置 `model.trust_env=false`，避免继承不可用的 SSH 会话 HTTP 代理；这是该服务的显式配置，不改变系统代理。preflight 现在会认证读取模型列表并确认模型存在，仍不会发起付费生成。开发过程逐题打印完成/停止原因；遇到未决请求立即停止后续任务。pilot-001 的两次 ProxyError 发生在 HTTPS 代理隧道建立阶段，训练尚未启动。恢复目录 pilot-002 保留旧记录的来源及核对说明，正式开发仍由用户启动。

## 并行与截断修复

霖睿 v25 配置的 discovery/developer 输出上限均为65,536；此前本接入分别只有16,384/8,192且启用HIGH thinking。pilot-002多次返回MAX_TOKENS，没有有效训练结果，费用仍计入预算。

新配置保留Gemini HIGH thinking，两类请求输出上限均提高到65,536。按剩余美元预算计算实际允许上限；若预算不足以预留至少32,768输出tokens，明确停止，不发送明显过小的请求。每题请求串行结算，四题之间可并发。这个改动尚不能保证模型永不截断。

并行分配：leaf CPU64–65，spooky CPU66–67，denoising CPU68–69，taxi CPU70–71。最终22题按固定任务顺序轮流映射到四个槽；一个槽同一时刻只跑一题。控制进程也限制到CPU64–71。GPU任务独占租用GPU4–7；MLEvolve预留GPU0–3和CPU0–63，不使用它暂时空闲的预留资源。每个训练容器内存上限32GB，四题最多128GB。不能将本次每题2核结果与原8核配置无说明混用。

旧run冻结的源码不随修复覆盖；新run必须继承此前每题的已知费用，不能以换目录重置预算。pilot-003继承pilot-002账本，尚未启动付费生成。Taxi有两次取消且账单未知的请求，会保留预留款并阻止该题重新调用；其余三题可独立继续。四题完整开发不能在Taxi未解决时冻结。启动：

```bash
cd /mnt/afs/yangsen1/projects/mle-rsi-experiments
RUN=/mnt/afs/yangsen1/runs/dream-rsi-mle-gemini-pilot-003
set -o pipefail
./experiment dev --run-dir "$RUN" 2>&1 | tee "$RUN/development.log"
# 另开终端，先Ctrl-C关闭旧viewer，再运行：
./experiment web --run-dir /mnt/afs/yangsen1/runs/dream-rsi-mle-gemini-pilot-003 --port 18766
```

重启viewer并刷新浏览器可看到新的分数说明。无有效候选时显示“暂无有效结果”，根节点明确标注未评估；不显示相对占位根节点的夸张提升百分比。

## 未决账单与预算恢复修复

未决API请求保留全额预留，但不再禁止该题发起新的、预算内的请求。原请求ID仍禁止自动重发。开发允许使用每题$5总额中的剩余部分；最终评估与开发继续共用该上限，不再额外锁死开发$2.5。

`./experiment upgrade-budget --run-dir RUN` 是显式迁移命令：必须获得实验控制锁，备份原冻结配置与来源版本，保留账本原样。只将能证明未进入transport的本地LimitReached请求恢复为可发送，并归档对应的停止结果，保留已完成轮次与候选。它不调用付费模型。其他源码变化不会被这个迁移命令接受。

pilot-003当前运行结束后，由已安排的本地修复进程自动执行以上迁移。修复进度见该run的 `budget-fix-apply.log` 和 `budget-fix-apply-status.json`。完成后在原主仓库执行 `./experiment dev --run-dir /mnt/afs/yangsen1/runs/dream-rsi-mle-gemini-pilot-003` 续跑；不要重新init或新建目录重置预算。
