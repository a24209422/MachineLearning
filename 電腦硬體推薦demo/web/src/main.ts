// 字型隨前端一起打包，離線也能顯示；中文用系統內建字型（見 style.css 的 --font-cjk）。
import '@fontsource/barlow-condensed/latin-500.css';
import '@fontsource/barlow-condensed/latin-700.css';
import '@fontsource/oxanium/latin-500.css';
import '@fontsource/oxanium/latin-700.css';
import '@fontsource/chakra-petch/latin-500.css';
import '@fontsource/chakra-petch/latin-700.css';
import '@fontsource/rajdhani/latin-600.css';
import '@fontsource/rajdhani/latin-700.css';
import '@fontsource/jetbrains-mono/latin-400.css';
import '@fontsource/jetbrains-mono/latin-700.css';
import '@fontsource/saira-condensed/latin-500.css';
import '@fontsource/saira-condensed/latin-700.css';
import './style.css';

import { checkQuote, explain, getMeta, parse, recommend } from './api';
import * as view from './render';
import { SAMPLE_QUOTE } from './sample';
import { applyTheme, savedTheme, THEMES } from './themes';
import type { Brand, Conditions, Meta, ParseResult, QuoteCheck, Recommendation, Usage } from './types';
import { debounce, esc, isAbort } from './util';

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

interface NlState {
  text: string;
  parsed: ParseResult;
  applied: Conditions;   // 解析完當下的條件，用來判斷之後有沒有手動改過
}

let meta: Meta;
let cond: Conditions;
let result: Recommendation | null = null;
let nl: NlState | null = null;
const explanations = new Map<string, view.ExplainView>();   // 同樣的條件不重複解說
let recommendCtl: AbortController | null = null;
let explainCtl: AbortController | null = null;

type View = 'recommend' | 'check';
let quoteUsage: Usage;
let quoteResult: QuoteCheck | null = null;
let quoteText = '';          // 上次送出健檢的原文：切換用途時用同一份重算
let quoteCtl: AbortController | null = null;

// ---------------------------------------------------------------- 主題

function setupThemePicker(): void {
  const select = $<HTMLSelectElement>('theme');
  const current = savedTheme();
  select.innerHTML = THEMES
    .map((t) => `<option value="${t.id}"${t.id === current ? ' selected' : ''}>${t.code} · ${esc(t.name)}</option>`)
    .join('');
  applyTheme(current);
  select.addEventListener('change', () => applyTheme(select.value));
}

// ---------------------------------------------------------------- 條件

function renderControls(): void {
  const budget = $<HTMLInputElement>('budget');
  if (document.activeElement !== budget) budget.value = String(cond.budget);
  $('usage').innerHTML = view.segButtons(meta.usages, cond.usage);
  $('brand').innerHTML = view.segButtons(meta.brands, cond.cpu_brand);
  const dgpu = $('dgpu');
  dgpu.setAttribute('aria-pressed', String(cond.require_dgpu));
  dgpu.textContent = cond.require_dgpu ? '必須配獨顯 · 開' : '必須配獨顯 · 關';
  $('weights').innerHTML = view.weightsHint(cond.usage, meta.weights);
  renderNlStatus();
}

function renderNlStatus(): void {
  $('nl-status').innerHTML = nl ? view.nlStatus(nl.parsed, view.isAdjusted(nl.applied, cond)) : '';
}

function setCondition(patch: Partial<Conditions>): void {
  cond = { ...cond, ...patch };
  renderControls();
  void refresh();
}

// ---------------------------------------------------------------- 推薦

async function refresh(opts: { autoExplain?: boolean } = {}): Promise<void> {
  recommendCtl?.abort();
  explainCtl?.abort();
  const ctl = (recommendCtl = new AbortController());
  $('result').classList.add('is-loading');
  try {
    result = await recommend(cond, ctl.signal);
  } catch (err) {
    if (isAbort(err)) return;
    showNotice('error', `連不上後端：${(err as Error).message}`);
    return;
  } finally {
    if (ctl === recommendCtl) $('result').classList.remove('is-loading');
  }
  renderResult();
  updateExplain(opts.autoExplain ?? false);
}

function showNotice(kind: 'error' | 'warning', message: string): void {
  $('notice').innerHTML = `<div class="notice notice--${kind}" role="${kind === 'error' ? 'alert' : 'status'}">${esc(message)}</div>`;
}

function renderResult(): void {
  const r = result!;
  $('budget-meter').innerHTML = view.budgetMeter(r, cond.budget);
  $('footer').innerHTML = view.footer(meta, r);
  $('notice').innerHTML = '';
  if (r.warning) showNotice('warning', r.warning);

  const ok = !r.error && r.final;
  $('result').hidden = !ok;
  if (!ok) {
    showNotice('error', r.error?.message ?? '沒有結果。');
    return;
  }
  $('metrics').innerHTML = view.metrics(r);
  const m = view.matrix(r);
  $('matrix').innerHTML = m.table;
  $('matrix-note').innerHTML = m.note;
  $('picks').innerHTML = view.picks(r.final!);
  $('buffer').innerHTML = view.buffer(r);
  $('checks').innerHTML = view.checks(r);
  $('compare').innerHTML = view.compare(r);
  $('shop').innerHTML = view.shop(r);
}

// ---------------------------------------------------------------- 解說

const explainKey = () => JSON.stringify([cond.budget, cond.usage, cond.require_dgpu, cond.cpu_brand, nl?.text ?? null]);

function renderExplain(v: view.ExplainView): void {
  const box = $('explain');
  box.innerHTML = view.explainBody(v);
  box.setAttribute('aria-busy', String(v.status === 'loading' || v.status === 'streaming'));
}

function updateExplain(auto: boolean): void {
  if (!result?.final) return;
  const cached = explanations.get(explainKey());
  if (cached) return renderExplain(cached);
  // 沒有 LLM 時，後端回的是本機模板，立刻就有，不必等按鈕
  if (!meta.llm.enabled || auto) return void runExplain();
  renderExplain({ status: 'idle', text: '', source: null, model: null, reason: null, error: null });
}

async function runExplain(): Promise<void> {
  explainCtl?.abort();
  const ctl = (explainCtl = new AbortController());
  const key = explainKey();
  const v: view.ExplainView = { status: 'loading', text: '', source: null, model: null, reason: null, error: null };
  renderExplain(v);
  try {
    await explain(
      {
        ...cond,
        request: nl?.text ?? null,
        notes: nl?.parsed.notes ?? null,
        adjusted: nl ? view.isAdjusted(nl.applied, cond) : false,
      },
      (ev) => {
        if (ctl !== explainCtl) return;
        if (ev.type === 'meta') Object.assign(v, { source: ev.source, model: ev.model, reason: ev.reason });
        else if (ev.type === 'delta') { v.text += ev.text; v.status = 'streaming'; }
        else if (ev.type === 'error') v.error = ev.reason;
        else if (ev.type === 'done') v.status = 'done';
        renderExplain(v);
      },
      ctl.signal,
    );
    v.status = 'done';
    if (!v.error) explanations.set(key, { ...v });
    renderExplain(v);
  } catch (err) {
    if (isAbort(err)) return;
    renderExplain({ ...v, status: 'failed', error: (err as Error).message });
  }
}

// ---------------------------------------------------------------- 一句話需求

async function onParse(e: SubmitEvent): Promise<void> {
  e.preventDefault();
  const text = $<HTMLTextAreaElement>('nl-text').value.trim();
  const status = $('nl-status');
  if (!text) {
    status.innerHTML = '<p class="warn-text">請先輸入一句話描述需求。</p>';
    return;
  }
  const button = $<HTMLButtonElement>('nl-submit');
  button.disabled = true;
  status.innerHTML = `<p class="muted"><span class="spinner" aria-hidden="true"></span>${meta.llm.enabled ? 'Claude 解析中…' : '解析中…'}</p>`;
  try {
    const p = await parse(text);
    cond = {
      budget: p.budget ?? cond.budget,
      usage: p.usage ?? cond.usage,
      require_dgpu: p.require_dgpu ?? cond.require_dgpu,
      cpu_brand: p.cpu_brand ?? cond.cpu_brand,
    };
    nl = { text, parsed: p, applied: { ...cond } };
    renderControls();
    // 剛用一句話送出需求時直接接著解說；若解析已退回規則式，代表 LLM 連不上，就不再多等一次
    await refresh({ autoExplain: p.source === 'llm' });
  } catch (err) {
    status.innerHTML = `<p class="warn-text">解析失敗：${esc((err as Error).message)}</p>`;
  } finally {
    button.disabled = false;
  }
}

// ---------------------------------------------------------------- 分頁

function setView(v: View, focus = false): void {
  for (const tab of document.querySelectorAll<HTMLButtonElement>('[role="tab"]')) {
    const on = tab.dataset.view === v;
    tab.setAttribute('aria-selected', String(on));
    tab.tabIndex = on ? 0 : -1;
    if (on && focus) tab.focus();
  }
  $('view-recommend').hidden = v !== 'recommend';
  $('view-check').hidden = v !== 'check';
  $('budget-meter').hidden = v !== 'recommend';    // 預算條只屬於推薦配置
  history.replaceState(null, '', v === 'check' ? '#check' : location.pathname + location.search);
}

// ---------------------------------------------------------------- 估價單健檢

function renderQuoteControls(): void {
  $('quote-usage').innerHTML = view.segButtons(meta.usages, quoteUsage);
}

function renderQuote(q: QuoteCheck): void {
  $('quote-status').innerHTML = view.quoteStatus(q);
  const ok = !q.error && q.verdict !== null;
  $('check-result').hidden = !ok;
  if (!ok) return;
  const verdict = $('check-verdict');
  verdict.className = `panel verdict verdict--${q.verdict}`;
  verdict.innerHTML = view.quoteVerdict(q);
  $('check-metrics').innerHTML = view.quoteMetrics(q, meta.fixed_cost);
  $('check-items').innerHTML = view.quoteItems(q);
  $('check-findings').innerHTML = view.quoteFindings(q);
  $('check-checks').innerHTML = view.checkList(q.checks);
  const fix = $('check-fix');
  fix.hidden = !q.fix;
  fix.innerHTML = q.fix ? view.quoteFix(q) : '';
  $('alt-price').innerHTML = view.quoteAlt('price', q);
  $('alt-score').innerHTML = view.quoteAlt('score', q);
  $('check-note').innerHTML = view.quoteNote(meta, q);
}

async function runQuote(text: string): Promise<void> {
  quoteCtl?.abort();
  const ctl = (quoteCtl = new AbortController());
  const button = $<HTMLButtonElement>('quote-submit');
  button.disabled = true;
  $('check-result').classList.add('is-loading');
  $('quote-status').innerHTML = `<p class="muted"><span class="spinner" aria-hidden="true"></span>${meta.llm.enabled ? 'Claude 正在對應零件…' : '比對中…'}</p>`;
  try {
    quoteResult = await checkQuote({ text, usage: quoteUsage }, ctl.signal);
    quoteText = text;
    renderQuote(quoteResult);
  } catch (err) {
    if (isAbort(err)) return;
    $('quote-status').innerHTML = `<p class="warn-text">健檢失敗：${esc((err as Error).message)}</p>`;
  } finally {
    if (ctl === quoteCtl) {
      button.disabled = false;
      $('check-result').classList.remove('is-loading');
    }
  }
}

function onQuote(e: SubmitEvent): void {
  e.preventDefault();
  const text = $<HTMLTextAreaElement>('quote-text').value.trim();
  if (!text) {
    $('quote-status').innerHTML = '<p class="warn-text">請先貼上一份菜單。</p>';
    return;
  }
  void runQuote(text);
}

// ---------------------------------------------------------------- 綁定

function bindEvents(): void {
  $<HTMLFormElement>('nl-form').addEventListener('submit', (e) => void onParse(e));
  $<HTMLTextAreaElement>('nl-text').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      $<HTMLFormElement>('nl-form').requestSubmit();
    }
  });

  const budget = $<HTMLInputElement>('budget');
  const applyBudget = () => {
    const v = Math.round(Number(budget.value));
    if (Number.isFinite(v) && v >= 0 && v !== cond.budget) setCondition({ budget: v });
  };
  budget.addEventListener('input', debounce(applyBudget, 450));
  budget.addEventListener('change', applyBudget);

  $('usage').addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('button[data-value]');
    if (b) setCondition({ usage: b.dataset.value as Usage });
  });
  $('brand').addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('button[data-value]');
    if (b) setCondition({ cpu_brand: b.dataset.value as Brand });
  });
  $('dgpu').addEventListener('click', () => setCondition({ require_dgpu: !cond.require_dgpu }));

  $('explain').addEventListener('click', (e) => {
    if ((e.target as HTMLElement).closest('[data-action="explain"]')) void runExplain();
  });

  const tablist = document.querySelector<HTMLElement>('[role="tablist"]')!;
  tablist.addEventListener('click', (e) => {
    const tab = (e.target as HTMLElement).closest<HTMLButtonElement>('[role="tab"]');
    if (tab) setView(tab.dataset.view as View);
  });
  window.addEventListener('hashchange', () => setView(location.hash === '#check' ? 'check' : 'recommend'));
  tablist.addEventListener('keydown', (e) => {     // 方向鍵切換分頁（ARIA tabs 的慣例）
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    setView($('view-check').hidden ? 'check' : 'recommend', true);
  });

  $<HTMLFormElement>('quote-form').addEventListener('submit', (e) => onQuote(e));
  $<HTMLTextAreaElement>('quote-text').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      $<HTMLFormElement>('quote-form').requestSubmit();
    }
  });
  $('quote-sample').addEventListener('click', () => {
    const box = $<HTMLTextAreaElement>('quote-text');
    box.value = SAMPLE_QUOTE;
    box.focus();
  });
  $('quote-usage').addEventListener('click', (e) => {
    const b = (e.target as HTMLElement).closest<HTMLButtonElement>('button[data-value]');
    if (!b || b.dataset.value === quoteUsage) return;
    quoteUsage = b.dataset.value as Usage;
    renderQuoteControls();
    // 已經有結果時，用同一份菜單照新的用途重算（後端會沿用上次的零件對應，不會再呼叫 LLM）
    if (quoteResult && quoteText) void runQuote(quoteText);
  });
}

async function init(): Promise<void> {
  setupThemePicker();
  try {
    meta = await getMeta();
  } catch (err) {
    showNotice('error', `連不上後端（${(err as Error).message}）。請確認 FastAPI 伺服器有在執行。`);
    return;
  }
  cond = { ...meta.defaults };
  quoteUsage = meta.defaults.usage;
  $('llm').innerHTML = view.llmTag(meta.llm);
  $('quote-llm').innerHTML = view.llmTag(meta.llm);
  renderQuoteControls();
  setView(location.hash === '#check' ? 'check' : 'recommend');
  $('snapshot').textContent = `快照 ${meta.snapshot_date}`;
  renderControls();
  bindEvents();
  await refresh();
}

void init();
