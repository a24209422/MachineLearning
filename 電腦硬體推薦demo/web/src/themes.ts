// 介面配色。版面固定是預案 E，配色可在六個預案之間切換；實際顏色定義在 style.css。

export interface Theme {
  id: string;
  code: string;
  name: string;
}

export const THEMES: Theme[] = [
  { id: 'gear', code: 'F', name: '裝備欄' },
  { id: 'breach', code: 'E', name: '駭入矩陣' },
  { id: 'neon', code: 'A', name: '霓虹雨夜' },
  { id: 'hazard', code: 'B', name: '工業警示' },
  { id: 'terminal', code: 'C', name: '駭客終端' },
  { id: 'scanner', code: 'D', name: '義眼掃描' },
];

export const DEFAULT_THEME = 'gear';
const STORAGE_KEY = 'hw-theme';

export function savedTheme(): string {
  try {
    const id = localStorage.getItem(STORAGE_KEY);
    if (id && THEMES.some((t) => t.id === id)) return id;
  } catch { /* 無痕模式或封鎖儲存時就用預設 */ }
  return DEFAULT_THEME;
}

export function applyTheme(id: string): void {
  document.documentElement.dataset.theme = id;
  try {
    localStorage.setItem(STORAGE_KEY, id);
  } catch { /* 記不住也沒關係，下次回到預設 */ }
}
