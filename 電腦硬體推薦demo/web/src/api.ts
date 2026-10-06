import type {
  Conditions, ExplainEvent, ExplainRequest, Meta, ParseResult, QuoteCheck, QuoteRequest, Recommendation,
} from './types';

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
  }
}

function post(body: unknown, signal?: AbortSignal): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  };
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      if (typeof data.detail === 'string') detail = data.detail;
    } catch { /* 不是 JSON 就用狀態文字 */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export async function getMeta(): Promise<Meta> {
  return json<Meta>(await fetch('/api/meta'));
}

export async function recommend(c: Conditions, signal?: AbortSignal): Promise<Recommendation> {
  return json<Recommendation>(await fetch('/api/recommend', post(c, signal)));
}

export async function parse(text: string): Promise<ParseResult> {
  return json<ParseResult>(await fetch('/api/parse', post({ text })));
}

export async function checkQuote(body: QuoteRequest, signal?: AbortSignal): Promise<QuoteCheck> {
  return json<QuoteCheck>(await fetch('/api/check', post(body, signal)));
}

/** 解說是 NDJSON 串流：一行一個事件，邊收邊交給 onEvent。 */
export async function explain(
  body: ExplainRequest,
  onEvent: (e: ExplainEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch('/api/explain', post(body, signal));
  if (!res.ok || !res.body) await json(res);

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  const flush = (all: boolean) => {
    let i: number;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim();
      buf = buf.slice(i + 1);
      if (line) onEvent(JSON.parse(line) as ExplainEvent);
    }
    if (all && buf.trim()) onEvent(JSON.parse(buf) as ExplainEvent);
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    flush(false);
  }
  flush(true);
}
