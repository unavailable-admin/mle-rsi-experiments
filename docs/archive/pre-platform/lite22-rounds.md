# Lite22 逐轮研发实验

总目标是在 MLE-bench 上超过 HMA、达到 SOTA，短期先达到 Lite22 SOTA。本入口用于探索研发；按轮查看 private 后进行人工调整的成绩，不是独立盲测证明。

## 资源与协议

- 专用半机池：物理核 CPU0–31（同时锁住 sibling CPU64–95），GPU0–3；四个八核训练槽，每容器 32 GiB，训练内存合计最多 128 GiB。控制、评分和策略进程限制在同一 CPU 半区；没有整场 240 GiB cgroup 总内存硬限制。
- 22 题各自保留累计树，每轮 5 批 × 5 候选、一次策略修订。最多四批同时进入生成/执行流水线；全局 API 并发 5，训练/评分共享四槽。CPU-only 候选可越过等待 GPU 的候选，不占用 GPU。GPU 题名单继承 guarded 配置，尚未自动按模型需求调整。
- 所有健康题完成本轮后才进入下一轮；失败题保留在 22 题分母内，独立阻塞，不拖停其他题。批次按原顺序入树，不以完成速度改变父节点选择。第一批 5 个候选后即可在网页查看健康指标。
- 每轮按固定 public validation 选出 solver/submission，复制并封存哈希，再 private 评分。每轮得牌率使用固定 22 题分母，并同时显示评测覆盖率。private 结果存在报告目录，不传入自动搜索的历史或提示词。
- 连续推进，不以 200 候选或 8 轮自动结束。内部框架兼容上限设为 1,000,000 轮，实际由预算与人工控制停止。默认全局 API 上限 $150、每题 $15，含未结算预留；每题累计执行/GPU 各 50h；单候选 900 秒。首次启动后 7 个日历日为新工作准入期限，包含暂停时间；已接收批次及已开始 offline/评分允许收尾。这些上限不保证跑满某个候选数。
- 停滞显示为“距离上次 public 改善的候选数”，仅是诊断指标，不据此自动删除题目或改变候选预算。

## 准备与启动

使用冻结副本启动，主仓库后续开发不会改变实跑代码。准备脚本不调用付费生成；运行 `start-lite-rounds.sh` 才开始连续搜索。

```bash
cd /mnt/afs/yangsen1/projects/mle-rsi-lite22-rounds-ready
bash scripts/start-lite-rounds.sh /mnt/afs/yangsen1/runs/dream-rsi-lite22-rounds-001
```

- 总览、学习曲线、暂停/继续：`http://127.0.0.1:18770`
- 原生搜索树、候选代码、错误与策略：`http://127.0.0.1:18771`

远程浏览：

```bash
ssh -N -L 18770:127.0.0.1:18770 -L 18771:127.0.0.1:18771 <服务器SSH别名>
```

总览页显示所有题目、逐轮得牌率及覆盖率、有效/超时/重复候选、public/private 曲线、已结算和预留费用、资源槽与排队状态。页面每 15 秒刷新，原生树页面读取相同 campaign。控制接口只监听 loopback，要求页面随机令牌和同源请求。

## 暂停、继续与恢复

网页暂停不强杀候选或 API。它停止接收新批次，最多收尾四个已接收五候选批次，以及已开始的 offline/评分；极端情况下训练收尾可约 75 分钟，加上 API/评分时间。暂停期间控制器和页面仍在，半机 CPU 租约仍保留。恢复继续相同冻结配置、树和累计账本。

```bash
./lite-rounds pause --run-dir /mnt/afs/yangsen1/runs/dream-rsi-lite22-rounds-001
./lite-rounds resume --run-dir /mnt/afs/yangsen1/runs/dream-rsi-lite22-rounds-001
./lite-rounds status --run-dir /mnt/afs/yangsen1/runs/dream-rsi-lite22-rounds-001
```

要让控制器收尾后退出并释放 CPU 租约：

```bash
./lite-rounds stop --run-dir /mnt/afs/yangsen1/runs/dream-rsi-lite22-rounds-001
```

控制器退出后，网页继续按钮不可用；再次执行启动脚本恢复。不要用 `kill -9`。有未结算请求或运行状态不明的候选时，相应题会阻塞，不能靠重启自动重发。查看 `task-status/<task>.json/.log` 和账本，完成核实后可在控制器停止时显式 `retry-task --task TASK`；该命令不会删除费用或重置不确定状态，执行现场不完整仍会被底层拒绝。

仅跑到指定轮次可直接使用 `./lite-rounds run --run-dir ROOT --through-round 1`，但不能与已有控制器同时运行。

## 轮间修改

网页暂停/继续只控制调度，不修改模型、预算、GPU 名单、验证协议或搜索逻辑。修改实验代码/配置时须另行准备新版本和明确的 public-history 迁移，保存原账本及来源；当前入口不提供热改源码或自动迁移。不得在旧运行中覆盖冻结配置绕过检查。private-aware 人工干预应记录，验证协议改变时旧分数不能直接合并。

## 验证

调度、GPU 等待不占 CPU、暂停后精确续跑、检查点完整性、mock private 报告隔离和新旧入口回归见 `tests/test_rounds.py` 与其余测试。mock 完成不代表真实 API 生成或真实得牌。正式运行目录初始化后仍无 paid candidates。实际 readiness 记录在运行目录和独立 readiness 目录，区分模拟 API、真实容器及网页检查。

最终准备验证：55 项测试通过，另 10 项 ML 环境测试通过；22×25 完整模拟首轮、共享文件系统暂停、四卡真实容器与网页控制检查均通过。详细证据见 [验证记录](verification.md)。
