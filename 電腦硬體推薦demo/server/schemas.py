# -*- coding: utf-8 -*-
"""API 的請求與回應格式。前端的 web/src/types.ts 與這裡一一對應，改一邊要記得改另一邊。"""
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Usage = Literal["遊戲", "剪輯創作", "文書上網"]
Brand = Literal["不限", "Intel", "AMD"]
Tier = Literal["legendary", "epic", "rare", "uncommon", "common"]


class Conditions(BaseModel):
    budget: int = Field(ge=0, le=10_000_000, description="預算上限（新台幣，含機殼與散熱器）")
    usage: Usage
    require_dgpu: bool = True
    cpu_brand: Brand = "不限"


class ParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ExplainRequest(Conditions):
    request: Optional[str] = Field(default=None, max_length=500, description="使用者原始的一句話需求")
    notes: Optional[str] = Field(default=None, max_length=1000, description="解析備註")
    adjusted: bool = Field(default=False, description="解析後是否又手動改過條件")


class Part(BaseModel):
    kind: str
    label: str
    id: str
    name: str
    short: str
    spec: str
    price: int
    pct: float
    perf: Optional[int]
    tier: Tier


class Build(BaseModel):
    price: int
    score: float
    cp: float
    parts: List[Part]
    scores: Dict[str, float]


class Upgrade(BaseModel):
    kind: str
    label: str
    before: str
    after: str
    d_price: int
    d_score: float
    marginal_cp: float


class Step(Upgrade):
    """kind 為 "trim" 時是「同分最省」：換成分數不低、總價更低的組合，d_price 為負，沒有邊際 CP 值。"""
    marginal_cp: Optional[float]
    psu_changed: bool
    total_price: int
    total_score: float


class TopBuild(BaseModel):
    score: float
    price: int
    cp: float
    cpu: str
    gpu: str
    ram: str
    ssd: str


class Check(BaseModel):
    rule: str
    ok: bool
    detail: str


class Cell(BaseModel):
    id: str
    short: str
    name: str
    price: int
    final: bool
    base: bool
    excluded: bool
    reason: Optional[str]


class MatrixRow(BaseModel):
    kind: str
    label: str
    cells: List[Cell]


class RecommendError(BaseModel):
    kind: Literal["budget_too_low", "no_build", "too_strict"]
    message: str
    min_price: Optional[int] = None


class Recommendation(BaseModel):
    conditions: Conditions
    warning: Optional[str] = None
    error: Optional[RecommendError] = None
    base: Optional[Build] = None
    final: Optional[Build] = None
    steps: List[Step] = []
    next_up: List[Upgrade] = []
    top: List[TopBuild] = []
    stats: Optional[Dict[str, int]] = None
    checks: List[Check] = []
    matrix: List[MatrixRow] = []


class LlmStatus(BaseModel):
    enabled: bool
    model: str
    reason: Optional[str] = None


class Meta(BaseModel):
    usages: List[Usage]
    brands: List[Brand]
    weights: Dict[str, Dict[str, float]]
    snapshot_date: str
    fixed_cost: int
    budget_floor: int
    budget_soft_cap: int
    defaults: Conditions
    llm: LlmStatus


class QuoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000, description="貼上的估價單原文")
    usage: Usage


class QuoteItem(BaseModel):
    kind: str
    label: str
    text: str
    quoted_price: Optional[int]
    match: Literal["exact", "closest", "filled", "none", "extra", "other"]
    part: Optional[str]
    part_id: Optional[str]
    db_price: Optional[int]
    note: Optional[str]


class QuoteTotals(BaseModel):
    stated: Optional[int] = Field(description="菜單自己寫的總計")
    listed_sum: Optional[int] = Field(description="菜單逐項標價加總（含機殼等其他項目）")
    listed_core: Optional[int] = Field(description="菜單六大件的標價小計")
    db_core: int = Field(description="六大件以資料庫價格計算的小計")
    db_total: int = Field(description="db_core 加上機殼與散熱器固定成本")


class Finding(BaseModel):
    level: Literal["error", "warn", "info", "good"]
    title: str
    detail: str


class AltPart(BaseModel):
    kind: str
    label: str
    name: str
    price: int
    changed: bool


class AltBuild(BaseModel):
    price: int
    score: float
    cp: float
    d_price: int
    d_score: float
    parts: List[AltPart]


class QuoteFix(BaseModel):
    changes: List[str]
    d_price: int
    d_score: float
    price: int
    score: float


class QuoteCheck(BaseModel):
    usage: Usage
    source: Literal["llm", "rules"]
    model: Optional[str] = None
    fallback_reason: Optional[str] = None
    error: Optional[str] = None
    verdict: Optional[Literal["fail", "improvable", "good"]] = None
    headline: Optional[str] = None
    items: List[QuoteItem] = []
    totals: Optional[QuoteTotals] = None
    score: Optional[float] = None
    cp: Optional[float] = None
    require_dgpu: Optional[bool] = None
    checks: List[Check] = []
    findings: List[Finding] = []
    fix: Optional[QuoteFix] = None
    same_price: Optional[AltBuild] = None
    same_score: Optional[AltBuild] = None


class ParseResult(BaseModel):
    budget: Optional[int]
    usage: Optional[Usage]
    require_dgpu: Optional[bool]
    cpu_brand: Optional[Brand]
    notes: str
    source: Literal["llm", "rules"]
    fallback_reason: Optional[str] = None
