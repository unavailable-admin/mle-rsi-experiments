# Dream-RSI × MLE-bench

主仓库的 `main` 是统一开发入口，包含 guarded Lite22 和 Spooky 单题累计搜索。历史实验由 tag 固定；不要用 main 的新代码覆盖正在运行的冻结副本。

| 版本 | 用途 |
|---|---|
| `guarded-lite22-12of22-20261014` | 实测 **22/22 有效、12/22 得牌（54.5%）** 的准确运行源码与实际配置 |
| `spooky-solver-500-ready-v1` | 单题 500 候选准备版，题内串行 |
| `spooky-solver-500-ready-v2` | 单题 500 候选并发版，5 路 API + 5 路候选执行 |
| `main` | 合并实验实现和原远端安装能力后的持续开发版本 |

54.5% 是一次完整运行的观测值。重新调用模型生成候选，即使使用相同源码和配置，也不保证相同分数或奖牌数。准确版本和逐题结果见 [来源说明](docs/provenance.md) 与 [成绩记录](docs/guarded-lite22-result.json)。

## 安装

需要 Python 3.12、Linux、bwrap、Docker + Sysbox、已准备的 MLE-bench 数据和候选训练环境；GPU 任务还需要 NVIDIA 容器支持。仓库不自动下载数据或训练镜像。

```bash
git clone --recurse-submodules git@github.com:unavailable-admin/mle-rsi-experiments.git
cd mle-rsi-experiments
./scripts/bootstrap.sh
export RSI_EVAL_WORKSPACE=/path/to/mle-workspace
# 按所选模型导出 RSI_GPT_API_KEY 或 RSI_GEMINI_API_KEY。
```

`.env.example` 是说明模板，不会自动加载。入口优先使用 `MLE_RSI_PYTHON`，否则使用仓库 `.venv/bin/python`；在原实验服务器兼容既有 controller 环境。配置内 `${RSI_EVAL_WORKSPACE}` 在读取新配置时展开，不修改已有运行配置。

工作区需要 `projects/mle-bench`、`data/mlebench/<task>/prepared/public`、候选 Python 环境、`envs/mlebench` 官方评分环境及 `tools/python` 容器运行时。GPU 配置、Docker 镜像、环境路径和预训练模型缓存需要匹配本机。GPT 实验配置保留原服务器路径，迁移时复制成 `*.local.json` 修改；`configs/gemini-dev4-lite22.json` 保留可展开环境变量的通用示例。

## Guarded Lite22

开发阶段：4 题，每题 4 轮 × 4 批 × 2 候选，轮间更新策略。最终阶段：冻结策略，在 22 题各新搜 2 批 × 2 候选，不进行 offline。最终 private 分数不回传搜索。

在独立 checkout 使用准确历史版本复跑：

```bash
git checkout guarded-lite22-12of22-20261014
git submodule update --init --recursive
# 先按本机环境检查配置和可用 CPU/GPU。
./experiment init --config configs/guarded-lite22-12of22.json --run-dir /path/to/new-run
/path/to/controller/python -m mle_rsi.supervisor --run-dir /path/to/new-run
```

该 tag 的配置记录实际 API 总上限 **$17.843318**（原合计 $20 已扣除 preceding firstbatch 消耗）；每任务 $5、开发每任务 $3。不要把历史“两个运行合计 $20”的约束误读成一个新运行会自动扣除另一个运行的账本。

main/v2 也能使用 guarded 配置：该配置不设置题内并发，因此保留 API 和训练串行。修正后的提示词等与准确历史源码有差异，属于同方案复跑，不是逐字节复现。默认配置 `gpt-firstbatch-guarded-dev4-lite22.json` 的新运行上限为 $20；准确历史有效配置为 `guarded-lite22-12of22.json`。

## Spooky 单题

20 轮 × 5 批 × 5 候选，累计 500 个候选名额，每轮 offline；跨轮继承全部树节点。搜索结束后直接测试 public 选出的检查点 solver/submission，不另开 final 搜索。5 路训练每路 8 CPU、32 GiB，总计 40 CPU、最多 160 GiB，无 GPU；CPU 锁覆盖物理核兄弟线程。

当前服务器已固定的运行副本和启动命令见 [单题说明](SPOOKY_EXPERIMENT.md)。学习曲线端口 18768，原生树/代码/策略端口 18769；它们读取同一个运行目录。已有实验只能使用自身冻结源码恢复。

## 验证与边界

宿主固定 public 划分、独立重算预测分数并校验 submission。它不能单凭输出证明任意生成代码内部完全没有验证泄漏，需结合代码审计。各版本是组合改动和单次随机生成实验，得牌变化不能直接解释为单一改动的因果效果。

```bash
/path/to/controller/python -m pytest tests/test_experiment.py tests/test_provider.py tests/test_first_batch.py tests/test_gpt_halfday.py tests/test_repair_guard.py tests/test_single_task.py tests/test_parallel_candidates.py tests/test_portable_setup.py
/path/to/ml/python -m unittest discover -s tests -p 'test_*native.py'
```

来源、固定 commit 和历史开发说明分别见 [provenance](docs/provenance.md) 和 [历史记录](docs/experiment-history.md)。
