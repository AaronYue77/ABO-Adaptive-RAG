"""Separate, reproducible QLoRA experiment for multi-tool dialogue planning.

No frozen A–D questions or product answers enter this instruction dataset.
Categories and question templates define this narrow evaluation scope.
"""

import argparse
import json
import random
import re
from hashlib import sha256
from pathlib import Path
from time import perf_counter

from app.abo.service import bind_runtime
from app.abo.turns import ANSWER_PROMPT, PLAN_PROMPT, parse_json, source_item

TRAIN = [
    ("BACKPACK", "双肩包", "backpack"),
    ("CHAIR", "椅子", "chair"),
    ("SOFA", "沙发", "sofa"),
    ("SHOES", "鞋", "shoes"),
    ("BOOT", "靴子", "boots"),
    ("SANDAL", "凉鞋", "sandals"),
    ("HEADPHONES", "耳机", "headphones"),
    ("DRINKING_CUP", "杯子", "drinking cup"),
    ("TABLE", "桌子", "table"),
    ("BED", "床", "bed"),
    ("LIGHT_BULB", "灯泡", "light bulb"),
    ("COFFEE", "咖啡", "coffee"),
    ("RUG", "地毯", "rug"),
    ("PILLOW", "枕头", "pillow"),
    ("TOWEL", "毛巾", "towel"),
    ("LUGGAGE", "行李箱", "luggage"),
]
DEV = [("ELECTRIC_FAN", "电风扇", "electric fan"), ("WATCH", "手表", "watch")]
TEST = [("TREADMILL", "跑步机", "treadmill"), ("BOTTLE", "水瓶", "water bottle")]


def build_dataset():
    rows = []
    for split, categories in (("train", TRAIN), ("dev", DEV), ("test", TEST)):
        for category, name, english in categories:
            base = {"age": None, "gender": "", "purpose": [], "constraints": []}
            cases = [
                (
                    f"我想买一个{name}，请帮我挑选并解释理由",
                    [("search", f"搜索{name}"), ("advice", f"按需求选择{name}并解释理由")],
                    [],
                    dict(base),
                ),
                (
                    f"帮我找几款{name}，先看图片，再说说分别有什么特点",
                    [
                        ("search", f"搜索{name}"),
                        ("advice", f"解释候选{name}的特点"),
                        ("visual", f"展示{name}图片"),
                    ],
                    [],
                    dict(base),
                ),
                (
                    f"{name}一共有多少款？再找几款给我看看",
                    [("count", f"{name}类商品有多少个？"), ("search", f"搜索{name}")],
                    [],
                    dict(base),
                ),
                (
                    "第一款看着不错，为什么你觉得它适合我？",
                    [("advice", "解释第一款与已知需求的匹配理由和不足")],
                    [1],
                    dict(base),
                ),
                (
                    "这两款该选哪个？比较一下材质和重量，顺便告诉我价格",
                    [
                        ("lookup", "比较前两款的材质和重量"),
                        ("business", "前两款的价格是多少？"),
                        ("advice", "结合已知需求比较前两款并说明取舍"),
                    ],
                    [1, 2],
                    dict(base),
                ),
                (
                    f"给我推荐黑色的{name}，但不要把没有售价当成免费",
                    [("search", f"搜索黑色{name}"), ("advice", f"说明黑色{name}的推荐理由")],
                    [],
                    {**base, "constraints": ["黑色"]},
                ),
                (
                    "我说的是第二款，不是第一款，介绍它的属性并看看图",
                    [("lookup", "第二款的商品属性"), ("visual", "第二款的商品图片")],
                    [2],
                    dict(base),
                ),
                (
                    f"给8岁男孩找{name}，要核实适用年龄，不能只搜商品名字",
                    [("search", f"搜索{name}"), ("advice", f"核实{name}是否适合8岁男孩")],
                    [],
                    {**base, "age": 8, "gender": "boy"},
                ),
            ]
            for index, (question, tasks, focus, needs) in enumerate(cases):
                candidates = (
                    []
                    if index in {0, 1, 2, 5, 7}
                    else [
                        {"listing_key": "reference-one@amazon.com", "title": f"{name}候选甲"},
                        {"listing_key": "reference-two@amazon.com", "title": f"{name}候选乙"},
                    ]
                )
                context = {
                    "previous_needs": {},
                    "previous_query": english if candidates else "",
                    "recent_turns": [],
                    "candidates": candidates,
                    "selected": [],
                    "question": question,
                }
                target = {
                    "tasks": [{"tool": tool, "question": query} for tool, query in tasks],
                    "query": english,
                    "needs": needs,
                    "focus": focus,
                    "images": any(t[0] in {"search", "visual"} for t in tasks),
                }
                rows.append(
                    {
                        "id": f"{split}-{category}-{index}",
                        "split": split,
                        "category": category,
                        "stage": "plan",
                        "context": context,
                        "target": target,
                    }
                )
            extras = [
                (
                    f"找几款{name}，不要黑色的，帮我选一个并说明取舍",
                    [("search", f"搜索{name}排除黑色"), ("advice", "结合排除条件解释候选的取舍")],
                    {"age": None, "gender": "", "purpose": [], "constraints": ["排除黑色"]},
                    [],
                    {},
                ),
                (
                    f"给我推荐{name}，预算200元，要查资料和解释理由，价格没有记录就明说",
                    [
                        ("search", f"搜索{name}"),
                        ("business", f"{name}的售价能否核实"),
                        ("advice", "解释推荐理由与预算未核实部分"),
                    ],
                    {"age": None, "gender": "", "purpose": [], "constraints": ["预算200元"]},
                    [],
                    {},
                ),
                (
                    "刚才的颜色换成白色，其他用途保持，先帮我筛选再讲理由",
                    [("search", f"搜索白色{name}"), ("advice", "按保留的日常使用需求说明筛选理由")],
                    {"age": None, "gender": "", "purpose": ["日常使用"], "constraints": ["白色"]},
                    [],
                    {"age": None, "gender": "", "purpose": ["日常使用"], "constraints": ["黑色"]},
                ),
                (
                    "第二款我想给65岁的妈妈用，能不能确定适合她？别猜没有写的参数",
                    [("advice", "核对第二款是否适合65岁母亲，明确缺少的适用性证据")],
                    {"age": 65, "gender": "female", "purpose": [], "constraints": []},
                    [2],
                    {},
                ),
            ]
            for index, (question, tasks, needs, focus, previous_needs) in enumerate(extras):
                context = {
                    "previous_needs": previous_needs,
                    "previous_query": english,
                    "recent_turns": [],
                    "candidates": [
                        {"listing_key": "reference-one@amazon.com", "title": f"{name}候选甲"},
                        {"listing_key": "reference-two@amazon.com", "title": f"{name}候选乙"},
                    ],
                    "selected": [],
                    "question": question,
                }
                rows.append(
                    {
                        "id": f"{split}-{category}-extra-{index}",
                        "split": split,
                        "category": category,
                        "stage": "plan",
                        "context": context,
                        "target": {
                            "tasks": [{"tool": tool, "question": q} for tool, q in tasks],
                            "query": english,
                            "needs": needs,
                            "focus": focus,
                            "images": any(tool == "search" for tool, q in tasks),
                        },
                    }
                )
            from full_retrieval import FullRetriever

            retriever = FullRetriever()
            try:
                product = retriever.db.execute(
                    "SELECT * FROM products WHERE product_type=? ORDER BY id LIMIT 1", (category,)
                ).fetchone()
            finally:
                retriever.close()
            if product:
                source = dict(product)
                for number, with_age in enumerate((True, False)):
                    needs = {
                        "age": 8 if with_age else None,
                        "gender": "boy" if with_age else "",
                        "purpose": [],
                        "constraints": [],
                    }
                    fact = source_item(source, needs, 1)
                    if with_age and fact["age_check"] != "unknown":
                        continue
                    question = (
                        f"给8岁的男孩买第一款{name}合适吗？请解释原因"
                        if with_age
                        else f"第一款{name}有什么可以核实的推荐理由？"
                    )
                    context = {
                        "question": question,
                        "user_context": {"needs": needs, "recent_user_questions": []},
                        "task_plan": {"tasks": [{"tool": "advice", "question": question}]},
                        "fresh_sources": [fact],
                        "verified_tool_answers": [],
                        "eligible_recommendations": [] if with_age else [fact["listing_key"]],
                    }
                    features = []
                    for key, label in (
                        ("brand", "品牌"),
                        ("color", "颜色"),
                        ("material", "材质"),
                        ("model_number", "型号"),
                    ):
                        if fact["fields"].get(key):
                            features.append(label + "为" + str(fact["fields"][key][0]))
                    details = "，".join(features[:2]) or "保留了商品原始名称与型号等记录"
                    answer = (
                        (
                            f"现有资料未说明第一款{name}是否适合8岁孩子，暂时不能确认适龄。资料中{details}。"
                            "这些只能用于核对商品，不能据此推断儿童使用安全性；建议先向实际商品页面核实适用年龄与尺寸。"
                        )
                        if with_age
                        else (
                            f"第一款{name}可以先作为候选，资料中{details}。这些是能核实的选购依据。"
                            "是否适合你，还需要结合具体用途和尺寸要求；我可以继续帮你核对相应的源字段。"
                        )
                    )
                    rows.append(
                        {
                            "id": f"{split}-{category}-answer-{number}",
                            "split": split,
                            "category": category,
                            "stage": "answer",
                            "context": context,
                            "target": {"answer": answer, "citations": ["E1"], "recommended": []},
                        }
                    )
    assert not (
        set(category for category, _, _ in TRAIN) & set(category for category, _, _ in DEV + TEST)
    )
    return rows


def inputs_for(runtime, row):
    import torch

    prompt = PLAN_PROMPT if row["stage"] == "plan" else ANSWER_PROMPT
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": json.dumps(row["context"], ensure_ascii=False)},
    ]
    inputs = runtime.processor.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
        return_dict=True,
        return_tensors="pt",
    ).to("cuda")
    prefix = inputs["input_ids"].shape[1]
    tokenizer = runtime.processor.tokenizer
    target = tokenizer(
        json.dumps(row["target"], ensure_ascii=False), add_special_tokens=False, return_tensors="pt"
    )["input_ids"].to("cuda")
    target = torch.cat((target, target.new_tensor([[tokenizer.eos_token_id]])), dim=1)
    inputs["input_ids"] = torch.cat((inputs["input_ids"], target), dim=1)
    inputs["attention_mask"] = torch.ones_like(inputs["input_ids"])
    if "mm_token_type_ids" in inputs:
        inputs["mm_token_type_ids"] = torch.cat(
            (inputs["mm_token_type_ids"], torch.zeros_like(target)), dim=1
        )
    positions = torch.arange(prefix - 1, inputs["input_ids"].shape[1] - 1, device="cuda")
    return inputs, target, positions


def score_prediction(row, obj):
    if row["stage"] == "plan":
        expected = {task["tool"] for task in row["target"]["tasks"]}
        actual = {task["tool"] for task in obj["tasks"]}
        needs = obj["needs"]
        target = row["target"]["needs"]
        correct = expected <= actual and actual <= expected | {"visual"}
        correct &= needs.get("age") == target["age"] and obj["focus"] == row["target"]["focus"]
        aliases = {
            "boy": "male",
            "男孩": "male",
            "男": "male",
            "girl": "female",
            "女孩": "female",
            "女": "female",
        }
        wanted = aliases.get(target["gender"], target["gender"])
        received = aliases.get(needs.get("gender", ""), needs.get("gender", ""))
        correct &= received == wanted
        text = json.dumps(needs, ensure_ascii=False).casefold()
        for requirement in target["constraints"]:
            if "黑色" in requirement:
                correct &= bool(re.search(r"黑|black", text))
            if "白色" in requirement:
                correct &= bool(re.search(r"白|white", text))
            if "排除" in requirement:
                correct &= bool(re.search(r"不|排除|非|exclude|not|avoid|without", text))
            for number in re.findall(r"\d+", requirement):
                correct &= number in text
        if "日常使用" in target["purpose"]:
            correct &= bool(re.search(r"日常|daily|everyday", text))
        return bool(correct)
    correct = (
        isinstance(obj["answer"], str) and len(obj["answer"]) > 30 and obj["citations"] == ["E1"]
    )
    if row["context"]["user_context"]["needs"]["age"] is not None:
        correct &= not obj["recommended"] and any(
            term in obj["answer"]
            for term in (
                "无法",
                "不能",
                "未标",
                "未明确",
                "未说明",
                "未提供",
                "不足",
                "未报告",
                "未收录",
                "不明确",
                "未记录",
            )
        )
    correct &= set(obj["recommended"]) <= {row["context"]["fresh_sources"][0]["listing_key"]}
    return bool(correct)


def evaluate(runtime, rows, output, variant):
    from full_conversation import FullConversation

    class Evidence:
        load_count = 1

        def ensure_reader(self):
            return type("Reader", (), {"vlm": runtime})()

    conversation = FullConversation(Evidence())
    scores = []
    for row in rows:
        trace = []
        try:
            prompt = PLAN_PROMPT if row["stage"] == "plan" else ANSWER_PROMPT
            raw = conversation.generate(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(row["context"], ensure_ascii=False)},
                ],
                "dialogue_training_eval",
                trace,
                550,
                use_adapter=variant == "candidate",
            )
            obj = parse_json(raw)
            correct = score_prediction(row, obj)
            error = None
        except Exception as exc:
            raw = trace[-1].get("raw_output", "") if trace else ""
            correct, error = False, type(exc).__name__
        result = {
            "id": row["id"],
            "question": row["context"]["question"],
            "target": row["target"],
            "raw_output": raw,
            "passed": bool(correct),
            "error": error,
            "calls": trace,
        }
        scores.append(result)
        with (output / f"{variant}-evaluation.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False) + "\n")
        print(variant, row["id"], bool(correct), flush=True)
    return {"cases": len(scores), "passed": sum(row["passed"] for row in scores), "rows": scores}


def train(output, epochs=1, all_text_layers=False):
    import peft
    import torch
    from common import file_hash, operation, write_json
    from qwen8_runtime import MODEL_ID, REVISION, Qwen8Runtime

    rows = build_dataset()
    output.mkdir(parents=True, exist_ok=False)
    dataset = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    (output / "instruction-data.jsonl").write_text(dataset, "utf-8")
    (output / "plan-prompt.txt").write_text(PLAN_PROMPT, "utf-8")
    (output / "answer-prompt.txt").write_text(ANSWER_PROMPT, "utf-8")
    write_json(
        output / "protocol.json",
        {
            "version": 2,
            "scope": "template-based held-out categories, not universal language understanding",
            "model": MODEL_ID,
            "revision": REVISION,
            "dataset_sha256": sha256(dataset.encode()).hexdigest(),
            "splits": {
                split: sum(row["split"] == split for row in rows)
                for split in ("train", "dev", "test")
            },
            "epochs": epochs,
            "learning_rate": 0.0001,
            "accumulation": 4,
            "seed": 20261118,
            "all_text_layers": all_text_layers,
            "selection": "strict development improvement, retain all failures, no frozen A-D data",
        },
    )
    runtime = Qwen8Runtime()
    dev = [row for row in rows if row["split"] == "dev"]
    before = evaluate(runtime, dev, output, "base")
    model = peft.prepare_model_for_kbit_training(
        runtime.model,
        use_gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
    )
    layers = [
        name
        for name, _ in model.named_modules()
        if "language_model" in name and name.endswith(".self_attn.q_proj")
    ]
    selected_layers = layers if all_text_layers else layers[-4:]
    targets = [
        name.removesuffix("q_proj") + suffix
        for name in selected_layers
        for suffix in ("q_proj", "v_proj")
    ]
    model = peft.get_peft_model(
        model, peft.LoraConfig(r=8, lora_alpha=16, target_modules=targets, bias="none")
    )
    runtime.model = model
    model.train()
    parameters = [parameter for parameter in model.parameters() if parameter.requires_grad]
    optimizer = torch.optim.AdamW(parameters, lr=0.0001)
    scaler = torch.amp.GradScaler("cuda", init_scale=256, enabled=runtime.dtype == torch.float16)
    examples = [row for row in rows if row["split"] == "train"]
    rng = random.Random(20261118)
    torch.manual_seed(20261118)
    started = perf_counter()
    updates = 0
    for epoch in range(epochs):
        rng.shuffle(examples)
        for start in range(0, len(examples), 4):
            optimizer.zero_grad(set_to_none=True)
            batch = examples[start : start + 4]
            for row in batch:
                inputs, target, positions = inputs_for(runtime, row)
                logits = model(**inputs, use_cache=False, logits_to_keep=positions).logits
                loss = torch.nn.functional.cross_entropy(
                    logits.float().flatten(0, 1), target.flatten()
                )
                if not torch.isfinite(loss):
                    raise RuntimeError("Nonfinite training loss")
                scaler.scale(loss / len(batch)).backward()
                with (output / "training-loss.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(
                        json.dumps({"id": row["id"], "loss": loss.item(), "epoch": epoch + 1})
                        + "\n"
                    )
                del inputs, target, positions, logits, loss
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(parameters, 1.0)
            previous = scaler.get_scale()
            scaler.step(optimizer)
            scaler.update()
            updates += scaler.get_scale() >= previous
            print("train", start + len(batch), "/", len(examples), "updates", updates, flush=True)
    if not updates:
        raise RuntimeError("No finite optimizer step")
    model.save_pretrained(output / "adapter", save_embedding_layers=False)
    training_seconds = perf_counter() - started
    trainable_parameters = sum(parameter.numel() for parameter in parameters)
    write_json(
        output / "training-completion.json",
        {
            "optimizer_updates": int(updates),
            "trainable_parameters": trainable_parameters,
            "training_seconds": training_seconds,
        },
    )
    # k-bit training upcasts unquantized modules. Reload NF4 for a fair inference comparison.
    import gc

    del model, optimizer, parameters, scaler, runtime
    gc.collect()
    torch.cuda.empty_cache()
    runtime = Qwen8Runtime()
    runtime.model = peft.PeftModel.from_pretrained(runtime.model, output / "adapter").eval()
    after = evaluate(runtime, dev, output, "candidate")
    selected = after["passed"] > before["passed"]
    test = (
        evaluate(runtime, [row for row in rows if row["split"] == "test"], output, "candidate")
        if selected
        else None
    )
    summary = {
        "base_dev": {k: v for k, v in before.items() if k != "rows"},
        "candidate_dev": {k: v for k, v in after.items() if k != "rows"},
        "heldout": {k: v for k, v in test.items() if k != "rows"} if test else None,
        "selected_by_dev": selected,
        "optimizer_updates": int(updates),
        "training_seconds": training_seconds,
        "adapter_sha256": file_hash(output / "adapter/adapter_model.safetensors"),
        "trainable_parameters": trainable_parameters,
        "note": "Planner and grounded advice instruction tuning; does not replace the frozen image C adapter.",
    }
    write_json(output / "training-summary.json", summary)
    (output / "训练与对照记录.txt").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    operation(
        "8B多任务对话规划补充QLoRA",
        "使用独立指令题，按类目分离训练开发与留出集；仅按开发集选择候选。",
        json.dumps(summary, ensure_ascii=False),
        [output / "训练与对照记录.txt"],
        "保留全部逐题生成和loss，正式C图文适配器未改动。",
    )
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--all-text-layers", action="store_true")
    args = parser.parse_args()
    bind_runtime()
    train(args.output, args.epochs, args.all_text_layers)
