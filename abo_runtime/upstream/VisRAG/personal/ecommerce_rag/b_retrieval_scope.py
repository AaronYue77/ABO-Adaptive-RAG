"""Frozen source-derived retrieval benchmarks over the complete ABO index."""
import argparse
from collections import Counter
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import re
from math import ceil, log2

from full_catalog import ROOT, connect, field
from audit import append_question
from common import WORKSPACE, file_hash, operation, read_jsonl, timestamp, write_json, write_jsonl
from full_retrieval import FullRetriever
from stage_a import snapshot


DATA = ROOT.parent / "abo-b-natural-300-v1"
QUOTAS = {
    "dev": {"natural": 15, "model": 10, "variant": 10, "language": 8, "tail": 10, "missing": 7},
    "test": {"natural": 60, "model": 45, "variant": 45, "language": 30, "tail": 35, "missing": 25},
}
EXPANDED_DATA = ROOT.parent / "abo-b-retrieval-1500-v1"
EXPANDED_QUOTAS = {
    "dev": dict.fromkeys(("natural", "model", "variant", "tail", "compound", "missing"), 50),
    "test": {"natural": 240, "model": 240, "variant": 200, "tail": 160, "compound": 120, "missing": 240},
}
MODES = ("bm25", "dense", "hybrid")
LANGUAGES = ("ja_JP", "de_DE", "fr_FR", "es_ES", "it_IT", "pt_BR")
CATALOG_SHA = json.loads((ROOT / "manifest.json").read_text("utf-8"))["database_sha256"] if (ROOT / "manifest.json").is_file() else None


def prior_families(expanded=False):
    output = set()
    old = ROOT.parent / "abo-full-evaluation-v2/retrieval-questions.jsonl"
    if old.is_file():
        output.update(row["reference_family"] for row in read_jsonl(old))
    old = ROOT.parent / "abo-8b-eval-v1/retrieval-queries.jsonl"
    if old.is_file():
        output.update(row["family_key"] for row in read_jsonl(old))
    if expanded:
        snapshots = (
            "abo-b-natural-300-v1/queries.jsonl", "abo-stage-d-v1/cases.jsonl",
            "abo-8b-eval-v1/questions.jsonl", "abo-8b-supplement-v1/qa/questions.jsonl",
            "abo-8b-120-v1/qa/questions.jsonl", "abo-8b-120-v1/photos/questions.jsonl",
            "abo-8b-photos-v1/questions.jsonl", "abo-8b-supplement-v1/photos/questions.jsonl",
            "abo-8b-photo-breadth-v1/questions.jsonl",
        )
        for relative in snapshots:
            for row in read_jsonl(ROOT.parent / relative):
                output.update(row.get("family_keys") or [row.get("family_key") or row["reference_family"]])
    return output


def facts(row, cat_sizes, expanded=False):
    data = json.loads(row["raw_json"])
    title = row["title"].strip()
    model = field(data, "model_number").strip()
    names = [(x.get("language_tag"), x.get("value","").strip()) for x in data.get("item_name", [])
             if x.get("language_tag") in LANGUAGES and 12 <= len(x.get("value","")) < 160]
    english = [x.get("value", "").strip() for x in data.get("item_name", []) if x.get("language_tag") == "en_US"]
    name = english[0] if english else ""
    queries = {}
    if row["brand"] and re.search(r"[a-zA-Z]", row["brand"]) and 4 <= len(model) <= 40 and re.search(r"\d", model):
        if re.fullmatch(r"[\w ./()-]+", model) and len(model.split()) <= 5:
            queries["model"] = (f'{row["brand"]} {model}', row["domain"], {})
    if len(name) >= 18:
        desc = re.split(r",| - | \(", name, maxsplit=1)[0].strip()
        if len(desc.split()) >= 4:
            queries["natural"] = (f"帮我找这款 {desc}，我想看它的商品资料", desc, {})
        if 8 <= cat_sizes[row["product_type"]] <= 145:
            queries["tail"] = (f"想找 {name[:160]}，在哪个商品条目？", name[:160], {})
    if row["market_count"] >= 2 and 14 <= len(title) < 150:
        queries["variant"] = (f'只查 {row["domain"]} 上的 {title}', title, {"domain": row["domain"]})
    if names and names[0][1].casefold() != name.casefold():
        lang, value = names[0]
        queries["language"] = (value, value, {})
        if expanded and row["market_count"] >= 2:
            queries["variant"] = (f'查 {row["domain"]} 的 {value}', value, {"domain": row["domain"]})
    if expanded and name:
        color = field(data, "color").strip()
        material = field(data, "material").strip()
        if color and material and 12 <= len(title) <= 150 and len(color) + len(material) <= 70:
            desc = re.split(r",| - | \(", title, maxsplit=1)[0].strip()
            queries["compound"] = (f"找 {desc}，颜色 {color}，材质 {material}", desc,
                                   {"product_type": row["product_type"]})
    return queries


def make_cases(data=DATA, quotas=QUOTAS):
    expanded = data == EXPANDED_DATA
    rows = []
    with closing(connect()) as db:
        # Use one scoped database source; source record and hash remain available for review.
        cat_sizes = dict(db.execute("SELECT product_type,count(*) FROM products GROUP BY product_type"))
        skus = dict(db.execute("SELECT sku,count(DISTINCT domain) FROM products GROUP BY sku HAVING count(DISTINCT domain)>1"))
        collations = db.execute("SELECT id,listing_key,sku,domain,title,brand,product_type,family_key FROM products")
        ids = sorted((dict(r) for r in collations),
                     key=lambda x: sha256((data.name.removeprefix("abo-") + "|" + x["listing_key"]).encode()).digest())
        used = prior_families(expanded)
        query_texts = set()
        for split, targets in quotas.items():
            counts = Counter()
            by_category = Counter()
            for summary in ids:
                family = summary["family_key"]
                if family in used:
                    continue
                row = summary | dict(db.execute("SELECT raw_json,raw_sha256 FROM products WHERE id=?",
                                                (summary["id"],)).fetchone())
                row["market_count"] = skus.get(row["sku"],1)
                available = facts(row, cat_sizes, expanded)
                if "model" in available and counts["missing"] < targets["missing"] and row["id"] % 3 == 0:
                    token = "ZQNV" + sha256(row["listing_key"].encode()).hexdigest()[:12].upper()
                    match = db.execute("SELECT count(*) FROM products_fts WHERE products_fts MATCH ?", ('"'+token+'"',)).fetchone()[0]
                    if match:
                        raise ValueError("Synthetic missing model appears in corpus")
                    available["missing"] = (f'{row["brand"]} {token}，请找这个完整型号', f'{row["brand"]} {token}',
                                             {})
                order = ("missing", "variant", "language", "tail", "compound", "model", "natural")
                picked = next((kind for kind in order
                               if kind in available and counts[kind] < targets.get(kind, 0)
                               and by_category[kind, row["product_type"]] < (max(7, ceil(targets[kind] / 20))
                                   if expanded else (7 if kind == "variant" else 4))
                               and available[kind][0].casefold() not in query_texts), None)
                if not picked:
                    continue
                question, _, filters = available[picked]
                counts[picked] += 1
                by_category[picked, row["product_type"]] += 1
                used.add(family)
                query_texts.add(question.casefold())
                prefix = "B1500" if expanded else "B300"
                rows.append({"question_id": f"{prefix}-{split}-{picked}-{counts[picked]:03d}", "question": question,
                             "split": split, "suite": picked, "reference_family": family,
                             "reference_answer": row["listing_key"] if picked != "missing" else "NO_MATCH",
                             "reference_pages": [row["listing_key"]] if picked != "missing" else [],
                             "product_type": row["product_type"], "source_listing": row["listing_key"],
                             "source_sha256": row["raw_sha256"], "filters": filters,
                             "formulation": "source-derived controlled query" if picked != "missing" else "synthetic absent model"})
                if dict(counts) == targets:
                    break
            if dict(counts) != targets:
                raise ValueError(f"Incomplete strata: {split} {counts}")
    return rows


def freeze(data=DATA, quotas=QUOTAS):
    if data.exists():
        raise FileExistsError(data)
    rows = make_cases(data, quotas)
    data.mkdir(parents=True)
    questions = data / "queries.jsonl"
    write_jsonl(questions, rows)
    write_json(data / "protocol.json", {"version": data.name, "questions_sha256": file_hash(questions),
                                        "catalog_sha256": CATALOG_SHA, "quotas": quotas, "modes": MODES,
                                        "scope": "Known-item, family-disjoint, semi-synthetic; pooled source-identity relevance. No collection-wide recall or blind human labels."})


def load(data=DATA, quotas=QUOTAS):
    path = data / "queries.jsonl"
    config = json.loads((data / "protocol.json").read_text("utf-8"))
    if config["questions_sha256"] != file_hash(path) or config["catalog_sha256"] != CATALOG_SHA:
        raise ValueError("Frozen query or catalog digest changed")
    rows = read_jsonl(path)
    if (Counter((r["split"], r["suite"]) for r in rows)
        != Counter({(split, k): v for split, targets in quotas.items() for k, v in targets.items()})
        or len({r["reference_family"] for r in rows}) != len(rows)):
        raise ValueError("Incomplete or leaking frozen split")
    return rows, config


def pair_metrics(rows, quotas=QUOTAS):
    out = {}
    for mode in MODES:
        modes = [r for r in rows if r["mode"] == mode]
        for split in ("dev", "test"):
            subset = [r for r in modes if r["split"] == split]
            if not subset:
                continue
            modeset = out.setdefault(split, {})
            stats = {}
            for stratum in ("all", *quotas[split]):
                group = [r for r in subset if stratum in ("all", r["suite"])]
                positive = [r for r in group if r["suite"] != "missing"]
                negative = [r for r in group if r["suite"] == "missing"]
                stats[stratum] = {"queries":len(group),"answerable":len(positive),
                    "exact_hit5":sum(r["metrics"]["exact_hit5"] for r in positive),
                    "family_hit5":sum(r["metrics"]["family_hit5"] for r in positive),
                    "mrr5": sum(r["metrics"]["reciprocal_rank5"] for r in positive) / len(positive) if positive else None,
                    "pooled_ndcg5": sum(r["metrics"]["pooled_ndcg5"] for r in positive) / len(positive) if positive else None,
                    "p95_seconds": sorted(r["retrieval_seconds"] for r in group)[ceil(.95 * len(group)) - 1],
                    "pooled_recall5": (sum(r["metrics"]["pooled_recall5"] for r in positive)
                                       / len(positive) if positive else None),
                    "missing":len(negative),"missing_has_candidates":sum(r["metrics"]["missing_has_candidates"] for r in negative)}
            modeset[mode] = stats
    return out


def evaluate(out, split, data=DATA, quotas=QUOTAS):
    rows, protocol = load(data, quotas)
    out.mkdir(parents=True, exist_ok=False)
    source = snapshot(out)
    config = out / "config.json"
    write_json(config, {"protocol": protocol, "source_sha256": source, "top_k": 5, "split": split})
    config_sha = file_hash(config)
    completed = []
    with closing(FullRetriever()) as retriever, (out / "results.jsonl").open("x", encoding="utf-8") as stream, \
         (out / "pool.jsonl").open("x", encoding="utf-8") as pool_stream:
        for row in rows:
            if split and row["split"] != split:
                continue
            data = {mode: retriever.search(row["question"], mode, 5, row["filters"]) for mode in MODES}
            pool = {item["listing_key"]: item for value in data.values() for item in value["items"]}
            positive = row["suite"] != "missing"
            labels = {key: ("exact" if key == row["reference_answer"] else "family"
                            if positive and item["family_key"] == row["reference_family"] else "nonmatch")
                      for key, item in pool.items()}
            pool_stream.write(json.dumps({"question_id":row["question_id"],"labels":labels},ensure_ascii=False)+"\n")
            pool_stream.flush()
            positives = {key for key, label in labels.items() if label != "nonmatch"}
            grades = {key: {"exact": 2, "family": 1, "nonmatch": 0}[label] for key, label in labels.items()}
            ideal = sum((2**grade - 1) / log2(rank+1)
                        for rank, grade in enumerate(sorted(grades.values(), reverse=True)[:5], 1))
            for mode, prediction in data.items():
                keys = [r["listing_key"] for r in prediction["items"]]
                pages = [{"page_id": r["listing_key"], "rank": r["rank"], "score":r["score"],
                          "text_source":"ABO catalog", "title":r["title"]} for r in prediction["items"]]
                result = {**row, "mode":mode, "retrieved_pages":pages, "citations":[],
                    "routing":{"lane":"retrieval_only","mode":mode}, "model":None, "model_revision":None,
                    "generation_seconds":0.0,"retrieval_seconds":prediction["seconds"],"status":"ok",
                    "run_name":out.name,"timestamp":timestamp(),"config_sha256":config_sha,"source_sha256":source,
                    "metrics":{"exact_hit5":row["reference_answer"] in keys if positive else None,
                               "family_hit5":bool(positives.intersection(keys)) if positive else None,
                               "reciprocal_rank5": 1 / (keys.index(row["reference_answer"]) + 1)
                                   if positive and row["reference_answer"] in keys else 0.0,
                               "pooled_ndcg5": sum((2**grades[key] - 1) / log2(rank+1)
                                   for rank, key in enumerate(keys, 1)) / ideal if ideal else 0.0,
                               "pooled_recall5":len(positives.intersection(keys))/len(positives) if positives else 0.0,
                               "missing_has_candidates":bool(keys) if not positive else None}}
                stream.write(json.dumps(result, ensure_ascii=False)+"\n")
                stream.flush()
                append_question(out, result)
                completed.append(result)
            if len(completed) % 90 == 0:
                print(row["question_id"], len(completed), flush=True)
    metrics = pair_metrics(completed, quotas)
    write_json(out / "metrics.json", metrics)
    report = out / "检索评测报告.txt"
    report.write_text("全库三路同题配对。精确身份命中与关联家族、池内召回分别报告；无匹配题返回任意候选即记有候选。"
                      "标签按目录身份和关联家族复核，语料外型号为生成的对照；自然题为源字段半合成。\n"
                      + json.dumps(metrics, ensure_ascii=False, indent=2), "utf-8")
    operation("B 冻结查询全量检索对照", "全量 BM25/E5/RRF，冻结 dev/test；候选池按源身份和家族标注。",
              f"{len(completed)} 条原始检索，详情见报告。", [report,out/"逐题结果.txt"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("freeze","run"))
    parser.add_argument("--run-name", default="stage-b-retrieval-300")
    parser.add_argument("--split", choices=("dev","test"))
    parser.add_argument("--expanded", action="store_true", help="Use the 300-dev / 1,200-test protocol")
    args = parser.parse_args()
    data, quotas = (EXPANDED_DATA, EXPANDED_QUOTAS) if args.expanded else (DATA, QUOTAS)
    if args.command == "freeze":
        freeze(data, quotas)
    elif re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,100}",args.run_name):
        evaluate(WORKSPACE/"reports/ecommerce_rag"/args.run_name, args.split, data, quotas)
    else:
        parser.error("Invalid run name")


if __name__ == "__main__":
    main()
