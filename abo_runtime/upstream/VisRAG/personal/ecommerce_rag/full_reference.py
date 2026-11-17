"""Resolve conversational objects from explicit selected entities, not old answers."""
import re
from catalog_labels_zh import CATEGORY_LABELS
from full_catalogue import CATEGORIES, continuation_request, exact_category_types
from full_evidence import FIELD_NAMES, PHOTO_REQUEST


ORDINAL = re.compile(r'第([0-9]{1,2}|[一二三四五六七八九十两])(?:个|款|件|台)?')


def ordinal_index(value):
    return int(value)-1 if value.isdigit() else '一二三四五六七八九十'.index(value.replace('两','二'))


def ordinal_choice(text,last_task):
    """An ordered reference belongs to the latest shopping task, not stale lists."""
    if not last_task or last_task.get('lane')!='product':return None
    if re.search(r'函数|代码|程序|算法|故事|诗|句子|例子|人|朋友',text):return None
    found=list(ORDINAL.finditer(text))
    if not found:
        bare=re.fullmatch(r'\s*([1-9一二三四五六七八九十两])\s*',text)
        found=[bare] if bare else []
    if not found:return None
    if len(found)>1:return {'ambiguous':True}
    value=found[0][1]
    index=ordinal_index(value)
    remainder=text[:found[0].start()]+text[found[0].end():]
    remainder=re.sub(r'选中|选择|就|选|这个|商品|产品|吧|呢|[\s？?！!。]','',remainder)
    resolved_text=text[:found[0].start()]+'这款'+text[found[0].end():]
    if not remainder:
        return {"index": index, "task": "select", "resolved_text": resolved_text}
    if property_overview_request(remainder):
        task = "properties"
    elif feature_request(remainder):
        task = "features"
    else:
        task = "overview" if view_request(remainder) else "field"
    return {"index": index, "task": task, "resolved_text": resolved_text}


def bare_category_choice(text,last_task,manifest):
    if not last_task or last_task.get('lane') not in {'catalog','product'}:return False
    value=re.sub(r'[？?！!。\s]+$','',text.strip())
    value=re.sub(r'^(?:选中|选择|就要|就|选|看看|查看)\s*','',value)
    value=re.sub(r'(?:类商品|类产品|类)$','',value.rstrip('呢吧 ')).strip()
    if exact_category_types(value,manifest):return True
    return any(re.fullmatch(pattern,value,re.I) and any(k in manifest['product_types'] for k in kinds) for pattern,en,kinds in CATEGORIES)


def property_overview_request(text):
    """Distinguish a general property summary from a named field or photo task."""
    if PHOTO_REQUEST.search(text) or re.search(
        rf"{FIELD_NAMES}|尺寸|尺码|容量|功能|用途|价格|库存|退款|对比|比较|区别|差异|相同|哪个|哪款|"
        r"怎么|如何|含义|定义|什么意思|什么是|什么叫|什么回事|解释.*(?:概念|原理)", text, re.I
    ):
        return False
    return bool(re.search(r"属性|规格|参数|\b(?:specs|specifications|properties)\b", text, re.I))


def feature_request(text):
    return bool(re.search(r'功能|用途|使用特点|特性|特色|功能属性|哪些属性|什么属性',text))


def view_request(text):
    if re.search(r'材质|材料|品牌|颜色|重量|多重|宽度|高度|长度|型号|尺寸|多少钱|库存|订单|退款',text):return False
    return bool(re.search(r'查看|看看|看下|看一下|了解|介绍|展示|显示|\bview\b|\bshow\b',text,re.I))


DETAIL_CONTENT = r'(?:资料|信息|详情|属性|规格|参数|原文|描述|内容|产品要点)'
MORE_DETAIL = re.compile(
    r'(?:(?:能不能|能|可以|请|帮我|给我)?(?:再|继续|接着)(?:给我)?(?:详细(?:点|一点|些|一些)|'
    r'说(?:详细|具体)点|介绍(?:一下|一点|一些)|介绍得更详细(?:一点)?|'
    r'(?:展示|列出|补充)(?:一点|一些|更多)?(?:资料|信息|详情|属性|规格|参数|内容)?)'
    r'|(?:那|我说的)?(?:还有|还剩)(?:呢|什么|哪些|没有)?(?:别的|其他|其它|更多|剩下的)?'
    r'(?:详细的?|没(?:展示|显示|列|说)过的)?(?:资料|信息|详情|属性|规格|参数|描述|内容|产品要点)?'
    r'|(?:能不能|能|可以)?(?:多说|多介绍)(?:一点|一些)'
    r'|(?:请|给我|帮我)?(?:看|翻到|继续看)?(?:下一页|下页|再翻一页))'
)
DETAIL_AMOUNT = r'完整|全部|所有|一次性|一次|都|全'
DETAIL_ACTION = r'展示|显示|列出|查看|补充|介绍|看|列|发|说|给'
DETAIL_WORDS = re.compile(
    '|'.join((DETAIL_AMOUNT,DETAIL_CONTENT,DETAIL_ACTION,
              r'刚才那款|刚才|这两款|这款|这件|这个|所选|选中|商品|产品'
              r'|一下|给我|帮我|能不能|我想|可以|麻烦|请|再|那|把|将|该|的|地|出|来|完|我'))
)


def product_detail_followup(text, active_product, explicit_product):
    """Keep underspecified follow-ups with the latest selected listing."""
    if not (active_product or explicit_product):
        return None
    clean = re.sub(r'[\s，,。.!！?？；;：:“”「」]+', '', text)
    clean = re.sub(r'(?:好吗|可以吗|吗|吧)$', '', clean)
    body = re.sub(r'^(?:那)?(?:还有(?:呢|什么|哪些|吗)?|接着|继续)', '', clean, count=1)
    remainder = re.sub(r'^(?:(?:请|帮我|给我|把|将))*?(?:剩下(?:的)?|其余(?:的)?|其他(?:的)?|没(?:展示|显示|列|说)过的)',
                       '', body, count=1)
    if (re.search(DETAIL_AMOUNT,remainder) and re.search(DETAIL_CONTENT+'|'+DETAIL_ACTION,remainder)
            and not DETAIL_WORDS.sub('',remainder)):
        return 'rest' if remainder != body else 'all'
    if continuation_request(text) or MORE_DETAIL.fullmatch(clean):
        return 'more'
    return None


def selected_name_reference(text,entities):
    explicit=re.search(r'这个商品|这款商品|该商品|所选(?:的)?商品|这款|这件|这双|这个包|这包',text)
    if explicit:return True
    for entity in entities:
        kind=entity.get('product_type','')
        if re.search(r'(?:这个|这款|该|刚才(?:的)?|选中(?:的)?)\s*'+re.escape(kind),text,re.I):return True
        for pattern,en,kinds in CATEGORIES:
            if kind in kinds and re.search(r'(?:这个|这款|该|刚才(?:的)?|选中(?:的)?)\s*(?:'+pattern+')',text,re.I):return True
    return False


def named_reference_types(text):
    result=set()
    for pattern,en,kinds in CATEGORIES:
        if re.search(r'(?:这个|这款|该|刚才(?:的)?|选中(?:的)?)\s*(?:'+pattern+')',text,re.I):result.update(kinds)
    return result


def normalize_selected_field_reference(text, products):
    """Reduce a category name only when the selected source actually has that type."""
    if len(products)!=1:
        return text
    kind = products[0]['product_type']
    label = CATEGORY_LABELS.get(kind,kind)
    aliases = [re.escape(label),re.escape(kind)]
    aliases.extend(pattern for pattern,_,types in CATEGORIES if kind in types)
    return re.sub(r'((?:这款|这个|这件|这台|这双|该|所选(?:的)?))\s*(?:'+'|'.join(aliases)+r')(?=\s*的)',
                  r'\1',text,count=1,flags=re.I)


def product_task(text,entities,last_task,has_explicit_id=False):
    """Return a grounded task only with a positive object reference or action."""
    # These are language roles, not allowed ordinary topics. Conceptual/programming
    # requests remain open even if a product was selected earlier.
    if re.search(r'这个函数|这段代码|这个程序|算法|动态规划|正则|字符串|什么是|心理学|营销方法|定义|原理',text):return None
    named_types=named_reference_types(text)
    if not has_explicit_id and entities and named_types and not any(e.get('product_type') in named_types for e in entities):
        return {'intent':'clarify','reason':'selected_object_type_conflict',
                'clarification':'当前选中的商品类别与这次提到的对象不一致。请先搜索或提供你要查询的那款商品编号。'}
    named=selected_name_reference(text,entities)
    pronoun=bool(re.search(r'^(?:那|请问|我说的?|我是说|那么)?\s*[他它](?:的|还|有|能|可|是|支持)',text))
    implicit=bool(re.fullmatch(r'(?:功能|功能属性|属性|规格|参数|用途|使用特点)(?:是什么|有哪些|有什么|呢|怎么样)?[？?！!。\s]*',text))
    recent_product=bool(last_task and last_task.get('lane')=='product')
    linked=has_explicit_id or named or ((pronoun or implicit) and recent_product)
    if not linked:return None
    if (pronoun or implicit) and not named and len(entities)>1:
        return {'intent':'clarify','reason':'ambiguous_selected_entity','clarification':'你指的是哪一款商品？请提供编号，或明确第一个/第二个。'}
    if property_overview_request(text):
        return {"intent": "fact", "reason": "resolved_product_properties", "product_task": "properties"}
    if feature_request(text):
        return {'intent':'fact','reason':'resolved_product_features','product_task':'features'}
    if view_request(text):
        return {'intent':'fact','reason':'resolved_product_view','product_task':'overview'}
    if re.search(r'品牌|材质|颜色|重量|多重|宽度|高度|长度|尺寸|型号|参数|规格|属性|配件|清洁|安装|组装|承重|续航',text):
        return {'intent':'fact','reason':'resolved_product_property','product_task':'field'}
    return None
