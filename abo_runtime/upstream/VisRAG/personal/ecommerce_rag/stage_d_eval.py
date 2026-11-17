"""Freeze source-derived Stage D scenarios, train the intent classifier, then evaluate."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from hashlib import sha256
import json
import re
import statistics

from full_catalog import ROOT, connect
from full_retrieval import FullRetriever
from d_compat_catalog import MODEL_PATTERN, canonical, normalize
from d_compat_route import DemandClassifier, train_router
from stage_d import DATA, VERSION, make_app, save_result
from stage_a import snapshot
from common import WORKSPACE, file_hash, operation, read_jsonl, write_json, write_jsonl


SIZE = {"train":180,"dev":60,"test":180}
INTENT_TEXT = {
    "train": {
        "find": ("想找一个适合 {model} 的{kind}", "帮我挑个 {model} 能配的{kind}", "我打算为 {model} 换个{kind}"),
        "check": ("这个{kind}能给 {model} 用吗", "{model} 跟我这款{kind}适配吗", "帮我核对现有{kind}是否支持 {model}"),
        "other": ("今天想聊聊 {model} 的名字由来", "帮我把 {model} 写进一段文案", "简述 {model} 这个品牌的历史"),
    },
    "dev": {
        "find": ("有无适用于 {model} 的{kind}推荐", "我想买 {model} 的{kind}", "替我搜 {model} 适用的{kind}"),
        "check": ("手头的{kind}在 {model} 能正常用吗", "我现有的{kind}跟 {model} 是不是兼容", "{model} 配这个{kind}行不行"),
        "other": ("解释下 {model} 的命名", "从 {model} 联想一个故事", "为 {model} 写一条广告标题"),
    },
    "test": {
        "find": ("我的设备是 {model}，缺个{kind}，能挑一个吗", "有没有搭 {model} 的{kind}可选", "帮我找 {model} 能装的{kind}"),
        "check": ("拿这个{kind}搭配 {model}，能用吗", "这个{kind}可用于 {model} 吗", "问下 {model} 能不能配这个{kind}"),
        "other": ("我想了解一下 {model} 名字怎么翻译", "请就 {model} 编一篇小故事", "明天做关于 {model} 的分享，帮我起标题"),
    },
}
TITLE_SUPPORT = re.compile(r"\b(?:compatible with|replacement for|mobile cover for|phone case for|protective tpu case for|"
                           r"hard back case for|tempered glass for|case for)\s+(?:apple\s+|samsung\s+|motorola\s+)?")
KIND = {
    "CELLULAR_PHONE_CASE":"手机壳","WIRELESS_ACCESSORY":"保护壳","PORTABLE_ELECTRONIC_DEVICE_COVER":"保护壳",
    "ACCESSORY_OR_PART_OR_SUPPLY":"保护壳","SCREEN_PROTECTOR":"钢化膜","HEADPHONES":"游戏耳机",
    "OFFICE_PRODUCTS":"墨盒","INK_OR_TONER":"墨盒","GROCERY":"咖啡胶囊","COFFEE":"咖啡胶囊",
    "CHARGING_ADAPTER":"充电器","POWER_BANK":"充电器","ELECTRONIC_ADAPTER":"转接线",
    "CARRYING_CASE_OR_BAG":"保护壳","COMPUTER_ADD_ON":"数据线",
}
ABSENT = "QZVX-993827"
FAULTS = ("recover_retrieve","persistent_retrieve","recover_demand","device_error","review_timeout","invalid_verdict")


def digest(text):
    return sha256(text.encode()).hexdigest()


def target_family(model):
    return re.sub(r"\s+(?:plus|pro max|pro|mini|max|edge|ultra|fe|s|play|power|xl|fold|oled|lite|20\d\d).*$","",model)


def choose_fold(family):
    v = int(digest("abo-d-fold-v1|"+family)[:8],16) % 20
    return "train" if v < 8 else "dev" if v < 12 else "test"


def candidates():
    pools = defaultdict(list)
    with closing(connect()) as db:
        for row in db.execute("SELECT listing_key,sku,domain,product_type,family_key,title,raw_sha256 FROM products "
                              "WHERE title LIKE '% for %' OR title LIKE '%compatible with%' ORDER BY listing_key"):
            kind = KIND.get(row["product_type"])
            if not kind:
                continue
            title = normalize(row["title"])
            if "not for" in title or "except" in title or "excluding" in title or "incompatible" in title:
                continue
            marker = TITLE_SUPPORT.search(title)
            if not marker:
                continue
            mention = MODEL_PATTERN.match(title,marker.end())
            if not mention:
                continue
            model = canonical(mention[0])
            if model.startswith(("macbook","p20","hp")) and re.match(r"\s*\d",title[mention.end():]):
                continue
            family = target_family(model)
            pools[choose_fold(family)].append({**dict(row),"model":model,"device_family":family,"kind":kind})
    return pools


def build_rows():
    subsets = candidates()
    all_rows = []
    seen_products = set()
    for split, count in SIZE.items():
        order = sorted(subsets[split],key=lambda row:digest("d-rows-v1|"+row["listing_key"]))
        selected = []
        by_hardware = Counter()
        unique_models = set()
        # First give each device and product type a chance; then add additional families.
        for cap in (1, 4, 12):
            for case in order:
                if len(selected) == count:
                    break
                if case["family_key"] in seen_products:
                    continue
                if by_hardware[case["device_family"]] >= cap:
                    continue
                selected.append(case)
                by_hardware[case["device_family"]] += 1
                seen_products.add(case["family_key"])
                unique_models.add(case["model"])
        if len(selected) != count:
            raise ValueError(f"{split} only {len(selected)} independent products")
        for idx, row in enumerate(selected):
            label = ("find","check","other")[idx%3]
            template = INTENT_TEXT[split][label][(idx//3)%3]
            route_text = template.format(**row)
            model, kind = row["model"],row["kind"]
            quote = row["title"]
            cited = f"ABO:{row['listing_key']}:title"
            # Scenarios deliberately separate direct matches, nearby unreported models, and turns requiring clarification.
            stratum = ("exact","nearby","discovery","followup","ambiguous","other")[idx%6]
            turns = []
            if stratum == "exact":
                turns = [{"text":f"这款 {row['sku']} 能给 {model} 用吗？","decision":"compatible",
                          "reference_pages":[cited],"reference_model":model}]
            elif stratum == "nearby":
                replacement = ABSENT
                head = re.match(r"^(iphone \d+)( plus)?$",model)
                if head:
                    replacement = head[1] if head[2] else head[1]+" plus"
                    # A listing may explicitly name both variants; use a source-absent code for such cases.
                    if replacement.casefold() in quote.casefold().replace(model.casefold()," "):
                        replacement = ABSENT
                model_text = replacement if replacement != ABSENT else f"设备型号是 {ABSENT}"
                turns = [{"text":f"我这款配件 {row['sku']} 能给 {model_text} 用吗？","decision":"unknown",
                          "reference_pages":[],"reference_model":replacement.lower()}]
            elif stratum == "discovery":
                turns = [{"text":f"想找适用于 {model} 的{kind}","decision":"found","reference_pages":[],
                          "reference_model":model}]
            elif stratum == "followup":
                turns = [{"text":"我想找个适用的"+kind,"decision":"clarify_model","reference_pages":[]},
                         {"text":f"型号是 {model}","decision":"found","reference_pages":[],"reference_model":model},
                         {"text":"第一款能配吗？","decision":"compatible","reference_pages":[],"reference_model":model}]
            elif stratum == "ambiguous":
                turns = [{"text":f"这款 {row['sku']} 能给 {model} 或 {ABSENT} 用吗？型号是 {ABSENT}",
                          "decision":"clarify_model","reference_pages":[]}]
                # Two explicitly named and distinct supported-format models keep this a genuine ambiguity.
                extra = "iPhone 8 Plus" if model != "iphone 8 plus" else "iPhone 7"
                turns[0]["text"] = f"这款 {row['sku']} 能给 {model} 或 {extra} 用吗？"
            else:
                turns = [{"text":"我想查订单和实时价格","decision":"out_of_scope","reference_pages":[]}]
            all_rows.append({"question_id":f"D-{split}-{idx+1:03d}","split":split,"suite":stratum,
                             "family_key":row["family_key"],"device_family":row["device_family"],
                             "source_listing":row["listing_key"],"source_sha256":row["raw_sha256"],
                             "source_quote":quote,"product_type":row["product_type"],"site":row["domain"],
                             "intent":label,"text":route_text,"turns":turns})
    return all_rows


def freeze():
    if DATA.exists():
        raise FileExistsError(DATA)
    rows = build_rows()
    # Fit D demand routing only. The accepted 8B/C weights remain the fixed reader.
    model = train_router([r for r in rows if r["split"]=="train"])
    dev = [r for r in rows if r["split"]=="dev"]
    classifier = DemandClassifier(model)
    attempts = []
    for threshold in (.75,.85,.90,.95,.98):
        predictions = [classifier.predict(r["text"]) for r in dev]
        covered = [(row, y) for row, (y,p,n) in zip(dev,predictions) if p>=threshold and n>=2]
        false = sum(y!=r["intent"] for r,y in covered)
        attempts.append({"threshold":threshold,"covered":len(covered),"correct":len(covered)-false,"incorrect":false})
    chosen = max(attempts,key=lambda a:(a["correct"]-3*a["incorrect"],a["threshold"]))
    DATA.mkdir(parents=True)
    case_path = DATA/"cases.jsonl"
    router_path = DATA/"router.json"
    write_jsonl(case_path,rows)
    write_json(router_path,{"model":model,"threshold":chosen["threshold"],"dev_selection":attempts})
    write_json(DATA/"protocol.json",{"version":VERSION,"counts":SIZE,"case_sha256":file_hash(case_path),
              "router_sha256":file_hash(router_path),"model":"character bigram/trigram multinomial naive Bayes",
              "scope":"Source-derived compatibility scenario templates; independent product and target device family splits; 24 separately injected faults."})


def load():
    settings = json.loads((DATA/"protocol.json").read_text("utf-8"))
    if settings["case_sha256"] != file_hash(DATA/"cases.jsonl"):
        raise ValueError("Frozen D scenarios changed")
    rows = read_jsonl(DATA/"cases.jsonl")
    if Counter(r["split"] for r in rows) != SIZE:
        raise ValueError("D splits have different sizes")
    for field in ("family_key","device_family"):
        by = {split:{r[field] for r in rows if r["split"]==split} for split in SIZE}
        if sum(map(len,by.values())) != len(set().union(*by.values())):
            raise ValueError(f"Split leakage in {field}")
    return rows,settings


def fault_cases(rows):
    exact = [r for r in rows if r["split"]=="test" and r["suite"]=="exact"][:4]
    cases = []
    for index, variant in enumerate(FAULTS):
        for offset, source in enumerate(exact):
            agent = ("retrieve" if "retrieve" in variant else "demand" if "demand" in variant else
                     "device" if "device" in variant else "review")
            inject = {"agent":agent,"persistent":not variant.startswith("recover"),
                      "kind":"invalid_verdict" if variant=="invalid_verdict" else "timeout"}
            turn = {**source["turns"][0],"inject":inject}
            if not variant.startswith("recover"):
                turn.update(decision="unavailable",reference_pages=[])
            cases.append({**source,"question_id":f"D-fault-{index+1}-{offset+1}",
                          "suite":variant,"turns":[turn]})
    return cases


def score(turn,result):
    model = result.get("device") or {}
    expected = turn["decision"]
    correct = result["decision"] == expected
    model_correct = model.get("model") == turn["reference_model"] if "reference_model" in turn else None
    cited = result["citations"]
    expected_sources = turn["reference_pages"]
    citation_correct = bool(cited) and (not expected_sources or
                       any(source == cit or source.rsplit(":",1)[0]+":" in cit for source in expected_sources for cit in cited)) \
                       if expected in ("compatible","incompatible","found") else None
    injected = turn.get("inject")
    diagnosed = any(e["agent"]==injected["agent"] for e in result["errors"]) if injected else None
    error_code = None
    if not correct:
        error_code = ("demand" if result["decision"]=="out_of_scope" else "device" if model_correct is False else
                      result.get("failure_stage") or ("retrieval" if not result["retrieved_pages"] else "review"))
    elif citation_correct is False:
        error_code = "citation"
    elif model_correct is False and not injected:
        error_code = "device"
    strict = correct and citation_correct is not False and (model_correct is not False or bool(injected))
    return {"strict_pass":strict,"decision_ok":correct,"model_ok":model_correct,"citation_ok":citation_correct,
            "wrong_supported_claim":expected in ("unknown","unavailable","clarify_model","clarify_accessory") and
                                     result["decision"] in ("compatible","found","incompatible"),
            "diagnosed_injection":diagnosed,"first_observed_error":error_code}


def run(out,split,gpu=False):
    frozen, protocol = load()
    rows = [r for r in frozen if r["split"]==split]
    out.mkdir(parents=True)
    sha = snapshot(out)
    config = out/"config.json"
    write_json(config,{"split":split,"dataset":protocol,"code":sha,"gpu":gpu})
    csha = file_hash(config)
    summaries = {}
    paired = {}
    with closing(FullRetriever()) as retriever:
        for mode in ("single","multi"):
            app = make_app(retriever,DATA,mode,gpu)
            captures = []
            scenarios = []
            for row in rows + (fault_cases(frozen) if split=="test" else []):
                previous = None
                decisions = []
                for idx, turn in enumerate(row["turns"],1):
                    value = app.respond(turn["text"],previous,site=row["site"],inject=turn.get("inject"))
                    previous = value["state"]
                    metrics = score(turn,value)
                    gold = turn | {"question_id":row["question_id"]+f"-{idx:02d}-{mode}"}
                    saved = save_result(out,{**value,"suite":row["suite"],"variant":mode,"split":split},
                                        turn["text"],idx,csha,sha,gold=gold,metrics=metrics)
                    captures.append(saved)
                    decisions.append(saved["metrics"]["strict_pass"])
                scenarios.append((row["question_id"],row["suite"],all(decisions)))
            primary = [r for r in captures if "-fault-" not in r["question_id"]]
            faults = [r for r in captures if "-fault-" in r["question_id"]]
            summaries[mode] = {"scenarios":len(rows),"turns":len(primary),
                "strict_scenarios":sum(ok for qid,_,ok in scenarios if "-fault-" not in qid),
                "strict_turns":sum(r["metrics"]["strict_pass"] for r in primary),
                "wrong_supported_claims":sum(r["metrics"]["wrong_supported_claim"] for r in primary+faults),
                "faults":len(faults),"faults_attributed":sum(bool(r["metrics"]["diagnosed_injection"]) for r in faults),
                "faults_expected_decision_pass":sum(r["metrics"]["decision_ok"] for r in faults),
                "p50_seconds":statistics.median(r["total_seconds"] for r in primary),
                "by_scenario":{k:{"total":sum(s==k for _,s,_ in scenarios),
                                  "passed":sum(ok and s==k for _,s,ok in scenarios)} for k in sorted({s for _,s,_ in scenarios})},
                "first_observed_error":dict(Counter(r["metrics"]["first_observed_error"] for r in primary
                                                   if r["metrics"]["first_observed_error"]))}
            paired[mode] = {qid:ok for qid,_,ok in scenarios}
            if app.reader and app.reader.reader:
                app.reader.unload()
    matcher = DemandClassifier(json.loads((DATA/"router.json").read_text("utf-8"))["model"])
    confusion = Counter((r["intent"],matcher.predict(r["text"])[0]) for r in rows)
    changes = {q:{"single":paired["single"][q],"multi":paired["multi"][q]} for q in paired["multi"]
               if paired["multi"][q]!=paired["single"][q]}
    result = {"split":split,"product_types":len({r["product_type"] for r in rows}),
              "device_families":len({r["device_family"] for r in rows}),"modes":summaries,
              "route_confusion":{"/".join(y):n for y,n in confusion.items()},"changes":changes}
    write_json(out/"metrics.json",result)
    report = out/"D阶段协作评测.txt"
    report.write_text("受控源文案样本。单路对照仅原始标题及首轮检索；多 Agent 可核对条目文案和有限补检。"
                      "独立故障注入和正常场景分开统计。报告同时给出模板分类混淆矩阵。\n"
                      + json.dumps(result,ensure_ascii=False,indent=2),"utf-8")
    operation("D 多 Agent 分组验收","冻结型号与配件家族隔离，单路和协作同题，故障注入单列。",
              f"{split} {len(rows)} 场景，详见结果。",[report,out/"逐题结果.txt"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command",choices=("freeze","dev","test"))
    parser.add_argument("--name",default="stage-d-eval")
    parser.add_argument("--gpu",action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9-]{0,80}",args.name):
        parser.error("Invalid result directory name")
    if args.command=="freeze":
        freeze()
    else:
        run(WORKSPACE/"reports/ecommerce_rag"/args.name,args.command,args.gpu)


if __name__ == "__main__":
    main()
