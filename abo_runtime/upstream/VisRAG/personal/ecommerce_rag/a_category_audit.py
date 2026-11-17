"""Check the A answer path for independently selected families in every ABO category."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path

from full_catalog import ROOT, connect
from full_app import FullApplication
from audit import append_question
from common import WORKSPACE, file_hash, operation, read_jsonl, write_json, write_jsonl


DATA = ROOT.parent / "abo-a-category-coverage-v1"
LABELS = {"brand": "品牌", "color": "颜色", "material": "材质", "model_number": "型号"}


def order_key(value):
    return sha256(f"{DATA.name}|{value}".encode()).digest()


def expected_value(source, field):
    # Independently read the ABO field using the application's source-language policy.
    choices = [(entry.get("language_tag", ""), str(entry["value"]))
               for entry in source.get(field, []) if isinstance(entry, dict) and entry.get("value") is not None]
    preferred = [value for lang, value in choices if str(lang).startswith("en")]
    if not preferred:
        preferred = [value for lang, value in choices if lang == "zh_CN"] or [value for _, value in choices[:1]]
    return list(dict.fromkeys(value.strip() for value in preferred if value.strip()))


def load_manifest():
    return json.loads((ROOT / "manifest.json").read_text("utf-8"))


def make_cases():
    grouped = defaultdict(list)
    cases = []
    with closing(connect()) as db:
        source = db.execute(
            "SELECT product_type, family_key, min(id) AS id FROM products GROUP BY product_type, family_key")
        for row in source:
            grouped[row["product_type"]].append((order_key(row["family_key"]), row["id"], row["family_key"]))
        for kind, family_rows in sorted(grouped.items()):
            for _, row_id, family in sorted(family_rows)[:2]:
                row = db.execute("SELECT listing_key, raw_json, raw_sha256 FROM products WHERE id=?", (row_id,)).fetchone()
                raw = json.loads(row["raw_json"])
                fields = sorted(LABELS, key=lambda name: order_key(f"{family}|{name}"))
                selected = next((name for name in fields if expected_value(raw, name)), fields[0])
                values = expected_value(raw, selected)
                key = row["listing_key"]
                citation = f"{key}:metadata:{selected}"
                cases.append({
                    "question_id": f"A-CATEGORY-{len(cases)+1:04d}", "category": kind, "family_key": family,
                    "question": f"{key} 的{LABELS[selected]}是什么？", "listing_key": key, "field": selected,
                    "values": values, "reference_pages": [citation] if values else [],
                    "source_sha256": row["raw_sha256"],
                })
    return cases, {name: min(len(rows), 2) for name, rows in grouped.items()}


def freeze():
    cases, quota = make_cases()
    if set(quota) != set(load_manifest()["product_types"]):
        raise ValueError("Catalog category coverage mismatch")
    DATA.mkdir(parents=True)
    write_jsonl(DATA / "questions.jsonl", cases)
    write_json(DATA / "protocol.json", {
        "version": DATA.name, "case_sha256": file_hash(DATA / "questions.jsonl"),
        "catalog_sha256": load_manifest()["database_sha256"], "category_quota": quota,
        "scope": "Selected source fields and citations through Stage A; no estimates of open-ended category accuracy.",
    })


def load():
    path = DATA / "questions.jsonl"
    protocol = json.loads((DATA / "protocol.json").read_text("utf-8"))
    manifest = load_manifest()
    cases = read_jsonl(path)
    if (protocol["version"] != DATA.name or file_hash(path) != protocol["case_sha256"]
            or manifest["database_sha256"] != protocol["catalog_sha256"]
            or set(manifest["product_types"]) != set(protocol["category_quota"])
            or Counter(x["category"] for x in cases) != protocol["category_quota"]
            or len({(x["category"], x["family_key"]) for x in cases}) != len(cases)):
        raise ValueError("Frozen category audit or catalog mismatch")
    return cases, protocol


def check(case, record):
    if record["status"] != "ok" or record["model_calls"]:
        return False
    if not case["values"]:
        return record["action"] == "not_found" and not record["citations"] and "未报告" in record["answer"]
    if record["action"] != "answer" or record["citations"] != case["reference_pages"]:
        return False
    claims = record.get("claims", [])
    if len(claims) != 1:
        return False
    claim, = claims
    pages = record["retrieved_pages"]
    return (claim["listing_key"] == case["listing_key"] and claim["field"] == case["field"]
            and claim["values"] == case["values"] and claim["source"]["sha256"] == case["source_sha256"]
            and all(value in record["answer"] for value in case["values"])
            and len(pages) == 1 and pages[0]["rank"] == 1 and pages[0]["page_id"] == case["reference_pages"][0])


def run(out):
    from common import timestamp

    cases, protocol = load()
    out.mkdir(parents=True)
    totals = Counter()
    passes = Counter()
    with closing(FullApplication("A", mode="bm25", report_root=out / "session", audit=False,
                                model_available=False)) as app, (out / "results.jsonl").open("x", encoding="utf-8") as raw:
        session = app.session()
        config = out / "config.json"
        write_json(config, {"protocol": protocol, "a_runtime_config_sha256": session.csha, "source_sha256": session.source})
        config_sha = file_hash(config)
        for case in cases:
            record = session.respond(case["question"], return_audit=True)
            passed = check(case, record)
            totals[case["category"]] += 1
            passes[case["category"]] += passed
            record.update(
                question_id=case["question_id"], run_name=out.name, run_directory=str(out.relative_to(WORKSPACE)),
                reference_answer=" / ".join(case["values"]) or "源记录未报告",
                reference_pages=case["reference_pages"], category=case["category"], family_key=case["family_key"],
                config_sha256=config_sha, metrics={"strict_pass": passed, "field_present": bool(case["values"])},
            )
            raw.write(json.dumps(record, ensure_ascii=False) + "\n")
            raw.flush()
            append_question(out, record)
        result = {"categories": len(totals), "passing_categories": sum(passes[k] == totals[k] for k in totals),
                  "cases": len(cases), "passed": sum(passes.values()), "with_source_values": sum(bool(r["values"]) for r in cases),
                  "catalog_sha256": protocol["catalog_sha256"], "case_sha256": protocol["case_sha256"],
                  "timestamp": timestamp(),
                  "by_category": {k: {"passed": passes[k], "cases": totals[k]} for k in totals}}
    write_json(out / "summary.json", result)
    report = out / "全类目字段与引用验收.txt"
    report.write_text("A 全类目源字段验收。每类最多两个独立商品家族，经正常入口核对字段值、商品身份与来源引用。\n"
                      "这组抽查衡量类目覆盖与可追溯性。自然问法、图片和兼容准确率由各自评测衡量。\n\n"
                      + json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    operation("A 全类目字段和引用验收", "固定每类最多两个独立家族，逐题走 A 正常入口核对原字段及原文摘要。",
              f"{result['passed']}/{len(cases)}，覆盖{len(totals)}类。", [report, out / "逐题结果.txt"])
    if result["passed"] != len(cases):
        raise ValueError(f"Category audit failed; see {report}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze", "run"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    elif args.output is None or not args.output.resolve().is_relative_to(WORKSPACE / "reports"):
        parser.error("run requires a new --output directory under reports")
    else:
        result = run(args.output.resolve())
        print({key: value for key, value in result.items() if key != "by_category"})


if __name__ == "__main__":
    main()
