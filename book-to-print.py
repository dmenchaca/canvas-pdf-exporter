#!/usr/bin/env python3
"""Zet het door console-export.js gedownloade boek (Rise-blokken) om naar schone,
printbare HTML met eigen opmaak, en maakt er optioneel een PDF van met headless Chrome.

Gebruik:
  python3 book-to-print.py <boek.html> [--pdf uit.pdf] [--html uit.html]
"""
import re
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag

CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

CSS = """
@page { size: A4; margin: 20mm 26mm 22mm; }
@page { @bottom-left { content: "__CPE_TITLE__"; font: 9pt Helvetica, Arial, sans-serif; color: #777; } @bottom-right { content: counter(page) " / " counter(pages); font: 9pt Helvetica, Arial, sans-serif; color: #777; } }
* { box-sizing: border-box; }
html { font-size: 11pt; }
body { margin: 0; font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; color: #1a1a1a; line-height: 1.5; text-rendering: optimizeLegibility; }
p { margin: 0 0 .85em; orphans: 3; widows: 3; }
p:last-child { margin-bottom: 0; }
a { color: inherit; text-decoration: underline; }
img { max-width: 100%; height: auto; display: block; }
table { border-collapse: collapse; width: 100%; margin: .8em 0 1.4em; font-size: .95em; break-inside: avoid; font-variant-numeric: tabular-nums; }
th, td { border: 1px solid #cfcfcf; padding: .35em .6em; text-align: left; vertical-align: top; }
th { background: #eef2f5; }

.cover { break-after: page; display: flex; flex-direction: column; justify-content: center; min-height: 240mm; }
.cover .cover-kicker { font-size: 11pt; color: #bc1413; margin-bottom: .8em; }
.cover h1 { font-size: 28pt; line-height: 1.15; margin: 0 0 .6em; letter-spacing: -.01em; text-wrap: balance; }
.cover .toc { margin-top: 3em; }
.cover .toc h2 { font-size: 10pt; text-transform: uppercase; letter-spacing: .08em; color: #555; margin: 0 0 .6em; }
.cover .toc ol { margin: 0; padding-left: 1.4em; font-size: 12pt; line-height: 1.7; }

.chapter { break-before: page; }
.chapter-header { border-bottom: 1px solid #bc1413; padding-bottom: .6em; margin: 0 0 1.6em; break-inside: avoid; break-after: avoid; }
.chapter-header .kicker { font-size: 11pt; color: #bc1413; margin: 0 0 .35em; }
.chapter-header h1 { font-size: 20pt; line-height: 1.2; margin: 0; letter-spacing: -.01em; text-wrap: balance; }

h2.section { font-size: 14pt; color: #1a1a1a; margin: 2em 0 .6em; break-after: avoid; line-height: 1.25; text-wrap: balance; }
h2.band { font-size: 17pt; color: #1a1a1a; line-height: 1.25; padding: 0 0 .35em; border-bottom: 1px solid #c9c9c9; margin: 2.8em 0 1.1em; break-after: avoid; text-wrap: balance; }
h3 { font-size: 13pt; line-height: 1.3; margin: 1.5em 0 .5em; break-after: avoid; }
.block { margin: 0 0 1.5em; }
.block.keep { break-inside: avoid; }

ul.list, ol.list, .block ul, .block ol, .card ul, .card ol, .quiz .question ul, .quiz .question ol, dd ul, dd ol, .labels ul, .labels ol, .answer ul, .answer ol { margin: .5em 0 1.5em; padding-left: 1.5em; }
ul.list li, ol.list li, .block li, .card li, dd li, .labels ol li li, .answer li { margin: 0 0 .45em; }
.block li p, .card li p { margin: 0; }
ol.numbered { list-style: none; padding-left: 0; counter-reset: n; }
ol.numbered li { counter-increment: n; position: relative; padding-left: 2.3em; margin: 0 0 .7em; }
ol.numbered li::before { content: counter(n); position: absolute; left: 0; top: .05em; width: 1.6em; height: 1.6em; border-radius: 50%; background: #bc1413; color: #fff; font-weight: 700; font-size: .85em; text-align: center; line-height: 1.6; }

figure { margin: 1.2em 0 1.8em; break-inside: avoid; }
figure img { margin: 0 auto; max-height: 90mm; width: auto; }
/* Brede schema's niet minuscuul laten: hele kolombreedte. */
figure.wide img { width: 100%; max-height: 90mm; object-fit: contain; }
/* Hotspot- en meterafbeeldingen: hooguit een halve pagina hoog. */
figure.graphic img { max-height: 80mm; width: auto; }
/* Storyline: veel fotodia's; iets lager zodat twee figuren op een pagina passen. */
.sl figure img { max-height: 72mm; }
figcaption { font-size: 9.5pt; line-height: 1.4; color: #666; margin-top: .6em; text-align: center; }
.image-aside { display: flex; gap: 1.4em; align-items: flex-start; break-inside: avoid; margin: 0 0 1.6em; }
.image-aside .text { flex: 1 1 55%; }
.image-aside figure { flex: 0 0 42%; margin: 0; }
.image-aside figure img { width: 100%; max-height: 70mm; object-fit: contain; }

.card { border: 1px solid #d9d9d9; border-radius: 6px; padding: 1em 1.3em 1.1em; margin: 0 0 1.3em; break-inside: avoid; background: #fff; }
.card .card-number { font-size: 9pt; line-height: 1.4; text-transform: uppercase; letter-spacing: .08em; color: #666; font-weight: 700; margin-bottom: .2em; }
.card h3 { margin: 0 0 .7em; font-size: 13pt; }
.card figure { margin: .6em 0 1em; }
.card figure img { max-height: 62mm; }
.process-intro { background: #f4f6f8; border: none; }

.quiz { break-inside: avoid; border: 1px solid #b9cdd8; border-radius: 6px; padding: 1.1em 1.3em 1.2em; margin: 2em 0 1.8em; background: #f7fafc; }
.quiz .quiz-label { font-size: 9pt; line-height: 1.4; text-transform: uppercase; letter-spacing: .1em; color: #2f6f8f; font-weight: 700; margin-bottom: .5em; }
.quiz .question { font-weight: 700; margin-bottom: .7em; }
.quiz .hint { font-style: italic; color: #555; margin-bottom: .5em; }
.quiz figure img { max-height: 70mm; margin: 0; }
.quiz ul.options { list-style: none; padding: 0; margin: .5em 0 0; }
.quiz ul.options li { position: relative; padding-left: 1.8em; margin: 0 0 .7em; break-inside: avoid; }
.quiz ul.options li::before { content: ""; position: absolute; left: 0; top: .2em; width: 1em; height: 1em; border: 1.5px solid #6a7b85; border-radius: 3px; background: #fff; }
.quiz ul.options.radio li::before { border-radius: 50%; }
body.forms .quiz ul.options li::before { border-color: transparent; background: transparent; }
.quiz .matching { width: 100%; }
.quiz .matching td { width: 50%; }
.quiz .matching .ok { color: #1d6b2f; font-weight: 700; margin-left: .4em; }
.quiz table.sorting th { width: 50%; }
.quiz ul.sorted { list-style: none; margin: 0; padding: 0; }
.quiz ul.sorted li { margin: 0 0 .5em; padding-left: 1.4em; position: relative; }
.quiz ul.sorted li::before { content: "✓"; color: #1d6b2f; font-weight: 700; position: absolute; left: 0; }
.quiz ul.options li .cpe-mark { position: absolute; left: 0; top: 0; color: #fff; opacity: .02; font-size: 2pt; letter-spacing: -.2em; }
.quiz ul.options li.correct { font-weight: 700; color: #1d6b2f; }
.quiz ul.options li.correct p { display: inline; }
.quiz .answer { margin-top: .9em; padding-top: .7em; border-top: 1px dashed #b9c6cd; color: #1a1a1a; }
.quiz .answer p { display: inline; }
.quiz .answer-label { font-weight: 700; color: #2f6f8f; }

.labels { margin: 1em 0 .6em; padding-left: 1.4em; }
.labels li { margin: 0 0 1.6em; }
.labels .label-title { font-weight: 700; margin-bottom: .25em; break-after: avoid; }

dl.accordion { margin: .8em 0 1.6em; }
dl.accordion dt { font-weight: 700; margin: 1.3em 0 .35em; break-after: avoid; }
dl.accordion dd { margin: 0 0 .6em 0; padding: .1em 0 .1em 1em; border-left: 3px solid #e2e2e2; break-inside: avoid; }

.note { font-style: italic; color: #666; }
.callout { display: flex; gap: 1.1em; align-items: flex-start; background: #f6ecea; border: 1px solid #bc1413; border-radius: 4px; padding: 1.1em 1.3em; margin: 1.4em 0 1.8em; break-inside: avoid; }
.callout .callout-icon { flex: 0 0 auto; width: 1.7em; height: 1.7em; border: 2px solid #bc1413; border-radius: 50%; color: #bc1413; font: 700 1em/1.55em Georgia, "Times New Roman", serif; text-align: center; }
.callout .callout-body { flex: 1 1 auto; }
.callout .callout-body p:last-child { margin-bottom: 0; }
.callout.quote { background: #f7f7f7; border-color: #cfcfcf; font-style: italic; }
"""


def norm(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


def clean_rich(node, soup):
    """Geeft de rijke inhoud (p, strong, ul, table...) van een .fr-view terug, opgeschoond."""
    if node is None:
        return ''
    frag = BeautifulSoup(str(node), 'html.parser')
    for bad in frag.select('svg, button, input, script, style, .visually-hidden-always'):
        bad.decompose()
    for br in frag.select('br.break-when-trailing'):
        br.replace_with(soup.new_tag('br'))
    for el in frag.find_all(True):
        for attr in list(el.attrs):
            if attr not in ('href', 'src', 'alt', 'colspan', 'rowspan'):
                del el.attrs[attr]
    # Lege omhulsels (div/span) platslaan
    for el in frag.find_all(['div', 'span']):
        el.unwrap()
    html = str(frag)
    html = re.sub(r'(<br\s*/?>\s*)+</p>', '</p>', html)
    html = re.sub(r'<p>\s*(&nbsp;|\s)*</p>', '', html)
    return html.strip()


def rich_of(block, selector, soup):
    parts = []
    for fv in block.select(selector):
        parts.append(clean_rich(fv, soup))
    return '\n'.join(p for p in parts if p)


def image_html(img):
    if img is None:
        return ''
    return '<img src="%s" alt="%s">' % (img.get('src', ''), norm(img.get('alt', '')))


def figure_class(img):
    """Brede schema's (verhouding >= 2.2) vullen de kolom; overige blijven op natuurlijke grootte."""
    try:
        w, h = float(img.get('width') or 0), float(img.get('height') or 0)
    except ValueError:
        w = h = 0
    return 'wide' if (w and h and w / h >= 2.2) else ''


def bg_color(el):
    m = re.search(r'--color-background:\s*(#[0-9a-fA-F]{3,6})', el.get('style', '') or '')
    return (m.group(1).lower() if m else '#ffffff')


def convert_block(wrapper, soup):
    inner = wrapper.select_one('[class*="block-"], [class*="blocks-"]')
    if inner is None:
        return ''
    cls = ' '.join(inner.get('class', []))
    # Eerste klasse die met block-/blocks- begint (bv. block-labeled-graphic, blocks-accordion).
    kind = next((c for c in cls.split() if re.match(r'^blocks?-[a-z-]+$', c) and '__' not in c and '--' not in c), '')
    out = []

    if kind == 'block-text':
        heading = inner.select_one('.block-text__heading')
        heading_txt = norm(heading.get_text(' ')) if heading else ''
        body_parts = []
        for col in inner.select('.block-text__col'):
            if col.select_one('.block-text__heading'):
                continue
            fv = col.select_one('.fr-view') or col
            body_parts.append(clean_rich(fv, soup))
        body = '\n'.join(p for p in body_parts if p)
        colored = bg_color(inner) not in ('#ffffff', '#fff')
        if heading_txt and not body:
            out.append('<h2 class="%s">%s</h2>' % ('band' if colored else 'section', heading_txt))
        else:
            if heading_txt:
                out.append('<h2 class="section">%s</h2>' % heading_txt)
            if body:
                out.append('<div class="block">%s</div>' % body)

    elif kind == 'block-list':
        numbered = 'numbered' in cls
        items = []
        for li in inner.select('li.block-list__item'):
            fv = li.select_one('.block-list__content .fr-view') or li.select_one('.block-list__content')
            items.append('<li>%s</li>' % clean_rich(fv, soup))
        tag = 'ol' if numbered else 'ul'
        out.append('<%s class="list %s">%s</%s>' % (tag, 'numbered' if numbered else '', ''.join(items), tag))

    elif kind == 'block-image':
        img = inner.select_one('img')
        cap = inner.select_one('figcaption')
        text = inner.select_one('.block-image__text .fr-view')
        fig = '<figure class="%s">%s%s</figure>' % (figure_class(img), image_html(img), ('<figcaption>%s</figcaption>' % norm(cap.get_text(' '))) if cap else '')
        if text and norm(text.get_text()) and figure_class(img) != 'wide':
            out.append('<div class="image-aside"><div class="text">%s</div>%s</div>' % (clean_rich(text, soup), fig))
        elif text and norm(text.get_text()):
            # Breed schema naast tekst wordt onleesbaar klein: tekst boven, schema op volle breedte.
            out.append('<div class="block">%s</div>%s' % (clean_rich(text, soup), fig))
        else:
            out.append(fig)

    elif kind == 'block-divider':
        txt = norm(inner.get_text(' '))
        if txt:
            out.append('<h2 class="band">%s</h2>' % txt)

    elif kind == 'block-process':
        for card in inner.select('.block-process-card'):
            num = card.select_one('.block-process-card__number')
            title = card.select_one('.block-process-card__title')
            img = card.select_one('.block-process-card__media img')
            desc = card.select_one('.block-process-card__description .fr-view')
            intro = 'intro' in ' '.join(card.get('class', []))
            out.append('<div class="card %s">%s%s%s%s</div>' % (
                'process-intro' if intro else '',
                ('<div class="card-number">%s</div>' % norm(num.get_text())) if num else '',
                ('<h3>%s</h3>' % norm(title.get_text(' '))) if title else '',
                ('<figure>%s</figure>' % image_html(img)) if img else '',
                clean_rich(desc, soup) if desc else ''))

    elif kind == 'block-labeled-graphic':
        img = inner.select_one('img')
        items = []
        for li in inner.select('ol.cpe-labels > li'):
            t = li.select_one('.cpe-label-title')
            d = li.select_one('.fr-view')
            items.append('<li><div class="label-title">%s</div>%s</li>' % (norm(t.get_text()) if t else '', clean_rich(d, soup) if d else ''))
        out.append('<figure class="graphic">%s</figure>' % image_html(img))
        if items:
            out.append('<ol class="labels">%s</ol>' % ''.join(items))

    elif kind == 'block-statement':
        # Opmerking/citaat in een kader (Rise 'statement'): als kader met icoon.
        q = inner.select_one('.block-statement__quote .fr-view') or inner.select_one('.block-statement__quote')
        if q is not None and norm(q.get_text(' ')):
            note = 'block-statement--note' in cls
            out.append('<div class="callout%s">%s<div class="callout-body">%s</div></div>' % (
                '' if note else ' quote', '<div class="callout-icon">i</div>' if note else '', clean_rich(q, soup)))

    elif kind == 'blocks-tabs':
        # Tabbladen: elke tab als kopje met zijn eigen inhoud eronder (in de speler zie je er één tegelijk).
        heads = inner.select('.blocks-tabs__header-item')
        panels = inner.select('.blocks-tabs__content-item')
        items = []
        for i, h in enumerate(heads):
            t = h.select_one('.fr-view') or h
            panel = panels[i] if i < len(panels) else None
            d = (panel.select_one('.fr-view') if panel else None) or panel
            items.append('<dt>%s</dt><dd>%s</dd>' % (norm(t.get_text(' ')), clean_rich(d, soup) if d else ''))
        out.append('<dl class="accordion tabs">%s</dl>' % ''.join(items))

    elif kind == 'blocks-accordion':
        items = []
        for item in inner.select('.blocks-accordion__item'):
            t = item.select_one('.blocks-accordion__title')
            d = item.select_one('.blocks-accordion__content .fr-view') or item.select_one('.blocks-accordion__content')
            items.append('<dt>%s</dt><dd>%s</dd>' % (norm(t.get_text(' ')) if t else '', clean_rich(d, soup) if d else ''))
        out.append('<dl class="accordion">%s</dl>' % ''.join(items))

    elif kind == 'block-cpe-ready':
        # Al door rise.js omgezet (bijv. sorteeroefening uit de cursusdata): ongewijzigd overnemen.
        out.append(inner.decode_contents())

    elif kind == 'block-knowledge':
        for card in inner.select('.quiz-card'):
            title = card.select_one('.quiz-card__title')
            img = card.select_one('.quiz-card__media img')
            hint = None
            opts = []
            kind_q = 'radio'
            for lab in card.select('.quiz-multiple-choice-option, .quiz-multiple-response-option'):
                if 'quiz-multiple-response-option' in ' '.join(lab.get('class', [])):
                    kind_q = 'check'
                txt = lab.select_one('.fr-view') or lab
                opts.append((norm(txt.get_text(' ')), clean_rich(txt, soup)))
            # Matching-vragen: twee kolommen (links = begrippen, rechts = antwoorden, in willekeurige volgorde)
            match_rows = []
            cols = card.select('.matching-interaction-col')
            holder = card if card.get('data-cpe-pairs') else card.select_one('[data-cpe-pairs]')
            pairs_attr = holder.get('data-cpe-pairs') if holder else None
            solved_pairs = []
            if pairs_attr:
                try:
                    import json
                    solved_pairs = json.loads(pairs_attr)
                except ValueError:
                    solved_pairs = []
            if solved_pairs and not opts:
                for l, r in solved_pairs:
                    match_rows.append('<tr><td>%s</td><td>%s <span class="ok">✓</span></td></tr>' % (l, r))
            elif len(cols) >= 2 and not opts:
                left = [norm(x.get_text(' ')) for x in cols[0].select('.matching-interaction-piece-content')]
                right = [norm(x.get_text(' ')) for x in cols[1].select('.matching-interaction-piece-content')]
                for i in range(max(len(left), len(right))):
                    match_rows.append('<tr><td>%s</td><td>%s</td></tr>' % (left[i] if i < len(left) else '', right[i] if i < len(right) else ''))
            correct = card.get('data-cpe-correct')
            correct = set(int(x) for x in correct.split(',') if x.strip().isdigit()) if correct else set()
            # Onzichtbare unieke marker per optie: daarop zoekt add_form_fields de plek van het vakje (optietekst kan ook elders voorkomen).
            global _OPT_SEQ
            marks = []
            for i in range(len(opts)):
                _OPT_SEQ += 1
                marks.append('cpeopt%04d' % _OPT_SEQ)
            opts = ['<li data-opt="%s" data-mark="%s"%s><span class="cpe-mark">%s</span>%s</li>' % (t.replace('"', '&quot;'), m, ' data-correct="1" class="correct"' if i in correct else '', m, h)
                    for i, ((t, h), m) in enumerate(zip(opts, marks))]
            title_html = clean_rich(title.select_one('.fr-view') or title, soup) if title else ''
            # Alleen de toelichting bij een goed antwoord; de 'fout'-tekst is voor lezers niet zinvol.
            feedback = card.get('data-cpe-feedback') or ''
            body = '<div class="quiz-label">Vraag</div>'
            body += '<div class="question">%s</div>' % title_html
            if img:
                body += '<figure>%s</figure>' % image_html(img)
            if opts:
                body += '<ul class="options %s">%s</ul>' % (kind_q, ''.join(opts))
            elif match_rows and solved_pairs:
                body += '<table class="matching"><tr><th>Begrip</th><th>Juiste koppeling</th></tr>%s</table>' % ''.join(match_rows)
            elif match_rows:
                body += '<p class="hint">Koppel elk begrip aan het juiste antwoord (antwoorden staan in willekeurige volgorde).</p>'
                body += '<table class="matching"><tr><th>Begrippen</th><th>Antwoorden (willekeurige volgorde)</th></tr>%s</table>' % ''.join(match_rows)
            else:
                inter = card.select_one('.quiz-card__interactive')
                if inter:
                    body += '<div>%s</div>' % clean_rich(inter, soup)
            if (correct or solved_pairs) and feedback:
                # Geen herhaling van de aangevinkte opties: alleen de toelichting van Rise.
                body += '<div class="answer"><span class="answer-label">Toelichting:</span> %s</div>' % feedback
            out.append('<div class="quiz">%s</div>' % body)

    else:
        # Onbekend bloktype: alle rijke tekst en afbeeldingen in documentvolgorde.
        parts = []
        for el in inner.find_all(['img', 'div']):
            if el.name == 'img':
                parts.append('<figure>%s</figure>' % image_html(el))
            elif 'fr-view' in el.get('class', []) and not el.find_parent(class_='fr-view'):
                parts.append(clean_rich(el, soup))
        if parts:
            out.append('<div class="block">%s</div>' % '\n'.join(parts))
        else:
            txt = norm(inner.get_text(' '))
            if txt:
                out.append('<div class="block">%s</div>' % txt)

    return '\n'.join(out)


def convert(src_html):
    soup = BeautifulSoup(src_html, 'html.parser')
    title = norm(soup.title.get_text()) if soup.title else 'Cursus'
    chapters = soup.select('.cpe-chapter')
    toc = []
    body = []
    for i, ch in enumerate(chapters, 1):
        h = ch.select_one('.cpe-chapter-header h1')
        ch_title = norm(h.get_text()) if h else 'Hoofdstuk %d' % i
        toc.append('<li>%s</li>' % ch_title)
        blocks = []
        for w in ch.select('.blocks-lesson > .noOutline'):
            html = convert_block(w, soup)
            if html:
                blocks.append(html)
        joined = '\n'.join(blocks)
        joined = re.sub(r'<h2 class="band">\s*Vraag\s*</h2>\s*(?=<div class="quiz">)', '', joined)
        body.append('<section class="chapter"><div class="chapter-header"><p class="kicker">Hoofdstuk %d</p><h1>%s</h1></div>%s</section>' %
                    (i, ch_title, joined))
    cover = ('<section class="cover"><p class="cover-kicker">E-module</p><h1>%s</h1>'
             '<div class="toc"><h2>Inhoud</h2><ol>%s</ol></div></section>') % (title, ''.join(toc))
    css = CSS.replace('__CPE_TITLE__', title.replace('\\', '\\\\').replace('"', '\\"'))
    return ('<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>%s</title><style>%s</style></head>'
            '<body class="forms">%s%s</body></html>') % (title, css, cover, '\n'.join(body))


_OPT_SEQ = 0


def add_form_fields(pdf_path, html):
    """Zet echte (invulbare) selectievakjes/keuzerondjes op de plek van elke quiz-optie."""
    import fitz  # PyMuPDF
    opts = re.findall(r'<ul class="options (radio|check)">(.*?)</ul>', html, flags=re.S)
    groups = []
    for kind, inner in opts:
        labels = re.findall(r'<li data-opt="[^"]*" data-mark="([^"]*)"( data-correct="1")?', inner)
        groups.append((kind, [(m, bool(c)) for m, c in labels]))
    doc = fitz.open(pdf_path)
    n = 0
    cur_page, cur_y = 0, -1.0  # opties staan in documentvolgorde: alleen vooruit zoeken
    for gi, (kind, labels) in enumerate(groups):
        for li, (label, is_correct) in enumerate(labels):
            needle = label  # unieke marker (onzichtbare tekst) aan het begin van de optie
            for page in doc:
                if page.number < cur_page:
                    continue
                hits = page.search_for(needle)
                if not hits:
                    continue
                r = min(hits, key=lambda x: x.y0)
                cur_page, cur_y = page.number, r.y0
                size = 11.0  # 1em bij 11pt, zelfde plek als het CSS-vakje (left 0, top .2em)
                box = fitz.Rect(r.x0, r.y0 + 0.2 * size, r.x0 + size, r.y0 + 1.2 * size)
                w = fitz.Widget()
                # Overal selectievakjes: keuzerondje-groepen gaan stuk in PyMuPDF (bad xref).
                w.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
                w.field_name = 'vraag_%d_optie_%d' % (gi, li)
                w.rect = box
                w.border_color = (0.42, 0.48, 0.52)
                w.border_width = 1
                w.fill_color = (1, 1, 1)
                w.text_color = (0.1, 0.1, 0.1)
                if is_correct:
                    w.field_value = True
                    w.border_color = (0.11, 0.42, 0.18)
                page.add_widget(w)
                n += 1
                break
    tmp = str(pdf_path) + '.tmp'
    doc.save(tmp, garbage=1, deflate=True)  # incrementeel bewaren lukt niet op Chrome-PDF's (xref-stream)
    doc.close()
    Path(tmp).replace(pdf_path)
    return n


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)
    src = Path(args[0])
    out_html = Path(args[args.index('--html') + 1]) if '--html' in args else src.with_name(src.stem + '-print.html')
    out_pdf = Path(args[args.index('--pdf') + 1]) if '--pdf' in args else None
    if '--ready' in args:
        # Invoer is al een printdocument (bijv. van console-storyline.js): alleen afdrukken + velden.
        html = src.read_text(encoding='utf-8')
    else:
        html = convert(src.read_text(encoding='utf-8'))
    out_html.write_text(html, encoding='utf-8')
    print('HTML:', out_html, len(html), 'bytes')
    if out_pdf:
        cmd = [CHROME, '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
               '--print-to-pdf=' + str(out_pdf), '--virtual-time-budget=10000', 'file://' + str(out_html.resolve())]
        subprocess.run(cmd, check=True, capture_output=True)
        try:
            n = add_form_fields(str(out_pdf), html)
            print('Invulbare velden:', n)
        except Exception as e:  # PyMuPDF ontbreekt of PDF afwijkend: dan zonder formulier
            print('Geen formuliervelden toegevoegd:', e)
        print('PDF:', out_pdf, out_pdf.stat().st_size, 'bytes')


if __name__ == '__main__':
    main()
