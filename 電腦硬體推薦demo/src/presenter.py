# -*- coding: utf-8 -*-
"""把推薦結果整理成 API 回應用的純資料。

recommender.py 回傳的配置裡夾著 pandas 讀出來的 numpy 型別，也沒有給人看的規格摘要。
這裡負責轉成 JSON 友善的 dict，並補上前端要的衍生欄位：
規格摘要、矩陣格子用的短名、依效能分換算的稀有度，以及整張零件矩陣。
"""
import re

from .compat import check_build
from .loader import FIXED_COST, FIXED_COST_LABEL
from .recommender import KIND_LABEL

BUDGET_FLOOR = 8000        # 低於此金額直接擋下
BUDGET_SOFT_CAP = 200000   # 高於此金額只給提示，不擋

# 矩陣與明細的列順序：先列會影響分數的四類，再列純粹是相容性與成本的兩類
KINDS = ["cpu", "gpu", "ram", "ssd", "mb", "psu"]
NORM_KEY = {"cpu": "cpu_norm", "gpu": "gpu_norm", "ram": "ram_norm", "ssd": "ssd_norm"}

# 稀有度：依同類效能分（同類最強 = 100）換算，主機板、電源、機殼不計分一律 common
TIERS = [(90, "legendary"), (75, "epic"), (55, "rare"), (40, "uncommon")]


def _py(v):
    """numpy 純量轉成 Python 內建型別，JSON 才序列化得了。"""
    return v.item() if hasattr(v, "item") else v


def tier_of(perf):
    if perf is None:
        return "common"
    return next((name for floor, name in TIERS if perf >= floor), "common")


def spec_text(kind: str, p: dict) -> str:
    """把零件規格壓成一行摘要。"""
    if kind == "cpu":
        igpu = "有內顯" if p["igpu"] else "無內顯"
        return f"{p['cores']}C/{p['threads']}T · {p['socket']} · TDP {p['tdp']}W · {igpu}"
    if kind == "mb":
        return f"{p['chipset']} · {p['form_factor']} · 支援 {p['mem_type']}"
    if kind == "ram":
        return f"{p['mem_type']} · {p['capacity_gb']}GB · {p['speed_mhz']}MHz"
    if kind == "gpu":
        if not p["discrete"]:
            return "使用 CPU 內建顯示核心"
        return f"{p['vram_gb']}GB · TDP {p['tdp']}W · 長 {p['length_mm']}mm"
    if kind == "ssd":
        return f"{p['capacity_gb']}GB · {p['interface']} · 讀取 {p['read_mbs']:,} MB/s"
    if kind == "psu":
        return f"{p['watt']}W · {p['efficiency']}"
    return ""


def short_name(kind: str, p: dict) -> str:
    """矩陣格子放得下的短名，例如 AMD Ryzen 5 7500F -> R5 7500F。"""
    name = p["name"]
    if kind == "cpu":
        m = re.search(r"\b(i[3579]-\w+)", name)
        if m:
            return m.group(1)
        m = re.search(r"Ryzen (\d) (\w+)", name)
        if m:
            return f"R{m.group(1)} {m.group(2)}"
    elif kind == "gpu":
        if not p["discrete"]:
            return "內顯"
        n = name.replace("NVIDIA GeForce ", "").replace("AMD Radeon ", "")
        return re.sub(r" \d+G$", "", n).replace(" SUPER", " S")
    elif kind == "ram":
        return f"D{p['mem_type'][-1]} {p['capacity_gb']}G/{p['speed_mhz']}"
    elif kind == "ssd":
        gb = p["capacity_gb"]
        cap = f"{gb // 1000}T" if gb >= 1000 else f"{gb}G"
        gen = re.search(r"(\d)\.0", p["interface"])
        fast = " 高速" if "高速" in name else ""
        return f"{cap} G{gen.group(1) if gen else '?'}{fast}"
    elif kind == "mb":
        return name.replace(" DDR4", " D4").replace(" DDR5", " D5")
    elif kind == "psu":
        m = re.search(r"(白金|金|銅)", name)
        return f"{p['watt']}W {m.group(1)}" if m else f"{p['watt']}W"
    return name


def part(kind: str, p: dict, total: int) -> dict:
    perf = round(_py(p[NORM_KEY[kind]])) if kind in NORM_KEY else None
    return {
        "kind": kind,
        "label": KIND_LABEL[kind],
        "id": str(p["id"]),
        "name": str(p["name"]),
        "short": short_name(kind, p),
        "spec": spec_text(kind, p),
        "price": int(p["price"]),
        "pct": round(p["price"] / total * 100, 1),
        "perf": perf,
        "tier": tier_of(perf),
    }


def build(b: dict) -> dict:
    """一組完整配置：總價、分數、逐項明細（含固定計價的機殼散熱器）。"""
    total = int(b["price"])
    parts = [part(k, b[k], total) for k in KINDS]
    parts.append({
        "kind": "case", "label": "其他", "id": "FIXED", "name": FIXED_COST_LABEL,
        "short": "機殼", "spec": "本 demo 不細分型號，以固定金額計入總價",
        "price": FIXED_COST, "pct": round(FIXED_COST / total * 100, 1),
        "perf": None, "tier": "common",
    })
    return {
        "price": total,
        "score": float(b["score"]),
        "cp": float(b["cp"]),
        "parts": parts,
        "scores": {
            "cpu_single": round(float(b["cpu"]["single_norm"]), 1),
            "cpu_multi": round(float(b["cpu"]["multi_norm"]), 1),
            "gpu": round(float(b["gpu"]["gpu_norm"]), 1),
            "ram": round(float(b["ram"]["ram_norm"]), 1),
            "ssd": round(float(b["ssd"]["ssd_norm"]), 1),
        },
    }


def _upgrade(o: dict) -> dict:
    return {
        "kind": o["kind"],
        "label": o["label"],
        "before": o["from"],
        "after": o["to"],
        "d_price": int(o["d_price"]),
        "d_score": float(o["d_score"]),
        "marginal_cp": None if o["marginal_cp"] is None else float(o["marginal_cp"]),
    }


def step(s: dict) -> dict:
    return {**_upgrade(s), "psu_changed": bool(s["psu_changed"]),
            "total_price": int(s["total_price"]), "total_score": float(s["total_score"])}


def top_build(b: dict) -> dict:
    return {
        "score": float(b["score"]), "price": int(b["price"]), "cp": float(b["cp"]),
        "cpu": b["cpu"]["name"], "gpu": b["gpu"]["name"], "ram": b["ram"]["name"], "ssd": b["ssd"]["name"],
    }


def matrix(data: dict, base: dict, final: dict, require_dgpu: bool, cpu_brand: str = None) -> list:
    """整個資料庫的零件矩陣：每類一列，依價格由低到高，標出起點、最終選擇與被條件排除者。"""
    cpus = data["cpu"].to_dict("records")
    allowed_sockets = {c["socket"] for c in cpus if not cpu_brand or c["brand"] == cpu_brand}
    allowed_mem = {m["mem_type"] for m in data["mb"].to_dict("records") if m["socket"] in allowed_sockets}

    def reason(kind, p):
        if kind == "cpu" and cpu_brand and p["brand"] != cpu_brand:
            return f"只考慮 {cpu_brand} CPU"
        if kind == "gpu" and require_dgpu and not p["discrete"]:
            return "必須配獨立顯卡"
        if kind == "mb" and p["socket"] not in allowed_sockets:
            return f"沒有可搭配的 {p['socket']} CPU"
        if kind == "ram" and p["mem_type"] not in allowed_mem:
            return f"沒有支援 {p['mem_type']} 的主機板"
        return None

    rows = []
    for kind in KINDS:
        cells = []
        for p in data[kind].sort_values(["price", "id"]).to_dict("records"):
            why = reason(kind, p)
            cells.append({
                "id": str(p["id"]),
                "short": short_name(kind, p),
                "name": str(p["name"]),
                "price": int(p["price"]),
                "final": p["id"] == final[kind]["id"],
                "base": p["id"] == base[kind]["id"],
                "excluded": why is not None,
                "reason": why,
            })
        rows.append({"kind": kind, "label": KIND_LABEL[kind], "cells": cells})
    return rows


def recommendation(data: dict, result: dict, require_dgpu: bool, cpu_brand: str = None) -> dict:
    """analyze() 成功時的完整回應內容。"""
    base, final = result["base"], result["final"]
    return {
        "base": build(base),
        "final": build(final),
        "steps": [step(s) for s in result["steps"]],
        "next_up": [_upgrade(o) for o in result["next_up"]],
        "top": [top_build(b) for b in result["top"]],
        "stats": {k: int(v) for k, v in result["stats"].items()},
        "checks": [{"rule": c["rule"], "ok": bool(c["ok"]), "detail": c["detail"]} for c in check_build(final)],
        "matrix": matrix(data, base, final, require_dgpu, cpu_brand),
    }
