// Modus C: Articulate Storyline-cursus (SCORM, HTML5-speler) → nette PDF met echte tekst.
//
// Werkwijze: Storyline levert per dia een JSON-bestand (html5/data/js/<id>.js) met alle
// tekst (textLib), afbeeldingen (imagelib / story_content) en lagen; de cursusstructuur
// staat in html5/data/js/data.js (scènes, dia's, quizvragen met juiste antwoorden) en het
// menu in html5/data/js/frame.js. We lezen die bestanden rechtstreeks (zelfde origin als de
// speler), zetten ze om naar dezelfde schone HTML als Rise-modus (RISE_PRINT_CSS) en drukken
// dat als één document af. Er hoeft dus niet door de dia's genavigeerd te worden.
//
// De helpers draaien in het spelertabblad (bovenste document) als window.__cpeStoryline.

function storylineInstallHelpers() {
  const S = {};
  S.esc = (t) => String(t == null ? '' : t).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  S.norm = (t) => String(t || '').replace(/\s+/g, ' ').trim();
  // Menulabels bevatten soms HTML-entiteiten (&apos;, &amp;): eerst decoderen, daarna weer netjes escapen.
  S.decode = (t) => String(t || '').replace(/&apos;|&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&');
  S.log = (...a) => { try { console.log('[cpe-sl]', ...a); } catch (e) { /* stil */ } };

  // Zoekt (door same-origin iframes heen) het document waarin de Storyline-speler draait.
  S.walk = function () {
    const isStory = (doc) => {
      const w = doc.defaultView;
      if (w && w.globals && w.globals.DATA_PATH_BASE !== undefined) return true;
      return Array.from(doc.scripts).some((s) => /html5\/data\/js\/frame\.js|slides\.min\.js|story_content\//.test(s.src || ''));
    };
    const visit = (doc) => {
      if (isStory(doc)) return doc;
      for (const f of doc.querySelectorAll('iframe')) {
        let cd = null;
        try { cd = f.contentDocument; } catch (e) { /* cross-origin */ }
        if (!cd || !cd.documentElement) continue;
        const r = visit(cd);
        if (r) return r;
      }
      return null;
    };
    const doc = visit(document);
    if (!doc) return null;
    return { doc, win: doc.defaultView, base: doc.location.href.replace(/[#?].*$/, '').replace(/[^/]*$/, '') };
  };

  S.detect = function () {
    const w = S.walk();
    if (!w) return null;
    return { title: S.norm(w.doc.title || document.title), base: w.base };
  };

  S.fetchText = async function (base, path) {
    const r = await fetch(base + path, { credentials: 'include' });
    if (!r.ok) throw new Error(path + ' → HTTP ' + r.status);
    return await r.text();
  };

  // De databestanden zijn JS: window.globalProvideData('slide', '<json>'). De JSON staat als
  // JS-stringliteral (met \'-, \n-, \uXXXX-escapes). Zelf decoderen: eval/new Function is in de
  // extensiecontext door de Content Security Policy niet toegestaan.
  S.decodeJsString = function (src, start) {
    // src[start] is het openingsteken (' of "); geeft [waarde, index na sluitteken].
    const q = src[start];
    let out = '';
    let i = start + 1;
    while (i < src.length) {
      const ch = src[i];
      if (ch === q) return [out, i + 1];
      if (ch !== '\\') { out += ch; i++; continue; }
      const n = src[i + 1];
      i += 2;
      switch (n) {
        case 'n': out += '\n'; break;
        case 'r': out += '\r'; break;
        case 't': out += '\t'; break;
        case 'b': out += '\b'; break;
        case 'f': out += '\f'; break;
        case 'v': out += '\v'; break;
        case '0': out += '\0'; break;
        case 'x': out += String.fromCharCode(parseInt(src.substr(i, 2), 16)); i += 2; break;
        case 'u':
          if (src[i] === '{') { const e = src.indexOf('}', i); out += String.fromCodePoint(parseInt(src.slice(i + 1, e), 16)); i = e + 1; }
          else { out += String.fromCharCode(parseInt(src.substr(i, 4), 16)); i += 4; }
          break;
        case '\r': if (src[i] === '\n') i++; break; // regelvervolg
        case '\n': case ' ': case ' ': break;
        default: out += n; // \' \" \\ \/ en onbekende escapes
      }
    }
    throw new Error('stringliteral niet afgesloten');
  };

  S.parseProvide = function (js) {
    const call = js.indexOf('globalProvideData');
    if (call < 0) throw new Error('geen globalProvideData in bestand');
    let i = js.indexOf('(', call) + 1;
    const skipWs = () => { while (i < js.length && /\s/.test(js[i])) i++; };
    skipWs();
    const first = S.decodeJsString(js, i); // naam ('slide', 'data', 'frame')
    i = first[1];
    skipWs();
    if (js[i] !== ',') throw new Error('onverwachte inhoud na naam');
    i++;
    skipWs();
    const second = S.decodeJsString(js, i);
    try {
      return JSON.parse(second[0]);
    } catch (e) {
      // Terugvaloptie (alleen waar eval is toegestaan, bijv. de console).
      let obj = null;
      const fake = { globalProvideData: (name, str) => { obj = JSON.parse(str); } };
      new Function('window', js)(fake);
      if (!obj) throw e;
      return obj;
    }
  };

  S.load = async function (onProgress) {
    const w = S.walk();
    if (!w) throw new Error('Geen Storyline-speler gevonden op dit tabblad.');
    const base = w.base;
    const data = S.parseProvide(await S.fetchText(base, 'html5/data/js/data.js'));
    let frame = null;
    try { frame = S.parseProvide(await S.fetchText(base, 'html5/data/js/frame.js')); } catch (e) { S.log('frame.js niet gelezen', e); }
    const metaById = {};
    const order = [];
    (data.scenes || []).forEach((sc) => {
      (sc.slides || []).forEach((sl) => {
        const id = String(sl.id || '').split('.').pop();
        if (!id) return;
        metaById[id] = Object.assign({ sceneId: sc.id, sceneTitle: sc.title || '' }, sl);
        order.push(id);
      });
    });
    const slides = {};
    const errors = [];
    for (let i = 0; i < order.length; i++) {
      const id = order[i];
      const m = metaById[id];
      if (onProgress) onProgress(i, order.length, m.title || id);
      const url = m.html5url || ('html5/data/js/' + id + '.js');
      try { slides[id] = S.parseProvide(await S.fetchText(base, url)); }
      catch (e) { errors.push(id + ': ' + (e.message || e)); }
    }
    S.state = { base, data, frame, metaById, order, slides, errors, title: S.norm(w.doc.title || document.title) };
    return S.state;
  };

  // ---- Tekst uit een dia-object ----
  S.textItems = function (ob) {
    const tl = ob.textLib;
    if (!tl || !tl.length) return null;
    // Bij toestandsgroepen (knoppen) staan meerdere varianten in textLib; neem de eerste met tekst.
    const item = tl.find((t) => t.vartext && t.id === '01') || tl.find((t) => t.vartext);
    if (!item) return null;
    const vt = item.vartext;
    const baseSize = ((vt.defaultBlockStyle || {}).baseSpanStyle || {}).fontSize || 0;
    const blocks = [];
    for (const b of vt.blocks || []) {
      const spans = (b.spans || []).map((sp) => ({ text: sp.text || '', bold: !!(sp.style && sp.style.fontIsBold), size: (sp.style && sp.style.fontSize) || baseSize }));
      const text = spans.map((s) => s.text).join('');
      if (!S.norm(text)) continue;
      const style = b.style || {};
      const list = ((style.listStyle || {}).listType || 'none');
      blocks.push({ spans, text, size: Math.max(baseSize, ...spans.map((s) => s.size)), list, level: style.listLevel || 0 });
    }
    return blocks.length ? blocks : null;
  };

  const BULLET_RE = /^\s*[▪•●■◦‣\-–]\s*\t?\s*/;
  S.blocksToHtml = function (blocks) {
    const out = [];
    let listOpen = null;
    const close = () => { if (listOpen) { out.push('</' + listOpen + '>'); listOpen = null; } };
    const spanHtml = (spans) => {
      // Storyline-auteurs zetten vaak hele tekstvakken in vet; dat is opmaak, geen nadruk.
      // Alleen vet houden als de alinea gemengd is (deels vet) of een kort label is.
      const nonEmpty = spans.filter((sp) => S.norm(sp.text));
      const allBold = nonEmpty.length > 0 && nonEmpty.every((sp) => sp.bold);
      const total = nonEmpty.map((sp) => sp.text).join('');
      const keepBold = !allBold || (blocks.length === 1 && S.norm(total).length <= 50);
      let s = '';
      for (const sp of spans) {
        const t = S.esc(sp.text).replace(/\r\n|\r|\n/g, '<br>');
        s += (sp.bold && keepBold && S.norm(sp.text)) ? '<strong>' + t + '</strong>' : t;
      }
      return s.replace(/(<br>\s*)+$/, '');
    };
    for (const b of blocks) {
      // Regels die met een opsommingsteken beginnen of een lijststijl hebben: als lijst.
      const lines = b.text.split(/\r\n|\r|\n/).filter((l) => S.norm(l));
      const isBulletText = lines.length && lines.every((l) => BULLET_RE.test(l));
      if (b.list !== 'none' || isBulletText) {
        const tag = b.list === 'number' || b.list === 'numbered' ? 'ol' : 'ul';
        if (listOpen !== tag) { close(); out.push('<' + tag + '>'); listOpen = tag; }
        if (isBulletText) lines.forEach((l) => out.push('<li>' + S.esc(l.replace(BULLET_RE, '')) + '</li>'));
        else out.push('<li>' + spanHtml(b.spans) + '</li>');
      } else {
        close();
        // Een alinea waarin sommige regels een opsommingsteken hebben: splitsen.
        if (lines.length > 1 && lines.some((l) => BULLET_RE.test(l))) {
          let ul = false;
          lines.forEach((l) => {
            if (BULLET_RE.test(l)) { if (!ul) { out.push('<ul>'); ul = true; } out.push('<li>' + S.esc(l.replace(BULLET_RE, '')) + '</li>'); }
            else { if (ul) { out.push('</ul>'); ul = false; } out.push('<p>' + S.esc(l) + '</p>'); }
          });
          if (ul) out.push('</ul>');
        } else {
          out.push('<p>' + spanHtml(b.spans) + '</p>');
        }
      }
    }
    close();
    return out.join('');
  };

  // Alle objecten van een laag plat, met absolute positie (groepen tellen hun eigen offset op).
  S.flatten = function (container, ox, oy, acc, groupKind, top) {
    for (const ob of container.objects || []) {
      const x = (ox || 0) + (ob.xPos || 0);
      const y = (oy || 0) + (ob.yPos || 0);
      const t = top || { x, y }; // positie van de bovenste groep: leden blijven bij elkaar
      acc.push({ ob, x, y, gx: t.x, gy: t.y, group: groupKind || '' });
      if (ob.objects) S.flatten(ob, x, y, acc, ob.kind, t);
    }
    return acc;
  };

  // De gepubliceerde bestanden staan onder de assetLib-url (meestal mobile/…); de url in
  // imagelib (story_content/…) bestaat vaak niet in de HTML5-uitvoer.
  S.assetUrl = function (assetId) {
    const lib = (S.state && S.state.data && S.state.data.assetLib) || [];
    const a = lib.find((x) => x.id === assetId);
    return a ? (a.url || a.mobileUrl || '') : '';
  };
  S.imageUrl = function (ob) {
    if (ob.imagelib && ob.imagelib.length) {
      const il = ob.imagelib[0];
      return (il.assetId != null && S.assetUrl(il.assetId)) || il.url || '';
    }
    const d = ob.data || {};
    if (d.imagedata) {
      if (d.imagedata.assetId != null) { const u = S.assetUrl(d.imagedata.assetId); if (u) return u; }
      if (d.imagedata.url) return d.imagedata.url;
    }
    if (d.html5data && d.html5data.url && ob.kind === 'image') return 'story_content/' + d.html5data.url;
    return '';
  };

  S.isDecorativeImage = function (ob, url) {
    const w = ob.width || 0, h = ob.height || 0;
    if (w < 240 || h < 150) return true;
    if (w / Math.max(h, 1) > 4.5 || h / Math.max(w, 1) > 4.5) return true;
    if (/_[0-9A-F]{6}_[0-9A-F]{6}\./i.test(url)) return true; // patroonvullingen
    if (/zoomIcon|\/Shape[0-9A-Za-z]+\.png$/i.test(url)) return true;
    const alt = (ob.imagelib && ob.imagelib[0] && ob.imagelib[0].altText) || '';
    if (/transparent|arrow|pijl|icon|button|knop|driehoek|triangle|achtergrond|background/i.test(alt)) return true;
    return false;
  };

  // Vaste onderdelen (voortgangsbalk, menu, kruimelpad) komen op vrijwel elke dia terug:
  // tel per laag- en teksthandtekening hoe vaak die voorkomt en sla de terugkerende over.
  S.computeChrome = function () {
    const st = S.state;
    const layerCount = {}, textCount = {};
    let slideCount = 0;
    for (const id of st.order) {
      const sl = st.slides[id];
      if (!sl) continue;
      slideCount++;
      const seenText = new Set();
      (sl.slideLayers || []).forEach((L, li) => {
        const items = S.flatten(L, 0, 0, []);
        const texts = items.map((it) => S.textItems(it.ob)).filter(Boolean).map((b) => S.norm(b.map((x) => x.text).join(' ')));
        const imgs = items.map((it) => S.imageUrl(it.ob)).filter(Boolean);
        if (!L.isBaseLayer && (texts.length || imgs.length)) {
          const key = texts.concat(imgs).join('|');
          layerCount[key] = (layerCount[key] || 0) + 1;
        }
        texts.forEach((t) => { if (!seenText.has(t)) { seenText.add(t); textCount[t] = (textCount[t] || 0) + 1; } });
      });
    }
    st.chrome = { layerCount, textCount, slideCount };
  };

  S.isChromeLayer = function (L) {
    if (L.isBaseLayer) return false;
    const items = S.flatten(L, 0, 0, []);
    const texts = items.map((it) => S.textItems(it.ob)).filter(Boolean).map((b) => S.norm(b.map((x) => x.text).join(' ')));
    const imgs = items.map((it) => S.imageUrl(it.ob)).filter(Boolean);
    if (!texts.length && !imgs.length) return true;
    const key = texts.concat(imgs).join('|');
    const n = S.state.chrome.layerCount[key] || 0;
    return n >= 3 && n >= S.state.chrome.slideCount * 0.15;
  };

  S.isChromeText = function (t) {
    const norm = S.norm(t);
    if (!norm) return true;
    if (/%_player|%_playerVars|%[A-Za-z_]+%/.test(norm)) return true; // variabelen (voortgang, titels)
    if (/^(menu|volgende|vorige|terug|next|previous|back|sluiten|close|verder|start|submit|verzenden|indienen|ok|oké|afspelen|play|pauze|pause|opnieuw|herhalen|replay)$/i.test(norm)) return true;
    if (/^[\d.\s]+$/.test(norm)) return true; // losse dianummers
    if (/^einde (hoofdstuk|module|e-module)/i.test(norm)) return true;
    const c = S.state.chrome.textCount[norm] || 0;
    return c >= 4 && c >= S.state.chrome.slideCount * 0.25;
  };

  // Verzamelt alle juiste keuze-id's uit de antwoorddefinitie van een quizvraag.
  S.correctChoices = function (interaction) {
    const ids = new Set();
    const values = [];
    const walk = (st) => {
      if (!st) return;
      if (Array.isArray(st)) { st.forEach(walk); return; }
      if (st.choiceid) ids.add(String(st.choiceid).split('.').pop());
      if (st.value != null && st.kind !== 'other') values.push(String(st.value));
      if (st.text != null) values.push(String(st.text));
      if (st.statements) walk(st.statements);
      if (st.statement) walk(st.statement);
    };
    (interaction.answers || []).filter((a) => a.status === 'correct').forEach((a) => walk(a.evaluate));
    return { ids, values };
  };

  // Koppelvragen: antwoorddefinitie bevat per juiste combinatie een 'equals'-uitspraak met
  // choiceid en het bijbehorende doel (matchid/targetid). Geeft [links, rechts]-paren.
  S.matchingPairs = function (it) {
    const byId = {};
    (it.choices || []).forEach((c) => { byId[String(c.id).split('.').pop()] = c.lmstext || ''; });
    (it.statements || []).forEach((c) => { byId[String(c.id).split('.').pop()] = c.lmstext || ''; });
    const pairs = [];
    const walk = (st) => {
      if (!st) return;
      if (Array.isArray(st)) { st.forEach(walk); return; }
      if (st.choiceid) {
        const choice = byId[String(st.choiceid).split('.').pop()];
        const sid = st.statementid || st.matchid || st.targetid;
        const stmt = sid != null ? (byId[String(sid).split('.').pop()] || '') : '';
        // Links het begrip/de stelling (doel), rechts het juiste sleep- of keuze-item.
        const clean = (t) => t.replace(/\s*-\s*(rectangle|oval|shape|picture|image|freeform|rounded rectangle)\s*\d*\s*$/i, '').trim();
        if (choice && stmt) pairs.push([clean(stmt), clean(choice)]);
      }
      if (st.statements) walk(st.statements);
      if (st.statement) walk(st.statement);
    };
    (it.answers || []).filter((a) => a.status === 'correct').forEach((a) => walk(a.evaluate));
    return pairs;
  };

  // Quizvragen: keuzes uit de cursusdata, juiste antwoorden uit de antwoorddefinitie.
  S.quizHtml = function (interactions, question, feedback) {
    const out = [];
    for (const it of interactions) {
      const { ids, values } = S.correctChoices(it);
      const choices = it.choices || [];
      const multi = /multipleresponse|multiresponse|checkbox/i.test(it.type || '') || ids.size > 1;
      let body = '<div class="quiz-label">Vraag</div>';
      const qText = question ? (question.blocks ? S.blocksToHtml(question.blocks) : '<p>' + S.esc(question.text) + '</p>') : '<p>' + S.esc(it.lmstext || '') + '</p>';
      body += '<div class="question">' + qText + '</div>';
      const isFillin = /fillin|numeric|text|essay/i.test(it.type || '') || /TextEntry|Numeric/i.test(it.lmsId || '');
      const isMatching = /matching|sequence|dragdrop|hotspot/i.test(it.type || '');
      if (isFillin) {
        const answers = choices.map((c) => c.lmstext).filter(Boolean).concat(values);
        body += '<div class="answer"><span class="answer-label">Juiste antwoord:</span> ' + S.esc(answers.join(' of ') || '—') + '</div>';
      } else if (isMatching) {
        // Koppel-/sleepvragen: de juiste combinaties staan in de antwoorddefinitie (paren van keuze-id's).
        const pairs = S.matchingPairs(it);
        if (pairs.length) {
          body += '<table class="matching">' + pairs.map(([l, r]) => '<tr><td>' + S.esc(l) + '</td><td>' + S.esc(r) + ' <span class="ok">✓</span></td></tr>').join('') + '</table>';
        } else if (choices.length) {
          body += '<ul class="options">' + choices.map((c) => '<li>' + S.esc(c.lmstext || '') + '</li>').join('') + '</ul>';
        }
      } else if (choices.length) {
        body += '<ul class="options ' + (multi ? 'check' : 'radio') + '">';
        choices.forEach((c) => {
          const cid = String(c.id || '').split('.').pop();
          const ok = ids.has(cid);
          S.optSeq = (S.optSeq || 0) + 1;
          const mark = 'cpeopt' + String(S.optSeq).padStart(4, '0');
          body += '<li data-opt="' + S.esc(c.lmstext || '') + '" data-mark="' + mark + '"' + (ok ? ' data-correct="1" class="correct"' : '') + '><span class="cpe-mark">' + mark + '</span>' + S.esc(c.lmstext || '') + '</li>';
        });
        body += '</ul>';
      } else if (values.length) {
        body += '<div class="answer"><span class="answer-label">Juiste antwoord:</span> ' + S.esc(values.join(' / ')) + '</div>';
      }
      if (feedback && it === interactions[interactions.length - 1]) body += '<div class="answer"><span class="answer-label">Toelichting:</span> ' + S.esc(feedback) + '</div>';
      out.push('<div class="quiz">' + body + '</div>');
    }
    return out;
  };

  // Zet één dia om naar HTML-blokken. Geeft '' terug als er niets inhoudelijks in staat.
  S.slideHtml = function (id, opts) {
    const st = S.state;
    const sl = st.slides[id];
    const meta = st.metaById[id] || {};
    if (!sl) return { html: '', heading: '' };
    const interactions = (meta.interactions || []).filter((it) => it.kind === 'interaction');
    const choiceTexts = new Set();
    interactions.forEach((it) => (it.choices || []).forEach((c) => choiceTexts.add(S.norm(c.lmstext))));

    // Inhoud verzamelen: basislaag eerst, daarna de inhoudslagen (pop-ups, tabbladen).
    const items = [];
    (sl.slideLayers || []).forEach((L, li) => {
      if (S.isChromeLayer(L)) return;
      S.flatten(L, 0, 0, []).forEach((it) => items.push(Object.assign(it, { layer: li })));
    });
    const seen = new Set();
    const entries = [];
    for (const it of items) {
      const ob = it.ob;
      if (ob.kind === 'video') { entries.push({ kind: 'note', text: 'Video: ' + (ob.altText || (ob.data && ob.data.altText) || 'video in de e-module'), x: it.x, y: it.y, gx: it.gx, gy: it.gy, layer: it.layer }); continue; }
      if (ob.kind === 'webobject') { entries.push({ kind: 'note', text: 'Webinhoud (niet af te drukken)', x: it.x, y: it.y, gx: it.gx, gy: it.gy, layer: it.layer }); continue; }
      const url = S.imageUrl(ob);
      if (url) {
        if (!S.isDecorativeImage(ob, url) && !seen.has('img:' + url)) {
          seen.add('img:' + url);
          entries.push({ kind: 'image', url, w: ob.width, h: ob.height, alt: (ob.imagelib && ob.imagelib[0] && ob.imagelib[0].altText) || '', x: it.x, y: it.y, gx: it.gx, gy: it.gy, layer: it.layer });
        }
      }
      const blocks = S.textItems(ob);
      if (!blocks) continue;
      if (ob.accType === 'button') continue; // knoppen (navigatie, sluiten, afspelen)
      const full = S.norm(blocks.map((b) => b.text).join(' '));
      if (S.isChromeText(full)) continue;
      if (seen.has('t:' + full)) continue;
      seen.add('t:' + full);
      const isChoice = choiceTexts.has(full);
      entries.push({ kind: 'text', blocks, text: full, size: Math.max(...blocks.map((b) => b.size)), isChoice, x: it.x, y: it.y, gx: it.gx, gy: it.gy, layer: it.layer, accType: ob.accType });
    }
    // Leesvolgorde: per laag, van boven naar beneden en van links naar rechts (banden van 60px).
    entries.sort((a, b) => (a.layer - b.layer) || (Math.round(a.gy / 80) - Math.round(b.gy / 80)) || (a.gx - b.gx) || (a.y - b.y) || (a.x - b.x));

    const texts = entries.filter((e) => e.kind === 'text' && !e.isChoice);
    let heading = null;
    if (texts.length) {
      const cands = texts.filter((e) => e.layer === 0 && e.size >= 40 && e.text.length <= 120 && !/^[\d.\s]+$/.test(e.text));
      cands.sort((a, b) => (b.size - a.size) || (a.y - b.y));
      if (cands.length) heading = cands[0];
    }
    const parts = [];
    if (heading && !opts.suppressHeading) parts.push('<h2 class="section">' + S.esc(heading.text) + '</h2>');
    let question = null;
    if (interactions.length) {
      question = texts.find((e) => e !== heading && /\?\s*$/.test(e.text)) || texts.find((e) => e !== heading && e.layer === 0) || heading;
    }
    // Feedbacklagen van quizvragen: 'Juist' → toelichting in het vraagkader, 'Onjuist' → weglaten.
    const layerKind = {};
    let feedback = '';
    if (interactions.length) {
      const byLayer = {};
      entries.forEach((e) => { if (e.kind === 'text' && e.layer > 0) (byLayer[e.layer] = byLayer[e.layer] || []).push(e); });
      Object.keys(byLayer).forEach((li) => {
        const ts = byLayer[li];
        const first = S.norm(ts[0].text);
        if (/^(onjuist|incorrect|fout|helaas|dat klopt niet|niet goed|jammer)\b/i.test(first)) layerKind[li] = 'wrong';
        else if (/^(juist|correct|goed|dat klopt|prima|goed gedaan|klopt)\b/i.test(first)) {
          layerKind[li] = 'right';
          const rest = ts.slice(1).map((e) => S.norm(e.text)).filter((t) => t && !/^(juist|correct|goed zo|dat klopt)!?\.?$/i.test(t));
          if (rest.length && !feedback) feedback = rest.join(' ').replace(/^(dat klopt|juist|correct|goed zo|goed gedaan|prima)[!.]*\s*/i, '');
        }
      });
    }
    const quizHtml = S.quizHtml(interactions, question, feedback);
    let quizPlaced = false;
    for (const e of entries) {
      if (e === heading || e === question || e.isChoice) continue;
      if (layerKind[e.layer]) continue; // feedbacklagen zijn al verwerkt
      if (quizHtml.length && !quizPlaced && e.layer > 0) { parts.push(...quizHtml); quizPlaced = true; }
      if (e.kind === 'text') {
        if (e.size >= 40 && e.text.length <= 120) parts.push('<h3>' + S.esc(e.text) + '</h3>');
        else parts.push('<div class="block">' + S.blocksToHtml(e.blocks) + '</div>');
      } else if (e.kind === 'image') {
        if (st.images && e.url in st.images && !st.images[e.url]) continue; // niet op te halen
        const src = st.images && st.images[e.url] ? st.images[e.url] : (st.base + e.url);
        const cls = e.w > e.h * 1.6 ? 'wide' : (e.h > e.w * 1.2 ? 'tall' : '');
        parts.push('<figure' + (cls ? ' class="' + cls + '"' : '') + '><img src="' + S.esc(src) + '" alt="' + S.esc(e.alt) + '"></figure>');
      } else if (e.kind === 'note') {
        parts.push('<p class="note">[' + S.esc(e.text) + ']</p>');
      }
    }
    if (quizHtml.length && !quizPlaced) parts.push(...quizHtml);
    return { html: parts.join('\n'), heading: heading ? heading.text : '' };
  };

  // Hoofdstukindeling: het spelermenu (frame.navData.outline), anders de scènes.
  S.chapters = function () {
    const st = S.state;
    const out = [];
    const outline = st.frame && st.frame.navData && st.frame.navData.outline && st.frame.navData.outline.links;
    const sceneById = {};
    (st.data.scenes || []).forEach((sc) => { sceneById[String(sc.id)] = sc; });
    const slidesOfScene = (sceneId) => (sceneById[sceneId] ? (sceneById[sceneId].slides || []).map((s) => String(s.id).split('.').pop()) : []);
    if (outline && outline.length) {
      outline.forEach((link) => {
        const sceneId = String(link.slideid || '').replace(/^_player\./, '').split('.')[0];
        let ids = slidesOfScene(sceneId);
        if (!ids.length && link.links) ids = link.links.map((l) => String(l.slideid || '').split('.').pop());
        out.push({ label: S.norm(S.decode(link.displaytext || link.slidetitle || '')), ids });
      });
      // Scènes die niet in het menu staan (bijv. casussen/quizzen) achteraan toevoegen.
      const used = new Set(out.flatMap((c) => c.ids));
      (st.data.scenes || []).forEach((sc) => {
        const ids = slidesOfScene(String(sc.id)).filter((id) => !used.has(id));
        if (ids.length && !/PromptScene/i.test(String(sc.id))) out.push({ label: S.norm(sc.title || ''), ids });
      });
    } else {
      (st.data.scenes || []).forEach((sc) => {
        if (/PromptScene/i.test(String(sc.id))) return;
        out.push({ label: S.norm(sc.title || ''), ids: slidesOfScene(String(sc.id)) });
      });
    }
    return out.filter((c) => c.ids.length);
  };

  S.isSkippableSlide = function (id) {
    const meta = S.state.metaById[id] || {};
    const t = S.norm(meta.title || '');
    if (/^(menu|keuzemenu|hoofdmenu|results? slide|resultaten|hervatten|warning|blank)$/i.test(t)) return true;
    if (/^(ResumePromptSlide|ExternalInterfaceErrorSlide)$/.test(id)) return true;
    return false;
  };

  // Afbeeldingen ophalen en als data-URI inbedden (zodat de HTML/PDF zelfstandig is).
  S.embedImages = async function (urls, onProgress) {
    const st = S.state;
    st.images = st.images || {};
    let i = 0;
    for (const u of urls) {
      i++;
      if (onProgress) onProgress(i, urls.length);
      if (u in st.images) continue;
      try {
        const resp = await fetch(st.base + u, { credentials: 'include' });
        const blob = await resp.blob();
        if (!resp.ok || !/^image\//.test(blob.type)) { st.images[u] = null; S.log('geen afbeelding', u, resp.status, blob.type); continue; }
        st.images[u] = await new Promise((ok, err) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = err; fr.readAsDataURL(blob); });
      } catch (e) { st.images[u] = null; S.log('afbeelding mislukt', u, e); }
    }
  };

  // Bouwt het complete printdocument (cover + inhoud + hoofdstukken) als HTML-string.
  S.build = async function (css, opts) {
    const o = Object.assign({ onProgress: null, embedImages: true, pageNumbers: true }, opts || {});
    if (!S.state) await S.load((i, n, t) => o.onProgress && o.onProgress('laden', i, n, t));
    const st = S.state;
    S.computeChrome();
    S.optSeq = 0;
    const chapters = S.chapters();
    // Eerst alle afbeeldings-URL's verzamelen (via een droge run zonder inbedding).
    const urls = new Set();
    if (o.embedImages) {
      st.images = st.images || {};
      for (const ch of chapters) for (const id of ch.ids) {
        const sl = st.slides[id];
        if (!sl || S.isSkippableSlide(id)) continue;
        (sl.slideLayers || []).forEach((L) => { if (S.isChromeLayer(L)) return; S.flatten(L, 0, 0, []).forEach((it) => { const u = S.imageUrl(it.ob); if (u && !S.isDecorativeImage(it.ob, u)) urls.add(u); }); });
      }
      await S.embedImages(Array.from(urls), (i, n) => o.onProgress && o.onProgress('afbeeldingen', i, n, ''));
    }
    const toc = [];
    const sections = [];
    const report = [];
    let n = 0;
    chapters.forEach((ch) => {
      const bodies = [];
      let title = ch.label;
      let firstHeading = '';
      ch.ids.forEach((id, idx) => {
        if (S.isSkippableSlide(id)) return;
        const meta = st.metaById[id] || {};
        const isTitleSlide = idx === 0 && /hoofdstuk\s*titel|titelpagina|title/i.test(meta.title || '');
        const r = S.slideHtml(id, { suppressHeading: isTitleSlide });
        if (isTitleSlide) {
          if (r.heading && (!title || /^H\s*\d+$/i.test(title))) title = r.heading; // menulabel gaat voor, tenzij het alleen 'H1' is
          return; // inhoud van de titeldia (meestal een opsomming van onderdelen) niet herhalen
        }
        if (!firstHeading && r.heading) firstHeading = r.heading;
        if (r.html) bodies.push(r.html);
      });
      const text = bodies.join('\n').replace(/<[^>]+>/g, ' ');
      if (!S.norm(text)) return; // leeg hoofdstuk (alleen menu/resultaten)
      if (/^(keuzemenu|menu|hoofdmenu)$/i.test(title) || (S.norm(text).length < 300 && /kies een hoofdstuk/i.test(text))) return;
      n++;
      // Menulabels als 'H3 Hulpmiddelen' of 'H1': het voorvoegsel staat niet in de cursus zelf.
      title = title.replace(/^H\s*\d+\s*[:.\-–]?\s*/i, '').trim();
      if (!title) title = firstHeading || ('Hoofdstuk ' + n);
      if (title === title.toUpperCase() && title.length > 3) title = title.charAt(0) + title.slice(1).toLowerCase();
      toc.push('<li>' + S.esc(title) + '</li>');
      sections.push('<section class="chapter"><div class="chapter-header"><p class="kicker">Hoofdstuk ' + n + '</p><h1>' + S.esc(title) + '</h1></div>' + bodies.join('\n') + '</section>');
      report.push({ i: n, title, slides: ch.ids.length, text: S.norm(text).length });
    });
    const title = st.title || 'Cursus';
    const cover = '<section class="cover"><p class="cover-kicker">E-module</p><h1>' + S.esc(title) + '</h1><div class="toc"><h2>Inhoud</h2><ol>' + toc.join('') + '</ol></div></section>';
    const pageCss = o.pageNumbers ? css : css.replace(/@page \{ @bottom-right[^\n]*\n/, '');
    const inner = cover + '\n' + sections.join('\n');
    const html = '<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>' + S.esc(title) + '</title><style>' + pageCss + '</style></head><body class="forms sl">' + inner + '</body></html>';
    return { html, inner, css: pageCss, title, report, chapters: n, errors: st.errors };
  };

  // Toont het printdocument in het bovenste document (voor Page.printToPDF / Cmd+P).
  S.show = function (inner, css) {
    Array.from(document.styleSheets).forEach((sh) => {
      const nd = sh.ownerNode;
      if (nd && nd.id === 'cpe-print-style') return;
      try { if (!sh.disabled) { sh.disabled = true; nd && nd.setAttribute('data-cpe-disabled', '1'); } } catch (e) { /* negeren */ }
    });
    Array.from(document.body.children).forEach((c) => { if (c.id !== 'cpe-print') { c.setAttribute('data-cpe-hidden', c.style.display || ''); c.style.display = 'none'; } });
    let stl = document.getElementById('cpe-print-style');
    if (!stl) { stl = document.createElement('style'); stl.id = 'cpe-print-style'; document.head.appendChild(stl); }
    stl.textContent = css + '\nhtml, body { height: auto !important; min-height: 0 !important; overflow: visible !important; margin: 0 !important; padding: 0 !important; background: #fff !important; }' +
      '\n#cpe-print { max-width: 760px; margin: 0 auto; }\n@media print { #cpe-print { max-width: none; } }';
    let root = document.getElementById('cpe-print');
    if (!root) { root = document.createElement('div'); root.id = 'cpe-print'; document.body.appendChild(root); }
    root.innerHTML = inner;
    window.scrollTo(0, 0);
  };

  S.restore = function () {
    const root = document.getElementById('cpe-print');
    if (root) root.remove();
    const stl = document.getElementById('cpe-print-style');
    if (stl) stl.remove();
    Array.from(document.querySelectorAll('[data-cpe-hidden]')).forEach((c) => { c.style.display = c.getAttribute('data-cpe-hidden'); c.removeAttribute('data-cpe-hidden'); });
    Array.from(document.querySelectorAll('[data-cpe-disabled]')).forEach((nd) => { try { nd.sheet.disabled = false; } catch (e) { /* negeren */ } nd.removeAttribute('data-cpe-disabled'); });
  };

  S.download = function (html, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([html], { type: /\.json$/.test(name) ? 'application/json' : 'text/html' }));
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  };

  // Ruwe cursusdata + afbeeldingen als één JSON-bestand: de invoer voor storyline-pdf.py.
  S.dump = async function (onProgress) {
    const st = await S.load((i, n, t) => onProgress && onProgress('laden', i, n, t));
    const urls = new Set();
    const walk = (o) => {
      if (!o || typeof o !== 'object') return;
      if (Array.isArray(o)) { o.forEach(walk); return; }
      if (o.imagelib || (o.data && (o.data.imagedata || o.data.html5data))) { const u = S.imageUrl(o); if (u) urls.add(u); }
      for (const k in o) if (k !== 'textLib') walk(o[k]);
    };
    Object.values(st.slides).forEach(walk);
    const images = {};
    let i = 0;
    for (const u of urls) {
      i++;
      if (onProgress) onProgress('afbeeldingen', i, urls.size, '');
      try {
        const r = await fetch(st.base + u, { credentials: 'include' });
        if (!r.ok) continue;
        if (!/^image\//.test(r.headers.get('content-type') || '')) continue;
        const b = await r.blob();
        images[u] = await new Promise((ok, err) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = err; fr.readAsDataURL(b); });
      } catch (e) { S.log('afbeelding mislukt', u, e); }
    }
    const out = { title: st.title, base: st.base, data: st.data, frame: st.frame, slides: st.slides, errs: st.errors, images };
    const name = st.title.replace(/[<>:"/\\|?*]/g, '').replace(/\s+/g, '-').slice(0, 100) + '.sl.json';
    return { json: JSON.stringify(out), name, slides: Object.keys(st.slides).length, images: Object.keys(images).length, errors: st.errors, title: st.title };
  };

  window.__cpeStoryline = S;
  return true;
}

// ---- Extensie (popup) ----

async function storylineEnsureHelpers(tabId) {
  const has = await riseExec(tabId, () => !!(window.__cpeStoryline && window.__cpeStoryline.build));
  if (!has) await riseExec(tabId, storylineInstallHelpers);
}

async function detectStorylineCourse(tabId) {
  try {
    await storylineEnsureHelpers(tabId);
    return await riseExec(tabId, () => window.__cpeStoryline.detect());
  } catch (e) {
    console.log('Storyline detection failed:', e);
    return null;
  }
}

async function runStorylineMode() {
  // Modus C: leest de cursusdata (dia's, vragen, afbeeldingen) en downloadt die als .sl.json.
  // De PDF wordt lokaal gemaakt met storyline-pdf.py (eigen opmaak, echte vinkvakjes).
  const status = document.getElementById('status');
  const startBtn = document.getElementById('startBtn');
  const stopBtn = document.getElementById('stopBtn');
  const progressContainer = document.getElementById('progressContainer');
  const progressBar = document.getElementById('progressBar');

  startBtn.disabled = true;
  startBtn.style.display = 'none';
  stopBtn.style.display = 'none';
  progressContainer.style.display = 'block';
  progressBar.style.width = '0%';
  const resetButtons = () => { startBtn.disabled = false; startBtn.style.display = 'block'; };

  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabId = tab.id;

  try {
    status.textContent = 'Cursus analyseren...';
    await storylineEnsureHelpers(tabId);
    const det = await riseExec(tabId, () => window.__cpeStoryline.detect());
    if (!det) { status.textContent = 'Geen Storyline-speler gevonden op dit tabblad.'; resetButtons(); return; }

    await riseExec(tabId, () => {
      window.__cpeSlProgress = { phase: '', i: 0, n: 0, title: '', done: false, error: null, result: null };
      window.__cpeStoryline.dump((phase, i, n, t) => { window.__cpeSlProgress = Object.assign(window.__cpeSlProgress, { phase, i, n, title: t }); })
        .then((r) => {
          window.__cpeStoryline.download(r.json, r.name);
          window.__cpeSlProgress.result = { name: r.name, slides: r.slides, images: r.images, errors: r.errors };
          window.__cpeSlProgress.done = true;
        })
        .catch((e) => { window.__cpeSlProgress.error = String(e && e.message || e); window.__cpeSlProgress.done = true; });
      return true;
    });

    let result = null;
    for (;;) {
      await new Promise((r) => setTimeout(r, 400));
      const p = await riseExec(tabId, () => window.__cpeSlProgress);
      if (!p) throw new Error('Voortgang verloren (pagina herladen?).');
      if (p.error) throw new Error(p.error);
      if (p.done) { result = p.result; break; }
      const label = p.phase === 'laden' ? 'Dia' : 'Afbeelding';
      status.textContent = `${label} ${p.i + (p.phase === 'laden' ? 1 : 0)} van ${p.n}${p.title ? ': ' + p.title : ''}...`;
      progressBar.style.width = `${(p.phase === 'afbeeldingen' ? 50 : 0) + ((p.i + 0.5) / Math.max(p.n, 1)) * 50}%`;
    }
    console.log('Storyline dump:', result);
    progressBar.style.width = '100%';
    status.textContent = `Klaar! ${result.name} gedownload (${result.slides} dia's, ${result.images} afbeeldingen). ` +
      `Maak nu de PDF: python3 storyline-pdf.py "${result.name}"`;
  } catch (e) {
    console.error(e);
    status.textContent = 'Fout: ' + (e.message || e);
  }
  resetButtons();
}
