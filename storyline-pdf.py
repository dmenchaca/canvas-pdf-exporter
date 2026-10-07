#!/usr/bin/env python3
"""Storyline-modus (mode C): bouwt een PDF rechtstreeks uit de cursusdata van een
Articulate Storyline-module.

Invoer: een .sl.json-dump (gemaakt in het spelertabblad met console-storyline.js /
de extensie), met data.js, frame.js, alle dia-bestanden en de afbeeldingen als data-URI's.

Werkwijze:
  1. extractie   -> per dia een lijst 'entries' (tekstvakken, afbeeldingen, invoervelden)
  2. classificatie -> documentmodel: kop, alinea, lijst, figuur, formule, vraag (met juiste
                      antwoorden en toelichting), notitie
  3. opmaak      -> ReportLab (platypus) met eigen paginaregels: koppen blijven bij hun tekst,
                    vraagkaders splitsen netjes, echte PDF-vinkvakjes (AcroForm).

Gebruik:  python3 storyline-pdf.py <cursus>.sl.json [--pdf uit.pdf] [--report]
"""
import argparse
import base64
import datetime
import html
import io
import json
import os
import re
import sys
from collections import Counter

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, CondPageBreak, Flowable, Frame, Image,
                                KeepTogether, ListFlowable, ListItem, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

# ----------------------------------------------------------------------------- helpers

WS = re.compile(r'\s+')


def norm(s):
    return WS.sub(' ', (s or '')).strip()


def decode(s):
    """HTML-entiteiten (frame.js bevat bijv. &apos;) en Storyline-escapes (^%^) opschonen."""
    s = html.unescape(s or '')
    s = s.replace('^%^', '%')
    return s


def esc(s):
    return html.escape(s or '', quote=False)


BULLET_RE = re.compile(r'^\s*[▪•●■◦‣\-–]\s*\t?\s*')
OPERATOR_RE = re.compile(r'^\s*[=+\-–×x:/]\s*$')
NAV_WORD_RE = re.compile(r'^(menu|volgende|vorige|terug|next|previous|back|sluiten|close|verder|start|submit|'
                         r'verzenden|indienen|ok|oké|afspelen|play|pauze|pause|opnieuw|herhalen|replay|doorgaan|'
                         r'antwoord indienen|controleer|controleren|begrippenlijst|bronnenlijst|bronnen|feedback)$', re.I)
# Zinnen die alleen over het navigeren in de speler gaan (in een PDF zinloos).
NAV_SENTENCE_RE = re.compile(
    r'^(je hebt (het onderdeel|dit onderdeel|dit hoofdstuk|deze e-module|de e-module)\b.*\b(afgerond|doorlopen)'
    r'|klik op (de knop|de pijl|volgende|vorige)\b.*\b(keuzemenu|menu|hoofdmenu|te gaan|af te ronden|verder)'
    r'|zodra je alle hoofdstukken.*\bkeuzemenu\b.*'
    r'|ga (terug )?naar (het )?(keuzemenu|hoofdmenu|menu)\b'
    r'|je kunt (de|deze) e-module (hieronder |nu )?afsluiten'
    r'|(of )?je kunt (het|dit) (scherm|tabblad)( in je browser)? (nu )?sluiten'
    r'|je bent aan het (eind|einde) van (deze|de) e-module gekomen'
    r'|e-module afsluiten'
    r'|beweeg je muis over\b.*'
    r'|klik op (de|het) (begrip|begrippen|woord|woorden|rode woorden|onderstreepte woorden?)\b.*\bvoor (meer )?(uitleg|informatie|voorbeelden)\.?)[.!]?$', re.I)


def is_nav_only(text):
    sentences = [x for x in re.split(r'(?<=[.!?])\s+', norm(text)) if x]
    return bool(sentences) and all(NAV_SENTENCE_RE.match(x) for x in sentences)


DOTS_RE = re.compile(r'^[\s.…_\-]+$')
LABEL_PREFIX_RE = re.compile(r'^(invulvak|sleepitem|picture|rectangle|oval|hotspot|freeform|shape|image|afbeelding|drop|drag|item)\s*\d*\s*[-:]\s*', re.I)
TUTORIAL_RE = re.compile(r'(deze knop|deze buttons?|met deze knoppen|navigeren|navigatie|via het menu|sluit de uitleg|'
                         r'klik op de knop|klik hier|hier zie je waar je)', re.I)
FEEDBACK_LEAD_RE = re.compile(r'^\s*(dat is (goed|juist|correct)|dat klopt|juist|correct|goed zo|goed gedaan|prima|klopt|'
                              r'helemaal goed|uitstekend|helaas[, ]+dat is (fout|niet juist|onjuist)|helaas|onjuist|fout|'
                              r'dat klopt niet|niet goed|jammer)[!.,]*\s*', re.I)
FEEDBACK_ONLY_RE = re.compile(r'^\s*(juist|onjuist|correct|incorrect|goed|fout)\s*[!.]*\s*$', re.I)
SKIP_SLIDE_TITLE_RE = re.compile(r'^(menu|keuzemenu|hoofdmenu|results? slide|resultaten|hervatten|warning|blank)$', re.I)
INSTRUCTION_RE = re.compile(r'^(kies|klik|vul|sleep|selecteer|geef|beantwoord|zet|maak|bekijk|lees|koppel|plaats|'
                            r'welke|welk|wat|waar|hoe)\b', re.I)
CREDIT_RE = re.compile(r'^\s*(©|\(c\)|bron\s*:|foto\s*:|beeld\s*:|afbeelding\s*:)', re.I)
GENERIC_TITLE_RE = re.compile(r'^(nog niet|tekst|slide|dia|hoofdstuk|titel|welkom|menu|einde|meerkeuze|vraag|quiz|'
                              r'afbeelding|video|intro$|\d|$)|\+', re.I)
HOTSPOT_LABEL_RE = re.compile(r'^j?(on)?juist\s*\d[\d\s]*$', re.I)
END_RE = re.compile(r'^einde (hoofdstuk|module|e-module|van (dit|het) hoofdstuk)', re.I)


# ----------------------------------------------------------------------------- extractie

class Course:
    def __init__(self, dump):
        self.title = norm(decode(dump.get('title') or 'Cursus'))
        self.data = dump['data']
        self.frame = dump.get('frame') or {}
        self.slides = dump['slides']
        self.images = dump.get('images') or {}
        self.asset_by_id = {a.get('id'): a for a in (self.data.get('assetLib') or [])}
        self.meta = {}
        # Tekstvariabelen met hun startwaarde (bv. HFDSTKtitel_03 = 'Handelingsperspectieven'): de hoofdstuktitel
        # op de verdeeldia is zo'n variabele.
        self.variables = {v.get('name'): v.get('value') for v in (dump.get('data') or {}).get('variables') or []
                          if v.get('type') == 'string' and isinstance(v.get('value'), str)}
        self.order = []
        for sc in self.data.get('scenes') or []:
            for s in sc.get('slides') or []:
                sid = str(s.get('id') or '').split('.')[-1]
                if not sid:
                    continue
                m = dict(s)
                m['sceneId'] = str(sc.get('id'))
                self.meta[sid] = m
                self.order.append(sid)
        self.entry_scene = str(self.data.get('entryPoint') or '').replace('_player.', '').split('.')[0]
        # Versieregel van de titeldia ('Versie 1 / september 2022') voor op het omslag.
        self.version = ''
        for sid in self.order:
            if self.version or not re.search(r'titel|welkom', self.meta.get(sid, {}).get('title') or '', re.I):
                continue
            for L in (self.slides.get(sid) or {}).get('slideLayers') or []:
                for it in self.flatten(L):
                    for blk in self.text_blocks(it['ob']) or []:
                        t = norm(blk['text'])
                        if not self.version and re.match(r'^versie\b', t, re.I) and len(t) <= 50:
                            self.version = t
        self._chrome()

    # -- dia-objecten plat maken (groepen tellen hun eigen offset op)
    def flatten(self, container, ox=0, oy=0, acc=None, top=None):
        acc = [] if acc is None else acc
        for ob in container.get('objects') or []:
            x = ox + (ob.get('xPos') or 0)
            y = oy + (ob.get('yPos') or 0)
            t = top or (x, y, ob.get('id') if ob.get('objects') else None, ob.get('accType') if ob.get('objects') else None)
            acc.append({'ob': ob, 'x': x, 'y': y, 'gx': t[0], 'gy': t[1], 'gid': t[2], 'gacc': t[3]})
            if ob.get('objects'):
                self.flatten(ob, x, y, acc, t)
        return acc

    @staticmethod
    def text_blocks(ob):
        tl = ob.get('textLib') or []
        item = next((t for t in tl if t.get('vartext') and t.get('id') == '01'), None) or \
            next((t for t in tl if t.get('vartext')), None)
        if not item:
            return None
        vt = item['vartext']
        base = ((vt.get('defaultBlockStyle') or {}).get('baseSpanStyle') or {}).get('fontSize') or 0
        blocks = []
        dstyle = vt.get('defaultBlockStyle') or {}
        prev_after = None  # spacingAfter van het vorige niet-lege blok; None na een lege regel of aan het begin
        for b in vt.get('blocks') or []:
            spans = []
            for sp in b.get('spans') or []:
                st = sp.get('style') or {}
                spans.append({'text': decode(sp.get('text') or ''), 'bold': bool(st.get('fontIsBold')),
                              'size': st.get('fontSize') or base})
            text = ''.join(s['text'] for s in spans)
            style = b.get('style') or {}
            if not norm(text):
                prev_after = None
                continue
            before = style.get('spacingBefore', dstyle.get('spacingBefore', 0)) or 0
            lst = (style.get('listStyle') or {}).get('listType') or 'none'
            # 'tight': geen lege regel en geen alinea-afstand t.o.v. het vorige blok -> op de dia gewoon de volgende regel
            blocks.append({'spans': spans, 'text': text, 'size': max([base] + [s['size'] for s in spans]),
                           'list': lst, 'level': style.get('listLevel') or 0,
                           'tight': prev_after == 0 and before == 0})
            prev_after = style.get('spacingAfter', dstyle.get('spacingAfter', 0)) or 0
        return blocks or None

    def same_picture(self, u1, u2):
        """Twee afbeeldingen die er (nagenoeg) hetzelfde uitzien, ook als het bestand verschilt."""
        try:
            import base64, io
            from PIL import Image, ImageChops, ImageStat
            ims = []
            for u in (u1, u2):
                d = self.images.get(u) or ''
                if not d.startswith('data:image/') or 'svg' in d[:30]:
                    return False
                ims.append(Image.open(io.BytesIO(base64.b64decode(d.split(',', 1)[1]))).convert('L').resize((64, 40)))
            return sum(ImageStat.Stat(ImageChops.difference(*ims)).mean) < 12
        except Exception:
            return False

    def divider_title(self, sid):
        """Hoofdstuktitel zoals op de verdeeldia ('3 Handelingsperspectieven'), als die uit een spelervariabele komt."""
        sl = self.slides.get(sid)
        if not sl or not sl.get('slideLayers'):
            return ''
        for it in self.flatten(sl['slideLayers'][0]):
            for b in self.text_blocks(it['ob']) or []:
                m = re.fullmatch(r'\s*%_player\.(HFDSTKtitel_\d+)%\s*', b['text'])
                if m and b['size'] >= 40 and norm(self.variables.get(m.group(1)) or ''):
                    return norm(self.variables[m.group(1)])
        # Anders: de grote titeltekst naast het grote hoofdstuknummer ('2' + 'Elektriciteit' / 'en risico's').
        big = [(it['y'], it['x'], b) for it in self.flatten(sl['slideLayers'][0]) for b in self.text_blocks(it['ob']) or []]
        if not any(b['size'] >= 150 and re.fullmatch(r'\s*\d+\s*', b['text']) for _, _, b in big):
            return ''
        lines = [norm(b['text']) for _, _, b in sorted(big, key=lambda t: (t[0], t[1]))
                 if b['size'] >= 60 and not re.fullmatch(r'\s*\d+\s*', b['text']) and '%' not in b['text'] and norm(b['text'])]
        out = ''
        for ln in lines:
            out = out[:-1] + ln if out.endswith('-') else (out + ' ' + ln).strip()  # 'elektriciteits-' + 'netwerk'
        return out

    def image_url(self, ob):
        il = ob.get('imagelib') or []
        if il:
            a = self.asset_by_id.get(il[0].get('assetId'))
            if a and (a.get('url') or a.get('mobileUrl')):
                return a.get('url') or a.get('mobileUrl')
            return il[0].get('url') or ''
        d = ob.get('data') or {}
        idata = d.get('imagedata')
        if idata:
            a = self.asset_by_id.get(idata.get('assetId'))
            if a and a.get('url'):
                return a['url']
            if idata.get('url'):
                return idata['url']
        h5 = d.get('html5data')
        if h5 and h5.get('url') and ob.get('kind') == 'image':
            return 'story_content/' + h5['url']
        return ''

    @staticmethod
    def decorative(ob, url, ignore_size=False):
        w = ob.get('width') or 0
        h = ob.get('height') or 0
        if not ignore_size and (w < 240 or h < 150):
            return True
        if ignore_size and (w < 50 or h < 50):
            return True
        if w / max(h, 1) > 4.5 or h / max(w, 1) > 4.5:
            return True
        if re.search(r'_[0-9A-F]{6}_[0-9A-F]{6}\.', url, re.I):
            return True
        if re.search(r'zoomIcon', url, re.I):
            return True
        # Knoppen/vormen met een vulling heten ook 'ShapeXXXX.png', maar foto's (kind 'image') ook: die houden.
        if ob.get('kind') != 'image' and re.search(r'/Shape[0-9A-Za-z]+\.png$', url, re.I):
            return True
        alt = ((ob.get('imagelib') or [{}])[0]).get('altText') or ''
        if re.search(r'transparent|arrow|pijl|icon|button|knop|driehoek|triangle|achtergrond|background', alt, re.I):
            return True
        return False

    # -- vaste onderdelen (voortgang, menu, kruimelpad) herkennen door frequentie
    def _chrome(self):
        layer_count = Counter()
        text_count = Counter()
        n = 0
        for sid in self.order:
            sl = self.slides.get(sid)
            if not sl:
                continue
            n += 1
            seen = set()
            for L in sl.get('slideLayers') or []:
                items = self.flatten(L)
                texts = [norm(' '.join(b['text'] for b in bl)) for bl in (self.text_blocks(it['ob']) for it in items) if bl]
                imgs = [u for u in (self.image_url(it['ob']) for it in items) if u]
                if not L.get('isBaseLayer') and (texts or imgs):
                    layer_count['|'.join(texts + imgs)] += 1
        self.layer_count = layer_count
        self.slide_count = max(n, 1)
        # Tweede ronde: hoe vaak komt een tekst voor buiten de vaste spelerlagen (menu, voortgang)?
        # Anders lijkt bv. de diakop 'Samenvatting' chrome omdat het woord in het menu op elke dia staat.
        for sid in self.order:
            sl = self.slides.get(sid)
            if not sl:
                continue
            seen = set()
            for L in sl.get('slideLayers') or []:
                if self.is_chrome_layer(L):
                    continue
                for bl in (self.text_blocks(it['ob']) for it in self.flatten(L)):
                    if not bl:
                        continue
                    t = norm(' '.join(b['text'] for b in bl))
                    if t not in seen:
                        seen.add(t)
                        text_count[t] += 1
        self.text_count = text_count

    def layer_signature(self, L):
        items = self.flatten(L)
        texts = [norm(' '.join(b['text'] for b in bl)) for bl in (self.text_blocks(it['ob']) for it in items) if bl]
        imgs = [u for u in (self.image_url(it['ob']) for it in items) if u]
        return texts, imgs

    def is_chrome_layer(self, L):
        if L.get('isBaseLayer'):
            return False
        texts, imgs = self.layer_signature(L)
        if not texts and not imgs:
            return True
        c = self.layer_count.get('|'.join(texts + imgs), 0)
        return c >= 3 and c >= self.slide_count * 0.15

    def is_tutorial_layer(self, L):
        """Uitleg van de spelerknoppen (pop-up met pijlen naar de navigatie)."""
        if L.get('isBaseLayer'):
            return False
        texts, _ = self.layer_signature(L)
        joined = ' '.join(texts)
        return bool(TUTORIAL_RE.search(joined))

    def is_chrome_text(self, t):
        t = norm(t)
        if not t:
            return True
        if re.search(r'%_player|%_playerVars|%[A-Za-z_]+%', t):
            return True
        if NAV_WORD_RE.match(t):
            return True
        if re.match(r'^[\d.\s]+$', t) or DOTS_RE.match(t):
            return True
        if END_RE.match(t):
            return True
        if re.match(r'^hoofdstuk\s*\d+$', t, re.I):
            return True
        c = self.text_count.get(t, 0)
        return c >= 4 and c >= self.slide_count * 0.25

    def slide_entries(self, sid):
        """Alle zichtbare inhoud van een dia als platte lijst met positie en laag."""
        sl = self.slides.get(sid)
        meta = self.meta.get(sid, {})
        if not sl:
            return [], [], {}
        interactions = [it for it in (meta.get('interactions') or []) if it.get('kind') == 'interaction']
        choice_texts = set()
        for it in interactions:
            for c in (it.get('choices') or []) + (it.get('statements') or []):
                choice_texts.add(norm(decode(c.get('lmstext'))))
                choice_texts.add(clean_label(c.get('lmstext')).lower())
        layer_ids = {}
        entries = []
        seen = set()
        # Lagen die alleen bij een fout antwoord verschijnen: overslaan. (Ze bevatten vaak exact dezelfde uitleg als
        # de 'juist'-laag; zouden ze eerst gelezen worden, dan valt die uitleg weg als dubbele tekst.)
        wrong_layers = set()
        for inter in interactions:
            wrong_layers |= feedback_layers(inter)[1] - feedback_layers(inter)[0]
        for li, L in enumerate(sl.get('slideLayers') or []):
            layer_ids[li] = L.get('id')
            if self.is_chrome_layer(L) or self.is_tutorial_layer(L) or L.get('id') in wrong_layers:
                continue
            for it in self.flatten(L):
                ob = it['ob']
                base = {'x': it['x'], 'y': it['y'], 'gx': it['gx'], 'gy': it['gy'], 'layer': li, 'id': ob.get('id'),
                        'gid': it.get('gid'), 'gacc': it.get('gacc')}
                kind = ob.get('kind')
                if kind == 'video':
                    entries.append(dict(base, kind='note', text='Video: ' + (ob.get('altText') or 'video in de e-module')))
                    continue
                if kind == 'webobject':
                    entries.append(dict(base, kind='note', text='Webinhoud (niet af te drukken)'))
                    continue
                if kind == 'textinput':
                    entries.append(dict(base, kind='input', w=ob.get('width') or 0, h=ob.get('height') or 0))
                    continue
                url = self.image_url(ob)
                if url and self.decorative(ob, url) and not self.decorative(ob, url, ignore_size=True) \
                        and self.images.get(url) and ('img:' + url) not in seen:
                    # Klein plaatje: alleen houden als er een onderschrift onder staat (thumbnailrij), zie structure_entries.
                    seen.add('img:' + url)
                    alt = ((ob.get('imagelib') or [{}])[0]).get('altText') or ''
                    entries.append(dict(base, kind='smallimg', url=url, w=ob.get('width') or 0, h=ob.get('height') or 0, alt=norm(alt)))
                elif url and not self.decorative(ob, url) and ('img:' + url) not in seen and self.images.get(url):
                    seen.add('img:' + url)
                    alt = ((ob.get('imagelib') or [{}])[0]).get('altText') or ''
                    entries.append(dict(base, kind='image', url=url, w=ob.get('width') or 0, h=ob.get('height') or 0, alt=norm(alt)))
                blocks = self.text_blocks(ob)
                if blocks:
                    # Losse navigatie-alinea's binnen een tekstvak ('Klik op volgende om naar X te gaan.') en alinea's met
                    # een spelervariabele ('… het hoofdstuk %_player.HFDSTKtitel_01% gekomen.'); de rest van het vak blijft.
                    blocks = [b for b in blocks if not is_nav_only(b['text']) and not re.search(r'%[A-Za-z_][A-Za-z0-9_.]*%', b['text'])] or None
                if not blocks:
                    continue
                full = norm(' '.join(b['text'] for b in blocks))
                popup_title = False
                if ob.get('accType') == 'button':
                    # Kopbalk van een pop-uplaag ('Inwendige verbranding' met sluitknop) is de titel van die uitleg.
                    if li == 0 or L.get('isBaseLayer') or len(full) > 60 or self.is_chrome_text(full):
                        continue
                    popup_title = True
                if self.is_chrome_text(full):
                    continue
                if is_nav_only(full):
                    continue  # 'Je hebt het onderdeel X afgerond', 'Klik op volgende om ...' e.d.
                if it.get('gacc') == 'button' and re.search(r'navigat', full, re.I):
                    continue  # kaart-knop die de uitleg over de spelerknoppen opent
                if ('t:' + full) in seen:
                    continue
                seen.add('t:' + full)
                entries.append(dict(base, kind='text', blocks=blocks, text=full, size=max(b['size'] for b in blocks), popup_title=popup_title,
                                    is_choice=full in choice_texts or full.lower() in choice_texts, acc=ob.get('accType'),
                                    all_bold=all(s['bold'] for b in blocks for s in b['spans'] if norm(s['text'])),
                                    w=ob.get('width') or 0, h=ob.get('height') or 0))
        # Losse tekst die binnen het kader van een groep met maar één tekst (de kaarttitel) ligt, hoort bij die kaart:
        # bv. 'Voor wie?' in een gekleurd vak met de uitleg als los tekstvak erin.
        for li, L in enumerate(sl.get('slideLayers') or []):
            rects = {}
            for it in self.flatten(L):
                ob = it['ob']
                if ob.get('objects') and it.get('gid') == ob.get('id'):
                    rects[ob.get('id')] = (it['x'], it['y'], ob.get('width') or 0, ob.get('height') or 0)
            texts_in = Counter(e['gid'] for e in entries if e['kind'] == 'text' and e['layer'] == li and e.get('gid'))
            for e in entries:
                if e['kind'] == 'text' and e['layer'] == li and e.get('gid') and CREDIT_RE.match(norm(e['text'])):
                    texts_in[e['gid']] = 99  # foto met bronvermelding, geen kaart
            for e in entries:
                if e['kind'] != 'text' or e['layer'] != li or e.get('gid') or CREDIT_RE.match(norm(e['text'])):
                    continue
                for gid, (gx, gy, gw, gh) in rects.items():
                    if texts_in.get(gid) == 1 and gw <= 1000 and gx - 5 <= e['x'] and e['x'] + (e.get('w') or 0) <= gx + gw + 5 \
                            and gy - 5 <= e['y'] and e['y'] + (e.get('h') or 0) <= gy + gh + 5:
                            e['gid'], e['gx'], e['gy'] = gid, gx, gy
                            break
        # Pijltjes/bolletjes als losse vormpjes links van losse tekstregels = opsomming (zoals de leerdoelen).
        icons = []
        if sl.get('slideLayers'):
            for it in self.flatten(sl['slideLayers'][0]):
                ob = it['ob']
                w, h = ob.get('width') or 0, ob.get('height') or 0
                if ob.get('kind') == 'vectorshape' and 8 <= w <= 60 and 8 <= h <= 60 and not self.text_blocks(ob) and not self.image_url(ob):
                    icons.append((it['x'], it['y'], w, h))
        if len(icons) >= 2:
            marked = []
            for e in entries:
                if e['kind'] != 'text' or e['layer'] != 0 or len(e['blocks']) != 1 or e['blocks'][0]['list'] != 'none':
                    continue
                if any(0 <= e['x'] - (ix + iw) <= 80 and e['y'] - 10 <= iy + ih / 2 <= e['y'] + (e.get('h') or 0) + 10 for ix, iy, iw, ih in icons):
                    marked.append(e)
            if len(marked) >= 2 and len({round(e['x'] / 20) for e in marked}) == 1:
                for e in marked:
                    e['icon_bullet'] = True
        if any((it.get('type') == 'matching' or it.get('statements')) for it in interactions):
            # Sleepitems liggen op één stapel (zelfde positie en maat): het zijn antwoordstukjes, geen dia-tekst.
            spots = Counter((round(e['x']), round(e['y']), e.get('w'), e.get('h')) for e in entries if e['kind'] == 'text')
            for e in entries:
                if e['kind'] == 'text' and spots[(round(e['x']), round(e['y']), e.get('w'), e.get('h'))] >= 2:
                    e['is_choice'] = True
            # De feedbacklaag toont vaak de opgeloste afbeelding ('Bekijk de goede antwoorden rechts in beeld').
            for li, L in enumerate(sl.get('slideLayers') or []):
                if L.get('id') not in wrong_layers:
                    continue
                texts, _ = self.layer_signature(L)
                if not re.search(r'(goede|juiste|correcte) antwoord', ' '.join(texts), re.I):
                    continue
                for it in self.flatten(L):
                    u = self.image_url(it['ob'])
                    if u and not self.decorative(it['ob'], u) and self.images.get(u):
                        entries.append({'kind': 'solution', 'layer': 0, 'x': it['x'], 'y': it['y'], 'gx': it['x'], 'gy': it['y'],
                                        'url': u, 'w': it['ob'].get('width') or 0, 'h': it['ob'].get('height') or 0, 'text': '', 'size': 0})
        entries = structure_entries(entries)
        entries.sort(key=lambda e: (e['layer'], round(e['gy'] / 80), e['gx'], e['y'], e['x']))
        return entries, interactions, layer_ids

    # -- hoofdstukken
    def chapters(self):
        outline = (((self.frame.get('navData') or {}).get('outline') or {}).get('links')) or []
        scene_by_id = {str(sc.get('id')): sc for sc in self.data.get('scenes') or []}

        def slides_of(scene_id):
            sc = scene_by_id.get(scene_id)
            return [str(s.get('id')).split('.')[-1] for s in (sc.get('slides') or [])] if sc else []

        out = []
        if outline:
            for link in outline:
                scene_id = str(link.get('slideid') or '').replace('_player.', '').split('.')[0]
                ids = slides_of(scene_id)
                if not ids and link.get('links'):
                    ids = [str(l.get('slideid') or '').split('.')[-1] for l in link['links']]
                out.append({'label': norm(decode(link.get('displaytext') or link.get('slidetitle') or '')), 'ids': ids,
                            'scene': scene_id})
            used = {i for c in out for i in c['ids']}
            for sc in self.data.get('scenes') or []:
                ids = [i for i in slides_of(str(sc.get('id'))) if i not in used]
                if ids and not re.search('PromptScene', str(sc.get('id'))):
                    out.append({'label': norm(decode(sc.get('title') or '')), 'ids': ids, 'scene': str(sc.get('id'))})
        else:
            for sc in self.data.get('scenes') or []:
                if re.search('PromptScene', str(sc.get('id'))):
                    continue
                out.append({'label': norm(decode(sc.get('title') or '')), 'ids': slides_of(str(sc.get('id'))),
                            'scene': str(sc.get('id'))})
        out = [c for c in out if c['ids']]

        # Leesvolgorde: de startscène (inleiding) eerst, dan genummerde hoofdstukken (H1, H2, ...),
        # daarna de rest in menuvolgorde (samenvatting, begrippenlijst, ...).
        def key(ic):
            i, c = ic
            if c['scene'] == self.entry_scene:
                return (0, 0, i)
            m = re.match(r'^(?:H|hoofdstuk)\s*(\d+)', c['label'], re.I)
            if m:
                return (1, int(m.group(1)), i)
            return (2, 0, i)
        return [c for _, c in sorted(enumerate(out), key=key)]

    def skippable(self, sid):
        t = norm(self.meta.get(sid, {}).get('title') or '')
        if SKIP_SLIDE_TITLE_RE.match(t):
            return True
        if sid in ('ResumePromptSlide', 'ExternalInterfaceErrorSlide'):
            return True
        return False


# ----------------------------------------------------------------------------- structuur op de dia

def _is_label(e):
    return (e['kind'] == 'text' and not e.get('is_choice') and len(e['blocks']) == 1 and
            len(norm(e['text'])) <= 40 and not re.search(r'[.;:]$', norm(e['text'])))


def structure_entries(entries):
    """Herkent vaste dia-patronen voordat de leesvolgorde wordt bepaald:
    - labelrijen: kort opschrift (pijl/balk) met de bijbehorende zin rechts ernaast op dezelfde hoogte;
    - kaarten: groepen met een korte titel en tekst eronder, met minstens twee naast elkaar.
    Alleen herschikken: alle tekst blijft letterlijk behouden."""
    texts = [e for e in entries if e['kind'] == 'text' and not e.get('is_choice')]
    used = set()

    # --- één afbeelding met meerdere naast elkaar geplaatste bijschriften eronder
    #     (bv. Pouch / Prismatische cel / Cilindrische cel onder één plaat met drie batterijen)
    for im in [e for e in entries if e['kind'] == 'image' and (e.get('w') or 0) < 1800]:  # geen schermvullende achtergrond
        x0, x1 = im['x'] - 60, im['x'] + (im.get('w') or 0) + 60
        bottom = im['y'] + (im.get('h') or 0)
        cand = [t for t in texts if id(t) not in used and t['layer'] == im['layer'] and len(norm(t['text'])) <= 60
                and t.get('acc') != 'button' and t.get('gacc') != 'button'
                and x0 <= t['x'] and t['x'] + (t.get('w') or 0) <= x1
                and im['y'] + 0.6 * (im.get('h') or 0) <= t['y'] <= bottom + 150]
        if len(cand) < 2:
            continue
        ys = [t['y'] for t in cand]
        if max(ys) - min(ys) > 30 or len({round(t['x'] / 100) for t in cand}) != len(cand):
            continue
        cand.sort(key=lambda t: t['x'])
        y = max(ys)
        entries.append({'kind': 'figcaps', 'layer': im['layer'], 'x': im['x'], 'y': y, 'gx': im['x'], 'gy': y,
                        'url': im['url'], 'w': im['w'], 'h': im['h'], 'alt': im.get('alt', ''), 'text': '', 'size': 0,
                        'caps': [t['blocks'] for t in cand]})
        used.add(id(im))
        for t in cand:
            used.add(id(t))

    # --- rij plaatjes met onderschrift (bv. Batterij 1,5 Volt / Stopcontact 230 Volt / ...)
    pics = [e for e in entries if e['kind'] in ('image', 'smallimg') and id(e) not in used]
    figs = []
    for im in pics:
        best = None
        for t in texts:
            if id(t) in used or len(norm(t['text'])) > 120:
                continue
            gap = t['y'] - (im['y'] + (im.get('h') or 0))
            if gap < -15 or gap > 90:
                continue
            ix0, ix1 = im['x'], im['x'] + (im.get('w') or 0)
            tx0, tx1 = t['x'], t['x'] + (t.get('w') or 0)
            if min(ix1, tx1) - max(ix0, tx0) < 0.3 * min(ix1 - ix0, tx1 - tx0, 1e9):
                continue
            if best is None or gap < best[0]:
                best = (gap, t)
        if best:
            figs.append((im, best[1]))
    bands = {}
    for im, t in figs:
        bands.setdefault((im['layer'], round(im['y'] / 120)), []).append((im, t))
    for (layer, _), fs in bands.items():
        if len(fs) < 2 or len({id(t) for _, t in fs}) != len(fs):
            continue
        fs.sort(key=lambda it: it[0]['x'])
        i0 = fs[0][0]
        entries.append({'kind': 'figrow', 'layer': layer, 'x': i0['x'], 'y': i0['y'], 'gx': i0['x'], 'gy': i0['y'], 'text': '', 'size': 0,
                        'figs': [{'url': im['url'], 'alt': im.get('alt', ''), 'w': im['w'], 'h': im['h'], 'blocks': t['blocks']} for im, t in fs]})
        for im, t in fs:
            used.add(id(im))
            used.add(id(t))
    texts = [t for t in texts if id(t) not in used]

    # --- tabel: losse tekstvakken die in rijen en kolommen staan (bv. 1 mA | tinteling)
    by_layer = {}
    for t in texts:
        if not t.get('gid') and len(t['blocks']) == 1 and len(norm(t['text'])) <= 80 and (t.get('h') or 0) <= 120:
            by_layer.setdefault(t['layer'], []).append(t)
    for layer, cand in by_layer.items():
        xs = sorted({round(t['x']) for t in cand})
        clusters = []
        for x in xs:
            if clusters and x - clusters[-1][-1] <= 25:
                clusters[-1].append(x)
            else:
                clusters.append([x])
        col_of = lambda t: next(i for i, c in enumerate(clusters) if c[0] - 1 <= round(t['x']) <= c[-1] + 1)
        rows = []
        for t in sorted(cand, key=lambda t: t['y'] + (t.get('h') or 0) / 2):
            yc = t['y'] + (t.get('h') or 0) / 2
            if rows and abs(yc - rows[-1]['yc']) <= 0.5 * max(t.get('h') or 1, 1):
                rows[-1]['cells'].append(t)
            else:
                rows.append({'yc': yc, 'cells': [t]})
        best, cur = [], []
        for r in rows:
            cols = tuple(sorted({col_of(c) for c in r['cells']}))
            ok = len(cols) >= 2 and len(cols) == len(r['cells'])
            if ok and (not cur or cols == cur[0][0]):
                cur.append((cols, r))
            else:
                if len(cur) > len(best):
                    best = cur
                cur = [(cols, r)] if ok else []
        if len(cur) > len(best):
            best = cur
        if len(best) >= 3:
            first = min((c for _, r in best for c in r['cells']), key=lambda c: (c['y'], c['x']))
            table_rows = [[c['blocks'] for c in sorted(r['cells'], key=lambda c: c['x'])] for _, r in best]
            entries.append({'kind': 'table', 'layer': layer, 'x': first['x'], 'y': first['y'], 'gx': first['x'], 'gy': first['y'],
                            'rows': table_rows, 'text': '', 'size': 0})
            for _, r in best:
                for c in r['cells']:
                    used.add(id(c))
    texts = [t for t in texts if id(t) not in used]

    # --- tegels: een tekstvak (titel + tekst) met een los vakje dat er bovenop ligt (bv. 'offensief binnen')
    tiles = []
    for box in texts:
        if len(box['blocks']) < 2 or len(norm(box['blocks'][0]['text'])) > 40:
            continue
        bx0, by0 = box['x'], box['y']
        bx1, by1 = bx0 + (box.get('w') or 0), by0 + (box.get('h') or 0)
        inner = [t for t in texts if t is not box and t['layer'] == box['layer'] and len(t['blocks']) == 1
                 and t['x'] >= bx0 - 5 and t['x'] + (t.get('w') or 0) <= bx1 + 5 and t['y'] >= by0 and t['y'] + (t.get('h') or 0) <= by1 + 5]
        if inner:
            tiles.append((box, sorted(inner, key=lambda t: t['y'])))
    if len(tiles) >= 2:
        tiles.sort(key=lambda bt: (bt[0]['y'], bt[0]['x']))
        b0 = tiles[0][0]
        # Tegels staan meestal in de rechterkolom naast de uitleg ('Je ziet deze hiernaast'): na de tekst plaatsen.
        right_col = all(bt[0]['x'] > min((t['x'] for t in texts if t not in [x[0] for x in tiles]), default=1e9) + 200 for bt in tiles)
        tile_gy = 1e5 if right_col else b0['y']
        entries.append({'kind': 'cards', 'layer': b0['layer'], 'x': b0['x'], 'y': tile_gy, 'gx': b0['x'], 'gy': tile_gy, 'text': '', 'size': 0,
                        'cards': [(norm(box['blocks'][0]['text']), box['blocks'][1:] + [blk for t in inner for blk in t['blocks']]) for box, inner in tiles]})
        for box, inner in tiles:
            used.add(id(box))
            for t in inner:
                used.add(id(t))
    texts = [t for t in texts if id(t) not in used]

    # --- labelrijen
    pairs = []
    for lab in texts:
        if not _is_label(lab) or (lab.get('w') or 0) > 520:
            continue
        best = None
        for p in texts:
            if p is lab or p['layer'] != lab['layer'] or _is_label(p):
                continue
            if p['x'] < lab['x'] + (lab.get('w') or 0) - 30:
                continue
            ov = min(lab['y'] + (lab.get('h') or 0), p['y'] + (p.get('h') or 0)) - max(lab['y'], p['y'])
            if ov <= 0.3 * min(lab.get('h') or 1, p.get('h') or 1):
                continue
            if best is None or p['x'] < best['x']:
                best = p
        if best is not None:
            pairs.append((lab, best))
    partners = [id(b) for _, b in pairs]
    if len(pairs) >= 2 and len(set(partners)) == len(partners):
        by_layer = {}
        for lab, val in pairs:
            by_layer.setdefault(lab['layer'], []).append((lab, val))
        for layer, ps in by_layer.items():
            if len(ps) < 2:
                continue
            ps.sort(key=lambda lv: lv[0]['y'])
            first = ps[0][0]
            entries.append({'kind': 'labelrows', 'layer': layer, 'x': first['x'], 'y': first['y'], 'gx': first['gx'],
                            'gy': first['gy'], 'rows': [(norm(l['text']), v['blocks']) for l, v in ps], 'text': '', 'size': 0})
            for l, v in ps:
                used.add(id(l))
                used.add(id(v))

    # --- kaarten
    groups = {}
    for e in texts:
        if id(e) in used or not e.get('gid'):
            continue
        groups.setdefault((e['layer'], e['gid']), []).append(e)
    cards = []
    for (layer, gid), members in groups.items():
        members.sort(key=lambda e: e['y'])
        title = members[0]
        if not _is_label(title):
            continue
        body = [m for m in members[1:] if m['y'] >= title['y'] + 0.5 * (title.get('h') or 0)]
        if len(body) != len(members) - 1:
            continue
        cards.append((layer, title, body))
    rows = {}
    for layer, title, body in cards:
        rows.setdefault((layer, round(title['gy'] / 80)), []).append((title, body))
    for (layer, _), cs in rows.items():
        # Kaarten moeten tekst hebben: alleen-titels (onderwerpenlijst, aanklikbare tegels) zijn geen kaarten.
        if not any(body for _, body in cs):
            continue
        cs.sort(key=lambda tb: tb[0]['gx'])
        t0 = cs[0][0]
        entries.append({'kind': 'cards', 'layer': layer, 'x': t0['gx'], 'y': t0['gy'], 'gx': t0['gx'], 'gy': t0['gy'],
                        'cards': [(norm(t['text']), [blk for b in body for blk in b['blocks']]) for t, body in cs],
                        'text': '', 'size': 0})
        for t, body in cs:
            used.add(id(t))
            for b in body:
                used.add(id(b))
    return [e for e in entries if id(e) not in used and e['kind'] != 'smallimg']


# ----------------------------------------------------------------------------- documentmodel

class IdSet:
    """Set van dicts op identiteit."""
    def __init__(self):
        self.ids = set()

    def add(self, o):
        self.ids.add(id(o))

    def __contains__(self, o):
        return id(o) in self.ids


def clean_label(t):
    """Sleep-/invulitems heten in de cursusdata vaak 'SLEEPITEM3 - gesloten' of 'Picture 4 - foto.jpg'."""
    t = norm(decode(t))
    t = LABEL_PREFIX_RE.sub('', t)
    t = re.sub(r'\s*-\s*(rectangle|oval|shape|picture|image|freeform|rounded rectangle)\s*\d*\s*$', '', t, flags=re.I)
    t = re.sub(r'\.(jpe?g|png|gif|svg)$', '', t, flags=re.I)
    t = re.sub(r'^\d+(\.\d+)*\s*[-–]\s*', '', t)  # "4 - 4.06 hybride auto" -> "hybride auto"
    return t.strip()


def runs_of(blocks, keep_bold_all=False):
    """Blokken -> lijst van (tekst, vet)-runs. Volledig vette tekstvakken zijn opmaak, geen nadruk;
    een kort, volledig vet lijstitem ("Omvang") is wel een opschrift en blijft vet."""
    out = []
    for b in blocks:
        ne = [s for s in b['spans'] if norm(s['text'])]
        all_bold = bool(ne) and all(s['bold'] for s in ne)
        if all_bold and (b.get('list') or 'none') != 'none' and len(norm(b['text'])) <= 40:
            all_bold = False
        for s in b['spans']:
            bold = s['bold'] and (not all_bold or keep_bold_all)
            out.append((s['text'], bold))
    return out


def runs_to_markup(runs):
    """Runs -> ReportLab-paragraaf-markup. Regeleinden binnen een tekstvak zijn zachte einden."""
    parts = []
    for text, bold in runs:
        t = re.sub(r'[\r\n\u000b]+', ' ', text)
        t = esc(t)
        if bold and t.strip():
            parts.append('<b>' + t + '</b>')
        else:
            parts.append(t)
    s = ''.join(parts)
    s = re.sub(r'[ \t]+', ' ', s)
    # \u2028 = regeleinde binnen een alinea (vervolgregel zonder witruimte ertussen, zoals op de dia)
    s = re.sub(r'\s*\u2028\s*', '<br/>', s.strip())
    return s.strip()


def split_tag(tag):
    """'ul' -> ('ul', 0); 'ol:1' -> ('ol', 1)."""
    if tag and ':' in tag:
        b, l = tag.split(':', 1)
        return b, int(l)
    return tag, 0


def block_to_items(block):
    """Eén tekstblok -> lijst van ('p'|'li', runs, listtype). Regels met opsommingstekens worden lijst."""
    text = block['text']
    lines = [l for l in re.split(r'\r\n|\r|\n|\u000b', text) if norm(l)]
    lst = block.get('list') or 'none'
    if lst != 'none':
        tag = 'ol' if re.search(r'number|decimal|arabic|letter|roman', lst, re.I) else 'ul'
        level = int(block.get('level') or 0)
        return [('li', runs_of([block]), tag + (':%d' % level if level else ''))]
    if lines and all(BULLET_RE.match(l) for l in lines):
        return [('li', [(BULLET_RE.sub('', l), False)], 'ul') for l in lines]
    if len(lines) > 1 and any(BULLET_RE.match(l) for l in lines):
        out = []
        for l in lines:
            if BULLET_RE.match(l):
                out.append(('li', [(BULLET_RE.sub('', l), False)], 'ul'))
            else:
                out.append(('p', [(l, False)], None))
        return out
    return [('p', runs_of([block]), None)]


def is_heading_like(e, slide_max_size, body_size):
    """Korte, grotere of vette regel zonder eindpunt: tussenkop."""
    if e['kind'] != 'text' or len(e['blocks']) != 1:
        return False
    t = norm(e['text'])
    if len(t) > 90 or len(t) < 2:
        return False
    if re.search(r'[.:;,]$', t):
        return False
    if e['size'] >= max(22, body_size + 3):
        return True
    if e['all_bold'] and e['size'] >= body_size and len(t) <= 60:
        return True
    return False


def is_label_before_body(e, nxt):
    """Kort opschrift ("Voor wie?", "Doorlooptijd") gevolgd door een langere alinea: tussenkop."""
    if e['kind'] != 'text' or len(e['blocks']) != 1 or nxt is None:
        return False
    t = norm(e['text'])
    if len(t) > 45 or re.search(r'[.:;,!]$', t):
        return False
    if len(t.split()) > 5 or CREDIT_RE.match(t):
        return False
    return len(norm(nxt['text'])) > 60 and nxt['size'] <= e['size'] and nxt['layer'] == e['layer']


def correct_choice_ids(interaction):
    ids, values = set(), []

    def walk(st):
        if not st:
            return
        if isinstance(st, list):
            for x in st:
                walk(x)
            return
        if st.get('choiceid'):
            ids.add(str(st['choiceid']).split('.')[-1])
        if st.get('value') is not None and st.get('kind') != 'other':
            values.append(str(st['value']))
        if st.get('text') is not None:
            values.append(str(st['text']))
        walk(st.get('statements'))
        walk(st.get('statement'))
    for a in interaction.get('answers') or []:
        if a.get('status') == 'correct':
            walk(a.get('evaluate'))
    return ids, values


def feedback_layers(interaction):
    """Per antwoordstatus de lagen die getoond worden (show_slidelayer in de antwoordacties)."""
    right, wrong = set(), set()

    def walk(a, acc):
        if isinstance(a, list):
            for x in a:
                walk(x, acc)
        elif isinstance(a, dict):
            if a.get('kind') == 'show_slidelayer':
                v = (a.get('objRef') or {}).get('value')
                if v:
                    acc.add(str(v))
            for v in a.values():
                if isinstance(v, (list, dict)):
                    walk(v, acc)
    for a in interaction.get('answers') or []:
        walk(a.get('actions'), right if a.get('status') == 'correct' else wrong)
    return right, wrong


def matching_pairs(it):
    by_id = {}
    for c in (it.get('choices') or []) + (it.get('statements') or []):
        by_id[str(c.get('id')).split('.')[-1]] = decode(c.get('lmstext') or '')
    pairs = []

    clean = clean_label

    def walk(st):
        if not st:
            return
        if isinstance(st, list):
            for x in st:
                walk(x)
            return
        if st.get('choiceid'):
            choice = by_id.get(str(st['choiceid']).split('.')[-1])
            sid = st.get('statementid') or st.get('matchid') or st.get('targetid')
            stmt = by_id.get(str(sid).split('.')[-1], '') if sid is not None else ''
            if choice and stmt:
                pairs.append((clean(stmt), clean(choice)))
        walk(st.get('statements'))
        walk(st.get('statement'))
    for a in it.get('answers') or []:
        if a.get('status') == 'correct':
            walk(a.get('evaluate'))
    return pairs


CHECK_LEAD_RE = re.compile(r'^\s*controleer of (je )?(jouw )?antwoord lijkt op (het )?onderstaande\.?\s*', re.I)


def strip_check_lead(items):
    """'Controleer of antwoord lijkt op het onderstaande.' is een spelerinstructie vóór de uitleg: alleen die zin weghalen."""
    out = []
    for i, (kind, runs, tag) in enumerate(items):
        if not out and runs:
            txt = ''.join(t for t, _ in runs)
            m = CHECK_LEAD_RE.match(txt)
            if m:
                cut = m.end()
                new, pos = [], 0
                for t, b in runs:
                    if pos + len(t) <= cut:
                        pos += len(t)
                        continue
                    new.append((t[max(0, cut - pos):], b))
                    pos += len(t)
                if not norm(''.join(t for t, _ in new)):
                    continue
                runs = new
        out.append((kind, runs, tag))
    return out


def merge_broken_tuples(seq):
    out = []
    for kind, runs, tag in seq:
        if kind == 'p' and out and out[-1][0] == 'p':
            prev = ''.join(r[0] for r in out[-1][1]).rstrip()
            cur = ''.join(r[0] for r in runs).lstrip()
            if prev and cur and not re.search(r'[.!?:;)\]"\u201d]$', prev) and cur[0].islower():
                out[-1] = ('p', out[-1][1] + [(' ', False)] + runs, tag)
                continue
        out.append((kind, runs, tag))
    return out


def build_quiz(course, entries, interactions, layer_ids, heading):
    """Vraagblok(ken) voor een dia met interacties, plus de lagen die al verwerkt zijn."""
    texts = [e for e in entries if e['kind'] == 'text' and not e['is_choice'] and e is not heading]
    base_texts = [e for e in texts if e['layer'] == 0 and not (CREDIT_RE.match(norm(e['text'])) and len(norm(e['text'])) < 80)]
    right_layers, wrong_layers = set(), set()
    for it in interactions:
        r, w = feedback_layers(it)
        right_layers |= r
        wrong_layers |= w
    consumed_layers = set()
    feedback = []
    for li, lid in layer_ids.items():
        if lid in right_layers or lid in wrong_layers:
            consumed_layers.add(li)
        if lid in right_layers:
            for e in texts:
                if e['layer'] != li:
                    continue
                t = norm(e['text'])
                if FEEDBACK_ONLY_RE.match(t):
                    continue
                feedback.append(e)
    # Toelichting: alle tekst van de 'juist'-laag, zonder de aanhef ("Dat is goed.").
    fb_items = []
    for e in feedback:
        for b in e['blocks']:
            for kind, runs, tag in block_to_items(b):
                fb_items.append((kind, runs, tag))
    # aanhef verwijderen
    cleaned = []
    first = True
    for kind, runs, tag in fb_items:
        txt = ''.join(r[0] for r in runs)
        if first:
            txt2 = FEEDBACK_LEAD_RE.sub('', txt, count=1)
            if not norm(txt2):
                continue
            if txt2 != txt:
                runs = [(txt2, False)]
            first = False
        if FEEDBACK_ONLY_RE.match(norm(txt)):
            continue
        cleaned.append((kind, runs, tag))
    fb_items = strip_check_lead(cleaned)
    # Afbeeldingen in de 'juist'-laag (bv. de grafiek bij de toelichting) horen bij de toelichting.
    fb_images, seen_fb = [], set()
    for li, lid in layer_ids.items():
        if lid in right_layers:
            for e in entries:
                if e['kind'] == 'image' and e['layer'] == li and e['url'] not in seen_fb:
                    seen_fb.add(e['url'])
                    if any(o['kind'] == 'image' and o['layer'] == 0 and course.same_picture(o['url'], e['url']) for o in entries):
                        continue  # zelfde foto als bij de vraag (vaak een iets andere uitsnede)
                    fb_images.append({'url': e['url'], 'w': e['w'], 'h': e['h'], 'alt': e.get('alt', '')})

    used = IdSet()
    quizzes = []
    for it in interactions:
        kind = it.get('type') or ''
        lms = it.get('lmsId') or ''
        q = {'kind': 'quiz', 'type': kind, 'question': [], 'options': [], 'pairs': [], 'answers': [], 'prompt': None,
             'feedback': fb_items if it is interactions[-1] else [],
             'fb_images': fb_images if it is interactions[-1] else []}
        # Vraagtekst: tekst die op '?' eindigt, anders de eerste basistekst.
        qe = next((e for e in base_texts if re.search(r'\?\s*$', e['text']) and e not in used), None) or \
            next((e for e in base_texts if e not in used), None) or heading
        if qe is not None and qe is not heading:
            used.add(qe)
            q['question'] = [item for b in qe['blocks'] for item in block_to_items(b)]
        elif heading is not None and not q['question']:
            q['question'] = [('p', [(heading['text'], False)], None)]
        if re.search(r'fillin|numeric|text|essay', kind, re.I) or re.search(r'TextEntry|Numeric', lms, re.I):
            ids, values = correct_choice_ids(it)
            ans = [decode(c.get('lmstext') or '') for c in (it.get('choices') or []) if str(c.get('id')).split('.')[-1] in ids]
            ans = [a for a in ans if norm(a)] or [v for v in values if norm(v)]
            q['answers'] = ans
            # Invulzin: teksten op dezelfde regel als het invoerveld, met een streep op de plek van het veld.
            inp = next((e for e in entries if e['kind'] == 'input'), None)
            if inp:
                cands = base_texts + [inp] + ([heading] if heading is not None else [])
                row = [e for e in cands if abs(e['y'] - inp['y']) < 60 and (e is inp or e not in used)]
                row.sort(key=lambda e: e['x'])
                parts = []
                for e in row:
                    if e is inp:
                        parts.append('________')
                    else:
                        used.add(e)
                        parts.append(norm(e['text']))
                if len(row) > 1:
                    q['prompt'] = ' '.join(parts)
            # Overige instructieteksten op de basislaag horen bij de vraag.
            for e in base_texts:
                if e not in used and e['size'] <= (qe['size'] if qe else 99) and len(norm(e['text'])) < 160:
                    used.add(e)
                    q['question'] += [item for b in e['blocks'] for item in block_to_items(b)]
        elif kind == 'matching' or it.get('statements'):
            q['pairs'] = matching_pairs(it)
            # Objectnamen van de auteur leesbaar maken: 'Doel hoogspanningslijn' -> 'hoogspanningslijn';
            # 'A HS lijn - 380.000 V' -> '380.000 V' als dat stukje letterlijk als sleepitem op de dia staat.
            pieces = {norm(e['text']) for e in entries if e['kind'] == 'text' and e.get('is_choice')}
            def _nice_choice(c):
                if ' - ' in c:
                    tail = c.rsplit(' - ', 1)[1].strip()
                    if tail in pieces:
                        return tail
                return c
            q['pairs'] = [(re.sub(r'^(doel|drop|target|vak)\s+', '', a, flags=re.I), _nice_choice(b)) for a, b in q['pairs']]
            sol = [e for e in entries if e['kind'] == 'solution']
            if sol:
                q['solution'] = {'url': sol[0]['url'], 'w': sol[0]['w'], 'h': sol[0]['h']}
            if q['pairs'] and all(DOTS_RE.match(a) or not a for a, b in q['pairs']):
                # Invuloefening met sleepwoorden: de 'begrippen' zijn lege invulvakken.
                q['answers'] = [b for a, b in q['pairs']]
                q['pairs'] = []
            if not q['pairs']:
                q['options'] = [(clean_label(c.get('lmstext') or ''), False) for c in it.get('choices') or []]
        else:
            ids, _ = correct_choice_ids(it)
            multi = kind == 'multipleresponse' or 'MultipleResponse' in lms or len(ids) > 1
            q['multi'] = multi
            for c in it.get('choices') or []:
                cid = str(c.get('id')).split('.')[-1]
                q['options'].append((clean_label(c.get('lmstext') or ''), cid in ids))
            if q['options'] and all(HOTSPOT_LABEL_RE.match(o[0]) for o in q['options']):
                # Aanwijsvraag (hotspots op een afbeelding): de labels zeggen niets, de toelichting wel.
                q['options'] = []
                q['hotspot'] = True
        # Instructies ("Kies het juiste antwoord.") horen in het vraagkader.
        for e in base_texts:
            if e not in used and INSTRUCTION_RE.match(norm(e['text'])) and len(norm(e['text'])) < 120:
                used.add(e)
                q['question'].append(('p', [(norm(e['text']), False)], None))
        quizzes.append(q)
    return quizzes, used, consumed_layers


def build_reflection(entries, heading):
    """Open vraag (invoerveld zonder beoordeling): vraag + voorbeeldantwoord uit de feedbacklaag."""
    texts = [e for e in entries if e['kind'] == 'text' and e is not heading]
    used = IdSet()
    q = {'kind': 'quiz', 'type': 'open', 'question': [], 'options': [], 'pairs': [], 'answers': [], 'prompt': None,
         'feedback': [], 'open': True}
    consumed = set()
    for e in texts:
        t = norm(e['text'])
        if e['layer'] == 0 and CREDIT_RE.match(t) and len(t) < 80:
            continue  # fotobron hoort bij de foto, niet bij de vraag
        if e['layer'] == 0:
            if re.match(r'^(vul hier je antwoord in|typ hier|schrijf hier)', t, re.I):
                used.add(e)
                continue
            if len(t) < 400:
                used.add(e)
                q['question'] += [item for b in e['blocks'] for item in block_to_items(b)]
        else:
            consumed.add(e['layer'])
            if NAV_WORD_RE.match(t):
                continue
            for b in e['blocks']:
                if re.match(r'^feedback$', norm(b['text']), re.I):
                    continue  # kopje van de feedbacklaag; 'Controleer of …' wordt door strip_check_lead per zin verwijderd
                q['feedback'] += block_to_items(b)
    if not q['question']:
        return [], used, set()
    q['feedback'] = strip_check_lead(q['feedback'])
    return [q], used, consumed


def merge_broken_paragraphs(items):
    """Auteurs breken zinnen soms af met Enter ("... ter plaatse is," / "kan de volgende ..."):
    twee alinea's waarvan de eerste niet op een zinseinde eindigt en de tweede met een kleine letter
    begint, zijn één zin."""
    out = []
    for it in items:
        if it['kind'] == 'p' and out and out[-1]['kind'] == 'p':
            prev = ''.join(r[0] for r in out[-1]['runs']).rstrip()
            cur = ''.join(r[0] for r in it['runs']).lstrip()
            # Alleen binnen hetzelfde tekstvak: losse vakjes (tabelcellen, tegels) nooit aan elkaar plakken.
            same_box = it.get('src') is not None and it.get('src') == out[-1].get('src')
            if same_box and prev and cur and not re.search(r'[.!?:;)\]"\u201d]$', prev) and cur[0].islower():
                out[-1]['runs'] = out[-1]['runs'] + [(' ', False)] + it['runs']
                continue
        out.append(it)
    return out


def slide_model(course, sid, suppress_heading=False):
    entries, interactions, layer_ids = course.slide_entries(sid)
    if not entries:
        return [], '', ''
    texts = [e for e in entries if e['kind'] == 'text' and not e['is_choice']]
    sizes = sorted(e['size'] for e in texts) if texts else [18]
    body_size = sizes[len(sizes) // 2]
    slide_max = sizes[-1]
    heading = None
    cands = [e for e in texts if e['layer'] == 0 and e['size'] >= 30 and len(e['text']) <= 120
             and not re.match(r'^[\d.\s]+$', e['text']) and len(e['blocks']) == 1
             and not re.search(r'[.:;,!]$', norm(e['text'])) and not INSTRUCTION_RE.match(norm(e['text']))
             and not CREDIT_RE.match(norm(e['text']))]
    if interactions or any(e['kind'] == 'input' for e in entries):
        cands = [e for e in cands if not re.search(r'\?\s*$', norm(e['text']))]
    if cands:
        cands.sort(key=lambda e: (-e['size'], e['y']))
        heading = cands[0]
    items = []
    used = IdSet()
    consumed_layers = set()
    quizzes = []
    if interactions:
        quizzes, used, consumed_layers = build_quiz(course, entries, interactions, layer_ids, heading)
    elif any(e['kind'] == 'input' for e in entries):
        quizzes, used, consumed_layers = build_reflection(entries, heading)
    if heading is not None and heading in used:
        heading = None
    # Pop-uplagen (klik op een begrip -> kader met uitleg): als kader met titel bij het begrip zetten.
    popups = []
    popup_ids = IdSet()
    if not quizzes:
        for li in sorted({e['layer'] for e in entries if e['layer'] > 0}):
            if li in consumed_layers:
                continue
            les = [e for e in entries if e['layer'] == li and e['kind'] == 'text' and not e.get('is_choice')]
            if not les:
                continue
            les.sort(key=lambda e: (e['y'], e['x']))
            title = les[0] if (les[0].get('popup_title') or (_is_label(les[0]) and len(les) > 1)) else None
            body = [e for e in les if e is not title]
            if not body:
                continue
            popups.append({'kind': 'popup', 'title': norm(title['text']) if title else '',
                           'items': [it for e in body for b in e['blocks'] for it in block_to_items(b)]})
            for e in les:
                popup_ids.add(e)

    if heading is not None and not suppress_heading:
        items.append({'kind': 'h2', 'text': norm(heading['text'])})
    quiz_placed = False
    has_solution = any(q.get('solution') for q in quizzes)
    biggest = max([e for e in entries if e['kind'] == 'image' and e['layer'] == 0], key=lambda e: (e.get('w') or 0) * (e.get('h') or 0), default=None)
    for e in entries:
        if e is heading or e in used or e in popup_ids or e.get('is_choice') or e['kind'] in ('input', 'solution'):
            continue
        if has_solution and e is biggest:
            continue  # onopgeloste versie van de sleepoefening; de opgeloste staat in het vraagkader
        if e.get('popup_title'):
            continue
        if e['layer'] in consumed_layers:
            continue
        if quizzes and not quiz_placed and e['layer'] > 0:
            items.extend(quizzes)
            quiz_placed = True
        if e['kind'] == 'text':
            if CREDIT_RE.match(norm(e['text'])) and len(norm(e['text'])) < 80:
                items.append({'kind': 'caption', 'text': norm(e['text'])})
                continue
            # Formule: tekstvak waarin een blok alleen een operator is ("=") -> één gecentreerde regel.
            if len(e['blocks']) >= 3 and any(OPERATOR_RE.match(b['text']) for b in e['blocks']):
                items.append({'kind': 'formula', 'text': ' '.join(norm(b['text']) for b in e['blocks'])})
                continue
            if e.get('icon_bullet'):
                entry = {'runs': block_to_items(e['blocks'][0])[0][1], 'level': 0, 'tag': 'ul'}
                if items and items[-1]['kind'] == 'list' and items[-1].get('icons'):
                    items[-1]['items'].append(entry)
                else:
                    items.append({'kind': 'list', 'tag': 'ul', 'items': [entry], 'open': False, 'icons': True})
                continue
            nxt = next((n for n in entries[entries.index(e) + 1:] if n['kind'] == 'text' and not n.get('is_choice')), None)
            if is_heading_like(e, slide_max, body_size) or is_label_before_body(e, nxt):
                items.append({'kind': 'h3', 'text': norm(e['text'])})
                continue
            for b in e['blocks']:
                for kind, runs, tag in block_to_items(b):
                    if kind == 'li':
                        base, level = split_tag(tag)
                        entry = {'runs': runs, 'level': level, 'tag': base}
                        if items and items[-1]['kind'] == 'list' and items[-1].get('open') and \
                                (items[-1]['tag'] == base or level > 0):
                            items[-1]['items'].append(entry)
                        else:
                            items.append({'kind': 'list', 'tag': base, 'items': [entry], 'open': True})
                    else:
                        if items and items[-1]['kind'] == 'list':
                            items[-1]['open'] = False
                        if re.match(r'^(\t| {2,})\S', b['text']) and not re.match(r'^\s*(\d+[.)]|[a-z][.)]\s|[•▪◦‣\-–])', b['text']) \
                                and items and items[-1]['kind'] == 'p' \
                                and items[-1].get('src') == e.get('id'):
                            # ingesprongen vervolgregel ('    Alle kunststof ...') hoort bij de alinea erboven
                            items[-1]['runs'] = items[-1]['runs'] + [('\u2028', False)] + runs
                            continue
                        if b.get('tight') and items and items[-1]['kind'] == 'p' and items[-1].get('src') == e.get('id'):
                            # volgende alinea zonder witregel of alinea-afstand: op de dia een nieuwe regel, geen nieuwe alinea
                            items[-1]['runs'] = items[-1]['runs'] + [('\u2028', False)] + runs
                            continue
                        items.append({'kind': 'p', 'runs': runs, 'src': e.get('id')})
            if items and items[-1]['kind'] == 'list':
                items[-1]['open'] = False
        elif e['kind'] == 'labelrows':
            items.append({'kind': 'labelrows', 'rows': [(lab, blocks_items(blocks)) for lab, blocks in e['rows']]})
        elif e['kind'] == 'table':
            items.append({'kind': 'table', 'rows': [[blocks_items(cell) for cell in row] for row in e['rows']]})
        elif e['kind'] == 'figcaps':
            items.append({'kind': 'figcaps', 'url': e['url'], 'w': e['w'], 'h': e['h'], 'alt': e['alt'],
                          'caps': [blocks_items(blocks) for blocks in e['caps']]})
        elif e['kind'] == 'figrow':
            items.append({'kind': 'figrow', 'figs': [dict(f, items=blocks_items(f['blocks'])) for f in e['figs']]})
        elif e['kind'] == 'cards':
            items.append({'kind': 'cards', 'cards': [(t, blocks_items(blocks)) for t, blocks in e['cards']]})
        elif e['kind'] == 'image':
            items.append({'kind': 'image', 'url': e['url'], 'w': e['w'], 'h': e['h'], 'alt': e['alt'], 'x': e['x'], 'y': e['y']})
        elif e['kind'] == 'note':
            items.append({'kind': 'note', 'text': e['text']})
    if quizzes and not quiz_placed:
        items.extend(quizzes)
    # Pop-ups direct na de regel die het begrip noemt, anders na de tekst van de dia.
    for pu in popups:
        key = pu['title'].lower()
        at = None
        if key:
            for i, x in enumerate(items):
                if x['kind'] in ('p', 'list', 'h3') and key in item_text(x).lower():
                    at = i
        if at is None:
            at = max([i for i, x in enumerate(items) if x['kind'] in ('p', 'list', 'h3', 'aside')] or [len(items) - 1])
        items.insert(at + 1, pu)
    # Hoofdstuk-overzicht (titel + onderwerpen onder elkaar, zoals op de verdeeldia): één overzichtskader.
    body_texts = [e for e in entries if e['kind'] == 'text' and e is not heading and not e.get('is_choice')]
    others = [e for e in entries if e['kind'] not in ('text',)]
    if not quizzes and not others and len(body_texts) >= 3 and all(_is_label(e) for e in body_texts) \
            and max(e['x'] for e in body_texts) - min(e['x'] for e in body_texts) <= 40:
        topics = [norm(e['text']) for e in sorted(body_texts, key=lambda e: e['y'])]
        items = [x for x in items if x['kind'] == 'h2'] + [{'kind': 'overview', 'topics': topics}]
    # Verdeeldia's: alleen korte opschriften onder elkaar (Transport / Distributie / Gebruik) -> lijst.
    out = []
    i = 0
    while i < len(items):
        if items[i]['kind'] == 'h3':
            j = i
            while j < len(items) and items[j]['kind'] == 'h3':
                j += 1
            if j - i >= 2 and (j >= len(items) or items[j]['kind'] in ('h2', 'image', 'quiz')):
                out.append({'kind': 'list', 'tag': 'ul', 'items': [{'runs': [(x['text'], False)], 'level': 0, 'tag': 'ul'} for x in items[i:j]]})
                i = j
                continue
        out.append(items[i])
        i += 1
    items = merge_broken_paragraphs(out)
    # Fotogalerijen: meerdere afbeeldingen op één dia naast elkaar, maximaal vier.
    imgs = [x for x in items if x['kind'] == 'image']
    if len(imgs) >= 2:
        first = items.index(imgs[0])
        body_entries = [e for e in entries if e['kind'] == 'text' and e is not heading and e['layer'] == 0
                        and not CREDIT_RE.match(norm(e['text']))]
        right_edge = max([e['x'] + (e.get('w') or 0) for e in body_entries] or [0])
        items = [x for x in items if x['kind'] != 'image']
        if body_entries and all(im.get('x', 0) >= right_edge - 40 for im in imgs):
            # foto's staan rechts naast de tekst: na de tekst zetten, niet tussen stap 1 en 2
            first = max([i for i, x in enumerate(items) if x['kind'] in ('p', 'list', 'h3')] or [first - 1]) + 1
        items.insert(min(first, len(items)), {'kind': 'gallery', 'images': imgs})
    elif len(imgs) == 1 and not quizzes:
        # Tekst links, foto rechts (zoals op de dia): naast elkaar zetten in plaats van onder elkaar.
        img = imgs[0]
        body_entries = [e for e in entries if e['kind'] == 'text' and e is not heading and e['layer'] == 0
                        and not CREDIT_RE.match(norm(e['text']))]
        right_edge = max([e['x'] + (e.get('w') or 0) for e in body_entries] or [0])
        if body_entries and img.get('x', 0) >= right_edge - 40:
            head = [x for x in items if x['kind'] == 'h2']
            caps = [x for x in items if x['kind'] == 'caption']
            rest = [x for x in items if x['kind'] not in ('h2', 'caption', 'image')]
            if rest and all(x['kind'] in ('p', 'list', 'h3') for x in rest):
                items = head + [{'kind': 'aside', 'items': rest, 'image': img, 'captions': [c['text'] for c in caps]}]
    signature = norm(' '.join(item_text(x) for x in items if x['kind'] in ('h2', 'h3', 'p', 'list', 'labelrows', 'cards', 'aside', 'figrow', 'figcaps', 'overview', 'table', 'popup')))
    return items, (norm(heading['text']) if heading is not None else ''), signature


def item_text(x):
    """Platte tekst van een modelitem (voor dubbele dia's en de controle op volledigheid)."""
    k = x['kind']
    r = lambda runs: ''.join(t for t, _ in runs)
    bi = lambda items: ' '.join(r(runs) for _, runs, _ in items)
    if k in ('h2', 'h3', 'formula', 'caption', 'note'):
        return x['text']
    if k == 'p':
        return r(x['runs'])
    if k == 'list':
        return ' '.join(r(li['runs']) for li in x['items'])
    if k == 'labelrows':
        return ' '.join(l + ' ' + bi(v) for l, v in x['rows'])
    if k == 'cards':
        return ' '.join(t + ' ' + bi(v) for t, v in x['cards'])
    if k == 'aside':
        return ' '.join(item_text(i) for i in x['items']) + ' ' + ' '.join(x.get('captions') or [])
    if k == 'figrow':
        return ' '.join(bi(f['items']) for f in x['figs'])
    if k == 'figcaps':
        return ' '.join(bi(c) for c in x['caps'])
    if k == 'table':
        return ' '.join(bi(cell) for row in x['rows'] for cell in row)
    if k == 'overview':
        return ' '.join(x['topics'])
    if k == 'popup':
        return x['title'] + ' ' + bi(x['items'])
    return ''


def dedupe_within_chapter(body):
    kk = lambda t: re.sub(r'[^a-z0-9]+', '', t.lower())
    seen = set()
    out = []
    for x in body:
        k = x['kind']
        if k == 'p':
            key = kk(item_text(x))
            if len(key) >= 40 and key in seen:
                continue
            seen.add(key)
        elif k == 'list':
            keep = []
            for li in x['items']:
                key = kk(''.join(t for t, _ in li['runs']))
                if len(key) >= 40 and key in seen:
                    continue
                seen.add(key)
                keep.append(li)
            if not keep:
                continue
            x = dict(x, items=keep)
        elif k in ('popup', 'aside', 'cards', 'labelrows', 'table', 'figrow', 'figcaps'):
            key = kk(item_text(x))
            if len(key) >= 40 and key in seen:
                continue
            seen.add(key)
            if k == 'aside':
                # alinea's in het zij-aan-zij-blok ook meetellen
                for i in x['items']:
                    seen.add(kk(item_text(i)))
        out.append(x)
    return out


def blocks_items(blocks):
    """block_to_items voor een reeks blokken; een blok zonder witregel of alinea-afstand na het vorige blok in
    hetzelfde tekstvak ('tight') wordt een nieuwe regel in dezelfde alinea, zoals op de dia."""
    out = []
    for b in blocks:
        its = block_to_items(b)
        if b.get('tight') and out and out[-1][0] == 'p' and its and its[0][0] == 'p':
            k, runs, tag = out[-1]
            out[-1] = (k, list(runs) + [('\u2028', False)] + list(its[0][1]), tag)
            its = its[1:]
        out.extend(its)
    return out


def num_label(label):
    return re.match(r'^\s*(?:H|hoofdstuk)\s*\d+\b', label or '', re.I) is not None


def build_document(course):
    chapters = []
    n = 0
    seen_slides = set()
    seen_signatures = set()
    for ch in course.chapters():
        body = []
        title = ch['label']
        divider = course.divider_title(ch['ids'][0]) if ch['ids'] else ''
        # Scènes waar een menudia van dit hoofdstuk naartoe springt (bv. Oefenen -> Casus 1..4)
        jumps = set()
        menu_words = set()
        intro = []
        for sid in ch['ids']:
            if course.skippable(sid) and course.slides.get(sid):
                raw = json.dumps(course.slides[sid])
                jumps |= {m.split('.')[0] for m in re.findall(r'"_player\.([A-Za-z0-9]+\.[A-Za-z0-9]+)"', raw)}
                for it in course.flatten(course.slides[sid]['slideLayers'][0]):
                    menu_words |= {norm(b['text']).lower() for b in course.text_blocks(it['ob']) or []}
                # inleidende zin op de menudia ('In dit hoofdstuk ga je aan de slag ...'); knopinstructies niet
                for it in course.flatten(course.slides[sid]['slideLayers'][0]):
                    for b in course.text_blocks(it['ob']) or []:
                        if re.match(r'^\s*In dit hoofdstuk\b', b['text']):
                            intro.append({'kind': 'p', 'runs': [(norm(b['text']), False)]})
        first_heading = ''
        first_meta_title = ''
        last_h2 = None
        for idx, sid in enumerate(ch['ids']):
            if course.skippable(sid):
                continue
            meta = course.meta.get(sid, {})
            is_title_slide = idx == 0 and re.search(r'hoofdstuk\s*titel|titelpagina|title', meta.get('title') or '', re.I)
            if sid in seen_slides:
                continue
            seen_slides.add(sid)
            items, heading, signature = slide_model(course, sid, suppress_heading=bool(is_title_slide))
            if signature and len(signature) > 40:
                if signature in seen_signatures:
                    continue  # dezelfde dia (bijv. verdeeldia of begrippenlijst) komt in meerdere scènes terug
                seen_signatures.add(signature)
            if is_title_slide:
                if heading and (not title or re.match(r'^H\s*\d+$', title, re.I)):
                    title = heading
                continue
            if not first_heading and heading:
                first_heading = heading
            mt = norm(decode(meta.get('title') or ''))
            if not first_meta_title and mt and not GENERIC_TITLE_RE.match(mt) and len(mt) <= 60:
                first_meta_title = mt
            # Vervolgdia's: "Onderwerp (2)" krijgt geen nieuwe kop.
            for it in items:
                if it['kind'] == 'h2':
                    stripped = re.sub(r'\s*\(\d+\)\s*$', '', it['text']).strip()
                    if last_h2 and stripped.lower() == last_h2.lower():
                        it['kind'] = 'skip'
                    else:
                        it['text'] = stripped
                        last_h2 = stripped
            body.extend(i for i in items if i['kind'] != 'skip')
        text_len = sum(len(i.get('text', '')) + sum(len(r[0]) for r in i.get('runs', [])) for i in body)
        if (not body and not (jumps and num_label(ch['label']))) or (text_len < 300 and re.search(r'kies een hoofdstuk', ' '.join(i.get('text', '') for i in body), re.I)):
            continue
        if re.match(r'^(keuzemenu|menu|hoofdmenu)$', title, re.I):
            continue
        if divider:
            title = divider  # titel zoals de deelnemer hem op de hoofdstukdia ziet
        title = re.sub(r'^H\s*\d+\s*[:.\-–]?\s*', '', title).strip()
        title = re.sub(r'\s+\d+$', '', title).strip()
        if not title or re.match(r'^hoofdstuk$', title, re.I):
            title = first_meta_title or first_heading or ''
        if title == title.upper() and len(title) > 3:
            title = title[0] + title[1:].lower()
        # Pop-up waarvan de tekst elders in het hoofdstuk al volledig staat (bv. 'Voorbeeld'-pop-up = volgende dia): weglaten.
        kk = lambda t: re.sub(r'[^a-z0-9]+', '', t.lower())
        def _rest(i):
            # alleen gewone dia-tekst telt; een tweede exemplaar van dezelfde pop-up regelt dedupe_within_chapter
            return kk(' '.join(item_text(x) for j, x in enumerate(body) if j != i and x['kind'] != 'popup'))
        body = [x for i, x in enumerate(body)
                if not (x['kind'] == 'popup' and len(kk(item_text(x))) > 80
                        and kk(' '.join(''.join(t for t, _ in r) for _, r, _ in x['items'])) in _rest(i))]
        # Letterlijke herhaling binnen hetzelfde hoofdstuk (begrippenlijst-dia's, opbouwdia's, pop-up = dia-tekst):
        # alleen de eerste keer tonen. Een samenvatting in een eigen hoofdstuk blijft dus staan.
        body = dedupe_within_chapter(body)
        # Een h2 die gelijk is aan de hoofdstuktitel direct aan het begin is dubbel.
        if body and body[0]['kind'] == 'h2' and body[0]['text'].lower() == title.lower():
            body = body[1:]
        # Opeenvolgende menu-items met dezelfde naam (Begrippenlijst 1..7, Casus 1..4) worden één hoofdstuk.
        if chapters and title and chapters[-1]['title'].lower() == title.lower():
            chapters[-1]['items'] = dedupe_within_chapter(chapters[-1]['items'] + body)
            chapters[-1]['slides'] += len(ch['ids'])
            chapters[-1]['scenes'].append(ch.get('scene'))
            chapters[-1].setdefault('labels', []).append(ch['label'])
            continue
        n += 1
        m = re.match(r'^\s*(?:H|hoofdstuk)\s*(\d+)\b', ch['label'], re.I)
        num = int(m.group(1)) if m else None  # nummer zoals in het spelermenu; Inleiding/Samenvatting/Casus zonder nummer
        if not title:
            title = 'Hoofdstuk %d' % num if num else 'Hoofdstuk'
        chapters.append({'n': n, 'num': num, 'title': title, 'items': body, 'slides': len(ch['ids']),
                         'scenes': [ch.get('scene')], 'jumps': jumps, 'intro': intro, 'menu_words': menu_words,
                         'labels': [ch['label']]})
    # Een genummerd hoofdstuk dat alleen een menu is naar losse scènes (H5 Oefenen -> Casus 1..4):
    # die scènes horen in Storyline bij dat hoofdstuk, dus daar onder plaatsen.
    for host in chapters:
        if host['num'] is None:
            continue
        own = {sc for sc in host['scenes'] if sc}
        # alleen scènes met een eigen knop op het menu ('Casus 1'); 'Volgende' naar de Samenvatting telt niet
        guests = [c for c in chapters if c is not host and c['num'] is None and c['scenes'] and
                  all(sc in host['jumps'] and sc not in own for sc in c['scenes'] if sc) and
                  all(lb.lower() in host.get('menu_words', set()) for lb in c.get('labels', []))]
        if not guests:
            continue
        for g in guests:
            if not any(it['kind'] == 'h2' for it in g['items'][:1]):
                g['items'] = [{'kind': 'h2', 'text': g['title']}] + g['items']
            host['items'] = host['items'] + g['items']
            host['slides'] += g['slides']
        host['items'] = host.get('intro', []) + host['items']
        chapters = [c for c in chapters if not any(c is g for g in guests)]
    for i, c in enumerate(chapters, 1):
        c['n'] = i
        for k in ('jumps', 'intro', 'menu_words', 'labels', 'scenes'):
            c.pop(k, None)
    return chapters


# ----------------------------------------------------------------------------- opmaak (ReportLab)

RED = colors.HexColor('#c8102e')
INK = colors.HexColor('#1a1a1a')
MUTED = colors.HexColor('#5f6b76')
BLUE = colors.HexColor('#1f5f8b')
GREEN = colors.HexColor('#1e7b34')
BOX_BG = colors.HexColor('#f4f6f8')
BOX_BORDER = colors.HexColor('#cfd6dc')
RULE = colors.HexColor('#e1e5e9')

FONT, FONT_B, FONT_I = 'Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique'


def register_fonts():
    global FONT, FONT_B, FONT_I
    cands = [
        ('/System/Library/Fonts/Supplemental/Arial.ttf', '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
         '/System/Library/Fonts/Supplemental/Arial Italic.ttf'),
        ('/Library/Fonts/Arial.ttf', '/Library/Fonts/Arial Bold.ttf', '/Library/Fonts/Arial Italic.ttf'),
        ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf'),
    ]
    for r, b, i in cands:
        if os.path.exists(r) and os.path.exists(b):
            try:
                pdfmetrics.registerFont(TTFont('Body', r))
                pdfmetrics.registerFont(TTFont('Body-Bold', b))
                pdfmetrics.registerFont(TTFont('Body-Italic', i if os.path.exists(i) else r))
                from reportlab.pdfbase.pdfmetrics import registerFontFamily
                registerFontFamily('Body', normal='Body', bold='Body-Bold', italic='Body-Italic', boldItalic='Body-Bold')
                FONT, FONT_B, FONT_I = 'Body', 'Body-Bold', 'Body-Italic'
                return
            except Exception:
                pass


def styles():
    S = {}
    S['body'] = ParagraphStyle('body', fontName=FONT, fontSize=10.5, leading=15, textColor=INK, spaceAfter=6)
    S['li'] = ParagraphStyle('li', parent=S['body'], spaceAfter=2)
    S['h1'] = ParagraphStyle('h1', fontName=FONT_B, fontSize=24, leading=28, textColor=INK, spaceAfter=4)
    S['kicker'] = ParagraphStyle('kicker', fontName=FONT, fontSize=11, leading=14, textColor=RED, spaceAfter=2)
    S['h2'] = ParagraphStyle('h2', fontName=FONT_B, fontSize=15, leading=19, textColor=INK, spaceBefore=14, spaceAfter=6)
    S['h3'] = ParagraphStyle('h3', fontName=FONT_B, fontSize=11.5, leading=15, textColor=INK, spaceBefore=10, spaceAfter=4)
    S['formula'] = ParagraphStyle('formula', fontName=FONT_B, fontSize=11, leading=16, textColor=INK, alignment=TA_CENTER,
                                  spaceBefore=6, spaceAfter=8)
    S['note'] = ParagraphStyle('note', parent=S['body'], fontName=FONT_I, textColor=MUTED)
    S['caption'] = ParagraphStyle('caption', parent=S['body'], fontSize=9, leading=12, textColor=MUTED, alignment=TA_CENTER)
    S['qlabel'] = ParagraphStyle('qlabel', fontName=FONT_B, fontSize=8.5, leading=11, textColor=BLUE, spaceAfter=4)
    S['q'] = ParagraphStyle('q', parent=S['body'], fontName=FONT_B, spaceAfter=6)
    S['qbody'] = ParagraphStyle('qbody', parent=S['body'], spaceAfter=4)
    S['opt'] = ParagraphStyle('opt', parent=S['body'], spaceAfter=0, leading=14)
    S['optc'] = ParagraphStyle('optc', parent=S['opt'], fontName=FONT_B, textColor=GREEN)
    S['fb_label'] = ParagraphStyle('fb_label', parent=S['body'], fontName=FONT_B, textColor=GREEN, spaceAfter=2)
    S['fb'] = ParagraphStyle('fb', parent=S['body'], spaceAfter=3)
    S['cover_k'] = ParagraphStyle('cover_k', fontName=FONT, fontSize=12, leading=16, textColor=RED, spaceAfter=8)
    S['cover_t'] = ParagraphStyle('cover_t', fontName=FONT_B, fontSize=30, leading=36, textColor=INK, spaceAfter=24)
    S['toc_h'] = ParagraphStyle('toc_h', fontName=FONT_B, fontSize=13, leading=17, textColor=INK, spaceBefore=10, spaceAfter=8)
    S['toc'] = ParagraphStyle('toc', fontName=FONT, fontSize=10.5, leading=17, textColor=INK, leftIndent=18)
    S['answer'] = ParagraphStyle('answer', parent=S['body'], fontName=FONT_B, textColor=GREEN)
    return S


class CheckBox(Flowable):
    """Echt PDF-vinkvakje (AcroForm); het juiste antwoord staat aangevinkt."""
    counter = 0

    def __init__(self, checked, size=10.5):
        Flowable.__init__(self)
        self.checked = checked
        self.size = size
        CheckBox.counter += 1
        self.name = 'cpe_opt_%04d' % CheckBox.counter

    def wrap(self, aw, ah):
        return self.size, self.size + 3

    def draw(self):
        c = self.canv
        ax, ay = c.absolutePosition(0, 1.5)
        c.acroForm.checkbox(name=self.name, x=ax, y=ay, size=self.size, checked=self.checked, buttonStyle='check',
                            borderWidth=1, borderColor=colors.HexColor('#7a8791'), fillColor=colors.white,
                            textColor=GREEN if self.checked else INK, forceBorder=True)


class Rule(Flowable):
    def __init__(self, width, color=RED, thickness=1.2, space=6):
        Flowable.__init__(self)
        self.w, self.color, self.t, self.space = width, color, thickness, space

    def wrap(self, aw, ah):
        self.w = aw
        return aw, self.t + self.space

    def draw(self):
        self.canv.setStrokeColor(self.color)
        self.canv.setLineWidth(self.t)
        self.canv.line(0, self.space / 2, self.w, self.space / 2)


class ChapterHeading(Paragraph):
    """h1 met TOC-melding."""
    pass


def image_flowable(data_uri, w, h, max_w, max_h):
    try:
        head, b64 = data_uri.split(',', 1)
        raw = base64.b64decode(b64)
        im = PILImage.open(io.BytesIO(raw))
        im.load()
        if im.mode not in ('RGB', 'RGBA', 'L'):
            im = im.convert('RGBA')
        # transparantie op wit
        if im.mode == 'RGBA':
            bg = PILImage.new('RGB', im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[3])
            im = bg
        pw, ph = im.size
        scale = min(max_w / pw, max_h / ph, (w or pw) * 0.42 / pw if w else 1.0)
        # bron-pixels op 1920-breed canvas -> ongeveer 0.42 pt per px (170mm/1920px)
        scale = min(max_w / pw, max_h / ph, max(scale, 0.25))
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=82, optimize=True)
        buf.seek(0)
        return Image(buf, width=pw * scale, height=ph * scale)
    except Exception as e:
        return None


def para(runs, style):
    m = runs_to_markup(runs)
    return Paragraph(m or ' ', style)


BULLETS = ['•', '–', '·', '·']


def li_paragraph(runs, tag, style):
    """Lijstitem in een vraagkader: opsommingsteken + inspringen per niveau."""
    base, level = split_tag(tag)
    st = ParagraphStyle('li%d' % level, parent=style, leftIndent=style.leftIndent + 12 + 14 * level, bulletIndent=style.leftIndent + 14 * level)
    return Paragraph('<bullet>%s</bullet>%s' % (BULLETS[min(level, 3)], runs_to_markup(runs)), st)


def list_flowable(tag, items, S, level=0):
    """Geneste lijst: items op niveau n+1 komen als sublijst onder het voorgaande item op niveau n."""
    lis = []
    i = 0
    while i < len(items):
        it = items[i]
        node = [para(it['runs'], S['li'])]
        j = i + 1
        while j < len(items) and items[j]['level'] > level:
            j += 1
        if j > i + 1:
            sub = items[i + 1:j]
            node.append(list_flowable(sub[0].get('tag', 'ul'), sub, S, level + 1))
        lis.append(ListItem(node, leftIndent=14))
        i = j
    if tag == 'ol':
        return ListFlowable(lis, bulletType='1', bulletFontName=FONT, bulletFontSize=10, leftIndent=16, spaceAfter=6 if level == 0 else 2)
    return ListFlowable(lis, bulletType='bullet', bulletFontName=FONT, bulletFontSize=8 if level == 0 else 9, start=BULLETS[min(level, 3)],
                        leftIndent=16, bulletOffsetY=-1, spaceAfter=6 if level == 0 else 2)


def quiz_flowable(q, S, width):
    inner_w = width - 2 * 10
    rows = []
    q['question'] = merge_broken_tuples(q['question'])
    q['feedback'] = merge_broken_tuples(q['feedback'])
    rows.append([Paragraph('VRAAG', S['qlabel'])])
    for qi, (kind, runs, tag) in enumerate(q['question']):
        txt = ''.join(r[0] for r in runs)
        if kind == 'li':
            rows.append([li_paragraph(runs, tag, S['qbody'])])
        elif qi > 0 and INSTRUCTION_RE.match(norm(txt)) and not txt.strip().endswith('?'):
            rows.append([para(runs, S['qbody'])])
        else:
            rows.append([para(runs, S['q'])])
    if q.get('prompt'):
        rows.append([Paragraph(esc(q['prompt']), S['qbody'])])
    if q.get('hotspot'):
        rows.append([Paragraph('Aanwijsvraag: klik in de e-module de juiste plek(ken) op de afbeelding aan. '
                               'De juiste plaatsing staat in de toelichting.', S['note'])])
    if q['pairs']:
        data = [[Paragraph('<b>Begrip</b>', S['opt']), Paragraph('<b>Hoort bij</b>', S['opt'])]]
        for a, b in q['pairs']:
            data.append([Paragraph(esc(a), S['opt']), Paragraph('→ ' + esc(b), S['optc'])])
        t = Table(data, colWidths=[inner_w * 0.45, inner_w * 0.55])
        t.setStyle(TableStyle([('LINEBELOW', (0, 0), (-1, -1), 0.5, BOX_BORDER), ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                               ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                               ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
        rows.append([t])
    for text, correct in q['options']:
        opt = Table([[CheckBox(correct), Paragraph(esc(text), S['optc'] if correct else S['opt'])]],
                    colWidths=[18, inner_w - 18])
        opt.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0),
                                 ('RIGHTPADDING', (0, 0), (-1, -1), 0), ('TOPPADDING', (0, 0), (-1, -1), 1),
                                 ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
        rows.append([opt])
    if q['answers']:
        label = 'Juiste antwoorden: ' if len(q['answers']) > 1 else 'Juiste antwoord: '
        rows.append([Paragraph(label + esc(', '.join(q['answers'])), S['answer'])])
    if q.get('open'):
        rows.append([Paragraph('Open vraag: schrijf je antwoord op en vergelijk het met het voorbeeld hieronder.', S['note'])])
    if q['feedback']:
        rows.append([Spacer(1, 4)])
        rows.append([Paragraph('Voorbeeldantwoord' if q.get('open') else 'Toelichting', S['fb_label'])])
        for kind, runs, tag in q['feedback']:
            if kind == 'li':
                rows.append([li_paragraph(runs, tag, S['fb'])])
            else:
                rows.append([para(runs, S['fb'])])
    t = Table(rows, colWidths=[width])
    t.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), BOX_BG), ('BOX', (0, 0), (-1, -1), 0.8, BOX_BORDER),
                           ('LEFTPADDING', (0, 0), (-1, -1), 10), ('RIGHTPADDING', (0, 0), (-1, -1), 10),
                           ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                           ('TOPPADDING', (0, 0), (0, 0), 8), ('BOTTOMPADDING', (0, -1), (0, -1), 8)]))
    t.spaceBefore = 8
    t.spaceAfter = 10
    return t


class Doc(BaseDocTemplate):
    def __init__(self, path, title, **kw):
        BaseDocTemplate.__init__(self, path, pagesize=A4, leftMargin=22 * mm, rightMargin=22 * mm, topMargin=20 * mm,
                                 bottomMargin=20 * mm, title=title, author='Canvas PDF Exporter', **kw)
        frame = Frame(self.leftMargin, self.bottomMargin, self.width, self.height, id='main', leftPadding=0,
                      rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([PageTemplate(id='page', frames=[frame], onPage=self._footer)])
        self.doc_title = title

    def _footer(self, canv, doc):
        canv.saveState()
        canv.setFont(FONT, 8.5)
        canv.setFillColor(MUTED)
        canv.drawRightString(self.pagesize[0] - self.rightMargin, 11 * mm, '%d' % doc.page)
        canv.drawString(self.leftMargin, 11 * mm, self.doc_title)
        canv.restoreState()

    def afterFlowable(self, fl):
        if isinstance(fl, ChapterHeading):
            self.notify('TOCEntry', (0, fl.toc_text, self.page))


def render(chapters, title, out_path):
    register_fonts()
    S = styles()
    doc = Doc(out_path, title)
    W = doc.width
    story = []
    # Omslag + inhoud
    story.append(Spacer(1, 40 * mm))
    story.append(Paragraph('E-module', S['cover_k']))
    story.append(Paragraph(esc(title), S['cover_t']))
    story.append(Rule(W))
    story.append(Paragraph('Inhoud', S['toc_h']))
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle('toc0', fontName=FONT, fontSize=10.5, leading=17, leftIndent=0)]
    toc.dotsMinLevel = 0
    story.append(toc)
    for ch in chapters:
        story.append(PageBreak())
        h = ChapterHeading(esc(ch['title']), S['h1'])
        h.toc_text = ch['title']
        story.append(KeepTogether(([Paragraph('Hoofdstuk %d' % ch['num'], S['kicker'])] if ch.get('num') else []) + [h, Rule(W), Spacer(1, 4)]))
        items = ch['items']
        i = 0
        while i < len(items):
            it = items[i]
            k = it['kind']
            if k in ('h2', 'h3'):
                # Kop blijft bij het volgende element.
                head = Paragraph(esc(it['text']), S[k])
                nxt = None
                if i + 1 < len(items):
                    nxt = flowable_for(items[i + 1], S, W)
                    i += 1
                if k == 'h2':
                    story.append(CondPageBreak(45 * mm))
                story.append(KeepTogether([head] + ([nxt] if nxt else [])))
            else:
                f = flowable_for(it, S, W)
                if f is not None:
                    story.append(f)
            i += 1
    doc.multiBuild(story)


def flowable_for(it, S, W):
    k = it['kind']
    if k == 'popup':
        return KeepTogether(([Paragraph('<b>%s</b>' % esc(it['title']), S['body'])] if it['title'] else []) +
                            [Paragraph(runs_to_markup(r), S['body']) for _, r, _ in it['items']])
    if k == 'table':
        return KeepTogether([Paragraph(' — '.join(' '.join(runs_to_markup(r) for _, r, _ in cell) for cell in row), S['body']) for row in it['rows']])
    if k == 'overview':
        return Paragraph('<b>In dit hoofdstuk:</b> ' + ', '.join(esc(t) for t in it['topics']), S['body'])
    if k == 'figcaps':
        fl = []
        img = flowable_for({'kind': 'image', 'data': it.get('data'), 'w': it['w'], 'h': it['h']}, S, W)
        if img is not None:
            fl.append(img)
        fl.append(Paragraph(' | '.join(' '.join(runs_to_markup(r) for _, r, _ in c) for c in it['caps']), S['caption']))
        return KeepTogether(fl)
    if k == 'figrow':
        fl = []
        for f in it['figs']:
            img = flowable_for({'kind': 'image', 'data': f.get('data'), 'w': f['w'], 'h': f['h']}, S, W)
            if img is not None:
                fl.append(img)
            fl.append(Paragraph(' '.join(runs_to_markup(r) for _, r, _ in f['items']), S['caption']))
        return KeepTogether(fl) if fl else None
    if k in ('labelrows', 'cards', 'aside'):
        fl = []
        if k == 'labelrows':
            for l, v in it['rows']:
                fl.append(Paragraph('<b>%s</b>  %s' % (esc(l), ' '.join(runs_to_markup(r) for _, r, _ in v)), S['body']))
        elif k == 'cards':
            for t, v in it['cards']:
                fl.append(Paragraph('<b>%s</b>  %s' % (esc(t), ' '.join(runs_to_markup(r) for _, r, _ in v)), S['body']))
        else:
            fl += [f for f in (flowable_for(x, S, W) for x in it['items']) if f is not None]
            img = dict(it['image'], kind='image')
            f = flowable_for(img, S, W)
            if f is not None:
                fl.append(f)
            fl += [Paragraph(esc(c), S['caption']) for c in it.get('captions') or []]
        return KeepTogether(fl) if fl else None
    if k == 'p':
        return para(it['runs'], S['body'])
    if k == 'list':
        return list_flowable(it['tag'], it['items'], S)
    if k == 'h2' or k == 'h3':
        return Paragraph(esc(it['text']), S[k])
    if k == 'formula':
        return Paragraph(esc(it['text']), S['formula'])
    if k == 'note':
        return Paragraph('[' + esc(it['text']) + ']', S['note'])
    if k == 'caption':
        return Paragraph(esc(it['text']), S['caption'])
    if k == 'image':
        img = image_flowable(it['data'], it['w'], it['h'], W * 0.9, 68 * mm)
        if img is None:
            return None
        img.hAlign = 'CENTER'
        img.spaceBefore = 4
        img.spaceAfter = 8
        return img
    if k == 'quiz':
        return quiz_flowable(it, S, W)
    if k == 'gallery':
        cells = []
        cw = (W - 6 * mm) / 2
        for im in it['images']:
            f = image_flowable(im['data'], im['w'], im['h'], cw, 52 * mm)
            if f is not None:
                cells.append(f)
        if not cells:
            return None
        rows = [cells[i:i + 2] for i in range(0, len(cells), 2)]
        for r in rows:
            while len(r) < 2:
                r.append('')
        t = Table(rows, colWidths=[cw + 3 * mm, cw + 3 * mm], hAlign='CENTER')
        t.setStyle(TableStyle([('ALIGN', (0, 0), (-1, -1), 'CENTER'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                               ('LEFTPADDING', (0, 0), (-1, -1), 2), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                               ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]))
        t.spaceBefore = 4
        t.spaceAfter = 8
        return t
    return None



# ----------------------------------------------------------------------------- opmaak (HTML, zelfde stijl als Rise-export)

def rise_css():
    """Gedeelde print-CSS uit book-to-print.py (één bron voor Rise én Storyline)."""
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location('book_to_print', os.path.join(here, 'book-to-print.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.CSS


_OPT_SEQ = [0]


def h_runs(runs):
    return runs_to_markup(runs).replace('<b>', '<strong>').replace('</b>', '</strong>')


def h_list(items):
    """Geneste lijst (level per item) -> ul/ol-HTML."""
    out = []
    stack = []  # open tags

    def open_tag(tag):
        out.append('<%s>' % tag)
        stack.append(tag)

    def close_to(level):
        while len(stack) > level:
            out.append('</li></%s>' % stack.pop())
    for it in items:
        level = int(it.get('level') or 0) + 1
        tag = it.get('tag') or 'ul'
        if level > len(stack):
            while len(stack) < level:
                if stack:
                    out[-1] = out[-1]  # li blijft open voor sublijst
                open_tag(tag)
                if len(stack) < level:
                    out.append('<li>')
            out.append('<li>' + h_runs(it['runs']))
        else:
            close_to(level)
            out.append('</li><li>' + h_runs(it['runs']))
    close_to(0)
    html_s = ''.join(out)
    # opruimen: lege <li> die als houder voor een sublijst zijn aangemaakt
    return html_s.replace('<li><ul>', '<li class="sub"><ul>').replace('<li><ol>', '<li class="sub"><ol>')


def h_quiz(q):
    body = ['<div class="quiz-label">Vraag</div>']
    qs = []
    for kind, runs, tag in merge_broken_tuples(q['question']):
        if kind == 'li':
            qs.append('<p>• ' + h_runs(runs) + '</p>')
        else:
            qs.append('<p>' + h_runs(runs) + '</p>')
    body.append('<div class="question">' + ''.join(qs) + '</div>')
    if q.get('prompt'):
        body.append('<p>' + esc(q['prompt']) + '</p>')
    if q.get('hotspot'):
        body.append('<p class="hint">Aanwijsvraag: klik in de e-module de juiste plek(ken) op de afbeelding aan. De juiste plaatsing staat in de toelichting.</p>')
    if q.get('open'):
        body.append('<p class="hint">Open vraag: schrijf je antwoord op en vergelijk het met het voorbeeld hieronder.</p>')
    if q.get('solution', {}).get('data'):
        body.append('<figure class="solution"><img src="%s" alt=""><figcaption>Juiste oplossing</figcaption></figure>' % q['solution']['data'])
    if q['pairs']:
        rows = ''.join('<tr><td>%s</td><td>%s <span class="ok">✓</span></td></tr>' % (esc(a), esc(b)) for a, b in q['pairs'])
        body.append('<table class="matching"><tr><th>Begrip</th><th>Hoort bij</th></tr>' + rows + '</table>')
    if q['options']:
        kind_q = 'check' if q.get('multi') else 'radio'
        lis = []
        for text, correct in q['options']:
            _OPT_SEQ[0] += 1
            m = 'cpeopt%04d' % _OPT_SEQ[0]
            lis.append('<li data-opt="%s" data-mark="%s"%s><span class="cpe-mark">%s</span>%s</li>' % (
                esc(text).replace('"', '&quot;'), m, ' data-correct="1" class="correct"' if correct else '', m, esc(text)))
        body.append('<ul class="options %s">%s</ul>' % (kind_q, ''.join(lis)))
    if q['answers']:
        label = 'Juiste antwoorden' if len(q['answers']) > 1 else 'Juiste antwoord'
        body.append('<div class="answer"><span class="answer-label">%s:</span> %s</div>' % (label, esc(', '.join(q['answers']))))
    if q['feedback']:
        fb = []
        for kind, runs, tag in merge_broken_tuples(q['feedback']):
            fb.append(('<p>• ' if kind == 'li' else '<p>') + h_runs(runs) + '</p>')
        for im in q.get('fb_images') or []:
            if im.get('data'):
                fb.append('<figure class="fb-image"><img src="%s" alt="%s"></figure>' % (im['data'], esc(im.get('alt', ''))))
        body.append('<div class="answer"><span class="answer-label">%s:</span> %s</div>' % (
            'Voorbeeldantwoord' if q.get('open') else 'Toelichting', ''.join(fb)))
    return '<div class="quiz">' + ''.join(body) + '</div>'


def h_blockitems(blockitems, merge=True):
    """Lijst van (kind, runs, tag) uit block_to_items -> HTML (alinea's en eenvoudige lijsten).
    merge=False voor inhoud die uit meerdere losse vakjes komt (kaarten, tegels, tabellen)."""
    out, lst = [], None
    for kind, runs, tag in (merge_broken_tuples(blockitems) if merge else blockitems):
        if kind == 'li':
            base, level = split_tag(tag)
            if lst is None:
                out.append('<%s>' % base)
                lst = base
            out.append('<li>' + h_runs(runs) + '</li>')
        else:
            if lst:
                out.append('</%s>' % lst)
                lst = None
            out.append('<p>' + h_runs(runs) + '</p>')
    if lst:
        out.append('</%s>' % lst)
    return ''.join(out)


def h_cell(items):
    """Celinhoud. Eén regel met een opsommingsstreepje (' - 1 mA') wordt zonder streepje getoond: de cel is al een rij."""
    if len(items) == 1:
        return h_runs(items[0][1])
    return h_blockitems(items, merge=False)


def h_item(it):
    k = it['kind']
    if k == 'popup':
        head = '<div class="pu-head">%s</div>' % esc(it['title']) if it['title'] else ''
        return '<div class="popup%s">%s<div class="pu-body">%s</div></div>' % ('' if it['title'] else ' untitled', head, h_blockitems(it['items']))
    if k == 'table':
        rows = ''.join('<tr>%s</tr>' % ''.join('<td>%s</td>' % h_cell(c) for c in row) for row in it['rows'])
        return '<table class="sltable">%s</table>' % rows
    if k == 'overview':
        return '<div class="overview"><div class="ov-head">In dit hoofdstuk</div><ul>%s</ul></div>' % ''.join('<li>%s</li>' % esc(t) for t in it['topics'])
    if k == 'figcaps':
        caps = ''.join('<div>%s</div>' % h_blockitems(c, merge=False) for c in it['caps'])
        img = '<img src="%s" alt="%s">' % (it['data'], esc(it.get('alt', ''))) if it.get('data') else ''
        return '<figure class="figcaps">%s<div class="caps n%d">%s</div></figure>' % (img, min(len(it['caps']), 5), caps)
    if k == 'figrow':
        figs = ''.join('<figure><div class="ph"><img src="%s" alt="%s"></div><figcaption>%s</figcaption></figure>'
                       % (f['data'], esc(f.get('alt', '')), h_blockitems(f['items'], merge=False)) for f in it['figs'] if f.get('data'))
        return '<div class="figrow n%d">%s</div>' % (min(len(it['figs']), 5), figs)
    if k == 'labelrows':
        rows = ''.join('<div class="labelrow"><div class="lbl">%s</div><div class="val">%s</div></div>' % (esc(l), h_blockitems(v))
                       for l, v in it['rows'])
        return '<div class="labelrows">%s</div>' % rows
    if k == 'cards':
        cs = ''.join('<div class="slcard"><div class="slcard-title">%s</div>%s</div>' % (esc(t), h_blockitems(v, merge=False)) for t, v in it['cards'])
        n = len(it['cards'])
        return '<div class="slcards n%d">%s</div>' % (min(n, 3) if n > 1 else 2, cs)
    if k == 'aside':
        img = it['image']
        if not img.get('data'):
            return ''.join(h_item(x) for x in it['items'])
        caps = ''.join('<figcaption>%s</figcaption>' % esc(c) for c in it.get('captions') or [])
        return ('<div class="image-aside"><div class="text">%s</div><figure><img src="%s" alt="%s">%s</figure></div>'
                % (''.join(h_item(x) for x in it['items']), img['data'], esc(img.get('alt', '')), caps))
    if k == 'h2':
        return '<h2 class="section">%s</h2>' % esc(it['text'])
    if k == 'h3':
        return '<h3>%s</h3>' % esc(it['text'])
    if k == 'p':
        return '<div class="block"><p>%s</p></div>' % h_runs(it['runs'])
    if k == 'list':
        return '<div class="block">%s</div>' % h_list(it['items'])
    if k == 'formula':
        return '<div class="block formula"><p>%s</p></div>' % esc(it['text'])
    if k == 'caption':
        return '<p class="note">%s</p>' % esc(it['text'])
    if k == 'note':
        return '<p class="note">[%s]</p>' % esc(it['text'])
    if k == 'image':
        if not it.get('data'):
            return ''
        cls = 'wide' if it['w'] > it['h'] * 1.6 else ''
        return '<figure class="%s"><img src="%s" alt="%s"></figure>' % (cls, it['data'], esc(it.get('alt', '')))
    if k == 'gallery':
        figs = ''.join('<figure><img src="%s" alt="%s"></figure>' % (im['data'], esc(im.get('alt', ''))) for im in it['images'] if im.get('data'))
        return '<div class="gallery">%s</div>' % figs if figs else ''
    if k == 'quiz':
        return h_quiz(it)
    return ''


def render_html(chapters, title, version=''):
    _OPT_SEQ[0] = 0
    toc = ''.join('<li><span class="tn">%s</span>%s</li>' % (('%d.' % ch['num']) if ch.get('num') else '', esc(ch['title'])) for ch in chapters)
    ver = '<p class="cover-version">%s</p>' % esc(version) if version else ''
    cover = ('<section class="cover"><p class="cover-kicker">E-module</p><h1>%s</h1>%s'
             '<div class="toc"><h2>Inhoud</h2><ul class="toc-list">%s</ul></div></section>') % (esc(title), ver, toc)
    secs = []
    for ch in chapters:
        parts, its, done = [], ch['items'], set()
        for i, it in enumerate(its):
            if i in done:
                continue  # pop-up al samen met het begrip gezet
            nxt = its[i + 1] if i + 1 < len(its) else None
            if nxt is not None and nxt['kind'] == 'popup' and nxt['title'] and it['kind'] in ('p', 'list', 'h3') \
                    and nxt['title'].lower() in item_text(it).lower():
                # begrip + uitleg bij elkaar houden (niet over een paginagrens splitsen)
                parts.append('<div class="keep-pair">%s%s</div>' % (h_item(it), h_item(nxt)))
                done.add(i + 1)
            else:
                parts.append(h_item(it))
        body = ''.join(parts)
        kicker = '<p class="kicker">Hoofdstuk %d</p>' % ch['num'] if ch.get('num') else ''
        secs.append('<section class="chapter"><div class="chapter-header">%s<h1>%s</h1></div>%s</section>'
                    % (kicker, esc(ch['title']), body))
    css = rise_css().replace('__CPE_TITLE__', title.replace('\\', '\\\\').replace('"', '\\"'))
    css += ('\n.labelrows { margin: .6em 0 1.6em; }'
            '\n.labelrow { display: flex; gap: 1.2em; align-items: center; margin: 0 0 .9em; break-inside: avoid; }'
            '\n.labelrow .lbl { flex: 0 0 34%; background: #14324f; color: #fff; font-weight: 700; font-size: .95em; letter-spacing: .02em;'
            ' padding: .45em 1.6em .45em .9em; border-radius: 6px 0 0 6px; clip-path: polygon(0 0, calc(100% - 1em) 0, 100% 50%, calc(100% - 1em) 100%, 0 100%); }'
            '\n.labelrow .val { flex: 1 1 auto; }'
            '\n.labelrow .val p { margin: 0; }'
            '\n.slcards { display: grid; gap: 1em; margin: .8em 0 1.6em; }'
            '\n.slcards.n2 { grid-template-columns: 1fr 1fr; } .slcards.n3 { grid-template-columns: 1fr 1fr 1fr; }'
            '\n.slcard { border: 1px solid #d9d9d9; border-radius: 6px; padding: .9em 1.1em; break-inside: avoid; }'
            '\n.slcard .slcard-title { font-weight: 700; margin-bottom: .4em; }'
            '\n.slcard p:last-child { margin-bottom: 0; }'
            '\n.image-aside figcaption { text-align: left; }'
            '\n.cover .cover-version { color: #555; margin: -.6em 0 0; }'
            '\n.cover .toc ul.toc-list { list-style: none; margin: 0; padding: 0; font-size: 12pt; line-height: 1.7; }'
            '\n.cover .toc .tn { display: inline-block; min-width: 1.8em; color: #bc1413; }'
            '\n.overview { border: 1px solid #cfd6dc; border-radius: 8px; overflow: hidden; max-width: 75%; margin: .4em 0 1.8em; break-inside: avoid; }'
            '\n.overview .ov-head { background: #14324f; color: #fff; font-weight: 700; padding: .55em 1.1em; }'
            '\n.overview ul { list-style: none; margin: 0; padding: .6em 1.1em .7em; }'
            '\n.overview li { margin: .3em 0; }'
            '\n.figrow { display: grid; gap: 1em; margin: 1em 0 1.6em; break-inside: avoid; align-items: start; }'
            '\n.figrow.n2 { grid-template-columns: repeat(2, 1fr); } .figrow.n3 { grid-template-columns: repeat(3, 1fr); }'
            '\n.figrow.n4 { grid-template-columns: repeat(4, 1fr); } .figrow.n5 { grid-template-columns: repeat(5, 1fr); }'
            '\n.figrow figure { margin: 0; }'
            '\n.figrow .ph { height: 34mm; display: flex; align-items: flex-end; justify-content: center; }'
            '\n.figrow .ph img { max-height: 34mm; max-width: 100%; width: auto; margin: 0; }'
            '\n.figrow figcaption { font-size: 9.5pt; line-height: 1.35; color: #333; font-style: italic; text-align: center; margin-top: .5em; }'
            '\n.figrow figcaption p { margin: 0; }'
            '\ntable.sltable { width: auto; min-width: 60%; border-collapse: collapse; margin: .6em 0 1.6em; font-size: 1em; break-inside: avoid; }'
            '\ntable.sltable td { border: none; border-bottom: 1px solid #e3e6e9; padding: .35em 1.4em .35em 0; vertical-align: top; }'
            '\ntable.sltable td:first-child { white-space: nowrap; font-weight: 700; color: #14324f; }'
            '\ntable.sltable tr:last-child td { border-bottom: none; }'
            '\n.popup { border: 2px solid #14324f; margin: .5em 0 1.4em 1.5em; break-inside: avoid; }'
            '\n.popup .pu-head { background: #14324f; color: #fff; font-weight: 700; padding: .45em 1em; }'
            '\n.popup .pu-body { padding: .7em 1em .8em; }'
            '\n.popup .pu-body p:last-child { margin-bottom: 0; }'
            '\n.keep-pair { break-inside: avoid; }'
            '\n.quiz figure.solution { margin: .6em 0 .9em; }'
            '\n.quiz .answer figure.fb-image { margin: .6em 0 0; }'
            '\nfigure.figcaps { margin: .8em 0 1.1em; break-inside: avoid; }'
            '\nfigure.figcaps img { width: 100%; height: auto; display: block; }'
            '\nfigure.figcaps .caps { display: grid; gap: 4mm; margin-top: .4em; text-align: center; font-size: .92em; }'
            '\nfigure.figcaps .caps.n2 { grid-template-columns: repeat(2, 1fr); }'
            '\nfigure.figcaps .caps.n3 { grid-template-columns: repeat(3, 1fr); }'
            '\nfigure.figcaps .caps.n4 { grid-template-columns: repeat(4, 1fr); }'
            '\nfigure.figcaps .caps.n5 { grid-template-columns: repeat(5, 1fr); }'
            '\nfigure.figcaps .caps p { margin: 0; }'
            '\n.quiz .answer figure.fb-image img { max-height: 75mm; max-width: 100%; }'
            '\n.quiz figure.solution img { max-height: 80mm; margin: 0 auto; }'
            '\n.quiz figure.solution figcaption { color: #1d6b2f; font-weight: 700; }'
            '\n.sl .quiz .answer p + p { display: block; margin: .5em 0 0; }'
            '\n.popup.untitled { border: none; border-left: 3px solid #14324f; background: #f4f6f8; }'
            '\n.popup.untitled .pu-body { padding: .55em 1em .6em; }')
    css += '\n.gallery { display: flex; flex-wrap: wrap; gap: 1em; margin: 1em 0 1.6em; break-inside: avoid; }\n.gallery figure { flex: 1 1 45%; margin: 0; }\n.gallery figure img { max-height: 55mm; width: auto; }\n.block.formula p { text-align: center; font-weight: 700; }\n'
    return ('<!doctype html><html lang="nl"><head><meta charset="utf-8"><title>%s</title><style>%s</style></head>'
            '<body class="forms sl">%s%s</body></html>') % (esc(title), css, cover, '\n'.join(secs))


def render_via_chrome(chapters, title, out_pdf, version=''):
    """Zelfde route als de Rise-export: HTML + book-to-print.py --ready (Chrome-print + invulbare vakjes)."""
    import subprocess
    import tempfile
    html_s = render_html(chapters, title, version)
    here = os.path.dirname(os.path.abspath(__file__))
    tmp = os.path.join(tempfile.gettempdir(), safe_name(title) + '.print.html')
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(html_s)
    r = subprocess.run([sys.executable, os.path.join(here, 'book-to-print.py'), tmp, '--ready', '--html', tmp, '--pdf', out_pdf],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit('book-to-print.py mislukt:\n' + r.stdout + r.stderr)
    print(r.stdout.strip())

# ----------------------------------------------------------------------------- main

def safe_name(title):
    s = re.sub(r'[^\w\- ]+', '', title, flags=re.U).strip()
    s = re.sub(r'\s+', '-', s)
    return s or 'cursus'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dump')
    ap.add_argument('--pdf')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--model', help='schrijf het documentmodel als JSON (debug)')
    ap.add_argument('--reportlab', action='store_true', help='oude ReportLab-opmaak i.p.v. de Rise-stijl via Chrome')
    a = ap.parse_args()
    with open(a.dump, encoding='utf-8') as f:
        dump = json.load(f)
    course = Course(dump)
    chapters = build_document(course)
    for ch in chapters:
        for it in ch['items']:
            if it['kind'] == 'image':
                it['data'] = course.images.get(it['url'], '')
            if it['kind'] == 'gallery':
                for im in it['images']:
                    im['data'] = course.images.get(im['url'], '')
            if it['kind'] == 'aside':
                it['image']['data'] = course.images.get(it['image']['url'], '')
            if it['kind'] == 'figrow':
                for f in it['figs']:
                    f['data'] = course.images.get(f['url'], '')
            if it['kind'] == 'figcaps':
                it['data'] = course.images.get(it['url'], '')
            if it['kind'] == 'quiz':
                for im in it.get('fb_images') or []:
                    im['data'] = course.images.get(im['url'], '')
            if it['kind'] == 'quiz' and it.get('solution'):
                it['solution']['data'] = course.images.get(it['solution']['url'], '')
    if a.model:
        with open(a.model, 'w', encoding='utf-8') as f:
            json.dump([{**ch, 'items': [{k: v for k, v in i.items() if k not in ('data', 'images')} for i in ch['items']]} for ch in chapters],
                      f, ensure_ascii=False, indent=1)
    if a.report:
        for ch in chapters:
            kinds = Counter(i['kind'] for i in ch['items'])
            print('%2d %-50s %s' % (ch['n'], ch['title'][:50], dict(kinds)))
    out = a.pdf or os.path.join(os.path.dirname(os.path.abspath(a.dump)),
                                safe_name(course.title) + '-' + datetime.datetime.now().strftime('%Y-%m-%d_%H%M') + '.pdf')
    if a.reportlab:
        render(chapters, course.title, out)
    else:
        render_via_chrome(chapters, course.title, out, course.version)
    print('OK', out)


if __name__ == '__main__':
    main()
