import { collection, shortcut, versionData, createStateStore, formatUpdated } from './reader-core.js';
import { createArticleRenderer, safeHTML, typesetMath } from './article-renderer.js';
import { badIcon, paintControl } from './icons.js';
import { bindReaderGestures } from './gestures.js';
import interestConfig from '../pipeline/config/interests.json';
import './reader.css';
import './styles.css';
const $ = selector => document.querySelector(selector);
const renderer = createArticleRenderer();
let papers = [], store, feed = 'all', mode = 'unread', visible = [], selected = null, selectedVersion = null, ready = false;
let feeds = [], selectionGeneration = 0, listScrollPosition = 0, articleScrollPosition = 0, query = '', sort = 'date', date = 'all', gestures;
const errors = new Map();
const tagLabels = new Map(interestConfig.interests.map(item => [item.id, item.label]));
const tagLabel = tag => tagLabels.get(labelValue(tag)) || labelValue(tag);
const compact = () => innerWidth <= 850;
const text = (tag, value, className) => { const el = document.createElement(tag); el.textContent = value ?? ''; if (className) el.className = className; return el; };
const labelValue = value => typeof value === 'string' ? value : value?.name || value?.name_en || '';
const joined = value => Array.isArray(value) ? value.map(labelValue).filter(Boolean).join(', ') : typeof value === 'string' ? value : '';
const shortDate = value => value ? String(value).slice(0, 10).replaceAll('-', '.') : '日付未確認';
const sourceLabel = value => /^arxiv$/i.test(value || '') ? 'arXiv' : value || 'Article';
function safeLink(value) { if (typeof value !== 'string' || !value.trim()) return ''; try { const u = new URL(value, location.origin); return ['http:', 'https:'].includes(u.protocol) ? u.href : ''; } catch { return ''; } }
async function json(url, options) { const response = await fetch(url, { credentials: 'same-origin', ...options }); if (!response.ok) throw new Error(`通信に失敗しました (${response.status})`); return response.json(); }
function notification(message, retry) { const row = text('div', message, 'notice'); if (retry) { const b = text('button', '再試行'); b.onclick = () => { row.remove(); retry(); }; row.append(b); } const close = text('button', '×'); close.setAttribute('aria-label', '通知を閉じる'); close.onclick = () => row.remove(); row.append(close); $('#notices').append(row); }
function articleOpen() { return document.body.classList.contains('article-open'); }
function readScroll(kind) { return compact() ? window.scrollY || 0 : $(kind === 'list' ? '#list' : '#article-scroll').scrollTop; }
function writeScroll(kind, top) { if (compact()) window.scrollTo({ top, left: 0, behavior: 'auto' }); else $(kind === 'list' ? '#list' : '#article-scroll').scrollTop = top; }
function capture() {
  if (!document.body.classList.contains('sidebar-open')) {
    if (!compact() || !articleOpen()) listScrollPosition = readScroll('list');
    if (!compact() || articleOpen()) articleScrollPosition = readScroll('article');
  }
  return { reader: true, feed, mode, query, sort, date, ids: visible.map(p => ({ id: p.id, version: p.version })), selected, version: selectedVersion, listScroll: listScrollPosition, articleScroll: articleScrollPosition };
}
function savePosition() { if (ready) history.replaceState(capture(), '', location.href); }
function writeHistory() { const url = new URL(location.href); if (selected) { url.searchParams.set('article', selected); url.searchParams.set('version', selectedVersion); } else { url.searchParams.delete('article'); url.searchParams.delete('version'); } history.pushState(capture(), '', url); }
function paperForDate(paper) {
  if (date === 'all' || (paper.announcementDate || paper.date) === date) return paper;
  const history = (paper.versionHistory || []).filter(v => (v.announcementDate || v.date) === date || v.announcementDates?.includes(date)).sort((a, b) => Number(String(b.version).replace(/^v/, '')) - Number(String(a.version).replace(/^v/, '')));
  return history.length ? { ...history[0], id: paper.id, versionHistory: paper.versionHistory } : null;
}
function scoped() {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const list = collection(papers, store.states, feed, 'all').map(paperForDate).filter(Boolean).filter(p => terms.every(term => [p.title, p.title_ja, p.summary_ja, p.relevance_ja, ...(Array.isArray(p.lead_ja) ? p.lead_ja : [p.lead_ja]), p.id, joined(p.authors), ...(p.tags || []).map(tagLabel)].join(' ').toLocaleLowerCase().includes(term)));
  return sort === 'title' ? list.sort((a, b) => a.title.localeCompare(b.title, 'en')) : list;
}
function snapshot() { visible = scoped().filter(p => mode !== 'unread' || !store.states[p.id]?.read); }
function syncControls() { $('#search').value = query; $('#sort').value = sort; $('#batch-date').value = date; $('#read-filter').value = mode; }
function renderFeeds() {
  const nav = $('#feeds'); nav.replaceChildren();
  for (const entry of feeds) {
    const saved = entry.id === 'starred' || entry.id === 'bad', active = entry.id === feed;
    const b = text('button', '', `feed-button${saved ? ' saved-view' : ''}${entry.id === 'starred' ? ' saved-start' : ''}`); b.classList.toggle('active', active); b.setAttribute('aria-current', active ? 'page' : 'false');
    const icon = text('span', saved ? entry.id === 'starred' ? '★' : '' : 'a', saved ? 'saved-icon' : 'feed-symbol'); icon.setAttribute('aria-hidden', 'true'); if (entry.id === 'bad') icon.innerHTML = badIcon;
    const count = saved ? papers.filter(p => store.states[p.id]?.[entry.id]).length : collection(papers, store.states, entry.id, 'unread').length;
    b.append(icon, text('span', entry.label, 'feed-name'), text('span', count, 'count')); b.onclick = () => changeCollection(entry.id, saved ? 'all' : 'unread', { query: '', date: 'all' }); nav.append(b);
  }
}
function tagsFor(data) { const tags = text('div', '', 'interest-tags'); for (const tag of data.tags || []) tags.append(text('span', tagLabel(tag))); return tags; }
function control(field, active, callback) { const b = text('button', '', 'icon-button'); paintControl(b, field, active); b.onclick = callback; return b; }
function renderList() {
  const scroll = !compact() || !articleOpen() ? readScroll('list') : listScrollPosition, list = $('#list'); list.replaceChildren();
  const scope = scoped(); $('#unread-count').textContent = `未読 ${scope.filter(p => !store.states[p.id]?.read).length}`;
  $('#count').textContent = `${visible.length} / ${papers.length} 件`; $('#bulk').disabled = !scope.some(p => !store.states[p.id]?.read);
  if (!visible.length) {
    const empty = text('div', '', 'empty'); empty.append(text('h2', mode === 'unread' && scope.length ? '未読の記事はありません' : 'この条件の記事はありません'), text('p', '検索語や告知日を変えてみてください。'));
    const reset = text('button', mode === 'unread' && scope.length ? 'すべての記事を表示' : '絞り込みをリセット', 'quiet-button'); reset.onclick = () => changeCollection(feed, mode === 'unread' && scope.length ? 'all' : 'unread', { query: '', date: 'all' }); empty.append(reset); list.append(empty);
  }
  for (const paper of visible) {
    const state = store.states[paper.id] || {}, row = text('div', '', 'paper-row'); row.dataset.id = paper.id; row.setAttribute('role', 'listitem'); row.classList.toggle('is-read', !!state.read); row.classList.toggle('selected', paper.id === selected);
    const swipe = text('div', state.read ? '未読に戻す' : '既読にする', 'swipe-label'); swipe.setAttribute('aria-hidden', 'true');
    const content = text('div', '', 'row-content'), open = text('button', '', 'paper-open'); open.setAttribute('aria-label', paper.title); open.setAttribute('aria-current', String(paper.id === selected));
    const title = text('h2', paper.title); title.lang = 'en'; open.append(title); if (paper.title_ja) open.append(text('p', paper.title_ja, 'paper-subtitle'));
    const meta = text('div', '', 'paper-meta'); meta.append(text('span', `${sourceLabel(paper.source)} ${paper.id}`, 'paper-identity'));
    const identity = meta.firstChild; identity.replaceChildren(text('span', sourceLabel(paper.source) + ' '), text('span', paper.id, 'paper-id'));
    const time = text('time', shortDate(paper.announcementDate || paper.date)); time.dateTime = paper.announcementDate || paper.date || ''; meta.append(time); open.append(meta, tagsFor(paper));
    open.onclick = () => { if (!gestures?.consumeClick(paper.id)) select(paper.id, paper.version); };
    const controls = text('div', '', 'row-controls');
    for (const field of ['starred', 'bad', 'read']) {
      const b = control(field, state[field], () => store.toggle(paper.id, field)); controls.append(b);
      const failure = errors.get(`${paper.id}:${field}`); if (failure) { const retry = text('button', '保存を再試行', 'inline-error'); retry.onclick = failure.retry; controls.append(retry); }
    }
    content.append(open, controls); row.append(swipe, content); list.append(row);
  }
  if (!compact()) writeScroll('list', scroll); renderFeeds(); renderActions();
}
function renderActions() {
  const state = store?.states[selected] || {}, index = visible.findIndex(p => p.id === selected);
  $('#article-actions').hidden = !selected;
  for (const [id, field] of [['read-state', 'read'], ['star-state', 'starred'], ['bad-state', 'bad']]) paintControl($(`#${id}`), field, state[field]);
  document.querySelectorAll('.inline-actions button').forEach(b => paintControl(b, b.dataset.field, state[b.dataset.field]));
  $('#previous').disabled = index <= 0; $('#next').disabled = index < 0 || index >= visible.length - 1; $('#article-position').textContent = index < 0 ? '' : `${index + 1} / ${visible.length}`;
}
function header(data, container, interactive = true) {
  const h = text('header', '', 'article-header'), metaLine = text('div', '', 'paper-meta-line'); metaLine.append(text('p', `${sourceLabel(data.source)} ${data.id} · ${shortDate(data.announcementDate || data.date)}`, 'article-meta'));
  if (interactive) { metaLine.append($('#version-label')); const controls = text('div', '', 'inline-actions'); for (const field of ['read', 'starred', 'bad']) controls.append(control(field, store.states[data.id]?.[field], () => store.toggle(data.id, field))); metaLine.append(controls); }
  h.append(metaLine); const title = text('h1', ''); title.lang = 'en'; const url = safeLink(data.source_url); if (url) { const link = text('a', data.title); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; title.append(link); } else title.textContent = data.title; h.append(title);
  if (data.title_ja) h.append(text('p', data.title_ja, 'japanese-title'));
  const authors = joined(data.authors), institutions = joined(data.institution_names || data.affiliations); if (authors) h.append(text('p', authors, 'authors')); if (institutions) h.append(text('p', institutions, 'institutions')); h.append(tagsFor(data)); container.append(h);
  if (data.lead_ja) { const lead = text('section', '', 'lead'); lead.setAttribute('aria-label', '3行要約'); for (const line of Array.isArray(data.lead_ja) ? data.lead_ja : String(data.lead_ja).split('\n')) if (line.trim()) lead.append(text('p', line)); container.append(lead); }
}
function bindArticleAnchors() {
  $('#article').querySelectorAll('a[href^="#"]').forEach(link => { link.onclick = event => { let id; try { id = decodeURIComponent(link.hash.slice(1)); } catch { return; } const target = [...$('#article').querySelectorAll('[id]')].find(el => el.id === id); if (!target) return; event.preventDefault(); const top = target.getBoundingClientRect().top; if (compact()) window.scrollTo({ top: window.scrollY + top - $('.article-toolbar').offsetHeight - 8, behavior: 'smooth' }); else $('#article-scroll').scrollTo({ top: $('#article-scroll').scrollTop + top - $('#article-scroll').getBoundingClientRect().top - 8, behavior: 'smooth' }); }; });
}
async function showArticle(scroll = 0) {
  gestures?.cancel(); $('#version-controls').append($('#version-label')); const generation = ++selectionGeneration, paper = papers.find(p => p.id === selected); document.body.classList.toggle('article-open', !!paper); renderList();
  if (!paper) {
    renderer.cancel(); const welcome = text('div', '', 'welcome'); welcome.append(text('p', 'YOUR READING DESK', 'section-label'), text('h2', '気になる論文から。'), text('p', '記事を選ぶと、本文と図をここに表示します。')); $('#article').replaceChildren(welcome); $('#version-label').hidden = true; $('#original').hidden = true; writeScroll('list', listScrollPosition); return;
  }
  const versions = [...new Set([paper.version || 'current', ...(paper.versionHistory || []).map(v => v.version)].filter(Boolean).map(String))];
  const selectBox = $('#version'); selectBox.replaceChildren(); for (const version of versions) { const option = text('option', version); option.value = version; selectBox.append(option); } selectBox.value = selectedVersion; $('#version-label').hidden = versions.length < 2;
  const data = versionData(paper, selectedVersion), url = safeLink(data.source_url); $('#original').hidden = !url; if (url) $('#original').href = url; else $('#original').removeAttribute('href');
  writeScroll('article', scroll);
  const body = await renderer.render($('#article'), selected, selectedVersion, header);
  if (generation === selectionGeneration) {
    const bodyURL = safeLink(body?.source_url); if (bodyURL) { $('#original').href = bodyURL; $('#original').hidden = false; }
    renderActions(); bindArticleAnchors(); requestAnimationFrame(() => { if (generation === selectionGeneration) writeScroll('article', scroll); });
    const index = visible.findIndex(p => p.id === selected); for (const neighbor of [visible[index - 1], visible[index + 1]]) if (neighbor) renderer.load(neighbor.id, neighbor.version || 'current').catch(() => {});
  }
}
function select(id, version, { push = true, markRead = true, scroll = 0 } = {}) {
  const paper = papers.find(p => p.id === id); if (!paper) return;
  if (push) savePosition(); selected = id; selectedVersion = String(version || paper.version || 'current'); articleScrollPosition = scroll;
  document.body.classList.remove('sidebar-open'); $('#menu').setAttribute('aria-expanded', 'false');
  if (push) writeHistory(); showArticle(scroll); if (markRead && !store.states[id]?.read) store.set(id, 'read', true);
}
function changeCollection(nextFeed = feed, nextMode = mode, patch = {}) {
  gestures?.cancel(); savePosition(); feed = nextFeed; mode = nextMode; query = patch.query ?? query; sort = patch.sort ?? sort; date = patch.date ?? date; snapshot(); selected = null; selectedVersion = null; listScrollPosition = 0; articleScrollPosition = 0;
  document.body.classList.remove('sidebar-open', 'article-open'); $('#menu').setAttribute('aria-expanded', 'false'); syncControls(); renderList(); showArticle(); writeHistory();
}
function move(delta) { const index = visible.findIndex(p => p.id === selected), next = visible[index < 0 ? 0 : index + delta]; if (next) select(next.id, next.version); }
function action(name) {
  if (!ready) return;
  if (name === 'next' || name === 'previous') return move(name === 'next' ? 1 : -1);
  if (name === 'nextFeed' || name === 'previousFeed') { const i = feeds.findIndex(f => f.id === feed), next = feeds[i + (name === 'nextFeed' ? 1 : -1)]; if (next) changeCollection(next.id, ['starred', 'bad'].includes(next.id) ? 'all' : 'unread'); return; }
  if (selected && (name === 'starred' || name === 'bad')) store.toggle(selected, name);
  if (name === 'original' && selected && !$('#original').hidden) window.open($('#original').href, '_blank', 'noopener,noreferrer');
}
function openSidebar() { savePosition(); gestures?.cancel(); document.body.classList.add('sidebar-open'); $('#menu').setAttribute('aria-expanded', 'true'); if (compact()) window.scrollTo({ top: 0, left: 0, behavior: 'auto' }); }
function closeSidebar() { gestures?.cancel(); document.body.classList.remove('sidebar-open'); $('#menu').setAttribute('aria-expanded', 'false'); writeScroll(articleOpen() ? 'article' : 'list', articleOpen() ? articleScrollPosition : listScrollPosition); }
function events() {
  $('#search').oninput = event => changeCollection(feed, mode, { query: event.target.value }); $('#read-filter').onchange = event => changeCollection(feed, event.target.value); $('#sort').onchange = event => changeCollection(feed, mode, { sort: event.target.value }); $('#batch-date').onchange = event => changeCollection(feed, mode, { date: event.target.value }); $('#reset').onclick = () => changeCollection(feed, 'unread', { query: '', sort: 'date', date: 'all' });
  $('#bulk').onclick = () => store.bulkRead(scoped().map(p => p.id)); $('#menu').onclick = openSidebar; $('#close-feeds').onclick = closeSidebar;
  $('#back').onclick = () => { gestures?.cancel(); savePosition(); selected = null; selectedVersion = null; showArticle(); writeHistory(); };
  $('#version').onchange = event => select(selected, event.target.value, { markRead: false }); $('#previous').onclick = () => move(-1); $('#next').onclick = () => move(1);
  for (const [id, field] of [['read-state', 'read'], ['star-state', 'starred'], ['bad-state', 'bad']]) $(`#${id}`).onclick = () => selected && store.toggle(selected, field);
  document.addEventListener('keydown', event => { const command = shortcut(event); if (command) { event.preventDefault(); action(command); } if (event.key === 'Escape' && document.body.classList.contains('sidebar-open')) closeSidebar(); });
  window.addEventListener('popstate', event => {
    const state = event.state; if (!state?.reader) return; gestures?.cancel(); feed = state.feed; mode = state.mode; query = state.query || ''; sort = state.sort || 'date'; date = state.date || 'all';
    visible = state.ids.map(entry => { const p = papers.find(p => p.id === (entry.id || entry)); return p && entry.version ? { ...versionData(p, entry.version), id: p.id } : p; }).filter(Boolean); selected = state.selected; selectedVersion = state.version; listScrollPosition = state.listScroll || 0; articleScrollPosition = state.articleScroll || 0;
    document.body.classList.remove('sidebar-open'); syncControls(); renderFeeds(); showArticle(articleScrollPosition);
  });
  let scrollTimer; for (const pane of [window, $('#list'), $('#article-scroll')]) pane.addEventListener('scroll', () => { clearTimeout(scrollTimer); scrollTimer = setTimeout(savePosition, 100); }, { passive: true });
  window.addEventListener('resize', () => gestures?.cancel());
  gestures = bindReaderGestures({ compact, selected: () => selected, visible: () => visible, isRead: id => !!store.states[id]?.read, toggleRead: id => store.toggle(id, 'read'), openSidebar, closeSidebar, select: p => select(p.id, p.version), preview: async p => { const body = await renderer.load(p.id, p.version || 'current'), article = text('article', ''); header(body, article, false); const narrative = text('div', '', 'narrative'); narrative.append(safeHTML(body.html)); article.append(narrative); typesetMath(narrative); return article; } });
}
async function boot() {
  try {
    const [data, reading, preferences, batch] = await Promise.all([json('/papers.json'), json('/api/reading-state'), json('/api/article-preferences'), json('/api/batch-status').catch(() => null)]);
    papers = Array.isArray(data) ? data : data.articles || [];
    const initial = Object.create(null); for (const p of papers) initial[p.id] = { read: reading.readIds.includes(p.id), starred: preferences.starredIds.includes(p.id), bad: preferences.badIds.includes(p.id) };
    store = createStateStore(initial, async (id, field, value) => {
      const operation = field === 'read' ? value ? 'read' : 'unread' : field === 'starred' ? value ? 'star' : 'unstar' : value ? 'bad' : 'unbad';
      await json(field === 'read' ? '/api/reading-state' : '/api/article-preferences', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ articleId: id, operation }) }); errors.delete(`${id}:${field}`); renderList();
    }, () => { if (ready) renderList(); }, failure => { errors.set(`${failure.id}:${failure.field}`, failure); renderList(); notification('状態を保存できなかったため表示を戻しました。', failure.retry); });
    feeds = [{ id: 'all', label: 'すべての記事' }, ...[...new Set(papers.map(p => p.source).filter(Boolean))].sort().map(source => ({ id: `source:${source}`, label: sourceLabel(source) })), { id: 'starred', label: 'スター' }, { id: 'bad', label: 'bad' }];
    const dates = [...new Set(papers.flatMap(p => [p.announcementDate || p.date, ...(p.versionHistory || []).flatMap(v => [v.announcementDate || v.date, ...(v.announcementDates || [])])]).filter(Boolean))].sort().reverse(); for (const d of dates) { const option = text('option', shortDate(d)); option.value = d; $('#batch-date').append(option); }
    snapshot(); ready = true; syncControls(); renderList();
    const latest = papers.reduce((a, p) => Date.parse(p.updated_at || p.date) > Date.parse(a || '1970-01-01') ? p.updated_at || p.date : a, null); $('#updated').textContent = `最終更新 ${formatUpdated(batch?.updated_at || data.updated_at || latest)}`;
    history.scrollRestoration = 'manual'; events(); const url = new URL(location.href), id = url.searchParams.get('article'); if (id) select(id, url.searchParams.get('version'), { push: false }); history.replaceState(capture(), '', location.href);
  } catch (error) { $('#list').replaceChildren(text('p', '記事と保存状態を読み込めませんでした。', 'empty')); notification(error.message, boot); }
}
boot();
