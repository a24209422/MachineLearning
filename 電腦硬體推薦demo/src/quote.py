# -*- coding: utf-8 -*-
"""估價單健檢：把一份現成的菜單（ChatGPT、店員、論壇開的都行）拿來體檢。

流程：
    1. 拆行 -- 把貼上的文字拆成「類別 / 原文 / 標價」，並認出哪一行是總計。
               有 LLM 時由 LLM 負責（見 llm.extract_quote），否則用這裡的規則式版本。
    2. 對應 -- 每個零件對到資料庫裡的一筆。資料庫沒有的型號找最接近的估算，
               但腳位、記憶體世代、內顯、電源瓦數這些會影響相容性的規格，
               一律以菜單原文為準覆寫，避免「換成近似品之後問題就不見了」。
    3. 體檢 -- 加總對不對、相容性、效能分，再跟同一份價目表上的
               「同樣的錢最高分」「同樣的分數最便宜」兩組配置比較。

價格比較一律以資料庫快照計算：菜單標價只拿來檢查加總與價差，
兩邊用同一份價目表比，才不會因為報價時間不同而失真。
"""
import math
import re
import unicodedata

from .compat import check_build, display_ok, memory_ok, psu_ok, required_watt, socket_ok
from .loader import FIXED_COST
from .recommender import KIND_LABEL, score_build, search

KINDS = ["cpu", "mb", "ram", "gpu", "ssd", "psu"]   # 對應順序：後面的零件要參考前面的結果
OTHER_LABEL = "其他"

# ================================================================ 1. 拆行

_LABEL_WORDS = [
    ("total", ["總計", "合計", "總價", "總金額", "總額", "共計", "total"]),
    ("cpu", ["中央處理器", "處理器", "cpu"]),
    ("mb", ["主機板", "主板", "motherboard", "mobo", "mb"]),
    ("ram", ["記憶體", "內存", "memory", "ram"]),
    ("gpu", ["顯示卡", "顯卡", "graphics", "gpu", "vga"]),
    ("ssd", ["固態硬碟", "固態", "硬碟", "儲存", "storage", "ssd"]),
    ("psu", ["電源供應器", "電源", "power supply", "psu"]),
    ("other", ["機殼", "散熱", "風扇", "水冷", "case", "cooler", "作業系統", "windows",
               "螢幕", "鍵盤", "滑鼠", "網卡", "喇叭", "耳機", "組裝"]),
]

# 沒有類別標籤時，改看型號長相
_MODEL_PATTERNS = [
    ("gpu", r"\b(?:rtx|gtx)\s?\d{3,4}|\brx\s?\d{3,4}|radeon|geforce|內顯|無獨顯"),
    ("cpu", r"\bi[3579][- ]?\d{4,5}|ryzen|銳龍|core\s?ultra"),
    ("mb", r"(?<![a-z0-9])[abhxz][2-9]\d0e?m?(?![0-9])"),
    ("ram", r"ddr[45]"),
    ("psu", r"\d{3,4}\s*w\b|80\s*\+|80\s*plus|金牌|銅牌|白金"),
    ("ssd", r"nvme|m\.2|\bssd\b|\d+\s*tb\b|gen\s?[345]\b|pcie\s?[345]"),
]

_IGNORE = re.compile(r"預算|用途|說明|備註|建議")


def _norm(text: str) -> str:
    """全形轉半形、去掉 Markdown 記號與項目符號，方便比對。"""
    t = unicodedata.normalize("NFKC", text)
    t = re.sub(r"[*_`#>]+", " ", t)
    t = re.sub(r"^\s*(?:[-•‧·]|\d+[.)、])\s+", "", t)
    return t.strip()


def _label_kind(text: str, cjk_only: bool = False):
    low = text.lower()
    for kind, words in _LABEL_WORDS:
        for w in words:
            if w.isascii():
                if not cjk_only and re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", low):
                    return kind
            elif w in low:
                return kind
    return None


def _model_kind(text: str):
    low = text.lower()
    for kind, pat in _MODEL_PATTERNS:
        if re.search(pat, low):
            return kind
    return None


_UNIT_AFTER = r"(?!\s*(?:mb|gb|tb|w\b|mhz|mt|rpm|mm|g\b|t\b|吋|核|執行緒))"


def find_price(text: str):
    """從一段文字找出標價。只認帶幣別、千分位或單獨成欄的數字，避免把型號當成價格。"""
    t = text.lower().replace("ntd", "nt$")
    cands = [m.group(1) for m in re.finditer(r"(?:nt\$|\$|新台幣|台幣)\s*(\d[\d,]*)", t)]
    cands += [m.group(1) for m in re.finditer(r"(\d[\d,]*)\s*(?:元|塊)", t)]
    cands += [str(float(m.group(1)) * 10000) for m in re.finditer(r"(\d+(?:\.\d+)?)\s*萬", t)]
    if not cands:
        cands = [m.group(1) for m in re.finditer(rf"(?<![\w.,])(\d{{1,3}}(?:,\d{{3}})+){_UNIT_AFTER}(?![\w,])", t)]
    if not cands:   # 「— 5500」「: 5500」或表格裡單獨一欄的數字
        m = re.search(r"(?:\s[—–\-~]\s*|[:：|]\s*|\t|\s{2,}|約\s*)(\d{3,6})\s*$", t) or \
            re.fullmatch(r"\s*(\d{3,6})\s*", t)
        if m:
            cands.append(m.group(1))
    for c in cands:
        v = int(float(c.replace(",", "")))
        if 100 <= v <= 500000:
            return v
    return None


_RAM_SPEEDS = {2133, 2400, 2666, 2933, 3000, 3200, 3600, 4000, 4800, 5200, 5600, 6000, 6400, 6800, 7200, 8000}


def _trailing_price(body: str, kind: str):
    """「i5-13400F 5900」這種只用空白隔開的標價：拿掉結尾數字後，剩下的仍要認得出型號才算。

    這樣「RTX 4060」的 4060 不會被當成價格；記憶體行結尾的 3200、6000 則視為頻率。
    """
    m = re.search(r"(?<=\s)(\d{3,6})\s*$", body)
    pat = dict(_MODEL_PATTERNS).get(kind)
    if not m or pat is None or not re.search(pat, body[:m.start()].lower()):
        return None
    v = int(m.group(1))
    if kind == "ram" and v in _RAM_SPEEDS:
        return None
    return v if 100 <= v <= 500000 else None


def _strip_price(body: str, price) -> str:
    """把標價從零件原文裡拿掉，免得後面把價格誤認成記憶體頻率之類的規格。"""
    if price is None:
        return body
    digits = ",?".join(str(price))
    pat = (rf"(?:[—–\-~:：|]\s*)?(?:約\s*)?(?:nt\$|ntd|\$|新台幣|台幣)?\s*{digits}\s*(?:元|塊)?"
           r"(?:\s*(?:左右|上下))?")
    hits = list(re.finditer(pat, body, flags=re.I))
    if hits:
        m = hits[-1]
        body = body[:m.start()] + body[m.end():]
    body = re.sub(r"[(（]\s*[)）]", "", body).strip(" -–—|:：,，")
    return body.rstrip("(（ ")


def _split(raw: str):
    """回傳 (類別, 零件原文, 標價)；跟零件無關的行回傳 None。"""
    line = _norm(raw)
    if not line or set(line) <= set("|-:–— "):
        return None

    if "|" in line:                                           # Markdown 表格
        cells = [c.strip() for c in line.strip("|").split("|") if c.strip()]
        if not cells or all(set(c) <= set("-: ") for c in cells):
            return None
        kind = _label_kind(cells[0])
        price, body = None, []
        for c in (cells[1:] if kind else cells):
            p = find_price(c) if not _model_kind(c) else None
            if p is not None and price is None:
                price = p
            else:
                body.append(c)
        text = " ".join(body)
        kind = kind or _model_kind(text) or _label_kind(text, cjk_only=True)
        return (kind, text, price) if kind else None

    m = re.match(r"^([^:：]{1,16})[:：]\s*(.*)$", line)       # 「CPU：Ryzen 5 7500F」
    if m and _label_kind(m.group(1)):
        kind, body = _label_kind(m.group(1)), m.group(2)
    else:
        body = line
        lk = _label_kind(line, cjk_only=True)
        if lk == "total":
            kind = "total"
        elif _IGNORE.search(line):
            return None
        else:
            kind = _model_kind(line) or lk
            body = re.sub(r"^(?:cpu|mb|ram|gpu|vga|ssd|psu)\s+", "", body, flags=re.I)
    if kind is None:
        return None
    price = find_price(body) or _trailing_price(body, kind)
    return kind, _strip_price(body, price) or body, price


def extract_rules(text: str) -> dict:
    """規則式拆行。回傳 {"items": [{kind, text, price, match_id, match}], "stated_total": int|None}。"""
    items, stated = [], None
    for raw in text.splitlines():
        got = _split(raw)
        if got is None:
            continue
        kind, body, price = got
        if kind == "total":
            stated = price if price is not None else stated
            continue
        items.append({"kind": kind, "text": body, "price": price, "match_id": None, "match": None})
    return {"items": items, "stated_total": stated}


# ================================================================ 2. 對應

CHIPSET_SOCKET = {
    "a620": "AM5", "b840": "AM5", "b650": "AM5", "b650e": "AM5", "b850": "AM5",
    "x670": "AM5", "x670e": "AM5", "x870": "AM5", "x870e": "AM5",
    "a320": "AM4", "a520": "AM4", "b450": "AM4", "b550": "AM4", "x470": "AM4", "x570": "AM4",
    "h610": "LGA1700", "b660": "LGA1700", "b760": "LGA1700", "h670": "LGA1700",
    "h770": "LGA1700", "z690": "LGA1700", "z790": "LGA1700",
    "h810": "LGA1851", "b860": "LGA1851", "z890": "LGA1851",
    "h510": "LGA1200", "b560": "LGA1200", "z590": "LGA1200",
}
# 資料庫裡沒有的晶片組，對到定位最接近的那一款
CHIPSET_ALIAS = {
    "b840": "a620", "b650e": "b650", "b850": "b650", "x670": "x670e", "x870": "x670e", "x870e": "x670e",
    "a320": "a520", "b450": "b550", "x470": "b550", "x570": "b550",
    "b660": "b760", "h670": "b760", "h770": "b760", "z690": "z790",
    "h810": "h610", "b860": "b760", "z890": "z790",
    "h510": "h610", "b560": "b760", "z590": "z790",
}
# 資料庫沒有的平台，挑零件時拿哪個平台的來估價
SOCKET_STANDIN = {"LGA1851": "LGA1700", "LGA1200": "LGA1700"}
# 只支援單一記憶體世代的平台：主機板就算沒寫，也能確定它吃哪一種
PLATFORM_MEM = {"AM5": "DDR5", "AM4": "DDR4", "LGA1851": "DDR5", "LGA1200": "DDR4"}

# 資料庫沒有的新款顯示卡，直接指定效能相近的那一張（依命名規則推算會偏差太多）
GPU_STANDIN = {
    ("NVIDIA", 5050, ""): "GPU05", ("NVIDIA", 5060, ""): "GPU06", ("NVIDIA", 5060, "ti"): "GPU06",
    ("NVIDIA", 5070, ""): "GPU08", ("NVIDIA", 5070, "ti"): "GPU09", ("NVIDIA", 5080, ""): "GPU11",
    ("NVIDIA", 5090, ""): "GPU11", ("NVIDIA", 4070, "super"): "GPU08", ("NVIDIA", 4080, ""): "GPU11",
    ("NVIDIA", 4090, ""): "GPU11",
    ("AMD", 9060, "xt"): "GPU07", ("AMD", 9070, ""): "GPU10", ("AMD", 9070, "xt"): "GPU10",
    ("AMD", 7800, "xt"): "GPU08", ("AMD", 7900, "gre"): "GPU08", ("AMD", 7900, "xtx"): "GPU11",
    ("AMD", 6700, "xt"): "GPU05",
}


def _cpu_model(text: str):
    """解析 CPU 型號，回傳 {brand, tier, gen, sku, suffix, socket, igpu} 或 None。"""
    t = text.lower()
    m = re.search(r"ultra\s*([579])\s*(\d{3})([a-z]*)", t)
    if m:
        suf = m.group(3)
        return {"brand": "Intel", "tier": int(m.group(1)), "gen": 15, "sku": int(m.group(2)),
                "suffix": suf, "socket": "LGA1851", "igpu": "f" not in suf, "ultra": True}
    m = re.search(r"i([3579])[- ]?(\d{4,5})([a-z]{0,2})", t)
    if m:
        num, suf = m.group(2), m.group(3)
        gen = int(num[:-3])
        return {"brand": "Intel", "tier": int(m.group(1)), "gen": gen, "sku": int(num[-3:]),
                "suffix": suf, "socket": "LGA1700" if gen >= 12 else "LGA1200", "igpu": "f" not in suf}
    m = re.search(r"(?:ryzen|銳龍|r[3579])\s*([3579])?\s*(\d{4})(x3d|xt|x|gt|ge|g|f)?", t) or \
        re.search(r"(?<!\d)()([1-9]\d{3})(x3d|xt|x|gt|ge|g|f)(?![a-z0-9])", t)
    if m:
        num, suf = m.group(2), m.group(3) or ""
        gen, sku = int(num[0]), int(num[1:])
        tier = int(m.group(1)) if m.group(1) else (9 if sku >= 900 else 7 if sku >= 700 else 5 if sku >= 500 else 3)
        return {"brand": "AMD", "tier": tier, "gen": gen, "sku": sku, "suffix": suf,
                "socket": "AM5" if gen >= 7 else "AM4",
                "igpu": ("f" not in suf) if gen >= 7 else ("g" in suf)}   # 7000 起非 F 都有內顯；5000 只有 G 有
    return None


def _cpu_distance(q: dict, d: dict) -> float:
    gap = 10 * (q["brand"] != d["brand"]) + 5 * abs(q["tier"] - d["tier"]) + 0.5 * (q["suffix"] != d["suffix"])
    if q.get("ultra"):            # Core Ultra 的型號編法跟 i 系列不同，只看等級與後綴
        return gap
    return gap + 4 * (q["socket"] != d["socket"]) + abs(q["sku"] - d["sku"]) / 50 + abs(q["gen"] - d["gen"])


_NV_GEN = {9: 0, 10: 1, 16: 2, 20: 3, 30: 4, 40: 5, 50: 6}


def _gpu_model(text: str):
    t = text.lower()
    if re.search(r"內顯|無獨顯|不需要獨顯|integrated|igpu", t):
        return {"none": True}
    m = re.search(r"(rtx|gtx)\s*(\d{3,4})\s*(ti)?\s*(super)?", t)
    if m:
        n = int(m.group(2))
        return {"vendor": "NVIDIA", "num": n, "gen": _NV_GEN.get(n // 100, 7), "tier": n % 100 // 10,
                "suffix": (m.group(3) or "") + (m.group(4) or ""), "vram": _vram(t)}
    m = re.search(r"\brx\s*(\d{4})\s*(xtx|xt|gre)?", t)
    if m:
        n = int(m.group(1))
        tier = n % 100 // 10 if n % 100 else n % 1000 // 100      # RX 9070 看後兩位，RX 7600 看百位
        return {"vendor": "AMD", "num": n, "gen": n // 1000, "tier": tier,
                "suffix": m.group(2) or "", "vram": _vram(t)}
    return None


def _vram(t: str):
    m = re.search(r"(\d{1,2})\s*g(?:b)?(?![a-z0-9])", t)
    return int(m.group(1)) if m else None


def _gpu_distance(q: dict, d: dict) -> float:
    return (3 * (q["vendor"] != d["vendor"]) + 2 * abs(q["tier"] - d["tier"])
            + abs(q["gen"] - d["gen"]) + 0.5 * (q["suffix"] != d["suffix"]))


def _mb_model(text: str):
    t = text.lower()
    m = re.search(r"(?<![a-z0-9])([abhxz][2-9]\d0e?)(m)?(?![0-9])", t)
    mem = re.search(r"ddr([45])|(?<![a-z0-9])d([45])(?![a-z0-9])", t)          # 「B760M D4」也算
    return {"chipset": m.group(1) if m else None,
            "matx": bool(m and m.group(2)) or "matx" in t or "m-atx" in t,
            "mem": f"DDR{mem.group(1) or mem.group(2)}" if mem else None}


def _ram_model(text: str):
    t = text.lower()
    mem = re.search(r"ddr([45])", t)
    kit, spans = None, []                                     # 套裝寫法：16G x2、2x16G、(2x8)
    for m in re.finditer(r"(\d+)\s*g(?:b)?\s*[x×*]\s*(\d)(?!\d)", t):
        kit = int(m.group(1)) * int(m.group(2))
        spans.append(m.span())
    for m in re.finditer(r"(\d)\s*[x×*]\s*(\d+)\s*g?(?:b)?", t):
        if int(m.group(2)) in (4, 8, 16, 24, 32, 48, 64):
            kit = kit or int(m.group(1)) * int(m.group(2))
            spans.append(m.span())
    total = next((int(m.group(1)) for m in re.finditer(r"(\d+)\s*g(?:b)?(?![a-z0-9])", t)
                  if not any(a <= m.start() < b for a, b in spans)), None)
    speed = next((int(m.group(1)) for m in re.finditer(r"(?<!\d)(\d{4})(?!\d)", t)
                  if 2000 <= int(m.group(1)) <= 9600), None)
    return {"mem": f"DDR{mem.group(1)}" if mem else None, "cap": total or kit, "speed": speed}


def _ssd_gen(interface: str) -> int:
    return int(re.search(r"(\d)\.0", interface).group(1))


def _ssd_model(text: str):
    t = text.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*t(?:b)?(?![a-z])", t)
    if m:
        cap = int(float(m.group(1)) * 1000)
    else:
        m = re.search(r"(\d{3,4})\s*g(?:b)?(?![a-z])", t)
        cap = int(m.group(1)) if m else None
    gen = re.search(r"gen\s?([345])|pcie\s?([345])", t)
    read = re.search(r"(\d[\d,]{3,5})\s*mb/?s", t)
    read = int(read.group(1).replace(",", "")) if read else None
    return {"cap": cap, "gen": int(gen.group(1) or gen.group(2)) if gen else None,
            "fast": bool((read and read >= 6500) or "高速" in t), "sata": "sata" in t}


def _psu_model(text: str):
    t = text.lower()
    m = re.search(r"(\d{3,4})\s*w\b", t)
    eff = None
    if re.search(r"白金|platinum|鈦|titanium", t):
        eff = "80+ Platinum"
    elif re.search(r"金牌|gold", t):
        eff = "80+ Gold"
    elif re.search(r"銅牌|bronze", t):
        eff = "80+ Bronze"
    return {"watt": int(m.group(1)) if m else None, "eff": eff}


def _log_gap(a, b) -> float:
    return abs(math.log2(max(a, 1)) - math.log2(max(b, 1)))


# 每個 _match_* 回傳 (資料庫零件, 對應程度, 依原文覆寫的規格, 備註)

def _match_cpu(text, rows, price):
    q = _cpu_model(text)
    if q is None:
        if price is None:
            return None, "none", {}, "認不出 CPU 型號"
        row = min(rows, key=lambda r: abs(r["price"] - price))
        return row, "closest", {}, f"認不出型號，以價格相近的 {row['name']} 估算"
    parsed = [(r, d) for r in rows if (d := _cpu_model(r["name"]))]
    over = {"socket": q["socket"], "igpu": q["igpu"]}
    for r, d in parsed:
        if not q.get("ultra") and all(d[k] == q[k] for k in ("brand", "tier", "gen", "sku", "suffix")):
            return r, "exact", over, None
    row = min(parsed, key=lambda rd: _cpu_distance(q, rd[1]))[0]
    keep = "、".join(x for x in [
        f"腳位 {q['socket']}" if q["socket"] != row["socket"] else None,
        ("有內顯" if q["igpu"] else "無內顯") if bool(q["igpu"]) != bool(row["igpu"]) else None] if x)
    return row, "closest", over, f"資料庫沒有這顆，以同級的 {row['name']} 估算" + (f"（相容性依原文：{keep}）" if keep else "")


def _match_gpu(text, rows, price):
    q = _gpu_model(text)
    if q and q.get("none"):
        return next(r for r in rows if not r["discrete"]), "exact", {}, None
    dgpus = [r for r in rows if r["discrete"]]
    if q is None:
        if price is None:
            return None, "none", {}, "認不出顯示卡型號"
        row = min(dgpus, key=lambda r: abs(r["price"] - price))
        return row, "closest", {}, f"認不出型號，以價格相近的 {row['name']} 估算"
    parsed = [(r, d) for r in dgpus if (d := _gpu_model(r["name"]))]
    for r, d in parsed:
        if all(d[k] == q[k] for k in ("vendor", "gen", "tier", "suffix")):
            if q["vram"] in (None, r["vram_gb"]):
                return r, "exact", {}, None
            return r, "closest", {}, f"資料庫只有 {r['vram_gb']}GB 版本，以它估算"
    row = _by_id(dgpus, GPU_STANDIN.get((q["vendor"], q["num"], q["suffix"])))
    if row is not None:
        return row, "closest", {}, f"資料庫沒有這張，以效能相近的 {row['name']} 估算"
    row = min(parsed, key=lambda rd: (_gpu_distance(q, rd[1]), abs(rd[0]["price"] - price) if price else 0))[0]
    return row, "closest", {}, f"資料庫沒有這張，以同級的 {row['name']} 估算"


def _match_mb(text, rows, cpu, ram_hint):
    """主機板支援哪一代記憶體，只看主機板自己那一行與平台，不參考菜單上的記憶體，
    否則「B650 配 DDR4」這種錯誤會被自己的記憶體行合理化掉。ram_hint 只用來在同晶片組裡挑款。"""
    q = _mb_model(text)
    if q["chipset"]:
        real = q["chipset"]
        alias = CHIPSET_ALIAS.get(real, real)
        same = [r for r in rows if r["chipset"].lower() == alias]
        if same:
            socket = CHIPSET_SOCKET.get(real, same[0]["socket"])
            mem = PLATFORM_MEM.get(socket) or q["mem"]
            prefer = mem or ram_hint
            row = min(same, key=lambda r: ((prefer is not None and r["mem_type"] != prefer),
                                           (r["form_factor"] == "mATX") != q["matx"], r["price"]))
            over = {"socket": socket, "mem_type": mem or row["mem_type"]}
            if alias == real and over["mem_type"] == row["mem_type"]:
                return row, "exact", over, None
            keep = "、".join(x for x in [
                f"腳位 {socket}" if socket != row["socket"] else None,
                f"支援 {mem}" if mem and mem != row["mem_type"] else None] if x)
            name = real.upper() if alias != real else f"{real.upper()} {mem} 版"
            return row, "closest", over, f"資料庫沒有 {name}，以同級的 {row['name']} 估算" + (f"（相容性依原文：{keep}）" if keep else "")
    # 認不出晶片組：挑跟 CPU 相容的最便宜一張
    mem = PLATFORM_MEM.get(cpu["socket"]) or q["mem"]
    row = _cheapest_mb(rows, cpu["socket"], mem or ram_hint)
    over = {"socket": cpu["socket"], "mem_type": mem or row["mem_type"]}
    return row, "closest", over, f"認不出晶片組，以相容的 {row['name']} 估算"


def _cheapest_mb(rows, socket, mem):
    socket = socket if any(r["socket"] == socket for r in rows) else SOCKET_STANDIN.get(socket, socket)
    pool = [r for r in rows if r["socket"] == socket] or rows
    return min(pool, key=lambda r: ((mem is not None and r["mem_type"] != mem), r["price"]))


def _match_ram(text, rows, mb):
    q = _ram_model(text)
    mem = q["mem"] or mb["mem_type"]
    pool = [r for r in rows if r["mem_type"] == mem] or rows
    cap, speed = q["cap"], q["speed"]
    row = min(pool, key=lambda r: (3 * _log_gap(cap, r["capacity_gb"]) if cap else 0)
              + (abs(speed - r["speed_mhz"]) / 400 if speed else 0) + r["price"] / 1e6)
    over = {"mem_type": mem}
    if q["mem"] and cap == row["capacity_gb"] and speed in (None, row["speed_mhz"]):
        return row, "exact", over, None
    return row, "closest", over, f"以規格最接近的 {row['name']} 估算"


def _match_ssd(text, rows):
    q = _ssd_model(text)
    cap, gen = q["cap"], q["gen"]
    row = min(rows, key=lambda r: (3 * _log_gap(cap, r["capacity_gb"]) if cap else 0)
              + (abs(gen - _ssd_gen(r["interface"])) if gen else 0)
              + 0.5 * (q["fast"] != ("高速" in r["name"])) + r["price"] / 1e6)
    if (cap == row["capacity_gb"] and gen in (None, _ssd_gen(row["interface"]))
            and not q["sata"] and q["fast"] == ("高速" in row["name"])):
        return row, "exact", {}, None
    why = "資料庫沒有 SATA SSD，" if q["sata"] else ""
    return row, "closest", {}, f"{why}以規格最接近的 {row['name']} 估算"


def _match_psu(text, rows):
    q = _psu_model(text)
    watt, eff = q["watt"], q["eff"]
    if watt is None:
        row = min(rows, key=lambda r: ((eff is not None and r["efficiency"] != eff), r["price"]))
        return row, "closest", {}, f"認不出瓦數，以 {row['name']} 估算"
    same = [r for r in rows if r["watt"] == watt]
    if same:
        row = min(same, key=lambda r: ((eff is not None and r["efficiency"] != eff), r["price"]))
        if eff in (None, row["efficiency"]):
            return row, "exact", {}, None
        return row, "closest", {}, f"以同瓦數的 {row['name']} 估算"
    # 瓦數對不上時價格往小估，相容性檢查則一律用原文的瓦數
    lower = [r for r in rows if r["watt"] <= watt]
    row = max(lower, key=lambda r: (r["watt"], r["efficiency"] == eff)) if lower else min(rows, key=lambda r: r["watt"])
    return row, "closest", {"watt": watt}, f"資料庫沒有 {watt}W，以 {row['name']} 估算價格（瓦數依原文 {watt}W）"


def _cheapest_psu(rows, cpu, gpu):
    need = required_watt(cpu, gpu)
    ok = [r for r in rows if r["watt"] >= need]
    return min(ok, key=lambda r: r["price"]) if ok else max(rows, key=lambda r: r["watt"])


def _by_id(rows, pid):
    return next((r for r in rows if r["id"] == pid), None) if pid else None


def _filled(kind, row, note):
    return {"kind": kind, "label": KIND_LABEL[kind], "text": "", "quoted_price": None,
            "match": "filled", "row": row, "note": note}


def resolve(extracted: dict, data: dict):
    """把拆好的行對應到資料庫零件。回傳 (配置, 逐項紀錄)；認不出 CPU 時配置為 None。

    配置裡的零件是資料庫那一筆的複本，再依原文覆寫腳位、記憶體世代、內顯與瓦數。
    """
    rows = {k: data[k].to_dict("records") for k in KINDS}
    primary, records = {}, []
    for it in extracted["items"]:
        kind = it["kind"] if it["kind"] in KINDS else "other"
        rec = {"kind": kind, "label": KIND_LABEL.get(kind, OTHER_LABEL), "text": it["text"],
               "quoted_price": it["price"], "match": "other", "row": None, "note": None,
               "llm_id": it.get("match_id"), "llm_match": it.get("match")}
        if kind != "other":
            if kind in primary:
                rec.update(match="extra", note="同類零件只評估第一項，這一項不列入效能與相容性計算")
            else:
                primary[kind] = rec
        records.append(rec)

    build = {}

    def settle(kind, matcher):
        rec = primary.get(kind)
        if rec is None:
            return
        row, level, over, note = matcher(rec)
        picked = _by_id(rows[kind], rec["llm_id"])
        if picked is not None and rec["llm_match"] in ("exact", "closest"):
            # LLM 選的零件優先；覆寫的規格仍取自原文，LLM 誤判成完全相同也不會掩蓋相容性問題
            level = rec["llm_match"]
            if level == "exact":
                note = None
            elif picked["id"] != (row["id"] if row else None) or note is None:
                note = f"資料庫沒有完全相同的型號，以 {picked['name']} 估算"
            row = picked
        if row is None:
            rec.update(match="none", note=note)
            return
        rec.update(match=level, row=row, note=note)
        build[kind] = {**row, **over}

    settle("cpu", lambda r: _match_cpu(r["text"], rows["cpu"], r["quoted_price"]))
    if "cpu" not in build:
        return None, records

    ram_hint = _ram_model(primary["ram"]["text"])["mem"] if "ram" in primary else None
    settle("mb", lambda r: _match_mb(r["text"], rows["mb"], build["cpu"], ram_hint))
    if "mb" not in build:
        row = _cheapest_mb(rows["mb"], build["cpu"]["socket"], ram_hint)
        build["mb"] = {**row, "socket": build["cpu"]["socket"]}
        records.append(_filled("mb", row, "菜單沒有列出主機板，以相容的最便宜一張估算"))

    settle("ram", lambda r: _match_ram(r["text"], rows["ram"], build["mb"]))
    if "ram" not in build:
        row = min((r for r in rows["ram"] if r["mem_type"] == build["mb"]["mem_type"]), key=lambda r: r["price"])
        build["ram"] = row
        records.append(_filled("ram", row, "菜單沒有列出記憶體，以相容的最便宜一組估算"))

    settle("gpu", lambda r: _match_gpu(r["text"], rows["gpu"], r["quoted_price"]))
    if "gpu" not in build:
        row = next(r for r in rows["gpu"] if not r["discrete"])
        build["gpu"] = row
        note = ("菜單沒有列出顯示卡，視為使用 CPU 內顯" if build["cpu"]["igpu"]
                else "菜單沒有列出顯示卡，而這顆 CPU 沒有內顯，接上螢幕不會有畫面")
        records.append(_filled("gpu", row, note))

    settle("ssd", lambda r: _match_ssd(r["text"], rows["ssd"]))
    if "ssd" not in build:
        row = min(rows["ssd"], key=lambda r: r["price"])
        build["ssd"] = row
        records.append(_filled("ssd", row, "菜單沒有列出 SSD，以最便宜的一顆估算"))

    settle("psu", lambda r: _match_psu(r["text"], rows["psu"]))
    if "psu" not in build:
        row = _cheapest_psu(rows["psu"], build["cpu"], build["gpu"])
        build["psu"] = row
        records.append(_filled("psu", row, "菜單沒有列出電源，以瓦數足夠的最便宜一顆估算"))

    return build, records


# ================================================================ 3. 體檢

_ALL_BUILDS = {}


def all_builds(data: dict, usage: str, require_dgpu: bool) -> list:
    """不設預算的完整搜尋結果。資料是靜態快照，同樣的條件只算一次。"""
    key = (id(data), usage, require_dgpu)
    if key not in _ALL_BUILDS:
        _ALL_BUILDS[key] = search(data, usage, None, require_dgpu)[0]
    return _ALL_BUILDS[key]


def _price_of(build: dict) -> int:
    return int(sum(build[k]["price"] for k in KINDS) + FIXED_COST)


def _passes(b: dict) -> bool:
    return (socket_ok(b["cpu"], b["mb"]) and memory_ok(b["mb"], b["ram"])
            and psu_ok(b["cpu"], b["gpu"], b["psu"]) and display_ok(b["cpu"], b["gpu"]))


_SPEC_KEYS = ("id", "socket", "mem_type", "igpu", "watt")


def _same_part(a: dict, b: dict) -> bool:
    """同一筆資料庫零件、而且覆寫過的規格也相同，才算沒換。"""
    return all(a.get(k) == b.get(k) for k in _SPEC_KEYS)


def _alt(b: dict, quote: dict, q_price: int, q_score: float) -> dict:
    return {
        "price": int(b["price"]), "score": float(b["score"]), "cp": float(b["cp"]),
        "d_price": int(b["price"] - q_price), "d_score": float(b["score"] - q_score),
        "parts": [{"kind": k, "label": KIND_LABEL[k], "name": str(b[k]["name"]), "price": int(b[k]["price"]),
                   "changed": not _same_part(b[k], quote[k])} for k in KINDS],
    }


def minimal_fix(data: dict, quote: dict, names: dict, usage: str):
    """組不起來時，找「動到最少零件、不掉分、多花最少錢」的修正。最多換兩個零件，電源視需要自動加大。"""
    rows = {k: data[k].to_dict("records") for k in KINDS}
    psus = sorted(rows["psu"], key=lambda r: r["price"])
    base_score = score_build(quote["cpu"], quote["gpu"], quote["ram"], quote["ssd"], usage)
    base_price = _price_of(quote)

    change_sets = [{}]                                        # {} = 只加大電源
    change_sets += [{k: r} for k in ("cpu", "mb", "ram", "gpu", "psu") for r in rows[k]]
    change_sets += [{"mb": m, "ram": r} for m in rows["mb"] for r in rows["ram"]]
    change_sets += [{"cpu": c, "mb": m} for c in rows["cpu"] for m in rows["mb"] if c["socket"] == m["socket"]]

    best = None
    for changes in change_sets:
        b = {**quote, **changes}
        if not psu_ok(b["cpu"], b["gpu"], b["psu"]):
            need = required_watt(b["cpu"], b["gpu"])
            b["psu"] = next((p for p in psus if p["watt"] >= need), None)
            if b["psu"] is None:
                continue
        if not _passes(b):
            continue
        changed = [k for k in KINDS if not _same_part(b[k], quote[k])]
        if not changed:
            continue
        score = score_build(b["cpu"], b["gpu"], b["ram"], b["ssd"], usage)
        price = _price_of(b)
        key = (len(changed), score < base_score - 0.5, price, -score)
        if best is None or key < best[0]:
            best = (key, b, changed, price, score)
    if best is None:
        return None
    _, b, changed, price, score = best
    return {
        "changes": [f"{KIND_LABEL[k]}：{names[k]} → {b[k]['name']}" for k in changed],
        "d_price": int(price - base_price), "d_score": float(score - base_score),
        "price": int(price), "score": float(score),
    }


def _spaced(text: str) -> str:
    """英數字與中文相接處補一個空白：「漏列CPU」->「漏列 CPU」。"""
    return re.sub(r"(?<=[A-Za-z0-9])(?=[一-鿿])|(?<=[一-鿿])(?=[A-Za-z0-9])", " ", text)


def _item(r: dict) -> dict:
    row = r["row"]
    return {"kind": r["kind"], "label": r["label"], "text": r["text"], "quoted_price": r["quoted_price"],
            "match": r["match"], "part": str(row["name"]) if row is not None else None,
            "part_id": str(row["id"]) if row is not None else None,
            "db_price": int(row["price"]) if row is not None else None, "note": r["note"]}


def evaluate(data: dict, build: dict, records: list, stated_total, usage: str) -> dict:
    """對已對應好的菜單做完整體檢，回傳給 API 用的 dict。"""
    checks = check_build(build)
    compatible = all(c["ok"] for c in checks)
    score = score_build(build["cpu"], build["gpu"], build["ram"], build["ssd"], usage)
    db_core = int(sum(build[k]["price"] for k in KINDS))
    db_total = db_core + FIXED_COST

    priced = [r["quoted_price"] for r in records if r["quoted_price"] is not None]
    core = [r["quoted_price"] for r in records if r["match"] in ("exact", "closest") and r["quoted_price"] is not None]
    listed_sum = sum(priced) if priced else None

    findings = [{"level": "error", "title": f"不相容：{c['rule']}", "detail": c["detail"]}
                for c in checks if not c["ok"]]
    for r in records:
        if r["match"] == "filled" and r["kind"] == "gpu" and build["cpu"]["igpu"]:
            findings.append({"level": "info", "title": "使用內顯", "detail": r["note"]})   # 文書機不配獨顯很正常
        elif r["match"] == "filled":
            findings.append({"level": "warn", "title": f"漏列{r['label']}", "detail": r["note"]})
        elif r["match"] == "none":
            findings.append({"level": "warn", "title": f"認不出{r['label']}", "detail": f"「{r['text']}」：{r['note']}"})

    if stated_total and listed_sum and abs(stated_total - listed_sum) > max(100, listed_sum * 0.01):
        diff = listed_sum - stated_total
        findings.append({"level": "warn", "title": "總計算錯了",
                         "detail": f"菜單寫總計 {stated_total:,} 元，逐項加起來是 {listed_sum:,} 元，"
                                   f"{'少算' if diff > 0 else '多算'}了 {abs(diff):,} 元。"})

    for r in records:
        if r["match"] == "exact" and r["quoted_price"]:
            db = int(r["row"]["price"])
            gap = (r["quoted_price"] - db) / db
            if abs(gap) >= 0.15:
                findings.append({"level": "info", "title": f"{r['label']}標價與資料庫差 {abs(gap) * 100:.0f}%",
                                 "detail": f"{r['row']['name']}：菜單 {r['quoted_price']:,} 元，資料庫快照 {db:,} 元。"})

    need = required_watt(build["cpu"], build["gpu"])
    if build["psu"]["watt"] >= max(750, need * 2.2):
        findings.append({"level": "info", "title": "電源瓦數遠超需求",
                         "detail": f"這組配置建議 {need}W 以上，菜單用了 {build['psu']['watt']}W；"
                                   "降一級省下的錢可以挪去顯示卡。"})

    for r in records:
        if r["match"] == "closest":
            findings.append({"level": "info", "title": f"{r['label']}以近似型號估算", "detail": r["note"]})

    require_dgpu = bool(build["gpu"]["discrete"])
    pool = all_builds(data, usage, require_dgpu)
    best = max((b for b in pool if b["price"] <= db_total), key=lambda b: (b["score"], -b["price"]), default=None)
    cheap = min((b for b in pool if b["score"] >= score - 1e-9), key=lambda b: (b["price"], -b["score"]), default=None)
    same_price = _alt(best, build, db_total, score) if best and best["score"] - score >= 0.5 else None
    same_score = _alt(cheap, build, db_total, score) if cheap and db_total - cheap["price"] >= 300 else None

    names = {k: str(build[k]["name"]) for k in KINDS}
    for r in records:
        if r["kind"] in KINDS and r["match"] == "closest" and r["text"]:
            names[r["kind"]] = r["text"]
    fix = None if compatible else minimal_fix(data, build, names, usage)

    if not compatible:
        verdict, headline = "fail", "這份菜單組不起來"
    elif (same_price and same_price["d_score"] >= 2) or (same_score and -same_score["d_price"] >= 1000):
        verdict, headline = "improvable", "組得起來，但同樣的錢可以配得更好"
    else:
        verdict, headline = "good", "配置合理，已接近同價位最佳解"
        findings.append({"level": "good", "title": "找不到明顯更好的組合",
                         "detail": "在同一份價目表上，沒有同樣的錢明顯更高分、或同樣分數明顯更便宜的配置。"})

    order = {"error": 0, "warn": 1, "info": 2, "good": 3}
    findings.sort(key=lambda f: order[f["level"]])
    for f in findings:
        f["title"] = _spaced(f["title"])

    return {
        "usage": usage, "verdict": verdict, "headline": headline,
        "items": [_item(r) for r in records],
        "totals": {"stated": stated_total, "listed_sum": listed_sum,
                   "listed_core": sum(core) if core else None, "db_core": db_core, "db_total": db_total},
        "score": float(score), "cp": float(score / (db_total / 1000.0)), "require_dgpu": require_dgpu,
        "checks": [{"rule": c["rule"], "ok": bool(c["ok"]), "detail": c["detail"]} for c in checks],
        "findings": findings, "fix": fix, "same_price": same_price, "same_score": same_score,
    }


def check(extracted: dict, usage: str, data: dict) -> dict:
    """拆好的菜單 -> 體檢結果。認不出 CPU 時只回傳 error 與逐項紀錄。"""
    build, records = resolve(extracted, data)
    if build is None:
        found = any(r["kind"] == "cpu" for r in records)
        msg = ("認不出菜單裡的 CPU 型號，無法評估。" if found else
               "沒有在菜單裡找到 CPU，無法評估。請確認貼上的內容包含處理器型號（例如 i5-13400F、Ryzen 5 7500F）。")
        return {"usage": usage, "error": msg, "items": [_item(r) for r in records]}
    return evaluate(data, build, records, extracted.get("stated_total"), usage)
