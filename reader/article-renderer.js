import DOMPurify from 'dompurify';
import renderMathInElement from 'katex/contrib/auto-render';
import 'katex/dist/katex.min.css';

export function safeHTML(html, doc = document) {
  const purifier = typeof DOMPurify.sanitize === 'function' ? DOMPurify : DOMPurify(doc.defaultView);
  const fragment = purifier.sanitize(String(html || ''), { RETURN_DOM_FRAGMENT: true, USE_PROFILES: { html: true }, FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe', 'object', 'embed', 'script'], FORBID_ATTR: ['style', 'srcset', 'id', 'name'] });
  for (const image of fragment.querySelectorAll('img')) {
    const src = image.getAttribute('src') || '';
    // Only the authenticated, manifest-checked image route may load an image.
    if (!/^\/api\/images\//.test(src) || src.includes('..') || src.includes('\\')) image.removeAttribute('src');
    image.loading = 'lazy'; image.decoding = 'async';
  }
  for (const anchor of fragment.querySelectorAll('a')) {
    anchor.rel = 'noopener noreferrer';
    if (anchor.getAttribute('href')?.startsWith('http')) anchor.target = '_blank';
  }
  return fragment;
}
export function typesetMath(element) {
  renderMathInElement(element, { delimiters: [{ left: '\\[', right: '\\]', display: true }, { left: '\\(', right: '\\)', display: false }], throwOnError: false, strict: 'ignore', trust: false });
}
export function enhanceImages(element) {
  for (const img of element.querySelectorAll('img')) {
    img.loading = 'lazy'; img.decoding = 'async';
    const retry = document.createElement('button');
    retry.className = 'image-retry'; retry.textContent = '画像を再読み込み'; retry.hidden = true;
    img.after(retry);
    img.addEventListener('error', () => { retry.hidden = false; });
    img.addEventListener('load', () => { retry.hidden = true; });
    retry.addEventListener('click', () => {
      retry.hidden = true;
      const src = img.getAttribute('src');
      if (src) { img.removeAttribute('src'); requestAnimationFrame(() => img.setAttribute('src', src)); }
    });
  }
}
export function createArticleRenderer({ fetcher = fetch, cacheLimit = 30 } = {}) {
  const cache = new Map();
  let generation = 0;
  async function load(id, version) {
    const key = `${encodeURIComponent(id)}/${encodeURIComponent(version)}`;
    if (cache.has(key)) return cache.get(key);
    const response = await fetcher(`/reader-articles/${key}.json`, { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`本文を読み込めません (${response.status})`);
    const body = await response.json();
    if (body.id !== id || String(body.version) !== String(version)) throw new Error('記事と版の対応を確認できません');
    cache.set(key, body);
    if (cache.size > cacheLimit) cache.delete(cache.keys().next().value);
    return body;
  }
  return {
    load,
    cancel: () => { generation++; },
    async render(container, id, version, renderHeader = () => {}) {
      const current = ++generation;
      container.replaceChildren(); container.setAttribute('aria-busy', 'true');
      const status = document.createElement('p'); status.className = 'article-status'; status.textContent = '本文を読み込み中…'; container.append(status);
      try {
        const body = await load(id, version);
        if (current !== generation) return null;
        container.replaceChildren(); renderHeader(body, container);
        const narrative = document.createElement('div'); narrative.className = 'narrative'; narrative.append(safeHTML(body.html)); container.append(narrative);
        typesetMath(narrative); enhanceImages(narrative);
        return body;
      } catch (error) {
        if (current !== generation) return null;
        status.textContent = error.message;
        const button = document.createElement('button'); button.textContent = '再試行';
        button.onclick = () => this.render(container, id, version, renderHeader); container.append(button);
        return null;
      } finally { if (current === generation) container.removeAttribute('aria-busy'); }
    }
  };
}
