#!/usr/bin/env node
// Genereert console-export.js uit rise.js: dezelfde helpers en CSS, plus een runner
// die het boek opbouwt en de printdialoog opent (Cmd+P → Bewaar als PDF).
// Gebruik: node build-console.js  →  plak de inhoud van console-export.js in de
// DevTools-console van het spelertabblad (bovenste frame).
const fs = require('fs');
const path = require('path');

// Eén bron voor de opmaak: de CSS in book-to-print.py wordt in rise.js overgenomen.
const pyPath = path.join(__dirname, 'book-to-print.py');
let src = fs.readFileSync(path.join(__dirname, 'rise.js'), 'utf8');
if (fs.existsSync(pyPath)) {
  const py = fs.readFileSync(pyPath, 'utf8');
  const m = py.match(/CSS = """([\s\S]*?)"""/);
  if (m) {
    let cssPy = m[1]
      .replace('body.forms .quiz ul.options li::before { border-color: transparent; background: transparent; }\n', '')
      .replace('.quiz ul.options li.correct p { display: inline; }',
        '.quiz ul.options li.correct p { display: inline; }\n.quiz ul.options li.correct::before { content: "✓"; color: #1d6b2f; font-weight: 700; font-size: .8em; line-height: 1.25; text-align: center; border-color: #1d6b2f; }')
      .replace(/`/g, '\\`');
    const updated = src.replace(/const RISE_PRINT_CSS = `[\s\S]*?`;\n/, 'const RISE_PRINT_CSS = `' + cssPy + '`;\n');
    if (updated !== src) { fs.writeFileSync(path.join(__dirname, 'rise.js'), updated); src = updated; console.log('rise.js: RISE_PRINT_CSS bijgewerkt uit book-to-print.py'); }
  }
}
const helpers = src.match(/function riseInstallHelpers\(\) \{[\s\S]*?\n\}\n/);
const css = src.match(/const RISE_PRINT_CSS = `([\s\S]*?)`;/);
if (!helpers || !css) throw new Error('rise.js: helpers of CSS niet gevonden');

const out = `// Canvas PDF Exporter — console-variant van Rise-modus (gegenereerd door build-console.js)
// 1. Open de Rise-cursus (spelertabblad), open DevTools → Console, kies "top" als frame.
// 2. Plak dit script en druk op Enter. Wacht tot "[cpe] klaar" verschijnt.
// 3. Er wordt automatisch een .html-bestand gedownload (boek met ingebedde afbeeldingen).
//    Optioneel: Cmd+P → "Bewaar als PDF", "Achtergrondafbeeldingen" aan.
// 4. Herstellen zonder herladen: window.__cpeRise.restore()
(async () => {
  ${helpers[0].replace(/^function riseInstallHelpers\(\)/, '(function ()').replace(/\n\}\n$/, '\n})();\n')}
  const CSS = ${JSON.stringify(css[1])};
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
`;
fs.writeFileSync(path.join(__dirname, 'console-export.js'), out);
console.log('console-export.js geschreven (' + out.length + ' bytes)');

// Modus C (Storyline): zelfde CSS, eigen helpers uit storyline.js.
const sl = fs.readFileSync(path.join(__dirname, 'storyline.js'), 'utf8');
const slHelpers = sl.match(/function storylineInstallHelpers\(\) \{[\s\S]*?\n\}\n/);
if (!slHelpers) throw new Error('storyline.js: helpers niet gevonden');
const outSl = `// Canvas PDF Exporter — console-variant van Storyline-modus (gegenereerd door build-console.js)
// 1. Open de Storyline-cursus (spelertabblad), open DevTools → Console, kies "top" als frame.
// 2. Plak dit script en druk op Enter. Wacht tot "[cpe-sl] klaar" verschijnt.
// 3. Er wordt een <cursus>.sl.json gedownload (cursusdata + afbeeldingen).
// 4. Maak de PDF lokaal:  python3 storyline-pdf.py <cursus>.sl.json
(async () => {
  ${slHelpers[0].replace(/^function storylineInstallHelpers\(\)/, '(function ()').replace(/\n\}\n$/, '\n})();\n')}
  console.time('[cpe-sl] totaal');
  try {
    const r = await window.__cpeStoryline.dump((phase, i, n, t) => { if (i === 1 || i % 10 === 0) console.log('[cpe-sl] ' + phase + ' ' + i + '/' + n + (t ? ': ' + t : '')); });
    if (r.errors && r.errors.length) console.warn('[cpe-sl] niet gelezen:', r.errors);
    window.__cpeStoryline.download(r.json, r.name);
    console.log('[cpe-sl] klaar: ' + r.slides + " dia's, " + r.images + ' afbeeldingen; ' + r.name + ' gedownload. Nu: python3 storyline-pdf.py "' + r.name + '"');
  } catch (e) {
    console.error('[cpe-sl] fout', e);
  }
  console.timeEnd('[cpe-sl] totaal');
})();
`;
fs.writeFileSync(path.join(__dirname, 'console-storyline.js'), outSl);
console.log('console-storyline.js geschreven (' + outSl.length + ' bytes)');
