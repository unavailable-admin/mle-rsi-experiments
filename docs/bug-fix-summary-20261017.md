# bug-fix 分支改动汇总（2026-10-17）

范围：相对 origin/main 的分支累计改动，包含本次提交前的工作区修改。实验状态以 HANDOFF.md 顶部和运行目录实时记录为准；提交源码不会切换正在运行的冻结部署。

## Context overflow 如何缓解

- cumulative 策略输入保留完整可见节点，但不再重复携带此前每棵累计树；online 与 replay 使用一致的空 base_context。Cactus R11 的实测输入由 25,578,723 字节降到 340,980 字节，可见节点与可选父节点仍为 251。
- 候选生成使用 evidence 上下文：父节点源码、路径变化、机制统计、近期失败及少量参考源码；不把所有历史源码逐条追加到请求中。
- discovery 配额为 150,000 tokens，developer 为 200,000；发送前按角色配额和模型输入/输出窗口检查，并预留 12,000 tokens 给宿主指令及一次有界提取重试。
- discovery 超过安全目标时，从辅助 reference_sources 尾部逐个移除并重新计数；保留父节点完整源码、诊断、任务和 public best 标识。context_selection 记录被省略节点及源码 SHA256。磁盘中的原始节点和不可变证据不删除。
- 必需内容自身仍超限时，在发请求前抛出 ContextOverflow。当前没有保证无限历史永不溢出，也不是按时间删除最旧搜索节点；developer 超限也没有上述参考源码裁剪。

代码：dream-rsi 的 online.py、prompting.py、evidence_context.py、llm.py，以及 mle_rsi/experiment.py。依赖修改由 patches/runtime/ 中的补丁和 manifest 固定。

## 分支累计功能与修复

| 范围 | 改动 |
|---|---|
| 多题实验与展示 | Lite22 混合 CPU/GPU 调度、逐轮封存和 private 评分、获牌退出、固定总题数报告、资源监控和曲线；树页面区分排队与真正停止，缓存已解码节点。 |
| 独立轮次 | 每题按自己的进度完成搜索、offline 更新及评分，再推进下一轮；不追齐跨题轮号，仍服从共享资源与费用上限。 |
| 实验续跑 | 4090 offline-max 续跑、5090 第二阶段导入、完整边界检查、不可变历史来源与哈希校验、请求命名空间隔离；费用及未知预留完整继承。 |
| Cactus 重启 | 单题从空树和初始策略开始，其他题继承进度；固定三折分层、平均折 AUC 选优、折外覆盖校验、最终全量重训配置记录；新旧分数不混排。 |
| online 故障恢复 | durable attempt slots、失败后的幂等续跑、已提交历史批次原父节点回放；策略进程故障记录输入量/退出码/stderr，并提供可审计公共分数回退。 |
| API 连接恢复 | 只自动重试证实未发送的连接建立失败；持久记录次数、退避和状态，不重复清账。不确定的 HTTP 504 尚无通用自动重试。 |
| offline 故障恢复 | 单独识别 PolicyTimeout，最多三次持久化回放尝试并重建 worker；审计恢复旧超时缓存；耗尽时保留此前有效策略，标记 skipped_timeout，新池分数为空。真正策略格式或完整性错误不冒充超时。 |
| 训练与资源 | 候选声明 CPU/GPU，验证/最终训练配置记录与检查；May/RANZCR/Melanoma 定向时限 1800 秒，其余 900 秒；完整输出原子保全与确定性冻结特征缓存。声明检查不等于证明任意代码的训练语义。 |
| 搜索停滞 | 持续无公共增益时保留 root draft 探索；offline baseline/light/max 分档并记录实际更新量；预测指纹、候选差异及选优复核证据。 |
| 容器与部署 | 容器退出/kill 竞态、采样失败日志及中断证据；AFS 与 user namespace 输入读权限修复；共享存储迁移、启动器及来源校验、可复现依赖补丁。 |
| 证据与交接 | 5090/4090 快照、Cactus 新划分配对复核、三折与独立调度部署、自动恢复核验、测试及 HANDOFF。 |

## 已知边界

- 最近强制回退是用户授权的一次运维恢复：回到最近 seal 与 official-result 完整边界，原预算与所有费用预留不变。脚本和数据库备份保留在实验目录 evidence/forced-boundary-restart/，不是通用自动回退功能。
- HTTP 504 等结果未知请求不能宣称已自动处理；历史请求归档也不代表已经知道真实计费。
- 修复后的独立调度仍受共享资源限制；已入队不等于所有题同时训练。
- 代码补丁、单元测试通过和真实实验的得分收益是不同证据；不能用工程修复直接证明模型更好。

## 依赖改动的版本管理

third_party/dream-rsi 和 third_party/rsi-eval 保持固定上游 commit。所有 tracked 文件的本地差异完整保存到 patches/runtime/*.patch，manifest 记录修改前后 SHA256；scripts/apply-runtime-patches.py 在 bootstrap 时校验并应用。因此子模块工作区显示 modified 是该机制的预期状态，不代表遗漏提交依赖改动。

## 本次提交验证

- controller-env：test_rounds、test_stage2、test_gpt_halfday、test_restart_task、test_offline_timeout_recovery、test_portable_setup，共 50 passed（67.92 秒）。覆盖上下文省略、online/replay 输入一致性、调度、恢复与预算继承。
- ML env：unittest 执行 test_kfold_native，1 passed；该环境有 pandas，未安装 pytest。首次合并收集因 controller-env 缺 pandas 未运行，随后按已有环境拆分完成，未新增安装依赖。
- update-runtime-patches 与 apply-runtime-patches 验证两个依赖工作区与 manifest 一致；git diff --check 通过。
- 测试不启动真实候选训练或付费模型请求。
