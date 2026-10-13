# Dream-RSI × MLE-bench

在固定的 Dream-RSI v26 基线上，为 MLE-bench 接入代码生成、容器训练、公共验证反馈、离线策略回放和最终评分。默认流程：四题各做两轮 online + offline，汇总历史回放选择通用策略，再冻结策略评估 Lite22。

## 代码与依赖

- `mle_rsi/`：任务适配、Gemini 请求与预算、容器执行、实验调度。
- `configs/gemini-dev4-lite22.json`：模型、资源和预算的示例配置。
- `third_party/dream-rsi`：`mle-integration` 分支，当前固定适配提交 `2eb9062123d7370fd37cb3c91eb37f691334bfd7`，基于 `3ccf88a209604795f75ec2e0f2f40352dc80b620`。
- `third_party/rsi-eval`：固定 `991d855a8c1cc71ea11a7f1512db54a564e5fd13`，提供容器执行与 MLE 评分接入。
- `examples/leaf_smoke.py`、`tests/`：不调用付费模型的验证代码。

依赖保留为 submodule，clone 时按主仓库记录的具体commit检出。Dream-RSI修改直接提交至独立的 `mle-integration` 分支，不修改上游main。上游来源：[Dream-RSI](https://github.com/3365538768/wsl-dream-rsi)、[rsi-eval](https://github.com/unavailable-admin/rsi-eval)。上游代码按各自的许可使用。

## 环境准备

需要 Python 3.12、Linux、bwrap、Docker + Sysbox、已准备的 MLE-bench 数据和训练环境。GPU 任务还需要 NVIDIA 容器支持。此仓库不下载数据、不构建训练镜像；准备流程参考 rsi-eval。

```bash
git clone --recurse-submodules https://github.com/unavailable-admin/mle-rsi-experiments.git
cd mle-rsi-experiments
./scripts/bootstrap.sh
export RSI_EVAL_WORKSPACE=/path/to/mle-workspace
export RSI_GEMINI_API_KEY='your-api-key'
cp configs/gemini-dev4-lite22.json configs/experiment.local.json
```

将本地配置中的 CPU、GPU、Docker 镜像、训练环境和模型缓存路径改成实际资源。`${RSI_EVAL_WORKSPACE}` 会在新实验初始化时展开。默认不自动加载 `.env`，请在 shell 中 export；也可通过 `MLE_RSI_PYTHON` 选择已有的 Python 3.12 环境。

工作区需要以下结构：

```text
$RSI_EVAL_WORKSPACE/
  projects/mle-bench/           # MLE-bench 源码及 experiments/splits/low.txt
  data/mlebench/<task>/prepared/public/
  envs/mle-agent/              # 候选训练 Python 环境；路径可在配置中修改
  envs/mle-lite-agent/         # 训练环境依赖的基础环境
  envs/mlebench/               # 官方 grader Python 环境
  tools/python/               # 容器内 Python 运行时依赖
  models/                     # 可选的只读预训练模型缓存
  cache/torch/                # 可选缓存
```

示例镜像和环境路径来自已有运行平台，不意味着任意机器直接 clone 后就能训练。确认这些依赖可用后运行预检；预检会认证读取模型列表，但不生成内容、不调用付费训练。

```bash
./experiment preflight --config configs/experiment.local.json
```

## 开发、冻结与最终评估

```bash
RUN="$RSI_EVAL_WORKSPACE/runs/dream-rsi-mle-001"
set -o pipefail
./experiment init --config configs/experiment.local.json --run-dir "$RUN"
./experiment dev --run-dir "$RUN" 2>&1 | tee "$RUN/development.log"
./experiment freeze --run-dir "$RUN" \
  && ./experiment final --run-dir "$RUN" 2>&1 | tee "$RUN/final.log"
./experiment status --run-dir "$RUN"
```

也可在 init 后执行 `./experiment run --run-dir "$RUN"`。`final --tasks leaf-classification` 可先评估指定题目，再运行不带 `--tasks` 的 final 补齐；已完成任务不会重跑。不要用新目录清零已发生的费用。

默认练习题为 Leaf、Spooky、Denoising、Taxi。一轮 online 计划产生两批、每批两个候选；offline 比较当前策略和一个新策略。两轮保留累计搜索树。最终每题计划四批、每批两个候选，固定策略、不再修订策略。失败或预算不足会导致实际候选数更少。

## 可视化

```bash
./experiment web --run-dir "$RUN" --port 18766
# 在本地电脑另开终端：
ssh -N -L 18766:127.0.0.1:18766 your-server
```

浏览器打开 `http://127.0.0.1:18766`。主卡片、节点详情和在线曲线显示原始验证指标及方向；搜索策略内部仍使用转换后的 score。根节点是未评估占位节点，不代表模型成绩。

## 预算与并行

每题开发和最终评估共用 API $5、GPU 分配时间 2h，以及候选累计墙钟 2h 上限；开发候选执行另限40分钟，每个候选最多15分钟。API开发阶段可使用总额内的剩余预算，最终阶段不重置余额。

默认四个任务槽并行，CPU组互不重叠，每组2核；题内候选训练与模型请求串行。GPU任务独占租用配置池中的空闲卡，每容器最多32GB内存。请自行选择不与其他实验冲突的CPU/GPU。

Gemini 3.1 Pro Preview 使用HIGH thinking，输出上限65,536；请求前按保守金额预留，实际额度受剩余预算限制。未知结果保留预留金额，新请求仍可使用余额；同一个未决请求不会自动重发。已知费用和预留金额在 status 中分开显示。上限是本地预算控制，不能保证供应商异常计费时仍严格符合预估。

## 评估边界与复现

公共验证是候选从公开训练数据划出的固定验证集，不是 Kaggle 公榜。目前检查指标、划分标识和输出文件，尚未对全部任务独立重算候选自报的验证分数。隐藏测试只由最终 grader 评估选定 submission，不反馈给搜索。

这是基于 v26 的 MLE 迁移试验，并非论文逐项复现。报告开发4题、其余18题、全部22题；剩余18题已有历史 baseline 观察，不能称为从未接触的盲测。

init 保存配置、源码哈希和依赖提交；run 保存账本、候选、调用证据、冻结策略和 summary.json。源码变化后默认拒绝恢复旧run。此整理版移除了历史试跑专用的预算迁移命令；正在运行的旧实验应继续使用其原有代码，不要把整理版直接覆盖过去。

```bash
# 测试需要准备好的工作区数据，但使用模拟模型，不产生API费用。
.venv/bin/python -m pytest tests -q
```

运行日志、数据、模型、账单、凭据、缓存和本机配置均不纳入仓库。
