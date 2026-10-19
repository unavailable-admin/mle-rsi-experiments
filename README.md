# MLE RSI Platform

新实现位于 `src/mle_rsi/`，Dream-RSI 是唯一的 submodule。旧实验代码和入口归档在 `legacy/`，仅供审计；已有实验继续使用自己的冻结代码，不能用本版本原地恢复旧 campaign。

[设计草案](docs/rsi-agent-mle-architecture-design.md) · [第一阶段实施记录](docs/platform-implementation.md) · [第二阶段实现与验收进度](docs/phase2-implementation.md)

[重构归属、接口与搜索流程分析](docs/rsi-agent-refactor-and-search-analysis.md)：通用 RSI 与 MLE 修改清单、候选与反馈字段、上下文管理，以及 online/offline 流程图。

```text
CLI → Controller → Dream-RSI Campaign
                       │ 候选
                       ▼
                 MLE 执行后端
                 资源池 → 容器
                       │ 产物
                       ▼
                 Public 验证 ── 分数/诊断返回 Campaign

Controller 在完整轮次后取得 public 最优结果 → 官方 private 评分 → 继续/退役
```

Controller 管理多题生命周期；Campaign 管理 online/offline。模型适配、账本、发布核验和状态展示支撑上述流程。默认保留原生搜索策略，未启用历史 forced repair、fallback 或 AST 去重。

## 安装与运行

需要 Python 3.12。两个 Python 包通过安装使用，不在导入时修改 `sys.path`。

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e third_party/dream-rsi/my_rsi -e .
.venv/bin/mle-rsi init --config configs/experiments/mock.json --run-dir /path/to/new-verification-run
.venv/bin/mle-rsi run --run-dir /path/to/new-verification-run
.venv/bin/mle-rsi status --run-dir /path/to/new-verification-run
.venv/bin/mle-rsi pause --run-dir /path/to/new-verification-run
.venv/bin/mle-rsi stop --run-dir /path/to/new-verification-run
```

`run` 也用于恢复新格式运行。pause/stop 停止接收新批次及新的 offline 工作，在途批次排空；强制中断保留批次计划和外部证据。不同进程不能同时控制同一 run；不同主机控制同一 run 被拒绝。

真实运行使用 `configs/experiments/mle-example.json` 和 `--host configs/hosts/example.json` 的本机副本。示例的服务地址、模型、价格、镜像和路径都是占位值，必须替换。支持兼容 chat API 和 Gemini native generateContent，两者均需独立 billing API；不承诺兼容所有网关。认证值只从配置指定的环境变量读取，不写入发布记录或容器。

初始化固定代码、配置、public 数据、验证划分和镜像身份；真实初始化要求两个仓库均为干净提交。主机需要已准备的候选环境、官方 mlebench 环境、Sysbox Docker 镜像及只读依赖挂载。GPU 还需显式 `gpu_config_dir`，内含对应 Sysbox GPU 配置。物理核只列一个逻辑 CPU，资源租约同时保护其兄弟线程。AFS 如需映射输出 UID，配置 `container_output_uid` / `container_output_gid`；仅修改新执行输出的所有者。

离线权重、实际环境清单和 GPU 配置的准备与验证见 [候选环境说明](docs/runtime-environment.md)。新 real 初始化先运行禁网环境探测；模型清单进入候选提示词，资产哈希随 release 固定。

APTOS/Cactus 三折通过任务 `validation: "kfold"` 开启；APTOS 总时限可配置为 3600 秒。不同验证协议使用独立新 run，不混排分数。宿主检查预测、划分和训练记录一致性，不能据此证明任意候选代码内部没有泄漏。

## 预算与恢复

单一 SQLite 账本记录 API 美元、执行秒数和 GPU 分配秒数。训练、宿主 public 评分和官方评分分别保留测量与结算。未知调用保留预留，不盲目重发；显式允许的 504 跳过也不等于免费。容器和宿主进程缺少终态测量时运行阻塞，不假装已完成或重新执行。

追加额度有独立、幂等的审计记录，不改写旧费用，例如：

```bash
.venv/bin/mle-rsi budget-add --run-dir RUN --id extra-001 --dimension api_usd --amount 2 --reason 'Explicit budget extension'
```

每题额度用 `--task TASK` 指定。总额度与每题额度分别约束；增加其中一个不会绕过另一个。相同 `--id` 重复执行不会重复增加额度。

`offline_tier` 支持 baseline/light/max，实际调参和反事实次数通过状态输出汇总。独立 offline refresh 在现有公开历史上更新策略；历史导入只进入新 campaign，并校验数据和验证协议、继承费用、生成新 namespace。回退通过导出完整轮次前缀后分叉实现。

## Web、工作流与历史

```bash
.venv/bin/mle-rsi serve --run-dir RUN --bind 127.0.0.1 --port 8080
.venv/bin/mle-rsi refresh --run-dir RUN --id refresh-001
.venv/bin/mle-rsi export --run-dir RUN --task TASK --output NEW_BUNDLE
.venv/bin/mle-rsi import --run-dir NEW_RUN --task TASK --bundle NEW_BUNDLE
.venv/bin/mle-rsi workflow --config WORKFLOW_JSON --run-dir NEW_WORKFLOW
```

Web 使用启动时生成的访问令牌，提供候选树、源码、公开诊断、产物、资源、费用和后台操作。工作流通过阶段依赖、门槛和历史继承声明 development/final 流程；`task_parallelism` 控制题目并发。

D09 修复/探索/fallback 与 D10 AST 去重均为显式可选项，默认关闭。策略规则编入同一份源码供 online、replay 和 tuning 使用。特征缓存也需显式开启，候选只读共享缓存、宿主校验发布；调用方负责声明确定性变换的完整依赖。配置与边界见第二阶段文档。

历史运维脚本在 `legacy/ops-5090/`，实验记录在 `docs/archive/pre-platform/`。这些资料保留原始路径叙述，仅供历史审计，不是当前运行入口。

## 验证

```bash
.venv/bin/pip install -e 'third_party/dream-rsi/my_rsi[test]'
.venv/bin/python -m pytest tests
.venv/bin/python -m pytest third_party/dream-rsi/my_rsi/tests/test_external_backend.py third_party/dream-rsi/my_rsi/tests/test_external_transport.py third_party/dream-rsi/my_rsi/tests/test_batch_recovery.py third_party/dream-rsi/my_rsi/tests/test_agent_control.py
```

三折原生 fixture 使用装有 pandas/sklearn/Pillow 的 ML Python 运行 `tests/unit/test_*native.py`。容器 fixture 为显式 opt-in，使用合成数据、模拟模型响应和账单，不使用真实 benchmark 数据或付费 API。它的本地主机参数见 `tests/integration/test_container_smoke.py`，不是通用部署配置。

迁入代码来源见 [NOTICE](NOTICE.md)。发布前固定 Dream-RSI gitlink；两个仓库的本地提交尚需分别推送，才可从远端完整克隆。
