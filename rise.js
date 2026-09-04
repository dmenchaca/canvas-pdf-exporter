// Mode B: Articulate Rise-cursus in de nieuwe Canvas/Rustici "modern" speler.
//
// Werkwijze: de tekst van elke les staat als gewone HTML in een (geneste) iframe.
// We zetten de opmaak tijdelijk om naar een doorlopende, printbare pagina, laten
// Chrome zelf een PDF renderen (Page.printToPDF via chrome.debugger) en voegen de
// losse les-PDF's samen met pdf-lib. Het resultaat is een PDF met echte,
// selecteerbare tekst in plaats van aan elkaar geplakte schermafbeeldingen.

let riseStopRequested = false;

// Opmaak die in het Rise-document wordt geïnjecteerd tijdens het exporteren.
const RISE_PRINT_CSS = `
  html, body { height: auto !important; overflow: visible !important; }
  #app, #innerApp, .lesson-progress-wrapper, .page-lesson-wrap, .page-lesson, .lesson,
  .lesson__content, .page-view, .lessonNavigation__wrapper, .page-wrap, .page, .page__wrapper {
    height: auto !important; max-height: none !important; min-height: 0 !important;
    overflow: visible !important; position: static !important;
    transform: none !important; animation: none !important; transition: none !important;
  }
  .page-wrap { margin: 0 !important; width: auto !important; box-shadow: none !important; }
  .course-navigation__sidebar, .nav-control__wrapper, #nav-sidebar, .navButtonsFull,
  .lesson-nav, .progress-wrap, .btn-skip-to-lesson, .lesson-progress__tooltip,
  .continue-hint, .block-divider__button, .continue-btn { display: none !important; }
  .scroll-animation, .block-list__animated-item {
    opacity: 1 !important; transform: none !important; visibility: visible !important;
    animation: none !important; transition: none !important;
  }
  .blocks-accordion__content {
    display: block !important; height: auto !important; max-height: none !important;
    overflow: visible !important; visibility: visible !important; opacity: 1 !important;
  }
  .block-image, .block-quote, .block-knowledge, .block-list__item, .block-statement,
  .block-video, .block-gallery, .block-chart, .block-attachment { break-inside: avoid; }
  .lesson-header-wrap { break-after: avoid; }
  .page__header { min-height: 0 !important; }
`;

// Wordt eenmalig in de pagina (isolated world) geïnstalleerd en levert helpers op
// window.__cpeRise. Alle functies moeten zelfstandig zijn: ze worden geserialiseerd.
function riseInstallHelpers() {
  const H = {};
  H.sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // Zoekt (door same-origin iframes heen) het document waarin de Rise-cursus draait.
  // Geeft { riseDoc, chain } terug; chain = [{ doc, iframe }] van boven naar beneden.
  H.walk = function () {
    const isRise = (doc) =>
      doc.getElementById('nav-sidebar-outline-list') ||
      doc.querySelector('.blocks-lesson');
    const visit = (doc, path) => {
      if (isRise(doc)) return { riseDoc: doc, chain: path };
      for (const f of doc.querySelectorAll('iframe')) {
        let cd = null;
        try { cd = f.contentDocument; } catch (e) { /* cross-origin */ }
        if (!cd || !cd.documentElement) continue;
        const r = visit(cd, path.concat([{ doc, iframe: f }]));
        if (r) return r;
      }
      return null;
    };
    return visit(document, []);
  };

  H.setImportant = function (el, props) {
    if (!el) return;
    if (!el.hasAttribute('data-cpe-style')) {
      el.setAttribute('data-cpe-style', el.getAttribute('style') || '');
    }
    for (const [k, v] of Object.entries(props)) el.style.setProperty(k, v, 'important');
  };

  H.restoreEl = function (el) {
    const o = el.getAttribute('data-cpe-style');
    if (o === null) return;
    if (o) el.setAttribute('style', o); else el.removeAttribute('style');
    el.removeAttribute('data-cpe-style');
  };

  // Overzicht van alle (geneste) frames en wat erin gevonden wordt, voor het logboek.
  H.diagnose = function () {
    const count = (doc, sel) => { try { return doc.querySelectorAll(sel).length; } catch (e) { return -1; } };
    const describe = (doc, depth, out) => {
      const entry = {
        depth,
        url: (doc.location && doc.location.href || '').substring(0, 160),
        title: (doc.title || '').substring(0, 80),
        readyState: doc.readyState,
        iframes: count(doc, 'iframe'),
        navOutlineList: count(doc, '#nav-sidebar-outline-list'),
        outlineLinks: count(doc, '#nav-sidebar-outline-list a.nav-sidebar__outline-item__link'),
        anyOutlineLinks: count(doc, 'a.nav-sidebar__outline-item__link'),
        blocksLesson: count(doc, '.blocks-lesson'),
        pageWrap: count(doc, '.page-wrap'),
        navSidebar: count(doc, '#nav-sidebar'),
        scormContent: count(doc, '#ScormContent'),
        outerClipDiv: count(doc, '#outerClipDiv')
      };
      out.push(entry);
      doc.querySelectorAll('iframe').forEach((f, i) => {
        let cd = null, err = null;
        try { cd = f.contentDocument; } catch (e) { err = e.message; }
        if (cd && cd.documentElement) {
          describe(cd, depth + 1, out);
        } else {
          out.push({ depth: depth + 1, iframe: i, id: f.id, name: f.name, src: (f.src || '').substring(0, 160), inaccessible: true, error: err });
        }
      });
    };
    const out = [];
    describe(document, 0, out);
    return out;
  };

  H.info = function () {
    const w = H.walk();
    if (!w) return null;
    const rd = w.riseDoc;
    const lessons = [];
    rd.querySelectorAll('#nav-sidebar-outline-list a.nav-sidebar__outline-item__link').forEach((a) => {
      const li = a.closest('li');
      const idFromLi = li && li.id ? li.id.replace(/^nav-outline-lesson-/, '') : '';
      const m = (a.getAttribute('href') || '').match(/#\/lessons\/([^/?#]+)/);
      const id = idFromLi || (m ? m[1] : '');
      const title = (a.textContent || '').replace(/\s+/g, ' ').trim();
      if (id) lessons.push({ id, title, href: a.href });
    });
    const titleEl = rd.getElementById('nav-sidebar-title');
    const courseTitle = (titleEl && titleEl.textContent.trim()) || rd.title || document.title || 'canvas-export';
    return {
      lessons,
      courseTitle,
      currentHref: rd.defaultView.location.href,
      depth: w.chain.length,
      riseDocUrl: (rd.location.href || '').substring(0, 160),
      outlineListFound: !!rd.getElementById('nav-sidebar-outline-list'),
      anyOutlineLinks: rd.querySelectorAll('a.nav-sidebar__outline-item__link').length,
      blocksLesson: rd.querySelectorAll('.blocks-lesson').length
    };
  };

  H.applyLayout = function (css) {
    const w = H.walk();
    if (!w) return false;
    const rd = w.riseDoc;
    let st = rd.getElementById('cpe-rise-print-style');
    if (!st) {
      st = rd.createElement('style');
      st.id = 'cpe-rise-print-style';
      (rd.head || rd.documentElement).appendChild(st);
    }
    st.textContent = css;

    // Generieke fallback: alle voorouders van de lesinhoud statisch en op auto-hoogte.
    const flat = {
      position: 'static', height: 'auto', 'max-height': 'none', 'min-height': '0',
      overflow: 'visible', transform: 'none', animation: 'none', transition: 'none'
    };
    let el = rd.querySelector('.blocks-lesson') || rd.querySelector('.page-wrap');
    while (el && el !== rd.body) { H.setImportant(el, flat); el = el.parentElement; }

    // Alle bovenliggende documenten en iframes.
    for (const { doc, iframe } of w.chain) {
      H.setImportant(doc.documentElement, { height: 'auto', overflow: 'visible' });
      H.setImportant(doc.body, { height: 'auto', overflow: 'visible', margin: '0' });
      let p = iframe;
      while (p && p !== doc.body) {
        H.setImportant(p, Object.assign({}, flat, { inset: 'auto' }));
        p = p.parentElement;
      }
      H.setImportant(iframe, { display: 'block', width: '100%', border: '0' });
    }
    return true;
  };

  // Zet elke iframe op de volledige hoogte van zijn inhoud, van binnen naar buiten.
  H.resize = function () {
    const w = H.walk();
    if (!w) return 0;
    let h = w.riseDoc.documentElement.scrollHeight;
    for (let i = w.chain.length - 1; i >= 0; i--) {
      const { doc, iframe } = w.chain[i];
      iframe.style.setProperty('height', h + 'px', 'important');
      iframe.style.setProperty('min-height', h + 'px', 'important');
      h = doc.documentElement.scrollHeight;
    }
    return h;
  };

  H.restore = function (href) {
    const w = H.walk();
    if (!w) return false;
    const st = w.riseDoc.getElementById('cpe-rise-print-style');
    if (st) st.remove();
    w.riseDoc.querySelectorAll('[data-cpe-style]').forEach(H.restoreEl);
    for (const { doc } of w.chain) doc.querySelectorAll('[data-cpe-style]').forEach(H.restoreEl);
    if (href) w.riseDoc.defaultView.location.href = href;
    window.scrollTo(0, 0);
    return true;
  };

  H.goto = function (href) {
    const w = H.walk();
    if (!w) return false;
    w.riseDoc.defaultView.location.href = href; // alleen de hash verandert: geen herlaad
    return true;
  };

  H.lessonReady = function (id) {
    const w = H.walk();
    if (!w) return false;
    const page = w.riseDoc.getElementById(id);
    return !!(page && page.classList.contains('page-transition-complete') && page.querySelector('.blocks-lesson'));
  };

  // Maakt verborgen inhoud zichtbaar (Continue-knoppen, accordeons), laat lazy
  // afbeeldingen laden door de pagina door te scrollen en wacht tot alles klaar is.
  H.prepare = async function (settle) {
    const w = H.walk();
    if (!w) return null;
    const rd = w.riseDoc;
    let continues = 0;
    for (let round = 0; round < 40; round++) {
      H.resize();
      const btn = rd.querySelector('.continue-btn:not([disabled]), button.block-divider__button:not([disabled])');
      if (!btn) break;
      btn.scrollIntoView({ block: 'center' });
      btn.click();
      continues++;
      await H.sleep(settle);
    }
    rd.querySelectorAll('.blocks-accordion__header, .blocks-accordion__header button').forEach((h) => {
      if (h.getAttribute('aria-expanded') === 'false') h.click();
    });
    await H.sleep(settle);

    const total = H.resize();
    const step = Math.max(300, Math.floor(window.innerHeight * 0.75));
    for (let y = 0; y <= total; y += step) {
      window.scrollTo(0, y);
      await H.sleep(80);
    }
    window.scrollTo(0, 0);

    const pending = Array.from(rd.images).filter((i) => !i.complete);
    await Promise.race([
      Promise.all(pending.map((i) => new Promise((r) => {
        i.addEventListener('load', r, { once: true });
        i.addEventListener('error', r, { once: true });
      }))),
      H.sleep(8000)
    ]);
    await H.sleep(settle);
    return { height: H.resize(), continues, images: rd.images.length };
  };

  window.__cpeRise = H;
  return true;
}

async function riseExec(tabId, func, args = []) {
  try {
    const [r] = await chrome.scripting.executeScript({ target: { tabId }, func, args });
    return r ? r.result : undefined;
  } catch (e) {
    Log.error(`executeScript mislukt (${func.name || 'anoniem'})`, e);
    throw e;
  }
}

async function riseEnsureHelpers(tabId) {
  const has = await riseExec(tabId, () => !!window.__cpeRise);
  if (!has) {
    await riseExec(tabId, riseInstallHelpers);
    Log.debug('Rise-helpers geïnstalleerd in pagina');
  }
}

// Logt de frame-structuur: eerst via de top-frame (door same-origin iframes heen),
// daarna per frame via allFrames zodat ook cross-origin frames zichtbaar worden.
async function logFrameDiagnostics(tabId) {
  try {
    await riseEnsureHelpers(tabId);
    const tree = await riseExec(tabId, () => window.__cpeRise.diagnose());
    Log.info('Frame-structuur vanuit top-frame', tree);
  } catch (e) {
    Log.error('Frame-diagnose vanuit top-frame mislukt', e);
  }
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: () => ({
        url: location.href.substring(0, 160),
        top: window === window.top,
        outlineLinks: document.querySelectorAll('a.nav-sidebar__outline-item__link').length,
        blocksLesson: document.querySelectorAll('.blocks-lesson').length,
        outerClipDiv: !!document.getElementById('outerClipDiv')
      })
    });
    Log.info('Alle frames (allFrames)', results.map((r) => Object.assign({ frameId: r.frameId }, r.result)));
  } catch (e) {
    Log.error('allFrames-diagnose mislukt', e);
  }
}

async function detectRiseCourse(tabId) {
  try {
    await riseEnsureHelpers(tabId);
    return await riseExec(tabId, () => window.__cpeRise.info());
  } catch (e) {
    Log.error('Rise-detectie mislukt', e);
    return null;
  }
}

function sanitizeFilename(title) {
  return (title || 'canvas-export')
    .replace(/[<>:"/\\|?*]/g, '')
    .replace(/\s+/g, '-')
    .substring(0, 100);
}

function base64ToBytes(b64) {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function runRiseMode() {
  const status = document.getElementById('status');
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const progressContainer = document.getElementById('progressContainer');
  const progressBar = document.getElementById('progressBar');
  const settle = parseInt(document.getElementById('delay').value) || 600;
  const scale = parseFloat(document.getElementById('riseScale').value) || 0.85;
  const addPageNumbers = document.getElementById('pageNumbers').checked;

  riseStopRequested = false;
  startBtn.disabled = true;
  startBtn.style.display = 'none';
  stopBtn.style.display = 'block';
  progressContainer.style.display = 'block';
  progressBar.style.width = '0%';

  const resetButtons = () => {
    startBtn.disabled = false;
    startBtn.style.display = 'block';
    stopBtn.style.display = 'none';
  };

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabId = tab.id;
  const debuggee = { tabId };
  let attached = false;
  let layoutApplied = false;
  let info = null;
  const parts = [];

  Log.info('Rise-export gestart', { tabId, url: tab.url, settle, scale, addPageNumbers });

  try {
    status.textContent = 'Cursus analyseren...';
    await riseEnsureHelpers(tabId);
    await logFrameDiagnostics(tabId);
    info = await riseExec(tabId, () => window.__cpeRise.info());
    Log.info('Cursusinfo', info);
    if (!info || !info.lessons.length) {
      Log.warn('Geen hoofdstukkenlijst gevonden; export afgebroken');
      status.textContent = 'Geen Rise-hoofdstukkenlijst gevonden. Open het menu (☰) en probeer opnieuw.';
      resetButtons();
      return;
    }

    try {
      await chrome.debugger.attach(debuggee, '1.3');
      attached = true;
      Log.info('Debugger gekoppeld');
    } catch (e) {
      Log.error('Debugger koppelen mislukt', e);
      status.textContent = 'Kan niet aan het tabblad koppelen (sluit DevTools en probeer opnieuw). ' + (e.message || '');
      resetButtons();
      return;
    }

    const applied = await riseExec(tabId, (css) => window.__cpeRise.applyLayout(css), [RISE_PRINT_CSS]);
    layoutApplied = true;
    Log.info('Printopmaak toegepast', applied);

    const total = info.lessons.length;
    for (let i = 0; i < total; i++) {
      if (riseStopRequested) break;
      const lesson = info.lessons[i];
      Log.info(`Hoofdstuk ${i + 1}/${total}`, lesson);
      status.textContent = `Hoofdstuk ${i + 1} van ${total}: ${lesson.title}...`;
      progressBar.style.width = `${((i + 0.5) / total) * 100}%`;

      await riseExec(tabId, (href) => window.__cpeRise.goto(href), [lesson.href]);
      const deadline = Date.now() + 15000;
      let ready = false;
      while (Date.now() < deadline) {
        ready = await riseExec(tabId, (id) => window.__cpeRise.lessonReady(id), [lesson.id]);
        if (ready) break;
        await new Promise((r) => setTimeout(r, 250));
      }
      if (!ready) Log.warn('Hoofdstuk meldde zich niet klaar binnen 15 s; toch printen', lesson.id);
      else Log.debug('Hoofdstuk geladen', lesson.id);
      await new Promise((r) => setTimeout(r, settle));

      const prep = await riseExec(tabId, (ms) => window.__cpeRise.prepare(ms), [Math.min(settle, 1500)]);
      Log.info('Voorbereid', prep);

      const t0 = Date.now();
      const { data } = await chrome.debugger.sendCommand(debuggee, 'Page.printToPDF', {
        printBackground: true,
        preferCSSPageSize: false,
        paperWidth: 8.27,
        paperHeight: 11.69,
        marginTop: 0.4,
        marginBottom: 0.5,
        marginLeft: 0.4,
        marginRight: 0.4,
        scale,
        displayHeaderFooter: false
      });
      parts.push(base64ToBytes(data));
      Log.info('PDF gerenderd', { bytes: parts[parts.length - 1].length, ms: Date.now() - t0 });
      progressBar.style.width = `${((i + 1) / total) * 100}%`;
    }
  } catch (e) {
    Log.error('Export mislukt', e);
    status.textContent = 'Fout: ' + (e.message || e);
  } finally {
    if (attached) {
      try { await chrome.debugger.detach(debuggee); Log.debug('Debugger losgekoppeld'); } catch (e) { Log.warn('Debugger loskoppelen', e); }
    }
    if (layoutApplied) {
      try {
        await riseExec(tabId, (href) => window.__cpeRise.restore(href), [info ? info.currentHref : null]);
        Log.debug('Oorspronkelijke opmaak hersteld');
      } catch (e) { Log.error('Herstellen van opmaak mislukt', e); }
    }
  }

  if (parts.length === 0) {
    if (!status.textContent.startsWith('Fout')) status.textContent = 'Geen hoofdstukken vastgelegd.';
    resetButtons();
    return;
  }

  status.textContent = 'PDF samenvoegen...';
  try {
    const { PDFDocument, StandardFonts, rgb } = PDFLib;
    const out = await PDFDocument.create();
    for (const bytes of parts) {
      const src = await PDFDocument.load(bytes);
      const pages = await out.copyPages(src, src.getPageIndices());
      pages.forEach((p) => out.addPage(p));
    }
    if (addPageNumbers) {
      const font = await out.embedFont(StandardFonts.Helvetica);
      const n = out.getPageCount();
      out.getPages().forEach((p, i) => {
        const text = `${i + 1} van ${n}`;
        const size = 9;
        const tw = font.widthOfTextAtSize(text, size);
        p.drawText(text, { x: p.getWidth() - 30 - tw, y: 16, size, font, color: rgb(0.3, 0.3, 0.3) });
      });
    }
    out.setTitle(info.courseTitle);
    const bytes = await out.save();

    const url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = sanitizeFilename(info.courseTitle) + '.pdf';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);

    const n = out.getPageCount();
    Log.info('PDF gedownload', { filename: a.download, lessons: parts.length, pages: n, bytes: bytes.length });
    status.textContent = (riseStopRequested ? 'Gestopt! ' : 'Klaar! ') +
      `PDF met ${parts.length} hoofdstuk${parts.length === 1 ? '' : 'ken'} (${n} pagina's) gedownload.`;
  } catch (e) {
    Log.error('Samenvoegen mislukt', e);
    status.textContent = 'Fout bij samenvoegen: ' + (e.message || e);
  }
  resetButtons();
}

function stopRiseMode() {
  riseStopRequested = true;
  document.getElementById('status').textContent = 'Stoppen na huidig hoofdstuk...';
}
