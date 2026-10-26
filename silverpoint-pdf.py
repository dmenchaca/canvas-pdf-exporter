#!/usr/bin/env python3
"""Modus D: Silverpoint-cursus (.sp.json van de extensie) -> PDF in dezelfde opmaak als Rise en Storyline.

    python3 silverpoint-pdf.py "<cursus>.sp.json" [--pdf uit.pdf] [--model model.json]

Bron: project.xml (paginastructuur = menu) en per pagina een .xaml met
  - TSRichTextBox   rijke tekst (C1Paragraph / C1List / C1Run / C1Hyperlink)
  - TSImage / TSHotspotImage   afbeelding (Source="Resources/...")
  - MediaPlayer     video (alleen als vermelding)
  - Question        vraag in IMS QTI 2.1 (juiste antwoord + feedback)
Verborgen pagina's (HiddenPages, bv. de disclaimer) waar een link naar verwijst, komen als kader na die alinea.
De tekst wordt letterlijk overgenomen; alleen de opmaak is eigen.
"""
import argparse
import base64
import html
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
_B2P = None


def b2p():
    global _B2P
    if _B2P is None:
        spec = importlib.util.spec_from_file_location('book_to_print', os.path.join(HERE, 'book-to-print.py'))
        _B2P = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_B2P)
    return _B2P


def esc(t):
    return html.escape(t or '', quote=True)


def norm(t):
    return re.sub(r'\s+', ' ', t or '').strip()


def local(tag):
    return tag.split('}', 1)[-1] if '}' in tag else tag.split(':')[-1]


def attr(el, name, default=None):
    for k, v in el.attrib.items():
        if local(k) == name or local(k).split('.')[-1] == name:
            return v
    return default


def parse_xaml(src):
    # XAML gebruikt prefixen (text:, media:, document:) die niet altijd gedeclareerd zijn: weghalen.
    s = re.sub(r'<\?xml[^>]*\?>', '', src)
    s = re.sub(r'\sxmlns(:\w+)?="[^"]*"', '', s)
    s = re.sub(r'<(/?)[\w]+:', r'<\1', s)
    s = re.sub(r'\s[\w]+:([\w.]+)=', r' \1=', s)
    return ET.fromstring(s)


# ----------------------------------------------------------------------------- rijke tekst

def runs_html(el, link=None):
    """C1Run/C1Span/C1Hyperlink -> HTML (vet, cursief, onderstreept, links)."""
    out = []
    t = local(el.tag)
    if t == 'C1Run':
        txt = attr(el, 'Text', '') or ''
        h = esc(txt).replace('\n', '<br/>')
        if not txt.strip():
            return h if '\n' not in txt else ''
        if (attr(el, 'FontWeight', '') or '').lower() == 'bold':
            h = '<strong>' + h + '</strong>'
        if (attr(el, 'FontStyle', '') or '').lower() == 'italic':
            h = '<em>' + h + '</em>'
        return h
    if t == 'C1Hyperlink':
        uri = attr(el, 'NavigateUri', '') or ''
        inner = ''.join(runs_html(c) for c in children_of(el))
        if uri.startswith('page://'):
            return '<span class="sp-pagelink" data-page="%s">%s</span>' % (esc(uri[7:].split('/')[0]), inner)
        if re.match(r'^(https?|mailto):', uri):
            return '<a href="%s">%s</a>' % (esc(uri), inner)
        att = attachment_key(uri)
        if att and ATT.data.get(att):
            return '<a href="#%s">%s</a>' % (ATT.ref(att), inner)
        return inner
    return ''.join(runs_html(c) for c in children_of(el))


def attachment_key(uri):
    """int://local/Resources/x.pdf of rsc://<id>/Resources/__LNK/x.pdf -> 'Resources/...'."""
    u = html.unescape(uri or '')
    u = re.sub(r'^int://local/', '', u)
    u = re.sub(r'^rsc://[^/]+/', '', u)
    return re.sub(r'\s+', ' ', u).strip() if u.startswith('Resources/') else None


class Attachments:
    """Bijlagen uit het pakket: achteraan in de PDF, links in de tekst springen ernaartoe."""
    def __init__(self):
        self.data, self.order = {}, []

    def ref(self, key):
        if key not in self.order:
            self.order.append(key)
        return 'bijlage-%d' % (self.order.index(key) + 1)

    def name(self, key):
        n = os.path.splitext(key.split('/')[-1])[0]
        return re.sub(r'^e-module .*? - ', '', n).strip(' .-')


ATT = Attachments()
POPUPS = {}  # verborgen pagina's (id -> titel/html), gevuld in build()
YT_THUMBS = {}  # YouTube-id -> miniatuur
THUMBS = {}  # video-bestand -> miniatuur (data-URL) uit de dump


def children_of(el):
    """Kinderen binnen <X.Children> (C1TextElement.Children) of direct."""
    out = []
    for c in el:
        if local(c.tag).endswith('.Children'):
            out.extend(list(c))
        elif '.' not in local(c.tag):
            out.append(c)
    return out


def doc_blocks(doc):
    """C1Document -> lijst ('p', html) / ('ul'|'ol', [html, ...])."""
    blocks = []
    for el in children_of(doc):
        t = local(el.tag)
        if t == 'C1Paragraph':
            h = ''.join(runs_html(c) for c in children_of(el)).strip()
            h = re.sub(r'^(<br/>)+|(<br/>)+$', '', h)
            if norm(re.sub(r'<[^>]+>', '', h)):
                blocks.append(('p', h))
        elif t == 'C1List':
            items = []
            for li in children_of(el):
                h = ''
                for c in children_of(li):
                    if local(c.tag) == 'C1Paragraph':
                        h += ''.join(runs_html(x) for x in children_of(c))
                    else:
                        h += runs_html(c)
                h = re.sub(r'^(<br/>|\s)+|(<br/>|\s)+$', '', h.strip())
                if norm(re.sub(r'<[^>]+>', '', h)):
                    items.append(h)
            if items:
                blocks.append(('ol' if (attr(el, 'MarkerStyle', '') or '') in ('Decimal', 'LowerLatin', 'UpperLatin') else 'ul', items))
        else:
            h = runs_html(el).strip()
            if norm(re.sub(r'<[^>]+>', '', h)):
                blocks.append(('p', h))
    return blocks


def blocks_html(blocks):
    out = []
    for kind, v in blocks:
        if kind == 'p':
            out.append('<p>%s</p>' % v)
        else:
            out.append('<%s>%s</%s>' % (kind, ''.join('<li>%s</li>' % x for x in v), kind))
    return ''.join(out)


def blocks_text(blocks):
    return norm(' '.join(re.sub(r'<[^>]+>', ' ', v if k == 'p' else ' '.join(v)) for k, v in blocks))


# ----------------------------------------------------------------------------- QTI

QTI = '{http://www.imsglobal.org/xsd/imsqti_v2p1}'


def qti_inner(el):
    """Tekst/HTML binnen een QTI-element, zonder stijlattributen."""
    s = ET.tostring(el, encoding='unicode')
    s = re.sub(r'^<[^>]+>|</[^>]+>$', '', s.strip())
    s = re.sub(r'\sxmlns(:\w+)?="[^"]*"', '', s)
    s = re.sub(r'<(/?)ns\d+:', r'<\1', s)
    s = re.sub(r'\s(style|class)="[^"]*"', '', s)
    s = re.sub(r'</?(div|span)>', '', s)
    s = re.sub(r'<p\s*/>', '', s)
    return s.strip()


def plain(h):
    return norm(html.unescape(re.sub(r'<[^>]+>', ' ', h or '')))


def feedback_of(root):
    fb = {}
    for m in root.iter(QTI + 'modalFeedback'):
        fb[m.get('identifier')] = qti_inner(m)
    return fb


LEAD_RE = re.compile(r'^\s*(<p>)?\s*(goed|juist|inderdaad|dat klopt|correct)[.!,]?\s*(</p>)?\s*', re.I)


def explanation(fb):
    """Toelichting bij het juiste antwoord: 'T'-feedback zonder losse aanhef ('Goed.'); leeg als er alleen lof staat."""
    t = (fb.get('T') or '').strip()
    rest = LEAD_RE.sub('', t, count=1)
    if plain(rest):
        return t
    # alleen lof ('Goed.'): de uitleg staat dan vaak in de feedback bij een fout antwoord ('Goed zijn: ...')
    f = (fb.get('F') or '').strip()
    if plain(f) and not re.match(r'^\s*(controleer|probeer|kijk nog|helaas[,.]?\s*(probeer|dat is niet))', plain(f), re.I):
        return f
    return ''


def build_question(qxml, images):
    root = ET.fromstring(qxml)
    body = root.find(QTI + 'itemBody')
    fb = feedback_of(root)
    q = {'kind': 'quiz', 'title': root.get('title') or '', 'explanation': explanation(fb), 'tip': '', 'type': ''}
    correct = {}
    for rd in root.iter(QTI + 'responseDeclaration'):
        vals = [v.text or '' for v in rd.iter(QTI + 'value')]
        alts = [m.get('mapKey') for m in rd.iter(QTI + 'mapEntry') if (m.get('mapKey') or '').strip() and float(m.get('mappedValue') or 0) > 0]
        correct[rd.get('identifier')] = {'values': vals, 'alts': alts, 'card': rd.get('cardinality')}
    ch = body.find('.//' + QTI + 'choiceInteraction')
    if ch is not None:
        q['type'] = 'choice'
        ok = set(correct.get(ch.get('responseIdentifier'), {}).get('values', []))
        q['multi'] = ch.get('maxChoices') != '1'
        q['options'] = [(plain(qti_inner(sc)), sc.get('identifier') in ok) for sc in ch.iter(QTI + 'simpleChoice')]
        return q
    gg = body.find('.//' + QTI + 'graphicGapMatchInteraction')
    if gg is not None:
        q['type'] = 'gapmatch'
        pairs = correct.get(gg.get('responseIdentifier'), {}).get('values', [])
        q['solution_html'] = gapmatch_html(gg, pairs, images)   # echte tekst als dat kan
        if not q['solution_html']:
            q['solution'] = gapmatch_image(gg, pairs, images)
        return q
    sp = body.find('.//' + QTI + 'selectPointInteraction')
    if sp is not None:
        q['type'] = 'gapmatch'   # zelfde weergave: afbeelding met de juiste oplossing
        q['solution'] = selectpoint_image(sp, root, images)
        return q
    if body.find('.//' + QTI + 'extendedTextInteraction') is not None:
        q['type'] = 'open'
        q['model'] = fb.get('F') or fb.get('T') or ''
        return q
    inl = list(body.iter(QTI + 'inlineChoiceInteraction'))
    te = list(body.iter(QTI + 'textEntryInteraction'))
    if inl or te:
        # Gatentekst: de zin met het juiste antwoord ingevuld (gemarkeerd)
        q['type'] = 'gaps'
        div = body
        s = ET.tostring(div, encoding='unicode')
        s = re.sub(r'\sxmlns(:\w+)?="[^"]*"', '', s)
        s = re.sub(r'<(/?)ns\d+:', r'<\1', s)

        fields = []

        def put(t):
            fields.append(t)
            return '\x00%d\x00' % (len(fields) - 1)

        def fill_inline(m):
            rid = re.search(r'responseIdentifier="([^"]+)"', m.group(0)).group(1)
            want = set(correct.get(rid, {}).get('values', []))
            for cm in re.finditer(r'<inlineChoice[^>]*identifier="([^"]+)"[^>]*>(.*?)</inlineChoice>', m.group(0), re.S):
                if cm.group(1) in want:
                    return put(plain(cm.group(2)))
            return put('…')

        def fill_text(m):
            rid = re.search(r'responseIdentifier="([^"]+)"', m.group(0)).group(1)
            c = correct.get(rid, {})
            vals = [v for v in (c.get('values') or []) if v.strip()] or [a for a in c.get('alts', [])]
            alts = [a for a in c.get('alts', []) if a not in vals]
            t = vals[0] if vals else '…'
            if alts:
                t += ' (ook goed: %s)' % ', '.join(alts)
            return put(t)
        s = re.sub(r'<inlineChoiceInteraction.*?</inlineChoiceInteraction>', fill_inline, s, flags=re.S)
        s = re.sub(r'<textEntryInteraction[^>]*/>', fill_text, s)
        # opmaak van de bron weg (stijlen, div/span), daarna de ingevulde velden terugzetten
        s = re.sub(r'\s(style|class)="[^"]*"', '', s)
        s = re.sub(r'</?(div|span|itemBody)[^>]*>', '', s)
        s = re.sub(r'<p\s*/>|<p>\s*</p>', '', s)
        only_field = not plain(re.sub(r'\x00\d+\x00', '', s))
        s = re.sub(r'\x00(\d+)\x00', lambda m: gap_field(fields[int(m.group(1))]), s)
        q['gaps_html'] = s.strip()
        q['gaps_only_field'] = only_field  # alleen het invulvak, geen zin
        return q
    q['type'] = 'unknown'
    return q


_TOOLS = None


def tools():
    """OCR (Apple Vision) uit image-tables.py."""
    global _TOOLS
    if _TOOLS is None:
        spec = importlib.util.spec_from_file_location('image_tables', os.path.join(HERE, 'image-tables.py'))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.VISION = m.vision_bin()
        _TOOLS = m
    return _TOOLS


def img_of(images, u):
    from PIL import Image
    d = images.get(u)
    if not d:
        return None
    im = Image.open(io.BytesIO(base64.b64decode(d.split(',', 1)[1]))).convert('RGBA')
    white = Image.new('RGBA', im.size, (255, 255, 255, 255))
    return Image.alpha_composite(white, im).convert('RGB')   # transparante labels: op wit, niet op zwart


def ocr_lines(im, scale=3):
    """Tekstregels (van boven naar beneden) in een afbeelding; [] als er geen tekst is."""
    from PIL import Image
    T = tools()
    big = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    p = os.path.join(tempfile.gettempdir(), 'cpe-sp-ocr.png')
    big.save(p)
    raw = sorted([l for l in T.ocr(p) if l['t'].strip()], key=lambda l: l['y'])
    rows = []
    for l in raw:   # regels op (bijna) dezelfde hoogte samen, dan links naar rechts
        if rows and abs(l['y'] - rows[-1][0]['y']) < 0.5 * l['h']:
            rows[-1].append(l)
        else:
            rows.append([l])
    ls = [l for r in rows for l in sorted(r, key=lambda l: l['x'])]
    return [dict(l, x=l['x'] / scale, y=l['y'] / scale, w=l['w'] / scale, h=l['h'] / scale) for l in ls]


def text_only(im, lines):
    """Afbeelding bestaat (vrijwel) alleen uit tekst op een effen achtergrond."""
    import numpy as np
    a = np.asarray(im).astype(int)
    if not lines:
        return False
    bgc = np.median(a.reshape(-1, 3), axis=0)
    ink = (np.abs(a - bgc).sum(2) > 90)
    for l in lines:
        ink[int(max(l['y'] - 2, 0)):int(l['y'] + l['h'] + 2), int(max(l['x'] - 2, 0)):int(l['x'] + l['w'] + 2)] = False
    return ink.mean() < 0.01


def gapmatch_html(gg, pairs, images):
    """Sleepvraag als echte tekst: vakken in hun kleur met de titel uit de achtergrond en het juiste antwoord erin.
    Alleen als de achtergrond per vak een effen kleur met tekst is en elk sleepitem alleen tekst is; anders None."""
    try:
        import numpy as np
        bg = gg.find(QTI + 'object')
        base = img_of(images, bg.get('data')) if bg is not None else None
        if base is None:
            return None
        W, H = float(bg.get('width') or base.width), float(bg.get('height') or base.height)
        sx, sy = base.width / W, base.height / H
        gaps = {g.get('identifier'): g.find(QTI + 'object') for g in gg.iter(QTI + 'gapImg')}
        spots = [(h.get('identifier'), [float(x) for x in (h.get('coords') or '0,0,0,0').split(',')]) for h in gg.iter(QTI + 'associableHotspot')]
        answer = {}
        for p in pairs:
            g, h = (p.split() + ['', ''])[:2]
            answer.setdefault(h, []).append(g)
        cells = []
        for hid, c in spots:
            m = 6  # randlijnen van het raster niet meetellen
            box = base.crop((int(c[0] * sx) + m, int(c[1] * sy) + m, int(c[2] * sx) - m, int(c[3] * sy) - m))
            a = np.asarray(box).astype(int)
            q = (a // 16) * 16
            vals, cnt = np.unique(q.reshape(-1, 3), axis=0, return_counts=True)
            if cnt.max() / cnt.sum() < 0.6:
                return None                       # geen effen vak (foto/tekening)
            col = vals[cnt.argmax()] + 8
            lines = ocr_lines(box)
            if not lines or not text_only(box, lines):
                return None
            title = ' '.join(l['t'] for l in lines)
            ink = np.median(a[(np.abs(a - col).sum(2) > 150)].reshape(-1, 3), axis=0) if (np.abs(a - col).sum(2) > 150).any() else [0, 0, 0]
            labels = []
            for g in answer.get(hid, []):
                obj = gaps.get(g)
                im = img_of(images, obj.get('data')) if obj is not None else None
                if im is None:
                    return None
                gl = ocr_lines(im)
                if not gl or not text_only(im, gl):
                    return None
                labels.append(' '.join(l['t'] for l in gl))
            cells.append({'c': c, 'bg': '#%02x%02x%02x' % tuple(int(v) for v in col),
                          'fg': '#%02x%02x%02x' % tuple(int(v) for v in ink), 'title': title, 'labels': labels})
        out = []
        for cl in cells:
            c = cl['c']
            st = 'left:%.2f%%;top:%.2f%%;width:%.2f%%;height:%.2f%%;background:%s;color:%s' % (
                c[0] / W * 100, c[1] / H * 100, (c[2] - c[0]) / W * 100, (c[3] - c[1]) / H * 100, cl['bg'], cl['fg'])
            labs = ''.join('<div class="gm-answer">%s</div>' % esc(t) for t in cl['labels'])
            out.append('<div class="gm-cell" style="%s">%s<div class="gm-title">%s</div></div>' % (st, labs, esc(cl['title'])))
        return '<div class="gm-board" style="aspect-ratio:%s/%s">%s</div>' % (int(W), int(H), ''.join(out))
    except Exception as e:  # OCR niet beschikbaar (geen macOS) of onverwachte vorm: terug naar de afbeelding
        print('sleepvraag als afbeelding:', e)
        return None


TEXT_IMAGES = [True]


def image_as_text(images, src):
    """Regel: een afbeelding die alleen tekst op een effen achtergrond is, wordt echte tekst (in de eigen kleuren)."""
    try:
        import numpy as np
        im = img_of(images, src)
        if im is None or im.width * im.height < 2000:
            return None
        small = im if im.width <= 1200 else im.resize((1200, int(im.height * 1200 / im.width)))
        ls = ocr_lines(small, 2)
        if not ls or not text_only(small, ls):
            return None
        a = np.asarray(small).astype(int)
        bgc = np.median(a.reshape(-1, 3), axis=0)
        mask = np.abs(a - bgc).sum(2) > 150
        ink = np.median(a[mask].reshape(-1, 3), axis=0) if mask.any() else np.array([0, 0, 0])
        rows, cur = [], []
        for l in ls:   # regels met een duidelijke witregel ertussen = nieuwe alinea
            if cur and l['y'] - (cur[-1]['y'] + cur[-1]['h']) > 0.8 * l['h']:
                rows.append(cur)
                cur = []
            cur.append(l)
        rows.append(cur)
        body = ''.join('<p>%s</p>' % esc(' '.join(l['t'] for l in r)) for r in rows)
        return '<div class="sp-textimage" style="background:#%02x%02x%02x;color:#%02x%02x%02x">%s</div>' % (
            tuple(int(v) for v in bgc) + tuple(int(v) for v in ink) + (body,))
    except Exception as e:
        print('afbeelding blijft afbeelding:', src, e)
        return None


def gap_field(t):
    """Ingevuld keuzevak/invulveld: als een gevuld veld met vinkje, zodat duidelijk is wat er gekozen moet worden.
    Inline opmaak, zodat het altijd zo afgedrukt wordt."""
    return ('<span class="gap" style="background:#eef6f0;color:#1f5c36;font-weight:600;border-bottom:1px solid #8fbf9f;'
            'border-radius:2px;padding:0 .3em;white-space:nowrap">%s</span>') % esc(t)


def selectpoint_image(sp, root, images):
    """Aanwijsvraag: de juiste plek(ken) als groene cirkel op de afbeelding."""
    try:
        from PIL import ImageDraw
        obj = sp.find(QTI + 'object')
        im = img_of(images, obj.get('data'))
        if im is None:
            return None
        W, H = float(obj.get('width') or im.width), float(obj.get('height') or im.height)
        sx, sy = im.width / W, im.height / H
        dr = ImageDraw.Draw(im)
        for a in root.iter(QTI + 'areaMapEntry'):
            if float(a.get('mappedValue') or 0) <= 0:
                continue
            c = [float(v) for v in (a.get('coords') or '').split(',') if v.strip()]
            lw = max(3, int(min(im.width, im.height) / 70))
            if a.get('shape') == 'circle' and len(c) >= 3:
                x, y, r = c[0] * sx, c[1] * sy, max(c[2] * sx, 10)
                dr.ellipse([x - r, y - r, x + r, y + r], outline=(29, 160, 70), width=lw)
            elif a.get('shape') == 'rect' and len(c) >= 4:
                dr.rectangle([c[0] * sx, c[1] * sy, c[2] * sx, c[3] * sy], outline=(29, 160, 70), width=lw)
            elif a.get('shape') == 'poly' and len(c) >= 6:
                pts = [(c[i] * sx, c[i + 1] * sy) for i in range(0, len(c) - 1, 2)]
                dr.line(pts + [pts[0]], fill=(29, 160, 70), width=lw)
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=88)
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print('aanwijsvraag:', e)
        return None


def gapmatch_image(gg, pairs, images):
    """Sleepvraag met afbeeldingen: de juiste plaatjes op hun plek getekend (Pillow)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    bg = gg.find(QTI + 'object')
    src = bg.get('data') if bg is not None else None
    if not src or not images.get(src):
        return None
    W, H = float(bg.get('width') or 0), float(bg.get('height') or 0)

    def load(u):
        d = images.get(u)
        if not d:
            return None
        return Image.open(io.BytesIO(base64.b64decode(d.split(',', 1)[1]))).convert('RGBA')
    base = load(src)
    sx, sy = base.width / (W or base.width), base.height / (H or base.height)
    gaps = {g.get('identifier'): g.find(QTI + 'object') for g in gg.iter(QTI + 'gapImg')}
    spots = {h.get('identifier'): [float(x) for x in (h.get('coords') or '0,0,0,0').split(',')] for h in gg.iter(QTI + 'associableHotspot')}
    for p in pairs:
        g, h = (p.split() + ['', ''])[:2]
        obj, c = gaps.get(g), spots.get(h)
        if obj is None or not c:
            continue
        im = load(obj.get('data'))
        if im is None:
            continue
        w, hgt = float(obj.get('width') or im.width), float(obj.get('height') or im.height)
        im = im.resize((max(1, int(w * sx)), max(1, int(hgt * sy))))
        # zoals in de speler: linksboven in het vak, zonder kader; de vaktitel (midden) blijft vrij
        pad = 6
        x0, y0 = int((c[0] + pad) * sx), int((c[1] + pad) * sy)
        base.alpha_composite(im, (x0, y0))
    buf = io.BytesIO()
    base.convert('RGB').save(buf, 'JPEG', quality=88)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


# ----------------------------------------------------------------------------- pagina's

def html_blocks(h):
    """htmlcontent (p/span-HTML) -> blokken."""
    out = []
    for m in re.finditer(r'<(p|li)\b[^>]*>(.*?)</\1>', h, re.S):
        inner = m.group(2)
        inner = re.sub(r'<span[^>]*font-weight:\s*bold[^>]*>(.*?)</span>', r'<strong>\1</strong>', inner, flags=re.S)
        inner = re.sub(r'<span[^>]*font-style:\s*italic[^>]*>(.*?)</span>', r'<em>\1</em>', inner, flags=re.S)
        inner = re.sub(r'<(?!/?(strong|em|br|a)\b)[^>]+>', '', inner)
        inner = re.sub(r'\s(class|style)="[^"]*"', '', inner).strip()
        if plain(inner):
            if m.group(1) == 'li' and out and out[-1][0] == 'ul':
                out[-1][1].append(inner)
            elif m.group(1) == 'li':
                out.append(('ul', [inner]))
            else:
                out.append(('p', inner))
    return out


def table3_html(el):
    """Table3 (Grid met RichTextBox-cellen, elk met Xaml) -> HTML-tabel."""
    cells = {}
    for b in el.iter():
        if local(b.tag) != 'Border':
            continue
        r, c = int(attr(b, 'Row', '0') or 0), int(attr(b, 'Column', '0') or 0)
        rtb = next((x for x in b.iter() if local(x.tag) == 'RichTextBox'), None)
        xa = html.unescape(attr(rtb, 'Xaml', '') or '') if rtb is not None else ''
        paras = []
        for pm in re.finditer(r'<Paragraph\b[^>]*>(.*?)</Paragraph>', xa, re.S):
            runs = []
            for rm in re.finditer(r'<Run\b([^>]*)Text="([^"]*)"', pm.group(1)):
                t = esc(html.unescape(rm.group(2)))
                if re.search(r'FontWeight="Bold"', rm.group(1)):
                    t = '<strong>%s</strong>' % t
                runs.append(t)
            if plain(''.join(runs)):
                paras.append(''.join(runs))
        cells[(r, c)] = ''.join('<p>%s</p>' % x for x in paras)
    if not cells:
        return ''
    R = max(r for r, c in cells) + 1
    C = max(c for r, c in cells) + 1
    rows = ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % cells.get((r, c), '') for c in range(C)) for r in range(R))
    return '<table class="sp-table">%s</table>' % rows


def page_items(xaml, images, hidden_pages):
    root = parse_xaml(xaml)
    els = []
    inner = set()   # elementen binnen een TSRichTextBox/Table3 niet nog eens apart lezen
    for el in root.iter():
        if local(el.tag) in ('TSRichTextBox', 'Table3', 'Question'):
            for d in el.iter():
                if d is not el:
                    inner.add(id(d))
    for el in root.iter():
        t = local(el.tag)
        if id(el) in inner:
            continue
        x = float(attr(el, 'Left', '0') or 0) if attr(el, 'Left') not in (None, 'NaN') else 0.0
        y = float(attr(el, 'Top', '0') or 0) if attr(el, 'Top') not in (None, 'NaN') else 0.0
        def num(v):
            try:
                f = float(v)
                return 0.0 if f != f else f   # NaN -> 0
            except (TypeError, ValueError):
                return 0.0   # 'NaN', '49*' (kolombreedte in een raster)
        w = num(attr(el, 'Width', '0'))
        hh = num(attr(el, 'Height', '0'))
        popup = (attr(el, 'IsPopup', '') or '').lower() == 'true'
        if t == 'TSRichTextBox' and popup:
            # tekst achter een klik-icoon (hotspot): als uitklapkader tonen
            hc = html.unescape(attr(el, 'htmlcontent', '') or '')
            bl = html_blocks(hc) if hc else []
            if not bl:
                doc = next((d for d in el.iter() if local(d.tag) == 'C1Document'), None)
                bl = doc_blocks(doc) if doc is not None else []
            if bl:
                els.append({'kind': 'popupbox', 'x': x, 'y': y, 'w': w, 'h': hh, 'blocks': bl})
            continue
        if t == 'Table3':
            els.append({'kind': 'table', 'x': x, 'y': y, 'w': w, 'h': hh, 'html': table3_html(el)})
            continue
        if t == 'Carousel':
            items = re.findall(r'source="([^"]+)"', html.unescape(attr(el, 'ItemsXML', '') or ''))
            els.append({'kind': 'carousel', 'x': x, 'y': y, 'w': w, 'h': hh, 'srcs': items, 'data': [images.get(u, '') for u in items]})
            continue
        if t == 'HtmlPlayer':
            url = attr(el, 'DisplayName', '') or ''
            m = re.search(r'youtube\.com/(?:v/|watch\?v=|embed/)([\w-]{6,})|youtu\.be/([\w-]{6,})', url)
            if m:
                vid = m.group(1) or m.group(2)
                els.append({'kind': 'youtube', 'x': x, 'y': y, 'w': w, 'h': hh, 'vid': vid})
            elif url:
                els.append({'kind': 'note', 'x': x, 'y': y, 'w': w, 'h': hh, 'text': 'Webinhoud in de e-module: ' + url})
            continue
        if t == 'TextBlock':
            tx = attr(el, 'Text', '') or ''
            if tx and not tx.startswith('{') and norm(tx):
                els.append({'kind': 'label', 'x': x, 'y': y, 'w': w, 'h': hh, 'text': norm(html.unescape(tx))})
            continue
        if t == 'C1RichTextBox':
            # ouder formaat: losse C1RichTextBox op het canvas, tekst in htmlcontent of een C1Document
            hc = html.unescape(attr(el, 'htmlcontent', '') or '')
            doc = next((d for d in el.iter() if local(d.tag) == 'C1Document'), None)
            bl = doc_blocks(doc) if doc is not None else []
            if not bl and hc:
                bl = html_blocks(hc)
            if bl:
                els.append({'kind': 'popupbox' if popup else 'text', 'x': x, 'y': y, 'w': w, 'h': hh, 'blocks': bl})
            continue
        if t == 'TSRichTextBox':
            doc = next((d for d in el.iter() if local(d.tag) == 'C1Document'), None)
            rtb = next((d for d in el.iter() if local(d.tag) == 'C1RichTextBox'), None)
            if rtb is not None and not w:
                rw = attr(rtb, 'Width', '0')
                w = float(rw) if rw not in (None, 'NaN', '') else 0.0
            if doc is not None:
                bl = doc_blocks(doc)
                if bl:
                    els.append({'kind': 'text', 'x': x, 'y': y, 'w': w, 'h': hh, 'blocks': bl})
        elif t in ('TSImage', 'TSHotspotImage'):
            src = attr(el, 'Source', '')
            link = next((attr(b, 'Link') for b in el.iter() if local(b.tag) == 'NavigateBehavior'), None)
            if src and images.get(src) and TEXT_IMAGES[0]:
                tb = image_as_text(images, src)
                if tb:
                    els.append({'kind': 'textimage', 'x': x, 'y': y, 'w': w, 'h': hh, 'html': tb})
                    continue
            if src or link:
                els.append({'kind': 'image', 'x': x, 'y': y, 'w': w, 'h': hh, 'src': src, 'data': images.get(src, ''), 'link': link,
                            'popup': popup})
        elif t == 'MediaPlayer':
            els.append({'kind': 'video', 'x': x, 'y': y, 'w': w, 'h': hh, 'src': attr(el, 'Source', '')})
        elif t == 'Question':
            qx = attr(el, 'QTIXML', '')
            if qx:
                q = build_question(qx, images)
                q.update({'x': x, 'y': y, 'w': w, 'h': hh})
                els.append(q)
    return els


def is_caption(e, img):
    if img and attachment_key(img.get('link') or '') and abs(e['x'] - img['x']) <= 60 and 0 <= e['y'] - (img['y'] + img['h']) <= 60:
        return True  # tekst onder een bijlage-pictogram
    t = blocks_text(e['blocks'])
    return (len(t) <= 90 and re.match(r'^(afbeelding|foto|bron|video)\b', t, re.I)) or \
        (img and 0 <= e['y'] - (img['y'] + img['h']) <= 40 and len(t) <= 90 and abs(e['x'] - img['x']) <= 40)


def layout(els):
    """Leesvolgorde en plaatsing: koppen/tekst links, afbeelding rechts -> naast elkaar; bijschrift bij de afbeelding."""
    imgs = [e for e in els if e['kind'] == 'image']
    for e in els:
        if e['kind'] == 'text':
            near = [i for i in imgs if 0 <= e['y'] - (i['y'] + i['h']) <= (60 if attachment_key(i.get('link') or '') else 40) and abs(e['x'] - i['x']) <= 60]
            if near and is_caption(e, near[0]):
                near[0].setdefault('caption', []).append(e)
                e['used'] = True
    rest = [e for e in els if not e.get('used')]
    rest.sort(key=lambda e: (e['y'] if e['kind'] != 'quiz' else e['y'] + 1e4, e['x']))
    # Hoofdtekst links (x < 380) en afbeeldingen rechts: als twee kolommen tonen
    left = [e for e in rest if e['kind'] == 'text' and e['x'] < 380]
    right = [e for e in rest if e['kind'] in ('image', 'video') and e['x'] >= 380]
    if left and right and not any(e['kind'] == 'image' and e['x'] < 380 and e['w'] > 400 for e in rest):
        others = [e for e in rest if e not in left and e not in right]
        return [{'kind': 'aside', 'left': left, 'right': right}] + others
    return rest


def flatten_pages(project):
    root = ET.fromstring(project)
    out = []
    pages = next(c for c in root.iter() if local(c.tag) == 'Pages')

    def walk(node, chapter, depth):
        for p in [c for c in node if local(c.tag) == 'Page']:
            title = p.get('Title') or p.get('Name') or ''
            ch = chapter if depth else title
            out.append({'id': p.get('Id'), 'title': title, 'name': p.get('Name'), 'src': p.get('Source'), 'chapter': ch, 'top': depth == 0})
            sub = next((c for c in p if local(c.tag) == 'Pages'), None)
            if sub is not None:
                walk(sub, ch, depth + 1)
    walk(pages, None, 0)
    hidden = {}
    hp = next((c for c in root.iter() if local(c.tag) == 'HiddenPages'), None)
    if hp is not None:
        for p in [c for c in hp.iter() if local(c.tag) == 'Page']:
            hidden[p.get('Id')] = {'title': p.get('Title') or p.get('Name'), 'src': p.get('Source')}
    return root.get('Title'), out, hidden


# ----------------------------------------------------------------------------- HTML

_SEQ = [0]


def h_quiz(q):
    out = ['<div class="quiz"><div class="quiz-label">Vraag</div>']
    if q.get('question_html'):
        out.append('<div class="question">%s</div>' % q['question_html'])
    if q['type'] == 'choice':
        lis = []
        for text, ok in q['options']:
            _SEQ[0] += 1
            m = 'cpeopt%04d' % _SEQ[0]
            lis.append('<li data-opt="%s" data-mark="%s"%s><span class="cpe-mark">%s</span>%s</li>' % (
                esc(text), m, ' data-correct="1" class="correct"' if ok else '', m, esc(text)))
        out.append('<ul class="options %s">%s</ul>' % ('check' if q.get('multi') else 'radio', ''.join(lis)))
    elif q['type'] == 'gaps':
        if q.get('gaps_only_field'):
            out.append('<div class="answer"><span class="answer-label">Juiste antwoord:</span> %s</div>' % re.sub(r'</?span[^>]*>', '', q['gaps_html']))
        else:
            out.append('<div class="gaps">%s</div>' % q['gaps_html'])
    elif q['type'] == 'gapmatch':
        if q.get('solution_html'):
            out.append('<figure class="solution">%s<figcaption>Juiste oplossing</figcaption></figure>' % q['solution_html'])
        elif q.get('solution'):
            out.append('<figure class="solution"><img src="%s" alt=""><figcaption>Juiste oplossing</figcaption></figure>' % q['solution'])
    elif q['type'] == 'open':
        out.append('<p class="hint">Open vraag: schrijf je antwoord op en vergelijk het met het voorbeeld hieronder.</p>')
        if plain(q.get('model')):
            out.append('<div class="answer"><span class="answer-label">Voorbeeldantwoord:</span> %s</div>' % q['model'])
    if q.get('explanation') and q['type'] != 'open':
        out.append('<div class="answer"><span class="answer-label">Toelichting:</span> %s</div>' % q['explanation'])
    out.append('</div>')
    return ''.join(out)


def h_image(e):
    att = attachment_key(e.get('link') or '')
    if att and ATT.data.get(att):
        # pictogram van een bijlage: als link naar de bijlage achteraan
        r = ATT.ref(att)
        cap = ''.join('<p><a href="#%s">%s</a></p>' % (r, re.sub(r'</?p>', '', blocks_html(c['blocks']))) for c in e.get('caption', []))
        return '<div class="sp-attach"><a href="#%s">📄 Bijlage %s: %s</a>%s</div>' % (
            r, r.split('-')[1], esc(ATT.name(att)), cap)
    pl = re.match(r'page://([\w-]+)', e.get('link') or '')
    if pl and POPUPS.get(pl.group(1)) and not POPUPS[pl.group(1)].get('shown'):
        pu = POPUPS[pl.group(1)]
        pu['shown'] = True
        cap = ''.join('<figcaption>%s</figcaption>' % blocks_html(c['blocks']) for c in e.get('caption', []))
        if not plain(re.sub(r'<img[^>]*>', '', pu['html'])):
            # pop-up zonder tekst = vergroting: alleen de grote afbeelding(en), met het bijschrift
            big = re.findall(r'<img src="([^"]+)"', pu['html'])
            return ''.join('<figure class="sp-zoom"><img src="%s" alt="">%s</figure>' % (b, cap if i == 0 else '') for i, b in enumerate(big))
        img = ('<figure><img src="%s" alt="">%s</figure>' % (e['data'], cap)) if e.get('data') else \
            ''.join('<div class="block">%s</div>' % blocks_html(c['blocks']) for c in e.get('caption', []))
        return img + '<div class="popup"><div class="pu-head">%s</div><div class="pu-body">%s</div></div>' % (esc(pu['title']), pu['html'])
    if not e.get('data'):
        # pictogram zonder afbeelding (bijlage): alleen het bijschrift, als gewone tekst
        return ''.join('<div class="block">%s</div>' % blocks_html(c['blocks']) for c in e.get('caption', []))
    cap = ''.join('<figcaption>%s</figcaption>' % blocks_html(c['blocks']) for c in e.get('caption', []))
    return '<figure><img src="%s" alt="">%s</figure>' % (e['data'], cap)


def h_el(e, popups):
    k = e['kind']
    if k == 'text':
        h = blocks_html(e['blocks'])
        extra = ''
        for pid in re.findall(r'data-page="([^"]+)"', h):
            if pid in popups and not popups[pid].get('shown'):
                popups[pid]['shown'] = True
                extra += '<div class="popup"><div class="pu-head">%s</div><div class="pu-body">%s</div></div>' % (
                    esc(popups[pid]['title']), popups[pid]['html'])
        return '<div class="block">%s</div>%s' % (h, extra)
    if k == 'image':
        return h_image(e)
    if k == 'textimage':
        return e['html']
    if k == 'table':
        return '<div class="block">%s</div>' % e['html']
    if k == 'label':
        return '<h4 class="sp-label">%s</h4>' % esc(e['text'])
    if k == 'note':
        return '<p class="note">[%s]</p>' % esc(e['text'])
    if k == 'popupbox':
        bl = list(e['blocks'])
        head = ''
        if bl and bl[0][0] == 'p' and len(plain(bl[0][1])) <= 60 and len(bl) > 1:
            head = '<div class="pu-head">%s</div>' % re.sub(r'</?strong>', '', bl[0][1])
            bl = bl[1:]
        return '<div class="popup">%s<div class="pu-body">%s</div></div>' % (head, blocks_html(bl))
    if k == 'carousel':
        figs = ''.join('<figure><img src="%s" alt=""></figure>' % d for d in e['data'] if d)
        return '<div class="sp-carousel">%s</div>' % figs
    if k == 'youtube':
        th = YT_THUMBS.get(e['vid'])
        url = 'https://www.youtube.com/watch?v=' + e['vid']
        img = ('<div class="sp-vthumb"><img src="%s" alt=""><span class="sp-play"></span></div>' % th) if th else ''
        return ('<figure class="sp-video"><a href="%s">%s</a><figcaption>&#9654; Video (YouTube): <a href="%s">%s</a> '
                '&ndash; bekijk deze video in de e-module.</figcaption></figure>') % (url, img, url, url)
    if k == 'video':
        name = os.path.splitext(os.path.basename(e.get('src') or ''))[0]
        th = THUMBS.get(e.get('src') or '')
        if th:
            return ('<figure class="sp-video"><div class="sp-vthumb"><img src="%s" alt=""><span class="sp-play"></span></div>'
                    '<figcaption>&#9654; Video in de e-module%s &ndash; bekijk deze video in de e-module.</figcaption></figure>') % (
                th, (': <strong>%s</strong>' % esc(name)) if name else '')
        return '<p class="note">[Video in de e-module%s]</p>' % (': ' + esc(name) if name else '')
    if k == 'quiz':
        return h_quiz(e)
    if k == 'aside':
        L = ''.join(h_el(x, popups) for x in e['left'])
        R = ''.join(h_el(x, popups) for x in e['right'])
        return '<div class="sp-aside"><div class="sp-text">%s</div><div class="sp-media">%s</div></div>' % (L, R)
    return ''


CSS_EXTRA = """
.sp figure.sp-zoom img { max-height: 120mm; width: auto; max-width: 100%; }
.sp .popup { break-inside: avoid; }
.sp table.sp-table { border-collapse: collapse; width: 100%; font-size: 9.2pt; margin: .3em 0 1em; }
.sp table.sp-table td { border: 1px solid #777; padding: .3em .5em; vertical-align: top; }
.sp table.sp-table p { margin: 0 0 .25em; }
.sp .sp-carousel { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; margin: .4em 0 1em; }
.sp .sp-carousel img { width: 100%; height: auto; max-height: 55mm; object-fit: cover; }
.sp .popup .pu-img img { max-height: 55mm; width: auto; max-width: 100%; }
.sp figure.sp-video { margin: .5em 0 1em; max-width: 120mm; break-inside: avoid; }
.sp .sp-vthumb { position: relative; display: inline-block; line-height: 0; border-radius: 4px; overflow: hidden; }
.sp .sp-vthumb img { max-height: 60mm; width: auto; max-width: 100%; display: block; }
.sp .sp-play { position: absolute; left: 50%; top: 50%; width: 15mm; height: 15mm; margin: -7.5mm 0 0 -7.5mm; border-radius: 50%; background: rgba(0,0,0,.55); }
.sp .sp-play::after { content: ""; position: absolute; left: 5.8mm; top: 4mm; border-style: solid; border-width: 3.5mm 0 3.5mm 5.6mm; border-color: transparent transparent transparent #fff; }
.sp figure.sp-video figcaption { font-size: 9pt; color: #333; margin-top: .3em; }
.sp .quiz .answer p + p, .sp .quiz .answer ul, .sp .quiz .answer ol { display: block; margin: .45em 0 0; }
.sp .quiz .answer ul, .sp .quiz .answer ol { padding-left: 1.3em; }
.sp .sp-textimage { padding: .6em .9em; border-radius: 4px; margin: .4em 0 1em; }
.sp .sp-textimage p { margin: 0 0 .35em; }
.sp .gm-board { position: relative; width: 100%; max-width: 150mm; border: 1px solid #111; margin: .4em 0 .2em; break-inside: avoid; }
.sp .gm-cell { position: absolute; box-sizing: border-box; border: 1px solid #111; padding: 2.2mm 2.6mm; display: flex; flex-direction: column; }
.sp .gm-title { margin: auto 0; text-align: center; font-size: 15pt; font-weight: 700; line-height: 1.15; }
.sp .gm-answer { align-self: flex-start; background: rgba(255,255,255,.92); color: #1a1a1a; border-left: 3px solid #1d6b2f; padding: .15em .45em; font-size: 8.8pt; line-height: 1.3; margin: 0 0 .25em; max-width: 92%; }
.sp .gm-answer::before { content: "✓ "; color: #1d6b2f; font-weight: 700; }
.sp .sp-attach { margin: .4em 0 1em; padding: .55em .8em; border: 1px solid #c6dcee; border-radius: 6px; background: #f3f8fc; }
.sp .sp-attach > a { font-weight: 700; }
.sp .sp-attach p { margin: .3em 0 0; font-size: 9pt; }
.sp .sp-att-marker { color: #fff; font-size: 1pt; }
.sp-aside { display: grid; grid-template-columns: 1fr 38%; gap: 6mm; align-items: start; margin: 0 0 1em; }
.sp-aside .sp-media figure { margin: 0 0 .8em; }
.sp-aside .sp-media img { width: 100%; height: auto; max-height: 70mm; object-fit: contain; }
.sp h3.page { font-size: 12.5pt; margin: 1.8em 0 .5em; break-after: avoid; }
.sp figure img { max-height: 95mm; width: auto; max-width: 100%; }
.sp figcaption, .sp figcaption p { font-size: 8.8pt; color: #555; margin: .3em 0 0; }
.sp .quiz .gaps p { margin: 0 0 .5em; }
.sp span.gap { background: #e7f2e9; color: #1d6b2f; font-weight: 700; padding: 0 .25em; border-radius: 3px; border-bottom: 1.5px solid #1d6b2f; }
.sp .popup { margin: .6em 0 1em; border-left: 3px solid #2f6fa3; background: #f3f6fa; padding: .65em .95em; border-radius: 0 6px 6px 0; }
.sp .popup .pu-head { font-weight: 700; color: #1d3a5c; margin: 0 0 .35em; }
.sp a { color: #1b62a5; text-decoration: underline; text-underline-offset: 2px; }
.sp .note { color: #666; font-style: italic; }
.sp .quiz figure.solution img { max-height: 90mm; }
"""


def build(dump):
    files = dump['files']
    images = dump.get('images') or {}
    title, pages, hidden = flatten_pages(files['project.xml'])
    title = html.unescape(title or dump.get('title') or 'Cursus')
    popups = POPUPS
    popups.clear()
    for pid, hp in hidden.items():
        src = files.get(hp['src'])
        if src:
            its = page_items(src, images, {})
            parts = []
            for e in sorted(its, key=lambda e: (e['y'], e['x'])):
                if e['kind'] == 'text':
                    parts.append(blocks_html(e['blocks']))
                elif e['kind'] == 'image' and e.get('data'):
                    parts.append('<figure class="pu-img"><img src="%s" alt=""></figure>' % e['data'])
                elif e['kind'] == 'table':
                    parts.append(e['html'])
                elif e['kind'] == 'video':
                    parts.append(h_el(e, {}))
            popups[pid] = {'title': hp['title'], 'html': ''.join(parts)}
    # bijlagen nummeren in leesvolgorde (eerste vermelding in de cursus)
    for p in pages:
        src = files.get(p['src']) or ''
        for m in re.finditer(r'(?:Link|NavigateUri)="([^"]+)"', src):
            k = attachment_key(m.group(1))
            if k and ATT.data.get(k):
                ATT.ref(k)
    chapters, cur = [], None
    for p in pages:
        src = files.get(p['src'])
        if not src:
            continue
        els = page_items(src, images, hidden)
        # vraagtekst = tekstvak direct boven de vraag (bevat meestal '?')
        for q in [e for e in els if e['kind'] == 'quiz']:
            above = [e for e in els if e['kind'] == 'text' and e['y'] < q['y'] and not e.get('used')]
            if above:
                qe = max(above, key=lambda e: e['y'])
                q['question_html'] = blocks_html(qe['blocks'])
                qe['used'] = True
        els = [e for e in els if not e.get('used')]
        if p['top'] or cur is None:
            cur = {'title': p['chapter'] or p['title'], 'pages': []}
            chapters.append(cur)
        # vaste kop in de pagina: alleen tonen als die meer zegt dan de menutitel
        tnorm = norm(p['title']).lower()
        for e in els:
            if e['kind'] == 'label':
                e['skip'] = e['text'].lower() == tnorm or (tnorm and e['text'].lower().startswith(tnorm) and False)
                if not e['skip'] and e['y'] < 40:
                    p = dict(p, title=e['text'])   # bijv. 'Oorzaken' -> 'Oorzaken van stoornissen in de vitale functies'
                    e['skip'] = True
        els = [e for e in els if not (e['kind'] == 'label' and e.get('skip'))]
        cur['pages'].append({'title': p['title'], 'items': layout(els)})
    return title, chapters, popups


def render(title, chapters, popups):
    B = b2p()
    toc = ''.join('<li>%s</li>' % esc(c['title']) for c in chapters)
    cover = ('<section class="cover"><p class="cover-kicker">E-module</p><h1>%s</h1>'
             '<div class="toc"><h2>Inhoud</h2><ol>%s</ol></div>%s</section>') % (esc(title), toc, B.provenance_html(title))
    secs = []
    for i, c in enumerate(chapters, 1):
        parts = []
        for j, p in enumerate(c['pages']):
            body = ''.join(h_el(e, popups) for e in p['items'])
            if not plain(re.sub(r'<img[^>]*>', 'IMG', body)) and 'IMG' not in body and '<img' not in body:
                continue
            if j > 0 or norm(p['title']).lower() != norm(c['title']).lower():
                parts.append('<h3 class="page">%s</h3>' % esc(p['title']))
            parts.append(body)
        secs.append('<section class="chapter"><div class="chapter-header"><p class="kicker">Hoofdstuk %d</p><h1>%s</h1></div>%s</section>'
                    % (i, esc(c['title']), ''.join(parts)))
    for k in ATT.order:
        if ATT.data.get(k):
            r = ATT.ref(k)
            secs.append('<section class="chapter sp-att" id="%s"><div class="chapter-header"><p class="kicker">Bijlage %s</p><h1>%s</h1></div>'
                        '<p>Deze bijlage hoort bij de e-module en staat op de volgende pagina\'s.</p><p class="sp-att-marker">[[%s]]</p></section>'
                        % (r, r.split('-')[1], esc(ATT.name(k)), r))
    css = B.CSS.replace('__CPE_TITLE__', title.replace('\\', '\\\\').replace('"', '\\"')) + CSS_EXTRA
    return ('<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>%s</title><style>%s</style></head>'
            '<body class="forms sp">%s%s</body></html>') % (esc(title), css, cover, '\n'.join(secs))


def yt_thumbs(dump):
    """Miniaturen van ingesloten YouTube-video's (openbare afbeelding van YouTube)."""
    import urllib.request
    ids = set()
    for k, v in (dump.get('files') or {}).items():
        for m in re.finditer(r'youtube\.com/(?:v/|watch\?v=|embed/)([\w-]{6,})', html.unescape(v)):
            ids.add(m.group(1))
    for vid in ids:
        try:
            data = urllib.request.urlopen('https://img.youtube.com/vi/%s/hqdefault.jpg' % vid, timeout=15).read()
            YT_THUMBS[vid] = 'data:image/jpeg;base64,' + base64.b64encode(data).decode()
        except Exception as e:
            print('geen YouTube-miniatuur', vid, e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dump')
    ap.add_argument('--pdf')
    ap.add_argument('--model')
    ap.add_argument('--bijlagen', action='store_true', help='bijlagen uit het pakket achteraan toevoegen en ernaar linken')
    a = ap.parse_args()
    dump = json.load(open(a.dump, encoding='utf-8'))
    # bijlagen achteraan + links alleen met --bijlagen; standaard blijft de tekst zoals op de pagina
    THUMBS.update(dump.get('thumbs') or {})
    yt_thumbs(dump)
    ATT.data = {re.sub(r'\s+', ' ', k).strip(): v for k, v in (dump.get('attachments') or {}).items()} if a.bijlagen else {}
    title, chapters, popups = build(dump)
    if a.model:
        json.dump({'title': title, 'chapters': chapters}, open(a.model, 'w'), ensure_ascii=False, indent=1,
                  default=lambda o: None)
    out = a.pdf or re.sub(r'(\.sp)?\.json$', '', a.dump) + '.pdf'
    page = render(title, chapters, popups)
    tmp = os.path.join(tempfile.gettempdir(), re.sub(r'\W+', '-', title) + '.sp.print.html')
    open(tmp, 'w', encoding='utf-8').write(page)
    r = subprocess.run([sys.executable, os.path.join(HERE, 'book-to-print.py'), tmp, '--ready', '--html', tmp, '--pdf', out],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit('book-to-print.py mislukt:\n' + r.stdout + r.stderr)
    print(r.stdout.strip())
    append_attachments(out)
    print('OK', out)


def append_attachments(pdf_path):
    """Pagina's van de bijlagen invoegen na hun titelpagina ([[bijlage-N]]-markering)."""
    if not any(ATT.data.get(k) for k in ATT.order):
        return
    import fitz
    doc = fitz.open(pdf_path)
    for k in reversed(ATT.order):
        d = ATT.data.get(k)
        if not d:
            continue
        mark = '[[%s]]' % ATT.ref(k)
        pno = next((p.number for p in doc if mark in p.get_text()), None)
        if pno is None:
            continue
        for rect in doc[pno].search_for(mark):
            doc[pno].add_redact_annot(rect)
        doc[pno].apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        src = fitz.open(stream=base64.b64decode(d.split(',', 1)[1]), filetype='pdf')
        doc.insert_pdf(src, start_at=pno + 1)
        print('bijlage toegevoegd:', ATT.name(k), len(src), "pagina's")
    tmp = pdf_path + '.tmp'
    doc.save(tmp, garbage=1, deflate=True)
    doc.close()
    os.replace(tmp, pdf_path)


if __name__ == '__main__':
    main()
