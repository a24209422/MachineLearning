# -*- coding: utf-8 -*-
"""FastAPI 後端：推薦 API，並直接送出建置好的前端（web/dist）。

啟動方式（在專案根目錄）：
    python -m uvicorn server.main:app --port 8501

API 文件會自動產生在 /docs。
"""
import json
import os
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src import llm, presenter, quote
from src.loader import FIXED_COST, SNAPSHOT_DATE, load_all
from src.recommender import USAGES, WEIGHTS, analyze

from .schemas import (Conditions, ExplainRequest, LlmStatus, Meta, ParseRequest,
                      ParseResult, QuoteCheck, QuoteRequest, Recommendation)

ROOT = Path(__file__).resolve().parent.parent
WEB_DIST = ROOT / "web" / "dist"

DATA = load_all()
DEFAULTS = Conditions(budget=30000, usage=USAGES[0], require_dgpu=True, cpu_brand="不限")


# ---------------------------------------------------------------- LLM 設定

def _read_env_file(path: Path) -> dict:
    """讀 KEY=VALUE 格式的 .env；不存在就回傳空 dict。"""
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


API_KEY = os.environ.get("ANTHROPIC_API_KEY") or _read_env_file(ROOT / ".env").get("ANTHROPIC_API_KEY")
CLIENT = llm.make_client(API_KEY) if API_KEY else None   # None = LLM 未啟用，全程走規則式備援


def _llm_status() -> LlmStatus:
    if CLIENT is not None:
        return LlmStatus(enabled=True, model=llm.MODEL)
    if llm.anthropic is None:
        return LlmStatus(enabled=False, model=llm.MODEL, reason="沒有安裝 anthropic 套件（需 Python 3.10 以上）")
    return LlmStatus(enabled=False, model=llm.MODEL, reason="找不到 ANTHROPIC_API_KEY")


# ---------------------------------------------------------------- 推薦

@lru_cache(maxsize=256)
def _analyze(usage: str, budget: int, require_dgpu: bool, cpu_brand):
    """同樣的條件只算一次。資料是靜態快照，結果不會變。"""
    return analyze(DATA, usage, budget, require_dgpu, cpu_brand)


def _brand(c: Conditions):
    return None if c.cpu_brand == "不限" else c.cpu_brand


app = FastAPI(
    title="電腦硬體 CP 值配置推薦 API",
    version="2.0",
    description="先找出 CP 值最高的組合當起點，再依邊際 CP 值逐步升級到預算上限。",
)


@app.get("/api/meta", response_model=Meta)
def meta():
    """選項、權重與 LLM 狀態，前端啟動時讀一次。"""
    return Meta(
        usages=USAGES,
        brands=["不限", "Intel", "AMD"],
        weights=WEIGHTS,
        snapshot_date=SNAPSHOT_DATE,
        fixed_cost=FIXED_COST,
        budget_floor=presenter.BUDGET_FLOOR,
        budget_soft_cap=presenter.BUDGET_SOFT_CAP,
        defaults=DEFAULTS,
        llm=_llm_status(),
    )


@app.post("/api/recommend", response_model=Recommendation)
def recommend(c: Conditions):
    """依條件算出推薦配置。組不出來時 error 會說明原因，不會回 4xx。"""
    out = {"conditions": c}
    if c.budget < presenter.BUDGET_FLOOR:
        out["error"] = {
            "kind": "budget_too_low",
            "message": f"預算過低，無法組出一台完整主機。含 {FIXED_COST:,} 元的機殼與散熱器，"
                       f"門檻約在 {presenter.BUDGET_FLOOR:,} 元以上。",
        }
        return out
    if c.budget > presenter.BUDGET_SOFT_CAP:
        out["warning"] = "已超出資料庫零件涵蓋範圍，結果會與最高規相同，多出來的預算不會被用掉。"

    result = _analyze(c.usage, c.budget, c.require_dgpu, _brand(c))
    if result["final"] is None:
        if result["min_price"]:
            out["error"] = {"kind": "no_build", "min_price": result["min_price"],
                            "message": f"目前條件下，{c.budget:,} 元組不出任何合法配置。"
                                       f"最低可行預算為 {result['min_price']:,} 元。"}
        else:
            out["error"] = {"kind": "too_strict",
                            "message": "篩選條件過嚴，資料庫裡沒有任何合法配置。請放寬 CPU 品牌或獨立顯卡的限制。"}
        return out

    out.update(presenter.recommendation(DATA, result, c.require_dgpu, _brand(c)))
    return out


@app.post("/api/parse", response_model=ParseResult)
def parse(req: ParseRequest):
    """一句話需求 -> 四個搜尋條件。有 LLM 用 Claude，否則用關鍵字規則。"""
    return llm.parse_request(req.text.strip(), CLIENT)


_QUOTE_CACHE = {}


def _extract_quote(text: str) -> dict:
    """同一份菜單只請 LLM 拆一次，切換用途重算時不必再付一次錢。只快取 LLM 成功的結果。"""
    if text in _QUOTE_CACHE:
        return _QUOTE_CACHE[text]
    extracted = llm.extract_quote(text, DATA, CLIENT)
    if extracted["source"] == "llm":
        if len(_QUOTE_CACHE) >= 64:
            _QUOTE_CACHE.pop(next(iter(_QUOTE_CACHE)))
        _QUOTE_CACHE[text] = extracted
    return extracted


@app.post("/api/check", response_model=QuoteCheck)
def check_quote(req: QuoteRequest):
    """估價單健檢：貼上一份現成的菜單，檢查加總、相容性與效能分，並找同價位更好的組合。

    認不出 CPU 時仍回 200，原因放在 error 欄位。
    """
    extracted = _extract_quote(req.text.strip())
    out = quote.check(extracted, req.usage, DATA)
    out.update(source=extracted["source"], fallback_reason=extracted["fallback_reason"],
               model=llm.MODEL if extracted["source"] == "llm" else None)
    return out


@app.post("/api/explain")
def explain(req: ExplainRequest):
    """結果解說，以 NDJSON 串流回傳。

    每一行是一個事件：
        {"type": "meta", "source": "llm" | "rules", "model": ..., "reason": ...}
        {"type": "delta", "text": "..."}
        {"type": "error", "reason": "..."}    串流中途失敗
        {"type": "done"}
    """
    result = _analyze(req.usage, req.budget, req.require_dgpu, _brand(req))
    if req.budget < presenter.BUDGET_FLOOR or result["final"] is None:
        raise HTTPException(status_code=422, detail="目前條件組不出配置，沒有東西可以解說。")

    def line(event: dict) -> str:
        return json.dumps(event, ensure_ascii=False) + "\n"

    def template(reason=None):
        yield line({"type": "meta", "source": "rules", "model": None, "reason": reason})
        yield line({"type": "delta", "text": llm.explain_rules(result, req.usage, req.budget)})
        yield line({"type": "done"})

    def ndjson(stream):
        return StreamingResponse(stream, media_type="application/x-ndjson")

    if CLIENT is None:
        return ndjson(template())

    context = llm.build_context(result, req.usage, req.budget, req.require_dgpu, _brand(req),
                                request=req.request, notes=req.notes, adjusted=req.adjusted)
    chunks = llm.explain_stream(context, CLIENT)
    try:
        first = next(chunks, "")      # 先等第一段文字：連不上就能整個退回模板，而不是開頭就報錯
    except llm.LLMUnavailable as err:
        return ndjson(template(str(err)))

    def stream():
        yield line({"type": "meta", "source": "llm", "model": llm.MODEL, "reason": None})
        yield line({"type": "delta", "text": first})
        try:
            for text in chunks:
                yield line({"type": "delta", "text": text})
        except llm.LLMUnavailable as err:
            yield line({"type": "error", "reason": str(err)})
        yield line({"type": "done"})

    return ndjson(stream())


# ---------------------------------------------------------------- 前端

if (WEB_DIST / "index.html").exists():
    # 放在所有 /api 路由之後：先比對到的路由優先
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
else:
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def no_frontend():
        return ("<h1>前端尚未建置</h1><p>請在 <code>web/</code> 資料夾執行 "
                "<code>npm install</code> 與 <code>npm run build</code>，或改用 "
                "<code>npm run dev</code> 開發模式。API 文件在 <a href='/docs'>/docs</a>。</p>")
