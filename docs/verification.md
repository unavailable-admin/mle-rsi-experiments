# 本机验证记录（2026-10-13）

- 基线 Dream-RSI `3ccf88a209604795f75ec2e0f2f40352dc80b620`，适配提交 `f011f78`。
- 独立 Python 3.12、bwrap、Docker Sysbox、固定镜像、22题公开说明与 CPU affinity 预检通过。
- `/mnt/afs/yangsen1/runs/dream-rsi-mle-verification-001`：smoke 模式，真实容器执行预写 leaf sklearn 解法，模拟 discovery/developer 模型；两轮 online/offline 完成，冻结成功，最终单题调度完成。候选公共 log loss `0.11754961379818821`，转换 quality `0.8948148589138023`。未调用真实 Gemini，未计算官方奖牌结果。
- 自有6个测试和17个原生 Web reader 测试通过。前端检查最初被宿主旧 Node 阻断，切换已安装 Node 22 后通过，JS语法检查通过。
- 原生 campaign 回归测试执行到中途主动中止，已通过其中5项；没有宣称完整原生测试集通过。原生 LASSO 耗时执行不属于本次 MLE 冒烟测试。
- 本地 HTTP `/`、`/app.js`、`/api/campaigns`、campaign 详情和累计 round 接口均返回200；累计开发树包含根节点和两个训练节点。尚未做浏览器截图/交互布局检查。
- Gemini transport 使用模拟 HTTP 验证账单结算、重复请求缓存、请求内容漂移拒绝，以及网络结果不确定时禁止自动重试。
- 尚未验证真实 Gemini 网络调用、GPU候选训练与全部22题；正式四题及最终评估未启动，交由用户启动。

冒烟 run 初始化时 submodule HEAD 尚为基线；frozen-config.json 已逐文件记录同一套适配源码哈希，后续提交没有改变源码内容。


## Lite22 连续逐轮准备（2026-10-15）

- 最终代码：`python -m pytest -q`，55 passed、10 skipped；这 10 项在 ML 环境运行 unittest 后全部通过。
- 最终版本完整 mock 首轮：22 题各 25 个候选，共 550 个名额，全部完成，无 blocked 任务。mock 不调用付费 API、不计官方得牌。
- 共享文件系统暂停复测：22 个任务控制器，40 个名额保存于完整批次边界，全部执行槽释放，无 blocked 任务。早期测试发现同时关闭策略进程时 5s 清理宽限不足；最终 controller 局部采用 30s，退出恢复默认值。
- 真实执行冒烟：四个预写 leaf 候选经共享池及继承 GPU 租约，同时占用 GPU0–3、四组八核；同时运行交集 35.79s，全部有效，宿主独立重算分数，账本无付费 API。该检查不是 22 题实际生成或训练性能验证。
- 22 题真实 public validation 资产准备完成；bwrap、Docker、镜像及模型服务只读认证检查通过。没有真实生成请求。
- Chromium 实际加载总览、HTTP 页面和 JSON 数据；令牌控制、跨源拒绝、原生树页面及 app.js 检查通过。服务器无 CJK 字体，headless 截图中文呈方框；页面 UTF-8 文本及客户端渲染正常，用户浏览器使用本机字体。
- 冻结副本：`/mnt/afs/yangsen1/projects/mle-rsi-lite22-rounds-ready`。正式目录：`/mnt/afs/yangsen1/runs/dream-rsi-lite22-rounds-001`。后者仍无搜索候选和付费账本；仅启动 18770/18771 可视化服务。
- 来源为 main `f04f6ed` 加本次未提交实现及 Dream-RSI 六行批次边界 hook；完整运行源码由 `frozen-config.json` 的 106 个文件哈希固定，启动脚本另记哈希。不能用 base commit 单独代替该冻结副本。
- 证据：正式目录的 `readiness.json`、`web-readiness.json`、`mock-readiness-report.json`、`version-provenance.json`；真实容器目录 `lite22-rounds-readiness-001`，暂停目录 `lite22-rounds-pause-003`。早期未启动准备目录及失败诊断保留，没有重置实际费用。
