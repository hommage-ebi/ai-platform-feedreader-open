import { collection, shortcut, gestureBlocked, gestureResult, versionData, createStateStore, formatUpdated } from './reader-core.js';
import { createArticleRenderer } from './article-renderer.js';
import './reader.css';
import './styles.css';
const $ = selector => document.querySelector(selector);
const renderer = createArticleRenderer();
let papers = [], store, feed = 'all', mode = 'all', visible = [], selected = null, selectedVersion = null, ready = false;
let feeds = [], selectionGeneration = 0, listScrollPosition = 0;
const errors = new Map();
const text = (tag, value, className) => { const el = document.createElement(tag); el.textContent = value ?? ''; if (className) el.className = className; return el; };
const labelValue = value => typeof value === 'string' ? value : value?.name || value?.name_en || '';
const joined = value => Array.isArray(value) ? value.map(labelValue).filter(Boolean).join(', ') : typeof value === 'string' ? value : '';
function safeLink(value) { if (typeof value !== 'string' || !value.trim()) return ''; try { const u = new URL(value, location.origin); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } }
async function json(url, options) { const response = await fetch(url, { credentials: 'same-origin', ...options }); if (!response.ok) throw new Error(`通信に失敗しました (${response.status})`); return response.json(); }
function notification(message, retry) { const row = text('div', message, 'notice'); if (retry) { const b = text('button', '再試行'); b.onclick = () => { row.remove(); retry(); }; row.append(b); } const close = text('button', '×'); close.setAttribute('aria-label', '通知を閉じる'); close.onclick = () => row.remove(); row.append(close); $('#notices').append(row); }
function capture() { if (!(innerWidth <= 900 && document.body.classList.contains('article-open'))) listScrollPosition = $('#list-pane').scrollTop; return { reader: true, feed, mode, ids: visible.map(p => p.id), selected, version: selectedVersion, listScroll: listScrollPosition, articleScroll: $('#article-scroll').scrollTop }; }
function savePosition() { if (ready) history.replaceState(capture(), '', location.href); }
function writeHistory() { const url = new URL(location.href); if (selected) { url.searchParams.set('article', selected); url.searchParams.set('version', selectedVersion); } else { url.searchParams.delete('article'); url.searchParams.delete('version'); } history.pushState(capture(), '', url); }
function snapshot() { visible = collection(papers, store.states, feed, mode); }
function renderFeeds() {
  const nav = $('#feeds'); nav.replaceChildren();
  for (const entry of feeds) { const b = text('button', entry.label); b.classList.toggle('active', entry.id === feed); b.setAttribute('aria-current', entry.id === feed ? 'page' : 'false'); b.onclick = () => changeCollection(entry.id, mode); nav.append(b); }
}
function renderList() {
  const scroll = innerWidth <= 900 && document.body.classList.contains('article-open') ? listScrollPosition : $('#list-pane').scrollTop, list = $('#list'); list.replaceChildren();
  $('#collection-title').textContent = feeds.find(f => f.id === feed)?.label || '記事';
  $('#all').setAttribute('aria-pressed', String(mode === 'all')); $('#unread').setAttribute('aria-pressed', String(mode === 'unread'));
  $('#count').textContent = `${visible.length} articles`; $('#bulk').disabled = !visible.length;
  if (!visible.length) { const empty = text('div', '', 'empty'); empty.append(text('h2', mode === 'unread' ? '未読の記事はありません' : 'まだ記事はありません'), text('p', '公開された記事がここに並びます。')); list.append(empty); }
  for (const paper of visible) {
    const state = store.states[paper.id] || {}, row = text('div', '', 'paper-row'); row.dataset.id = paper.id;
    row.classList.toggle('is-read', !!state.read); row.classList.toggle('selected', paper.id === selected);
    const open = text('button', '', 'paper-open'); open.setAttribute('aria-label', paper.title); open.setAttribute('aria-current', String(paper.id === selected));
    const meta = text('div', '', 'paper-meta'); meta.append(text('span', paper.source || 'Article'), text('span', paper.id, 'paper-id'));
    if (paper.version) meta.append(text('span', paper.version, 'paper-version'));
    meta.append(text('time', paper.date || ''));
    for (const tag of (paper.tags || []).slice(0, 4)) meta.append(text('span', tag, 'tag'));
    open.append(meta, text('h2', paper.title));
    if (paper.title_ja) open.append(text('p', paper.title_ja, 'paper-subtitle'));
    if (paper.lead_ja) open.append(text('p', Array.isArray(paper.lead_ja) ? paper.lead_ja.join(' ') : paper.lead_ja, 'paper-lead'));
    open.onclick = () => select(paper.id); row.append(open);
    const controls = text('div', '', 'row-controls');
    for (const [field, label, activeLabel] of [['read', '未読', '既読'], ['starred', '☆', '★'], ['bad', 'bad', 'bad']]) {
      const b = text('button', state[field] ? activeLabel : label); b.setAttribute('aria-label', `${paper.title}: ${field === 'starred' ? 'スター' : field === 'bad' ? 'bad' : '既読'}を切り替え`); b.setAttribute('aria-pressed', String(!!state[field])); b.onclick = () => store.toggle(paper.id, field); controls.append(b);
      const failure = errors.get(`${paper.id}:${field}`); if (failure) { const retry = text('button', '保存を再試行', 'inline-error'); retry.onclick = failure.retry; controls.append(retry); }
    }
    row.append(controls); list.append(row);
  }
  $('#list-pane').scrollTop = scroll; renderActions();
}
function renderActions() {
  const state = store?.states[selected] || {}, index = visible.findIndex(p => p.id === selected);
  $('#article-actions').hidden = !selected;
  $('#read-state').textContent = state.read ? '既読' : '未読'; $('#read-state').setAttribute('aria-pressed', String(!!state.read));
  $('#star-state').textContent = state.starred ? '★' : '☆'; $('#star-state').setAttribute('aria-pressed', String(!!state.starred)); $('#star-state').setAttribute('aria-label', 'スターを切り替え');
  $('#bad-state').setAttribute('aria-pressed', String(!!state.bad)); $('#previous').disabled = index <= 0; $('#next').disabled = index < 0 || index >= visible.length - 1;
  $('#article-position').textContent = index < 0 ? '' : `${index + 1} / ${visible.length}`;
}
function header(data, container) {
  const h = text('header', '', 'article-header'); h.append(text('p', [data.source, data.date, data.version].filter(Boolean).join(' · '), 'article-meta'), text('h1', data.title));
  if (data.title_ja) h.append(text('h2', data.title_ja));
  const authors = joined(data.authors), institutions = joined(data.institution_names || data.affiliations);
  if (authors) h.append(text('p', authors, 'authors')); if (institutions) h.append(text('p', institutions, 'institutions'));
  if (data.lead_ja) { const lead = text('div', '', 'lead'); for (const line of Array.isArray(data.lead_ja) ? data.lead_ja : String(data.lead_ja).split('\n')) if (line.trim()) lead.append(text('p', line)); h.append(lead); }
  container.append(h);
}
async function showArticle(scroll = 0) {
  const generation = ++selectionGeneration;
  const paper = papers.find(p => p.id === selected);
  document.body.classList.toggle('article-open', !!paper);
  renderList();
  if (!paper) { renderer.cancel(); const welcome = text('div', '', 'welcome'); welcome.append(text('p', 'YOUR READING DESK', 'section-label'), text('h2', '気になる論文から。'), text('p', '記事を選ぶと、本文と図をここに表示します。')); $('#article').replaceChildren(welcome); $('#version-label').hidden = true; $('#original').hidden = true; $('#list-pane').scrollTop = listScrollPosition; return; }
  const versions = [...new Set([paper.version || 'current', ...(paper.versionHistory || []).map(v => v.version)].filter(Boolean).map(String))];
  const selectBox = $('#version'); selectBox.replaceChildren(); for (const version of versions) { const option = text('option', version); option.value = version; selectBox.append(option); } selectBox.value = selectedVersion;
  $('#version-label').hidden = versions.length < 2;
  const data = versionData(paper, selectedVersion), url = safeLink(data.source_url || paper.source_url);
  $('#original').hidden = !url; $('#original').href = url;
  const body = await renderer.render($('#article'), selected, selectedVersion, header);
  if (generation === selectionGeneration) { if (body?.source_url) $('#original').href = safeLink(body.source_url); requestAnimationFrame(() => { if (generation === selectionGeneration) $('#article-scroll').scrollTop = scroll; }); }
}
function select(id, version, { push = true, markRead = true, scroll = 0 } = {}) {
  const paper = papers.find(p => p.id === id); if (!paper) return;
  if (push) savePosition(); selected = id; selectedVersion = String(version || paper.version || 'current');
  if (push) writeHistory();
  showArticle(scroll);
  if (markRead && !store.states[id]?.read) store.set(id, 'read', true);
}
function changeCollection(nextFeed, nextMode) {
  savePosition(); feed = nextFeed; mode = nextMode; snapshot(); selected = null; selectedVersion = null;
  listScrollPosition = 0; $('#list-pane').scrollTop = 0; renderFeeds(); renderList(); document.body.classList.remove('sidebar-open', 'article-open'); $('#menu').setAttribute('aria-expanded', 'false'); showArticle(); writeHistory();
}
function move(delta) { const index = visible.findIndex(p => p.id === selected), next = visible[index < 0 ? 0 : index + delta]; if (next) select(next.id); }
function action(name) {
  if (!ready) return;
  if (name === 'next' || name === 'previous') return move(name === 'next' ? 1 : -1);
  if (name === 'nextFeed' || name === 'previousFeed') { const i = feeds.findIndex(f => f.id === feed), next = feeds[i + (name === 'nextFeed' ? 1 : -1)]; if (next) changeCollection(next.id, mode); return; }
  if (selected && (name === 'starred' || name === 'bad')) store.toggle(selected, name);
  if (name === 'original' && selected && !$('#original').hidden) window.open($('#original').href, '_blank', 'noopener,noreferrer');
}
function events() {
  const placeActions = () => { const target = innerWidth > 900 ? $('.article-toolbar') : $('#article-pane'); if ($('#article-actions').parentElement !== target) target.append($('#article-actions')); };
  placeActions(); window.addEventListener('resize', placeActions);
  $('#all').onclick = () => changeCollection(feed, 'all'); $('#unread').onclick = () => changeCollection(feed, 'unread');
  $('#bulk').onclick = () => store.bulkRead(visible.map(p => p.id));
  $('#menu').onclick = () => { const open = document.body.classList.toggle('sidebar-open'); $('#menu').setAttribute('aria-expanded', String(open)); };
  $('#back').onclick = () => { savePosition(); selected = null; selectedVersion = null; showArticle(); writeHistory(); };
  $('#version').onchange = event => select(selected, event.target.value, { markRead: false });
  $('#previous').onclick = () => move(-1); $('#next').onclick = () => move(1);
  for (const [id, field] of [['read-state', 'read'], ['star-state', 'starred'], ['bad-state', 'bad']]) $(`#${id}`).onclick = () => selected && store.toggle(selected, field);
  document.addEventListener('keydown', event => { const command = shortcut(event); if (command) { event.preventDefault(); action(command); } if (event.key === 'Escape') { document.body.classList.remove('sidebar-open'); $('#menu').setAttribute('aria-expanded', 'false'); } });
  window.addEventListener('popstate', event => {
    const state = event.state; if (!state?.reader) return;
    feed = state.feed; mode = state.mode; visible = state.ids.map(id => papers.find(p => p.id === id)).filter(Boolean); selected = state.selected; selectedVersion = state.version;
    listScrollPosition = state.listScroll || 0; renderFeeds(); showArticle(state.articleScroll || 0); $('#list-pane').scrollTop = listScrollPosition;
  });
  let scrollTimer; for (const pane of [$('#list-pane'), $('#article-scroll')]) pane.addEventListener('scroll', () => { clearTimeout(scrollTimer); scrollTimer = setTimeout(savePosition, 100); }, { passive: true });
  let gesture, touchActive = false;
  const clearGesture = () => { gesture = null; document.body.classList.remove('sidebar-preview'); document.body.style.removeProperty('--drawer-x'); $('#article-pane').style.removeProperty('--swipe'); };
  const startGesture = (target, x, y, pointerId) => {
    gesture = { startX: x, startY: y, x, y, width: innerWidth, blocked: gestureBlocked(target), article: !!target.closest('#article-pane'), pointerId };
  };
  const preview = (x, y) => {
    if (!gesture) return;
    gesture.x = x; gesture.y = y;
    const horizontal = Math.abs(x - gesture.startX) > Math.abs(y - gesture.startY) * 1.8;
    if (!gesture.blocked && gesture.startX <= 24 && x > gesture.startX && horizontal) { document.body.classList.add('sidebar-preview'); document.body.style.setProperty('--drawer-x', `${Math.min(0, -260 + x - gesture.startX)}px`); }
    if (!gesture.blocked && gesture.article && horizontal) $('#article-pane').style.setProperty('--swipe', `${Math.max(-36, Math.min(36, (x - gesture.startX) * .15))}px`);
  };
  const end = cancelled => {
    if (!gesture) return;
    const command = gestureResult({ ...gesture, cancelled }), article = gesture.article;
    clearGesture();
    if (command === 'sidebar') { document.body.classList.add('sidebar-open'); $('#menu').setAttribute('aria-expanded', 'true'); }
    else if (article && command) action(command);
  };
  // Keep touch-action unrestricted: a pan-y ancestor also restricts embedded
  // horizontal tables/formulae. Cancel native scrolling only for a committed
  // horizontal intent on a gesture-safe surface; vertical and blocked regions
  // retain native scrolling and pinch zoom.
  document.addEventListener('touchstart', event => {
    touchActive = true;
    if (event.touches.length !== 1 || innerWidth > 900) { clearGesture(); return; }
    const touch = event.touches[0]; startGesture(event.target, touch.clientX, touch.clientY, touch.identifier);
  }, { passive: true });
  document.addEventListener('touchmove', event => {
    if (!gesture || event.touches.length !== 1) { clearGesture(); return; }
    const touch = [...event.touches].find(t => t.identifier === gesture.pointerId); if (!touch) return;
    preview(touch.clientX, touch.clientY);
    const dx = gesture.x - gesture.startX, dy = gesture.y - gesture.startY;
    if (!gesture.blocked && (gesture.article || gesture.startX <= 24 && dx > 0) && Math.abs(dx) > 8 && Math.abs(dx) > Math.abs(dy) * 1.8 && event.cancelable) event.preventDefault();
  }, { passive: false });
  document.addEventListener('touchend', event => {
    const touch = gesture && [...event.changedTouches].find(t => t.identifier === gesture.pointerId);
    if (touch) { preview(touch.clientX, touch.clientY); end(false); }
    if (!event.touches.length) touchActive = false;
  });
  document.addEventListener('touchcancel', () => { end(true); touchActive = false; });
  // Pointer events remain a fallback for touch devices without Touch Events.
  document.addEventListener('pointerdown', event => {
    if (touchActive || event.pointerType !== 'touch' || !event.isPrimary || innerWidth > 900) return;
    startGesture(event.target, event.clientX, event.clientY, event.pointerId);
  }, { passive: true });
  document.addEventListener('pointermove', event => { if (!touchActive && gesture && gesture.pointerId === event.pointerId) preview(event.clientX, event.clientY); }, { passive: true });
  document.addEventListener('pointerup', event => { if (!touchActive && gesture && gesture.pointerId === event.pointerId) { preview(event.clientX, event.clientY); end(false); } });
  document.addEventListener('pointercancel', event => { if (!touchActive && gesture && gesture.pointerId === event.pointerId) end(true); });
}
async function boot() {
  try {
    const [data, reading, preferences, batch] = await Promise.all([json('/papers.json'), json('/api/reading-state'), json('/api/article-preferences'), json('/batch-status.json').catch(() => null)]);
    papers = Array.isArray(data) ? data : data.articles || [];
    const initial = Object.create(null); for (const p of papers) initial[p.id] = { read: reading.readIds.includes(p.id), starred: preferences.starredIds.includes(p.id), bad: preferences.badIds.includes(p.id) };
    store = createStateStore(initial, async (id, field, value) => {
      const operation = field === 'read' ? value ? 'read' : 'unread' : field === 'starred' ? value ? 'star' : 'unstar' : value ? 'bad' : 'unbad';
      await json(field === 'read' ? '/api/reading-state' : '/api/article-preferences', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ articleId: id, operation }) });
      errors.delete(`${id}:${field}`); renderList();
    }, () => { if (ready) renderList(); }, failure => { errors.set(`${failure.id}:${failure.field}`, failure); renderList(); notification('状態を保存できなかったため表示を戻しました。', failure.retry); });
    feeds = [{ id: 'all', label: 'すべての記事' }, ...[...new Set(papers.map(p => p.source).filter(Boolean))].sort().map(source => ({ id: `source:${source}`, label: source })), { id: 'starred', label: '★ スター' }, { id: 'bad', label: 'bad' }];
    snapshot(); ready = true; renderFeeds(); renderList();
    const latest = papers.reduce((a, p) => Date.parse(p.updated_at || p.date) > Date.parse(a || '1970-01-01') ? p.updated_at || p.date : a, null); $('#updated').textContent = `最終更新 ${formatUpdated(batch?.updated_at || data.updated_at || latest)}`;
    history.scrollRestoration = 'manual'; events();
    const url = new URL(location.href), id = url.searchParams.get('article'); if (id) select(id, url.searchParams.get('version'), { push: false }); history.replaceState(capture(), '', location.href);
  } catch (error) { $('#list').replaceChildren(text('p', '記事と保存状態を読み込めませんでした。', 'empty')); notification(error.message, boot); }
}
boot();
