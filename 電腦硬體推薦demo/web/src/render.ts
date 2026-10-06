// 各區塊的 HTML。全部是純函式：吃資料、吐字串，所有文字都經過 esc()。

import { renderMarkdown } from './markdown';
import type {
  AltBuild, Build, Check, Conditions, LlmStatus, Meta, ParseResult, QuoteCheck, QuoteMatch,
  Recommendation, Step, Tier, Usage,
} from './types';
import { esc, fixed, money, signed } from './util';

export const TIER_LABEL: Record<Tier, string> = {
  legendary: '傳說', epic: '史詩', rare: '稀有', uncommon: '罕見', common: '普通',
};

const WEIGHT_LABEL: Record<string, string> = {
  gpu: '顯示卡', cpu_single: 'CPU 單核', cpu_multi: 'CPU 多核', ram: '記憶體', ssd: '固態硬碟',
};

const KIND_CODE: Record<string, string> = { cpu: 'CPU', gpu: 'GPU', ram: 'RAM', ssd: 'SSD' };

// ---------------------------------------------------------------- 條件區

export function segButtons(options: readonly string[], current: string): string {
  return options
    .map((o) => `<button type="button" data-value="${esc(o)}" aria-pressed="${o === current}">${esc(o)}</button>`)
    .join('');
}

export function weightsHint(usage: Usage, weights: Meta['weights']): string {
  const w = weights[usage];
  const items = Object.entries(WEIGHT_LABEL).map(([k, label]) => `${label} ${fixed(w[k], 2)}`);
  return `「${esc(usage)}」評分權重：${items.join(' · ')}　每個零件先換算成 0–100 分（同類最強者為 100）再加權相加。`;
}

export function llmTag(llm: LlmStatus): string {
  if (llm.enabled) return `<span class="llm llm--on">LLM · ${esc(llm.model)}</span>`;
  return `<span class="llm" title="${esc(llm.reason)}">LLM 離線 · 規則式備援</span>`;
}

export function nlStatus(parsed: ParseResult, adjusted: boolean): string {
  const fields: [string, string | null][] = [
    ['預算', parsed.budget ? `預算 ${money(parsed.budget)} 元` : null],
    ['用途', parsed.usage],
    ['獨顯', parsed.require_dgpu === null ? null : parsed.require_dgpu ? '必須配獨顯' : '不需要獨顯'],
    ['CPU 品牌', parsed.cpu_brand ? `CPU ${parsed.cpu_brand}` : null],
  ];
  const got = fields.filter(([, v]) => v).map(([, v]) => `<span class="chip">${esc(v)}</span>`);
  const missed = fields.filter(([, v]) => !v).map(([k]) => k);
  const by = parsed.source === 'llm' ? 'Claude 解析' : '關鍵字規則解析';
  let html = `<div class="nl-status__line"><span class="muted">已讀取（${by}）</span>${got.join('') || '<span class="muted">沒有讀到可用的條件</span>'}`;
  if (missed.length) html += `<span class="chip chip--ghost">${esc(missed.join('、'))}未提及，沿用目前設定</span>`;
  html += '</div>';
  if (parsed.notes) html += `<p class="nl-status__notes">${esc(parsed.notes)}</p>`;
  if (parsed.fallback_reason) html += `<p class="warn-text">LLM 這次無法使用（${esc(parsed.fallback_reason)}），已改用關鍵字規則解析。</p>`;
  if (adjusted) html += `<p class="muted">之後已手動調整過條件，以下結果以目前的條件為準。</p>`;
  return html;
}

export function budgetMeter(r: Recommendation | null, budget: number): string {
  if (!r?.final) {
    return `<div class="meter__head"><span>預算使用</span><span>—</span></div><div class="meter"><div style="width: 0"></div></div>`;
  }
  const used = r.final.price;
  const pct = Math.min(100, (used / Math.max(budget, 1)) * 100);
  return `<div class="meter__head"><span>預算使用</span><span class="strong">${money(used)} / ${money(budget)} · 剩 ${money(budget - used)}</span></div>
<div class="meter" role="img" aria-label="預算已使用 ${fixed(pct)}%"><div style="width: ${pct.toFixed(1)}%"></div></div>`;
}

// ---------------------------------------------------------------- 結果區

function metric(label: string, value: string, unit: string, sub: string, highlight = false): string {
  return `<div class="panel metric">
<div class="metric__label">${label}</div>
<div class="metric__value${highlight ? ' metric__value--hl' : ''}">${value}${unit ? `<span class="metric__unit"> ${unit}</span>` : ''}</div>
<div class="metric__sub">${sub}</div>
</div>`;
}

export function metrics(r: Recommendation): string {
  const base = r.base!;
  const final = r.final!;
  const up = r.steps.length > 0;
  return [
    metric('總價', money(final.price), '元',
      up ? `起點 ${money(base.price)} · <span class="good">升級 +${money(final.price - base.price)}</span>` : '起點即最終配置'),
    metric('效能總分', fixed(final.score), '/ 100',
      up ? `<span class="good">比起點 ${signed(final.score - base.score)} 分</span>` : '沒有可行的升級', true),
    metric('CP 值', fixed(final.cp, 2), '', `起點 ${fixed(base.cp, 2)} · 每千元換到的分數`),
    metric('搜尋規模', money(r.stats?.valid ?? 0), '組', `合法配置 / 展開 ${money(r.stats?.expanded ?? 0)} 組`),
  ].join('');
}

export function matrix(r: Recommendation): { table: string; note: string } {
  const cols = Math.max(...r.matrix.map((row) => row.cells.length));
  const reasons = new Set<string>();
  const rows = r.matrix.map((row) => {
    const cells = row.cells.map((c) => {
      const cls = ['cell'];
      const state: string[] = [];
      if (c.final) { cls.push('cell--final'); state.push('最終選擇'); }
      if (c.base) { cls.push('cell--base'); state.push('CP 值起點'); }
      if (c.excluded) { cls.push('cell--excluded'); state.push(`排除：${c.reason}`); reasons.add(c.reason ?? ''); }
      const title = [c.name, `${money(c.price)} 元`, ...state].join(' · ');
      const sr = state.length ? `<span class="sr-only">（${esc(state.join('，'))}）</span>` : '';
      return `<td class="${cls.join(' ')}" title="${esc(title)}"><span class="cell__name">${esc(c.short)}</span><span class="cell__price">${money(c.price)}</span>${sr}</td>`;
    });
    const pad = '<td class="cell cell--empty"></td>'.repeat(cols - row.cells.length);
    return `<tr><th scope="row">${esc(row.label)}</th>${cells.join('')}${pad}</tr>`;
  });
  const excluded = [...reasons].filter(Boolean);
  const note = '虛線框是 CP 值最高的起點，實心格是升級後的最終選擇——它在每一列往右移了多少，就是錢花去哪裡。'
    + (excluded.length ? `劃掉的是被條件排除的選項：${esc(excluded.join('、'))}。` : '');
  return { table: `<tbody>${rows.join('')}</tbody>`, note };
}

export function picks(final: Build): string {
  return final.parts
    .map((p) => `<div class="pick">
<span class="tier tier--${p.tier}">${TIER_LABEL[p.tier]}</span>
<span class="pick__kind">${esc(p.label)}</span>
<span class="pick__name"><strong>${esc(p.name)}</strong><small>${esc(p.spec)}</small></span>
<span class="pick__perf">${p.perf === null ? '不計分' : `效能 ${p.perf}`}</span>
<span class="pick__price">${money(p.price)}<small>${fixed(p.pct, 0)}%</small></span>
</div>`)
    .join('');
}

const isTrim = (s: Step) => s.kind === 'trim';
const stepCode = (s: Step) =>
  isTrim(s) ? '省' : s.label.includes('+') ? '平台' : KIND_CODE[s.kind] ?? s.kind.toUpperCase();

export function buffer(r: Recommendation): string {
  const { steps } = r;
  if (!steps.length) {
    return '<p class="muted">起點配置已經用掉絕大部分預算，在剩餘金額內沒有可行的升級。</p>';
  }
  const slots = steps
    .map((s) => isTrim(s)
      ? `<li class="slot slot--trim"><span class="slot__code">${esc(stepCode(s))}</span><span class="slot__cp">${money(s.d_price)}</span></li>`
      : `<li class="slot"><span class="slot__code">${esc(stepCode(s))}</span><span class="slot__cp">${fixed(s.marginal_cp ?? 0, 2)}</span></li>`)
    .join('');
  const details = steps
    .map((s, i) => `<li${isTrim(s) ? ' class="step--trim"' : ''}><span class="step__n">${String(i + 1).padStart(2, '0')}</span>
<span class="step__what"><strong>${isTrim(s) ? '同分最省 · ' : ''}${esc(s.label)}</strong>　${esc(s.before)} → ${esc(s.after)}${s.psu_changed ? `<small>（電源同步${isTrim(s) ? '更換' : '加大'}）</small>` : ''}</span>
${isTrim(s)
    ? `<span class="step__cost good">${money(s.d_price)}</span>`
    : `<span class="step__cost bad">+${money(s.d_price)}</span>`}<span class="step__gain good">${signed(s.d_score)}</span></li>`)
    .join('');

  const notes: string[] = [];
  if (steps.some((s) => !isTrim(s) && s.label.includes('+'))) {
    notes.push('「平台」代表連主機板一起換：跨腳位換 CPU 時主機板必須跟著換，記憶體從 DDR4 升到 DDR5 也一樣，價差已合併計算。');
  }
  const rise = steps.findIndex((s, i) => i > 0 && !isTrim(s) && !isTrim(steps[i - 1])
    && (s.marginal_cp ?? 0) > (steps[i - 1].marginal_cp ?? 0) + 1e-9);
  if (rise > 0) {
    notes.push(`第 ${rise + 1} 步的邊際 CP 值比前一步還高：前一步已經墊高了電源這類共用資源，這一步不必重複付錢，所以相對更划算。一個零件划不划算，取決於它旁邊放了什麼。`);
  }
  if (steps.some(isTrim)) {
    notes.push('「省」是同分最省檢查：升級停下來後拿窮舉結果比價，找到分數不低、總價更低的組合就換過去，省下的錢再繼續升級。所以最終配置一定是達到這個分數的最低花費。');
  }
  return `<ol class="slots" aria-label="升級序列，數字為邊際 CP 值；「省」底下是省下的金額">${slots}</ol>
<ol class="steps">${details}</ol>
<p class="hint">每一步都挑邊際 CP 值最高的升級，也就是「下一千元花在哪裡最值得」。${notes.join('')}</p>`;
}

export function checkList(list: Check[]): string {
  const items = list
    .map((c) => `<li class="daemon${c.ok ? '' : ' daemon--fail'}"><span class="daemon__rule">${esc(c.rule)}</span><span class="daemon__state">${c.ok ? '已通過' : '未通過'}</span><span class="daemon__detail">${esc(c.detail)}</span></li>`)
    .join('');
  const allOk = list.every((c) => c.ok);
  return `<ul class="daemons">${items}</ul>${allOk ? '<p class="hint">四條硬性規則全數通過，這組配置可以實際組裝。</p>' : ''}`;
}

export const checks = (r: Recommendation) => checkList(r.checks);

export interface ExplainView {
  status: 'idle' | 'loading' | 'streaming' | 'done' | 'failed';
  text: string;
  source: 'llm' | 'rules' | null;
  model: string | null;
  reason: string | null;
  error: string | null;
}

export function explainBody(v: ExplainView): string {
  if (v.status === 'idle') {
    return `<p class="muted">算完之後可以請 Claude 把這組配置用白話講一遍，數字只取自本頁的計算結果。</p>
<button type="button" class="btn btn--primary" data-action="explain">請 Claude 解說這組配置</button>`;
  }
  if (v.status === 'loading') {
    return '<p class="muted"><span class="spinner" aria-hidden="true"></span>Claude 正在撰寫解說…</p>';
  }
  if (v.status === 'failed') {
    return `<p class="warn-text">解說失敗：${esc(v.error)}</p><button type="button" class="btn" data-action="explain">再試一次</button>`;
  }
  let caption: string;
  if (v.source === 'llm') caption = `由 Claude（${esc(v.model)}）依本頁計算結果撰寫，數字以上方資料為準。`;
  else if (v.reason) caption = `LLM 這次無法使用（${esc(v.reason)}），改用固定模板。`;
  else caption = '由固定模板產生（LLM 未啟用）。';
  const caret = v.status === 'streaming' ? '<span class="caret" aria-hidden="true"></span>' : '';
  const err = v.error ? `<p class="warn-text">解說中斷：${esc(v.error)}</p>` : '';
  return `<div class="prose">${renderMarkdown(v.text)}${caret}</div>${err}<p class="hint">${caption}</p>`;
}

export function compare(r: Recommendation): string {
  const final = r.final!;
  const best = r.top[0];
  const gap = best.score - final.score;
  let msg: string;
  if (gap <= 0.05) {
    msg = '<p class="good">升級路徑剛好也達到了本預算下的最高分，逐步升級法在這個預算點取得了最佳解。</p>';
  } else if (gap <= 1.5) {
    msg = `<p>純追分最高可到 <strong>${fixed(best.score)} 分</strong>，只比升級路徑多 ${fixed(gap)} 分——逐步升級法幾乎追平窮舉最佳解，而且每一步都說得出理由。</p>`;
  } else {
    msg = `<p class="warn-text">純追分最高可到 <strong>${fixed(best.score)} 分</strong>，比升級路徑多 ${fixed(gap)} 分。逐步升級法每次只挑眼前最划算的一步，高價值升級剛好超出剩餘預算時，錢會被卡在便宜但效益低的小升級上。</p>`;
  }
  const rows = r.top
    .map((b, i) => `<tr><td>${i + 1}</td><td class="num">${fixed(b.score)}</td><td class="num">${money(b.price)}</td><td class="num">${fixed(b.cp, 2)}</td><td>${esc(b.cpu)}</td><td>${esc(b.gpu)}</td><td>${esc(b.ram)}</td><td>${esc(b.ssd)}</td></tr>`)
    .join('');
  return `${msg}<div class="scroll"><table class="table">
<thead><tr><th scope="col">排名</th><th scope="col">效能分</th><th scope="col">總價</th><th scope="col">CP</th><th scope="col">CPU</th><th scope="col">顯示卡</th><th scope="col">記憶體</th><th scope="col">固態硬碟</th></tr></thead>
<tbody>${rows}</tbody></table></div>`;
}

export function shop(r: Recommendation): string {
  if (!r.next_up.length) return '<p class="muted">已經是資料庫裡的最高規，沒有可以再升的項目。</p>';
  const items = r.next_up
    .map((o) => `<li class="offer"><span class="offer__name">${esc(o.after)}</span><span class="offer__price">+${money(o.d_price)} 元</span>
<span class="offer__meta">${esc(o.label)} · 效能 ${signed(o.d_score)} · 邊際 CP ${fixed(o.marginal_cp, 2)}</span><span class="offer__lock">預算不足</span></li>`)
    .join('');
  return `<ul class="offers">${items}</ul><p class="hint">邊際 CP 值越低，代表越接近「花錢買心安」的區間。</p>`;
}

export function footer(meta: Meta, r: Recommendation | null): string {
  const stats = r?.stats
    ? `本次搜尋展開 ${money(r.stats.expanded)} 組候選，其中 ${money(r.stats.valid)} 組同時通過相容性檢查與預算限制。電源不參與窮舉：效能貢獻為 0，瓦數足夠時取最便宜者即為最佳解。`
    : '';
  return `<p>${stats}</p>
<p>零件價格與跑分為 ${esc(meta.snapshot_date)} 的靜態快照，僅供課堂展示，非即時報價。機殼與散熱器以固定 ${money(meta.fixed_cost)} 元計入總價。</p>
<details><summary>方法的已知限制</summary>
<ol>
<li><strong>預算是上限，程式會盡量花完。</strong>目標函數是「預算內把加權分數最大化」，所有權重都是正的，錢多就會繼續加規格。選「文書上網」卻填 7 萬元，它仍會買下對文書幾乎無用的高階顯卡。</li>
<li><strong>逐步升級法不保證最佳解。</strong>每步只看當下最划算的選擇，可能被便宜的小升級卡住預算（見對照區的差距）。</li>
<li><strong>效能分數是加權合成的代理指標，不是實測幀數。</strong>記憶體與 SSD 沒有通用跑分，是用容量與速度合成的；權重表也是人為設定的估計值。</li>
<li><strong>價格是 ${esc(meta.snapshot_date)} 的靜態快照。</strong>不會自動更新，也不含特價與組裝費。</li>
<li><strong>LLM 只負責翻譯，不參與推薦。</strong>一句話需求只會轉成預算、用途、獨顯、CPU 品牌四個條件；解說的數字限定取自計算結果，但措辭由模型生成。</li>
</ol>
</details>`;
}

// ---------------------------------------------------------------- 估價單健檢

type Verdict = NonNullable<QuoteCheck['verdict']>;

const VERDICT_BADGE: Record<Verdict, string> = { fail: '組不起來', improvable: '可以更好', good: '配得不錯' };

const MATCH_LABEL: Record<QuoteMatch, string> = {
  exact: '相同', closest: '近似', filled: '系統補上', none: '認不出', extra: '未計入', other: '不評估',
};

const FINDING_TAG: Record<QuoteCheck['findings'][number]['level'], string> = {
  error: '錯誤', warn: '注意', info: '參考', good: '很好',
};

export function quoteStatus(q: QuoteCheck): string {
  const by = q.source === 'llm' ? `由 Claude（${esc(q.model)}）對應` : '關鍵字規則比對';
  const n = q.items.filter((i) => i.match !== 'filled').length;
  let html = `<div class="nl-status__line"><span class="muted">已讀取 ${n} 行零件（${by}）</span></div>`;
  if (q.fallback_reason) html += `<p class="warn-text">LLM 這次無法使用（${esc(q.fallback_reason)}），已改用關鍵字規則比對。</p>`;
  if (q.error) html += `<p class="warn-text">${esc(q.error)}</p>`;
  return html;
}

function verdictSub(q: QuoteCheck): string {
  if (q.verdict === 'fail') {
    const failed = q.checks.filter((c) => !c.ok).length;
    const f = q.fix;
    const fix = f
      ? `最少換 ${f.changes.length} 個零件、${f.d_price >= 0 ? `多花 ${money(f.d_price)}` : `反而省 ${money(-f.d_price)}`} 元就能組。`
      : '在兩個零件以內找不到修正方式。';
    return `有 ${failed} 條相容性規則沒過，照這份菜單買回來裝不起來或開不了機。${fix}`;
  }
  if (q.verdict === 'improvable') {
    const parts: string[] = [];
    if (q.same_price) parts.push(`同樣的錢最高可到 ${fixed(q.same_price.score)} 分（${signed(q.same_price.d_score)}）`);
    if (q.same_score) parts.push(`同樣的分數最少只要 ${money(q.same_score.price)} 元（省 ${money(-q.same_score.d_price)}）`);
    return `${parts.join('；')}。`;
  }
  return '在同一份價目表上，找不到同樣的錢明顯更高分、或同樣分數明顯更便宜的組合。';
}

export function quoteVerdict(q: QuoteCheck): string {
  return `<span class="verdict__badge">${VERDICT_BADGE[q.verdict!]}</span>
<h2 class="verdict__title">${esc(q.headline)}</h2>
<p class="verdict__sub">${verdictSub(q)}</p>`;
}

export function quoteMetrics(q: QuoteCheck, fixedCost: number): string {
  const t = q.totals!;
  const passed = q.checks.filter((c) => c.ok).length;
  const listed = t.listed_sum;
  let stated: string;
  if (t.stated === null) stated = '菜單沒有寫總計';
  else if (listed !== null && Math.abs(t.stated - listed) > Math.max(100, listed * 0.01)) {
    stated = `<span class="bad">菜單寫總計 ${money(t.stated)}，跟加總不符</span>`;
  } else stated = `菜單寫總計 ${money(t.stated)}，加總正確`;
  return [
    metric('效能總分', fixed(q.score!), '/ 100', `依「${esc(q.usage)}」權重 · CP 值 ${fixed(q.cp!, 2)}`, true),
    metric('資料庫價格', money(t.db_total), '元', `六大件 ${money(t.db_core)} + 機殼散熱 ${money(fixedCost)}`),
    metric('菜單標價加總', listed === null ? '—' : money(listed), listed === null ? '' : '元', stated),
    metric('相容性', `${passed} / ${q.checks.length}`, '通過',
      passed === q.checks.length ? '<span class="good">可以實際組裝</span>' : '<span class="bad">有零件互不相容</span>'),
  ].join('');
}

export function quoteItems(q: QuoteCheck): string {
  const rows = q.items.map((i) => {
    const diff = i.match === 'exact' && i.quoted_price !== null && i.db_price !== null ? i.quoted_price - i.db_price : null;
    const diffCell = diff === null
      ? '<td class="num muted">—</td>'
      : `<td class="num${diff > 0 ? ' bad' : diff < 0 ? ' good' : ''}">${diff > 0 ? '+' : ''}${money(diff)}</td>`;
    const text = i.text ? esc(i.text) : '<span class="muted">（菜單沒列）</span>';
    const part = i.part ? `<strong>${esc(i.part)}</strong>` : '<span class="muted">—</span>';
    const note = i.note ? `<small>${esc(i.note)}</small>` : '';
    const price = (v: number | null) => (v === null ? '<span class="muted">—</span>' : money(v));
    return `<tr class="qrow qrow--${i.match}"><th scope="row">${esc(i.label)}</th><td class="qtext">${text}</td>
<td class="qpart">${part}${note}</td><td><span class="match match--${i.match}">${MATCH_LABEL[i.match]}</span></td>
<td class="num">${price(i.quoted_price)}</td><td class="num">${price(i.db_price)}</td>${diffCell}</tr>`;
  }).join('');
  return `<div class="scroll"><table class="table qtable">
<thead><tr><th scope="col">類別</th><th scope="col">菜單原文</th><th scope="col">對應到的資料庫零件</th><th scope="col">對應</th><th scope="col">菜單價</th><th scope="col">資料庫價</th><th scope="col">差額</th></tr></thead>
<tbody>${rows}</tbody></table></div>
<p class="hint">「近似」是資料庫沒有同型號，以效能與定位最接近的零件估算價格與分數，但腳位、記憶體世代、內顯與電源瓦數仍照菜單原文檢查相容性。差額只算型號相同的項目，正數代表菜單比資料庫貴。</p>`;
}

export function quoteFindings(q: QuoteCheck): string {
  if (!q.findings.length) return '<p class="muted">沒有發現問題。</p>';
  const items = q.findings
    .map((f) => `<li class="finding finding--${f.level}"><span class="finding__tag">${FINDING_TAG[f.level]}</span>
<span class="finding__body"><strong>${esc(f.title)}</strong><span>${esc(f.detail)}</span></span></li>`)
    .join('');
  return `<ul class="findings">${items}</ul>`;
}

export function quoteFix(q: QuoteCheck): string {
  const f = q.fix!;
  const cost = f.d_price >= 0
    ? `多花 <strong class="bad">${money(f.d_price)}</strong> 元`
    : `反而省 <strong class="good">${money(-f.d_price)}</strong> 元`;
  return `<h2 id="fix-title" class="panel__title">最小修正 · 讓這份菜單組得起來</h2>
<ul class="fix-list">${f.changes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>
<p>${cost}，效能 <strong class="${f.d_score >= 0 ? 'good' : 'bad'}">${signed(f.d_score)}</strong> 分；修正後總價 ${money(f.price)} 元、效能分 ${fixed(f.score)}。</p>
<p class="hint">先找動到最少零件的改法；同樣動一個零件時，挑不掉分、多花最少的那一個。電源不夠會順便加大。</p>`;
}

export function quoteAlt(kind: 'price' | 'score', q: QuoteCheck): string {
  const alt: AltBuild | null = kind === 'price' ? q.same_price : q.same_score;
  const head = kind === 'price'
    ? '<h2 id="alt-price-title" class="panel__title">同樣的錢 · 最高能到幾分</h2>'
    : '<h2 id="alt-score-title" class="panel__title">同樣的分數 · 最少要花多少</h2>';
  if (!alt) {
    const msg = kind === 'price'
      ? `在 ${money(q.totals!.db_total)} 元以內，找不到比菜單高 0.5 分以上的組合。`
      : '找不到效能分不低於菜單、又便宜 300 元以上的組合。';
    return `${head}<p class="good">${msg}這方面菜單已經是同價位的好選擇。</p>`;
  }
  const lead = kind === 'price'
    ? `<span class="alt__big">${fixed(alt.score)}</span><span class="metric__unit"> 分</span><span class="alt__delta good">${signed(alt.d_score)}</span>
<span class="alt__sub">總價 ${money(alt.price)} 元${alt.d_price < 0 ? `，還比菜單省 ${money(-alt.d_price)} 元` : ''}</span>`
    : `<span class="alt__big">${money(alt.price)}</span><span class="metric__unit"> 元</span><span class="alt__delta good">省 ${money(-alt.d_price)}</span>
<span class="alt__sub">效能分 ${fixed(alt.score)}，菜單是 ${fixed(q.score!)}</span>`;
  const parts = alt.parts
    .map((p) => `<li class="alt__part${p.changed ? ' alt__part--changed' : ''}"><span class="alt__kind">${esc(p.label)}</span>
<span class="alt__name">${esc(p.name)}${p.changed ? '<span class="sr-only">（與菜單不同）</span>' : ''}</span><span class="alt__price">${money(p.price)}</span></li>`)
    .join('');
  const n = alt.parts.filter((p) => p.changed).length;
  return `${head}<div class="alt__lead">${lead}</div><ul class="alt__parts">${parts}</ul>
<p class="hint">標亮的 ${n} 項跟菜單不同；價格為資料庫快照，另加機殼散熱固定成本。</p>`;
}

export function quoteNote(meta: Meta, q: QuoteCheck): string {
  const dgpu = q.require_dgpu
    ? '菜單有獨立顯卡，所以只跟有獨顯的組合比'
    : '菜單沒有獨立顯卡，所以也納入只用內顯的組合';
  return `兩邊都用 ${esc(meta.snapshot_date)} 的資料庫價格計算（含機殼與散熱器固定 ${money(meta.fixed_cost)} 元），菜單標價只用來檢查加總與價差。比較時不限 CPU 品牌，${dgpu}。效能分是加權合成的參考分數，不是實測幀數。`;
}

export function isAdjusted(applied: Conditions, now: Conditions): boolean {
  return (Object.keys(applied) as (keyof Conditions)[]).some((k) => applied[k] !== now[k]);
}
