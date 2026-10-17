# 5090 / 4090 实验统一交接（2026-10-17）

分支累计改动、context overflow 缓解及当前限制见 [bug-fix 汇总](docs/bug-fix-summary-20261017.md)。本次提交前回归验证 50 项控制器测试及 1 项三折评分测试通过；依赖本地修改由 patches/runtime 完整版本化，固定子模块基线不变。

## 最新：用户授权立即中断并回退（2026-10-17）
用户接受丢弃未完成轮次，不再等训练自然结束。已停止自动等待脚本，强制停止所属控制器及10个训练容器，仅影响该实验。各题恢复至最近 seal+official-result 完整边界：APTOS16、Dog8、Jigsaw12、Taxi16、RANZCR12、Melanoma12、TabularMay13；Cactus已退役。备份DB/配置/账本及回退脚本在实验 `evidence/forced-boundary-restart/`。
原始预算账本逐项保持不变；在途/504未知记录纳入 lineage.historical_unsettled，保留所有费用预留；新请求/执行使用 rollback-20261017 命名空间避免冲突。新控制器PID1083455。之前 PID1047779 以及等待恢复PID1079795均不再运行。以前的“Melanoma待解除阻塞”条目已由此次回退取代。此次恢复没有实现将来504自动重试，仍是待改进项。

## 切换状态补充（2026-10-17 16:43 UTC）
旧控制器已正常退出。第一次补丁启动失败：漏迁移 metadata.continuation_search_protocol（implementation_hash 改变），无候选重跑。已逐题验证原配置、旧 implementation hash 和原 search hash，仅更新未退役题的 runtime implementation_hash/search hash，评分 protocol_hash 与预算保持不变；证据在实验 evidence/offline-timeout-switch/runtime-migration*.json。新控制器 PID 1047779 已进入 running，APTOS R17 已进入运行。
另发现 Melanoma 被未结算请求保护阻塞：stage2:dev-siim-isic-melanoma-classification:tree-0012:r3:a2:response:0，HTTP 504、uncertain，保留 0.861844 美元预留；未重发、未清账。当前不可声称全部任务恢复正常。

## 待部署：offline 回放超时恢复（2026-10-17）

用户随后授权开始切换（“现在可以么”）。已于 2026-10-17 15:45 UTC 请求优雅 stop，并启动独立自动切换进程 PID 998609。脚本等待控制器正常退出，再部署 4 个 runtime 文件、审计解除 APTOS 阻塞并启动续跑。不可重复启动切换脚本；状态见 `/mnt/afs/yangsen1/experiments/lite22-cactus-reset-001/evidence/offline-timeout-switch/status.json`，日志 `switch.log`。出现意外 blocked 或用户撤销 stop 时会停止自动部署。
验证：35 项不同的相关测试通过（包含 7 项新增恢复测试）；1000 版本存储压力测试主动中止，未计为通过。原 offline 中断恢复测试单独通过。补丁干净基线重建和 diff 检查通过。
开发补丁新增 PolicyTimeout 分类、三次有持久记录的本地回放重试、历史超时缓存审计恢复，以及耗尽后沿用已验证策略的明确降级收尾。新池分数保持 null，候选/账本不改。详见 docs/stage2.md 末节。
APTOS R17 原因：scenario 0 等待子进程超时，同策略另外 3 场景成功；隔离复核同冻结池 hash、同场景、同 8 秒限制，5 步回放成功，总计约 0.184 秒。原日志不足以确定当时的环境延迟来源。截至启动等待脚本时，运行代码及 blocked 状态未变；之后以切换状态文件和 deployment.json 为准。


新 Codex 对话先读本文。本文是当前工作的唯一交接入口；下方旧文档仅供查证，不能覆盖本页最新状态。

## 自动恢复修复已部署（2026-10-17）

用户已授权修复 Cactus 连接失败和 Dog 策略回放故障，并实现自动恢复。旧控制器已正常排空已接收批次并退出。`lite22-cactus-reset-001/evidence/auto-recovery/deploy.py` 已使用 `tested-code/` 中通过测试的 5 个源码文件完成部署，审计恢复 5 个已证实未发送的 Cactus 请求，保留原账本全部费用和预留，并启动新控制器 PID 732372。不要另外启动第二个控制器；实时状态看新 run，部署证据见 `deploy.log` 和 `deployment.json`。

38 项相关测试通过；真实 Dog R8 的 4 个批次/195 个候选已完成零 API、零训练回放核验。详细规则见 `docs/stage2.md` 自动恢复一节；只重试已证实未发送的连接失败，未知结果请求继续保留预留并阻塞。历史批次回放故障沿用已提交决策，不重新选择旧父节点。全部费用预留、已收费用和预算上限保持。

## 最新进展：独立逐题续跑与 Cactus 三折重启（2026-10-17）

本节覆盖下方历史状态。用户已授权启动新实验，不追加预算：`/mnt/afs/yangsen1/experiments/lite22-cactus-reset-001/{code,run,evidence}`。旧 `lite22-stage2-001` 控制器已正常停止并结算在途 Dog 候选；旧目录为不可变历史依赖，必须保留，不要再启动它。

- Cactus 空搜索树、初始策略，从 R1 开始；固定 3 折分层（seed=20261017），每折训练 9450 / 验证 4725 行，全部 14175 行各有一次折外预测，以三折平均 AUC 选优。最终相同配置全量重训；不继承旧候选或排名。
- 其余 7 题继承停机时精确状态：Dog 已完成 R7、保留 R8 前四批（195 节点）；APTOS R16；Taxi R15；Tabular May R12；Jigsaw、RANZCR、Melanoma R11。验证协议不变；14 获牌题封存。
- 各题独立推进自己的下一轮，不追齐轮号。原资源限流、累计费用、预算上限、截止时间保持不变；Cactus 新请求命名空间 `cactus-reset1`。
- 已于 2026-10-17 11:34 UTC 启动最终冻结版本，控制器 PID 643955，11:36 UTC 进入 `running / independent`，8 题已按自身下一轮入队、blocked={}；具体实时状态以新 run 的 `round-status.json` 和 `task-status/` 为准。三网页沿用 19170/19171/19172，启动须显式指定新 run。详情见 `docs/stage2.md`。
- 57 项不同测试覆盖调度、续跑、三折评分、提示和网页缓存；首次总览读取历史超过旧等待时限，已修复重复解码并放宽等待后启动。证据在新实验 `evidence/`，包括 `verification.json`、`kfold-native-tests.log`、`kfold-final-regression-tests.log`、`dashboard-final-tests.log`、`kfold-prompt-tests.log`、`upgrade-kfold.log`；先前未启动的单划分配置归档于 `single-holdout-before-launch/`。

## 第二阶段最初准备时的状态（历史）

本节及 `docs/stage2.md` 覆盖下方第一阶段整理时的授权和待办状态。已按用户要求实现十项待办的工程支持，并准备独立新实验。用户明确授权新增 API 总计 $150、每题 $15、每题执行/GPU 各 50 小时；历史账本和未结算预留完整保留。不自动启动训练或生成请求。

- 开发分支仍为 `bug-fix`。新部署 `/mnt/afs/yangsen1/experiments/lite22-stage2-001/code`，新 run `/mnt/afs/yangsen1/experiments/lite22-stage2-001/run`。
- 已完成共享存储迁移：新实验代码、结果、日志和证据统一位于上述实验目录；原 `/opt/mle-rsi-stage2` 副本已清理。22 题配置加载及数据库完整性检查通过，账本保持不变，迁移专项测试 10 passed。已有 Python/模型/GPU 配置仍沿用本机运行依赖。
- 14 题得牌封存；其余起点：Dog R7，Cactus/Melanoma/Jigsaw/RANZCR R11，Taxi R15，Tabular May R12，APTOS R16。完整公共历史导入，后续轮中搜索不导入，但全部费用继承。
- 三个只读查看页面已准备在 19170/19171/19172；控制器未启动，control=pause，没有新增 API 请求或训练。具体启动和 SSH 隧道命令见 `docs/stage2.md`。
- 最终离线测试 87 passed、13 skipped；证据位于 `docs/evidence/20261017-stage2/`。运行环境和认证模型列表检查通过，无付费 API 调用。
- cumulative policy 输入去掉重复的旧整树，与 replay 一致；Cactus R11 输入从 25,578,723 字节降至 340,980 字节，可见节点和可选父节点均保持 251。
- 默认 offline baseline；新增轻量/max 配置与实际次数记录。Tabular May/RANZCR/Melanoma 定向上限 1800 秒，其余 900 秒。新候选声明 CPU/GPU、记录验证/最终训练配置，并可原子封存完整输出、缓存确定性冻结特征。
- Cactus 新划分及两份公共 AUC 并列候选复核计划在 `/mnt/afs/yangsen1/experiments/lite22-stage2-001/evidence/cactus-recheck-boundary`；现已完成两份冻结代码的新划分重训，结果见下。其余同代码时限配对和 offline 强度对照的实测收益仍待实验。
- 旧两笔执行 running 和一笔 API uncertain 原样保留预留；新 run 按封存 lineage 隔离历史请求，使用 stage2 请求/执行命名空间。旧费用缺失不伪造结算。

### 启动后的权限修复（2026-10-17）

用户启动后，Dog R8 首批 5 个候选因共享文件权限拒绝而退出，API 合计 $0.1242282。已停止控制器，保留失败 attempts 和全部费用；未重发请求。原因是容器映射 UID 无法读取 640/750 的 AFS 输入。现启动前调整 runtime/prepared 的非秘密输入读/遍历权限，execute 在创建 agent.py/task.json 后显式调整只读权限，兼容 umask 0027。凭据、API 日志、外部符号链接目标不改权限。真实 Sysbox GPU 配置容器验证 73 个输入可读、输出及缓存可写；无训练/付费调用，专项测试 11 passed。部署源码变更和冻结哈希更新已留证于 `evidence/permission-fix/`，账本和资产内容未变。当前 stopped，用户用 `stage2 start` 继续原 run，不清空历史或预算。

### Cactus 新划分实测复核（2026-10-17）

用户已授权执行。仅比较预先冻结的 R1 solver 与 tree-0001:n5；划分 seed=20261017，训练 seed=0，相同 GPU7/8 CPU/900秒上限，无生成 API、无私榜评分。原 solver AUC=0.9999940292、logloss=0.00310920；对照 AUC=0.9999973463、logloss=0.00157595。新验证集 2835 行，573 行与旧验证集重合。AUC 差值 0.0000033171，1000 次配对分层 bootstrap 95% 区间 [-0.0000079611, 0.0000179124] 包含0。能打破旧验证满分并列，但单划分/单训练种子不足以证明稳定优胜或得牌收益；未修改主实验划分、选优或提交。两次成功训练及一次缺 task 字段的训练前失败合计约59.69秒执行/GPU时间，均计入主 run 的 Cactus development 账本，API新增0。证据：`docs/evidence/20261017-stage2/cactus-recheck/analysis.json`；完整训练产物在实验 `evidence/cactus-recheck-boundary/retrain-seed0/`。

## 第一阶段整理时的授权与工作边界（历史记录）

用户要求整合代码、写十项改动计划并提交分支，方便新对话继续。已授权停止本机 5090；没有授权在此次整理中启动新实验、追加预算或实施十项效果优化。4090 仅查看，没有操作它的控制器。下一对话执行新任务时，以用户的新指令为准。

本次只整合已有实现、补充文档及离线证据。不要把待办表理解为已经完成。不得为恢复而清空预算、伪造节点或重发结果不确定的付费请求。

## 一个开发仓库、两个主要实验目录

唯一后续开发仓库：`/mnt/afs/yangsen1/projects/mle-rsi-experiments`

当前分支：`bug-fix`。不要默认 checkout main；main 不含这次整合。

| 项目 | 5090 主实验 | 4090 offline_max 对照探索 |
|---|---|---|
| 主机（记录） | `10-118-92-147` | `10-118-247-110` |
| 最新实验目录 | `/opt/mle-rsi-next/runs/lite22-medal-001` | `/mnt/afs/yangsen1/runs/dream-rsi-offline-max-001` |
| 实验实际代码 | `/opt/mle-rsi-next/code` | `/mnt/afs/yangsen1/projects/mle-rsi-offline-max` |
| 控制器 Python | `/opt/mle-rsi-fixes/controller-env/bin/python` | 启动器及环境见 `offline-max`、`docs/offline-max.md`；ML 环境 `/mnt/afs/yangsen1/envs/mle-offline-max` |
| 资源 | 8 GPU、64 物理核、最大16槽 | 4 GPU、32物理核、最大8槽 |
| 监控总览/树/资源端口 | 18970 / 18971 / 18972 | 19070 / 19071 / 19072 |
| 检查时状态 | 已按用户要求停止；控制器退出，active/waiting为空 | 共享记录为 stopped；没有远程进程存活验证 |
| 最新进度 | APTOS进入R17，其余7题已阻塞 | 健康任务进入R11后停止，5题阻塞 |
| 累计得牌 | 14/22 | 14/22，继承R4奖牌，无新增 |

5090于2026-10-17 08:11:33 UTC停止（北京时间16:11:33）。先通过官方stop禁止新批次，再终止核实属于本实验的最后一个APTOS训练容器，控制器完成收尾退出。这个候选属于人工中止，不应用于估算自然失败率。监控服务保留，其他人的容器未动。

**这是开发入口的统一，不是把所有历史依赖物理搬走。** `/opt`是5090本地盘；4090看不到它。新代码不能直接覆盖冻结运行代码，否则来源校验会失败。旧实验和代码目录暂不删除：历史绝对引用、环境、模型等仍依赖它们。

必要历史依赖（只读）：

- 5090 R1–R4源实验：`/opt/mle-rsi-fixes/runs/lite22-001`；原环境/模型也在`/opt/mle-rsi-fixes`。
- R4跨机导出：`/mnt/afs/yangsen1/runs/code-releases/lite22-5090-round4`。
- 旧共享开发目录`mle-rsi-mixed-medal`与`mle-rsi-offline-max`保留作来源/运行依赖，不再作为后续开发入口。
- 两机都不是整齐停在一个共同轮次。恢复必须逐题核对checkpoint、节点、attempt、账本及评分封存，不能声称“统一R17边界”或直接复用仅接受完整R4的导出器。

## 本分支包含哪些已有改动

继承的提交链（共同基线`f04f6ed`）：

1. `2df6a98`：5090混合4/8核调度、GPU独占、得牌退出、迁移及监控接管，含此前未提交的多轮基础设施。
2. `c408563`：4090 offline_max配置、跨机导入、离线刷新、调参/反事实回放及验证入口。
3. `aa6e699`：启动器导入前固定仓库、验证导入资产。
4. `556bbfc`：offline_max验证与启动交接文档。
5. 本次整合提交：收录4090未提交恢复逻辑、测试、历史操作脚本、本文与离线证据。

额外收录的4090未提交实现：

- `mle_rsi/rounds.py::round_slot_evidence`：核对5批×5次durable attempts，区分实际节点与明确中断的slots；不伪造中断节点。这采取“25次尝试”而非“保证25个物化节点”的语义，后续需要明确协议并补异常覆盖测试。
- `unresolved`：对于已有持久化`model_request_skip`标记的特定dream不确定请求，允许继续独立工作，但保留费用预留；不适用于所有不确定请求。
- `tests/test_rounds.py`：相应dream skip/预算不释放测试。
- `docs/archive/4090-recovery-20261016/*.txt`：一次性恢复脚本与旧staged源码的逐字归档；含特定实验的状态修改，**禁止作为常规启动/恢复命令执行**。历史干预证据仍在4090实验的`interventions/`。

第三方代码采用“固定submodule基线＋版本化补丁”：`patches/runtime/{dream-rsi.patch,rsi-eval.patch,manifest.json}`。4090当前子模块改动全部与manifest相符。运行`python3 scripts/apply-runtime-patches.py`可核验/应用，重复运行幂等。`git status`里的两个小写`m`子模块是已记录补丁的应用状态，不是漏提交。

原目标仓库的未提交工作已预先保全：git stash说明`Before repository consolidation 2026-10-17`，独立备份`/mnt/afs/yangsen1/runs/code-releases/consolidation-backup-20261017`。旧多轮实现被后继提交覆盖；独有历史交接文档也已保留。不要盲目stash pop把旧实现覆盖回来。

## 最新问题与实验证据

### 5090

| 任务 | 已评分到 | 当前故障或约束 |
|---|---:|---|
| Dog Breed | R7 | R8内部完成但199/200节点 |
| Cactus | R11 | R12为299/300；有Docker/Sysbox查询进程异常 |
| Melanoma | R11 | R12为299/300；有900秒API超时；执行累计约49.78h |
| Taxi | R15 | R16为399/400；容器kill遇已退出409错误 |
| Jigsaw | R11 | R12触及每题50h执行上限 |
| RANZCR | R11 | R12触及每题50h执行上限 |
| Tabular May | R12 | R13触及每题50h执行上限 |
| APTOS | R16 | R17人工停止，尚未完成 |

共同代码缺陷：online允许批内部分失败、提交其余节点；固定五批后标记ROUND_COMPLETED，5090外层却要求累计节点数恰好round×25，导致任务永久blocked。4090已有槽位核算的局部修复已收录，但尚未部署到5090，也不代表全部恢复问题已解决。

账本还出现已结束执行残留running（Cactus/Taxi），恢复前必须查容器真实状态、产物和实际费用后对账。API不确定结果不能按“没花钱”处理。总API预算150美元、每题15美元；每题累计执行/GPU限制180000秒，历史支出继承，不重置。

### offline_max是否值得默认开启

当前决定：**下一阶段不默认全开，保留可选功能。** 不是证明该方法无效，而是现有实测收益不足以支持全任务默认支付额外开销。

配置：5策略版本（含incumbent），每版本最多48次调参、4个调参场景，回放预算随N增长约0.7N，反事实4 episodes×前8个位置×3个备选；online仍每题每轮25候选。实际receipt在4090实验`offline-max/*.json`，部分版本试验次数不足48，不能说全部跑满。

| 同轮private比较 | 4090 offline_max | 5090原策略 |
|---|---:|---:|
| Dog Breed R5，低好 | 0.30137 | 0.30464 |
| Dog Breed R6，低好 | 0.30137 | 0.30155 |
| Jigsaw R5，高好 | 0.98222 | 0.98240 |
| Taxi R5，低好 | 4.02074 | 3.99606 |
| Tabular May R10，高好 | 0.99584 | 0.99682 |

offline没有新增奖牌；Dog Breed有局部积极信号，其他任务多持平或落后。不同硬件、随机性、恢复干预和阻塞使其不是严格因果对照。

4090新增可靠性问题：Jigsaw/Melanoma/RANZCR报Policy failed while receiving view（具体底层原因未定位）；Dog Breed上下文158574 tokens超过150000；Taxi请求RemoteProtocolError结果不确定。目录中有RANZCR超时skip、Dog槽位核算的干预证据。正式对照必须从同一逐题起点、同机资源和相同新增预算分叉。

## 十项改动清单（计划与已实现部分分开）

| # | 改动 | 当前状态与具体动作 | 验收重点 |
|---|---|---|---|
| 1 P0 | 中断候选后的轮次收尾和恢复 | 4090局部槽位核算已收录；明确“attempt slots还是实际节点”协议，处理部分提交、停止、续跑 | 单候选中断不永久卡死；不伪造节点；恢复不重复调用/评分 |
| 2 P0 | 容器异常与账本对账 | 待做；处理退出时top/kill竞态，保存真实退出状态和费用证据；修复旧残留须单独审计 | 不因容器已退出而遗留running；不误杀其他实验 |
| 3 P0 | 预算终止与追加预算协议 | 待做；独立显示budget_exhausted，区别故障；新阶段追加预算显式记录 | 历史费用不清零，含未结算预留；预算耗尽不再错误宣称健康运行 |
| 4 P0 | 策略进程可靠性、上下文控制 | 待做；记录退出码/stderr/输入大小，查明接收失败；有边界的证据筛选和已验证策略回退 | 长历史不会无界增长；回退可审计；不静默丢关键证据/重试不确定请求 |
| 5 P2/可选 | offline强度分档与对照 | 全开实现已收录但下一阶段默认不启用；可比较基线/较低强度/max | 记录真实调参次数、成本、时长和在线收益；先选Dog Breed等有信号任务 |
| 6 P1 | 验证与最终提交训练一致 | 待做；优先Melanoma，统一模型配置或让选优反映实际最终模型，预留最终训练时间 | 验证900/400轮、提交350/220轮的隐藏降级可检测；不得以私榜选solver |
| 7 P1 | 按候选需求路由CPU/GPU | 待做；Jigsaw词法CPU、BGE/Transformer需要GPU请求；避免整题固定资源类型 | GPU方案不静默回落CPU；资源租约正确，CPU候选不占GPU |
| 8 P1 | 定向延时与中间成果保全 | 待做；优先Tabular May、近完成RANZCR/Melanoma，试900对1800；完整成员原子保存 | 相同代码/资源配对；无额外训练授权不跑；超时产物必须完整验证；成本计入总预算 |
| 9 P1/实验 | 选优复核与停滞探索 | 离线诊断已完成；Cactus需新独立划分/重训；停滞任务试少量新draft | 辅助logloss不直接替代AUC；旧/新验证分数不混排；按实际方法而非分支名衡量多样性 |
| 10 P2 | 缓存确定性特征 | 仅诊断，待做；先看Melanoma固定ResNet特征约占81%时长 | 数据/划分/模型权重/预处理版本严格匹配；禁复用随机增强或微调中变化的特征 |

推荐执行次序：先1–4保证可靠恢复和成本边界；效果优化依次关注6（Melanoma一致性）、8（定向时限）、7（Jigsaw资源匹配）、9（Cactus独立复核）。5不默认全开；10后置。实现和启动是两个步骤，本轮整理不启动任何新实验。

## 已做离线诊断及限制

原审计覆盖5090 R1–R4剩余8题800候选。证据已复制进仓库`docs/evidence/20261017/`，无需寻找旧对话：

- `offline-audit-report.txt`、`offline-audit-results.json`、`offline-audit-cactus-ties.csv`及两份可复算脚本；脚本仍指向原始只读数据路径，不能保证在4090直接运行。
- Cactus 53个AUC=1候选中43种不同预测；logloss有差异不代表private会更好。同一满分集重采样不能解除饱和；独立划分需新训练，未执行。
- Melanoma患者交集0；验证5582行、103阳性、412患者；R4-R3 AUC+0.004421，按患者400次配对bootstrap95%区间[-0.003090,0.011497]。这是已搜索验证集上的诊断，不是独立泛化证明；没有全面排查跨患者近重复图像。
- R3/R4都因剩余时间不足将最终CatBoost/LightGBM配置从900/400降为350/220，不能单独用它解释R4退步，但一致性问题明确。
- 28个超时中6个留有验证CSV，仅Jigsaw n9还留有submission；Tabular May的8个MLP超时没有输出预测/模型。不能追溯恢复未保存状态。Jigsaw n9仅做CSV基础/public检查，未恢复成正式有效候选。
- Jigsaw BGE在CPU上800秒处理25728/312735文本，简单翻倍时限仍可能不够。
- 按历史频率、算法不随预算变化估算，六个有超时任务每轮25次从900放宽到1800，额外最多约105分钟累计作业时间；不是整轮墙钟增量，也不是4090未来上限。
- `5090-snapshot.json`、`4090-snapshot.json`包含整理时状态和逐轮正式评分，后续应重新读取运行目录确认时效。

## 本次整合验证

- 完整测试：77 passed，13 skipped（78.39秒）；跳过项涉及其他ML/原生环境要求，不代表已验证两台机器上的完整真实训练。
- 子模块补丁manifest核对通过，重复应用幂等；5090四个ops脚本与部署副本逐字一致。
- 4090新增槽位检查使旧跨机复用测试的伪造第二轮数据不再合法；只修复mock测试夹具，生成真实模拟第二轮，不放宽生产完整性检查。
- 测试日志：`docs/evidence/20261017/integration-tests.log`。没有启动真实模型请求或训练实验。

## 开发、测试与接手步骤

1. 在本仓库当前整合分支开发；`git status`核对本轮提交及子模块补丁。不要再在旧分散目录同时开发。
2. 新克隆先`git submodule update --init --recursive`，再`python3 scripts/apply-runtime-patches.py`（或bootstrap）。补丁有前后SHA校验，不要跳过。
3. 5090现有控制器环境可执行：`/opt/mle-rsi-fixes/controller-env/bin/python -m pytest -q`。包含mock单元/集成测试，不启动真实实验。
4. 先按1–4修复并补恢复测试；任何旧账本/实验状态修复须输出前后证据。不得直接把本开发仓库指向旧run强行resume，冻结source_hash会不匹配。
5. 新阶段新建独立run，明确每题起点、资源、预算增量、改动开关。保留已得牌14题的封存解；未得牌任务仍按public选择，不能回溯按private挑“最好checkpoint”伪装盲评。
6. 分支本地commit不代表GitHub已发布；远端认证此前失败，本次用户仅要求分支提交，未要求push。

可直接粘贴给新对话：

> 请先阅读 `/mnt/afs/yangsen1/projects/mle-rsi-experiments/HANDOFF.md`，在 `bug-fix` 分支工作。5090和4090最新实验路径及状态见文档。先核对已实现与待办，优先处理可靠性与恢复；不要自动启动实验、重置预算或把offline_max全开。具体实施范围按我的下一条指令。
