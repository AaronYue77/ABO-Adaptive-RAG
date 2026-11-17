# Kaggle 调用与 LoRA 导出

## 日常问答

1. 打开 [ABO Adaptive RAG Product Assistant 8B](https://www.kaggle.com/code/aaronyue77/abo-adaptive-rag-product-assistant-8b)，点击 **Edit**，在设置中启用当前账号可用的 GPU，启动会话。
2. 运行第一个代码单元格。看到「商品助手已就绪」后，在 Notebook 运行 `ask("我想查电风扇这类商品")`。模型、正式 LoRA、商品资料与推理都在 Kaggle 会话中。
3. 需要从 PowerShell 输入时，保持会话运行，在 **Run → Kaggle Jupyter Server** 复制 **VSCode Compatible URL**，在项目目录运行 `.\scripts\run_abo.ps1 -Command cloud-chat`，然后粘贴到终端提示。该地址用于连接自己的会话。

Notebook v7 的 L4 保存运行完成，使用本项目的私有发布 Dataset 和官方 Qwen 模型挂载。新增购物对话回归 34/34，涵盖类目与站点统计、搜索后调整条件、尺码与变体核对、规格和实时库存的混合请求。原跨类目回归 49/49，类目商品追问 576/576，字段与引用抽查 1,089/1,089。

已启动的 PowerShell 连接使用对应 Kaggle 内核中的已加载版本。切换到 v7 时，在最新 Notebook 启动新 GPU 会话并执行首个代码单元格，然后用新会话的 VSCode Compatible URL 连接 PowerShell。

## 云端微调与发布

正式适配器已在 Kaggle T4 上用 `transformers`、`peft`、`bitsandbytes` 完成 4-bit NF4 QLoRA。冻结训练 120 题，独立开发 50 题，共 60 次优化器更新，训练峰值 10.24 GiB。阶段代码使用相同的 Qwen3-VL-8B-Instruct 底座和可追溯的商品图文证据。

重新训练时，在 GPU Notebook 挂载固定的底座、训练和开发划分及其证据页，运行 [C 阶段的训练函数](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/c8_experiment.py)。[阶段协议](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/c8_protocol.py) 按商品家族隔离训练、开发和测试。历史 120/50 实验的逐题结果和正式适配器已保存。重新跑该历史协议需要备齐原冻结 60 题训练阶段的协议与证据页，日常推理 Notebook 挂载的是已验收适配器。

训练代码的 `save_pretrained()` 输出 `adapter_config.json` 和 `adapter_model.safetensors`。验证后可以发布这两个文件以及训练摘要和底座版本。正式适配器的权重为 853,168 字节，已可从 [GitHub Release](https://github.com/AaronYue77/ABO-Adaptive-RAG/releases/tag/qwen3-vl-8b-abo-lora-v1) 下载。该配置只训练了文本模型最后两层的 Q/V 投影，文件大小由目标层与 LoRA 秩决定。

Kaggle 的实际 GPU 时间与可用型号以账号界面为准。P100 已退役，当前项目已在 Kaggle T4 完成训练、在 L4 完成独立推理验收。Colab 可运行短时交互训练，免费 GPU 型号和配额随可用资源变化。 [Kaggle Notebook 文档](https://www.kaggle.com/docs/notebooks) · [Kaggle P100 公告](https://www.kaggle.com/discussions/product-announcements/735239) · [Colab 资源限制](https://research.google.com/colaboratory/faq.html#resource-limits)
