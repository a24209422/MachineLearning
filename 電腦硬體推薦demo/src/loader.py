# -*- coding: utf-8 -*-
"""讀取零件 CSV，並把各項效能數據正規化成 0-100 分。

正規化採「同類最強者 = 100」的比例縮放，刻意**不用** min-max。

理由：各欄位的真實差距天差地遠——CPU 單核最強只比最弱快 1.4 倍，
GPU 卻有 15.8 倍。min-max 會把兩者一律拉成 0~100，等於謊稱
「CPU 換一顆的提升」和「顯卡換一張的提升」一樣大，
導致遊戲用途也會把錢砸在 CPU 上。比例縮放則忠實保留真實倍數關係。
"""
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# 價格與跑分的擷取日期。這是一份靜態快照，不會自動更新。
SNAPSHOT_DATE = "2026-08-01"

# 機殼 + 散熱器固定成本：不參與搜尋，但要計入總價，否則預算會失真。
FIXED_COST = 2500
FIXED_COST_LABEL = "機殼 + 散熱器（固定計價）"


def _ratio(series: pd.Series) -> pd.Series:
    """以該欄位的最大值為 100 分，其餘按真實比例換算。"""
    hi = series.max()
    if hi == 0:
        return pd.Series(0.0, index=series.index)
    return series / hi * 100.0


def load_all() -> dict:
    """讀入六張表並補上正規化欄位，回傳 {類別: DataFrame}。"""
    cpu = pd.read_csv(DATA_DIR / "cpu.csv")
    gpu = pd.read_csv(DATA_DIR / "gpu.csv")
    mb = pd.read_csv(DATA_DIR / "motherboard.csv")
    ram = pd.read_csv(DATA_DIR / "ram.csv")
    ssd = pd.read_csv(DATA_DIR / "ssd.csv")
    psu = pd.read_csv(DATA_DIR / "psu.csv")

    # CPU 與 GPU 有現成跑分，直接按比例換算
    cpu["single_norm"] = _ratio(cpu["single_score"])
    cpu["multi_norm"] = _ratio(cpu["multi_score"])
    cpu["cpu_norm"] = 0.5 * cpu["single_norm"] + 0.5 * cpu["multi_norm"]  # 雷達圖用的綜合軸
    gpu["gpu_norm"] = _ratio(gpu["score"])


    # RAM / SSD 沒有通用跑分，用規格合成一個代理分數
    ram["ram_norm"] = 0.7 * _ratio(ram["capacity_gb"]) + 0.3 * _ratio(ram["speed_mhz"])
    ssd["ssd_norm"] = 0.5 * _ratio(ssd["capacity_gb"]) + 0.5 * _ratio(ssd["read_mbs"])

    # 電源不影響效能分數，不需要正規化欄位
    return {"cpu": cpu, "gpu": gpu, "mb": mb, "ram": ram, "ssd": ssd, "psu": psu}