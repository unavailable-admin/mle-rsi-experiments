# 5090 第二阶段：完整轮次起点、独立新增预算

本阶段是探索性继续实验。已实现十项待办对应的工程支持；准备阶段未执行新的模型生成、训练、私榜评分，也不声称已经证明效果提升。后续运行状态以 HANDOFF 最新进展为准。Cactus 新划分重训、900/1800 同代码配对和 offline 强度对照仍是后续实际实验，不会由主实验启动命令额外自动启动。

## 已实现与验证边界

| 项目 | 实现 | 实际训练前仍需明确的限制 |
|---|---|---|
| 1 轮次收尾 | 五批 × 五个 durable attempt slots；验证实际节点、缺失位置、父节点、revealed ID 一致，不补造节点；部分失败后幂等续跑 | 全批失败仍停止，不把不确定请求重新发送 |
| 2 容器与账本 | top 采样错误保留日志；退出/kill 竞态正常收尾；中断时先留 container ID、终止状态和费用证据；只有验证成功才结算 | 旧缺失费用不推测；历史两笔 running 预留原样归档保留 |
| 3 预算 | budget_exhausted 独立显示；本阶段全局新增 $150、每题新增 $15、执行/GPU 各新增 180000 秒，基于历史实际支出及全部预留计算逐题绝对上限 | 旧 API 不确定请求继续全额预留；不清零，不把旧剩余额度叠加成更大的新增额度 |
| 4 策略与上下文 | cumulative online 使用完整可见节点而不重复发送以前每棵累计树，与 replay 一致；失败记录 input bytes/退出码/stderr；有持久记录的确定性公共分数回退；模型上下文先省略辅助参考代码并明确记录省略 | 精确父代码、诊断和任务不能容纳时，在发送前拒绝；旧接收失败的具体进程退出原因不能由旧日志追溯确认 |
| 5 offline 分档 | baseline/light/max 三档；默认 baseline（保留原来的每轮一次策略修订）；记录真实调参次数、策略版本和反事实报告数 | light/max 不默认开启；效果需要同起点、同资源、同预算对照 |
| 6 训练一致性 | runtime 训练配置记录接口；host 拒绝验证/最终模型配置不一致或缺失；提示要求先预算最终训练，完整同配置 ensemble members | 配置记录由候选代码产生；不是对任意 Python 的静态语义证明。真实训练应抽查记录与代码 |
| 7 候选资源 | 必须声明 MLE_RESOURCE='cpu'/'gpu'；资源池按候选请求分配独占 GPU；CPU 候选不占 GPU；无声明作为可修复候选错误 | 实际 GPU 可用性仍在启动前/运行时检查，不抢其他任务设备 |
| 8 时限与保全 | Tabular May/RANZCR/Melanoma 上限 1800 秒，其他 900；预算可缩短实际时限；完整输出包原子提交，超时后仅接受哈希验证且独立评分通过的完整包 | 不从未保存模型恢复训练；放宽时限的因果收益需同代码配对 |
| 9 选优与停滞 | 连续 50 个候选无公共分数增益时留一个 root draft；Cactus 单独新分层划分与公共选出的冻结源码复核计划 | 新划分是公开历史数据上的稳健性诊断，非全新测试集；AUC 仍为主，logloss 不替换 AUC；旧新分数不混排 |
| 10 确定性缓存 | 按任务隔离缓存；key 必须含数据/划分/权重/预处理/提取器 SHA256，且 frozen/deterministic 均为真；原子写入，不加载 pickle | 仅供候选显式采用；不自动缓存任意训练代码，不用于随机增强/微调特征 |

## 新实验起点

| 任务 | 最近完整且正式评分轮次 |
|---|---:|
| Dog Breed | R7 |
| Cactus | R11 |
| Melanoma | R11 |
| Jigsaw | R11 |
| RANZCR | R11 |
| Taxi | R15 |
| Tabular May | R12 |
| APTOS | R16 |

14 个已得牌任务继续封存。未完成的后续轮次不进入新搜索历史，但发生的全部费用和不确定预留保留。导入的是完整公共搜索历史及策略状态，不是只拿冠军 solver。按公共分数选择解，不按 private 分数倒挑起点。

新目录 `/mnt/afs/yangsen1/experiments/lite22-stage2-001/run`；部署代码 `/mnt/afs/yangsen1/experiments/lite22-stage2-001/code`。父实验 `/opt/mle-rsi-next/runs/lite22-medal-001` 不修改。新请求和执行目录使用 stage2 前缀，避免与归档的轮中请求 ID 冲突。`stage2-lineage.json` 保存起点、父记录哈希、新增授权、历史未结算记录和停机证据。

代码与结果均放在共享存储的独立实验目录：

```text
/mnt/afs/yangsen1/experiments/lite22-stage2-001/
├── code/      # 本次实验冻结代码
├── run/       # 导入历史、新结果、账本及日志
└── evidence/  # 准备与迁移证据
```

开发仍只在 `/mnt/afs/yangsen1/projects/mle-rsi-experiments` 的 `bug-fix` 分支进行。当前部署仍绑定 5090；Python 环境、模型与 GPU 配置沿用已有本机依赖，共享代码/结果不等于可直接换机启动。原 `/opt/mle-rsi-stage2` 部署在校验后退役。

## 检查、可视化及启动

5090 主机：

```bash
cd /mnt/afs/yangsen1/experiments/lite22-stage2-001/code
/opt/mle-rsi-fixes/controller-env/bin/python -m mle_rsi.stage2 check
```

只启动可视化（不启动控制器、不发生成请求）：

```bash
cd /mnt/afs/yangsen1/experiments/lite22-stage2-001/code
/opt/mle-rsi-fixes/controller-env/bin/python -m mle_rsi.stage2 view
```

明确启动新实验：

```bash
cd /mnt/afs/yangsen1/experiments/lite22-stage2-001/code
/opt/mle-rsi-fixes/controller-env/bin/python -m mle_rsi.stage2 start
```

启动器先检查封存源码、划分文件、机器、CPU、GPU 映射和镜像，再确认三个页面可用，最后启动后台控制器。断开 SSH 不终止实验。不要运行旧的 R4 handoff 启动器。已有活跃控制器时拒绝重复启动。

本地电脑 SSH 隧道（使用你已有的 5090 登录凭据）：

```bash
ssh -p 60000 -N -o ExitOnForwardFailure=yes \
  -L 19170:127.0.0.1:19170 \
  -L 19171:127.0.0.1:19171 \
  -L 19172:127.0.0.1:19172 \
  root@103.237.29.215
```

公网入口沿用既有交接配置；已核对本机内网地址 `10.119.216.143` 和 sshd 监听 22。若本地能直达内网，也可把连接改成 `root@10.119.216.143`，去掉 `-p 60000`。未从用户电脑验证公网连通性。

- 总览：<http://127.0.0.1:19170/>
- 搜索树：<http://127.0.0.1:19171/>
- 资源：<http://127.0.0.1:19172/>

日志：`/mnt/afs/yangsen1/experiments/lite22-stage2-001/run/controller.log`。暂停、继续由总览页面控制；未启动控制器时按钮禁用。

共享存储迁移只调整部署位置、启动默认路径和各 campaign 的 `output_dir`。原账本、正式轮次封存、验证数据和历史 lineage 保持原样；迁移前配置及逐文件内容校验证据保存在实验目录 `evidence/shared-storage-migration/`。历史证据中保留原始绝对路径，不做文本批量替换。

## 共享存储权限修复后的恢复

2026-10-17 首次启动 Dog R8 的 5 个候选因 AFS 权限失败，API 已结算 $0.1242282。已保留这些 attempts 和费用并停止控制器。修复后的 `stage2 start` 会继续原 run；不必新建实验或清空预算。启动检查调整 candidate-visible runtime/prepared 权限；新候选代码和任务输入显式设为可读，避免继承 0027 umask 后再次失败。真实容器读写检查通过（73 个输入文件，输出及缓存可写），测试 11 passed。证据见实验目录 `evidence/permission-fix/`。

Cactus 复核已于用户后续授权下单独执行：新划分打破两候选 AUC=1 的并列，但 AUC 差异置信区间包含0，不能证明稳定优胜。主实验仍保留旧划分及选优规则。详细数值与成本见 HANDOFF 和 `docs/evidence/20261017-stage2/cactus-recheck/analysis.json`。

## 独立逐题调度（2026-10-17）

用户确认各题起点不同，无需追齐轮次。控制器为每题独立推进下一轮：Dog R8、Cactus/Melanoma/Jigsaw/RANZCR R12、Tabular May R13、Taxi R16、APTOS R17。每题仍先完成自己的搜索、策略更新和评分，再进入下一轮；快题不等待慢题。共享 task/batch/API/CPU/GPU 限流及预算、获牌封存规则继续生效。单题阻塞不阻止其他题推进。`--through-round` 仍是各题自身的绝对轮次上限，不是新增轮数。

总览的逐轮表只按各题累计轮号聚合，不能解释为同一时间窗口的同步评测。原生树页面结合外层控制器状态显示排队/运行/封存，避免只根据短期 writer lock 把排队任务显示为停止。

Cactus 新划分复核提交 `1f64303` 已包含在开发分支，仅为证据和复核脚本；此次调度更新不改变主实验的划分、候选排名或预算。

### 用户随后确认：Cactus 从零重启

新目录为 `/mnt/afs/yangsen1/experiments/lite22-cactus-reset-001/{code,run,evidence}`。本次覆盖上一节“保持 Cactus 旧划分”的临时决定：用户进一步确认 Cactus 使用固定 3 折分层验证（seed=20261017），清空搜索树和学习策略，从 R1 开始。全部候选使用相同折，以宿主重算的三折平均 AUC 选优，记录各折 AUC、logloss 和 pooled OOF AUC（仅诊断）。每个样本恰有一次折外预测；各折训练配置一致，最终以相同配置在全部公开训练数据上重新拟合，四次训练共用原单候选时限。宿主检查折编号、覆盖及配置/成员记录；这些声明不能证明任意训练代码绝无泄漏。它不导入旧分数或候选，仍属利用既有经验设计的探索性实验。此前准备的单划分版本未启动，已归档后替换。

其他 7 个活跃任务复制停机时的完整 SQLite 状态，包括 Dog R8 的已完成批次，不退回上一完整轮；14 题保持获牌封存。旧账本逐字复制、所有费用与未决预留保留，绝对预算和整体截止时间均不重置；此次新增额度为 0。Cactus 的新 API/执行命名空间是 `cactus-reset1`，其余任务维持原编号以避免重复请求。

为了避免复制庞大的不可变历史，新目录引用旧目录中的内容寻址对象、已完成执行产物、封存报告和获牌 campaign；可写 SQLite、其他任务工作区及未来产物位于新目录。**旧实验目录必须保留，不能再次启动并行花费同一预算。** 新准备代码位于 `mle_rsi/restart_task.py`。

新实验沿用网页端口 19170/19171/19172；启动命令必须显式指定新 run：

```bash
cd /mnt/afs/yangsen1/experiments/lite22-cactus-reset-001/code
/opt/mle-rsi-fixes/controller-env/bin/python -m mle_rsi.stage2 start --run-dir /mnt/afs/yangsen1/experiments/lite22-cactus-reset-001/run
```

### 连接失败与策略回放自动恢复（2026-10-17）

本次 Cactus 首批 5 个请求均在 TLS 建连阶段出现 `ConnectError`，没有 provider request ID、响应或账单；旧实现将其统一视为 uncertain，并因只配置一次尝试而阻塞。修复后的 GPT transport 仅对 `ConnectError`、`ConnectTimeout`、`PoolTimeout` 在请求发送前的失败重试，单次最多 3 次，保留逐次连接证据；响应读取、写入失败、未知账单和取消不归入安全重试。连接尝试用尽时将明确未发送的请求标记为 `not_sent`，保留原费用预留及请求内容。原生 usage 记为零次实际生成费用；再次恢复必须沿用相同输入哈希。

控制器对 `ConnectionNotSent` 且无其他未决请求/执行的任务自动退避 30/60/120/240/300 秒，恢复同一轮、同一请求和已完成候选。最多 5 次任务层自动恢复，再失败进入 blocked。暂停或停止会结束退避等待；其他任务不受影响。不会自动重试结果不确定的 POST。

Dog R8 的根因是策略回放进程超时，host fallback 在已完成的第一个批次重新选出了与历史不同的父节点。现在发生策略进程故障时，回放只复用不可变的已提交 `ReplayStep`，校验父节点仍符合历史前缀，写入 `policy_replay_recovery` 审计；后续未执行批次才采用原有确定性兜底。健康进程重放结果不同、源代码/种子/历史/协议改变仍然由原一致性检查拦截。旧 fallback 证据不覆盖、不删除。

真实 Dog R8 的 4 个批次、195 个候选已做无 API/训练的回放核验。修复部署须先停止控制器并排空已接收批次，保留全部账本和历史，再封存新源码哈希、审计校正这 5 个已证实未发送的请求并恢复任务。证据保存在新实验 `evidence/auto-recovery/`。


## Offline 回放超时恢复补丁（2026-10-17，开发完成后待切换）

当前运行目录的冻结代码未更新，APTOS 的 blocked 状态也未修改。

- 策略响应超时独立为 `PolicyTimeout`，记录 startup/reset/decision 阶段、时限、退出码和 stderr；JSON 格式错误和策略异常继续按 `PolicyError` 处理。
- 回放只对本地超时重建 worker 并重跑该 episode，最多三次超时，间隔 1/2 秒。次数持久化，重启不能重置上限；不调用模型、不重训候选。成功 episode 保持缓存。
- 旧缓存仅对精确的空 `Policy timeout or malformed output: ` 错误识别为历史超时；pool/episode/policy 原记录先归档，再重新评估。包含真实错误的混合失败 bundle 不自动放行。
- 超时耗尽时仅可保留此前已验证的 incumbent。记录 `offline_fallback`、`offline_status=skipped_timeout`，新 pool 分数为 null。候选结果保留并继续正常轮次收尾；无有效旧策略、真实代码错误或完整性错误仍停止。
- 此补丁不承诺自动处理任意异常；不重试不确定的 API 请求，不改选优协议或历史费用。

切换步骤（本次尚未执行）：禁止新批次，等待已接收工作收尾及控制器退出；备份并更新冻结代码/来源清单；归档 APTOS 原 blocked 状态后解除该题阻塞并启动控制器。旧回放超时缓存由程序自动审计恢复，无需删除候选或修改得分。切换前应重新确认当时在途工作，不能把代码测试耗时当成部署等待时间。

验证：新增 7 项恢复测试全部通过；加上持久 worker、并行回放、campaign 续跑等，共 35 项不同的相关测试通过。额外 1000 版本存储压力测试因不属于本次必要范围主动中止，未计为通过；其后的 offline 中断恢复用例单独运行通过。版本化 runtime 补丁在临时目录从干净基线重建、逐文件哈希核对通过。
