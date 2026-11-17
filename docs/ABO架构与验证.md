# ABO 架构与验证

应用以 Adaptive-rag 的 FastAPI、LangGraph 状态与五路策略实现 ABO 配置。运行入口默认采用商品档案和只读兼容职责。明确的需求由确定性分流承接，模糊表达交给 Qwen3-VL-8B-Instruct 进行受约束的语义分类。

[A–D 阶段详细流程](A-D阶段流程.md) · [日志与排查](日志与排查.md)

```mermaid
flowchart TD
    A["Notebook、PowerShell、HTTP"] --> B["当前输入和会话中已确认的商品身份"]
    B --> R{"Adaptive-rag 五路分流"}
    R -->|direct| D["问候或可解释的业务边界"]
    R -->|refuse| F["解释 ABO 服务范围"]
    R -->|single| S["目录、商品取证或政策快照"]
    R -->|multi| M["逐件查证、规格对比和部分答案"]
    R -->|agent| G["需求、型号、检索、兼容复核"]
    S --> E["源字段、原始描述、8B / LoRA 读图"]
    M --> E
    E --> P["用商品名称和自然句子组织回答"]
    G --> P
    D --> P
    F --> P
    P --> T["实际对话、逐题 TXT / JSONL 与运行事件"]
```

商品的身份由 ASIN 和站点共同确定。目录翻页与商品展示各自依赖最近任务。明确要求全部资料时一次给出该商品的可读源属性、产品要点、原始描述和来源字段。要求剩余资料时依据已经展示的源条目。价格、实时库存和交易动作都有明确的资料边界。

商品比较先分别核对证据。重量和尺寸仅在可验证单位内换算。缺失值或不可比单位保留可确认部分。同步给出规格与价格要求时，已验证规格仍会返回。

原项目的全量 ABO 索引、冻结协议、C 阶段 120/50 LoRA 和 D 阶段四角色可读证据工作流来自 VisRAG 派生项目。常用数值来自本项目的实际运行和冻结题集。

- [Adaptive-rag 源码](https://github.com/JEen-wang/Adaptive-rag/tree/2a12a62382a8c3ec033fa9a5b19b9b23b8780ab8)
- [Adaptive-rag 路由设计](https://github.com/JEen-wang/Adaptive-rag/blob/main/docs/04-adaptive-rag.md)
- [ABO 数据与许可](https://amazon-berkeley-objects.s3.us-east-1.amazonaws.com/index.html)
- [Qwen3-VL-8B-Instruct](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct)
