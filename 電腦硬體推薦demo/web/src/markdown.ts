import { esc } from './util';

/** 解說用的極簡 Markdown：段落、清單、粗體。先跳脫再轉換，模型輸出的 HTML 不會被執行。 */
export function renderMarkdown(src: string): string {
  const blocks = src.replace(/\r\n/g, '\n').trim().split(/\n{2,}/);
  return blocks
    .filter((b) => b.trim())
    .map((block) => {
      const lines = block.split('\n');
      if (lines.every((l) => /^\s*[-*] /.test(l))) {
        return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*] /, ''))}</li>`).join('')}</ul>`;
      }
      const heading = /^#{1,4} (.+)$/.exec(block);
      if (heading) return `<h4>${inline(heading[1])}</h4>`;
      return `<p>${lines.map(inline).join('<br>')}</p>`;
    })
    .join('');
}

function inline(s: string): string {
  return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
}
