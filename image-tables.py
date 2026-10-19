#!/usr/bin/env python3
"""Tabellen die in Storyline als afbeelding zijn geplaatst omzetten naar echte (HTML-)tabellen.

    python3 image-tables.py <cursus>.sl.json            # maakt <cursus>.tables.json + <cursus>.tables-review.html
    python3 storyline-pdf.py <cursus>.sl.json           # gebruikt <cursus>.tables.json automatisch

Werkwijze per afbeelding:
1. Rasterlijnen zoeken (dunne lijnen over vrijwel de hele tabelbreedte/-hoogte); samengevoegde cellen
   (colspan/rowspan) waar een tussenlijn ontbreekt.
2. Elke cel apart herkennen met Apple Vision (macOS, tools/vision-ocr.swift), vergroot en met een rand
   in de celkleur. Lettertjes die Vision mist (losse 'C') via Tesseract.
3. Celkleur = meest voorkomende lichte kleur in de cel; tekstkleur = mediaan van de inkt; vet via de
   streekdikte (of een blauwe kopcel).
4. Geen tabel als het vlak grotendeels donker is (foto) of als er tekeningen in een cel staan.
5. Controle: elke cel wordt ook met Tesseract gelezen; verschillen komen in de review-pagina zodat een
   mens ze kan nalopen. Correcties gaan in <cursus>.tables-fixes.json en blijven bij een nieuwe run staan:
       {"<afbeelding-url>": {"replace": [["fout", "goed"], ...]}}      of
       {"<afbeelding-url>": {"html": "<table ...>...</table>"}}         (volledig handmatig)
"""
import argparse
import base64
import difflib
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.expanduser('~/.cache/canvas-pdf-exporter')
TMP = tempfile.mkdtemp(prefix='cpe-tables-')


# ----------------------------------------------------------------------------- OCR

def vision_bin():
    src = os.path.join(HERE, 'tools', 'vision-ocr.swift')
    out = os.path.join(CACHE, 'vision-ocr')
    if not os.path.exists(out) or os.path.getmtime(out) < os.path.getmtime(src):
        os.makedirs(CACHE, exist_ok=True)
        r = subprocess.run(['swiftc', '-O', src, '-o', out], capture_output=True, text=True)
        if r.returncode:
            raise SystemExit('vision-ocr compileren mislukt (macOS met Xcode-tools nodig):\n' + r.stderr)
    return out


def ocr(path):
    r = subprocess.run([VISION, path], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)['lines']
    except Exception:
        return []


def tesseract(img, psm='6'):
    if not shutil.which('tesseract'):
        return ''
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    r = subprocess.run(['tesseract', '-', 'stdout', '-l', 'nld+eng', '--psm', psm], input=buf.getvalue(), capture_output=True)
    return r.stdout.decode('utf-8', 'ignore')


HOMO = str.maketrans({'А': 'A', 'В': 'B', 'С': 'C', 'Е': 'E', 'Н': 'H', 'К': 'K', 'М': 'M', 'О': 'O', 'Р': 'P', 'Т': 'T',
                      'Х': 'X', 'а': 'a', 'с': 'c', 'е': 'e', 'о': 'o', 'р': 'p', 'х': 'x', 'у': 'y', 'і': 'i', 'ı': 'i',
                      'Ι': 'I', 'Ο': 'O', 'Τ': 'T', 'Α': 'A', 'Β': 'B', 'Ε': 'E', 'Κ': 'K', 'Μ': 'M', 'Ν': 'N', 'Ρ': 'P',
                      'Χ': 'X', 'ο': 'o'})


def clean(t):
    """OCR geeft soms Cyrillische/Griekse lookalikes (Т i.p.v. T) of een puntloze ı; apostrof in een woord is in de
    bron (Word) vrijwel altijd de typografische ’ (risico’s), die Vision als ' teruggeeft."""
    return re.sub(r"(?<=\w)'(?=\w)", '’', t.translate(HOMO))


# ----------------------------------------------------------------------------- raster

def runs(mask):
    out, s = [], None
    for i, v in enumerate(mask):
        if v and s is None:
            s = i
        if not v and s is not None:
            out.append((s, i - 1))
            s = None
    if s is not None:
        out.append((s, len(mask) - 1))
    return out


def lines(frac, thr, maxw):
    return [(s + e) // 2 for s, e in runs(frac > thr) if e - s + 1 <= maxw]


def dedupe(ls, gap=10):
    out = []
    for v in ls:
        if out and v - out[-1] <= gap:
            out[-1] = (out[-1] + v) // 2
        else:
            out.append(v)
    return out


def hexc(rgb):
    return '#%02x%02x%02x' % tuple(int(x) for x in rgb)


def grid(a):
    H, W, _ = a.shape
    edge = a.mean(2) < 200          # randlijnen zijn donkerder dan de lichte celkleuren
    maxw = max(4, int(min(H, W) * 0.012))
    hl = lines(edge.mean(1), 0.80, maxw)
    if len(hl) < 2:
        return [], [], edge
    vl = lines(edge[hl[0]:hl[-1] + 1].mean(0), 0.80, maxw)
    if len(vl) >= 2:
        hl = lines(edge[:, vl[0]:vl[-1] + 1].mean(1), 0.80, maxw)
    hl, vl = dedupe(hl), dedupe(vl)
    if len(vl) >= 2 and len(hl) >= 2:
        # lijnen die alleen in één kolom lopen (koppen met twee niveaus)
        extra = []
        for j in range(len(vl) - 1):
            seg = edge[:, vl[j] + 3:vl[j + 1] - 2]
            if seg.shape[1] > 10:
                extra += lines(seg.mean(1), 0.85, maxw)
        hl = dedupe(sorted(set(hl + [y for y in extra if hl[0] - 3 <= y <= hl[-1] + 3])))
    return hl, vl, edge


def convert(img):
    im = img.convert('RGB')
    a = np.asarray(im).astype(int)
    H, W, _ = a.shape
    hl, vl, edge = grid(a)
    if len(hl) < 3 or len(vl) < 2:
        return None
    lum = a.mean(2)
    if (lum[hl[0]:hl[-1], vl[0]:vl[-1]] > 200).mean() < 0.6:
        return None  # foto
    full_path = os.path.join(TMP, 'full.png')
    im.save(full_path)
    full = ocr(full_path)

    def hline(y, x0, x1):
        return edge[max(0, y - 2):y + 3, x0 + 3:x1 - 2].any(0).mean() > 0.8

    def read_cell(x0, y0, x1, y1):
        reg = a[y0 + 3:y1 - 2, x0 + 3:x1 - 2].reshape(-1, 3)
        light = reg[reg.mean(1) > 200]
        if len(light) == 0:
            light = reg
        q = (light // 8) * 8
        vals, counts = np.unique(q, axis=0, return_counts=True)
        bg = vals[counts.argmax()] + 4
        raw = im.crop((x0 + 3, y0 + 3, x1 - 2, y1 - 2))
        rw, rh = raw.size
        P = 12
        padded = Image.new('RGB', (rw + 2 * P, rh + 2 * P), tuple(int(v) for v in bg))
        padded.paste(raw, (P, P))
        crop = padded.resize(((rw + 2 * P) * 2, (rh + 2 * P) * 2), Image.LANCZOS)
        tmp = os.path.join(TMP, 'cell.png')
        crop.save(tmp)
        ca = np.asarray(crop).astype(int)
        info = []
        for l in ocr(tmp):
            lx0, ly0 = int(max(l['x'], 0)), int(max(l['y'], 0))
            lx1, ly1 = int(min(l['x'] + l['w'], ca.shape[1])), int(min(l['y'] + l['h'], ca.shape[0]))
            box = ca[ly0:ly1, lx0:lx1].reshape(-1, 3)
            if len(box) == 0:
                continue
            bl = box.mean(1)
            thr = bl.mean() - 25
            ink = box[bl < thr] if (bl < thr).any() else box[bl <= np.percentile(bl, 10)]
            sub = ca[ly0:ly1, lx0:lx1].mean(2) < 140
            dark = int(sub.sum())
            nruns = int((sub[:, 1:] & ~sub[:, :-1]).sum() + sub[:, 0].sum()) if dark else 1
            info.append({'t': clean(l['t']), 'x': x0 + 3 - P + l['x'] / 2, 'y': y0 + 3 - P + l['y'] / 2,
                         'w': l['w'] / 2, 'h': l['h'] / 2, 'stroke': dark / max(nruns, 1),
                         'col': hexc(np.median(ink, axis=0) if len(ink) else (0, 0, 0))})
        if not info:
            for l in full:  # kleine cel: OCR van de hele afbeelding
                if x0 - 3 <= l['x'] + l['w'] / 2 <= x1 + 3 and y0 - 3 <= l['y'] + l['h'] / 2 <= y1 + 3:
                    info.append({'t': clean(l['t']), 'x': l['x'], 'y': l['y'], 'w': l['w'], 'h': l['h'], 'stroke': 0, 'col': '#1f3864'})
        if not info and (x1 - x0) < 0.15 * W and (lum[y0 + 3:y1 - 2, x0 + 3:x1 - 2] < 140).sum() > 30:
            t = tesseract(raw.resize((rw * 4, rh * 4)), psm='7').strip()   # losse letter ('C')
            if re.fullmatch(r'[A-Z0-9][.)]?', t):
                info.append({'t': t, 'x': x0 + 3, 'y': y0 + 3, 'w': 10, 'h': 10, 'stroke': 0, 'col': '#1f3864'})
        info.sort(key=lambda l: l['y'])
        groups = []
        for l in info:  # regels op dezelfde hoogte samen, dan links naar rechts
            if groups and abs(l['y'] - groups[-1][0]['y']) < 0.5 * l['h']:
                groups[-1].append(l)
            else:
                groups.append([l])
        return bg, [l for g in groups for l in sorted(g, key=lambda l: l['x'])]

    rows = []
    for r in range(len(hl) - 1):
        y0, y1 = hl[r], hl[r + 1]
        inner = [x for x in vl[1:-1] if edge[y0 + 3:y1 - 2, max(0, x - 2):x + 3].any(1).mean() > 0.7]
        bounds = [vl[0]] + inner + [vl[-1]]
        cells = []
        for c in range(len(bounds) - 1):
            x0, x1 = bounds[c], bounds[c + 1]
            bg, info = read_cell(x0, y0, x1, y1)
            cells.append({'x0': x0, 'x1': x1, 'y0': y0, 'span': sum(1 for v in vl[1:] if x0 < v <= x1),
                          'rowspan': 1, 'bg': hexc(bg), 'lines': info})
        rows.append({'y0': y0, 'y1': y1, 'cells': cells})
    # tekeningen in een cel -> geen tabel
    for r in rows:
        for cl in r['cells']:
            cy0, cy1, cx0, cx1 = r['y0'] + 4, r['y1'] - 3, cl['x0'] + 4, cl['x1'] - 3
            if cy1 - cy0 < 8 or cx1 - cx0 < 8:
                continue
            dark = lum[cy0:cy1, cx0:cx1] < 140
            for l in cl['lines']:
                dark[int(max(l['y'] - cy0 - 2, 0)):int(l['y'] + l['h'] - cy0 + 3),
                     int(max(l['x'] - cx0 - 2, 0)):int(l['x'] + l['w'] - cx0 + 3)] = False
            if dark.mean() > 0.02:
                return None
    # rowspan: geen lijn tussen deze cel en de cel erboven
    for k in range(1, len(rows)):
        keep = []
        for cl in rows[k]['cells']:
            above = None
            for kk in range(k - 1, -1, -1):
                above = next((u for u in rows[kk]['cells'] if abs(u['x0'] - cl['x0']) < 6 and abs(u['x1'] - cl['x1']) < 6
                              and not u.get('_gone')), None)
                if above:
                    break
            if above and not hline(rows[k]['y0'], cl['x0'], cl['x1']):
                above['rowspan'] += 1
                cl['_gone'] = True
                above['lines'] = read_cell(above['x0'], above['y0'], above['x1'], rows[k]['y1'])[1]
            else:
                keep.append(cl)
        rows[k]['cells'] = keep
    strokes = [l['stroke'] for r in rows for c in r['cells'] for l in c['lines'] if l['stroke']]
    med = float(np.median(strokes)) if strokes else 0
    for r in rows:
        for c in r['cells']:
            c.pop('_gone', None)
            rr, gg, bb = [int(c['bg'][i:i + 2], 16) for i in (1, 3, 5)]
            blue = bb > rr + 15 and bb > 235 and rr < 235       # lichtblauwe kopcel
            for l in c['lines']:
                l['bold'] = blue or (len(l['t']) >= 3 and med and l['stroke'] > med * 1.3)
    return {'w': W, 'h': H, 'colw': [vl[i + 1] - vl[i] for i in range(len(vl) - 1)], 'rows': rows,
            'border': hexc(np.median(a[edge].reshape(-1, 3), axis=0)) if edge.any() else '#999999'}


BUL = re.compile(r'^\s*([•·▪●◦\-–>]|[0-9]+[.)])\s*')


def cell_paras(c):
    """OCR-regels -> alinea's; afgebroken regels samenvoegen, nieuwe alinea na een afgesloten zin of bij een opsommingsteken."""
    paras, prev = [], None
    for l in c['lines']:
        t = re.sub(r'^([•·▪●])\s*', r'\1 ', l['t'])
        if prev is None or BUL.match(t):
            newp = True
        else:
            pt = paras[-1]['t']
            newp = (bool(re.search(r'[.:!?)]\s*$', pt)) and t[:1].isupper() and not BUL.match(pt)) or \
                   (bool(re.search(r'[.:!?]\s*$', pt)) and bool(BUL.match(pt)) and l['x'] < prev['x'] - 8)
        if newp:
            paras.append({'t': t, 'bold': l['bold'], 'col': l['col']})
        else:
            paras[-1]['t'] += ' ' + t
        prev = l
    return paras


def to_html(spec):
    total = sum(spec['colw'])
    cols = ''.join('<col style="width:%.1f%%">' % (w * 100 / total) for w in spec['colw'])
    out = ['<table class="imgtable" style="--bd:%s">%s' % (spec['border'], cols)]
    for r in spec['rows']:
        out.append('<tr>')
        for c in r['cells']:
            inner = ''.join('<p style="color:%s">%s</p>' % (p['col'], ('<b>%s</b>' if p['bold'] else '%s') % html.escape(p['t']))
                            for p in cell_paras(c))
            out.append('<td%s%s style="background:%s">%s</td>' % (
                ' colspan="%d"' % c['span'] if c['span'] > 1 else '',
                ' rowspan="%d"' % c['rowspan'] if c.get('rowspan', 1) > 1 else '', c['bg'], inner))
        out.append('</tr>')
    out.append('</table>')
    return ''.join(out)


def crosscheck(img, spec):
    """Elke cel ook met Tesseract lezen; woordverschillen teruggeven voor menselijke controle."""
    if not shutil.which('tesseract'):
        return []
    im = img.convert('RGB')
    out = []
    n = lambda t: re.sub(r'\s+', ' ', t).strip()
    for ri, r in enumerate(spec['rows']):
        for c in r['cells']:
            y1 = spec['rows'][min(len(spec['rows']) - 1, ri + c.get('rowspan', 1) - 1)]['y1']
            crop = im.crop((c['x0'] + 3, r['y0'] + 3, c['x1'] - 2, y1 - 2))
            if crop.size[0] < 8 or crop.size[1] < 8:
                continue
            a = n(' '.join(p['t'] for p in cell_paras(c)))
            b = n(tesseract(crop.resize((crop.size[0] * 3, crop.size[1] * 3), Image.LANCZOS)).replace('\n', ' '))
            aw, bw = a.split(), b.split()
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, aw, bw).get_opcodes():
                if op != 'equal' and (aw[i1:i2] or any(re.search(r'\w', w) for w in bw[j1:j2])):
                    out.append([' '.join(aw[i1:i2]), ' '.join(bw[j1:j2])])
    return out


# ----------------------------------------------------------------------------- cursus

def main():
    global VISION
    ap = argparse.ArgumentParser()
    ap.add_argument('dump')
    ap.add_argument('--min-lines', type=int, default=6, help='minimaal aantal tekstregels in de afbeelding')
    a = ap.parse_args()
    VISION = vision_bin()
    sys.argv = [sys.argv[0]]
    import importlib.util
    spec = importlib.util.spec_from_file_location('slpdf', os.path.join(HERE, 'storyline-pdf.py'))
    sl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sl)
    d = json.load(open(a.dump))
    course = sl.Course(d)
    base = a.dump[:-8] if a.dump.endswith('.sl.json') else a.dump
    fixes_path = base + '.tables-fixes.json'
    fixes = json.load(open(fixes_path)) if os.path.exists(fixes_path) else {}
    out, seen = {}, set()
    for sid in course.order:
        for L in (course.slides.get(sid) or {}).get('slideLayers') or []:
            for it in course.flatten(L):
                url = course.image_url(it['ob'])
                data = course.images.get(url) if url else None
                if not data or url in seen or not data.startswith('data:image/') or 'svg' in data[:30]:
                    continue
                seen.add(url)
                img = Image.open(io.BytesIO(base64.b64decode(data.split(',', 1)[1])))
                p = os.path.join(TMP, 'img.png')
                img.convert('RGB').save(p)
                if len(ocr(p)) < a.min_lines:
                    continue
                t = convert(img)
                if not t:
                    continue
                h = to_html(t)
                fx = fixes.get(url) or {}
                for old, new in fx.get('replace', []):
                    h = h.replace(html.escape(old), html.escape(new))
                if 'html' in fx:
                    h = fx['html']  # leeg = deze afbeelding niet tonen (bv. vergrote kopie)
                out[url] = {'html': h, 'sha': hashlib.sha1(data.encode()).hexdigest()[:12],
                            'checks': [] if 'html' in fx else crosscheck(img, t), 'fixed': bool(fx)}
                print('tabel:', url, '(%d controlepunten)' % len(out[url]['checks']))
    json.dump(out, open(base + '.tables.json', 'w'), ensure_ascii=False, indent=1)
    # review-pagina: origineel naast resultaat, met de Tesseract-verschillen
    css = ('body{font-family:Helvetica Neue,Arial;margin:20px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:20px;'
           'border-bottom:1px solid #ccc;padding:12px 0;break-inside:avoid}img{width:100%}table.imgtable{border-collapse:collapse;'
           'width:100%;font-size:8.5pt;line-height:1.3}table.imgtable td{border:1px solid var(--bd);padding:3px 6px;vertical-align:top}'
           'table.imgtable p{margin:0 0 .25em}.chk{font-size:8pt;color:#a33}')
    body = []
    for url, v in out.items():
        chk = ''.join('<div class="chk">OCR: <b>%s</b> &nbsp;|&nbsp; Tesseract: %s</div>' % (html.escape(x), html.escape(y)) for x, y in v['checks'])
        body.append('<h4>%s%s</h4><div class="pair"><img src="%s"><div>%s%s</div></div>' % (
            html.escape(url), ' (gecorrigeerd)' if v['fixed'] else '', course.images[url], v['html'], chk))
    open(base + '.tables-review.html', 'w').write('<!doctype html><meta charset="utf-8"><style>%s</style>%s' % (css, ''.join(body)))
    print('%d tabellen -> %s.tables.json (controleer %s.tables-review.html)' % (len(out), base, base))


VISION = None
if __name__ == '__main__':
    main()
