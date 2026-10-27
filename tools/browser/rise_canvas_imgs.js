(async () => {
  window.__ci = {state: 'run'};
  try {
    let win = null;
    const visit = (w, d) => { if (d > 6 || win) return; try { if (/scormcontent\/index\.html/.test(w.location.pathname)) { win = w; return; } [...w.document.querySelectorAll('iframe,frame')].forEach(f => { try { visit(f.contentWindow, d + 1); } catch (e) {} }); } catch (e) {} };
    visit(window, 0);
    const base = win.location.href.replace(/[#?].*$/, '').replace(/[^/]*$/, '');
    let data = null;
    if (typeof win.__fetchCourse === 'function') data = await win.__fetchCourse();
    else { const t = await (await fetch(base + 'runtime-data.js')).text(); const m = t.match(/__jsonp\(\s*"[^"]*"\s*,\s*"([^"]*)"/); const bin = atob(m[1]); const b = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) b[i] = bin.charCodeAt(i); data = JSON.parse(new TextDecoder().decode(b)); }
    const course = data.course || data;
    const items = (course.mondrian || {}).items || {};
    const out = {};
    for (const it of Object.values(items)) {
      if (it.type !== 'image') continue;
      const fill = ((it.states || {}).default || {}).fill || {};
      const a = (it.assets || {})[fill.assetId];
      if (!a || out[a.path]) continue;
      try {
        const blob = await (await fetch(base + 'assets/' + a.path)).blob();
        const bmp = await createImageBitmap(blob);
        const s = Math.min(1, 1400 / bmp.width);
        const c = document.createElement('canvas'); c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
        c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
        out[a.path] = c.toDataURL('image/jpeg', 0.85);
      } catch (e) { out[a.path] = 'ERR ' + e; }
    }
    const name = (course.title || 'cursus').replace(/[<>:"/\\|?*]/g, '').replace(/\s+/g, '-') + '.canvasimg.json';
    const el = document.createElement('a'); el.href = URL.createObjectURL(new Blob([JSON.stringify(out)], {type: 'application/json'})); el.download = name; document.body.appendChild(el); el.click(); el.remove();
    window.__ci = {state: 'done', name, n: Object.keys(out).length, err: Object.values(out).filter(v => v.startsWith('ERR')).length};
  } catch (e) { window.__ci = {state: 'done', err: String(e)}; }
})();
