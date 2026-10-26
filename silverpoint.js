// Modus D: Silverpoint-cursus (ThreeShips/Nemo-speler, oudere NIPV/IFV-e-modules met menu links en "1 van 33").
//
// Werkwijze: de speler laadt elke pagina in een eigen (same-origin) iframe en heeft de volledige bron van de
// cursus in het geheugen: window.__tsOfflineData = { 'project.xml': ..., 'Pages/<id>.xaml': ..., ... }.
// project.xml bevat de paginastructuur (menu), elke .xaml de inhoud van één pagina: rijke tekst, afbeeldingen,
// video's en vragen in IMS QTI 2.1 (met het juiste antwoord en de feedback). We downloaden die bron plus alle
// afbeeldingen als één .sp.json; de PDF wordt lokaal gemaakt met silverpoint-pdf.py (zelfde opmaak als Rise
// en Storyline). Er hoeft dus niet door de pagina's genavigeerd te worden.
//
// De helpers draaien in het spelertabblad (bovenste document) als window.__cpeSilverpoint.

function silverpointInstallHelpers() {
  const P = {};
  P.log = (...a) => { try { console.log('[cpe-sp]', ...a); } catch (e) { /* stil */ } };

  // Zoekt (door same-origin iframes heen) een venster met __tsOfflineData.
  P.find = function () {
    const visit = (w, depth) => {
      if (depth > 6) return null;
      try {
        if (w.__tsOfflineData && w.__tsOfflineData['project.xml']) return w;
      } catch (e) { return null; }
      let frames = [];
      try { frames = Array.from(w.document.querySelectorAll('iframe,frame')); } catch (e) { return null; }
      for (const f of frames) {
        let cw = null;
        try { cw = f.contentWindow; void cw.document; } catch (e) { continue; }
        const r = visit(cw, depth + 1);
        if (r) return r;
      }
      return null;
    };
    return visit(window, 0);
  };

  P.detect = function () {
    const w = P.find();
    if (!w) return null;
    const m = /<Project[^>]*\sTitle="([^"]*)"/.exec(w.__tsOfflineData['project.xml']);
    const pages = (w.__tsOfflineData['project.xml'].match(/Source="Pages\//g) || []).length;
    const div = document.createElement('div');
    div.innerHTML = m ? m[1] : '';
    return { title: div.textContent || 'Cursus', pages };
  };

  P.download = function (text, name) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  };

  // Bron + afbeeldingen als één JSON-bestand: de invoer voor silverpoint-pdf.py.
  P.dump = async function (onProgress) {
    const w = P.find();
    if (!w) throw new Error('Geen Silverpoint-speler gevonden.');
    const od = w.__tsOfflineData;
    const base = w.location.href.replace(/[^/]*$/, '');   // .../0/js/index.html -> .../0/js/
    const det = P.detect();
    // Alle verwijzingen naar bestanden in Resources/ (afbeeldingen; video's en bijlagen alleen als naam).
    const refs = new Set();
    const add = (u) => {
      // int://local/Resources/x.pdf en rsc://<id>/Resources/__LNK/x.pdf zijn bijlagen in het pakket
      u = (u || '').replace(/&amp;/g, '&').replace(/^int:\/\/local\//, '').replace(/^rsc:\/\/[^/]+\//, '');
      if (/^Resources\//.test(u)) refs.add(u);
    };
    for (const [k, v] of Object.entries(od)) {
      if (!/^(Pages|HiddenPages|MasterPages)\//.test(k)) continue;
      // Meerdere keren ontsleutelen: carrousels (ItemsXML) en vragen (QTIXML) zitten dubbel geëscaped in de XAML
      let txt = v;
      for (let n = 0; n < 3; n++) txt = txt.replace(/&quot;/g, '"').replace(/&apos;/g, "'").replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
      // Elke verwijzing naar een bestand in Resources/, waar die ook staat (Source, data, src, source, ItemsXML, QTI ...)
      (txt.match(/Resources\/[^"'<>\n]+?\.(?:png|jpe?g|gif|svg|bmp|webp|mp4|webm|m4v|mov|mp3|wav|pdf)/gi) || []).forEach(add);
      (txt.match(/(?:Link|NavigateUri)="((?:int|rsc):\/\/[^"]+)"/g) || []).forEach((m) => add(m.replace(/^[a-zA-Z]+="/, '').replace(/"$/, '')));
    }
    const images = {};
    const list = Array.from(refs).filter((u) => /\.(png|jpe?g|gif|svg|bmp|webp)$/i.test(u));
    let i = 0;
    for (const u of list) {
      i++;
      if (onProgress) onProgress('afbeeldingen', i, list.length, u.split('/').pop());
      // Silverpoint zet bestanden soms onder js/Resources en soms een niveau hoger
      const tries = [base + u, base.replace(/js\/$/, '') + u, base + encodeURI(u)];
      for (const url of tries) {
        try {
          const r = await fetch(url, { credentials: 'include' });
          if (!r.ok || !/^image\//.test(r.headers.get('content-type') || '')) continue;
          const b = await r.blob();
          images[u] = await new Promise((ok, err) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = err; fr.readAsDataURL(b); });
          break;
        } catch (e) { /* volgende proberen */ }
      }
      if (!images[u]) P.log('afbeelding niet gevonden', u);
    }
    // Bijlagen (PDF) als data meenemen: in de PDF worden ze achteraan toegevoegd en gelinkt
    const attachments = {};
    for (const u of Array.from(refs).filter((x) => /\.pdf$/i.test(x))) {
      for (const url of [base + u, base.replace(/js\/$/, '') + u, base + encodeURI(u)]) {
        try {
          const r = await fetch(url, { credentials: 'include' });
          if (!r.ok || !/pdf/.test(r.headers.get('content-type') || '')) continue;
          const b = await r.blob();
          attachments[u] = await new Promise((ok, err) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = err; fr.readAsDataURL(b); });
          break;
        } catch (e) { /* volgende proberen */ }
      }
      if (!attachments[u]) P.log('bijlage niet gevonden', u);
    }
    // Video's: één beeld (na ~15% van de duur) als miniatuur, zodat in de PDF duidelijk is dat er een video is
    const thumbs = {};
    const vids = Array.from(refs).filter((x) => /\.(mp4|webm|m4v|mov)$/i.test(x));
    for (const u of vids) {
      if (onProgress) onProgress('video', vids.indexOf(u) + 1, vids.length, u.split('/').pop());
      for (const url of [base + u, base.replace(/js\/$/, '') + u, base + encodeURI(u)]) {
        try {
          const shot = await new Promise((ok, bad) => {
            const v = document.createElement('video');
            v.muted = true; v.preload = 'auto'; v.crossOrigin = 'use-credentials'; v.src = url;
            const t = setTimeout(() => bad(new Error('timeout')), 20000);
            v.addEventListener('error', () => { clearTimeout(t); bad(new Error('video')); });
            v.addEventListener('loadedmetadata', () => { v.currentTime = Math.min(Math.max(1, (v.duration || 10) * 0.15), (v.duration || 10) - 0.1); });
            v.addEventListener('seeked', () => {
              try {
                const c = document.createElement('canvas');
                const w = Math.min(960, v.videoWidth || 640);
                c.width = w; c.height = Math.round(w * (v.videoHeight || 360) / (v.videoWidth || 640));
                c.getContext('2d').drawImage(v, 0, 0, c.width, c.height);
                clearTimeout(t); ok(c.toDataURL('image/jpeg', 0.82));
              } catch (e) { clearTimeout(t); bad(e); }
            }, { once: true });
          });
          thumbs[u] = shot;
          break;
        } catch (e) { /* volgende proberen */ }
      }
      if (!thumbs[u]) P.log('geen miniatuur voor', u);
    }
    const out = { kind: 'silverpoint', attachments, thumbs, title: det.title, base, files: od, images, media: Array.from(refs).filter((u) => !images[u] && !attachments[u]) };
    const name = det.title.replace(/[<>:"/\\|?*]/g, '').replace(/\s+/g, '-').slice(0, 100) + '.sp.json';
    return { json: JSON.stringify(out), name, pages: det.pages, images: Object.keys(images).length, missing: list.length - Object.keys(images).length, title: det.title };
  };

  window.__cpeSilverpoint = P;
  return true;
}

// ---- Extensie (popup) ----

async function silverpointEnsureHelpers(tabId) {
  const has = await riseExec(tabId, () => !!(window.__cpeSilverpoint && window.__cpeSilverpoint.dump));
  if (!has) await riseExec(tabId, silverpointInstallHelpers);
}

async function detectSilverpointCourse(tabId) {
  try {
    await silverpointEnsureHelpers(tabId);
    return await riseExec(tabId, () => window.__cpeSilverpoint.detect());
  } catch (e) {
    console.log('Silverpoint detection failed:', e);
    return null;
  }
}

async function runSilverpointMode() {
  // Modus D: downloadt de cursusbron (.sp.json); de PDF wordt lokaal gemaakt met silverpoint-pdf.py.
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
    await silverpointEnsureHelpers(tabId);
    const det = await riseExec(tabId, () => window.__cpeSilverpoint.detect());
    if (!det) { status.textContent = 'Geen Silverpoint-speler gevonden op dit tabblad.'; resetButtons(); return; }
    await riseExec(tabId, () => {
      window.__cpeSpProgress = { phase: '', i: 0, n: 0, title: '', done: false, error: null, result: null };
      window.__cpeSilverpoint.dump((phase, i, n, t) => { window.__cpeSpProgress = Object.assign(window.__cpeSpProgress, { phase, i, n, title: t }); })
        .then((r) => {
          window.__cpeSilverpoint.download(r.json, r.name);
          window.__cpeSpProgress.result = { name: r.name, pages: r.pages, images: r.images, missing: r.missing };
          window.__cpeSpProgress.done = true;
        })
        .catch((e) => { window.__cpeSpProgress.error = String(e && e.message || e); window.__cpeSpProgress.done = true; });
      return true;
    });
    let result = null;
    for (;;) {
      await new Promise((r) => setTimeout(r, 400));
      const p = await riseExec(tabId, () => window.__cpeSpProgress);
      if (!p) throw new Error('Voortgang verloren (pagina herladen?).');
      if (p.error) throw new Error(p.error);
      if (p.done) { result = p.result; break; }
      status.textContent = `Afbeelding ${p.i} van ${p.n}...`;
      progressBar.style.width = `${(p.i / Math.max(p.n, 1)) * 100}%`;
    }
    progressBar.style.width = '100%';
    status.textContent = `Klaar! ${result.name} gedownload (${result.pages} pagina's, ${result.images} afbeeldingen` +
      `${result.missing ? ', ' + result.missing + ' niet gevonden' : ''}). Maak nu de PDF: python3 silverpoint-pdf.py "${result.name}"`;
  } catch (e) {
    console.error(e);
    status.textContent = 'Fout: ' + (e.message || e);
  }
  resetButtons();
}
