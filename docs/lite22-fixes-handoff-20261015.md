# Lite22 搜索退步排查与修复交接

记录日期：2026-10-15。面向在 5090 上接手的 Codex。本文记录已确认的现象、建议修改及验收方法；尚未实施这些算法修复。

主要结论：当前搜索较浅与生成随机性可以解释部分差异，但已经确认父节点排序与旧实验不同、修复比例随批宽扩大而下降，以及不同代码产生完全相同预测。优先修复和验证这些可观测问题，不要把所有退步归因于随机性。

## 运行边界与位置

- 用户要求本机正在运行的实验保持原样。不要修改、暂停、重启或覆盖其冻结源码、配置、树和账本。
- 两台机器共享 `/mnt/afs`。在 5090 修改这个目录里的原项目文件，也会改变本机看到的文件；换机器并不自动隔离源码。
- 5090 上另外已有一场运行，安装 Codex 时看到控制器及账本正在更新。它不是本次文档中已经修复的新版本，也不要未经核对就覆盖它。
- 建议在 5090 的独立新目录或独立 worktree 开发，准备新的运行目录和冻结版本。不要通过覆盖旧配置来绕过来源校验。
- 本轮用户请求是写交接文档；本文中的训练方案是后续建议，不表示已经启动了新的付费实验。

| 用途 | 路径 |
|---|---|
| 当前开发仓库 | `/mnt/afs/yangsen1/projects/mle-rsi-experiments` |
| 本机正式运行冻结源码 | `/mnt/afs/yangsen1/projects/mle-rsi-lite22-rounds-ready` |
| 本机当前运行，下文记为 NEW | `/mnt/afs/yangsen1/runs/dream-rsi-lite22-rounds-001` |
| 上次 54.5% 运行，下文记为 OLD | `/mnt/afs/yangsen1/runs/dream-rsi-gpt-firstbatch-guarded-001` |
| 已存在的 5090 运行 | `/mnt/afs/yangsen1/runs/dream-rsi-lite22-5090-2-001` |
| 5090 已存在的部署源码 | `/mnt/afs/yangsen1/runs/dream-rsi-lite22-5090-2-001/deployment/code` |
| 5090 本地环境目录 | `/opt/mle-rsi-5090-2` |

5090 内网地址为 `10.119.216.143:22`，公网入口为 `103.237.29.215:60000`。本机使用 `/root/.ssh/mle-rsi-5090` 密钥经内网连接成功。文档不包含密码或登录令牌。

5090 已安装 `codex-ys` 0.162.1、herdr 0.9.3。个人 Codex 配置在 `/root/.codex-ys`，已迁移 19 个历史会话并验证真实模型请求成功。它是独立快照；本机个人目录仍为 `/mnt/afs/yangsen1/.codex`。`proxy_on`、`proxy_off`、`proxy_status` 已配置，herdr 新终端自动加载代理。

## 比较口径

OLD 实测 22/22 有效提交、12/22 private 得牌，即 54.5%。版本标记为 `guarded-lite22-12of22-20261014`，逐题结果位于 `docs/guarded-lite22-result.json`。

OLD 在四道开发题上每题做 4 轮 × 4 批 × 2 候选，选出并冻结 policy；最终 22 题重新搜索，每题 2 批 × 2 候选，不继承开发 solver，不进行 final offline。

NEW 是 22 道题各自累计搜索，每轮 5 批 × 5 候选及一次 offline。每题完成本轮后按 public 选解，复制并封存 solver 和既有 submission，再做 private 评分；没有另开 final 搜索。跨轮保留题内树。NEW 没有导入 OLD 的 solver，也没有加载 OLD 的 frozen policy。

22 题的 prepared 资产检查中，manifest、固定划分和 truth 文件均与 OLD 一致；两次的 `mle_rsi/validation.py`、`mle_rsi/candidate_runtime.py`、原生 `repair_guard.py` 和 `prompting.py` 的冻结源码哈希相同。discovery 模型和 reasoning 均为 `gpt-6-sol / medium`，单候选上限均为 900 秒、8 CPU。不要据此假定新旧资源执行性能完全相同。

以下统计是 2026-10-15 上午排查期间的历史快照，不是实时看板。不同统计采样时间不同，不能混成同一时刻的进度。public 分数不能直接换算成 private 得牌率，也不能用 private 奖牌阈值直接判定 public 是否得牌。

## 问题一 父节点排序被随机扰动逆转

优先级：最高。排序行为差异已复现；对最终成绩的净影响尚未通过实跑消融量化。

OLD 的 `frozen-policy.py` 对候选按以下二元组排序：

```python
(score, jitter)
```

即真实分数优先，jitter 只在同分时起作用。NEW 的初始 `best_first_policy_source` 使用：

```python
(score + jitter * max(abs(score), 1e-12), node_id)
```

默认 `replay_jitter = 0.05`，online 也使用这个参数。候选 quality 接近时，约 ±5% 的扰动足以逆转分数排序；它不是纯粹打破平局。

使用实跑保存的第一批前缀、online manifest 中的 seed 和 jitter，保持宽度 5，仅替换为 OLD 的排序规则重放第二批决策。在当时已完成第二批的 9 题中，4 题的非修复父节点改变；NEW 重放选择与实跑记录一致：

| 题目 | OLD 排序会选的父节点 | NEW 实际选中的父节点 |
|---|---|---|
| 猫狗，log loss 越低越好 | n4，0.035332 | n2，0.065990 |
| APTOS，分数越高越好 | n4，0.904074 | n3，0.865215 |
| Insults，AUC 越高越好 | n4，0.892660 | n3，0.890164 |
| NOMAD，loss 越低越好 | n5，0.063567 | n3，0.064307 |

猫狗第二批：一个槽位修复 n3，其余四个都基于较差的 n2。APTOS 第二批五个槽位都基于 n3。Insults 后续仍产生了更好解，因此不能把“父节点较差”直接等同于“子节点必然较差”。

相关代码和证据：

- `third_party/dream-rsi/my_rsi/src/dream_rsi/policy.py` 中 `best_first_policy_source` 的 `rank`。
- `mle_rsi/repair_policy.py` 中 `repair_best_first_source`，它包装上述默认策略。
- `third_party/dream-rsi/my_rsi/src/dream_rsi/config.py` 的 `replay_jitter`。
- `third_party/dream-rsi/my_rsi/src/dream_rsi/online.py`，传入 jitter 并保存 `online_manifest`。
- `OLD/frozen-policy.py`。
- `NEW/campaigns/dev-<task>/campaign.sqlite` 的 `tree`、`online_manifest` 记录。

建议恢复真实分数优先的排序，明确稳定的同分处理规则。保留有意探索能力时，应显式安排探索槽位，而不是让随机噪声隐式改写 exploit 排序。不应无区别地删除 replay 所需的随机情景。

验收：构造非同分、同分、接近零分、不同父节点集合排列的测试；高分不因 jitter 被低分反超，同分选择可复现。用保存的真实前缀复现上表。检查 online、replay 和断点恢复使用一致的语义。不能仅修改本地 policy 工厂却遗漏已持久化的初始 policy。

## 问题二 有效率下降与修复比例稀释

按候选数和阶段重新对齐后：

| 口径 | OLD | NEW |
|---|---:|---:|
| 每题前两个候选 | 37/44 有效，84.1% | 28/44 有效，63.6% |
| 全部首批 | 37/44 有效，84.1% | 77/110 有效，70.0% |
| 第二批，诊断快照内已完成部分 | 44/44 有效 | 36/45 有效 |

第二批 NEW 只有 9 题，不能把 80% 与 OLD 全部 22 题的 100% 当作严格配对结果。首批 NEW 的 33 个失败为：21 个 runtime_error、9 个 timeout、2 个 source_error、1 个 output_contract_error。

具体错误包括：过度转义的 `token_pattern` 导致空词表；源码重复关键字和非法语法；DataLoader 使用 ZIP 句柄时读取出错；试图写只读 `/data`；CPU/GPU 张量混用；特征维度不一致；验证 ID 和提交格式错误。ZIP 失败与多进程共享归档句柄一致，但尚未对该根因做隔离复现。没有发现这些错误由评分标准改变引起。

首批角色统计：improve 46/66 有效，explore 14/22，hypothesis 17/22。不能把所有失败归因于新增 explore/hypothesis 指令，improve 也有明显失败。

当前 repair policy 和宿主 `bounded_repair_decision` 都只保留一个失败候选修复槽，选择最近的符合条件的失败节点；已经作为父节点扩展过的失败和 debug 阶段失败受到限制。批宽由 2 增为 5 后，强制定向修复占比从最多 50% 降至 20%。这是旧规则与新批宽的交互，不是本次新写了一个坏的 repair guard。

建议：对“没有任何有效候选”的任务增加有界的定向修复分配，优先覆盖不同错误类别，并对修复链保留明确的深度、次数和预算上限。可以先试 2–3 个修复槽作为实验参数，不能宣称这是已经验证的最优配置。有有效解时继续保留改进和探索名额。

必须同时核对：

- `mle_rsi/repair_policy.py`。
- `third_party/dream-rsi/my_rsi/src/dream_rsi/repair_guard.py`。
- `BatchValidator.fill` 和 `OnlineScheduler` 的补齐规则。
- offline replay 的同一约束。

只修改生成的 policy、不修改宿主 guard，可能让额外修复父节点再次被过滤。所有修复必须计入原候选和 API 预算，不能重发状态不确定的付费请求。

验收：全失败、部分成功、修复再次失败、候选预算不足、暂停恢复等场景；确认槽位数、父节点选择、失败链界限和费用累计正确。

## 问题三 Birds 漏掉全零标签样本

诊断快照中 Birds 前 10 个候选全部失败。第一批错误包括 rec_id 映射、音频路径、特征长度以及变长 CSV 解析。第二批四个候选已经完成训练和输出，但被宿主验证拒绝。

这四个候选为 `tree-0001-r1-a0`、`r1-a1`、`r1-a2`、`r1-a4`。应有 52 个验证样本，实际都只有 35 个，缺失的 17 个完全相同，而且标签都是全零：

```text
13 130 145 165 178 192 200 205 231 232 238 294 296 299 32 76 9
```

例如 r1-a0 的 `read_labels` 中：

```python
if vals:
    labels[rid] = ...
```

这把“已知没有任何鸟类”的合法录音删除了。OLD 的解法正确区分 `?` 未知标签与空的已知标签集合。

证据位置：

- `NEW/campaigns/dev-mlsp-2013-birds/executions/tree-0001-r1-a0/agent.py`。
- 同目录 `independent-score.log` 和 `output/validation_predictions.csv`。
- `NEW/prepared/mlsp-2013-birds/truth.csv`、`split.csv`、`manifest.json`。
- `NEW/api/dev-mlsp-2013-birds/ab68bed324f2a53d8c3a9ba2/request.json`：第二批 repair 的实际请求，包含父代码和上一轮 ParserError；没有发现修复上下文漏传。
- OLD 最佳候选在 `campaigns/final-mlsp-2013-birds/executions/tree-0001-r1-a0/agent.py`。

建议改善数据契约说明和错误反馈：明确保留空标签录音，排除真正未知标签，固定预测列顺序；ID 校验错误应报告预期/实际数量、缺失/额外/重复 ID 的有限样例。目前只有笼统的 `Validation prediction IDs differ from the host-owned fixed split`。

可在 `candidate_runtime.write_validation` 增加适用于该类固定行划分的完整性预检，尽早暴露遗漏；保留宿主独立复核。不要放宽完整性校验来让错误候选通过，不要自动代填遗漏样本的预测。像像素和文本任务有特殊 ID 协议，不能盲目套用同一个行级校验。

自动反馈中可以使用 public 数据格式及固定 ID 信息，不能包含 private 标签。不要把人工读取 truth 后的这 17 个 ID 硬编码成补丁。

验收：构造同时含正标签、空标签、`?` 的小样例；全零行保留、未知行正确处理、验证覆盖完整；修复过程不直接修改既有候选证据。

## 问题四 Leaf 概率校准不足与预测重复

保存的 public 预测独立计算得到：

| 候选 | 准确率 | Log loss | 真实类别平均概率 |
|---|---:|---:|---:|
| OLD final 最佳 r1-a1 | 99.44% | 0.05097 | 95.85% |
| NEW 首批最佳 r0-a0 | 98.88% | 1.39135 | 25.37% |
| NEW 第二批最佳 r1-a2 | 98.88% | 1.16122 | 31.97% |

主要症状是分类多数正确，但概率过于分散。OLD 最佳有不同特征变换、双 RBF SVM、逻辑回归融合，以及在 inner split 上选择的概率幂次校准。NEW 首批最佳是较简单的固定权重 SVM/LR 融合。校准是很强的解释，但旧解同时改变了特征和模型，尚不能把全部收益归因于校准一个因素。

NEW 首批 r0-a1、a2、a3、a4 的 `validation_predictions.csv` SHA256 完全相同，loss 都为 2.446938387。不同代码通过了 AST 去重，却输出相同预测；首批五个候选只有两套不同验证预测。

建议分成两项：

1. 保留 AST 预执行去重，新增执行后的预测重复统计和后续搜索反馈。先按规范化 ID、列顺序后的精确预测建立指纹，区分 validation 与 submission；验证预测一致不代表所有测试预测一定一致。不要把有效重复候选判成代码错误，也不要宣称执行后的检查能节约已经消耗的训练预算。
2. 为概率任务增加 public 上的准确率、log loss、预测置信度等诊断，让候选识别“分类正确但概率不准”。校准参数只能用 outer training 内部划分选取，不能在 outer validation 上拟合温度再把同一 validation 分数当作无偏成绩。

相关代码：`mle_rsi/diversity.py` 中 `source_fingerprint`、`NoveltyGate`、`DiversityPrompts` 和 `audit`，以及 `mle_rsi/validation.py`、`backend.py` 和候选反馈构造处。

实际 evidence prompt 中 parent source、路径和 measured score 都存在；没有证据显示 Leaf 因丢失父代码而无法改进。祖先摘要截断仅在路径超过 32 步时触发，这批深度不触发。

验收：代码不同但预测相同、CSV 顺序不同但规范化预测相同、确实不同预测、无效输出、重启后的重复记录一致性；校准诊断正确区分准确率与 log loss，保持原始主要评分指标和验证划分不变。

## 推荐实施与实验顺序

1. 先冻结诊断快照和基线源码，记录代码哈希及实际配置。读取 `AGENTS.md` 并核对当前分支和未提交内容；不要重置现有工作区。
2. 优先修复父节点排序，使用真实前缀重放和小型确定性测试验证。这项不需要付费生成或训练。
3. 增加验证覆盖错误细节、概率诊断和预测重复可见性；尽量先只增加观察信息，避免同时改变选解逻辑。
4. 单独实现并验证全失败任务的有界修复分配，检查宿主 guard 和 replay 一致性。
5. 获得运行指示后，可在 5090 先选 Birds、Leaf、猫狗、APTOS 做两批小实验。若统一宽度为 5，即每题 10 个候选。分别验证数据适配、概率校准和父节点选择。
6. 比较修复效果时，最好在相同 5090 环境跑原配置对照与修复版，保持模型、验证划分、批宽、候选数、超时和资源限制一致。单次生成具有随机性；有余量再做重复运行。不要把换硬件后的提升直接归因于修复。
7. 小实验确认行为正确后，再考虑新版本完整 Lite22。比较修复时从头搜索；若继承历史或旧 solver，应另设运行并明确来源，不能当作独立复跑。

重点输出：有效率及错误类别、每批实际修复数和成功数、所选父节点相对最佳节点的分差、不同预测数量、按候选数及批次对齐的 public 曲线，以及完成后的 private 成绩。不要只汇总最终奖牌数。

private 结果继续只用于独立报告，不进入自动搜索提示。此前人已查看过 private，后续人工设计属于探索性研发；这些运行不能直接作为独立盲测的 SOTA 证明。

## 已排除与尚未确认的解释

- 已核对：public 验证资产和评分源码一致；主要生成模型设置一致；单候选时间与 CPU 配额一致；修复请求含父代码和既有错误；深度未触发长祖先截断。
- 已确认：排序噪声改变了真实父节点选择；扩大批宽稀释了定向修复比例；Birds 漏掉全零标签样本；Leaf 多份代码产生相同预测，且概率校准明显不足。
- 尚未确认：新增角色对整体有效率的因果影响、资源共享对超时率的影响、上述改动各自能带来多少 private 得牌收益。需要隔离验证或对照实验，不能凭代码差异直接定量归因。
- 先前聊天中的 65/35、后调整为 45/55，是主观原因判断，不是实验测出的统计概率，不应写入正式效果评估。

## 在 5090 接手

在 herdr 终端启动 `codex-ys`，可直接发送：

```text
请读取 /mnt/afs/yangsen1/projects/mle-rsi-experiments/docs/lite22-fixes-handoff-20261015.md。
先核对证据和当前运行情况，在独立目录准备修复与验证。
不要修改本机旧实验，也不要覆盖 5090 已存在的运行目录。
先处理父节点排序、Birds 数据契约诊断、修复槽位规则和预测重复诊断。
新训练的范围以我接下来给出的指示为准。
```
