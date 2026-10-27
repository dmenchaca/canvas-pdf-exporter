(async () => {
  window.__rd = {state: 'run'};
  try {
    let win = null;
    const visit = (w, d) => { if (d > 6 || win) return; try { if (/scormcontent\/index\.html/.test(w.location.pathname)) { win = w; return; } [...w.document.querySelectorAll('iframe,frame')].forEach(f => { try { visit(f.contentWindow, d + 1); } catch (e) {} }); } catch (e) {} };
    visit(window, 0);
    if (!win) { window.__rd = {state: 'done', err: 'no rise frame'}; return; }
    let data = null, how = '';
    if (typeof win.__fetchCourse === 'function') { data = await win.__fetchCourse(); how = 'fetchCourse'; }
    else {
      const base = win.location.href.replace(/[#?].*$/, '').replace(/[^/]*$/, '');
      const resp = await fetch(base + 'runtime-data.js', {credentials: 'include'});
      const m = resp.ok && (await resp.text()).match(/__jsonp\(\s*"[^"]*"\s*,\s*"([^"]*)"/);
      if (m) { const bin = atob(m[1]); const bytes = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i); data = JSON.parse(new TextDecoder('utf-8').decode(bytes)); how = 'runtime-data'; }
    }
    if (!data) { window.__rd = {state: 'done', err: 'no data'}; return; }
    const course = data.course || data;
    const name = (course.title || document.title || 'cursus').replace(/[<>:"/\\|?*]/g, '').replace(/\s+/g, '-').slice(0, 100) + '.rise.json';
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(data)], {type: 'application/json'})); a.download = name; document.body.appendChild(a); a.click(); a.remove();
    window.__rd = {state: 'done', name, how, lessons: (course.lessons || []).length};
  } catch (e) { window.__rd = {state: 'done', err: String(e)}; }
})();
