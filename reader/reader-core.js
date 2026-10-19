/** Source-independent navigation and state primitives. No gesture preview mutates state. */
export const byNewest = (a, b) => (Date.parse(b.date) || 0) - (Date.parse(a.date) || 0) || String(a.id).localeCompare(String(b.id));
export function collection(papers, states, feed = 'all', mode = 'all') {
  return [...papers].sort(byNewest).filter(p => {
    const s = states[p.id] || {};
    return (feed === 'all' || feed === 'starred' && s.starred || feed === 'bad' && s.bad || feed === `source:${p.source}`) && (mode !== 'unread' || !s.read);
  });
}
export function shortcut(event) {
  const target = event.target;
  if (event.altKey || event.ctrlKey || event.metaKey || event.isComposing || target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"]),[role="textbox"]')) return null;
  const key = event.key.toLowerCase();
  if (event.shiftKey && (key === 'j' || key === 'k')) return key === 'j' ? 'nextFeed' : 'previousFeed';
  if (event.shiftKey) return null;
  return { j: 'next', k: 'previous', b: 'original', s: 'starred', g: 'bad' }[key] || null;
}
export function gestureBlocked(target) {
  if (target?.closest?.('figure,img,table,pre,.katex-display,a,button,input,textarea,select,[data-no-swipe]')) return true;
  for (let el = target; el && el !== target.ownerDocument?.body; el = el.parentElement) {
    if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0) return true;
  }
  return false;
}
export function gestureResult({ startX, startY, x, y, width, blocked = false, cancelled = false }) {
  const dx = x - startX, dy = y - startY;
  if (blocked || cancelled || Math.abs(dx) < Math.max(64, width * .18) || Math.abs(dx) < Math.abs(dy) * 1.8) return null;
  if (startX <= 24 && dx > 0) return 'sidebar';
  return dx < 0 ? 'next' : 'previous';
}
export function versionData(paper, version) {
  const selected = (paper.versionHistory || []).find(v => String(v.version) === String(version));
  if (String(paper.version) === String(version)) return selected || paper;
  // Do not inherit current lead, bibliography, or images into an older version.
  return selected || { version, title: paper.title, source: paper.source, id: paper.id };
}
export function createStateStore(initial, persist, onChange = () => {}, onError = () => {}) {
  const states = Object.assign(Object.create(null), structuredClone(initial)), confirmed = Object.assign(Object.create(null), structuredClone(initial)), queues = new Map(), revisions = new Map();
  function set(id, field, value) {
    const key = JSON.stringify([id, field]), revision = (revisions.get(key) || 0) + 1;
    revisions.set(key, revision);
    (states[id] ||= {})[field] = value;
    onChange(states);
    const request = (queues.get(key) || Promise.resolve()).catch(() => {}).then(async () => {
      try {
        await persist(id, field, value);
        (confirmed[id] ||= {})[field] = value;
        return true;
      } catch (error) {
        if (revisions.get(key) === revision) {
          states[id][field] = confirmed[id]?.[field] || false;
          onChange(states);
          onError({ id, field, value, error, retry: () => set(id, field, value) });
        }
        return false;
      }
    });
    queues.set(key, request);
    request.finally(() => { if (queues.get(key) === request) queues.delete(key); });
    return request;
  }
  return { states, set, toggle: (id, field) => set(id, field, !states[id]?.[field]), bulkRead: ids => Promise.all([...new Set(ids)].map(id => set(id, 'read', true))) };
}
export function formatUpdated(date) {
  if (!date || Number.isNaN(Date.parse(date))) return '—';
  return new Intl.DateTimeFormat('ja-JP', { timeZone: 'Asia/Tokyo', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(date)) + ' JST';
}
