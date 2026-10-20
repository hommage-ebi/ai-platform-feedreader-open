import { gestureBlocked } from './reader-core.js';

export function bindReaderGestures(api) {
  let gesture = null, layer = null, settleTimer = null, touchActive = false, suppressed = null, revision = 0;
  const $ = selector => document.querySelector(selector);
  const selectionActive = () => !!window.getSelection()?.toString();
  function passiveCopy(node) {
    const copy = node.cloneNode(true); copy.removeAttribute('id'); copy.inert = true; copy.setAttribute('aria-hidden', 'true');
    copy.querySelectorAll('[id],[name]').forEach(el => { el.removeAttribute('id'); el.removeAttribute('name'); });
    copy.querySelectorAll('a,button,input,select,textarea,[tabindex]').forEach(el => el.setAttribute('tabindex', '-1'));
    return copy;
  }
  function clear() {
    revision++; if (settleTimer) clearTimeout(settleTimer); settleTimer = null;
    layer?.remove(); layer = null; if (gesture?.row) gesture.row.style.transform = '';
    gesture = null; document.body.classList.remove('sidebar-preview');
  }
  function sidebarLayer(g) {
    const root = document.createElement('div'); root.className = 'sidebar-swipe-layer'; root.inert = true; root.setAttribute('aria-hidden', 'true');
    const backdrop = document.createElement('div'); backdrop.className = 'sidebar-swipe-backdrop';
    const drawer = passiveCopy($('#sidebar')); drawer.classList.add('sidebar-drawer'); root.append(backdrop, drawer); document.body.append(root);
    layer = root; g.drawer = drawer; g.backdrop = backdrop;
    if (!g.closing) document.body.classList.add('sidebar-preview');
  }
  function articleLayer(g, dx) {
    const live = $('#article'), toolbar = $('.article-toolbar'), actions = $('#article-actions');
    const top = Math.max(0, toolbar.getBoundingClientRect().bottom), bottom = Math.max(top, innerHeight - actions.getBoundingClientRect().height);
    const root = document.createElement('div'); root.className = 'article-swipe-layer'; root.style.top = `${top}px`; root.style.height = `${bottom - top}px`; root.inert = true; root.setAttribute('aria-hidden', 'true');
    const current = document.createElement('div'); current.className = 'article-swipe-page';
    const copy = passiveCopy(live); copy.style.position = 'relative'; copy.style.top = `${live.getBoundingClientRect().top - top}px`; current.append(copy);
    const neighbor = document.createElement('div'); neighbor.className = 'article-swipe-page article-swipe-neighbor'; root.append(current, neighbor); document.body.append(root); layer = root; g.current = current; g.neighbor = neighbor;
    loadNeighbor(g, dx < 0 ? 1 : -1);
  }
  function loadNeighbor(g, direction) {
    const list = api.visible(), index = list.findIndex(p => p.id === api.selected()), next = list[index + direction]; g.direction = direction; g.next = next; g.neighbor.replaceChildren();
    const currentRevision = revision;
    if (next) api.preview(next).then(article => { if (gesture === g && revision === currentRevision && g.direction === direction) g.neighbor.replaceChildren(article); }).catch(() => {});
  }
  function start(target, x, y, pointerId) {
    clear(); if (!api.compact() || selectionActive() || x <= 16) return;
    const sidebar = document.body.classList.contains('sidebar-open'), row = target.closest('.paper-row'), safeRow = row && !target.closest('.row-controls') && !sidebar;
    const blocked = safeRow ? false : gestureBlocked(target);
    if (blocked) return;
    const kind = sidebar ? 'sidebar' : x <= 56 ? 'sidebar' : safeRow ? 'row' : target.closest('#article-pane') && api.selected() ? 'article' : null;
    if (!kind) return;
    gesture = { kind, startX: x, startY: y, x, y, pointerId, closing: sidebar, width: innerWidth, row: kind === 'row' ? row.querySelector('.row-content') : null, id: row?.dataset.id, start: Date.now(), lastTime: Date.now(), lastX: x, velocity: 0, locked: false };
  }
  function preview(x, y) {
    const g = gesture; if (!g) return false;
    const now = Date.now(); if (x !== g.lastX) { g.velocity = (x - g.lastX) / Math.max(1, now - g.lastTime); g.lastX = x; g.lastTime = now; } else if (now - g.lastTime > 100) g.velocity = 0;
    g.x = x; g.y = y; const dx = x - g.startX, dy = y - g.startY;
    if (!g.locked) {
      if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) { clear(); return false; }
      if (Math.abs(dx) < 8 || Math.abs(dx) < Math.abs(dy) * 1.8) return false;
      if (g.kind === 'row' && dx < 0 || g.kind === 'sidebar' && (g.closing ? dx > 0 : dx < 0)) { clear(); return false; }
      g.locked = true;
      if (g.kind === 'sidebar') sidebarLayer(g);
      if (g.kind === 'article') articleLayer(g, dx);
    }
    if (g.kind === 'row') g.row.style.transform = `translateX(${Math.min(112, Math.max(0, dx))}px)`;
    else if (g.kind === 'sidebar') {
      const shift = g.closing ? Math.max(-g.width, Math.min(0, dx)) : Math.min(0, -g.width + Math.max(0, dx)); g.drawer.style.transform = `translate3d(${shift}px,0,0)`; g.backdrop.style.opacity = String(.2 * (1 + shift / g.width));
    } else {
      const direction = dx < 0 ? 1 : -1; if (direction !== g.direction) loadNeighbor(g, direction);
      const shift = g.next ? dx : dx * .2; g.current.style.transform = `translate3d(${shift}px,0,0)`; g.neighbor.style.transform = `translate3d(${shift + g.direction * g.width}px,0,0)`;
    }
    return true;
  }
  function end(cancelled) {
    const g = gesture; if (!g) return;
    if (cancelled || !g.locked || selectionActive()) { clear(); return; }
    const dx = g.x - g.startX, dy = g.y - g.startY, horizontal = Math.abs(dx) > Math.abs(dy) * 1.8;
    if (g.kind === 'row') {
      const commit = dx > Math.abs(dy) * 1.5 && dx >= 72 && Date.now() - g.start < 1000, id = g.id; suppressed = { id, until: Date.now() + 500 }; clear(); if (commit) api.toggleRead(id); return;
    }
    const distance = Math.abs(dx), sameDirection = Math.sign(g.velocity) === Math.sign(dx);
    const threshold = g.kind === 'sidebar' ? Math.max(72, Math.min(150, g.width * .28)) : Math.max(72, Math.min(120, g.width * .24));
    const fast = distance >= (g.kind === 'sidebar' ? 36 : 40) && Math.abs(g.velocity) >= (g.kind === 'sidebar' ? .45 : .55) && sameDirection;
    const commit = Math.abs(dx) > Math.abs(dy) * 2 && (distance >= threshold || fast), root = layer;
    if (!root) { clear(); return; } root.classList.add('is-settling');
    if (g.kind === 'sidebar') {
      const opens = commit ? !g.closing : g.closing; g.drawer.style.transform = `translate3d(${opens ? 0 : -g.width}px,0,0)`; g.backdrop.style.opacity = opens ? '.2' : '0';
      settleTimer = setTimeout(() => { clear(); if (commit) g.closing ? api.closeSidebar() : api.openSidebar(); }, 180);
    } else {
      const goes = commit && g.next; g.current.style.transform = `translate3d(${goes ? -g.direction * g.width : 0}px,0,0)`; g.neighbor.style.transform = `translate3d(${goes ? 0 : g.direction * g.width}px,0,0)`;
      settleTimer = setTimeout(() => { clear(); if (goes) api.select(g.next); }, 180);
    }
  }
  document.addEventListener('touchstart', event => {
    touchActive = true; if (event.touches.length !== 1) { clear(); return; } const t = event.touches[0]; start(event.target, t.clientX, t.clientY, t.identifier);
  }, { passive: true });
  document.addEventListener('touchmove', event => {
    if (!gesture) return; if (event.touches.length !== 1) { clear(); return; } const t = [...event.touches].find(t => t.identifier === gesture.pointerId); if (t && preview(t.clientX, t.clientY) && event.cancelable) event.preventDefault();
  }, { passive: false });
  document.addEventListener('touchend', event => {
    const t = gesture && [...event.changedTouches].find(t => t.identifier === gesture.pointerId); if (t) { preview(t.clientX, t.clientY); end(false); } if (!event.touches.length) touchActive = false;
  });
  document.addEventListener('touchcancel', () => { clear(); touchActive = false; });
  document.addEventListener('pointerdown', event => { if (!touchActive && event.pointerType === 'touch' && event.isPrimary) start(event.target, event.clientX, event.clientY, event.pointerId); }, { passive: true });
  document.addEventListener('pointermove', event => { if (!touchActive && gesture && gesture.pointerId === event.pointerId) preview(event.clientX, event.clientY); }, { passive: true });
  document.addEventListener('pointerup', event => { if (!touchActive && gesture && gesture.pointerId === event.pointerId) { preview(event.clientX, event.clientY); end(false); } });
  document.addEventListener('pointercancel', clear);
  return { cancel: clear, consumeClick(id) { return suppressed?.id === id && suppressed.until > Date.now(); } };
}
