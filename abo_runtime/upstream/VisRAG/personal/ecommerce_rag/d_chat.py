"""Compose the full B assistant and the D compatibility workflow in one session."""
import argparse
import json
from pathlib import Path
import re
import uuid

from full_app import FullApplication, public_item
from full_catalogue import SEARCH_REQUEST, catalogue_request
from full_conversation import clear_route, direct_business_request, price_request, specific_product_reference
from full_reference import ordinal_choice
from full_retrieval import exact_skus
from d_compat_catalog import accessory_kind, resolve_models
from d_compat_route import FIND
from stage_d import DATA, VERSION, StageD, classifier_config, save_result
from c8_reader import ReaderAdapter
from stage_a import snapshot
from common import WORKSPACE, file_hash, write_json
from audit import append_result


ENTRY_VERSION = "full-catalog-and-compatibility-v5"
COMPAT_RELATION = re.compile(r"兼容|适配|适用|装得上|装不上|能(?:不能|否)?(?:给|装|配|用在)|能用吗"
                             r"|能不能用|合不合适|(?:这个|这款).{0,12}能用|\b(?:compatib\w*|fits?|works? with)\b", re.I)
TARGET_CUE = re.compile(r"给|我的|用在|装在|搭配|适用于|配件|保护套|换个壳|找个壳|\bfor\b", re.I)
DEVICE_WORD = re.compile(r"手机|打印机|笔记本|游戏机|设备|相机|咖啡机|吸尘器|平板|主机")
REPLY_ONLY = re.compile(r"(?:是|对|是的|对的|没错|确认|对就是它|yes|correct)[。！!.\s]*$", re.I)
RESET_WORDS = {"重置","reset","清空"}
EXIT_WORDS = {"退出","exit","quit"}


def compatibility_request(text, *, pending, recent, selected):
    """Use positive signs before switching ordinary conversation to a compatibility workflow."""
    skus = exact_skus(text)
    ordinary = clear_route(text, (), None, has_product_id=bool(skus))
    if ((ordinary and ordinary["intent"] in {"general", "out_of_scope"}) or
        (catalogue_request(text) and not COMPAT_RELATION.search(text)) or direct_business_request(text) or
        price_request(text,bool(skus or selected or recent))):
        return False
    if re.search(r"查(?:一下)?订单|退款|查物流|退货政策|现行|现在.*(?:有货|多少钱)",text):
        return False
    kind = accessory_kind(text)
    models, _ = resolve_models(text)
    has_action = SEARCH_REQUEST.match(text) or FIND.search(text)
    referenced = specific_product_reference(text) or bool(re.search(r"这款|那个|上一款|第.{1,2}款",text))
    if recent and pending and (
        REPLY_ONLY.fullmatch(text) or
        (not has_action and models and len(text)<=42 and not re.search(r"品牌|材质|片段|价格|代码",text)) or
        (skus and not re.search(r"品牌|材质|颜色|规格|重量|参数",text))
    ):
        return True
    if COMPAT_RELATION.search(text):
        if kind or (models and (skus or selected or recent or has_action or TARGET_CUE.search(text))):
            return True
        if recent and referenced:
            return True
    if has_action and (kind or "配件" in text):
        return bool(models or (DEVICE_WORD.search(text) and TARGET_CUE.search(text)) or
                    re.search(r"适用|兼容|给.{0,15}配件",text))
    return False


class StageDChat:
    def __init__(self, output, *, data=DATA, mode="multi", site="", gpu=False, visual=False, model_dir=None, application_sources=()):
        self.site = site
        self.output = output
        adapter = ReaderAdapter.accepted() if gpu else None
        classifier, threshold, protocol = classifier_config(data)
        self.app = FullApplication("B",report_root=output/"catalog_lane",audit=False,
                                   model_dir=model_dir,adapter=adapter,model_available=gpu,native_lookup=True,visual=visual)
        if site and site not in self.app.retriever.manifest["domains"]:
            self.app.close()
            raise ValueError("Site must be an ABO marketplace")
        self.compat = StageD(self.app.retriever,classifier=classifier,threshold=threshold,
                             conversation=self.app.conversation if gpu else None,
                             reader=self.app.reader if gpu else None,mode=mode)
        self.catalog = self.app.session()
        self.device_state = None
        self.pending = False
        self.last_lane = None
        self.turn = 0
        self.source_sha = snapshot(output,extra=application_sources)
        config = output/"config.json"
        from app.abo.dialogue_adapter import configuration as dialogue_configuration
        dialogue_adapter = dialogue_configuration() if gpu else None
        write_json(config,{"entry":ENTRY_VERSION,"compatibility_workflow":VERSION,"mode":mode,
                           "gpu":gpu,"visual_rerank":visual,"catalog_records":self.app.retriever.manifest["records"],
                           "adapter_sha256":adapter.sha256 if adapter else None,
                           "source_sha256":self.source_sha,"compatibility_protocol":protocol,
                           "dialogue_adapter_sha256":dialogue_adapter['adapter_sha256'] if dialogue_adapter else None,
                           "dialogue_planner":"adaptive-multiple-tools-v1" if gpu else "source-lookup",
                           "web_dialogue_contract":"source-identity-filter-release-v3"})
        self.config_sha = file_hash(config)

    def close(self):
        self.app.close()

    def _compat_context(self, text):
        if self.last_lane == "compatibility":
            return self.device_state
        if (not exact_skus(text) and self.catalog.last_lane == "product" and
            COMPAT_RELATION.search(text) and re.search(r"这款|这个|它|所选|刚才",text) and len(self.catalog.selected)==1):
            sku, site = self.catalog.selected[0].split("@",1)
            return {"mode":"check","model":"","origin":"missing_model","sku":sku,"domain":site,"kind":""}
        return None

    def _catalog_request(self,text):
        request = {"domain": self.site} if self.site else {}
        if self.last_lane != "compatibility" or not self.device_state or exact_skus(text):
            return text,request
        state = self.device_state
        chosen = ordinal_choice(text,{"lane":"product"})
        if chosen and state.get("candidates"):
            if chosen.get("ambiguous") or not 0 <= chosen["index"] < len(state["candidates"]):
                self.catalog.candidates = list(state["candidates"])
                self.catalog.candidate_entities = [
                    public_item(self.app.retriever.get(*key.split("@",1))[0]) for key in self.catalog.candidates
                ]
                self.catalog.last_task = {"lane":"product","kind":"search_results"}
            else:
                key = state["candidates"][chosen["index"]]
                sku,site = key.split("@",1)
                return ("选择 "+key if chosen["task"]=="select" else chosen["resolved_text"]),{"skus":[sku],"domain":site}
        if specific_product_reference(text) and state.get("sku") and state.get("domain"):
            request.update(skus=[state["sku"]],domain=state["domain"])
        return text,request

    def respond(self,text,*,adaptive=None,allow_compatibility=True,presenter=None):
        text = text.strip()
        if not 0 < len(text) <= 1600:
            raise ValueError("请输入 1 到 1600 个字符。")
        self.turn += 1
        if text.casefold() in RESET_WORDS:
            self.last_lane,self.device_state,self.pending = None,None,False
        elif allow_compatibility and compatibility_request(text,pending=self.pending,recent=self.last_lane=="compatibility",
                                                            selected=self.catalog.last_lane=="product" and bool(self.catalog.selected)):
            result = self.compat.respond(text,self._compat_context(text),site=self.site)
            if result["decision"] != "out_of_scope":
                self.device_state = result["state"]
                self.pending = result["decision"] in ("clarify_model","clarify_accessory","clarify_site")
                self.last_lane = "compatibility"
                if presenter:
                    result.update(presenter({**result,"question":text}))
                return save_result(self.output,{**result,"entry_lane":self.last_lane,"adaptive":adaptive},text,self.turn,
                                   self.config_sha,self.source_sha)
        effective, request = self._catalog_request(text)
        record = self.catalog.respond(effective,request,return_audit=True,previous_lane=self.last_lane)
        self.pending = False
        self.last_lane = record.get("conversation_lane") or ("catalog" if record["action"]!="reset" else None)
        row = {**record,"entry_lane": self.last_lane,"effective_question":effective,"question":text,"adaptive":adaptive,
               "decision":record["action"],"matches":[],"run_name":self.output.name,
               "run_directory":str(self.output.relative_to(WORKSPACE)),
               "question_id":f"D-{self.turn:03d}","config_sha256":self.config_sha,"source_sha256":self.source_sha}
        if presenter:
            row.update(presenter(row))
        append_result(self.output,row)
        return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command",choices=("chat","ask","check"),nargs="?",default="chat")
    parser.add_argument("--question")
    parser.add_argument("--site",default="")
    parser.add_argument("--gpu",action="store_true",help="Enable 8B small talk and C adapter image reading.")
    parser.add_argument("--model-dir",type=Path)
    parser.add_argument("--mode",choices=("single","multi"),default="multi")
    parser.add_argument("--data",type=Path,default=DATA)
    args = parser.parse_args()
    if args.command=="ask" and not args.question:
        parser.error("--question required for ask")
    if args.command=="check":
        from full_retrieval import FullRetriever

        catalog = FullRetriever()
        try:
            classifier_config(args.data)
            print(json.dumps({"entry":ENTRY_VERSION,"records":catalog.manifest["records"],
                              "categories":len(catalog.manifest["product_types"]),
                              "sites":len(catalog.manifest["domains"]),"gpu":args.gpu,"mode":args.mode}))
        finally:
            catalog.close()
        return
    out = WORKSPACE/"reports/ecommerce_rag"/("stage-d-chat-"+uuid.uuid4().hex[:10])
    out.mkdir(parents=True)
    app = StageDChat(out,data=args.data,mode=args.mode,site=args.site,gpu=args.gpu,model_dir=args.model_dir)
    try:
        while True:
            try:
                text = args.question if args.command=="ask" else input("你：").strip()
            except (KeyboardInterrupt,EOFError):
                break
            if text.casefold() in EXIT_WORDS:
                break
            if not text:
                continue
            try:
                result = app.respond(text)
            except ValueError as error:
                if args.command=="ask":
                    parser.error(str(error))
                print("助手："+str(error))
                continue
            if args.command=="ask":
                print(json.dumps(result,ensure_ascii=False,indent=2))
                break
            print("助手："+result["answer"])
            for match in result.get("matches",[]):
                print("  "+match["listing"]+" "+match["title"])
            for cite in result.get("citations",[]):
                print("  来源 "+cite)
    finally:
        app.close()
    print("记录："+str(out/"逐题结果.txt"))
