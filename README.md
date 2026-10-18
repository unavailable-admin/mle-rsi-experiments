# MLE RSI Platform

新实现位于 `src/mle_rsi/`，Dream-RSI 是唯一的 submodule。旧实验代码和入口归档在 `legacy/`，仅供审计；已有实验继续使用自己的冻结代码，不能用本版本原地恢复旧 campaign。

[设计草案](docs/rsi-agent-mle-architecture-design.md) · [实施覆盖与验收](docs/platform-implementation.md)

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

真实运行使用 `configs/experiments/mle-example.json` 和 `--host configs/hosts/example.json` 的本机副本。示例的服务地址、模型、价格、镜像和路径都是占位值，必须替换。当前 provider 针对兼容 chat API 加独立 billing API；不承诺兼容所有网关。认证值只从配置指定的环境变量读取，不写入发布记录或容器。

初始化固定代码、配置、public 数据、验证划分和镜像身份；真实初始化要求两个仓库均为干净提交。主机需要已准备的候选环境、官方 mlebench 环境、Sysbox Docker 镜像及只读依赖挂载。GPU 还需显式 `gpu_config_dir`，内含对应 Sysbox GPU 配置。物理核只列一个逻辑 CPU，资源租约同时保护其兄弟线程。AFS 如需映射输出 UID，配置 `container_output_uid` / `container_output_gid`；仅修改新执行输出的所有者。

APTOS/Cactus 三折通过任务 `validation: "kfold"` 开启；APTOS 总时限可配置为 3600 秒。不同验证协议使用独立新 run，不混排分数。宿主检查预测、划分和训练记录一致性，不能据此证明任意候选代码内部没有泄漏。

## 预算与恢复

单一 SQLite 账本记录 API 美元、执行秒数和 GPU 分配秒数。训练、宿主 public 评分和官方评分分别保留测量与结算。未知调用保留预留，不盲目重发；显式允许的 504 跳过也不等于免费。容器和宿主进程缺少终态测量时运行阻塞，不假装已完成或重新执行。

追加额度有独立、幂等的审计记录，不改写旧费用，例如：

```bash
.venv/bin/mle-rsi budget-add --run-dir RUN --id extra-001 --dimension api_usd --amount 2 --reason 'Explicit budget extension'
```

每题额度用 `--task TASK` 指定。总额度与每题额度分别约束；增加其中一个不会绕过另一个。相同 `--id` 重复执行不会重复增加额度。

`offline_tier` 支持 baseline/light/max，实际调参和反事实次数通过状态输出汇总。单独 offline refresh、旧数据库导入和分叉不属于本版本的恢复入口。

## 验证

```bash
.venv/bin/python -m pytest tests
.venv/bin/python -m pytest third_party/dream-rsi/my_rsi/tests/test_external_backend.py third_party/dream-rsi/my_rsi/tests/test_external_transport.py third_party/dream-rsi/my_rsi/tests/test_batch_recovery.py third_party/dream-rsi/my_rsi/tests/test_agent_control.py
```

三折原生 fixture 使用装有 pandas/sklearn/Pillow 的 ML Python 运行 `tests/unit/test_*native.py`。容器 fixture 为显式 opt-in，使用合成数据、模拟模型响应和账单，不使用真实 benchmark 数据或付费 API。它的本地主机参数见 `tests/integration/test_container_smoke.py`，不是通用部署配置。

迁入代码来源见 [NOTICE](NOTICE.md)。发布前固定 Dream-RSI gitlink；两个仓库的本地提交尚需分别推送，才可从远端完整克隆。
