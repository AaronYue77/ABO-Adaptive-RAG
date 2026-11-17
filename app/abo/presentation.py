"""Compose readable replies from verified catalogue evidence and source links."""

from html.parser import HTMLParser
import re


KEY = re.compile(r'(?<![A-Z0-9])(?:B[0-9][A-Z0-9]{8}|[0-9]{9}[0-9X])@[a-z0-9./-]+',re.I)
IDENTITY_QUESTION = re.compile(r'编号是什么|货号是什么|商品编码是什么|(?:给我|告诉我|列出|显示).{0,10}(?:编号|ASIN|SKU)|\bwhat.{0,20}(?:asin|sku)\b',re.I)
FIELD_LABELS = {"brand":"品牌","color":"颜色","material":"材质","model_number":"型号",
                "width":"宽度","height":"高度","length":"长度","weight":"重量"}


class SourceText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]

    def handle_data(self, data):
        self.parts.append(data)


def readable_text(value):
    text = str(value).strip()
    if re.search(r"</?[A-Za-z][^>]*>",text):
        parser=SourceText()
        parser.feed(text)
        text=" ".join(parser.parts)
    return " ".join(text.split())


def item_names(row, retriever):
    names = {}
    for item in row.get("catalog_items",[])+row.get("matches",[]):
        key=item.get("listing_key") or item.get("listing")
        if key and item.get("title"):
            names[key]=readable_text(item["title"])
    keys = list(row.get("current_listing_keys_after",[]))
    keys.extend(claim["listing_key"] for claim in row.get("claims",[]) if claim.get("listing_key"))
    keys.extend(match.group().rstrip(".") for match in KEY.finditer(row.get("answer","")))
    for key in dict.fromkeys(keys):
        if key not in names and "@" in key:
            sku,domain=key.split("@",1)
            source=retriever.get(sku,domain)
            if source:
                names[key]=readable_text(source[0]["title"])
    count={}
    for title in names.values():
        count[title]=count.get(title,0)+1
    return {key: (f"{title}（{key.split('@',1)[1]} 站）" if count[title]>1 else title)
            for key,title in names.items()}


def readable_sources(row):
    sources=[]
    pages={page["page_id"]:page for page in row.get("retrieved_pages",[]) if page.get("page_id")}
    for citation in row.get("citations",[]):
        match=KEY.search(citation)
        if citation == 'abo:catalog:count':
            label='ABO 目录统计'
        elif match:
            label=match.group().split("@",1)[1].rstrip(".")+" 商品资料"
        elif "policy" in citation or row.get("conversation_lane")=="policy":
            label="售后政策快照"
        elif pages.get(citation,{}).get("photo_page") or "photo" in citation:
            label="商品原图"
        else:
            label="商品资料页"
        sources.append({"label":label,"citation":citation})
    return sources


def list_products(row, names, show_ids):
    from catalog_labels_zh import CATEGORY_LABELS

    items=row.get("catalog_items",[]) or row.get("matches",[])
    types={item.get("product_type") for item in items}
    category=CATEGORY_LABELS.get(next(iter(types))) if len(types)==1 else None
    first=f"我找到了几款{category}。" if category else "我找到了这些商品。"
    criteria=row.get('retrieval_trace',{}).get('required_source_criteria',{})
    if criteria:
        requested=[label for key,label in (('color','颜色'),('exclude_color','颜色排除'),('material','材质'),
                   ('exclude_material','材质排除'),('style','款式'),('size','尺码')) if key in criteria]
        if requested:
            first=f"我找到了符合{'、'.join(requested)}条件的商品记录。"
    title=first+"回复「第一款」「第二款」这样的序号，就可以继续查那款的资料。"
    if row["decision"]=="found":
        title=row["answer"]
    lines=[title]
    for index,item in enumerate(items,1):
        key=item.get("listing_key") or item.get("listing")
        name=names.get(key) or readable_text(item["title"])
        lines.append(f"{index}. {name}"+(f"（{key}）" if show_ids and key else ""))
    return "\n".join(lines)


def describe_listing(row, names, show_ids):
    from catalog_labels_zh import CATEGORY_LABELS

    claims=row.get("claims",[])
    if not claims:
        return None
    sections={}
    for claim in claims:
        sections.setdefault(claim["listing_key"],[]).append(claim)
    parts=[]
    for key, items in sections.items():
        title=names.get(key,"这款商品")
        if show_ids:
            title+=f"（{key}）"
        plain=[]
        for claim in items:
            if claim["source_field"] in {"bullet_point","product_description"}:
                continue
            value=readable_text(claim["value"])
            if claim["source_field"]=="product_type":
                value=CATEGORY_LABELS.get(value,value)
            plain.append((claim["label"],value))
        notes=[readable_text(claim["value"]) for claim in items
               if claim["source_field"] in {"bullet_point","product_description"}]
        text=f"这款是 {title}。"
        if plain:
            if len(plain)<=6:
                text+="它的"+ "，".join(f"{name}是 {value}" for name,value in plain)+"。"
            else:
                text+="它的资料里写明"+ "，".join(f"{name}为 {value}" for name,value in plain)+"。"
        if notes:
            text+="\n\n商品原文还写了这些要点：\n"+"\n".join(f"- {value}" for value in notes)
        parts.append(text)
    answer="\n\n".join(parts)
    if "还有未展示" in row["answer"]:
        answer+="\n\n如果想看这件商品的全部资料，可以说「完整展示」。"
    return answer


def present(row, retriever):
    """Keep the original evidence answer and offer a plain customer-facing version."""
    question=row.get("question","")
    show_ids=bool(IDENTITY_QUESTION.search(question) or
                  row.get("routing",{}).get("reason")=="selected_listing_identity")
    answer=row["answer"]
    action=row.get("decision") or row.get("action")
    names=item_names(row,retriever) if (KEY.search(answer) or row.get("catalog_items") or row.get("matches")
                                        or row.get("claims")) else {}
    if action in {"search_results","found"} and (row.get("catalog_items") or row.get("matches")):
        answer=list_products({**row,"decision":action},names,show_ids)
    elif action=="selected" and row.get("current_listing_keys_after"):
        key=row["current_listing_keys_after"][0]
        answer=f"好，接下来就看 {names.get(key,'这款商品')}。你可以继续问它的规格、外观或其他资料。"
        if show_ids:
            answer+=f" 它的编号是 {key}。"
    elif row.get("answer_source")=="verified_source_fields_and_notes":
        answer=describe_listing(row,names,show_ids) or answer
    elif action=="requires_tool" and row.get("routing",{}).get("reason")=="no_verified_purchase_prices":
        if re.search(r'更贵|更便宜|哪.*贵|cheaper|expensive',question,re.I):
            answer="ABO 没有记录这次比价需要的报价，所以我无法判断哪款更贵。你可以继续问我两款在规格或包装上的区别。"
        else:
            answer="我可以帮你找商品和规格，不过现有 ABO 资料没有可核实的售价或折扣。"
    if not show_ids:
        answer=KEY.sub(lambda match: names.get(match.group().rstrip("."),"这条商品记录"),answer)
    if action=="catalog":
        answer=re.sub(r'（[A-Z][A-Z0-9_]+）','',answer)
    return {"display_answer":answer.strip(),"display_sources":readable_sources(row)}
