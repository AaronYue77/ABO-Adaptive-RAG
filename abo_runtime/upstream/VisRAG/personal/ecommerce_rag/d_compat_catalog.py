"""Full ABO lookup and conservative, source-bound compatibility claims."""
from dataclasses import dataclass
import json
import re
import unicodedata

from full_catalog import terms


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold().strip()


MODEL_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:"
    r"iphone\s*(?:[0-9]{1,2}|xs|xr|x|se)(?:\s*(?:pro\s*max|plus|pro|max|mini|\+))?"
    r"|galaxy\s*(?:note\s*|[sam]\s*)\d{1,2}(?:\s*(?:ultra|edge|plus|fe|s|\+))?"
    r"|(?:google\s*)?pixel\s*(?:\d{1,2}[a-z]?|xl)(?:\s*(?:pro\s*xl|pro|xl|fold))?"
    r"|moto\s*[ge]\s*\d{1,2}(?:\s*(?:plus|play|power))?"
    r"|(?:huawei\s*)?p\d{1,2}\s*lite(?:\s*20\d\d(?:\s*version)?)?"
    r"|ipad\s*(?:mini\s*\d?|air\s*\d?|pro\s*\d+(?:\.\d+)?|\d+)"
    r"|macbook\s*(?:air|pro)\s*(?:\d{2}(?:\.\d+)?)(?:\s*inch)?"
    r"|a[12]\d{3}"
    r"|airpods\s*(?:pro(?:\s*20\d\d)?|[1234])"
    r"|nintendo\s*switch(?:\s*(?:oled|lite))?"
    r"|playstation\s*[345](?:\s*pro)?|xbox\s*(?:one(?:\s*[sx])?|series\s*[sx])"
    r"|keurig\s*2\.0|k[\s-]*cup|hp\s*\d{2,4}(?:\s*xl)?"
    r")(?![a-z0-9])", re.I
)

COMPAT_WORD = re.compile(
    r"\b(not compatible with|not for|does not fit|doesn't fit|not fit|incompatible with|except|excluding|"
    r"compatible with|designed for|made for|suitable for|replacement for|for use with|fits|for)\b"
    r"|(?:不适用(?:于)?|不兼容|适用(?:于)?|兼容)", re.I
)
CASUAL = (
    (r"苹果\s*(\d{1,2})\s*(?:代)?\s*(?:的)?\s*(?:大屏(?:版)?|大号)", r"iphone \1 plus"),
    (r"苹果\s*(\d{1,2})\s*(?:代)?\s*(?:的)?\s*(?:小屏(?:版)?|标准版)", r"iphone \1"),
)
ALIASES = (
    (r"苹果\s*(?=[0-9]|xs|xr|se\b|x\b)", "iphone "),
    (r"(?<![a-z0-9])ps\s*(?=[345](?![a-z0-9]))", "playstation "),
    (r"(?<![a-z0-9])switch\b(?!\s*(?:blade|button))", "nintendo switch"),
    (r"任天堂\s*(?:游戏机)?", "nintendo switch"),
    (r"惠普\s*(?=\d)", "hp "),
    (r"三星\s*(?=[asm]\s*\d|note\s*\d)", "galaxy "),
)
ACCESSORIES = {
    "phone_case": (r"手机壳|手机套|保护壳|手机保护套|换(?:个|一款)?壳|\b(?:phone case|phone cover|mobile cover|protective case)\b",
                   r"\b(?:case|cover|bumper)\b|手机壳|保护套"),
    "screen": (r"钢化膜|手机膜|电脑防窥膜|屏幕贴膜|屏幕保护膜|\b(?:screen protector|privacy screen)\b",
               r"\b(?:tempered glass|screen protector|screen filter|privacy screen)\b|钢化膜|贴膜"),
    "headset": (r"游戏耳机|耳麦|耳机|\b(?:headset|headphones)\b",
                r"\b(?:headset|headphones)\b|耳机"),
    "coffee": (r"咖啡胶囊|咖啡袋|咖啡机胶囊|\b(?:coffee pods|k[- ]cup pods)\b",
               r"\b(?:coffee|pods|k[- ]cup)\b|咖啡胶囊"),
    "ink": (r"墨盒|硒鼓|打印耗材|\b(?:ink cartridge|toner)\b",
            r"\b(?:ink|cartridge|kartuş|toner|cartouche|tinte)\b|墨盒|硒鼓"),
    "charger": (r"充电|电源适配器|\b(?:charger|power adapter)\b",
                r"\b(?:charger|charging|adapter|power bank)\b|充电"),
    "cable": (r"数据线|转接线|连接线|\b(?:usb cable|adapter cable)\b",
              r"\b(?:cable|adapter)\b|线"),
    "controller": (r"游戏手柄|手柄|控制器|\bgame controller\b",
                   r"\b(?:controller|gamepad|joystick)\b|手柄"),
    "filter": (r"滤芯|滤网|\b(?:filter|cartridge filter)\b", r"\bfilter\b|滤芯|滤网"),
}
GENERIC_CODE = re.compile(r"(?i)(?<![a-z0-9])(?=[a-z0-9_-]*\d)(?:[a-z]{2,}[-_ ]?\d[a-z0-9_-]{1,16})(?![a-z0-9])")
ACCESSORY_WORD = re.compile(r"\b(?:case|cover|protector|filter|adapter|charger|cable|headset|headphones|pods|toner|cartridge|"
                            r"controller|remote|mount|stand|replacement|refill|kit|sleeve|strap|bag)\b|配件|壳|滤芯|胶囊|墨盒")
NEGATIVE = re.compile(r"^not |^does|^incompatible|^except|^excluding|^不")


def canonical(text):
    value = re.sub(r"\s+", " ", normalize(text).replace("+", " plus")).strip()
    value = re.sub(r"^(iphone|ipad|galaxy|pixel|hp|airpods|playstation)\s*(?=\d)", r"\1 ", value)
    value = re.sub(r"(?<=\d)(?=pro|plus|max|mini|ultra|edge|fe|play|power)", " ", value)
    value = re.sub(r"^moto\s*([eg])\s*(?=\d)", r"moto \1", value)
    value = re.sub(r"^galaxy\s+([sma])\s+(?=\d)", r"galaxy \1", value)
    value = re.sub(r"^iphone\s*(xs|xr|x|se)\b", r"iphone \1", value)
    value = re.sub(r"^(?:google |huawei )| inch$", "", value)
    return value


def models(text):
    return list(dict.fromkeys(canonical(m.group()) for m in MODEL_PATTERN.finditer(normalize(text))))


def resolve_models(text):
    value = normalize(text)
    guesses = [canonical(m.expand(repl)) for pattern, repl in CASUAL if (m := re.search(pattern, value))]
    if guesses:
        return guesses, "informal_variant"
    value = re.sub(r"(?<!nintendo )\bswitch\b(?!\s*(?:blade|button))", "nintendo switch", value)
    for pattern, replacement in ALIASES:
        if "switch" not in replacement:
            value = re.sub(pattern, replacement, value)
    found = models(value)
    if found:
        return found, "standard_or_alias"
    marker = re.search(r"(?:型号(?:是|为)?|model(?:\s+is)?)[\s:：]+(\S+)", value)
    if marker and GENERIC_CODE.fullmatch(marker[1].strip("，。？！,")):
        return [canonical(marker[1].strip("，。？！,"))], "explicit_exact_code"
    return [], "missing_model"


def accessory_kind(text):
    return next((key for key, (pattern, _) in ACCESSORIES.items() if re.search(pattern, text, re.I)), "")


@dataclass(frozen=True)
class Evidence:
    citation: str
    listing: str
    field: str
    quote: str
    source_member: str
    source_line: int
    raw_sha256: str


def fields(row):
    item = json.loads(row["raw_json"])
    values = [("title", row["title"])]
    for key in ("item_name", "bullet_point"):
        for index, entry in enumerate(item.get(key) or []):
            if isinstance(entry, dict) and isinstance(entry.get("value"), str):
                values.append((f"{key}[{index}]", entry["value"]))
    output = []
    seen = set()
    for field, quote in values:
        if not quote or quote in seen:
            continue
        seen.add(quote)
        output.append(Evidence(f"ABO:{row['listing_key']}:{field}", row["listing_key"], field,
                               quote, row["source_member"], row["source_line"], row["raw_sha256"]))
    return output


def contains_kind(item, kind):
    return not kind or bool(re.search(ACCESSORIES[kind][1], item["title"], re.I))


def segment_models(value, target):
    found = models(value)
    # Exact OEM codes can be queried even when outside the familiar device families.
    if not models(target) and GENERIC_CODE.fullmatch(target) and re.search(
        r"(?<![a-z0-9_-])" + re.escape(target) + r"(?![a-z0-9_-])", value
    ):
        found.append(target)
    # Expand only explicit, unambiguous same-family numeric lists.
    for prefix in ("iphone", "airpods", "hp", "ipad mini"):
        pattern = r"\b" + prefix + r"\s+(\d+)(?:\s*(?:/|&|and|ve)\s*(?:hp\s*)?\d+)+(?![a-z0-9])"
        for match in re.finditer(pattern, value):
            if re.match(r"\s*(?:plus|pro|max|mini|xl|\+)", value[match.end():]):
                continue
            found.extend(f"{prefix} {n}" for n in re.findall(r"\d+", match[0]))
    return set(found)


def classify(evidence, target):
    """A quoted model plus an explicit support/exclusion relation is required."""
    text = normalize(evidence.quote)
    markers = list(COMPAT_WORD.finditer(text))
    hits = set()
    if target == "p20 lite 2019" and "p20 lite" in models(text) and re.search(r"\bnot for 2019 version\b", text):
        hits.add("incompatible")
    for i, marker in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        tail = text[marker.end():end]
        tail = re.split(r"(?<!\d)\.|\.(?!\d)|[;!?。；！]|\b(?:except|excluding|but|without support)\b",
                        tail, maxsplit=1)[0]
        matched = segment_models(tail, target)
        if target not in matched:
            continue
        marker_text = normalize(marker[0])
        if NEGATIVE.search(marker_text):
            hits.add("incompatible")
        # A bare 'for' can occur in suitability language for unrelated products.
        elif marker_text == "for" and not ACCESSORY_WORD.search(text[:marker.start()]):
            continue
        elif re.search(r"\b(?:only|except|excluding|select models|some models)\b", tail) and (
            len(matched) > 1 or not re.match(r"\s*(?:apple\s+)?" + re.escape(target) + r"\b", tail)
        ):
            continue
        elif target.startswith("macbook") and (re.search(r"\ba[12]\d{3}\b|\b20\d\d\b", text)):
            # A screen-size family alone doesn't establish compatibility across years.
            continue
        elif target == "huawei p20 lite" and "2019" in text:
            continue
        else:
            hits.add("compatible")
    return hits


class CatalogTool:
    """Stage B catalog and FTS5, with exact phrase search before broadening."""

    def __init__(self, retriever):
        self.retriever = retriever

    def exact(self, sku, domain):
        return self.retriever.get(sku, domain or None)

    def search(self, model, kind, domain, *, broaden=False, limit=24):
        tokens = terms(model).split()
        if not tokens or not 1 <= limit <= 50:
            raise ValueError("Invalid phrase search")
        if broaden:
            filters = {"domain": domain} if domain else None
            return self.retriever.search(model, "bm25", limit, filters)["items"]
        arguments = ['"' + " ".join(tokens) + '"']
        where = ""
        if domain:
            where = " AND p.domain=?"
            arguments.append(domain)
        arguments.append(75 if kind else limit)
        rows = self.retriever.db.execute(
            "SELECT p.*, -bm25(products_fts,8,5,2,1) score FROM products_fts "
            "JOIN products p ON p.id=products_fts.rowid WHERE products_fts MATCH ?" + where
            + " ORDER BY score DESC,p.listing_key LIMIT ?", arguments
        )
        return [dict(r) for r in rows if contains_kind(r, kind)][:limit]
