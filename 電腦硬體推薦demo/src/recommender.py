# -*- coding: utf-8 -*-
"""組合搜尋、評分，以及「先挑最划算、再逐步升級」的推薦流程。

搜尋策略是「分層剪枝」而不是暴力全窮舉：
    CPU x 主機板  先用腳位過濾   -> 只剩少數合法配對
      x RAM       再用記憶體世代過濾
      x GPU x SSD 才真正展開
電源不進迴圈：因為電源對效能分數的貢獻是 0，在瓦數足夠的前提下
永遠是越便宜越好，所以直接挑「滿足需求中最便宜的一顆」即為最佳解。

推薦流程分兩段：
    第一段 起點 -- 取 CP 值（效能分 / 價格）最高的組合，這是最划算的一台。
    第二段 升級 -- CP 值最佳解跟預算無關，給它多少錢它都推薦同一台，
                   所以剩下的預算改用「邊際 CP 值」逐步花掉，湊到預算上限。
"""
from .compat import display_ok, memory_ok, required_watt, socket_ok
from .loader import FIXED_COST

USAGES = ["遊戲", "剪輯創作", "文書上網"]

# 各用途的效能權重，每列加總為 1.0
WEIGHTS = {
    "遊戲":     {"gpu": 0.55, "cpu_single": 0.25, "cpu_multi": 0.05, "ram": 0.10, "ssd": 0.05},
    "剪輯創作": {"gpu": 0.35, "cpu_single": 0.10, "cpu_multi": 0.35, "ram": 0.15, "ssd": 0.05},
    "文書上網": {"gpu": 0.10, "cpu_single": 0.35, "cpu_multi": 0.10, "ram": 0.20, "ssd": 0.25},
}

# 只有這四類零件會影響效能分數；主機板與電源純粹是相容性與成本
SCORING_KINDS = ["cpu", "gpu", "ram", "ssd"]
KIND_LABEL = {"cpu": "CPU", "gpu": "顯示卡", "ram": "記憶體", "ssd": "固態硬碟",
              "mb": "主機板", "psu": "電源"}


def score_build(cpu: dict, gpu: dict, ram: dict, ssd: dict, usage: str) -> float:
    """依用途權重把各零件的正規化分數加權相加，滿分 100。"""
    w = WEIGHTS[usage]
    return (
        w["gpu"] * gpu["gpu_norm"]
        + w["cpu_single"] * cpu["single_norm"]
        + w["cpu_multi"] * cpu["multi_norm"]
        + w["ram"] * ram["ram_norm"]
        + w["ssd"] * ssd["ssd_norm"]
    )


def _cheapest_psu(psu_by_price: list, need_watt: int):
    """在瓦數足夠的電源裡挑最便宜的；都不夠則回傳 None。"""
    for psu in psu_by_price:
        if psu["watt"] >= need_watt:
            return psu
    return None


def search(data: dict, usage: str, budget=None, require_dgpu: bool = False,
           cpu_brand: str = None):
    """列舉所有合法且在預算內的配置。

    budget 傳 None 代表不設預算上限（用來回答「最低可行預算是多少」）。
    回傳 (配置清單, 統計資訊)。
    """
    cpus = data["cpu"]
    if cpu_brand:
        cpus = cpus[cpus["brand"] == cpu_brand]
    gpus = data["gpu"]
    if require_dgpu:
        gpus = gpus[gpus["discrete"]]

    cpu_rows = cpus.to_dict("records")
    mb_rows = data["mb"].to_dict("records")
    ram_rows = data["ram"].to_dict("records")
    gpu_rows = gpus.to_dict("records")
    ssd_rows = data["ssd"].to_dict("records")
    psu_by_price = sorted(data["psu"].to_dict("records"), key=lambda p: p["price"])

    builds = []
    stats = {"expanded": 0, "over_budget": 0, "no_psu": 0}
    psu_cache = {}
    cap = float("inf") if budget is None else budget

    for cpu in cpu_rows:
        for mb in mb_rows:
            if not socket_ok(cpu, mb):
                continue
            cost_a = cpu["price"] + mb["price"] + FIXED_COST
            if cost_a > cap:
                continue

            for ram in ram_rows:
                if not memory_ok(mb, ram):
                    continue
                cost_b = cost_a + ram["price"]
                if cost_b > cap:
                    continue

                for gpu in gpu_rows:
                    if not display_ok(cpu, gpu):
                        continue

                    need = required_watt(cpu, gpu)
                    if need not in psu_cache:
                        psu_cache[need] = _cheapest_psu(psu_by_price, need)
                    psu = psu_cache[need]
                    if psu is None:          # 資料庫裡沒有帶得動的電源
                        stats["no_psu"] += 1
                        continue

                    cost_c = cost_b + gpu["price"] + psu["price"]
                    if cost_c > cap:
                        continue

                    for ssd in ssd_rows:
                        stats["expanded"] += 1
                        price = cost_c + ssd["price"]
                        if price > cap:
                            stats["over_budget"] += 1
                            continue
                        score = score_build(cpu, gpu, ram, ssd, usage)
                        builds.append({
                            "cpu": cpu, "mb": mb, "ram": ram,
                            "gpu": gpu, "ssd": ssd, "psu": psu,
                            "price": int(price),
                            "score": score,
                            "cp": score / (price / 1000.0),   # 每千元換到幾分
                        })

    stats["valid"] = len(builds)
    return builds, stats


# --------------------------------------------------------------- 升級分析

def _make(base: dict, changes: dict, psu_by_price: list, usage: str):
    """套用一組零件異動，重挑電源並重算價格與分數。

    換 CPU 可能撞腳位、換 RAM 可能撞世代、換 GPU 可能需要更大的電源，
    這些都在這裡一併處理；任何一條規則不過就回傳 None。
    """
    b = dict(base)
    b.update(changes)
    if not socket_ok(b["cpu"], b["mb"]):
        return None
    if not memory_ok(b["mb"], b["ram"]):
        return None
    if not display_ok(b["cpu"], b["gpu"]):
        return None

    psu = _cheapest_psu(psu_by_price, required_watt(b["cpu"], b["gpu"]))
    if psu is None:
        return None
    b["psu"] = psu

    b["price"] = int(
        b["cpu"]["price"] + b["mb"]["price"] + b["ram"]["price"]
        + b["gpu"]["price"] + b["ssd"]["price"] + psu["price"] + FIXED_COST
    )
    b["score"] = score_build(b["cpu"], b["gpu"], b["ram"], b["ssd"], usage)
    b["cp"] = b["score"] / (b["price"] / 1000.0)
    return b


def _cpu_variants(data: dict, base: dict, cpu_cand: dict) -> list:
    """換 CPU 時要考慮的所有連帶異動。

    腳位相同就單換 CPU；不同就必須連主機板一起換（跨平台升級），
    而新主機板若支援不同世代的記憶體，記憶體也得跟著換。
    這對應現實：從 AM4 升級到 AM5 本來就不可能只換 CPU。
    """
    if cpu_cand["socket"] == base["mb"]["socket"]:
        return [{"cpu": cpu_cand}]

    variants = []
    for mb in data["mb"].to_dict("records"):
        if mb["socket"] != cpu_cand["socket"]:
            continue
        if mb["mem_type"] == base["ram"]["mem_type"]:
            variants.append({"cpu": cpu_cand, "mb": mb})          # 記憶體可以留用
        else:
            for ram in data["ram"].to_dict("records"):
                if ram["mem_type"] == mb["mem_type"]:
                    variants.append({"cpu": cpu_cand, "mb": mb, "ram": ram})
    return variants


def _ram_variants(data: dict, base: dict, ram_cand: dict) -> list:
    """換記憶體時要考慮的所有連帶異動。

    世代相同就單換記憶體；不同就必須連主機板一起換成同腳位、支援新世代的那一張。
    少了這條，起點只要挑到 DDR4 主機板（便宜所以 CP 值高），之後就永遠升不上 DDR5：
    換同腳位的 CPU 不會動到主機板，單換記憶體又會撞世代。
    """
    if ram_cand["mem_type"] == base["mb"]["mem_type"]:
        return [{"ram": ram_cand}]
    return [{"ram": ram_cand, "mb": mb} for mb in data["mb"].to_dict("records")
            if mb["socket"] == base["cpu"]["socket"] and mb["mem_type"] == ram_cand["mem_type"]]


def upgrade_options(data: dict, base: dict, usage: str, budget=None,
                    cpu_brand: str = None, require_dgpu: bool = False) -> list:
    """列出所有可行的升級，依邊際 CP 值由高到低排序。

    邊際 CP 值 = 增加的分數 / 增加的價格（每千元）。
    它回答的是「下一個一千元花在哪個零件上最值得」，
    和整機 CP 值不同：整機 CP 值看的是平均，邊際 CP 值看的是下一塊錢。

    大多數升級只動一個零件，但換 CPU 可能連帶換主機板與記憶體，
    換記憶體也可能連帶換主機板（DDR4 升 DDR5），
    此時價差會合併計算——不能只算 CPU 的錢卻不算換平台的代價。
    """
    psu_by_price = sorted(data["psu"].to_dict("records"), key=lambda p: p["price"])
    cap = float("inf") if budget is None else budget
    options = []

    for kind in SCORING_KINDS:
        for cand in data[kind].to_dict("records"):
            if cand["id"] == base[kind]["id"]:
                continue
            if kind == "cpu" and cpu_brand and cand["brand"] != cpu_brand:
                continue
            if kind == "gpu" and require_dgpu and not cand["discrete"]:
                continue

            if kind == "cpu":
                variants = _cpu_variants(data, base, cand)
            elif kind == "ram":
                variants = _ram_variants(data, base, cand)
            else:
                variants = [{kind: cand}]

            # 同一顆候選零件可能有多種連帶組合，只保留最划算的那一種
            best = None
            for changes in variants:
                nb = _make(base, changes, psu_by_price, usage)
                if nb is None:
                    continue

                d_price = nb["price"] - base["price"]
                d_score = nb["score"] - base["score"]
                if d_price <= 0 or d_score <= 0:   # 只看「加錢換效能」的升級
                    continue
                if nb["price"] > cap:
                    continue

                marginal = d_score / (d_price / 1000.0)
                if best is None or marginal > best["marginal_cp"]:
                    changed = [k for k in ("cpu", "mb", "ram", "gpu", "ssd")
                               if nb[k]["id"] != base[k]["id"]]
                    best = {
                        "kind": kind,
                        "label": " + ".join(KIND_LABEL[k] for k in changed),
                        "from": " + ".join(base[k]["name"] for k in changed),
                        "to": " + ".join(nb[k]["name"] for k in changed),
                        "psu_changed": nb["psu"]["id"] != base["psu"]["id"],
                        "d_price": int(d_price),
                        "d_score": d_score,
                        "marginal_cp": marginal,
                        "build": nb,
                    }

            if best is not None:
                options.append(best)

    options.sort(key=lambda o: -o["marginal_cp"])
    return options


def upgrade_path(data: dict, base: dict, usage: str, budget: int,
                 cpu_brand: str = None, require_dgpu: bool = False,
                 max_steps: int = 15):
    """貪婪法：反覆挑邊際 CP 值最高的升級，直到預算用完或無路可升。

    每一步價格嚴格上升、預算有限，所以一定會停；max_steps 只是保險。
    回傳 (每一步的紀錄, 最終配置)。
    """
    steps, current = [], base
    for _ in range(max_steps):
        options = upgrade_options(data, current, usage, budget, cpu_brand, require_dgpu)
        if not options:
            break
        best = options[0]
        current = best["build"]
        steps.append({**best, "total_price": current["price"],
                      "total_score": current["score"]})
    return steps, current


def trim_step(builds: list, current: dict):
    """同分最省：在預算內的合法配置裡，找分數不低於目前、總價更低的最便宜一組。

    貪婪升級只會往上加，早期走了一步不划算的路就回不了頭；
    這裡直接拿窮舉結果比價，保證最終配置是「達到這個分數的最低花費」。
    找不到更便宜的就回傳 None。
    """
    cheaper = [b for b in builds
               if b["price"] < current["price"] and b["score"] >= current["score"] - 1e-9]
    if not cheaper:
        return None
    nb = min(cheaper, key=lambda b: (b["price"], -b["score"]))
    changed = [k for k in ("cpu", "mb", "ram", "gpu", "ssd") if nb[k]["id"] != current[k]["id"]]
    return {
        "kind": "trim",
        "label": " + ".join(KIND_LABEL[k] for k in changed),
        "from": " + ".join(current[k]["name"] for k in changed),
        "to": " + ".join(nb[k]["name"] for k in changed),
        "psu_changed": nb["psu"]["id"] != current["psu"]["id"],
        "d_price": int(nb["price"] - current["price"]),
        "d_score": nb["score"] - current["score"],
        "marginal_cp": None,          # 省錢又不掉分，邊際 CP 值沒有意義
        "build": nb,
        "total_price": nb["price"],
        "total_score": nb["score"],
    }


def analyze(data: dict, usage: str, budget: int, require_dgpu: bool = False,
            cpu_brand: str = None, top_n: int = 5) -> dict:
    """完整流程：搜尋 -> 取 CP 值最高當起點 -> 逐步升級湊滿預算（穿插同分最省檢查）。"""
    builds, stats = search(data, usage, budget, require_dgpu, cpu_brand)

    out = {"stats": stats, "top": [], "base": None, "steps": [],
           "final": None, "next_up": [], "min_price": None}

    if not builds:
        # 預算內無解，回頭找不設上限時的最低總價，告訴使用者門檻在哪
        fallback, _ = search(data, usage, None, require_dgpu, cpu_brand)
        out["min_price"] = min((b["price"] for b in fallback), default=None)
        return out

    # 第一段 起點：CP 值最高（CP 值相同時取分數高的）
    base = max(builds, key=lambda b: (b["cp"], b["score"]))
    # 第二段 升級：把剩下的預算依邊際 CP 值花掉。
    # 每次升級停下來就做一次「同分最省」檢查；換到更便宜的組合後，省下的錢再繼續升級。
    # 一定會停：升級讓分數嚴格上升，省錢讓價格嚴格下降且分數不掉，同一組配置不可能出現兩次。
    steps, final = [], base
    while True:
        more, final = upgrade_path(data, final, usage, budget, cpu_brand, require_dgpu)
        steps += more
        trim = trim_step(builds, final)
        if trim is None:
            break
        steps.append(trim)
        final = trim["build"]
    # 補充：再往上還能升什麼（不受預算限制，回答「再多花點錢值不值得」）
    next_up = upgrade_options(data, final, usage, None, cpu_brand, require_dgpu)[:5]

    # 對照組：純粹追求效能總分最大化的前 N 名
    builds.sort(key=lambda b: (-b["score"], b["price"]))

    out.update({"top": builds[:top_n], "base": base, "steps": steps,
                "final": final, "next_up": next_up})
    return out
