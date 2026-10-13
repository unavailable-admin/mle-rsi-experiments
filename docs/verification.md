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
