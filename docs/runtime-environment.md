# 离线模型与候选环境

所有新 real run 在初始化时，使用候选 Python、Sysbox 镜像、实际只读模型挂载和配置的 GPU 执行禁网探测。失败不生成 ready.json，也不开始付费模型调用。mock 不执行环境探测。

## 准备与使用

```bash
.venv/bin/mle-rsi prepare-host --host SOURCE_HOST.json --output-dir /absolute/path/to/new-host-bundle
.venv/bin/mle-rsi init --config EXPERIMENT.json --host /absolute/path/to/new-host-bundle/host.json --run-dir NEW_RUN
```

SOURCE_HOST 必须包含实际候选环境、镜像、CPU/GPU 和必要挂载。`torch_cache` 指向含 `hub/checkpoints` 的目录；`pretrained_models` 使用 `组织/模型/config.json` 布局。缺少权重时不自动联网下载。

prepare-host 将模型复制到新目录，解引用缓存软链接并设置副本目录 755、文件 644，保留原缓存权限。对配置的 GPU，通过 nvidia-smi XML 获取真实设备 minor，绑定本机实际存在的 NVIDIA 驱动库，生成独立 Sysbox 配置，不覆盖旧实验配置。探测使用与候选相同的 CPU/GPU 租约，并拒绝占用繁忙 GPU。

初始化再次探测，不信任手工填写的 runtime_inventory。结果冻结到 runtime/inventory.json，真实 Python/包版本、缺失依赖、模型路径、测试过的加载选项和任务类型进入 discovery 提示词；大型资产哈希表不进入提示词。模型权重、tokenizer、预处理文件和 GPU 配置哈希进入 release，恢复时校验。

模型加载 helper：

- `model, transform = rt.load_resnet50()`：只加载清单中的本地 ResNet50。
- `model, processor = rt.load_pretrained(path)`：path 必须是 verified 模型的精确容器路径；SigLIP 返回 image processor，文本模型返回 tokenizer。强制 local_files_only，ModernBERT 使用已验证的 reference_compile=False。
- GPU 方案需声明 `MLE_RESOURCE = 'gpu'`。任务允许 GPU 本身不会分配 GPU。

## 本机验证（2026-10-19）

本机模板：[5090-verified.json](../configs/hosts/5090-verified.json)。它引用当前机器上独立的权重副本和 GPU 配置，不是跨机器通用配置。通用输入模板见 [example.json](../configs/hosts/example.json)。实验本身未启动。

GPU 1 / RTX 5090、PyTorch 2.7.1+cu128、torchvision 0.22.1+cu128、transformers 4.51.3 下，禁网完成五组模型加载与有限数值推理：ResNet50、BGE-base-en-v1.5、ModernBERT-large、SigLIP2-so400m-patch16-256、DeBERTa-v3-large。随后使用实际候选 runtime helper 再次验证五组模型。

额外功能验证：sklearn/LightGBM/XGBoost/CatBoost 拟合与预测；OpenCV + HOG + 滤波；SoundFile WAV 编解码 + librosa mel 频谱；英文/俄文 Unicode 字符特征。

发现并处理的部署问题：原 GPU 配置引用另一版本驱动（580.95.05，本机为570.148.08），GPU 1 配置错误映射 GPU 3；权重原始权限不允许映射用户读取；环境清单为空且没有实际进入提示词；ModernBERT 默认编译路径失败。现有 OpenCV 是 opencv-python-headless，按模块 cv2 探测可正确识别。

accelerate 未安装，清单标记 unavailable；上述已验证加载不使用 device_map='auto'。本机没有专用音频或俄语文本归一化权重；这些任务只验证基础特征/数据处理环境，不声称英文编码器能替代任务专用模型。小输入推理和环境检查不等于22题训练通过，更不代表得牌。完整检查见 [验证记录](verification/runtime-environment-20261019.json)。

## 22 题覆盖

| 任务 | 环境与可用预训练范围 |
|---|---|
| aptos2019-blindness-detection | 图像处理 + GPU；ResNet50 / SigLIP |
| dog-breed-identification | 图像处理 + GPU；ResNet50 / SigLIP |
| dogs-vs-cats-redux-kernels-edition | 图像处理 + GPU；ResNet50 / SigLIP |
| plant-pathology-2020-fgvc7 | 图像处理 + GPU；ResNet50 / SigLIP |
| siim-isic-melanoma-classification | 图像处理 + GPU；ResNet50 / SigLIP |
| histopathologic-cancer-detection | 图像处理 + GPU；ResNet50 / SigLIP |
| ranzcr-clip-catheter-line-classification | 图像处理 + GPU；ResNet50 / SigLIP |
| the-icml-2013-whale-challenge-right-whale-redux | 图像处理 + GPU；ResNet50 / SigLIP |
| aerial-cactus-identification | 图像处理；视觉预训练可用 |
| leaf-classification | 表格描述符 / HOG / 图像处理；视觉预训练可选 |
| jigsaw-toxic-comment-classification-challenge | 文本特征；BGE / ModernBERT / DeBERTa |
| detecting-insults-in-social-commentary | 文本特征；BGE / ModernBERT / DeBERTa |
| random-acts-of-pizza | 文本特征；BGE / ModernBERT / DeBERTa |
| spooky-author-identification | 文本特征；BGE / ModernBERT / DeBERTa |
| text-normalization-challenge-english-language | 英文文本处理；未提供专用序列转换权重 |
| text-normalization-challenge-russian-language | 俄文 Unicode 文本处理；未提供专用俄语权重 |
| mlsp-2013-birds | 音频解码 / 频谱；未提供音频预训练权重 |
| denoising-dirty-documents | 图像滤波 / 特征与常规学习；无需强制预训练 |
| new-york-city-taxi-fare-prediction | 表格学习库 |
| nomad2018-predict-transparent-conductors | 表格学习库 |
| tabular-playground-series-dec-2021 | 表格学习库 |
| tabular-playground-series-may-2022 | 表格学习库 |
