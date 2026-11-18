# Kaggle 调用与 LoRA 导出

## 公开 8B 体验

1. 登录 Kaggle，打开 [ABO Adaptive RAG Public 8B](https://www.kaggle.com/code/aaronyue77/abo-adaptive-rag-public-8b)，点 **Copy & Edit** 复制到自己的账号。
2. 在自己的 Notebook 的 **Session options** 启用 **Internet** 和可用的 NVIDIA GPU。公开 Notebook 已附加 `qwen-lm/qwen-3-vl/Transformers/8b-instruct/1`，如果自行导入 GitHub 的 `.ipynb` 或输入列表中缺少它，在 **Add Input → Models** 选择这个官方模型版本。 [Kaggle 官方 Qwen 模型](https://www.kaggle.com/models/qwen-lm/qwen-3-vl/transformers/8b-instruct/1)
3. 运行第一个代码单元格，等待「ABO 商品助手已就绪」。首次启动从 GitHub 和 ABO 官方来源下载所需资源并建立全量目录。在 Notebook 里可运行 `ask("我想查电风扇这类商品")`。 [Notebook 源文件](../scripts/kaggle_abo_public.ipynb)
4. 保持会话运行，在 **Run → Kaggle Jupyter Server** 复制自己的 **VSCode Compatible URL**。打开本机 ABO 网页，点 **连接 Kaggle** 并粘贴。若用 PowerShell 输入，则在项目目录运行 `.\scripts\run_abo.ps1 -Command cloud-chat`，把地址粘贴到本机提示。 [Kaggle Jupyter Server 说明](https://www.kaggle.com/docs/notebooks#kaggle-jupyter-server)

公开版本只使用固定 GitHub 代码版本、公开 ABO 数据、官方 Qwen 模型输入和公开 LoRA Release。LoRA ZIP 和适配器权重加载前会校验 SHA256。第二个代码单元用四道固定公开问题检查类目、找图、真实 8B 回复和 LoRA 读图。GPU 用量走当前访客自己的账号。 [LoRA Release](https://github.com/AaronYue77/ABO-Adaptive-RAG/releases/tag/qwen3-vl-8b-abo-lora-v1)

页面显示 `Kaggle GPU` 时，对话由 Notebook 执行，类目浏览、属性面板和图片预览由本机服务提供。网页各段历史、Notebook 的 `ask()` 和终端分别保存需求与商品选择，共用已经加载的8B。历史对话保持可续聊；短暂断线会有限恢复，真正停止或重启 GPU 会话后，运行新版初始化单元并复制新地址。客户端会在连接阶段识别旧版 Notebook 并提示更新，避免连接成功后首轮才报错。地址含凭据，只粘贴到本机连接框或终端提示。 [网页启动说明](../README.md#打开网页问答台) · [恢复机制](多任务对话与恢复.md)

## 私有回归版本

已有 [私有 Notebook v7](https://www.kaggle.com/code/aaronyue77/abo-adaptive-rag-product-assistant-8b) 使用私有夹具并完成 L4 保存运行。新增购物对话回归 34/34，涵盖类目与站点统计、搜索后调整条件、尺码与变体核对、规格和实时库存的混合请求。原跨类目回归 49/49，类目商品追问 576/576，字段与引用抽查 1,089/1,089。

## 云端微调与发布

正式适配器已在 Kaggle T4 上用 `transformers`、`peft`、`bitsandbytes` 完成 4-bit NF4 QLoRA。冻结训练 120 题，独立开发 50 题，共 60 次优化器更新，训练峰值 10.24 GiB。阶段代码使用相同的 Qwen3-VL-8B-Instruct 底座和可追溯的商品图文证据。

重新训练时，在 GPU Notebook 挂载固定的底座、训练和开发划分及其证据页，运行 [C 阶段的训练函数](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/c8_experiment.py)。[阶段协议](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/c8_protocol.py) 按商品家族隔离训练、开发和测试。历史 120/50 实验的逐题结果和正式适配器已保存。重新跑该历史协议需要备齐原冻结 60 题训练阶段的协议与证据页，日常推理 Notebook 挂载的是已验收适配器。

训练代码的 `save_pretrained()` 输出 `adapter_config.json` 和 `adapter_model.safetensors`。验证后可以发布这两个文件以及训练摘要和底座版本。正式适配器的权重为 853,168 字节，已可从 [GitHub Release](https://github.com/AaronYue77/ABO-Adaptive-RAG/releases/tag/qwen3-vl-8b-abo-lora-v1) 下载。该配置只训练了文本模型最后两层的 Q/V 投影，文件大小由目标层与 LoRA 秩决定。

对话理解与推荐说明的补充训练使用独立的 [QLoRA 实验入口](../app/abo/dialogue_training.py)。其候选权重、需求规划、缺证据说明和开发对照单独记录；开发结果没有变好的候选不启用。文本规划与说明适配器、原C图文适配器按任务切换，共用一个底座。微调结束后重新加载NF4底座进行推理对照，避免把训练期间的FP32参数与缓存状态带进低显存推理。

Kaggle 的实际 GPU 时间与可用型号以账号界面为准。P100 已退役，当前项目已在 Kaggle T4 完成训练、在 L4 完成独立推理验收。Colab 可运行短时交互训练，免费 GPU 型号和配额随可用资源变化。 [Kaggle Notebook 文档](https://www.kaggle.com/docs/notebooks) · [Kaggle P100 公告](https://www.kaggle.com/discussions/product-announcements/735239) · [Colab 资源限制](https://research.google.com/colaboratory/faq.html#resource-limits)
