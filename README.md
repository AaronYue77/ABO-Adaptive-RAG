# ABO Adaptive RAG 商品助手

这套项目直接从 [Adaptive-rag](https://github.com/JEen-wang/Adaptive-rag/tree/2a12a62382a8c3ec033fa9a5b19b9b23b8780ab8) 的源码开发，使用 FastAPI 和 LangGraph 运行 ABO 商品助手。全量库有 147,702 条商品记录、576 个原始类目和 32 个站点。商品以 ASIN@站点定位。

- 查商品、规格和原始资料时给出来源。会话可以继续看当前商品，也可以显式切换到目录。
- 比较重量、尺寸和类别属性时分别核对来源。缺价格、库存或执行能力时说明边界。
- Kaggle 用 Qwen3-VL-8B-Instruct 做短寒暄、模糊意图分类和图文读取。接入原有 C 阶段 120/50 LoRA 和 D 配件兼容四角色流程。

[架构与证据流程](docs/ABO架构与验证.md) · [上游原始说明](README.upstream.md)

## Kaggle

打开独立的 [ABO Adaptive RAG Product Assistant 8B](https://www.kaggle.com/code/aaronyue77/abo-adaptive-rag-product-assistant-8b)，启动 GPU 会话并运行第一个代码单元格，看到就绪提示后调用 `ask("茶杯")`。私有 Notebook 的离线依赖、官方 ABO 数据快照与固定模型通过挂载提供。

要从 PowerShell 连接这个 Notebook，在初始化后打开 **Run → Kaggle Jupyter Server**，复制 **VSCode Compatible URL**，再运行下面的命令，将 URL 粘贴到本机提示处。

```powershell
.\scripts\run_abo.ps1 -Command cloud-chat
```

## 本地开发

创建 Python 环境并安装 [ABO 运行依赖](requirements-abo.txt)。如果已有上次运行使用的 ABO 工作区，可以独立复制并校验必要资产：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-abo.txt
.\.venv\Scripts\python.exe .\scripts\import_abo_assets.py --source-workspace 'E:\大模型项目自学E\doc-vqa-assistant'
.\scripts\run_abo.ps1 -Command check
.\scripts\run_abo.ps1 -Command chat
```

本地默认运行目录、源字段和文本兼容证据。Kaggle 入口可用 GPU 执行读图。HTTP 可用 `-Command serve` 启动，默认地址为 `http://127.0.0.1:8769/docs`。请求 `POST /api/v1/chat` 时传入 `question`，再用返回的 `session_id` 续聊。

## 验收范围

本地新增了 49 个真实商品对话回归用例，以及覆盖 576 类的一商品选择和资料追问检查。旧冻结的 A、B、C、D 数据协议适用原实验口径。已运行结果保存在工作区下的 `abo_runtime/reports/ecommerce_rag`，包括原始 JSONL 与逐题 TXT。

项目主体保留 Adaptive-rag 的 MIT 许可。复用的 VisRAG 派生代码保留 [Apache-2.0 许可](abo_runtime/upstream/VisRAG/LICENSE)。模型与 ABO 数据分别遵循各自的发布许可。训练二进制、私有数据夹具与本地运行日志保存在授权的工作区与 Kaggle 输入中。
