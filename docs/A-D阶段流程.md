# A–D 阶段详细流程

四个阶段共享 ABO 商品身份、证据来源和逐题记录。A 建立商品数据和证据，B 用这些资产完成会话取证，C 在固定证据上训练和验证图文适配器，D 复用目录、检索和按需读图来核对配件兼容。网页、FastAPI、PowerShell 和 Kaggle 使用同一个 Adaptive-rag 会话入口。

[首页总流程图](../README.md#a-d-总流程) · [统一问答与分流](ABO架构与验证.md) · [日志与排查](日志与排查.md)

## A 阶段

### 商品数据、索引与证据

```mermaid
flowchart TD
    src["ABO 官方商品元数据、图片清单和原图"] --> validate["校验源文件，保留原始记录与来源位置"]
    validate --> identify["按 ASIN 与站点确定商品身份"]
    identify --> db["全量 SQLite 目录<br/>147,702 条 · 576 类 · 32 站点"]
    db --> fts["名称、类目、型号检索<br/>FTS5 / BM25"]
    db --> dense["离线多语言 E5 向量<br/>按行与目录身份对应"]
    db --> source["可核实字段、标题及要点<br/>按需缓存原图、生成派生证据页"]
    db --> browser["网页浏览类目、分页商品和属性<br/>按同一条商品记录匹配官方照片"]
    fts --> check["逐条核对目录、全文索引、向量行和来源摘要"]
    dense --> check
    source --> sample["576 类分层字段与引用抽查"]
    check --> reuse["交给 B<br/>目录、候选查询和可追溯证据"]
    sample --> reuse
```

全量覆盖指商品元数据与索引。原图和派生证据页按用例或请求准备。新 Notebook 验证了覆盖 576 类的 1,089 次来源字段和引用抽查。全库结构检查与字段答题使用各自的报告。

- [构建全库与站点身份](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/full_catalog.py) · [生成向量](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/full_dense.py)
- [生成图文证据卡](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/full_cards.py) · [全库核对](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/a_catalog_verify.py) · [全类目抽查](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/a_category_audit.py)

## B 阶段

### 对话、检索与按需取证

```mermaid
flowchart TD
    q["完整问题、本线程需求与商品身份<br/>网页输入须连接 Kaggle"] --> plan["GPU · 8B 多任务语义规划<br/>逐项考虑可用工具，保留年龄和用途"]
    plan --> release["放宽某条件或查询可选值<br/>清除对应旧筛选，重新取证"] --> search
    plan --> route{"按依赖执行必要工具，可组合多路"}
    route -->|"问候、范围外、实时或交易请求"| plain["寒暄、说明范围或解释可用证据"]
    route -->|浏览目录| directory["运行目录分页，确认用户想查的类目"]
    route -->|问数量| tally["按记录、类目、站点和已核实条件统计"]
    tally -->|继续查看候选| search
    route -->|查找或调整条件| search["按真实类目、站点和源字段条件筛选"]
    directory -->|指定类目| search
    search --> rank["日常使用 SQLite BM25<br/>离线对照 E5 / RRF 及可选重排"]
    rank --> semantic["8B 按完整需求筛查物体与用途<br/>保留原始字段条件和明确的排除项"]
    semantic --> suitability["重新读候选原始记录<br/>检查年龄、用途和条件的支持、缺失与冲突"]
    suitability --> choose["解释选择理由和待核实项<br/>展示名称并保存 ASIN@站点"]
    rank -.-> visual["本地 -Visual · VisRAG-Ret<br/>至多六个候选的原图"] --> choose
    route -->|指定商品或追问| evidence{"本轮需要的来源"}
    choose --> evidence
    evidence -->|无本轮证据| missing["只说明本次未核实，不编造属性<br/>不据此断言全库不存在"]
    evidence -->|明确规格| fields["直接读源值与单位"]
    evidence -->|尺码或变体追问| variants["读原标题与字段，查同站点同品牌同型号"]
    evidence -->|概览、继续或完整展示| detail["复用商品身份，按源条目组织内容"]
    evidence -->|比较或复合请求| multi["按商品和站点独立查证，保留部分可答证据"]
    evidence -->|图片或页内问题| reader["按需选择原图或证据页，交给 8B Reader"]
    route -->|售后| policy["核对站点与已保存的政策快照"]
    reader --> guard["检查答案和引用，按已启用策略有限补查"]
    fields --> answer["综合多个工具的可答部分<br/>8B 证据复核与有限纠正<br/>写入会话、逐题与运行日志"]
    tally --> answer
    variants --> answer
    detail --> answer
    multi --> answer
    guard --> answer
    policy --> answer
    plain --> answer
    answer --> browser["网页显示回答和来源<br/>按本轮任务选择商品图片"]
    rank -.-> offline["独立的冻结检索对照<br/>以商品身份与家族计分"]
```

目录页码、上一轮筛选条件和当前选中的商品分别维护。目录数量以历史商品记录为单位，同一 ASIN 在不同站点可分别出现。明确的颜色、材质、尺码和款式条件在排序前核对，查询现有库存时只报告可验证的历史字段。尺码保留 US、UK、EU 等原始标注，同站点的变体需核对品牌与型号。Notebook 与本地日常会话使用 BM25。本地 `-Visual` 可对文字检索候选做商品图重排。B 的冻结检索对照另有独立协议。

CPU 模式沿用明确请求的确定性取证。GPU 模式在其上增加每轮多任务计划、需求记忆、推荐说明和来源复核。同一问题可同时统计、搜索、比较、说明缺失价格与展示照片。[历史续聊和断线恢复](多任务对话与恢复.md)保存各线程独立状态，不再由新对话自动发送“重置”。

- [新项目会话路由](../app/abo/service.py) · [商品对话与逐件答证](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/full_app.py)
- [检索模式](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/full_retrieval.py) · [原始字段、单位和图片证据](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/full_evidence.py) · [售后快照](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/full_policy.py)
- [冻结检索对照](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/b_retrieval_scope.py) · [可读展示](../app/abo/presentation.py)

## C 阶段

### 8B 图文微调与同题对照

```mermaid
flowchart TD
    evidence["复用 B 的证据页、引用格式与 Reader"] --> frozen["冻结商品家族、源答案、页顺序和摘要"]
    frozen --> train["120 道训练题<br/>图文取证、单位、引用、缺证据"]
    frozen --> dev["50 道开发题<br/>阈值与选版检查"]
    frozen --> suite["冻结问答与分层照片对照"]
    model["同一 Qwen3-VL-8B-Instruct<br/>Kaggle T4 · 4-bit NF4 · FP16"] --> base["C0 原提示"]
    model --> prompted["C1 核对字段、单位与引用的提示"]
    train --> finetune["QLoRA 更新，记录 loss、显存与训练配置"]
    model --> finetune
    finetune --> adapter["保存适配器和 SHA256"]
    adapter --> adapted["C2 同一 C1 提示加适配器"]
    base --> paired["相同问题、证据和底座进行配对"]
    prompted --> paired
    adapted --> paired
    dev --> paired
    suite --> paired
    paired --> score["评分检查答案、原值、单位与引用<br/>检查拒答、误拒答及逐题退化"]
    score --> publish["归档达标的 120/50 正式版本<br/>按需加载至图文 Reader 与 D 的原图环节"]
    publish --> runtime["本地 NF4 从官方四片权重加载<br/>Kaggle 使用官方模型挂载"]
    runtime --> public["公开 Notebook 启动个人 GPU 会话<br/>从 GitHub Release 校验并加载正式 LoRA"]
    instructions["独立补充指令<br/>复合任务、指代、适龄缺证据与推荐解释"] --> dialog["对话规划与说明 QLoRA 候选<br/>保存训练loss和按类目分离的划分"]
    model --> dialog
    dialog --> gate["仅按独立开发对照决定是否启用<br/>留出集另报，正式C图文适配器保持独立"]
```

训练和选版记录沿用原冻结协议。C0、C1、C2 比较回答侧的变化，B 的检索质量使用 B 的独立协议。日常会话复用已确认好的适配器摘要。日志维护发布无需重新微调。

- [协议和家族隔离](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/c8_protocol.py) · [4-bit 加载](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/qwen8_runtime.py) · [QLoRA 与对照运行](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/c8_experiment.py)
- [训练集扩展与适配器选版](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/c8_upgrade.py) · [照片覆盖评测](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/c8_visual_breadth.py) · [加载并核验正式适配器](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/c8_reader.py)

## D 阶段

### 配件兼容、多角色核验与故障记录

```mermaid
flowchart TD
    q["统一入口收到配件兼容问题或相关追问"] --> intent["需求理解角色<br/>规则与按需的 8B 结构化提议"]
    intent --> model["型号识别角色<br/>解析俗称、变体和已确认机型"]
    model --> ready{"设备、配件和站点足够明确？"}
    ready -->|有歧义| clarify["询问区分信息，保存待确认项"]
    ready -->|确认| search["证据检索角色<br/>用 A 的目录和 B 的检索核对型号及站点"]
    search --> review["兼容复核角色<br/>核对来源、完整型号、引用和支持或排除关系"]
    review -->|可继续补查| retry["在预算内有限补检"]
    retry --> search
    review -->|按需看图| image["C 适配器辅助读取官方原图"]
    image --> answer
    clarify --> answer
    review --> answer["明确支持、排除、冲突、待澄清或证据不足"]
    intent -.-> fault["异常记录角色、尝试和耗时<br/>继续安全回退或说明不可用"]
    search -.-> fault
    review -.-> fault
    fault --> answer
    answer --> log["回话与引用 · 逐题 TXT / JSONL<br/>对话记录 · 运行事件 · 同号问题追踪"]
```

兼容结论依赖可核实的型号和站点证据。遇到故障时，逐题记录继续保留角色、次数、回退和原始来源。独立的 D 评测代码支持冻结场景与故障注入。日常 Kaggle 验收走过俗称澄清、候选搜索、指代追问和 LoRA 辅助读图。

- [需求和用户原文校验](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/d_compat_route.py) · [型号与来源匹配](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/d_compat_catalog.py)
- [四角色和故障恢复](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/stage_d.py) · [统一交互](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/d_chat.py) · [冻结验收与故障注入](../abo_runtime/upstream/VisRAG/personal/ecommerce_rag/stage_d_eval.py)
