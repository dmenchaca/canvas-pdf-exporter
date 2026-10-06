// Canvas PDF Exporter — console-variant van Rise-modus (gegenereerd door build-console.js)
// 1. Open de Rise-cursus (spelertabblad), open DevTools → Console, kies "top" als frame.
// 2. Plak dit script en druk op Enter. Wacht tot "[cpe] klaar" verschijnt.
// 3. Er wordt automatisch een .html-bestand gedownload (boek met ingebedde afbeeldingen).
//    Optioneel: Cmd+P → "Bewaar als PDF", "Achtergrondafbeeldingen" aan.
// 4. Herstellen zonder herladen: window.__cpeRise.restore()
(async () => {
  (function () {
  const H = {};
  H.sleep = (ms) => new Promise((r) => { const end = performance.now() + ms; const ch = new MessageChannel(); ch.port1.onmessage = () => { if (performance.now() >= end) { ch.port1.close(); r(); } else ch.port2.postMessage(0); }; ch.port2.postMessage(0); });
  H.log = (...a) => { try { console.log('[cpe]', ...a); } catch (e) { /* stil */ } };

  // Zoekt (door same-origin iframes heen) het document waarin de Rise-cursus draait.
  // Geeft { riseDoc, chain } terug; chain = [{ doc, iframe }] van boven naar beneden.
  H.walk = function () {
    const isRise = (doc) =>
      doc.getElementById('nav-sidebar-outline-list') ||
      doc.getElementById('nav-sidebar-outline') ||
      doc.getElementById('cover') ||
      doc.querySelector('.overview-list-item__link') ||
      doc.querySelector('.blocks-lesson') ||
      doc.querySelector('.page-wrap');
    const visit = (doc, path) => {
      // Het bovenste document bevat na openBook zelf leskopieën: niet als Rise-document zien.
      if (isRise(doc) && !doc.getElementById('cpe-book')) return { riseDoc: doc, chain: path };
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

  H.norm = (t) => (t || '').replace(/\s+/g, ' ').trim();

  H.info = function () {
    const w = H.walk();
    if (!w) return null;
    const rd = w.riseDoc;
    const lessons = [];
    const seen = new Set();
    // Zoek lesverwijzingen zo generiek mogelijk: zijbalk, anders de lijst op de omslag, anders alles.
    const scope = rd.getElementById('nav-sidebar-outline-list') || rd.getElementById('nav-sidebar-outline') ||
      rd.getElementById('nav-content-sidebar') || rd.querySelector('.overview-list') || rd;
    scope.querySelectorAll('a[href*="/lessons/"]').forEach((a) => {
      if (/button/i.test(a.className)) return; // "Ga verder"-knop op de omslag
      const li = a.closest('li');
      const idFromLi = li && /^nav-outline-lesson-/.test(li.id) ? li.id.replace(/^nav-outline-lesson-/, '') : '';
      const m = (a.getAttribute('href') || '').match(/\/lessons\/([^/?#]+)/);
      const id = idFromLi || (m ? m[1] : '');
      const title = H.norm(a.textContent);
      if (id && !seen.has(id)) { seen.add(id); lessons.push({ id, title, href: a.href }); }
    });
    const titleEl = rd.getElementById('nav-sidebar-title') ||
      rd.querySelector('.nav-sidebar-header__title, .nav-sidebar-header h1, .cover__header-content-title');
    const courseTitle = H.norm(titleEl && titleEl.textContent) || rd.title || document.title || 'canvas-export';
    return {
      lessons,
      courseTitle,
      currentHref: rd.defaultView.location.href,
      depth: w.chain.length
    };
  };

  // De scrollcontainer van de les (Rise scrollt niet het venster maar #page-wrap).
  H.scroller = function (rd) {
    const pw = rd.getElementById('page-wrap') || rd.querySelector('.page-wrap');
    if (pw && pw.scrollHeight > pw.clientHeight) return pw;
    let best = null;
    rd.querySelectorAll('*').forEach((e) => {
      const ov = rd.defaultView.getComputedStyle(e).overflowY;
      if ((ov === 'auto' || ov === 'scroll') && e.scrollHeight > e.clientHeight + 50 &&
        (!best || e.scrollHeight > best.scrollHeight)) best = e;
    });
    return best || rd.scrollingElement || rd.documentElement;
  };

  // De les-pagina met dit id (Rise zet data-lesson-id op de buitenste .page).
  H.livePage = function (rd, id, title) {
    // Rise laat oude pagina's in de DOM staan (overgangen die in een verborgen tabblad niet afronden) en zet
    // hetzelfde data-lesson-id op allemaal: kies daarom op titel, anders de nieuwste (laatste in de DOM).
    const pages = Array.from(rd.querySelectorAll('.page[data-lesson-id]')).filter((p) => p.querySelector('.blocks-lesson'));
    const byId = id ? pages.filter((p) => p.getAttribute('data-lesson-id') === id) : [];
    const cand = byId.length ? byId : pages;
    if (title) {
      const t = H.norm(title).toLowerCase();
      const m = cand.filter((p) => { const h = p.querySelector('h1'); return h && H.norm(h.textContent).toLowerCase() === t; });
      if (m.length) return m[m.length - 1];
    }
    return cand[cand.length - 1] || null;
  };

  H.signature = function (page) {
    const b = page && page.querySelector('.blocks-lesson');
    if (!b) return '';
    const t = H.norm(b.textContent);
    return t.length + ':' + t.slice(0, 120) + '|' + t.slice(-120);
  };

  H.goto = function (href) {
    const w = H.walk();
    if (!w) return false;
    w.riseDoc.defaultView.location.href = href; // alleen de hash verandert: geen herlaad
    return true;
  };

  // Wacht tot les `id` echt getoond wordt. Rise hergebruikt het .page-element en zet
  // data-lesson-id én de titel soms al vóór de inhoud is gewisseld; daarom eisen we
  // dat de inhoud stabiel is én verschilt van de vorige les.
  H.waitForLesson = async function (lesson, prevSignature, timeoutMs) {
    const t0 = Date.now();
    let last = null;
    let stableSince = 0;
    while (Date.now() - t0 < (timeoutMs || 15000)) {
      const w = H.walk();
      if (w) {
        const rd = w.riseDoc;
        const hash = rd.defaultView.location.hash || '';
        const page = H.livePage(rd, lesson.id, lesson.title);
        if (page && hash.indexOf('/lessons/' + lesson.id) !== -1 &&
          !/page-transition-(back-)?(leave|exit)/.test(page.className)) {
          const h1 = page.querySelector('h1');
          const titleOk = !h1 || !lesson.title || H.norm(h1.textContent).toLowerCase() === lesson.title.toLowerCase();
          const sig = H.signature(page);
          if (titleOk && sig && sig !== prevSignature) {
            if (sig === last) {
              if (Date.now() - stableSince >= 400) return { ok: true, ms: Date.now() - t0, sig };
            } else {
              last = sig;
              stableSince = Date.now();
            }
          }
        }
      }
      await H.sleep(150);
    }
    return { ok: false, ms: Date.now() - t0, sig: last || '' };
  };

  // Maakt verborgen inhoud zichtbaar (Continue-knoppen, accordeons), laat lazy
  // afbeeldingen laden door de les door te scrollen en wacht tot alles klaar is.
  H.prepare = async function (settle) {
    const w = H.walk();
    if (!w) return null;
    const rd = w.riseDoc;
    let continues = 0;
    for (let round = 0; round < 40; round++) {
      const btn = rd.querySelector('.continue-btn:not([disabled]), button.block-divider__button:not([disabled])');
      if (!btn) break;
      btn.scrollIntoView({ block: 'center' });
      btn.click();
      continues++;
      await H.sleep(settle);
    }
    rd.querySelectorAll('.blocks-accordion__header, .blocks-accordion__header button, [class*="accordion"] [aria-expanded="false"]').forEach((h) => {
      if (h.getAttribute('aria-expanded') === 'false') h.click();
    });
    await H.sleep(settle);
    let quizzes = 0;
    try { quizzes = await H.solveQuizzes(rd, settle); } catch (e) { H.log('quiz oplossen mislukt', e); }
    try { quizzes += await H.solveMatching(rd, settle); } catch (e) { H.log('koppelvraag oplossen mislukt', e); }

    const sc = H.scroller(rd);
    const total = sc.scrollHeight;
    const step = Math.max(300, Math.floor((sc.clientHeight || rd.defaultView.innerHeight) * 0.75));
    for (let y = 0; y <= total; y += step) {
      sc.scrollTop = y;
      await H.sleep(120);
    }
    sc.scrollTop = 0;

    const pending = Array.from(rd.images).filter((i) => !i.complete);
    await Promise.race([
      Promise.all(pending.map((i) => new Promise((r) => {
        i.addEventListener('load', r, { once: true });
        i.addEventListener('error', r, { once: true });
      }))),
      H.sleep(8000)
    ]);
    await H.sleep(settle);
    return { continues, quizzes, images: rd.images.length, scrollHeight: total };
  };

  // Lost kennisvragen op: dient een antwoord in, leest welke opties Rise als juist
  // markeert (is-correct) en zet de quiz terug met "Doe de quiz overnieuw".
  // Resultaat komt als data-cpe-correct="0,2" op de .quiz-card (blijft bij klonen).
  H.solveQuizzes = async function (rd, settle) {
    let solved = 0;
    const isCorrect = (el) => /(?:^|\s)(?:is-correct|\S+--correct)(?:\s|$)/.test(el.className || '');
    for (const card of Array.from(rd.querySelectorAll('.block-knowledge .quiz-card'))) {
      if (card.hasAttribute('data-cpe-correct')) continue;
      const labels = () => Array.from(card.querySelectorAll('label.quiz-multiple-choice-option, label.quiz-multiple-response-option'));
      if (!labels().length) continue; // matching e.d.: niet automatisch op te lossen
      const inputOf = (l) => l.querySelector('input');
      const submit = () => card.querySelector('button.quiz-card__button, [data-testid="quiz-submit-button"]');
      const retake = () => card.querySelector('.block-knowledge__retake');
      const correctIdx = () => labels().map((o, i) => isCorrect(o) ? i : -1).filter((i) => i >= 0);
      const waitFor = async (test, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (test()) return true; await H.sleep(120); } return false; };
      const reset = async () => {
        const r = retake();
        if (r) { r.click(); await waitFor(() => !!submit(), 6000); await H.sleep(300); }
        // Soms blijven vinkjes staan na herstarten: uitzetten.
        labels().forEach((l) => { const inp = inputOf(l); if (inp && inp.checked && !inp.disabled) l.click(); });
        await H.sleep(200);
      };
      const submitAndWait = async () => {
        const b = submit();
        if (!b) return false;
        b.click();
        // Wachten tot Rise het resultaat echt heeft aangebracht (klassen op de opties of het feedback-icoon);
        // in een verborgen tabblad kan dat traag zijn.
        const judged = () => labels().some((l) => /--(?:complete|correct|incorrect)(?:\s|$)/.test(l.className || '')) ||
          !!card.querySelector('.quiz-card__feedback-icon--correct, .quiz-card__feedback-icon--incorrect');
        await waitFor(judged, 10000);
        await waitFor(() => !!H.norm((card.querySelector('.quiz-card__feedback-text') || {}).textContent), 2000);
        await H.sleep(settle);
        return true;
      };
      card.scrollIntoView({ block: 'center' });
      let found = [];
      let fbText = '';
      const readFeedback = () => {
        const ok = card.querySelector('.quiz-card__feedback-icon--correct');
        return ok ? H.norm((card.querySelector('.quiz-card__feedback-text') || {}).textContent) : '';
      };
      // Al eerder (goed) beantwoord: markering staat er al, dan is er geen herstartknop.
      if (!submit()) {
        found = correctIdx();
        fbText = readFeedback();
        if (!found.length) await reset();
      }
      const isRadio = (inputOf(labels()[0]) || {}).type === 'radio';
      if (found.length) {
        // niets meer te doen
      } else if (isRadio) {
        for (let i = 0; i < labels().length && !found.length; i++) {
          labels()[i].click();
          await H.sleep(250);
          if (!(await submitAndWait())) break;
          found = correctIdx();
          fbText = readFeedback();
          if (!found.length) await reset();
        }
      } else {
        labels().forEach((l) => { const inp = inputOf(l); if (inp && !inp.checked) l.click(); });
        await H.sleep(250);
        if (await submitAndWait()) { found = correctIdx(); fbText = readFeedback(); }
      }
      if (found.length) {
        card.setAttribute('data-cpe-correct', found.join(','));
        if (fbText) card.setAttribute('data-cpe-feedback', fbText);
        solved++;
      }
      if (retake()) await reset(); // na een fout antwoord terugzetten; na een goed antwoord is er geen herstart
    }
    return solved;
  };

  // Lost koppelvragen (matching) op. Werkwijze: koppel links[i] aan rechts[perm[i]], dien in,
  // lees per paar Goed/Fout uit de resultatenlijst, bewaar goede paren, herstart en probeer
  // voor de resterende begrippen een andere volgorde. Resultaat: data-cpe-pairs (JSON) op de kaart.
  H.solveMatching = async function (rd, settle) {
    let solved = 0;
    const waitFor = async (test, ms) => { const t0 = Date.now(); while (Date.now() - t0 < ms) { if (test()) return true; await H.sleep(120); } return false; };
    for (const card of Array.from(rd.querySelectorAll('.block-knowledge .quiz-card'))) {
      if (card.hasAttribute('data-cpe-pairs')) continue;
      const hasWidget = () => !!card.querySelector('.matching-interaction');
      const results = () => Array.from(card.querySelectorAll('.matching-results-item')).map((it) => {
        const pieces = Array.from(it.querySelectorAll('.matching-results-item-piece-content')).map((x) => H.norm(x.textContent));
        const fb = it.querySelector('.matching-results-item-feedback');
        const ok = !!(fb && /(?:^|\s)\S+--correct(?:\s|$)/.test(Array.from(fb.querySelectorAll('*')).concat([fb]).map((e) => e.className).join(' ')));
        return { left: pieces[0] || '', right: pieces[1] || '', ok };
      });
      if (!hasWidget() && !results().length) continue;
      const submit = () => card.querySelector('button.quiz-card__button, [data-testid="quiz-submit-button"]');
      const retake = () => card.querySelector('.block-knowledge__retake');
      const known = {}; // left -> right (bevestigd goed)
      const wrongTried = {}; // left -> Set van foute rechts
      const record = () => {
        results().forEach((r) => {
          if (r.ok) known[r.left] = r.right;
          else { (wrongTried[r.left] = wrongTried[r.left] || new Set()).add(r.right); }
        });
      };
      card.scrollIntoView({ block: 'center' });
      if (!hasWidget()) record(); // al eerder ingediend
      for (let round = 0; round < 8; round++) {
        const lefts = Array.from(card.querySelectorAll('.matching-interaction-col-1 .matching-interaction-piece'));
        if (lefts.length && Object.keys(known).length >= lefts.length) break;
        if (!hasWidget()) {
          const r = retake();
          if (!r) break;
          r.click();
          await waitFor(hasWidget, 6000);
          await H.sleep(300);
          if (!hasWidget()) break;
        }
        const L = () => Array.from(card.querySelectorAll('.matching-interaction-col-1 .matching-interaction-piece')).filter((b) => !/--matched/.test(b.className));
        const R = () => Array.from(card.querySelectorAll('.matching-interaction-col-2 .matching-interaction-piece')).filter((b) => !/--matched/.test(b.className));
        const txt = (b) => H.norm((b.querySelector('.matching-interaction-piece-content') || b).textContent);
        // Eerst de bekende paren, dan de rest met een nog niet geprobeerde rechterkant.
        let guard = 0;
        while (L().length && guard++ < 20) {
          const l = L()[0];
          const lt = txt(l);
          let target = null;
          if (known[lt]) target = R().find((b) => txt(b) === known[lt]);
          if (!target) {
            const tried = wrongTried[lt] || new Set();
            const usedRights = new Set(Object.values(known));
            target = R().find((b) => !tried.has(txt(b)) && !usedRights.has(txt(b))) || R().find((b) => !tried.has(txt(b))) || R()[0];
          }
          if (!target) break;
          l.click();
          await H.sleep(250);
          target.click();
          await H.sleep(350);
        }
        const b = submit();
        if (!b) break;
        b.click();
        await waitFor(() => results().length > 0, 6000);
        await H.sleep(settle);
        record();
        if (results().length && results().every((r) => r.ok)) break;
      }
      const pairs = Object.keys(known).map((k) => [k, known[k]]);
      if (pairs.length) {
        card.setAttribute('data-cpe-pairs', JSON.stringify(pairs));
        const ok = card.querySelector('.quiz-card__feedback-icon--correct');
        const fbText = ok ? H.norm((card.querySelector('.quiz-card__feedback-text') || {}).textContent) : '';
        if (fbText) card.setAttribute('data-cpe-feedback', fbText);
        solved++;
      }
      if (retake()) { retake().click(); await waitFor(hasWidget, 6000); }
    }
    return solved;
  };

  // Maakt een schone, statische kopie van de lesinhoud (in het Rise-document).
  H.extractLesson = function (lesson) {
    const w = H.walk();
    if (!w) return null;
    const rd = w.riseDoc;
    const page = H.livePage(rd, lesson.id, lesson.title);
    const blocks = page && page.querySelector('.blocks-lesson');
    if (!blocks) return null;

    const clone = blocks.cloneNode(true);
    // Canvasblokken ('custom'): Rise rendert ze pas als ze in beeld komen; hun inhoud komt uit de
    // cursusdata en wordt als gewoon tekstblok in de kopie gezet (zo verwerken rise.js en
    // book-to-print.py ze allebei).
    if (H.courseData) {
      clone.querySelectorAll('[data-block-id]').forEach((wr) => {
        const item = H.courseData.byBlock[wr.getAttribute('data-block-id')];
        if (!item) return;
        if (item.variant === 'sorting' && item.piles && item.items) {
          // Sorteeroefening: kaarten per stapel, zoals de cursist ze na afloop juist gesorteerd ziet.
          const piles = item.piles.map((p) => '<th>' + H.esc(H.norm(p.title)) + '</th>').join('');
          const cols = item.piles.map((p) => item.items.filter((c) => c.pileId === p.id)
            .map((c) => '<li>' + H.esc(H.norm(c.title)) + (c.description ? '<div class="note">' + H.esc(H.norm(c.description)) + '</div>' : '') + '</li>').join(''));
          const body = '<tr>' + cols.map((c) => '<td><ul class="sorted">' + c + '</ul></td>').join('') + '</tr>';
          wr.innerHTML = '<div class="block-cpe-ready"><div class="quiz sorting"><div class="quiz-label">Vraag</div>' +
            '<p class="hint">Sorteeroefening: elke kaart staat hieronder in de juiste categorie.</p>' +
            '<table class="matching sorting"><tr>' + piles + '</tr>' + body + '</table></div></div>';
          return;
        }
        if (item.type !== 'custom') return;
        const html = H.customBlockHtml(item);
        if (!html) return;
        wr.innerHTML = '<div class="block-text cpe-custom"><div class="block-text__container"><div class="block-text__row">' +
          '<div class="block-text__col"><div class="fr-view">' + html + '</div></div></div></div></div>';
      });
    }
    clone.querySelectorAll('[style]').forEach((el) => {
      el.style.removeProperty('opacity');
      el.style.removeProperty('transform');
      el.style.removeProperty('visibility');
    });
    clone.querySelectorAll('[hidden], [aria-hidden="true"]').forEach((el) => {
      if (el.closest('.blocks-accordion, .blocks-tabs, .block-flashcards, .quiz-card, .carousel')) {
        el.removeAttribute('hidden');
        el.removeAttribute('aria-hidden');
      }
    });
    // Labeled graphic (hotspots): tekst van de markers als lijst onder de afbeelding.
    clone.querySelectorAll('.block-labeled-graphic').forEach((blk) => {
      const items = Array.from(blk.querySelectorAll('.map-item'));
      if (!items.length) return;
      const list = rd.createElement('ol');
      list.className = 'cpe-labels';
      items.forEach((it) => {
        const t = it.querySelector('.bubble__title');
        const d = it.querySelector('.bubble__description, .bubble__content');
        const li = rd.createElement('li');
        if (t) { const b = rd.createElement('div'); b.className = 'cpe-label-title'; b.textContent = H.norm(t.textContent); li.appendChild(b); }
        if (d) {
          const c = rd.createElement('div');
          c.innerHTML = d.innerHTML; // zonder Rise-klassen, anders blijft de tekst verborgen
          c.querySelectorAll('[aria-hidden], [hidden]').forEach((x) => { x.removeAttribute('aria-hidden'); x.removeAttribute('hidden'); });
          li.appendChild(c);
        }
        list.appendChild(li);
      });
      const fig = blk.querySelector('.labeled-graphic-canvas__figure');
      const ol = fig && fig.querySelector('ol');
      if (ol) ol.remove();
      (blk.querySelector('.view-labeled-graphic') || blk).appendChild(list);
    });
    // Afbeeldingen: absolute URL's (de kopie komt in een ander document terecht).
    const origImgs = Array.from(blocks.querySelectorAll('img'));
    Array.from(clone.querySelectorAll('img')).forEach((img, i) => {
      const src = (origImgs[i] && (origImgs[i].currentSrc || origImgs[i].src)) || img.getAttribute('data-src') || img.src;
      if (src) img.setAttribute('src', src);
      img.removeAttribute('srcset');
      img.removeAttribute('sizes');
      img.setAttribute('loading', 'eager');
    });
    clone.querySelectorAll('picture source').forEach((s) => s.remove());
    // Inline achtergrondafbeeldingen: absolute URL uit de computed style van het origineel.
    const origBg = Array.from(blocks.querySelectorAll('[style*="url("]'));
    Array.from(clone.querySelectorAll('[style*="url("]')).forEach((el, i) => {
      const o = origBg[i];
      const bg = o && rd.defaultView.getComputedStyle(o).backgroundImage;
      if (bg && bg !== 'none') el.style.backgroundImage = bg; else el.style.removeProperty('background-image');
    });
    clone.querySelectorAll('video, audio, iframe').forEach((m) => {
      const ph = rd.createElement('p');
      ph.className = 'cpe-media';
      ph.textContent = '[' + m.tagName.toLowerCase() + ': ' + (m.getAttribute('title') || m.getAttribute('aria-label') || 'media') + ']';
      m.replaceWith(ph);
    });
    clone.querySelectorAll('script, style, link').forEach((s) => s.remove());
    return { html: clone.outerHTML, text: H.norm(clone.textContent).length, images: clone.querySelectorAll('img').length };
  };

  // Verbergt de speler in het bovenste document en maakt het boek aan.
  H.openBook = function (css, courseTitle) {
    const w = H.walk();
    if (!w) return null;
    const rd = w.riseDoc;
    let book = document.getElementById('cpe-book');
    if (book) return book;
    let st = document.getElementById('cpe-book-style');
    if (!st) { st = document.createElement('style'); st.id = 'cpe-book-style'; document.head.appendChild(st); }
    st.textContent = css;
    Array.from(document.body.children).forEach((el) => {
      if (el.id === 'cpe-book' || el.tagName === 'SCRIPT') return;
      el.setAttribute('data-cpe-hidden', el.getAttribute('style') || '');
      el.style.setProperty('display', 'none', 'important');
    });
    book = document.createElement('div');
    book.id = 'cpe-book';
    // Zelfde klassen als body/#app van Rise zodat afhankelijke selectors blijven werken.
    const app = rd.getElementById('app');
    book.className = [rd.body.className, app ? app.className : ''].join(' ').trim();
    if (courseTitle) document.title = courseTitle;
    document.body.appendChild(book);
    return book;
  };

  H.appendChapter = function (lesson, index, total, html) {
    const book = document.getElementById('cpe-book');
    if (!book) return false;
    const sec = document.createElement('section');
    sec.className = 'cpe-chapter';
    const header = document.createElement('div');
    header.className = 'cpe-chapter-header';
    const k = document.createElement('p');
    k.className = 'cpe-kicker';
    k.textContent = 'Hoofdstuk ' + (index + 1) + ' van ' + total;
    const h1 = document.createElement('h1');
    h1.textContent = lesson.title;
    header.appendChild(k);
    header.appendChild(h1);
    sec.appendChild(header);
    const wrap = document.createElement('div');
    wrap.innerHTML = html;
    sec.appendChild(wrap);
    book.appendChild(sec);
    // Korte blokken (kopbalken, intro-zinnen) niet onderaan een pagina laten bungelen.
    wrap.querySelectorAll('.blocks-lesson > *').forEach((blk) => {
      // Alleen echt korte blokken (kopbalken); lange ketens van 'avoid' duwen anders alles door.
      if (blk.offsetHeight > 0 && blk.offsetHeight < 140) blk.classList.add('cpe-keep-with-next');
    });
    return true;
  };

  H.closeBook = function () {
    const book = document.getElementById('cpe-book');
    if (book) book.remove();
    const pr = document.getElementById('cpe-print');
    if (pr) pr.remove();
    const ps = document.getElementById('cpe-print-style');
    if (ps) ps.remove();
    document.querySelectorAll('[data-cpe-disabled]').forEach((n) => { try { if (n.sheet) n.sheet.disabled = false; } catch (e) { /* negeren */ } n.removeAttribute('data-cpe-disabled'); });
    document.querySelectorAll('[data-cpe]').forEach((n) => n.remove());
    const st = document.getElementById('cpe-book-style');
    if (st) st.remove();
    document.querySelectorAll('[data-cpe-hidden]').forEach((el) => {
      const o = el.getAttribute('data-cpe-hidden');
      if (o) el.setAttribute('style', o); else el.removeAttribute('style');
      el.removeAttribute('data-cpe-hidden');
    });
    return true;
  };

  H.restore = function (href) {
    H.closeBook();
    const w = H.walk();
    if (w && href) w.riseDoc.defaultView.location.href = href;
    window.scrollTo(0, 0);
    return true;
  };

  // Maakt van het boek één zelfstandig HTML-bestand (CSS en afbeeldingen ingebed),
  // zodat de PDF ook buiten de browser gemaakt kan worden (headless Chrome).
  H.exportHtml = async function () {
    const book = document.getElementById('cpe-book');
    if (!book) throw new Error('Geen boek aanwezig; eerst buildBook.');
    const clone = book.cloneNode(true);
    const imgs = Array.from(clone.querySelectorAll('img'));
    let inlined = 0;
    let failed = 0;
    for (const img of imgs) {
      const src = img.getAttribute('src');
      if (!src || /^data:/.test(src)) continue;
      try {
        const blob = await (await fetch(src, { credentials: 'include' })).blob();
        const dataUrl = await new Promise((ok, err) => {
          const fr = new FileReader();
          fr.onload = () => ok(fr.result);
          fr.onerror = err;
          fr.readAsDataURL(blob);
        });
        img.setAttribute('src', dataUrl);
        inlined++;
      } catch (e) { failed++; }
    }
    const title = H.norm(document.title).replace(/[<&]/g, ' ');
    const html = '<!doctype html>\n<html lang="nl"><head><meta charset="utf-8"><title>' + title + '</title></head>' +
      '<body>' + clone.outerHTML + '</body></html>';
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
    a.download = title.replace(/[<>:"/\\|?*]/g, '').replace(/\s+/g, '-').slice(0, 100) + '.html';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    return { bytes: html.length, images: imgs.length, inlined, failed };
  };


  // ---- Omzetting van Rise-blokken naar schone, printbare HTML (port van book-to-print.py) ----
  H.esc = (t) => String(t || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  // Rijke inhoud (p, strong, ul, table...) van een .fr-view, opgeschoond.
  H.cleanRich = function (node) {
    if (!node) return '';
    const frag = node.cloneNode(true);
    frag.querySelectorAll('svg, button, input, script, style, .visually-hidden-always').forEach((b) => b.remove());
    frag.querySelectorAll('br.break-when-trailing').forEach((br) => br.replaceWith(document.createElement('br')));
    frag.querySelectorAll('*').forEach((el) => {
      Array.from(el.attributes).forEach((a) => { if (!['href', 'src', 'alt', 'colspan', 'rowspan'].includes(a.name)) el.removeAttribute(a.name); });
    });
    // Lege omhulsels (div/span) platslaan, van binnen naar buiten.
    let wrappers = Array.from(frag.querySelectorAll('div, span'));
    wrappers.reverse().forEach((el) => { el.replaceWith(...el.childNodes); });
    let html = frag.innerHTML;
    html = html.replace(/(<br\s*\/?>\s*)+<\/p>/g, '</p>');
    html = html.replace(/<p>\s*(?:&nbsp;|\s)*<\/p>/g, '');
    return html.trim();
  };

  H.imageHtml = (img) => img ? '<img src="' + H.esc(img.getAttribute('src') || '') + '" alt="' + H.esc(img.getAttribute('alt') || '') + '">' : '';
  H.figureClass = function (img) {
    const w = parseFloat(img && img.getAttribute('width')) || 0;
    const h = parseFloat(img && img.getAttribute('height')) || 0;
    return (w && h && w / h >= 2.2) ? 'wide' : '';
  };
  H.bgColor = function (el) {
    const m = /--color-background:\s*(#[0-9a-fA-F]{3,6})/.exec(el.getAttribute('style') || '');
    return m ? m[1].toLowerCase() : '#ffffff';
  };

  // Rise-cursusdata (lessen, blokken, 'mondrian'-canvasblokken) uit de speler: window.__fetchCourse().
  H.loadCourseData = async function () {
    if (H.courseData !== undefined) return H.courseData;
    H.courseData = null;
    try {
      const w = H.walk();
      const win = w && w.riseDoc.defaultView;
      let data = null;
      if (win && typeof win.__fetchCourse === 'function') {
        data = await win.__fetchCourse();
      } else if (win) {
        // Nieuwere Rise-uitvoer: runtime-data.js = __jsonp("runtime-data.js", "<base64 van JSON>").
        const base = w.riseDoc.location.href.replace(/[#?].*$/, '').replace(/[^/]*$/, '');
        const resp = await fetch(base + 'runtime-data.js', { credentials: 'include' });
        if (resp.ok) {
          const m = (await resp.text()).match(/__jsonp\(\s*"[^"]*"\s*,\s*"([^"]*)"/);
          if (m) {
            const bin = atob(m[1]);
            const bytes = new Uint8Array(bin.length);
            for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
            data = JSON.parse(new TextDecoder('utf-8').decode(bytes));
          }
        }
      }
      if (data) {
        const course = (data && data.course) || data;
        const byBlock = {};
        (course.lessons || []).forEach((l) => (l.items || []).forEach((it) => { if (it && it.id) byBlock[it.id] = it; }));
        H.courseData = { course, byBlock };
      }
    } catch (e) { console.log('Cursusdata niet geladen:', e); }
    return H.courseData;
  };

  // Tiptap-JSON (rijke tekst van canvasblokken) -> HTML. Alleen structuur en nadruk; geen kleuren.
  H.tiptapHtml = function (node) {
    if (!node) return '';
    const kids = () => (node.content || []).map(H.tiptapHtml).join('');
    switch (node.type) {
      case 'doc': return kids();
      case 'paragraph': { const k = kids(); return H.norm(k.replace(/<[^>]+>/g, '')) ? '<p>' + k + '</p>' : ''; }
      case 'heading': { const lvl = Math.min(Math.max((node.attrs && node.attrs.level) || 3, 3), 4); return '<h' + lvl + '>' + kids() + '</h' + lvl + '>'; }
      case 'bulletList': return '<ul>' + kids() + '</ul>';
      case 'orderedList': return '<ol>' + kids() + '</ol>';
      case 'listItem': return '<li>' + kids().replace(/^<p>([\s\S]*)<\/p>$/, '$1') + '</li>';
      case 'hardBreak': return '<br>';
      case 'text': {
        let t = H.esc(node.text || '');
        (node.marks || []).forEach((m) => {
          if (m.type === 'bold') t = '<strong>' + t + '</strong>';
          else if (m.type === 'italic') t = '<em>' + t + '</em>';
          else if (m.type === 'underline') t = '<u>' + t + '</u>';
          else if (m.type === 'link' && m.attrs && m.attrs.href) t = '<a href="' + H.esc(m.attrs.href) + '">' + t + '</a>';
        });
        return t;
      }
      default: return kids();
    }
  };

  // Canvasblok ('custom'/'mondrian'): de inhoud staat niet in de DOM (wordt pas gerenderd als het
  // blok zichtbaar in beeld komt) maar wel in de cursusdata. Elementen in leesvolgorde (groepen
  // van links naar rechts, binnen een groep van boven naar beneden); badges/kopjes worden tussenkoppen.
  H.customBlockHtml = function (item) {
    const cd = H.courseData;
    const mon = cd && cd.course && cd.course.mondrian;
    if (!mon || !mon.blockuments || !mon.items) return '';
    const bd = mon.blockuments[item.blockumentId];
    if (!bd) return '';
    const items = mon.items;
    const stateOf = (el) => (el.states && (el.states[el.initialState || 'default'] || el.states.default)) || {};
    const childrenOf = (el) => (el.children || [])
      .map((c) => items[c.id]).filter((e) => e && !e.removed && e.initialVisible !== false && e.authoringVisible !== false)
      .map((e) => ({ e, st: stateOf(e) }))
      .sort((a, b) => (Math.round(a.st.y / 40) - Math.round(b.st.y / 40)) || (a.st.x - b.st.x));
    const out = [];
    const imageUrl = (el, st) => {
      const key = (st.media && st.media.image && st.media.image.key) || (el.assets && el.assets.image && el.assets.image.key) || '';
      if (!key) return '';
      const base = H.walk().riseDoc.location.href.replace(/[#?].*$/, '').replace(/[^/]*$/, '');
      return base + 'assets/' + key.split('/').pop();
    };
    const walk = (el, st, depth) => {
      const text = st.text && st.text.json ? H.tiptapHtml(st.text.json) : '';
      const plain = H.norm(text.replace(/<[^>]+>/g, ' '));
      if (el.type === 'group') {
        childrenOf(el).forEach((c) => walk(c.e, c.st, depth + 1));
        return;
      }
      if (el.type === 'image') {
        const u = imageUrl(el, st);
        if (u) out.push('<figure><img src="' + H.esc(u) + '" alt="' + H.esc(st.altText || '') + '"></figure>');
        return;
      }
      if (!plain) return;
      const isBadge = el.type === 'shape' || /badge|label|kop|heading|title/i.test(el.name || '');
      const short = plain.length <= 90 && !/[.!?]\s*\S/.test(plain);
      if (isBadge && short) out.push('<h3>' + H.esc(plain) + '</h3>');
      else out.push(text);
    };
    (bd.children || []).map((c) => items[c.id]).filter(Boolean).forEach((root) => walk(root, stateOf(root), 0));
    return out.length ? '<div class="block custom">' + out.join('\n') + '</div>' : '';
  };

  H.convertBlock = function (wrapper) {
    const blockId = wrapper.getAttribute('data-block-id');
    const item = blockId && H.courseData && H.courseData.byBlock[blockId];
    if (item && item.type === 'custom') {
      const html = H.customBlockHtml(item);
      if (html) return html;
    }
    const inner = wrapper.querySelector('[class*="block-"], [class*="blocks-"]');
    if (!inner) return '';
    const cls = Array.from(inner.classList);
    const kind = cls.find((c) => /^blocks?-[a-z-]+$/.test(c) && !c.includes('__') && !c.includes('--')) || '';
    const out = [];
    const txt = (el) => H.norm(el ? el.textContent : '');

    if (kind === 'block-cpe-ready') {
      out.push(inner.innerHTML); // al omgezet in extractLesson (sorteeroefening uit de cursusdata)
    } else if (kind === 'block-text') {
      const heading = inner.querySelector('.block-text__heading');
      const headingTxt = txt(heading);
      const bodyParts = [];
      inner.querySelectorAll('.block-text__col').forEach((col) => {
        if (col.querySelector('.block-text__heading')) return;
        bodyParts.push(H.cleanRich(col.querySelector('.fr-view') || col));
      });
      const body = bodyParts.filter(Boolean).join('\n');
      const colored = !['#ffffff', '#fff'].includes(H.bgColor(inner));
      if (headingTxt && !body) out.push('<h2 class="' + (colored ? 'band' : 'section') + '">' + H.esc(headingTxt) + '</h2>');
      else {
        if (headingTxt) out.push('<h2 class="section">' + H.esc(headingTxt) + '</h2>');
        if (body) out.push('<div class="block">' + body + '</div>');
      }
    } else if (kind === 'block-list') {
      const numbered = cls.join(' ').includes('numbered');
      const items = Array.from(inner.querySelectorAll('li.block-list__item')).map((li) =>
        '<li>' + H.cleanRich(li.querySelector('.block-list__content .fr-view') || li.querySelector('.block-list__content')) + '</li>');
      const tag = numbered ? 'ol' : 'ul';
      out.push('<' + tag + ' class="list ' + (numbered ? 'numbered' : '') + '">' + items.join('') + '</' + tag + '>');
    } else if (kind === 'block-image') {
      const img = inner.querySelector('img');
      const cap = inner.querySelector('figcaption');
      const text = inner.querySelector('.block-image__text .fr-view');
      const fig = '<figure class="' + H.figureClass(img) + '">' + H.imageHtml(img) + (cap ? '<figcaption>' + H.esc(txt(cap)) + '</figcaption>' : '') + '</figure>';
      if (text && txt(text) && H.figureClass(img) !== 'wide') out.push('<div class="image-aside"><div class="text">' + H.cleanRich(text) + '</div>' + fig + '</div>');
      else if (text && txt(text)) out.push('<div class="block">' + H.cleanRich(text) + '</div>' + fig);
      else out.push(fig);
    } else if (kind === 'block-divider') {
      const t = txt(inner);
      if (t) out.push('<h2 class="band">' + H.esc(t) + '</h2>');
    } else if (kind === 'block-process') {
      inner.querySelectorAll('.block-process-card').forEach((card) => {
        const num = card.querySelector('.block-process-card__number');
        const title = card.querySelector('.block-process-card__title');
        const img = card.querySelector('.block-process-card__media img');
        const desc = card.querySelector('.block-process-card__description .fr-view');
        const intro = card.className.includes('intro');
        out.push('<div class="card ' + (intro ? 'process-intro' : '') + '">' +
          (num ? '<div class="card-number">' + H.esc(txt(num)) + '</div>' : '') +
          (title ? '<h3>' + H.esc(txt(title)) + '</h3>' : '') +
          (img ? '<figure>' + H.imageHtml(img) + '</figure>' : '') +
          (desc ? H.cleanRich(desc) : '') + '</div>');
      });
    } else if (kind === 'block-labeled-graphic') {
      const img = inner.querySelector('img');
      const items = Array.from(inner.querySelectorAll('ol.cpe-labels > li')).map((li) => {
        const t = li.querySelector('.cpe-label-title');
        const d = li.querySelector('.fr-view');
        return '<li><div class="label-title">' + H.esc(txt(t)) + '</div>' + (d ? H.cleanRich(d) : '') + '</li>';
      });
      out.push('<figure class="graphic">' + H.imageHtml(img) + '</figure>');
      if (items.length) out.push('<ol class="labels">' + items.join('') + '</ol>');
    } else if (kind === 'block-statement') {
      // Opmerking/citaat in een kader (Rise 'statement'): als kader met icoon.
      const q = inner.querySelector('.block-statement__quote .fr-view') || inner.querySelector('.block-statement__quote');
      if (q && txt(q)) {
        const note = cls.includes('block-statement--note');
        out.push('<div class="callout' + (note ? '' : ' quote') + '">' + (note ? '<div class="callout-icon">i</div>' : '') +
          '<div class="callout-body">' + H.cleanRich(q) + '</div></div>');
      }
    } else if (kind === 'blocks-tabs') {
      // Tabbladen: elke tab als kopje met zijn eigen inhoud eronder.
      const heads = Array.from(inner.querySelectorAll('.blocks-tabs__header-item'));
      const panels = Array.from(inner.querySelectorAll('.blocks-tabs__content-item'));
      const items = heads.map((h, i) => {
        const t = h.querySelector('.fr-view') || h;
        const panel = panels[i];
        const d = panel ? (panel.querySelector('.fr-view') || panel) : null;
        return '<dt>' + H.esc(txt(t)) + '</dt><dd>' + (d ? H.cleanRich(d) : '') + '</dd>';
      });
      out.push('<dl class="accordion tabs">' + items.join('') + '</dl>');
    } else if (kind === 'blocks-accordion') {
      const items = Array.from(inner.querySelectorAll('.blocks-accordion__item')).map((item) => {
        const t = item.querySelector('.blocks-accordion__title');
        const d = item.querySelector('.blocks-accordion__content .fr-view') || item.querySelector('.blocks-accordion__content');
        return '<dt>' + H.esc(txt(t)) + '</dt><dd>' + (d ? H.cleanRich(d) : '') + '</dd>';
      });
      out.push('<dl class="accordion">' + items.join('') + '</dl>');
    } else if (kind === 'block-knowledge') {
      inner.querySelectorAll('.quiz-card').forEach((card) => {
        const title = card.querySelector('.quiz-card__title');
        const img = card.querySelector('.quiz-card__media img');
        let kindQ = 'radio';
        const rawOpts = Array.from(card.querySelectorAll('.quiz-multiple-choice-option, .quiz-multiple-response-option')).map((lab) => {
          if (lab.classList.contains('quiz-multiple-response-option')) kindQ = 'check';
          const t = lab.querySelector('.fr-view') || lab;
          return { text: txt(t), html: H.cleanRich(t) };
        });
        const correctAttr = card.getAttribute('data-cpe-correct') || '';
        const correct = new Set(correctAttr.split(',').filter((x) => /^\d+$/.test(x.trim())).map(Number));
        const holder = card.hasAttribute('data-cpe-pairs') ? card : card.querySelector('[data-cpe-pairs]');
        let pairs = [];
        try { pairs = holder ? JSON.parse(holder.getAttribute('data-cpe-pairs')) : []; } catch (e) { pairs = []; }
        const cols = card.querySelectorAll('.matching-interaction-col');
        const rows = [];
        if (pairs.length && !rawOpts.length) {
          pairs.forEach(([l, r]) => rows.push('<tr><td>' + H.esc(l) + '</td><td>' + H.esc(r) + ' <span class="ok">✓</span></td></tr>'));
        } else if (cols.length >= 2 && !rawOpts.length) {
          const left = Array.from(cols[0].querySelectorAll('.matching-interaction-piece-content')).map(txt);
          const right = Array.from(cols[1].querySelectorAll('.matching-interaction-piece-content')).map(txt);
          for (let i = 0; i < Math.max(left.length, right.length); i++) rows.push('<tr><td>' + H.esc(left[i] || '') + '</td><td>' + H.esc(right[i] || '') + '</td></tr>');
        }
        const opts = rawOpts.map((o, i) => '<li' + (correct.has(i) ? ' class="correct"' : '') + '>' + o.html + '</li>');
        const feedback = card.getAttribute('data-cpe-feedback') || '';
        let body = '<div class="quiz-label">Vraag</div>';
        body += '<div class="question">' + (title ? H.cleanRich(title.querySelector('.fr-view') || title) : '') + '</div>';
        if (img) body += '<figure>' + H.imageHtml(img) + '</figure>';
        if (opts.length) body += '<ul class="options ' + kindQ + '">' + opts.join('') + '</ul>';
        else if (rows.length && pairs.length) body += '<table class="matching"><tr><th>Begrip</th><th>Juiste koppeling</th></tr>' + rows.join('') + '</table>';
        else if (rows.length) {
          body += '<p class="hint">Koppel elk begrip aan het juiste antwoord (antwoorden staan in willekeurige volgorde).</p>';
          body += '<table class="matching"><tr><th>Begrippen</th><th>Antwoorden (willekeurige volgorde)</th></tr>' + rows.join('') + '</table>';
        } else {
          const inter = card.querySelector('.quiz-card__interactive');
          if (inter) body += '<div>' + H.cleanRich(inter) + '</div>';
        }
        if ((correct.size || pairs.length) && feedback) body += '<div class="answer"><span class="answer-label">Toelichting:</span> ' + H.esc(feedback) + '</div>';
        out.push('<div class="quiz">' + body + '</div>');
      });
    } else {
      // Onbekend bloktype: alle rijke tekst en afbeeldingen in documentvolgorde.
      const parts = [];
      inner.querySelectorAll('img, .fr-view').forEach((el) => {
        if (el.tagName === 'IMG') parts.push('<figure>' + H.imageHtml(el) + '</figure>');
        else if (!el.parentElement.closest('.fr-view')) parts.push(H.cleanRich(el));
      });
      if (parts.length) out.push('<div class="block">' + parts.join('\n') + '</div>');
      else if (txt(inner)) out.push('<div class="block">' + H.esc(txt(inner)) + '</div>');
    }
    return out.join('\n');
  };

  // Bouwt uit #cpe-book (ruwe Rise-blokken) het schone printdocument en toont dat in
  // het bovenste document. Geeft de volledige HTML terug (ook bruikbaar als export).
  H.renderPrint = function (css, opts) {
    const o = Object.assign({ pageNumbers: true }, opts || {});
    const book = document.getElementById('cpe-book');
    if (!book) throw new Error('Geen boek aanwezig; eerst buildBook.');
    const title = H.norm(document.title) || 'Cursus';
    const chapters = Array.from(book.querySelectorAll('.cpe-chapter'));
    const toc = [];
    const sections = [];
    chapters.forEach((ch, idx) => {
      const h = ch.querySelector('.cpe-chapter-header h1');
      const chTitle = h ? H.norm(h.textContent) : 'Hoofdstuk ' + (idx + 1);
      toc.push('<li>' + H.esc(chTitle) + '</li>');
      const blocks = Array.from(ch.querySelectorAll('.blocks-lesson > .noOutline')).map(H.convertBlock).filter(Boolean);
      let joined = blocks.join('\n');
      joined = joined.replace(/<h2 class="band">\s*Vraag\s*<\/h2>\s*(?=<div class="quiz">)/g, '');
      sections.push('<section class="chapter"><div class="chapter-header"><p class="kicker">Hoofdstuk ' + (idx + 1) + '</p><h1>' + H.esc(chTitle) + '</h1></div>' + joined + '</section>');
    });
    const cover = '<section class="cover"><p class="cover-kicker">E-module</p><h1>' + H.esc(title) + '</h1><div class="toc"><h2>Inhoud</h2><ol>' + toc.join('') + '</ol></div></section>';
    const pageCss = (o.pageNumbers ? css : css.replace(/@page \{ @bottom-left[^\n]*\n/, ''))
      .replace('__CPE_TITLE__', title.replace(/\\/g, '\\\\').replace(/"/g, '\\"'));
    const inner = cover + '\n' + sections.join('\n');

    // Tonen in het bovenste document: boek verbergen, printdocument ernaast zetten.
    // Stylesheets van de speler (Bootstrap e.d.) uitschakelen: die zetten body op 100%/overflow
    // hidden (dan drukt Chrome één pagina af) en kapen klassen als .card en .cover.
    Array.from(document.styleSheets).forEach((sh) => {
      const n = sh.ownerNode;
      if (n && (n.id === 'cpe-print-style' || n.id === 'cpe-book-style')) return;
      try { if (!sh.disabled) { sh.disabled = true; n && n.setAttribute('data-cpe-disabled', '1'); } } catch (e) { /* negeren */ }
    });
    let st = document.getElementById('cpe-print-style');
    if (!st) { st = document.createElement('style'); st.id = 'cpe-print-style'; document.head.appendChild(st); }
    st.textContent = pageCss + '\nhtml, body { height: auto !important; min-height: 0 !important; overflow: visible !important; margin: 0 !important; padding: 0 !important; }' +
      '\n#cpe-book { display: none !important; }\n#cpe-print { max-width: 760px; margin: 0 auto; }\n@media print { #cpe-print { max-width: none; } }';
    let root = document.getElementById('cpe-print');
    if (!root) { root = document.createElement('div'); root.id = 'cpe-print'; document.body.appendChild(root); }
    root.innerHTML = inner;
    window.scrollTo(0, 0);
    return '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>' + H.esc(title) + '</title><style>' + pageCss + '</style></head><body>' + inner + '</body></html>';
  };

  // Volledige verzameling: alle hoofdstukken in het boek zetten. Wordt zowel door de
  // extensie als door de console-variant gebruikt. onProgress(i, total, lesson, detail).
  H.buildBook = async function (css, opts) {
    const o = Object.assign({ settle: 600, timeout: 15000, onProgress: null, shouldStop: () => false }, opts || {});
    const info = H.info();
    if (!info || !info.lessons.length) throw new Error('Geen Rise-hoofdstukkenlijst gevonden.');
    const total = info.lessons.length;
    const report = [];
    let prevSig = '';
    await H.loadCourseData();
    // Eerst alle lessen verzamelen (de speler moet zichtbaar blijven om te renderen).
    const chapters = [];
    for (let i = 0; i < total; i++) {
      if (o.shouldStop()) break;
      const lesson = info.lessons[i];
      if (o.onProgress) o.onProgress(i, total, lesson, 'laden');
      H.goto(lesson.href);
      let ready = await H.waitForLesson(lesson, prevSig, Math.min(o.timeout, 5000));
      if (!ready.ok) {
        // Hash-navigatie pakt soms niet na quiz-interactie: klik dan de echte menulink.
        const w0 = H.walk();
        const link = w0 && Array.from(w0.riseDoc.querySelectorAll('a[href*="/lessons/' + lesson.id + '"]')).find((a) => !/button/i.test(a.className));
        if (link) { link.click(); ready = await H.waitForLesson(lesson, prevSig, o.timeout); }
      }
      for (let attempt = 0; !ready.ok && attempt < 2; attempt++) {
        // Nog niet geladen: eerst naar een andere les springen en dan terug (Rise negeert soms een herhaalde hash).
        const other = info.lessons[(i + attempt + 1) % total];
        H.goto(other.href);
        await H.sleep(1500);
        const w1 = H.walk();
        const link1 = w1 && Array.from(w1.riseDoc.querySelectorAll('a[href*="/lessons/' + lesson.id + '"]')).find((a) => !/button/i.test(a.className));
        if (link1) link1.click(); else H.goto(lesson.href);
        ready = await H.waitForLesson(lesson, prevSig, o.timeout);
      }
      await H.sleep(o.settle);
      const prep = await H.prepare(Math.min(o.settle, 1500));
      let ex = H.extractLesson(lesson);
      const w = H.walk();
      const sigNow = w ? H.signature(H.livePage(w.riseDoc, lesson.id, lesson.title)) : '';
      if (ex && sigNow && sigNow === prevSig && total > 1) {
        // Zelfde inhoud als vorige les: niet als hoofdstuk opnemen (dubbel), wel melden.
        H.log('dubbele inhoud overgeslagen voor', lesson.title);
        ex = null;
      }
      prevSig = sigNow;
      const line = { i: i + 1, title: lesson.title, ready: ready.ok, waitMs: ready.ms,
        continues: prep && prep.continues, quizzes: prep && prep.quizzes, text: ex && ex.text, images: ex && ex.images };
      report.push(line);
      H.log('hoofdstuk', line);
      if (ex) chapters.push({ lesson, i, html: ex.html });
    }
    H.openBook(css, info.courseTitle);
    chapters.forEach((c) => H.appendChapter(c.lesson, c.i, total, c.html));
    // Wachten tot afbeeldingen in het boek geladen zijn.
    const imgs = Array.from(document.querySelectorAll('#cpe-book img')).filter((i) => !i.complete);
    await Promise.race([
      Promise.all(imgs.map((i) => new Promise((r) => { i.addEventListener('load', r, { once: true }); i.addEventListener('error', r, { once: true }); }))),
      H.sleep(15000)
    ]);
    await H.sleep(300);
    window.scrollTo(0, 0);
    return { info, report, chapters: chapters.length, height: document.documentElement.scrollHeight };
  };

  window.__cpeRise = H;
  return true;
})();

  const CSS = "\n@page { size: A4; margin: 20mm 26mm 22mm; }\n@page { @bottom-left { content: \"__CPE_TITLE__\"; font: 9pt Helvetica, Arial, sans-serif; color: #777; } @bottom-right { content: counter(page) \" / \" counter(pages); font: 9pt Helvetica, Arial, sans-serif; color: #777; } }\n* { box-sizing: border-box; }\nhtml { font-size: 11pt; }\nbody { margin: 0; font-family: \"Helvetica Neue\", Helvetica, Arial, sans-serif; color: #1a1a1a; line-height: 1.5; text-rendering: optimizeLegibility; }\np { margin: 0 0 .85em; orphans: 3; widows: 3; }\np:last-child { margin-bottom: 0; }\na { color: inherit; text-decoration: underline; }\nimg { max-width: 100%; height: auto; display: block; }\ntable { border-collapse: collapse; width: 100%; margin: .8em 0 1.4em; font-size: .95em; break-inside: avoid; font-variant-numeric: tabular-nums; }\nth, td { border: 1px solid #cfcfcf; padding: .35em .6em; text-align: left; vertical-align: top; }\nth { background: #eef2f5; }\n\n.cover { break-after: page; display: flex; flex-direction: column; justify-content: center; min-height: 240mm; }\n.cover .cover-kicker { font-size: 11pt; color: #bc1413; margin-bottom: .8em; }\n.cover h1 { font-size: 28pt; line-height: 1.15; margin: 0 0 .6em; letter-spacing: -.01em; text-wrap: balance; }\n.cover .toc { margin-top: 3em; }\n.cover .toc h2 { font-size: 10pt; text-transform: uppercase; letter-spacing: .08em; color: #555; margin: 0 0 .6em; }\n.cover .toc ol { margin: 0; padding-left: 1.4em; font-size: 12pt; line-height: 1.7; }\n\n.chapter { break-before: page; }\n.chapter-header { border-bottom: 1px solid #bc1413; padding-bottom: .6em; margin: 0 0 1.6em; break-inside: avoid; break-after: avoid; }\n.chapter-header .kicker { font-size: 11pt; color: #bc1413; margin: 0 0 .35em; }\n.chapter-header h1 { font-size: 20pt; line-height: 1.2; margin: 0; letter-spacing: -.01em; text-wrap: balance; }\n\nh2.section { font-size: 14pt; color: #1a1a1a; margin: 2em 0 .6em; break-after: avoid; line-height: 1.25; text-wrap: balance; }\nh2.band { font-size: 17pt; color: #1a1a1a; line-height: 1.25; padding: 0 0 .35em; border-bottom: 1px solid #c9c9c9; margin: 2.8em 0 1.1em; break-after: avoid; text-wrap: balance; }\nh3 { font-size: 13pt; line-height: 1.3; margin: 1.5em 0 .5em; break-after: avoid; }\n.block { margin: 0 0 1.5em; }\n.block.keep { break-inside: avoid; }\n\nul.list, ol.list, .block ul, .block ol, .card ul, .card ol, .quiz .question ul, .quiz .question ol, dd ul, dd ol, .labels ul, .labels ol, .answer ul, .answer ol { margin: .5em 0 1.5em; padding-left: 1.5em; }\nul.list li, ol.list li, .block li, .card li, dd li, .labels ol li li, .answer li { margin: 0 0 .45em; }\n.block li p, .card li p { margin: 0; }\nol.numbered { list-style: none; padding-left: 0; counter-reset: n; }\nol.numbered li { counter-increment: n; position: relative; padding-left: 2.3em; margin: 0 0 .7em; }\nol.numbered li::before { content: counter(n); position: absolute; left: 0; top: .05em; width: 1.6em; height: 1.6em; border-radius: 50%; background: #bc1413; color: #fff; font-weight: 700; font-size: .85em; text-align: center; line-height: 1.6; }\n\nfigure { margin: 1.2em 0 1.8em; break-inside: avoid; }\nfigure img { margin: 0 auto; max-height: 90mm; width: auto; }\n/* Brede schema's niet minuscuul laten: hele kolombreedte. */\nfigure.wide img { width: 100%; max-height: 90mm; object-fit: contain; }\n/* Hotspot- en meterafbeeldingen: hooguit een halve pagina hoog. */\nfigure.graphic img { max-height: 80mm; width: auto; }\n/* Storyline: veel fotodia's; iets lager zodat twee figuren op een pagina passen. */\n.sl figure img { max-height: 72mm; }\nfigcaption { font-size: 9.5pt; line-height: 1.4; color: #666; margin-top: .6em; text-align: center; }\n.image-aside { display: flex; gap: 1.4em; align-items: flex-start; break-inside: avoid; margin: 0 0 1.6em; }\n.image-aside .text { flex: 1 1 55%; }\n.image-aside figure { flex: 0 0 42%; margin: 0; }\n.image-aside figure img { width: 100%; max-height: 70mm; object-fit: contain; }\n\n.card { border: 1px solid #d9d9d9; border-radius: 6px; padding: 1em 1.3em 1.1em; margin: 0 0 1.3em; break-inside: avoid; background: #fff; }\n.card .card-number { font-size: 9pt; line-height: 1.4; text-transform: uppercase; letter-spacing: .08em; color: #666; font-weight: 700; margin-bottom: .2em; }\n.card h3 { margin: 0 0 .7em; font-size: 13pt; }\n.card figure { margin: .6em 0 1em; }\n.card figure img { max-height: 62mm; }\n.process-intro { background: #f4f6f8; border: none; }\n\n.quiz { break-inside: avoid; border: 1px solid #b9cdd8; border-radius: 6px; padding: 1.1em 1.3em 1.2em; margin: 2em 0 1.8em; background: #f7fafc; }\n.quiz .quiz-label { font-size: 9pt; line-height: 1.4; text-transform: uppercase; letter-spacing: .1em; color: #2f6f8f; font-weight: 700; margin-bottom: .5em; }\n.quiz .question { font-weight: 700; margin-bottom: .7em; }\n.quiz .hint { font-style: italic; color: #555; margin-bottom: .5em; }\n.quiz figure img { max-height: 70mm; margin: 0; }\n.quiz ul.options { list-style: none; padding: 0; margin: .5em 0 0; }\n.quiz ul.options li { position: relative; padding-left: 1.8em; margin: 0 0 .7em; break-inside: avoid; }\n.quiz ul.options li::before { content: \"\"; position: absolute; left: 0; top: .2em; width: 1em; height: 1em; border: 1.5px solid #6a7b85; border-radius: 3px; background: #fff; }\n.quiz ul.options.radio li::before { border-radius: 50%; }\n.quiz .matching { width: 100%; }\n.quiz .matching td { width: 50%; }\n.quiz .matching .ok { color: #1d6b2f; font-weight: 700; margin-left: .4em; }\n.quiz table.sorting th { width: 50%; }\n.quiz ul.sorted { list-style: none; margin: 0; padding: 0; }\n.quiz ul.sorted li { margin: 0 0 .5em; padding-left: 1.4em; position: relative; }\n.quiz ul.sorted li::before { content: \"✓\"; color: #1d6b2f; font-weight: 700; position: absolute; left: 0; }\n.quiz ul.options li .cpe-mark { position: absolute; left: 0; top: 0; color: #fff; opacity: .02; font-size: 2pt; letter-spacing: -.2em; }\n.quiz ul.options li.correct { font-weight: 700; color: #1d6b2f; }\n.quiz ul.options li.correct p { display: inline; }\n.quiz ul.options li.correct::before { content: \"✓\"; color: #1d6b2f; font-weight: 700; font-size: .8em; line-height: 1.25; text-align: center; border-color: #1d6b2f; }\n.quiz .answer { margin-top: .9em; padding-top: .7em; border-top: 1px dashed #b9c6cd; color: #1a1a1a; }\n.quiz .answer p { display: inline; }\n.quiz .answer-label { font-weight: 700; color: #2f6f8f; }\n\n.labels { margin: 1em 0 .6em; padding-left: 1.4em; }\n.labels li { margin: 0 0 1.6em; }\n.labels .label-title { font-weight: 700; margin-bottom: .25em; break-after: avoid; }\n\ndl.accordion { margin: .8em 0 1.6em; }\ndl.accordion dt { font-weight: 700; margin: 1.3em 0 .35em; break-after: avoid; }\ndl.accordion dd { margin: 0 0 .6em 0; padding: .1em 0 .1em 1em; border-left: 3px solid #e2e2e2; break-inside: avoid; }\n\n.note { font-style: italic; color: #666; }\n.callout { display: flex; gap: 1.1em; align-items: flex-start; background: #f6ecea; border: 1px solid #bc1413; border-radius: 4px; padding: 1.1em 1.3em; margin: 1.4em 0 1.8em; break-inside: avoid; }\n.callout .callout-icon { flex: 0 0 auto; width: 1.7em; height: 1.7em; border: 2px solid #bc1413; border-radius: 50%; color: #bc1413; font: 700 1em/1.55em Georgia, \"Times New Roman\", serif; text-align: center; }\n.callout .callout-body { flex: 1 1 auto; }\n.callout .callout-body p:last-child { margin-bottom: 0; }\n.callout.quote { background: #f7f7f7; border-color: #cfcfcf; font-style: italic; }\n";
  const settle = (window.__cpeOptions && window.__cpeOptions.settle) || 600;
  console.time('[cpe] totaal');
  try {
    const r = await window.__cpeRise.buildBook(CSS, {
      settle,
      onProgress: (i, total, lesson) => console.log('[cpe] hoofdstuk ' + (i + 1) + '/' + total + ': ' + lesson.title)
    });
    console.table(r.report);
    console.log('[cpe] klaar: ' + r.chapters + ' hoofdstukken, hoogte ' + r.height + 'px.');
    window.__cpeReport = r;
    const x = await window.__cpeRise.exportHtml();
    window.__cpeRise.renderPrint(CSS, { pageNumbers: true });
    console.log('[cpe] HTML-bestand gedownload (' + Math.round(x.bytes / 1024) + ' kB, ' + x.inlined + '/' + x.images + ' afbeeldingen ingebed' + (x.failed ? ', ' + x.failed + ' mislukt' : '') + '). Opmaak toegepast: Cmd+P → Bewaar als PDF geeft de nette versie; het .html-bestand is voor book-to-print.py (invulbare vakjes).');
  } catch (e) {
    console.error('[cpe] fout', e);
  }
  console.timeEnd('[cpe] totaal');
})();
