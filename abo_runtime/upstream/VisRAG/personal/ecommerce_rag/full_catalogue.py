"""Catalog statistics, public search plans and source-extractive product overviews."""
import json
import re
import unicodedata
from full_cards import evidence_fields,display_values
from catalog_labels_zh import CATEGORY_LABELS
from full_retrieval import EXPANSIONS

# Public language aliases map to actual source types; unrecognized queries still
# use semantic query planning. This table does not limit the searchable catalog.
CATEGORIES=[
 (r'运动鞋|跑鞋|sneakers?|running shoes','shoes sneakers',['SHOES','TECHNICAL_SPORT_SHOE']),
 (r'靴子|短靴|长靴|boots?','boot',['BOOT']),
 (r'凉鞋|sandals?','sandal',['SANDAL']),
 (r'鞋垫|insoles?|shoe inserts?','shoe insert',['SHOE_INSERT']),
 (r'鞋子|鞋类|鞋(?!架|柜|带|刷|油)|footwear|\bshoes\b','shoes',['SHOES','BOOT','SANDAL','TECHNICAL_SPORT_SHOE']),
 (r'手机壳|手机保护套|phone case','phone case',['CELLULAR_PHONE_CASE']),
 (r'钢化膜|屏幕保护膜|screen protectors?','screen protector tempered glass',['SCREEN_PROTECTOR']),
 (r'耳机|headphones?','headphones',['HEADPHONES']),
 (r'双肩包|背包|backpacks?','backpack',['BACKPACK']),
 (r'椅子|chairs?','chair',['CHAIR','BEAN_BAG_CHAIR']),
 (r'沙发|sofas?','sofa',['SOFA']),
 (r'灯泡|light bulbs?','light bulb',['LIGHT_BULB']),
 (r'茶杯|\bteacups?\b|\btea cups?\b','tea cup teacup',['DRINKING_CUP']),
 (r'咖啡杯|马克杯|饮用杯|(?:coffee|drinking) cups?|\bmugs?\b','mug cup',['DRINKING_CUP']),
 (r'咖啡|coffee','coffee',['COFFEE']),
 (r'化妆品|彩妆|\bmakeup\b|\bcosmetics?\b','makeup cosmetics mascara',['FACE_SHAPING_MAKEUP','MASCARA','COSMETIC_POWDER','BEAUTY','ABIS_BEAUTY']),
 (r'地毯|rugs?','rug',['RUG']),
 (r'手提包|handbags?','handbag',['HANDBAG']),
 (r'帽子|hats?','hat',['HAT']),
 (r'桌子|tables?','table',['TABLE']),
]
LABELS={'brand':'品牌','color':'颜色','material':'材质','model_number':'型号','product_type':'原始类别',
        'fabric_type':'面料','style':'款式','size':'尺寸标记','width':'宽度','height':'高度','length':'长度','weight':'重量'}
EXTRA_LABELS={'color_code':'颜色编码','finish_type':'表面处理','item_shape':'形状','pattern':'图案',
              'model_name':'型号名称','model_year':'型号年份','product_description':'商品描述（源文）',
              'item_keywords':'源记录关键词'}


def catalogue_request(text):
    if re.search(r'这(?:个|款|件)商品|该商品|所选商品',text):return False
    if re.search(r'写|代码|原理|定义|什么是|如何设计',text):return False
    return bool(re.search(r'商品列表|类目列表|收录范围|资料覆盖范围|(?:你这里|这里|你)?(?:都)?能查(?:到)?些?什么(?:[？?。]|$)',text) or
      (re.search(r'商品|产品|品类|类目|知识库|资料库|库里',text) and
       re.search(r'有哪些|有什么|哪些|什么种类|什么类型|都有些什么|都有什么|包含什么|收录什么',text)))


def category_search_request(text,manifest):
    command = SEARCH_REQUEST.match(text)
    if command and exact_category_types(command[1],manifest):
        return True
    if re.search(r'除了|以外|其他类目|其它类目|收录哪些类目|什么是|原理|为什么|怎么|如何|代码|函数|算法|字符串|解释|讲解',text):return False
    if not re.search(r'查|找|搜索|看看|看一下|浏览|有哪些|有没有|想买',text,re.I):return False
    return any(re.search(pattern,text,re.I) and any(k in manifest['product_types'] for k in kinds) for pattern,en,kinds in CATEGORIES)



SEARCH_REQUEST = re.compile(
    r"^(?:[，,。！!\s]*(?:请问|请|帮我|我想要|我想|我要|替我))*\s*"
    r"(?:搜索|查找|找一找|找(?:一个|个|些)?|查询|查一下|查看|查|看看|看一下|看下|看|浏览|想买|买"
    r"|search\b|find\b|show me\b|looking for\b)[:：\s]*(\S.*)$", re.I
)



def exact_category_types(text,manifest):
    key = re.sub(r'(?:类的?(?:商品|产品)?|的(?:商品|产品)|类目|分类|商品|产品)$','',text.strip(" \t。?!？！")).strip()
    product_types = manifest['product_types']
    for candidate in (text.strip(" \t。?!？！"),key):
        if candidate.upper() in product_types:
            return [candidate.upper()]
        localized = [kind for kind,name in CATEGORY_LABELS.items() if name == candidate and kind in product_types]
        if localized:
            return localized
    return []


def catalog_search_route(text, retriever):
    """Recognize a named product from the catalog, including unseen short names."""
    if re.search(r"现行|实时|今天|库存|有货|保修|退货|退款|下单|订单|物流"
                 r"|原理|什么是|为什么|怎么|如何|意思|写|代码|函数|字符串|算法|翻译", text):
        return None
    command = SEARCH_REQUEST.match(text)
    if command:
        query = command[1].strip(" \t，,。?!？！")
        normalized = normalize_search(text, retriever.manifest)
        model = re.fullmatch(r"(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9./_-]{3,47}", query)
        if (normalized['inferred_product_types'] or model or re.search(r'商品|产品|型号|品牌',query)
                or retriever.title_has_phrase(normalized['query'])):
            return {"intent":"search","reason":"explicit_catalog_search"}
        return None
    query = text.strip(" \t\n,，。.!！?？")
    if not 1 <= len(query) <= 48 or not re.fullmatch(r"[\w\s+./'-]+", query):
        return None
    if re.search(r"吗|呢|吧|请问|告诉我|你(?:好|能|会|是|可)", query):
        return None
    types = retriever.manifest["product_types"]
    if exact_category_types(query,retriever.manifest) or any(re.fullmatch(pattern, query, re.I) and
                                    any(kind in types for kind in kinds) for pattern, _, kinds in CATEGORIES):
        return {"intent":"search","reason":"named_catalog_category"}
    if retriever.title_has_phrase(query):
        return {"intent":"search","reason":"actual_catalog_title_phrase"}
    return None


def continuation_request(text):
    clean=re.sub(r'[\s，,。.!！?？]+','',text)
    return bool(re.fullmatch(r'(?:那|我说的|我是说|除此之外)?(?:还有(?:呢|吗|什么|哪些|别的|其他|其它|更多)?(?:相关信息|信息|资料|内容)?|继续|接着说|再说(?:点|一些)|再多介绍一点)',clean))



def catalog_navigation(text):
    """Resolve page commands and ordinary backward references within a catalog turn."""
    clean = re.sub(r'[\s，,。.!！?？]+','',text)
    if re.search(r'^(?:请|帮我|我想)?(?:回到|返回|重看|看|看一下)(?:商品|类目)?(?:目录的?)?(?:第一页|开头)$', clean) \
       or re.search(r'^(?:请|帮我)?(?:从头|重新)(?:列|看|浏览|展示|打开).{0,14}(?:目录|类目|商品)', clean):
        return 'first'
    if continuation_request(text) or re.fullmatch(r'(?:请|帮我)?(?:看|翻到|继续看)?(?:下一页|下页|下一批|后面一页|nextpage)'
                                                r'(?:目录|类目|商品)?|(?:再翻一页|换一批|再来一页)', clean, re.I):
        return 'next'
    if re.search(r'什么是|定义|原理|代码|写一|接下来怎么|聊聊|什么材质|重量|品牌|库存|价格', clean):
        return None
    catalog_scope = re.search(r'商品|产品|品类|类目|种类|目录', clean)
    shown = re.search(r'(?:上面|前面|刚才|刚刚|已经列|已经说|刚列)(?:的|提到的|这些|那些|这批|那批)?'
                      r'|(?:这些|那些|这批|那批|之前提过的|没提到的|没列过的)', clean)
    more = re.search(r'还有|还剩|另外|其他|其它|更多|不同|剩下|没(?:提|列|说|展示)|别的|接着', clean)
    enumerate_next = re.search(r'^(?:请|帮我)?(?:继续|再|接着)(?:给我)?(?:列|展示|看看?|说|举)', clean)
    if (catalog_scope and more and (shown or catalogue_request(text) or enumerate_next)) \
       or (shown and more and re.search(r'还有|再|哪些|接着',clean)) \
       or (catalog_scope and enumerate_next):
        return 'next'
    return None


def overview_request(text):
    return bool(re.search(r'(?:相关|更多|其他|其它|详细|基本|全部|完整).{0,5}(?:信息|资料|介绍|内容)|(?:介绍|了解).{0,8}(?:这款|这个商品|该商品)|商品(?:的)?(?:信息|资料|详情)|(?:这款|这个商品).{0,8}(?:有什么|有哪些)',text))


def catalog_page(manifest,offset=0,budget=14):
    types=manifest['product_types']
    preferred=[('CELLULAR_PHONE_CASE','手机壳'),('SHOES','鞋'),('BOOT','靴子'),('SANDAL','凉鞋'),
      ('TECHNICAL_SPORT_SHOE','运动鞋'),('HEADPHONES','耳机'),('BACKPACK','背包'),('CHAIR','椅子'),
      ('SOFA','沙发'),('TABLE','桌子'),('DRINKING_CUP','茶杯、马克杯等饮用杯'),('BED','床'),('LIGHT_BULB','灯泡'),('COFFEE','咖啡'),('RUG','地毯')]
    used={k for k,l in preferred}
    ordered=[(k,label,types[k]) for k,label in preferred if k in types]
    ordered += [(k,CATEGORY_LABELS.get(k,k),n) for k,n in types.items() if k not in used]
    chunk=ordered[offset:offset+budget]
    if not chunk:return '这份目录的全部原始类目已展示完，可以指定类目查商品。',offset,[]
    lines=[f'本地ABO历史商品资料共有 {manifest["records"]:,} 条、{len(types)} 个原始类目。'+('例如：' if offset==0 else '接着上次目录，还有：')]
    lines += [f'- {label}（{kind}）：{n:,} 条' for kind,label,n in chunk]
    lines.append('可以直接说“我想查询鞋子类的商品”，或说“还有呢”继续看类目。这些是历史资料，不代表实时在售库存。')
    return '\n'.join(lines),offset+len(chunk),[{'product_type':k,'label':l,'records':n} for k,l,n in chunk]


def normalize_search(text,manifest,filters=None,*,separate_terms=True):
    filters=dict(filters or {});query=text.strip()
    request=SEARCH_REQUEST.match(query)
    if request:query=request[1]
    query=re.sub(r'^(?:选中|选择|就要|就|选)\s*','',query)
    query=re.sub(r'[呢吧？?！!。\s]+$','',query)
    query=re.sub(r'(?:类的?商品|类的?产品|类商品|类产品|商品|产品)[？?。\s]*$','',query).strip()
    inferred=[];matched=None
    exact=exact_category_types(query,manifest)
    if exact:
        inferred=exact
        matched='exact_source_type' if query.upper() in exact else 'chinese_catalog_label'
        query=' '.join(exact)
    for pattern,english,kinds in ([] if inferred else CATEGORIES):
        if re.search(pattern,query,re.I):
            inferred=[k for k in kinds if k in manifest['product_types']]
            replacement=f' {english} ' if separate_terms else english
            query=re.sub(pattern,replacement,query,flags=re.I);matched=pattern;break
    if inferred:
        if filters.get('product_type') and filters['product_type'] not in inferred:
            raise ValueError('Requested category conflicts with the explicit product_type filter')
        if not filters.get('product_type') and not filters.get('product_types'):filters['product_types']=inferred
    pattern='|'.join(re.escape(k) for k in sorted(EXPANSIONS,key=len,reverse=True))
    query=re.sub(pattern,lambda m:f' {EXPANSIONS[m[0]]} ' if separate_terms else EXPANSIONS[m[0]],query)
    query=re.sub(r'类的?|的商品|的产品',' ',query)
    query=' '.join(query.split())
    return {'original_query':text,'query':query,'filters':filters,'category_alias':matched,
      'inferred_product_types':inferred,'needs_semantic_translation':bool(re.search(r'[\u4e00-\u9fff]',query))}


def normalized(text):
    return re.sub(r'\s+','',unicodedata.normalize('NFKC',text)).casefold()


TITLE_DIMENSION = re.compile(
    r"(?<![\d.])\d+(?:\.\d+)?\s*['′\"]\s*[x×]\s*\d+(?:\.\d+)?\s*['′\"]",
    re.I,
)


def overview_items(product,doc,*,include_title_dimension=False):
    raw=json.loads(product['raw_json']);items=[]
    for i,(key,value) in enumerate(evidence_fields(raw)):
        items.append({'item_id':f'field:{key}:{i}','label':LABELS.get(key,key),'value':value,'source_field':key})
    if include_title_dimension and not any(i["source_field"] in {"width","length","height"} for i in items):
        title_dimension = TITLE_DIMENSION.search(product["title"])
        if title_dimension:
            items.insert(2, {"item_id": "field:title_dimension", "label": "商品标题标注尺寸（原文）",
                             "value": title_dimension.group(), "source_field": "item_name"})
    for i,value in enumerate(display_values(raw,'bullet_point')):
        items.append({'item_id':f'bullet:{i}','label':'产品要点（源文）','value':value.strip(),'source_field':'bullet_point'})
    for key,label in [('marketplace','登记平台'),('country','站点国家/地区代码')]:
        if raw.get(key):items.append({'item_id':'source:'+key,'label':label,'value':str(raw[key]),'source_field':key})
    for key,label in EXTRA_LABELS.items():
        values=display_values(raw,key)
        if key=='color_code' and isinstance(raw.get(key),list):
            values=[value for value in raw[key] if isinstance(value,str)]
        for i,value in enumerate(values):
            value=value.strip()
            if value:
                items.append({'item_id':f'extra:{key}:{i}','label':label,'value':value,'source_field':key})
    for item in items:
        found=[] if item['item_id'].startswith('source:') else [p['page_id'] for p in doc['pages'] if normalized(item['value']) in normalized(p['text'])]
        item.update(citation=found[0] if found else product['listing_key']+':metadata',
          listing_key=product['listing_key'],raw_sha256=product['raw_sha256'],
          source_member=product['source_member'],source_line=product['source_line'])
    return items


def overview_chunk(product,doc,shown,budget=6,kind='overview',*,complete=False):
    items=overview_items(product,doc,include_title_dimension=kind=='properties');remaining=[i for i in items if i['item_id'] not in shown]
    # First provide fields; a continuation reveals source notes or remaining fields.
    first=not shown;fields=[i for i in remaining if i['item_id'].startswith('field:')]
    features=[i for i in remaining if i['source_field']=='bullet_point']
    chunk=remaining if complete else (
        features[:budget] if kind=='features' and features else (fields[:budget] if first and fields else remaining[:budget]))
    title=('这款商品的基本资料：' if first else '这款商品还记录了以下资料：')
    if kind=='features':title='这款商品在源资料中记载的功能与使用要点：' if features else '资料没有单独的功能说明，可以确认以下已记录信息：'
    if complete:title='这款商品已收录的可读属性和原文：' if first else '这款商品剩余的已收录资料：'
    lines=[title,product['listing_key'],product['title']]
    lines += [f'- {i["label"]}：{i["value"]}' for i in chunk]
    if not chunk:lines=['这款商品的已收录字段和产品要点已经展示完，没有更多未展示的资料。',product['listing_key']]
    elif len(remaining)>len(chunk):lines.append('还有未展示的源资料，可以继续问“还有呢”。')
    else:lines.append('以上是当前已收录的资料；未报告的参数不能据此补充。')
    return '\n'.join(lines),chunk
