"""Bounded multi-agent accessory compatibility over the full historical ABO catalog."""
from collections import OrderedDict
from dataclasses import asdict, dataclass
import json
import re
import sqlite3
import time

from full_app import EvidenceReader
from full_conversation import FullConversation
from full_retrieval import exact_skus
from qwen8_runtime import MODEL_ID, REVISION
from c8_reader import ReaderAdapter
from d_compat_catalog import (ACCESSORIES, CatalogTool, accessory_kind, classify, contains_kind, fields,
                              normalize, resolve_models)
from d_compat_route import BOTH, FIND, DemandClassifier, intent, read_proposal
from common import WORKSPACE, file_hash, timestamp
from audit import append_result


VERSION = "abo-accessory-agent-v1"
DATA = WORKSPACE / "data/ecommerce_rag/abo-stage-d-v1"
STAGES = ("demand", "device", "retrieve", "review", "visual")
SAFE_ERRORS = (ValueError, TypeError, TimeoutError, sqlite3.Error, OSError, RuntimeError, KeyError)
DOMAINS = {"美国站": "amazon.com", "印度站": "amazon.in", "西班牙站": "amazon.es",
           "加拿大站": "amazon.ca", "英国站": "amazon.co.uk", "日本站": "amazon.co.jp",
           "德国站": "amazon.de", "法国站": "amazon.fr", "墨西哥站": "amazon.com.mx"}
FOLLOW = re.compile(r"这款|那个|刚才|上一款|上一个|第[一二三123]款|\b(?:this one|that one)\b", re.I)
RESET = re.compile(r"重新开始|清空|换个话题|^重置[。！!]?$")
OUTSIDE = re.compile(r"给我退款|退货|查订单|查物流|实时价格|现在(?:还有|有)货|帮我下单")
TURN_BUDGET = 50.0
PATTERN_REQUEST = re.compile(r"有图案|带图案|印花|花纹|patterned|printed", re.I)
PATTERN_SOURCE = re.compile(r"\b(?:designer|printed|pattern|floral|flower)\b|印花|图案", re.I)


@dataclass(frozen=True)
class Request:
    mode: str
    model: str
    device_origin: str
    listing_sku: str
    domain: str
    kind: str
    alternatives: bool
    photo: bool
    patterned: bool = False


class Stopped(Exception):
    def __init__(self, stage, reason):
        self.stage = stage
        self.reason = reason


class DemandAgent:
    def __init__(self, classifier, conversation=None, threshold=.88):
        self.classifier = classifier
        self.threshold = threshold
        self.conversation = conversation

    def task(self, text, previous, sku, kind, trace, *, use_model=True):
        proposal = ""
        mode, reason, confidence = intent(text, has_target=bool(sku), accessory=kind,
                                          pending=previous, classifier=self.classifier, threshold=self.threshold)
        if self.conversation and use_model and reason == "uncertain":
            model = read_proposal(self.conversation, text, trace)
            mode, proposal, reason = model["intent"], model["device_span"], "8b_tool_proposal"
        return {"mode": mode, "proposal": proposal, "reason": reason, "confidence": confidence}


class DeviceAgent:
    def resolve(self, text, proposal, previous):
        options, origin = resolve_models(text)
        if not options and proposal:
            options, origin = resolve_models("型号是 " + proposal)
        if not options and previous and previous["origin"] == "informal_variant" and re.match(r"^(?:是的|确认|对，就是|对的|没错|yes)\b|^(?:是|对)[。！!]?$", text, re.I):
            return {"model": previous["model"], "origin": "confirmed_alias", "choices": []}
        if not options and previous and (FOLLOW.search(text) or FIND.search(text)) and previous["model"]:
            return {"model": previous["model"], "origin": previous["origin"], "choices": []}
        return {"model": options[0] if len(options) == 1 else "", "origin": origin,
                "choices": options if len(options) != 1 else []}


class RetrievalAgent:
    def __init__(self, tools):
        self.tools = tools

    def get(self, request, broaden=False):
        if request.mode == "check":
            return self.tools.exact(request.listing_sku, request.domain)
        return self.tools.search(request.model, request.kind, request.domain, broaden=broaden)


def verify_rows(request, rows):
    keys = set()
    for row in rows:
        if row["listing_key"] != row["sku"]+"@"+row["domain"] or row["listing_key"] in keys:
            raise ValueError("Invalid returned listing identity")
        if request.mode == "check" and row["sku"] != request.listing_sku:
            raise ValueError("Wrong requested listing")
        if request.domain and row["domain"] != request.domain:
            raise ValueError("Wrong returned marketplace")
        keys.add(row["listing_key"])
    return rows


def verify_review(request, rows, review):
    status, matches, evidence, retry = review
    if status not in {"compatible","incompatible","conflict","clarify_site","found","unknown","no_listing"}:
        raise ValueError("Invalid compatibility status")
    if status == "found" and not matches:
        raise ValueError("A found decision needs grounded matches")
    returned = {row["listing_key"]:row for row in rows}
    for match in matches:
        row = returned[match["listing"]]
        claims = [asdict(q) for q in fields(row) if classify(q,request.model) == {"compatible"}]
        if not match["citations"] or not all(c in claims for c in match["citations"]):
            raise ValueError("Invalid positive recommendation citations")
    if status in ("compatible","incompatible"):
        if len(rows) != 1 or not evidence:
            raise ValueError("Unbound compatibility verdict")
        valid = fields(rows[0])
        if any(quote not in valid for quote in evidence):
            raise ValueError("Fabricated citation")
        polarities = set().union(*(classify(quote,request.model) for quote in valid))
        if polarities != {status} or not any(status in classify(quote,request.model) for quote in evidence):
            raise ValueError("Unsubstantiated compatibility verdict")
    return status,matches,evidence,retry


class ReviewAgent:
    def inspect(self, request, rows, *, single=False):
        if request.mode == "check" and len(rows) > 1:
            return "clarify_site", [], [], False
        observed = []
        accepted = []
        statuses = []
        for row in rows:
            if request.mode == "find" and (not contains_kind(row,request.kind)
                                             or (request.patterned and not PATTERN_SOURCE.search(row["title"]))):
                continue
            citations = fields(row)
            if single:
                citations = citations[:1]
            usable = []
            polarity = set()
            for quote in citations:
                hit = classify(quote, request.model)
                if hit:
                    polarity.update(hit)
                    usable.append(quote)
            if request.mode == "check":
                observed = usable or citations[:1]
            if polarity == {"compatible"}:
                accepted.append({"listing": row["listing_key"], "title": row["title"],
                                 "citations": [asdict(q) for q in usable[:2]]})
                statuses.append("compatible")
            elif polarity == {"incompatible"}:
                statuses.append("incompatible")
            elif len(polarity) > 1:
                statuses.append("conflict")
        if request.mode == "find":
            return ("found" if accepted else "unknown"), accepted[:5], [], not accepted
        if statuses:
            return statuses[0], [], observed[:4], False
        return ("unknown" if rows else "no_listing"), [], observed[:2], False


class StageD:
    def __init__(self, retriever, *, classifier=None, threshold=.88, conversation=None, reader=None, mode="multi"):
        self.catalog = retriever
        self.tools = CatalogTool(retriever)
        self.demand = DemandAgent(classifier, conversation, threshold)
        self.devices = DeviceAgent()
        self.retrieval = RetrievalAgent(self.tools)
        self.review = ReviewAgent()
        self.conversation = conversation
        self.reader = reader
        self.mode = mode

    def respond(self, text, previous=None, *, site="", inject=None):
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 1600:
            raise ValueError("Question must have 1–1600 characters")
        if site and site not in self.catalog.manifest["domains"]:
            raise ValueError("Site must be in the ABO catalog")
        text = text.strip()
        trace, model_calls, errors = [], [], []
        started = time.perf_counter()
        searched = OrderedDict()
        turn = {"demand": None, "device": None, "request": None, "evidence": [], "matches": [],
                "trace":trace, "model_calls":model_calls,"errors":errors,"visual_observation":None}

        def call(agent, func, *, fallback=None, inputs=None, validate=None):
            attempts = 2 if fallback and self.mode == "multi" else 1
            for attempt in range(attempts):
                begin = time.perf_counter()
                record = {"agent":agent, "attempt":attempt + 1, "status":"ok", "inputs":inputs or {}}
                trace.append(record)
                try:
                    if begin - started >= TURN_BUDGET:
                        raise TimeoutError("turn_budget")
                    faulty = inject and inject["agent"] == agent and (attempt == 0 or inject["persistent"])
                    if faulty and inject.get("kind","timeout") == "timeout":
                        raise TimeoutError("injected_timeout")
                    result = (func if attempt == 0 else fallback)()
                    if faulty and inject.get("kind") == "invalid_verdict":
                        result = ("compatible",[],[],False)
                    if validate:
                        result = validate(result)
                    if time.perf_counter() - started > TURN_BUDGET:
                        raise TimeoutError("turn_budget")
                    return result
                except SAFE_ERRORS as error:
                    reason = str(error) if str(error) in ("turn_budget","injected_timeout") else type(error).__name__
                    record.update(status="error", reason=reason)
                    errors.append({"agent":agent,"reason":reason})
                finally:
                    record["seconds"] = round(time.perf_counter() - begin, 6)
            raise Stopped(agent, errors[-1]["reason"])

        def finish(status, answer, state=None):
            citations = list(dict.fromkeys([e["citation"] for e in turn["evidence"]]
                       + [e["citation"] for m in turn["matches"] for e in m["citations"]]))
            return {**turn, "decision":status, "answer":answer, "state":state,
                    "retrieved_pages":list(searched.values()), "citations":citations,
                    "retrieval_seconds":sum(r["seconds"] for r in trace if r["agent"] == "retrieve"),
                    "generation_seconds":sum(r.get("generation_seconds",0.0) for r in model_calls),
                    "total_seconds":round(time.perf_counter()-started,6)}

        if RESET.search(text):
            return finish("reset", "已清空本次会话中的设备和配件信息。")
        if OUTSIDE.search(text):
            return finish("out_of_scope", "配件助手可核对商品资料。订单、退款和实时库存不在这个查询范围。")

        domain_hits = {domain for label, domain in DOMAINS.items() if label in text}
        domain_hits.update(d for d in re.findall(r"(?i)(?:primenow\.)?amazon\.[a-z]+(?:\.[a-z]+)?", text.lower())
                           if d in self.catalog.manifest["domains"])
        if len(domain_hits) > 1 or (site and domain_hits and site not in domain_hits):
            return finish("clarify_site", "你提到了不同站点。要查哪一个站点？")
        skus = exact_skus(text)
        if len(skus) > 1:
            return finish("clarify_accessory", "这次提到了多个商品编号。请先指定要核对的一个配件。")
        kind = accessory_kind(text)
        carry = previous if previous and (FOLLOW.search(text) or not (kind and FIND.search(text))) else None
        sku = skus[0] if skus else (carry.get("sku","") if carry and not FIND.search(text) else "")
        selected_site = ""
        ordinal = re.search(r"第([一二三四五12345])款",text)
        if not skus and carry and ordinal:
            index = "一二三四五".find(ordinal[1]) if not ordinal[1].isdigit() else int(ordinal[1])-1
            if index >= len(carry.get("candidates",[])):
                return finish("clarify_accessory","这个序号没有对应的候选。请先搜索配件。",previous)
            sku, selected_site = carry["candidates"][index].split("@",1)
        domain = site or next(iter(domain_hits), "") or selected_site or (carry.get("domain","") if carry else "")
        patterned = bool(PATTERN_REQUEST.search(text) or (carry and carry.get("patterned")))
        kind = kind or (carry.get("kind","") if carry else "")
        try:
            task = call("demand", lambda: self.demand.task(text, carry, sku, kind, model_calls),
                         fallback=lambda: self.demand.task(text, carry, sku, kind, model_calls, use_model=False),
                         inputs={"candidate_provided":bool(sku), "accessory_kind":kind})
            turn["demand"] = task
            if task["mode"] == "other":
                return finish("out_of_scope", "你可以描述设备和要找的配件，或者让我核对一款配件是否适用。", previous)
            device = call("device", lambda: self.devices.resolve(text, task["proposal"], carry),
                          inputs={"demand_reason":task["reason"]})
            turn["device"] = device
            state = {"mode":task["mode"],"model":device["model"],"origin":device["origin"],"sku":sku,"domain":domain,"kind":kind,"patterned":patterned}
            if carry and carry.get("candidates"):
                state["candidates"] = carry["candidates"]
            if not device["model"]:
                prompt = ("你提到了多个型号，请问要核对哪一个：" + "、".join(device["choices"]) + "？") if device["choices"] \
                    else "你的设备具体是哪一款？日常叫法或设置中显示的机型名称都可以。"
                return finish("clarify_model", prompt, state)
            if device["origin"] == "informal_variant":
                return finish("clarify_model", f"你指的是 {device['model']} 吗？确认后我再核对配件。", state)
            if not sku and task["mode"] == "check":
                if BOTH.search(text) or FIND.search(text):
                    task["mode"] = "find"
                    state["mode"] = "find"
                else:
                    return finish("clarify_accessory", "请告诉我配件商品编号，或先让我帮你找明确支持的配件。", state)
            request = Request(task["mode"],device["model"],device["origin"],sku,domain,kind,
                              bool(BOTH.search(text)),bool(re.search(r"看图|包装上|看图片|from the (?:photo|image)", text, re.I)),patterned)
            if request.kind and request.kind not in ACCESSORIES:
                raise ValueError("Unsupported accessory kind")
            turn["request"] = asdict(request)
            rows = call("retrieve", lambda: self.retrieval.get(request), fallback=lambda: self.retrieval.get(request),
                        inputs={"tool":"exact_listing" if request.mode=="check" else "search_model_phrase",
                                "model":request.model,"sku":sku,"marketplace":domain,"kind":kind},
                        validate=lambda value:verify_rows(request,value))

            def remember(values):
                for row in values:
                    key = row["listing_key"]
                    if key not in searched:
                        searched[key] = {"page_id":key,"rank":len(searched)+1,"score":float(row.get("score",1)),
                                         "text_source":"ABO catalog", "title":row["title"]}
            remember(rows)
            if request.mode == "check" and len(rows) == 1:
                state["domain"] = rows[0]["domain"]
            decision, matches, evidence, retry = call(
                "review", lambda:self.review.inspect(request,rows,single=self.mode=="single"),
                inputs={"candidates":len(rows),"model":request.model},
                validate=lambda value:verify_review(request,rows,value))
            if retry and self.mode == "multi":
                more = call("retrieve", lambda:self.retrieval.get(request,broaden=True),
                            inputs={"tool":"broaden_bm25","model":request.model,"marketplace":domain},
                            validate=lambda value:verify_rows(request,value))
                remember(more)
                decision,matches,evidence,_ = call("review",lambda:self.review.inspect(request,more),
                                                  inputs={"candidates":len(more),"retry":True},
                                                  validate=lambda value:verify_review(request,more,value))
            if request.alternatives and request.mode=="check" and decision in ("unknown","incompatible","no_listing"):
                other = Request("find",request.model,request.device_origin,"",domain,kind,False,False,patterned)
                more = call("retrieve",lambda:self.retrieval.get(other),inputs={"tool":"find_alternative","model":request.model},validate=lambda value:verify_rows(other,value))
                remember(more)
                _, matches, _, _ = call("review",lambda:self.review.inspect(other,more),
                                        inputs={"candidates":len(more),"alternative":True},
                                        validate=lambda value:verify_review(other,more,value))
            turn["evidence"],turn["matches"] = [asdict(e) for e in evidence],matches
            if matches:
                state["candidates"] = [match["listing"] for match in matches]
            if rows and len(rows)==1 and self.reader and self.conversation and request.mode=="check" \
               and (request.photo or decision == "unknown"):
                try:
                    turn["visual_observation"] = call("visual",lambda:self.photo(rows[0],request.model,model_calls),
                                                      inputs={"listing":rows[0]["listing_key"]})
                except Stopped:
                    pass
            responses = {
                "compatible":f"商品资料明确标注适用于 {request.model}。",
                "incompatible":f"商品资料明确排除 {request.model}。",
                "conflict":"资料中同时出现支持与排除，暂不能确认兼容。",
                "clarify_site":"该商品编号有多个站点版本。请告诉我你要查的站点。",
                "found":f"找到 {len(matches)} 个在商品资料中明确标注适用于 {request.model} 的候选。",
                "unknown":f"现有商品资料不足以确认是否适用于 {request.model}。",
                "no_listing":"在指定范围内没有找到该商品编号。",
            }
            if request.mode=="check" and matches:
                responses[decision] += f" 另找到 {len(matches)} 个有明确型号证据的候选。"
            if request.photo:
                observed = turn["visual_observation"]
                if observed:
                    responses[decision] += " 已另外读取商品原图。图片转录在核验记录中，兼容结论依据所引用的商品原文。"
                elif self.reader:
                    responses[decision] += " 本次无法读取原图，可查看已核对的商品文字。"
            return finish(decision,responses[decision],state)
        except Stopped as error:
            turn["failure_stage"] = error.stage
            return finish("unavailable","本轮核验遇到工具或模型错误，请稍后重试。",previous)

    def photo(self, row, model, trace):
        from full_cards import photo
        from stage_c import PROMPT_C1

        path, meta = photo(row)
        if path is None:
            raise OSError("ABO source image unavailable")
        runtime = self.reader.ensure_reader().vlm
        self.reader.adapter.mount(runtime)
        instruction = (f"商品的目标设备型号是 {model}。只转录图片中实际看得清的适用型号或排除说明，"
                       "保留文字；看不到时写 NOT_FOUND。商品图只作为补充观察。"+PROMPT_C1)
        messages = [{"role":"user","content":[{"type":"image","image":str(path)},
                                            {"type":"text","text":instruction}]}]
        result = self.conversation.generate(messages,"d_photo",trace,128,use_adapter=True)
        return {"listing":row["listing_key"],"source":meta.get("url"),"sha256":meta.get("sha256"),"adapter_sha256":self.reader.adapter.sha256,
                "model_output":result,"status":"unverified_visual_transcription"}


def classifier_config(path):
    protocol = json.loads((path / "protocol.json").read_text("utf-8"))
    model = path / "router.json"
    if protocol["router_sha256"] != file_hash(model):
        raise ValueError("Router fingerprint changed")
    data = json.loads(model.read_text("utf-8"))
    selected = next((row for row in data["dev_selection"] if row["threshold"] == data["threshold"]), None)
    # A low-quality development classifier stays in offline evaluation; it must not select live tools.
    threshold = data["threshold"] if (selected and selected["covered"] >= 10
                                      and selected["correct"] / selected["covered"] >= .90) else None
    return DemandClassifier(data["model"]), threshold, protocol


def make_app(retriever, data, mode, gpu=False, model_dir=None):
    classifier, threshold, _ = classifier_config(data)
    reader = EvidenceReader(model_dir=model_dir, adapter=ReaderAdapter.accepted()) if gpu else None
    return StageD(retriever,classifier=classifier,threshold=threshold,
                  conversation=FullConversation(reader) if reader else None,reader=reader,mode=mode)


def save_result(out, result, text, index, config_sha, source_sha, *, gold=None, metrics=None):
    pages = result["retrieved_pages"]
    executed = any(call.get("model_executed") for call in result["model_calls"])
    item = {**result,"run_name":out.name, "question_id":gold["question_id"] if gold else f"D-{index:03d}",
            "question":text,"reference_answer":gold["decision"] if gold else "交互未标注",
            "reference_pages":gold.get("reference_pages",[]) if gold else [],"retrieved_pages":pages,
            "model":MODEL_ID if executed else None,"model_revision":REVISION if executed else None,
            "source_sha256":source_sha,"config_sha256":config_sha,"timestamp":timestamp(),
            "routing":{"workflow":"d-accessory","decision":result["decision"]},
            "retrieval_trace":result["trace"],"raw_output":json.dumps(result["model_calls"],ensure_ascii=False),
            "metrics":metrics or {"evaluation":"unlabelled_interaction"},
            "status":"error" if result["decision"]=="unavailable" else "ok"}
    append_result(out,item)
    return item


def main():
    from d_chat import main as run_chat

    run_chat()


if __name__ == "__main__":
    main()
