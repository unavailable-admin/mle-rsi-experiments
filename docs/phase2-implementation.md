# 第二阶段实施与验收记录

目标：完成后置功能、D09/D10、必要历史工作流配置化和仓库整理。验证累计不超过 3600 秒；不包括 max 算法收益调优、多机控制器或跨机接管。

本文件随实现更新，尚未最终验收。新代码仍位于独立的 `mle-rsi-platform`，原开发工作区和运行实验源码保持不动。

## 功能与接口

| 范围 | 实现 |
|---|---|
| D09 修复、探索、fallback | `decision_rules.py` 把显式规则编入实际策略源码，经 registry 注册和哈希；online/replay/tuning 使用相同源码。支持修复次数上限、优先修复、best-first、停滞 draft、空决策/决策异常 fallback。进程超时仍为超时，不伪造有效评估。 |
| D10 去重 | `NoveltyGate` 边界与 `PythonASTGate` 实现；AST 保留常量，忽略注释/格式；持久记录归属和拒绝理由，按配置有界再生成。语法身份不等于语义身份。 |
| D11 导出/导入/分叉 | 导出完整轮次前缀，校验树与冻结 world、快照、所有文件；导入仅允许新 campaign，生成新 namespace，校验数据/指标/验证协议，历史策略评分不复用。`--through-round` 实现无损回退分叉。 |
| 历史费用 | 新账本和旧 `budget.json` 都可导出；继承包括被回退轮次、未决调用的费用/预留，不能通过分叉清账。新预算必须容纳历史费用。 |
| 独立 refresh | `Campaign.refresh(id)` 复用 offline 逻辑，持久化独立状态；不生成候选、不增加 online 轮数；未完成 refresh 阻止新 online，最后原子提交策略激活和回执。 |
| 特征缓存 | 候选只读共享缓存，在私有输出中提出新条目；宿主校验后原子发布。键包括数据/协议/镜像/runtime、变换源码及声明的参数与输入；仅缓存 numeric arrays，禁止 pickle，配置空间上限，碰撞保留诊断。 |
| 第二 provider | Gemini native `generateContent` 格式，复用请求/费用/未知状态/恢复账本。提取最终文本，过滤 thought parts。费用以兼容网关实际 USD 账单为准，不按 token 估算冒充已结算费用。 |
| MLE Web | 独立本机认证服务，任务成绩、候选树、源码、公开诊断、产物下载、资源、预算、追加额度、pause/stop/run、refresh、导出/导入及后台日志。数据通过只读核心接口获取。 |
| 队列与 guarded 阶段 | `task_parallelism` 控制题目队列；workflow 配置声明阶段依赖、候选/费用/奖牌门槛及公开历史继承。复用同一 Controller，失败门槛不启动下一阶段，重复执行复用完成状态。 |
| 仓库整理 | 旧运维与示例迁到 `legacy/`；历史根文档和实验资料迁到 `docs/archive/pre-platform/`。 |

Gemini 线协议依据 [Google 官方 generateContent 参考](https://ai.google.dev/api/generate-content)。当前实现针对带独立 billing API 的兼容网关，延续原实验的账单模式；没有账单证据时保留未知预留。

## 配置与命令

可选规则显式写在实验 `search` 中，例如：

```json
{"search":{"decision_rules":{"repair_limit":2,"prefer_repair":true,"best_first":false,"stagnation_draft":3,"fallback":true},"novelty_gate":"python-ast","novelty_retries":2},"feature_cache":{"enabled":true,"max_entry_bytes":134217728,"max_total_bytes":1073741824},"task_parallelism":1}
```

规则默认关闭。启用、改变规则或缓存协议应创建新 run，通过验证后的历史导入继承；不修改已冻结 run 的配置。

```bash
mle-rsi refresh --run-dir RUN --id refresh-001 --task TASK
mle-rsi export --run-dir SOURCE --task TASK --output NEW_BUNDLE --through-round 2
mle-rsi init --config NEW_CONFIG --run-dir NEW_RUN
mle-rsi import --run-dir NEW_RUN --task TASK --bundle NEW_BUNDLE
mle-rsi run --run-dir NEW_RUN
mle-rsi serve --run-dir NEW_RUN --bind 127.0.0.1 --port 8080
mle-rsi workflow --config WORKFLOW_JSON --run-dir NEW_WORKFLOW
```

旧格式运行使用 `export --campaign dev-TASK --public-dir /path/to/public`，不执行旧脚本。支持旧 Dream-RSI storage format 2；不承诺导入缺失快照、被手动破坏或无法核实数据协议的历史。源 run 可继续运行：导出的是当时已完成轮次与费用快照，之后的源 run 新支出属于其后续分支，不会自动同步到新 run。

导出/导入不带入 private 官方报告到 agent。保留的历史 submission 经哈希核验复制到新运行目录；新 Controller 可独立正式评分。历史策略源码会按新配置重新验证/评估，不把旧 runtime 的策略分数当作可比较结果。

Web 默认仅监听 localhost，启动时输出独立访问令牌。浏览器页面只在内存保存令牌，API 必须携带令牌；不在 URL 中传递。远程浏览可通过已有 SSH 转发，不需要开启多机控制。

缓存适用于不依赖标签的确定性预处理。调用方必须声明输入、权重、随机种子等所有依赖；哈希及训练记录无法证明任意用户代码内部没有隐含全局状态或数据泄漏。

## 验证

计时记录位于本机 `runs/phase2-validation-budget.json`，每项运行记录耗时和退出状态。最终验收摘要将在完成后固定到 `docs/verification/`。中间失败与修复后的通过结果都会保留；不以中断或未运行的用例充当通过。

## 第二阶段追踪对应

| 原编号 | 本阶段覆盖 |
|---|---|
| C02 / D02 / D04 / M08–M09 | Gemini 适配与原有请求、账本恢复共用实现 |
| C04、C23 / D11 / M09、M11 | 单机历史导出、导入、继承、前缀回退分叉；跨机控制不在范围内 |
| C08 / D09 | 有界修复、探索、best-first、fallback；默认关闭 |
| C09 / D10 / M02 | AST 去重和有界再生成；任务 instructions 表达实验角色约束 |
| C11 / D12 / M01、M12 | 独立 refresh；保留既有 baseline/light/max，不做收益调优 |
| C21 / M01、M05、M07 | 确定性数值特征缓存 |
| C24 / D05、D08 / M12 | 认证 Web、树/源码/产物、资源/账本与控制操作 |
| M01、M03 / 历史运维 | task_parallelism 队列、guarded workflow 和历史目录归档 |

配置样例：`configs/experiments/optional-features.json`、`gemini-example.json` 和 `configs/workflows/guarded-mock.json`。真实样例中的模型、服务、价格和宿主路径均需按实际环境配置。

## 已执行验证（最终汇总前）

- 定向回归：50 passed / 2 opt-in skipped，包含两个三折原生用例；XML 保存在 `/tmp/mle-platform-phase2-regression.xml`。
- Chromium 实际页面：登录、候选源码、资源、追加额度、停止，1 passed，无页面脚本异常。宿主缺失浏览器依赖，已仅解包到 `/tmp/mle-browser-libs`，未修改系统安装。
- 真实兼容 Chat API：成功响应并以独立账单结算 $0.00034117。
- Leaf：真实模型生成一个类先验基线，容器执行和公开验证有效，官方 grader 接受提交，生成费用 $0.00568388。该基线只验证链路，不代表模型效果。
- GPU 1：持有原实验租约与平台资源租约，执行 16 元素 CUDA 运算；容器公开评分、测量结算和已完成结果复用均通过。
- Gemini：所选配置未提供 credential_file，且相应环境变量不可用；未发出真实 Gemini 请求。native 协议与实际费用恢复逻辑已有模拟传输测试，真实网关兼容性仍是验证限制。
