# -*- coding: utf-8 -*-
"""LLM 輔助：自然語言輸入 -> 搜尋條件，以及推薦結果的白話解說。

LLM 只負責「翻譯」，不參與推薦本身：
    輸入端 -- 把一句話需求轉成 預算 / 用途 / 是否必須獨顯 / CPU 品牌 四個參數，
              之後的搜尋、評分、升級完全由 recommender.py 決定。
    輸出端 -- 把 analyze() 的結果整理成一份數字摘要交給 LLM，請它用白話解釋；
              提示裡明確禁止它引用摘要以外的數字，避免編造價格、跑分或幀數。
    估價單 -- 把貼上的菜單拆成零件並對應到資料庫，相容性、加總與比較由 quote.py 計算。

三者都有規則式的備援版本：沒裝 anthropic 套件、沒有 API key、
或連不上網路時自動退回，整個 app 照樣能離線運作。
"""
import json
import re
import unicodedata

from . import quote
from .compat import check_build
from .loader import FIXED_COST, FIXED_COST_LABEL
from .recommender import USAGES, WEIGHTS

try:
    import anthropic
except ImportError:          # 選用套件：沒裝就只用規則式版本
    anthropic = None

MODEL = "claude-sonnet-5-5"

# 伺服器端備援：模型的安全分類器若拒答，API 會在同一個請求裡改由建議的備援模型回答
_BETAS = ["server-side-fallback-2026-07-01"]

BRANDS = ["不限", "Intel", "AMD"]
UNSET = "未提及"


class LLMUnavailable(Exception):
    """LLM 這次無法使用；訊息是可以直接顯示給使用者看的原因。"""


def make_client(api_key: str = None):
    """建立 Claude 用戶端；沒裝 anthropic 套件時回傳 None。"""
    if anthropic is None:
        return None
    return anthropic.Anthropic(api_key=api_key, timeout=60.0, max_retries=1)


def _reason(err: Exception) -> str:
    """把 SDK 的例外轉成一句中文原因。"""
    if anthropic is not None:
        if isinstance(err, anthropic.AuthenticationError):
            return "API key 無效"
        if isinstance(err, anthropic.PermissionDeniedError):
            return "API key 沒有使用此模型的權限"
        if isinstance(err, anthropic.RateLimitError):
            return "請求太頻繁，已被限流"
        if isinstance(err, anthropic.APITimeoutError):
            return "連線逾時"
        if isinstance(err, anthropic.APIConnectionError):
            return "連不上 Claude API（可能沒有網路）"
        if isinstance(err, anthropic.APIStatusError):
            return f"Claude API 回傳錯誤 {err.status_code}"
    if isinstance(err, TypeError) and "authentication" in str(err):
        return "找不到 API key"
    return f"{type(err).__name__}: {err}"


# ================================================================ 輸入端：解析

_PARSE_SYSTEM = f"""\
你是電腦組裝推薦系統的輸入解析器。使用者會用一句話描述想買的桌上型電腦，你要把它轉成系統的四個搜尋參數。系統只認得這四個參數，其他需求一律無法處理。

- budget：預算上限，新台幣「元」，整數。「3萬」=30000、「兩萬五」=25000、「35k」=35000。給範圍（如 2 到 3 萬）取上限；「左右」「大概」就取該數字。沒提到填 0。
- usage：主要用途，從下列三者擇一：
  - 遊戲：打電動、電競、3A 大作、Steam、LOL、FPS 等
  - 剪輯創作：影片剪輯、調色、3D 建模與渲染、直播、修圖、程式編譯等吃多核心的工作
  - 文書上網：Office、上網、看影片、上課、寫報告
  同時提到多種時，取最吃效能、也是使用者最重視的那一項。沒提到或無法判斷填「{UNSET}」。
- require_dgpu：是否必須配獨立顯卡。明確要求獨顯或好顯卡填「是」；明確說不需要、內顯就好填「否」；沒提到填「{UNSET}」。不要因為用途是遊戲就自行推成「是」，系統會依用途權重自己決定。
- cpu_brand：明確指定 Intel 或 AMD 才填（i5、i7、Ryzen、銳龍等型號也算指定；「不要 AMD」等於指定 Intel）；明確說哪家都可以填「不限」；沒提到填「{UNSET}」。
- notes：用 1 到 3 句繁體中文告訴使用者你怎麼理解這段話，包括做了哪些換算或假設。若使用者提到系統無法納入的需求（例如機殼顏色、RGB、靜音、指定型號、筆電、螢幕、指定 FPS），直接點出「這項本系統無法納入」。不要寫推薦內容。"""

_PARSE_SCHEMA = {
    "type": "object",
    "properties": {
        "budget": {"type": "integer", "description": "預算上限（新台幣元）；沒提到填 0"},
        "usage": {"type": "string", "enum": USAGES + [UNSET]},
        "require_dgpu": {"type": "string", "enum": ["是", "否", UNSET]},
        "cpu_brand": {"type": "string", "enum": BRANDS + [UNSET]},
        "notes": {"type": "string"},
    },
    "required": ["budget", "usage", "require_dgpu", "cpu_brand", "notes"],
    "additionalProperties": False,
}


def _last_text(message) -> str:
    """取最後一個文字區塊。發生伺服器端備援時，前面可能夾著被拒答模型的殘段。"""
    texts = [b.text for b in message.content if b.type == "text"]
    return texts[-1] if texts else ""


def parse_with_llm(text: str, client) -> dict:
    """請 Claude 把一句話需求轉成搜尋參數。失敗時丟出 LLMUnavailable。"""
    try:
        msg = client.beta.messages.create(
            model=MODEL,
            max_tokens=4096,
            betas=_BETAS,
            fallbacks="default",
            output_config={
                "effort": "low",   # 短句抽取，不需要深入推理
                "format": {"type": "json_schema", "schema": _PARSE_SCHEMA},
            },
            system=_PARSE_SYSTEM,
            messages=[{"role": "user", "content": text}],
        )
    except Exception as err:
        raise LLMUnavailable(_reason(err)) from err

    if msg.stop_reason == "refusal":
        raise LLMUnavailable("模型拒絕處理這段輸入")
    if msg.stop_reason == "max_tokens":
        raise LLMUnavailable("模型輸出被截斷")
    try:
        raw = json.loads(_last_text(msg))
    except json.JSONDecodeError as err:
        raise LLMUnavailable("模型回傳的格式無法解析") from err

    budget = int(raw.get("budget") or 0)
    return {
        "budget": budget if budget > 0 else None,
        "usage": raw["usage"] if raw.get("usage") in USAGES else None,
        "require_dgpu": {"是": True, "否": False}.get(raw.get("require_dgpu")),
        "cpu_brand": raw["cpu_brand"] if raw.get("cpu_brand") in BRANDS else None,
        "notes": str(raw.get("notes", "")).strip(),
        "source": "llm",
    }


# ---------------------------------------------------------------- 規則式備援

_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "兩": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_NUM = "零〇一二兩两三四五六七八九十"


def _cn_int(s: str) -> int:
    """中文數字轉整數，只處理到「九十九」，預算用不到更大的。"""
    if "十" in s:
        tens, _, ones = s.partition("十")
        return (_CN_DIGIT.get(tens, 1) if tens else 1) * 10 + (_CN_DIGIT.get(ones, 0) if ones else 0)
    return _CN_DIGIT.get(s, 0)


def _find_budget(text: str):
    """找出句子裡所有像預算的金額，取最大者（給範圍時取上限）。

    小於 5,000 的一律忽略，避免把「4K 螢幕」「i5」「RTX 4060」當成預算。
    純數字必須帶有「元 / 預算 / 以內」等字眼，否則型號數字會被誤認。
    """
    t = re.sub(r"(?<=\d),(?=\d{3})", "", text)                           # 30,000 -> 30000
    found = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*萬\s*(?:(\d)\s*千?(?![\d萬]))?", t):  # 3萬、3.5萬、3萬5（千）
        found.append(float(m.group(1)) * 10000 + int(m.group(2) or 0) * 1000)
    for m in re.finditer(rf"([{_CN_NUM}]+)萬(?:([{_CN_NUM}])(?!萬))?", t):  # 三萬五、十二萬
        found.append(_cn_int(m.group(1)) * 10000 + (_CN_DIGIT.get(m.group(2), 0) * 1000))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*[kK千](?![a-zA-Z])", t):     # 35k、8千
        found.append(float(m.group(1)) * 1000)
    for m in re.finditer(r"(?:預算|大約|約|NT\$?|\$)\s*(\d{4,7})", t):     # 預算 30000
        found.append(float(m.group(1)))
    for m in re.finditer(r"(\d{4,7})\s*(?:元|塊|台幣|以內|以下|左右|上下)", t):  # 30000 元
        found.append(float(m.group(1)))
    found = [v for v in found if v >= 5000]
    return int(max(found)) if found else None


def _hits(text: str, words: list) -> int:
    """數一數關鍵字出現幾個。英文字詞要求前後不接英文字母，避免 pr 撞到 pro。"""
    n = 0
    for w in words:
        if w.isascii():
            n += bool(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text))
        else:
            n += w in text
    return n


_USAGE_WORDS = {
    "剪輯創作": ["剪輯", "剪片", "剪影片", "後製", "調色", "渲染", "算圖", "建模", "3d", "blender",
             "premiere", "pr", "達文西", "davinci", "after effects", "ae", "直播", "實況",
             "修圖", "photoshop", "創作", "編譯"],
    "遊戲": ["遊戲", "電玩", "打電動", "game", "gaming", "電競", "3a", "steam", "lol",
           "英雄聯盟", "apex", "valorant", "特戰", "fps", "原神", "吃雞", "魔物", "黑神話"],
    "文書上網": ["文書", "上網", "辦公", "office", "word", "excel", "報告", "作業", "上課",
             "追劇", "看影片", "youtube", "netflix", "網頁", "簡報", "文件"],
}

_BRAND_WORDS = {
    "Intel": ["intel", "英特爾", "英特尔", "i3", "i5", "i7", "i9", "core ultra", "酷睿"],
    "AMD": ["amd", "超微", "ryzen", "銳龍", "锐龙", "r5", "r7", "r9"],
}


def parse_with_rules(text: str) -> dict:
    """關鍵字比對版的解析器，LLM 不能用時的備援。"""
    text = unicodedata.normalize("NFKC", text)    # 全形數字與英文轉半形，例如「３萬」「ＡＭＤ」
    t = text.lower()

    scores = {u: _hits(t, ws) for u, ws in _USAGE_WORDS.items()}
    best = max(scores, key=scores.get)       # 平手時依字典順序：剪輯創作 > 遊戲 > 文書上網
    usage = best if scores[best] > 0 else None

    no_gpu = re.search(r"(不要|不需要|不需|不用|免|沒有|無)\s*(獨顯|獨立顯卡|顯卡|顯示卡)", t) \
        or re.search(r"內顯(就好|即可|就夠|就行|就可以)|用內顯", t)
    want_gpu = re.search(r"獨顯|獨立顯卡|顯卡|顯示卡|rtx|gtx|radeon|rx\s?\d", t)
    require_dgpu = False if no_gpu else (True if want_gpu else None)

    negated = {b for b, ws in _BRAND_WORDS.items()
               if re.search(rf"(不要|不想要|不考慮|排除|避開)\s*({'|'.join(map(re.escape, ws))})", t)}
    named = {b for b, ws in _BRAND_WORDS.items() if _hits(t, ws)} - negated
    if len(named) == 1:
        cpu_brand = named.pop()
    elif len(negated) == 1 and not named:
        cpu_brand = ({"Intel", "AMD"} - negated).pop()
    elif len(named) == 2 or re.search(r"(品牌|哪家|intel|amd).{0,6}(都可以|都行|不限|沒差|都好)", t):
        cpu_brand = "不限"
    else:
        cpu_brand = None

    notes = "關鍵字比對只認得常見說法；設定 API key 啟用 Claude 後，可以理解更口語的描述。"
    return {"budget": _find_budget(text), "usage": usage, "require_dgpu": require_dgpu,
            "cpu_brand": cpu_brand, "notes": notes, "source": "rules"}


def parse_request(text: str, client=None) -> dict:
    """先試 LLM，不行就退回規則式。回傳值多一個 fallback_reason 說明為何退回。"""
    if client is not None:
        try:
            return {**parse_with_llm(text, client), "fallback_reason": None}
        except LLMUnavailable as err:
            return {**parse_with_rules(text), "fallback_reason": str(err)}
    return {**parse_with_rules(text), "fallback_reason": None}


# ================================================================ 估價單健檢：拆行與對應

_QUOTE_SYSTEM = """\
你是電腦估價單的解析器。使用者會貼上一份組裝電腦的菜單（可能來自 ChatGPT、店家估價單或論壇，格式不一），你要把每個零件拆出來，並對應到【資料庫零件】中的一筆。對應完之後，系統會用資料庫的規格與價格檢查相容性、計算效能分。

- items：菜單上的每個零件依序列一筆。
  - kind：cpu、mb（主機板）、ram、gpu（顯示卡）、ssd、psu（電源）、other（機殼、散熱器、作業系統、螢幕、周邊等）。
  - text：這個零件在菜單上的原文（品牌、型號、規格），不含價格。
  - price：這一項的標價，新台幣元，整數；沒寫填 0；給範圍時取下限。
  - match_id：對應的資料庫零件 id；kind 是 other 填空字串。
  - match：exact（同型號或同規格）、closest（資料庫沒有，填效能與定位最接近的一筆）、none（無法判斷）。
- stated_total：菜單自己寫的總計；沒寫填 0。不要自己加總，系統要拿它檢查菜單有沒有算錯。

對應原則：
- CPU 與顯示卡看型號；主機板看晶片組（B650、Z790 等）與支援的記憶體世代；記憶體看世代、總容量、頻率；SSD 看容量與 PCIe 世代；電源看瓦數與認證。
- 型號不同就是 closest，例如 Ryzen 5 7600 對到 Ryzen 5 7500F、RTX 5070 對到 RTX 4070。
- 菜單寫錯或彼此不相容時照原文對應，不要替它修正；相容性由系統另外檢查。例如菜單寫 B650 主機板配 DDR4 記憶體，主機板照樣對到 B650，記憶體照樣對到 DDR4。
- 同類零件出現兩次（例如兩顆 SSD）就列兩筆。
- 說明文字、預算、用途、效能描述都不是零件，不要列。"""


def quote_catalog(data: dict) -> str:
    """給 LLM 對照用的資料庫零件清單：id、名稱，加上對應時用得到的規格。"""
    lines = []
    for p in data["cpu"].to_dict("records"):
        lines.append(f"{p['id']}  {p['name']}（{p['socket']}，{'有' if p['igpu'] else '無'}內顯）")
    for p in data["mb"].to_dict("records"):
        lines.append(f"{p['id']}  {p['name']}（{p['chipset']}，{p['socket']}，{p['mem_type']}，{p['form_factor']}）")
    for kind in ("ram", "gpu", "ssd", "psu"):
        lines += [f"{p['id']}  {p['name']}" for p in data[kind].to_dict("records")]
    return "\n".join(lines)


def _quote_schema(ids: list) -> dict:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": quote.KINDS + ["other"]},
                        "text": {"type": "string"},
                        "price": {"type": "integer"},
                        "match_id": {"type": "string", "enum": ids + [""]},
                        "match": {"type": "string", "enum": ["exact", "closest", "none"]},
                    },
                    "required": ["kind", "text", "price", "match_id", "match"],
                    "additionalProperties": False,
                },
            },
            "stated_total": {"type": "integer"},
        },
        "required": ["items", "stated_total"],
        "additionalProperties": False,
    }


def extract_quote_with_llm(text: str, data: dict, client) -> dict:
    """請 Claude 把菜單拆行並對應到資料庫零件。失敗時丟出 LLMUnavailable。

    回傳格式與 quote.extract_rules 相同。LLM 給的 id 若跟類別對不上就丟掉，交給規則式比對。
    """
    id_kind = {str(p["id"]): k for k in quote.KINDS for p in data[k].to_dict("records")}
    try:
        msg = client.beta.messages.create(
            model=MODEL,
            max_tokens=8192,
            betas=_BETAS,
            fallbacks="default",
            output_config={
                "effort": "low",   # 逐行抽取與查表，不需要深入推理
                "format": {"type": "json_schema", "schema": _quote_schema(list(id_kind))},
            },
            system=_QUOTE_SYSTEM + "\n\n【資料庫零件】\n" + quote_catalog(data),
            messages=[{"role": "user", "content": text}],
        )
    except Exception as err:
        raise LLMUnavailable(_reason(err)) from err

    if msg.stop_reason == "refusal":
        raise LLMUnavailable("模型拒絕處理這段輸入")
    if msg.stop_reason == "max_tokens":
        raise LLMUnavailable("模型輸出被截斷")
    try:
        raw = json.loads(_last_text(msg))
    except json.JSONDecodeError as err:
        raise LLMUnavailable("模型回傳的格式無法解析") from err

    items = []
    for it in raw.get("items", []):
        kind = it.get("kind", "other")
        pid = it.get("match_id") or None
        if pid and id_kind.get(pid) != kind:
            pid = None
        items.append({"kind": kind, "text": str(it.get("text", "")).strip(),
                      "price": int(it.get("price") or 0) or None,
                      "match_id": pid, "match": it.get("match") if pid else None})
    return {"items": items, "stated_total": int(raw.get("stated_total") or 0) or None}


def extract_quote(text: str, data: dict, client=None) -> dict:
    """先試 LLM，不行就退回規則式。多帶 source 與 fallback_reason 兩個欄位。"""
    if client is not None:
        try:
            return {**extract_quote_with_llm(text, data, client), "source": "llm", "fallback_reason": None}
        except LLMUnavailable as err:
            return {**quote.extract_rules(text), "source": "rules", "fallback_reason": str(err)}
    return {**quote.extract_rules(text), "source": "rules", "fallback_reason": None}


# ================================================================ 輸出端：解說

_EXPLAIN_SYSTEM = """\
你是電腦組裝顧問，在一個課堂展示用的推薦系統裡，向不熟硬體的使用者解說這次的推薦結果。

系統的推薦方式：
1. 起點：先找「效能分 ÷ 價格」最高的組合，也就是最划算的一台。
2. 升級：再用剩下的預算，每一步挑「邊際 CP 值」（每多花一千元增加的分數）最高的升級，直到預算用完。
3. 同分最省：升級停下來後，若有分數不低、總價更低的組合就換過去，省下的錢再繼續升級，所以最終配置是達到該分數的最低花費。
4. 效能分是 0 到 100 的加權合成分，權重依用途而定，不是實測幀數。

寫法：
- 繁體中文、台灣用語，口語但精確，約 250 到 400 字。
- Markdown，固定四段，每段以粗體小標開頭：**一句話結論**、**錢主要花在哪**、**為什麼這樣配**、**你可能要注意的**。
- 價格、分數、瓦數、型號只能取自使用者訊息裡的資料，不要補上市價、FPS、跑分或資料裡沒有的型號。
- 有原始需求時，解說要對應回那句話；需求裡系統沒處理到的部分，在最後一段誠實說明。【搜尋條件】是實際採用的條件，與原始需求不一致時以它為準。
- 「純追分最佳解」比升級路徑高時，用一兩句說明這是逐步升級法的取捨即可。
- 用途是文書上網、預算卻明顯偏高時，提醒系統會盡量把預算花完，文書其實用不到這麼多錢。
- 開頭不寒暄，結尾不提問。"""


def _fmt_build(b: dict) -> str:
    return (f"CPU {b['cpu']['name']}／顯示卡 {b['gpu']['name']}／記憶體 {b['ram']['name']}"
            f"／SSD {b['ssd']['name']}／主機板 {b['mb']['name']}／電源 {b['psu']['name']}")


def build_context(result: dict, usage: str, budget: int, require_dgpu: bool,
                  cpu_brand: str = None, request: str = None, notes: str = None,
                  adjusted: bool = False) -> str:
    """把 analyze() 的結果攤成一份純文字摘要，當成 LLM 解說的唯一資料來源。"""
    base, steps, final = result["base"], result["steps"], result["final"]
    w = WEIGHTS[usage]
    lines = []

    if request:
        lines += ["【使用者原始需求】", request]
        if notes:
            lines.append(f"（解析備註：{notes}）")
        if adjusted:
            lines.append("（使用者之後在側邊欄手動改過條件）")
        lines.append("")

    lines += [
        "【搜尋條件】",
        f"預算上限 {budget:,} 元｜用途 {usage}｜必須獨顯 {'是' if require_dgpu else '否'}"
        f"｜CPU 品牌 {cpu_brand or '不限'}",
        f"用途權重：顯示卡 {w['gpu']}、CPU 單核 {w['cpu_single']}、CPU 多核 {w['cpu_multi']}、"
        f"記憶體 {w['ram']}、固態硬碟 {w['ssd']}",
        "",
        "【起點：CP 值最高的組合】",
        f"總價 {base['price']:,} 元，效能分 {base['score']:.1f}，CP 值 {base['cp']:.2f}（每千元換到的分數）",
        _fmt_build(base),
        "",
        "【升級路徑】（每步挑邊際 CP 值最高者；「同分最省」是換成分數不低、總價更低的組合）",
    ]
    if steps:
        for i, s in enumerate(steps, 1):
            if s["kind"] == "trim":
                psu = "，電源也跟著更換" if s["psu_changed"] else ""
                lines.append(
                    f"第 {i} 步 同分最省（{s['label']}）：{s['from']} → {s['to']}，省下 {-s['d_price']:,} 元，"
                    f"分數 {s['d_score']:+.1f}{psu}，累計 {s['total_price']:,} 元"
                )
                continue
            psu = "，電源也跟著加大" if s["psu_changed"] else ""
            lines.append(
                f"第 {i} 步 {s['label']}：{s['from']} → {s['to']}，追加 {s['d_price']:,} 元，"
                f"+{s['d_score']:.1f} 分，邊際 CP 值 {s['marginal_cp']:.2f}{psu}，累計 {s['total_price']:,} 元"
            )
    else:
        lines.append("沒有升級：起點已用掉大部分預算，剩下的錢不夠任何一項升級。")

    parts = [("CPU", final["cpu"]), ("主機板", final["mb"]), ("記憶體", final["ram"]),
             ("顯示卡", final["gpu"]), ("固態硬碟", final["ssd"]), ("電源", final["psu"])]
    lines += [
        "",
        f"【最終配置】總價 {final['price']:,} 元（剩 {budget - final['price']:,} 元），"
        f"效能分 {final['score']:.1f} / 100，CP 值 {final['cp']:.2f}",
    ]
    for kind, p in parts:
        lines.append(f"- {kind}：{p['name']}，{p['price']:,} 元（{p['price'] / final['price'] * 100:.0f}%）")
    lines.append(f"- {FIXED_COST_LABEL}：{FIXED_COST:,} 元")
    lines.append(
        "各項正規化分數（同類最強 = 100）："
        f"CPU 單核 {final['cpu']['single_norm']:.0f}、CPU 多核 {final['cpu']['multi_norm']:.0f}、"
        f"顯示卡 {final['gpu']['gpu_norm']:.0f}、記憶體 {final['ram']['ram_norm']:.0f}、"
        f"固態硬碟 {final['ssd']['ssd_norm']:.0f}"
    )

    checks = check_build(final)
    lines += ["", "【相容性檢查】"]
    lines += [f"{'通過' if c['ok'] else '不通過'}：{c['rule']}（{c['detail']}）" for c in checks]

    top = result["top"][0]
    lines += [
        "",
        "【對照：同預算下純追分最高的組合】",
        f"效能分 {top['score']:.1f}，總價 {top['price']:,} 元，"
        f"比升級路徑多 {top['score'] - final['score']:.1f} 分",
    ]

    if result["next_up"]:
        lines += ["", "【如果預算再多一點】（超出目前預算）"]
        for o in result["next_up"][:3]:
            lines.append(f"- {o['label']} 換成 {o['to']}：追加 {o['d_price']:,} 元，"
                         f"+{o['d_score']:.1f} 分，邊際 CP 值 {o['marginal_cp']:.2f}")

    return "\n".join(lines)


def explain_stream(context: str, client):
    """逐段產生 Claude 的解說文字，給 /api/explain 串流用。失敗時丟出 LLMUnavailable。"""
    try:
        with client.beta.messages.stream(
            model=MODEL,
            max_tokens=16000,
            betas=_BETAS,
            fallbacks="default",
            output_config={"effort": "medium"},
            system=_EXPLAIN_SYSTEM,
            messages=[{"role": "user", "content": "以下是這次的推薦結果，請解說。\n\n" + context}],
        ) as stream:
            yield from stream.text_stream
            final = stream.get_final_message()
    except Exception as err:
        raise LLMUnavailable(_reason(err)) from err
    if final.stop_reason == "refusal":
        raise LLMUnavailable("模型拒絕產生解說")


def explain_rules(result: dict, usage: str, budget: int) -> str:
    """模板版解說，LLM 不能用時的備援。只做數字整理，不做推論。"""
    base, steps, final = result["base"], result["steps"], result["final"]
    parts = sorted(
        [("顯示卡", final["gpu"]), ("CPU", final["cpu"]), ("主機板", final["mb"]),
         ("記憶體", final["ram"]), ("固態硬碟", final["ssd"]), ("電源", final["psu"])],
        key=lambda kp: -kp[1]["price"],
    )
    spend = "、".join(f"{k} {p['price']:,} 元（{p['price'] / final['price'] * 100:.0f}%）"
                     for k, p in parts[:3] if p["price"] > 0)

    if steps:
        ups = [s for s in steps if s["kind"] != "trim"]
        trims = [s for s in steps if s["kind"] == "trim"]
        labels = "、".join(dict.fromkeys(s["label"] for s in ups))
        why = (f"起點是 CP 值最高的 {base['price']:,} 元組合（{base['score']:.1f} 分），"
               f"之後用剩下的預算升級了 {len(ups)} 步，依序動到：{labels}。")
        if trims:
            why += (f"途中做了 {len(trims)} 次同分最省，換成分數不低、總價更低的組合，"
                    f"共省下 {-sum(s['d_price'] for s in trims):,} 元。")
        why += f"總共多花 {final['price'] - base['price']:,} 元、加了 {final['score'] - base['score']:.1f} 分。"
    else:
        why = f"CP 值最高的組合（{base['price']:,} 元）已經用掉大部分預算，剩下的錢不夠任何一項升級。"

    notes = []
    gap = result["top"][0]["score"] - final["score"]
    if gap > 0.05:
        notes.append(f"同預算純追分最高可到 {result['top'][0]['score']:.1f} 分，比這台多 {gap:.1f} 分，"
                     "這是逐步升級法只看眼前一步的代價。")
    if usage == "文書上網" and budget >= 30000:
        notes.append("系統會盡量把預算花完；文書上網其實用不到這麼高的預算。")
    notes.append("效能分是加權合成的參考分數，不是實測幀數。")

    return (
        f"**一句話結論**　這台 {final['price']:,} 元的「{usage}」主機，效能分 "
        f"{final['score']:.1f} / 100，預算還剩 {budget - final['price']:,} 元。\n\n"
        f"**錢主要花在哪**　{spend}。\n\n"
        f"**為什麼這樣配**　{why}\n\n"
        f"**你可能要注意的**　{''.join(notes)}"
    )
