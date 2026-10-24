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
        if att:
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
        q['solution'] = gapmatch_image(gg, correct.get(gg.get('responseIdentifier'), {}).get('values', []), images)
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

        def fill_inline(m):
            rid = re.search(r'responseIdentifier="([^"]+)"', m.group(0)).group(1)
            want = set(correct.get(rid, {}).get('values', []))
            for cm in re.finditer(r'<inlineChoice[^>]*identifier="([^"]+)"[^>]*>(.*?)</inlineChoice>', m.group(0), re.S):
                if cm.group(1) in want:
                    return '<span class="gap">%s</span>' % esc(plain(cm.group(2)))
            return '<span class="gap">…</span>'
        s = re.sub(r'<inlineChoiceInteraction.*?</inlineChoiceInteraction>', fill_inline, s, flags=re.S)

        def fill_text(m):
            rid = re.search(r'responseIdentifier="([^"]+)"', m.group(0)).group(1)
            c = correct.get(rid, {})
            vals = [v for v in (c.get('values') or []) if v.strip()] or [a for a in c.get('alts', [])]
            alts = [a for a in c.get('alts', []) if a not in vals]
            t = vals[0] if vals else '…'
            if alts:
                t += ' (ook goed: %s)' % ', '.join(alts)
            return '<span class="gap">%s</span>' % esc(t)
        s = re.sub(r'<textEntryInteraction[^>]*/>', fill_text, s)
        s = re.sub(r'\s(style|class)="[^"]*"', '', s)
        s = re.sub(r'</?(div|span|itemBody)[^>]*>', '', s)
        s = re.sub(r'<p\s*/>|<p>\s*</p>', '', s)
        q['gaps_html'] = s.strip()
        q['gaps_only_field'] = not plain(re.sub(r'<mark[^>]*>.*?</span>', '', s))  # alleen het invulvak, geen zin
        return q
    q['type'] = 'unknown'
    return q


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
        # onderaan in het vak, op een wit label: de kop van het vak (bv. 'Brand') blijft leesbaar
        cx, by = (c[0] + c[2]) / 2 * sx, c[3] * sy
        x0, y0 = int(cx - im.width / 2), int(by - im.height - 6 * sy)
        pad = int(4 * sx)
        from PIL import ImageDraw
        ImageDraw.Draw(base).rectangle([x0 - pad, y0 - pad, x0 + im.width + pad, y0 + im.height + pad], fill=(255, 255, 255, 235), outline=(29, 107, 47, 255), width=max(2, int(2 * sx)))
        base.alpha_composite(im, (x0, y0))
    buf = io.BytesIO()
    base.convert('RGB').save(buf, 'JPEG', quality=88)
    return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()


# ----------------------------------------------------------------------------- pagina's

def page_items(xaml, images, hidden_pages):
    root = parse_xaml(xaml)
    els = []
    for el in root.iter():
        t = local(el.tag)
        x = float(attr(el, 'Left', '0') or 0) if attr(el, 'Left') not in (None, 'NaN') else 0.0
        y = float(attr(el, 'Top', '0') or 0) if attr(el, 'Top') not in (None, 'NaN') else 0.0
        w = attr(el, 'Width', '0')
        w = float(w) if w not in (None, 'NaN', '') else 0.0
        hh = attr(el, 'Height', '0')
        hh = float(hh) if hh not in (None, 'NaN', '') else 0.0
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
            if src or link:
                els.append({'kind': 'image', 'x': x, 'y': y, 'w': w, 'h': hh, 'src': src, 'data': images.get(src, ''), 'link': link})
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
        if q.get('solution'):
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
    if not e.get('data'):
        return ''
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
    if k == 'video':
        name = os.path.splitext(os.path.basename(e.get('src') or ''))[0]
        return '<p class="note">[Video in de e-module%s]</p>' % (': ' + esc(name) if name else '')
    if k == 'quiz':
        return h_quiz(e)
    if k == 'aside':
        L = ''.join(h_el(x, popups) for x in e['left'])
        R = ''.join(h_el(x, popups) for x in e['right'])
        return '<div class="sp-aside"><div class="sp-text">%s</div><div class="sp-media">%s</div></div>' % (L, R)
    return ''


CSS_EXTRA = """
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
    popups = {}
    for pid, hp in hidden.items():
        src = files.get(hp['src'])
        if src:
            bl = [b for e in page_items(src, images, {}) if e['kind'] == 'text' for b in e['blocks']]
            popups[pid] = {'title': hp['title'], 'html': blocks_html(bl)}
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dump')
    ap.add_argument('--pdf')
    ap.add_argument('--model')
    a = ap.parse_args()
    dump = json.load(open(a.dump, encoding='utf-8'))
    ATT.data = {re.sub(r'\s+', ' ', k).strip(): v for k, v in (dump.get('attachments') or {}).items()}
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
