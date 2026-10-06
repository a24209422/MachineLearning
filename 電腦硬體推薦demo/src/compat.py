# -*- coding: utf-8 -*-
"""相容性規則。

四條都是硬性條件：任何一條不過，該組合直接淘汰，不會出現在推薦結果裡。
搜尋迴圈與畫面上的檢查清單共用同一組函式，避免兩邊規則走鐘。
"""

# 電源瓦數餘裕倍率。CPU + GPU 的 TDP 只是核心功耗，
# 主機板、記憶體、風扇、以及顯卡瞬時功耗突波都要靠這個倍率吸收。
PSU_HEADROOM = 1.5


def required_watt(cpu: dict, gpu: dict) -> int:
    """回傳這組 CPU + GPU 建議的最低電源瓦數。"""
    return int(round((cpu["tdp"] + gpu["tdp"]) * PSU_HEADROOM))


def socket_ok(cpu: dict, mb: dict) -> bool:
    return cpu["socket"] == mb["socket"]


def memory_ok(mb: dict, ram: dict) -> bool:
    return mb["mem_type"] == ram["mem_type"]


def psu_ok(cpu: dict, gpu: dict, psu: dict) -> bool:
    return psu["watt"] >= required_watt(cpu, gpu)


def display_ok(cpu: dict, gpu: dict) -> bool:
    """沒有獨立顯卡時，CPU 必須內建顯示核心，否則這台機器沒有畫面輸出。"""
    return bool(gpu["discrete"]) or bool(cpu["igpu"])


def check_build(build: dict) -> list:
    """對一組完整配置逐條檢查，回傳給畫面顯示用的結果清單。"""
    cpu, mb, ram, gpu, psu = (
        build["cpu"], build["mb"], build["ram"], build["gpu"], build["psu"],
    )
    need = required_watt(cpu, gpu)
    return [
        {
            "rule": "CPU 腳位與主機板相符",
            "ok": socket_ok(cpu, mb),
            "detail": f"CPU {cpu['socket']}　／　主機板 {mb['socket']}",
        },
        {
            "rule": "主機板支援的記憶體世代與 RAM 相符",
            "ok": memory_ok(mb, ram),
            "detail": f"主機板 {mb['mem_type']}　／　記憶體 {ram['mem_type']}",
        },
        {
            "rule": f"電源瓦數足夠（需 (CPU {cpu['tdp']}W + GPU {gpu['tdp']}W) × {PSU_HEADROOM}）",
            "ok": psu_ok(cpu, gpu, psu),
            "detail": f"電源 {psu['watt']}W　≥　需求 {need}W",
        },
        {
            "rule": "有畫面輸出（獨顯或 CPU 內顯擇一）",
            "ok": display_ok(cpu, gpu),
            "detail": (
                f"獨立顯卡：{gpu['name']}" if gpu["discrete"]
                else f"無獨顯，改用 {cpu['name']} 的內顯"
            ),
        },
    ]
