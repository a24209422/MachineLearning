// API 的資料格式，與 server/schemas.py 一一對應：改一邊要記得改另一邊。

export type Usage = '遊戲' | '剪輯創作' | '文書上網';
export type Brand = '不限' | 'Intel' | 'AMD';
export type Tier = 'legendary' | 'epic' | 'rare' | 'uncommon' | 'common';

export interface Conditions {
  budget: number;
  usage: Usage;
  require_dgpu: boolean;
  cpu_brand: Brand;
}

export interface Part {
  kind: string;
  label: string;
  id: string;
  name: string;
  short: string;
  spec: string;
  price: number;
  pct: number;
  perf: number | null;
  tier: Tier;
}

export interface Build {
  price: number;
  score: number;
  cp: number;
  parts: Part[];
  scores: Record<string, number>;
}

export interface Upgrade {
  kind: string;
  label: string;
  before: string;
  after: string;
  d_price: number;
  d_score: number;
  marginal_cp: number;
}

/** kind 為 'trim' 時是「同分最省」：換成分數不低、總價更低的組合，d_price 為負，沒有邊際 CP 值。 */
export interface Step extends Omit<Upgrade, 'marginal_cp'> {
  marginal_cp: number | null;
  psu_changed: boolean;
  total_price: number;
  total_score: number;
}

export interface TopBuild {
  score: number;
  price: number;
  cp: number;
  cpu: string;
  gpu: string;
  ram: string;
  ssd: string;
}

export interface Check {
  rule: string;
  ok: boolean;
  detail: string;
}

export interface Cell {
  id: string;
  short: string;
  name: string;
  price: number;
  final: boolean;
  base: boolean;
  excluded: boolean;
  reason: string | null;
}

export interface MatrixRow {
  kind: string;
  label: string;
  cells: Cell[];
}

export interface RecommendError {
  kind: 'budget_too_low' | 'no_build' | 'too_strict';
  message: string;
  min_price: number | null;
}

export interface Recommendation {
  conditions: Conditions;
  warning: string | null;
  error: RecommendError | null;
  base: Build | null;
  final: Build | null;
  steps: Step[];
  next_up: Upgrade[];
  top: TopBuild[];
  stats: Record<string, number> | null;
  checks: Check[];
  matrix: MatrixRow[];
}

export interface LlmStatus {
  enabled: boolean;
  model: string;
  reason: string | null;
}

export interface Meta {
  usages: Usage[];
  brands: Brand[];
  weights: Record<string, Record<string, number>>;
  snapshot_date: string;
  fixed_cost: number;
  budget_floor: number;
  budget_soft_cap: number;
  defaults: Conditions;
  llm: LlmStatus;
}

export interface ParseResult {
  budget: number | null;
  usage: Usage | null;
  require_dgpu: boolean | null;
  cpu_brand: Brand | null;
  notes: string;
  source: 'llm' | 'rules';
  fallback_reason: string | null;
}

export interface QuoteRequest {
  text: string;
  usage: Usage;
}

export type QuoteMatch = 'exact' | 'closest' | 'filled' | 'none' | 'extra' | 'other';

export interface QuoteItem {
  kind: string;
  label: string;
  text: string;
  quoted_price: number | null;
  match: QuoteMatch;
  part: string | null;
  part_id: string | null;
  db_price: number | null;
  note: string | null;
}

export interface QuoteTotals {
  stated: number | null;
  listed_sum: number | null;
  listed_core: number | null;
  db_core: number;
  db_total: number;
}

export interface Finding {
  level: 'error' | 'warn' | 'info' | 'good';
  title: string;
  detail: string;
}

export interface AltPart {
  kind: string;
  label: string;
  name: string;
  price: number;
  changed: boolean;
}

export interface AltBuild {
  price: number;
  score: number;
  cp: number;
  d_price: number;
  d_score: number;
  parts: AltPart[];
}

export interface QuoteFix {
  changes: string[];
  d_price: number;
  d_score: number;
  price: number;
  score: number;
}

export interface QuoteCheck {
  usage: Usage;
  source: 'llm' | 'rules';
  model: string | null;
  fallback_reason: string | null;
  error: string | null;
  verdict: 'fail' | 'improvable' | 'good' | null;
  headline: string | null;
  items: QuoteItem[];
  totals: QuoteTotals | null;
  score: number | null;
  cp: number | null;
  require_dgpu: boolean | null;
  checks: Check[];
  findings: Finding[];
  fix: QuoteFix | null;
  same_price: AltBuild | null;
  same_score: AltBuild | null;
}

export interface ExplainRequest extends Conditions {
  request: string | null;
  notes: string | null;
  adjusted: boolean;
}

export type ExplainEvent =
  | { type: 'meta'; source: 'llm' | 'rules'; model: string | null; reason: string | null }
  | { type: 'delta'; text: string }
  | { type: 'error'; reason: string }
  | { type: 'done' };
