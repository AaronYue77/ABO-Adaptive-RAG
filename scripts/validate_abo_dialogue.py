"""Run a separate everyday-dialogue acceptance without touching frozen A–D data."""

import json
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.abo.service import AboDialogue, bind_runtime  # noqa: E402
from app.abo.state import clear_state, export_state, import_state  # noqa: E402

CASES = (
    ("audience", "给8岁男孩找篮球，要核实适用年龄并说明理由"),
    ("reason", "第一款看着可以，告诉我为什么推荐它，还有哪些条件未确认"),
    ("resume", "请继续核对刚才第一款的适龄资料"),
    ("new", ""),
    ("compound", "手表类商品有多少个？顺便找几款电风扇给我看看图片"),
    ("new", ""),
    ("budget", "找黑色双肩包，用来上大学带书和15英寸笔记本，说明理由与价格"),
    ("change", "改成蓝色，其他用途保持，说明选择理由"),
    ("new", ""),
    ("two_categories", "分别找红色鞋子和蓝色背包，各选一款并说明理由"),
    ("new", ""),
    ("visual", "帮我看图，B07T9TLVJ3能配 iPhone 8 Plus 吗？"),
    ("scope", "写Python代码帮我统计商品"),
    ("policy", "Amazon.com 的一般退货政策是什么？"),
)


def verify(chat):
    rows = []
    saved = None
    for kind, question in CASES:
        if kind == "new":
            clear_state(chat, "验收场景分隔，程序事件。")
            continue
        if kind == "resume":
            saved = export_state(chat)
            clear_state(chat, "线程恢复验收分隔，程序事件。")
            import_state(chat, saved)
        row = chat.respond(question)
        passed = row["status"] == "ok" and not row.get("issue_id")
        if kind in {"audience", "reason", "resume"}:
            passed &= chat.turn_memory.get("needs", {}).get("age") == 8
            passed &= bool(row["citations"])
            if not any(
                item.get("age_check") == "supported" for item in row.get("requirement_checks", [])
            ):
                passed &= any(
                    word in row["display_answer"]
                    for word in (
                        "未收录",
                        "未说明",
                        "未报告",
                        "未标",
                        "不足",
                        "不能",
                        "无法",
                        "未核实",
                        "没有能",
                    )
                )
        if kind == "compound":
            passed &= (
                "28" in row["display_answer"]
                and "abo:catalog:count" in row["citations"]
                and bool(row.get("catalog_items"))
            )
            passed &= not any(
                word in row["display_answer"]
                for word in ("无法提供图片", "图片无法", "不能提供图片")
            )
        if kind == "budget":
            passed &= any(word in row["display_answer"] for word in ("售价", "价格")) and bool(
                row["citations"]
            )
            passed &= "书" in json.dumps(chat.turn_memory, ensure_ascii=False)
        if kind == "change":
            passed &= "书" in json.dumps(chat.turn_memory.get("needs"), ensure_ascii=False)
        if kind == "two_categories":
            passed &= (
                len(
                    [
                        task
                        for task in row.get("task_plan", {}).get("tasks", [])
                        if task["tool"] == "search"
                    ]
                )
                == 2
            )
        if kind == "visual":
            passed &= bool(row.get("visual_observation")) and row["decision"] == "compatible"
        if kind == "scope":
            passed &= row["decision"] == "out_of_scope"
        if kind == "policy":
            passed &= bool(row["citations"]) and row["decision"] == "answer"
        result = {
            "kind": kind,
            "question": question,
            "passed": bool(passed),
            "decision": row["decision"],
            "answer": row["display_answer"],
            "citations": row["citations"],
            "issue_id": row.get("issue_id"),
        }
        rows.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    summary = {
        "cases": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "checks": rows,
        "scope": "separate interactive acceptance, not frozen A-D scores",
        "model_loads": chat.app.reader.load_count,
    }
    (chat.output / "everyday-acceptance.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    (chat.output / "专项验收结果.txt").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    assert summary["passed"] == summary["cases"], [row for row in rows if not row["passed"]]
    return summary


if __name__ == "__main__":
    workspace = bind_runtime()
    output = workspace / "reports/ecommerce_rag" / ("abo-dialogue-validation-" + uuid4().hex[:10])
    output.mkdir(parents=True)
    session = AboDialogue(output, gpu=True, entry="独立日常商品对话验收")
    try:
        verify(session)
    finally:
        session.close()
