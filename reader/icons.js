export const badIcon = '<svg class="thumbs-down-icon" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M17 14V3H6.5a2 2 0 0 0-2 1.7L3.2 12a2 2 0 0 0 2 2.3H9v5a2.7 2.7 0 0 0 2.7 2.7L17 14Z"/><path d="M17 3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-3"/></svg>';
const feedIcons = {
  all: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><rect x="5" y="4" width="14" height="16" rx="2"/><path d="M8.5 9h7M8.5 12h7M8.5 15h4"/></svg>',
  source: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M6 4h8l4 4v12H6z"/><path d="M14 4v4h4M9 12h6M9 15h6"/></svg>'
};
export function feedSymbol(id) {
  if (id === 'all') return { kind: 'all', markup: feedIcons.all };
  const source = id.startsWith('source:') ? id.slice(7).trim() : '';
  if (/^arxiv$/i.test(source)) return { kind: 'arxiv', markup: '' };
  return { kind: 'source', markup: feedIcons.source };
}
export function paintControl(button, field, active) {
  button.dataset.field = field;
  button.setAttribute('aria-pressed', String(!!active));
  button.classList.toggle('is-active', !!active);
  button.classList.toggle('is-read', field === 'read' && !!active);
  const label = field === 'read' ? active ? '未読に戻す' : '既読にする' : field === 'starred' ? active ? 'スターを外す' : 'スターを付ける' : active ? 'badを取り消す' : '関心に合わない';
  button.setAttribute('aria-label', label); button.title = label;
  if (field === 'read') button.innerHTML = '<span class="read-dot" aria-hidden="true"></span>';
  else if (field === 'bad') button.innerHTML = badIcon;
  else button.textContent = active ? '★' : '☆';
}
