const ESC: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/** 所有插進 innerHTML 的文字都要先過這一關。 */
export function esc(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
}

export const money = (n: number) => n.toLocaleString('en-US');
export const fixed = (n: number, digits = 1) => n.toFixed(digits);
export const signed = (n: number, digits = 1) => (n >= 0 ? '+' : '') + n.toFixed(digits);

export function debounce<A extends unknown[]>(fn: (...args: A) => void, ms: number) {
  let t: number | undefined;
  return (...args: A) => {
    window.clearTimeout(t);
    t = window.setTimeout(() => fn(...args), ms);
  };
}

export function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError';
}
