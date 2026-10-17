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
