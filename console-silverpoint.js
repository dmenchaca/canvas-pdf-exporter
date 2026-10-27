// Canvas PDF Exporter — console-variant van Silverpoint-modus (gegenereerd door build-console.js)
// 1. Open de Silverpoint-cursus (spelertabblad), open DevTools → Console, kies "top" als frame.
// 2. Plak dit script en druk op Enter. Wacht tot "[cpe-sp] klaar" verschijnt.
// 3. Er wordt een <cursus>.sp.json gedownload (cursusbron + afbeeldingen).
// 4. Maak de PDF lokaal:  python3 silverpoint-pdf.py <cursus>.sp.json
(async () => {
  (function () {
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
      const txt = v.replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>');
      (txt.match(/(?:Source|data)="(Resources\/[^"]+)"/g) || []).forEach((m) => add(m.replace(/^[a-zA-Z]+="/, '').replace(/"$/, '')));
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
})();

  try {
    const r = await window.__cpeSilverpoint.dump((phase, i, n, t) => { if (i === 1 || i % 10 === 0) console.log('[cpe-sp] ' + phase + ' ' + i + '/' + n); });
    window.__cpeSilverpoint.download(r.json, r.name);
    console.log('[cpe-sp] klaar: ' + r.pages + " pagina's, " + r.images + ' afbeeldingen; ' + r.name + ' gedownload. Nu: python3 silverpoint-pdf.py "' + r.name + '"');
  } catch (e) {
    console.error('[cpe-sp] fout', e);
  }
})();
