(async () => {
  window.__dd = {state: 'run'};
  const find = (pred) => { const out = []; const visit = (w, d) => { if (d > 6) return; try { if (pred(w)) out.push(w); [...w.document.querySelectorAll('iframe,frame')].forEach(f => { try { visit(f.contentWindow, d + 1); } catch (e) {} }); } catch (e) {} }; visit(window, 0); return out; };
  try {
    const sp = find(w => w.__tsOfflineData && w.__tsOfflineData['project.xml']);
    const sl = find(w => w.DS || w.globalProvideData || w.document.querySelector('#slide-window'));
    const rise = find(w => w.document.querySelector('[class*="lesson"], #app [class*="blocks-"]') || /rise/i.test(w.location.pathname) || w.__riseCourse);
    if (sp.length) {
      const src = await (await fetch('http://127.0.0.1:8766/inject.js?v=' + Date.now(), {cache: 'no-store'})).text(); new Function(src)();
      const r = await window.__cpeSilverpoint.dump(() => {}); window.__cpeSilverpoint.download(r.json, r.name);
      window.__dd = {state: 'done', type: 'silverpoint', name: r.name, pages: r.pages, images: r.images, missing: r.missing};
    } else if (sl.length) {
      const src = await (await fetch('http://127.0.0.1:8766/console-storyline.js?v=' + Date.now(), {cache: 'no-store'})).text();
      window.__slLog = []; const ol = console.log; console.log = (...a) => { window.__slLog.push(a.join(' ')); ol(...a); };
      (0, eval)(src);
      for (let i = 0; i < 150 && !window.__slLog.some(l => /klaar|fout/.test(l)); i++) await new Promise(r => setTimeout(r, 1000));
      window.__dd = {state: 'done', type: 'storyline', log: window.__slLog.slice(-1)[0]};
    } else {
      window.__dd = {state: 'done', type: rise.length ? 'rise' : 'unknown', title: document.title, frames: find(() => true).map(w => { try { return w.location.pathname.slice(-50); } catch (e) { return 'x'; } })};
    }
  } catch (e) { window.__dd = {state: 'done', type: 'error', err: String(e)}; }
})();
