#!/usr/bin/env python3
"""Independent, adversarial completeness test for Silverpoint PDF exports.

Written WITHOUT reading the exporter. Works only from the raw *.sp.json source
dumps and the produced PDFs.

Usage:
    python3 tests/silverpoint_verify.py <bron-dir> <pdf-dir> [<bron-dir> <pdf-dir> ...]
        [--out report.json] [--only NAME] [--no-ocr]

For every <bron-dir>/<name>.sp.json the PDF <pdf-dir>/<name>.pdf is checked.
Writes a JSON report (default: ./silverpoint_verify_report.json) and prints a
human-readable summary plus every concrete failure.

Checks
  1 text coverage (word 3-gram coverage of every text-bearing source string)
  2 new / merged / altered words in the PDF body
  3 images (dHash match against images embedded in the PDF; OCR fallback)
  4 videos (thumbnail present + caption with the video name) / YouTube cards
  5 external links (link annotations)
  6 hidden pages linked from visible pages (text + images)
  7 questions (prompt, options, correct answer present and marked, feedback)
  8 inventory of tags/attributes carrying text, flags those never in a PDF
  9 structure: project.xml page titles present
Dependencies: PyMuPDF, PIL, numpy; optional local `tesseract` binary (OCR of
images that are missing from the PDF, to detect "converted to text").
"""
import base64
import collections
import html
import html.parser
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET

import fitz  # PyMuPDF
import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

# --------------------------------------------------------------------------
# Explicit whitelists (the product's intentional omissions)
# --------------------------------------------------------------------------
# Praise-only feedback inside question XAML (the "correct" modalFeedback that
# says nothing but "well done").  Matched against the whole normalised unit.
PRAISE_WHITELIST = [
    r"correct", r"goed zo", r"goed", r"goed antwoord", r"juist",
    r"het antwoord is juist", r"dat is juist", r"prima", r"heel goed",
]
# Wrong-answer lead-ins ("Helaas", "Dat is fout. Het juiste antwoord is") that the
# PDF drops from the incorrect-answer feedback.  NOT whitelisted; reported in their
# own category "feedback-leadin-removed" so they don't drown other omissions.
NEG_LEADIN = (r"(helaas|jammer|fout|onjuist|niet goed|dat is (nog )?niet( helemaal)?( goed| juist)?|"
              r"dat is fout|dat klopt niet|helaas dat is fout|(je|jouw) antwoord is (niet goed|fout|onjuist)|"
              r"helaas het door jou gegeven antwoord is fout)( (het|de) juiste antwoord(en)? (is|zijn)( het)?)?")
# Placeholder / designer strings that are never shown to learners.
DESIGNER_PLACEHOLDERS = {"typ hier uw tekst", "typ hier de subtitel", "ondertitel"}
# Words the exporter itself adds (chapter headings, captions, disclaimer...).
# Used ONLY by the new-word check; any PDF token not in source vocabulary and
# not in this list is reported.
EXPORTER_CHROME_WORDS = set("""
inhoud hoofdstuk vraag toelichting afbeelding video bekijk deze in de e module
pdf versie van nipv gemaakt op is gewijzigd neem dan contact via support oefio nl
zodat kan worden bijgewerkt pop up juiste antwoord antwoorden correct
youtube voorbeeldantwoord vergelijk oplossing hieronder vul januari februari maart april mei juni juli augustus september oktober
november december open link deze e-module audio fragment geluid
""".split())

# Whole PDF lines the exporter adds (disclaimer with date, etc.); their tokens are
# exempt from the new-word check.
EXPORTER_CHROME_LINES = [r"PDF-versie van de NIPV", r"gemaakt op \d+ \w+ \d{4}"]

SCHEMA_URL_RE = re.compile(r"w3\.org/|imsglobal\.org/xsd|schemas\.microsoft\.com|schemas\.openxmlformats", re.I)
HTTP_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.I)
PAGE_RE = re.compile(r"page://([0-9a-fA-F-]{36})")
IMG_RE = re.compile(r"Resources/[^\"<>|]*?\.(?:jpe?g|png|gif|bmp)", re.I)
VID_EXT = (".mp4", ".flv", ".wmv", ".mov", ".m4v", ".webm", ".avi")
AUD_EXT = (".mp3", ".wav", ".m4a", ".wma", ".ogg")


# --------------------------------------------------------------------------
# Normalisation / tokenising
# --------------------------------------------------------------------------
def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("­", "").replace("​", "")
    s = re.sub("[‘’‚‛´`]", "'", s)
    s = re.sub("[“”„‟]", '"', s)
    s = re.sub("[‐-―−]", "-", s)
    return s.lower()


TOK_RE = re.compile(r"[^\W_]+", re.U)


def toks(s):
    return TOK_RE.findall(norm(s))


def localname(t):
    return t.split("}")[-1]


# --------------------------------------------------------------------------
# Source parsing
# --------------------------------------------------------------------------
C1_BLOCKS = {"C1Paragraph", "C1ListItem", "C1TableCell", "C1Document"}


class Unit:
    __slots__ = ("text", "file", "tag", "attr", "kind", "qid", "meta")

    def __init__(self, text, file, tag, attr, kind, qid=None, meta=None):
        self.text, self.file, self.tag, self.attr, self.kind = text, file, tag, attr, kind
        self.qid, self.meta = qid, meta or {}


class HTMLBlocks(html.parser.HTMLParser):
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr",
             "ul", "ol", "table", "blockquote", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks, self.cur, self.hrefs, self.imgs = [], [], [], []

    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.cur)).strip()
        if t:
            self.blocks.append(t)
        self.cur = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.BLOCK:
            self.flush()
        if tag == "br":
            self.cur.append(" ")
        if tag == "a" and a.get("href"):
            self.hrefs.append(a["href"])
        for k in ("src", "data-src", "data"):
            if a.get(k):
                self.imgs.append(a[k])

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.flush()

    def handle_data(self, d):
        self.cur.append(d)

    def close(self):
        super().close()
        self.flush()


def html_blocks(s):
    p = HTMLBlocks()
    try:
        p.feed(s)
        p.close()
    except Exception:
        return [re.sub(r"<[^>]+>", " ", s)], [], []
    return p.blocks, p.hrefs, p.imgs


def parse_xml(s):
    s = re.sub(r"^\s*<\?xml[^>]*\?>", "", s.strip())
    return ET.fromstring(s.encode("utf-8"))


def looks_texty(v):
    if not v or v.startswith("{") or "\n" in v and "<" in v:
        return False
    if re.match(r"^(https?:|mailto:|page:|/|Resources/|#|[a-z]+://)", v, re.I):
        return False
    if ";component/" in v or re.search(r"\.(png|jpe?g|ttf|xaml|mp4|html|qti)\b", v, re.I):
        return False
    if re.search(r"[:;]\s*\S+;", v):  # css-ish
        return False
    words = re.findall(r"[A-Za-zÀ-ÿ]{2,}", v)
    return len(words) >= 2 and " " in v.strip()


TEXTY_ATTR_NAMES = {"text", "title", "descr", "description", "header", "content", "label",
                    "tooltip", "caption", "alt", "displaytext", "question", "answer", "name"}
# attributes that are known to be identifiers / design data, never shown
NON_DISPLAY_ATTRS = {"Name", "Tag", "ScoreID", "Source", "Id", "TargetName", "PlaceholderName",
                     "FontFamily", "extProps", "Wrapper", "EmptyImage", "IconSource",
                     "QTIXML", "htmlcontent", "Xaml", "ItemsXML", "NavigateUri", "Link",
                     "DisplayName", "Subtitle"}


class SourceFile:
    """All content extracted from one XAML file."""

    def __init__(self, path, xaml):
        self.path = path
        self.units = []          # Unit
        self.images = []         # (resource path, tag, attr)
        self.urls = []           # (url, tag, attr)
        self.page_links = []     # page ids
        self.videos = []         # (src, mediatype)
        self.youtube = []        # url
        self.ext_images = []     # remote <img src="http..."> inside htmlcontent
        self.questions = []      # dict
        self.inventory = collections.Counter()   # (tag, attr) -> number of texty values
        self.tags = collections.Counter()
        self.parse_error = None
        try:
            root = parse_xml(xaml)
        except Exception as e:  # pragma: no cover
            self.parse_error = str(e)
            return
        self._walk(root)

    # -- helpers
    def _add(self, text, tag, attr, kind, **kw):
        text = re.sub(r"\s+", " ", text or "").strip()
        if text and toks(text):
            self.units.append(Unit(text, self.path, tag, attr, kind, **kw))
            self.inventory[(tag, attr)] += 1

    def _scan_refs(self, v, tag, attr):
        if attr in ("QTIXML", "Xaml", "ItemsXML", "EmptyImage"):
            return
        if attr == "htmlcontent":  # links come from <a href> only (see html_blocks)
            for m in IMG_RE.findall(v):
                self.images.append((html.unescape(m), tag, attr))
            self.page_links += [m.lower() for m in PAGE_RE.findall(v)]
            return  # parsed structurally elsewhere (avoids xmlns/schema URLs)
        for m in IMG_RE.findall(v):
            self.images.append((html.unescape(m), tag, attr))
        for m in HTTP_RE.findall(v):
            if attr in ("schemaLocation",) or SCHEMA_URL_RE.search(m):
                continue
            self.urls.append((html.unescape(m).rstrip(".,;"), tag, attr))
        self.page_links += [m.lower() for m in PAGE_RE.findall(v)]

    def _walk(self, root):
        parent = {c: p for p in root.iter() for c in p}
        done_runs = set()
        for el in root.iter():
            tag = localname(el.tag)
            self.tags[tag] += 1
            attrs = {localname(k): v for k, v in el.attrib.items()}
            for k, v in attrs.items():
                self._scan_refs(v, tag, k)
            if tag == "C1Document":
                # group runs by nearest block ancestor (paragraph / list item / cell)
                groups = collections.OrderedDict()
                for r in el.iter():
                    if localname(r.tag) != "C1Run":
                        continue
                    a = parent.get(r)
                    while a is not None and localname(a.tag) not in C1_BLOCKS:
                        a = parent.get(a)
                    groups.setdefault(id(a), []).append(r)
                    done_runs.add(r)
                for runs in groups.values():
                    txt = "".join(r.attrib.get("Text", "") for r in runs)
                    self._add(txt, "C1Run", "Text", "c1doc", meta={"box": self._box(el, parent)})
            for k, v in attrs.items():
                if k == "htmlcontent":
                    blocks, hrefs, imgs = html_blocks(v)
                    for b in blocks:
                        self._add(b, tag, k, "html", meta={"box": id(el), "popup": attrs.get("IsPopup")})
                    for h in hrefs:
                        self._scan_refs(h, tag, k + "/a@href")
                    for im in imgs:
                        if re.match(r"https?://", im, re.I):
                            self.ext_images.append((im, tag, k))
                elif k == "Xaml":
                    self._rtb_xaml(v, tag)
                elif k == "ItemsXML":
                    self._carousel(v, tag)
                elif k == "QTIXML":
                    self._qti(v, el, attrs)
                elif k == "Text" and tag == "C1Run":
                    if el not in done_runs:
                        self._add(v, tag, k, "c1doc")
                elif k == "Text" and tag == "TextBlock":
                    if not v.startswith("{"):
                        self._add(v, tag, k, "label")
                elif k == "DisplayName" and tag == "HtmlPlayer":
                    self.youtube.append(v)
                elif k in NON_DISPLAY_ATTRS:
                    continue
                elif (k.lower() in TEXTY_ATTR_NAMES and re.search(r"[A-Za-z]{2}", v)
                      and not v.startswith("{")) or looks_texty(v):
                    self._add(v, tag, k, "other-attr")
            if tag == "MediaPlayer":
                src = attrs.get("Source", "")
                mt = attrs.get("MediaType", "")
                if not mt:
                    mt = "Audio" if src.lower().endswith(AUD_EXT) else "Video"
                self.videos.append((src, mt))
            if el.text and el.text.strip() and tag not in ("String",):
                self._add(el.text, tag, "#text", "text-node")

    @staticmethod
    def _box(el, parent):
        p = el
        while p is not None and localname(p.tag) != "TSRichTextBox":
            p = parent.get(p)
        return id(p) if p is not None else None

    def _rtb_xaml(self, v, tag):
        try:
            r = parse_xml(v)
        except Exception:
            self._add(re.sub(r"<[^>]+>", " ", v), tag, "Xaml", "rtb-xaml")
            return
        for para in r.iter():
            if localname(para.tag) == "Paragraph":
                parts = []
                for e in para.iter():
                    ln = localname(e.tag)
                    if ln == "Run":
                        parts.append(e.attrib.get("Text", "") + (e.text or ""))
                    elif ln == "LineBreak":
                        parts.append(" ")
                    for kk, vv in e.attrib.items():
                        self._scan_refs(vv, "RichTextBox.Xaml/" + ln, localname(kk))
                self._add("".join(parts), "RichTextBox", "Xaml", "rtb-xaml")

    def _carousel(self, v, tag):
        try:
            r = parse_xml(v)
        except Exception:
            self._add(re.sub(r"<[^>]+>", " ", v), tag, "ItemsXML", "carousel")
            return
        for it in r.iter():
            for kk, vv in it.attrib.items():
                kk = localname(kk)
                self._scan_refs(vv, "Carousel/" + localname(it.tag), kk)
                if kk in ("title", "descr") or looks_texty(vv):
                    if "<" in vv:
                        for b in html_blocks(vv)[0]:
                            self._add(b, "Carousel/item", kk, "carousel")
                    else:
                        self._add(vv, "Carousel/item", kk, "carousel")
            if it.text and it.text.strip():
                self._add(it.text, "Carousel/" + localname(it.tag), "#text", "carousel")

    def _qti(self, v, el, attrs):
        qn = len(self.questions)
        qid = "%s#q%d" % (self.path, qn)
        try:
            r = parse_xml(v)
        except Exception as e:
            self.questions.append({"qid": qid, "error": "QTI parse: %s" % e})
            return
        q = {"qid": qid, "itype": attrs.get("InteractionType"), "kinds": [], "options": [],
             "correct": [], "feedback": {}, "prompt": [], "images": [], "order": []}
        ids = {}
        corr_vals = []
        for e in r.iter():
            ln = localname(e.tag)
            if ln.endswith("Interaction"):
                q["kinds"].append(ln)
            for kk, vv in e.attrib.items():
                if ln == "img" and re.match(r"https?://", vv or "", re.I):
                    self.ext_images.append((vv, "QTI/img", localname(kk)))
                    continue
                self._scan_refs(vv, "QTI/" + ln, localname(kk))
                if localname(kk) in ("data", "src", "data-src") and IMG_RE.search(vv or ""):
                    q["images"].append(html.unescape(vv))
            if ln == "correctResponse":
                for x in e:
                    if localname(x.tag) == "value":
                        inner = (x.text or "") + "".join(ET.tostring(y, encoding="unicode") for y in x)
                        corr_vals.append(inner.strip())
        tagged = {}

        def text_of(node):
            s = ET.tostring(node, encoding="unicode")
            s = re.sub(r"<(/?)ns\d+:", r"<\1", s)
            return [b for b in html_blocks(s)[0]]

        for e in r.iter():
            ln = localname(e.tag)
            if ln in ("simpleChoice", "inlineChoice", "gapText", "simpleAssociableChoice",
                      "hottext"):
                t = " ".join(text_of(e))
                ids[e.attrib.get("identifier")] = t
                q["options"].append((e.attrib.get("identifier"), t))
                for b in text_of(e):
                    self._add(b, "QTI/" + ln, "#text", "qti-option", qid=qid)
            elif ln == "gapImg":
                lab = e.attrib.get("objectLabel", "")
                t = " ".join(html_blocks(lab)[0]) if "<" in lab else lab
                ids[e.attrib.get("identifier")] = t
                q["options"].append((e.attrib.get("identifier"), t))
                self._add(t, "QTI/gapImg", "objectLabel", "qti-option", qid=qid)
            elif ln == "modalFeedback":
                fid = e.attrib.get("identifier")
                blocks = text_of(e)
                q["feedback"][fid] = blocks
                for b in blocks:
                    self._add(b, "QTI/modalFeedback[%s]" % fid, "#text", "qti-feedback",
                              qid=qid, meta={"fid": fid})
            elif ln == "prompt":
                for b in text_of(e):
                    q["prompt"].append(b)
                    self._add(b, "QTI/prompt", "#text", "qti-prompt", qid=qid)
        # itemBody text outside interactions
        for body in r.iter():
            if localname(body.tag) == "itemBody":
                b2 = ET.fromstring(ET.tostring(body))
                for p in list(b2.iter()):
                    for c in list(p):
                        if localname(c.tag).endswith("Interaction"):
                            # replace the interaction by a split marker: the PDF fills
                            # the gap with the answer, so text around it is separate
                            mk = ET.Element("p")
                            mk.text = " "
                            mk.tail = c.tail
                            idx = list(p).index(c)
                            p.remove(c)
                            p.insert(idx, mk)
                for b in text_of(b2):
                    q["prompt"].append(b)
                    self._add(b, "QTI/itemBody", "#text", "qti-prompt", qid=qid)
        for cv in corr_vals:
            parts = cv.split()
            if cv in ids:
                q["correct"].append(ids[cv] or "<image option %s>" % cv)
                q["order"].append(ids[cv])
            elif parts and any(p in ids for p in parts):
                # directed pair (gap -> hotspot): only the gap label is text
                q["correct"].append(" / ".join(ids[p] for p in parts if p in ids and ids[p]))
            elif "<" in cv:
                q["correct"].append(" ".join(html_blocks(html.unescape(cv))[0]))
            else:
                q["correct"].append(cv)  # literal (text entry) or point coordinates
        q["correct_ids"] = corr_vals
        self.questions.append(q)


class Module:
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)[:-len(".sp.json")]
        d = json.load(open(path, encoding="utf-8"))
        self.d = d
        self.title = d.get("title", self.name)
        self.files = d.get("files", {})
        self.images = d.get("images", {})
        self.thumbs = d.get("thumbs", {})
        self.media = d.get("media", [])
        proj = parse_xml(self.files["project.xml"])
        self.visible, self.hidden = [], {}
        pmap = {c: pp for pp in proj.iter() for c in pp}
        for sec in proj.iter():
            ln = localname(sec.tag)
            par = pmap.get(sec)
            if par is not None and localname(par.tag) == "Page":
                continue  # nested sub-pages are collected via their top-level <Pages>
            if ln == "Pages":
                for pg in sec.iter():
                    if localname(pg.tag) == "Page":
                        self.visible.append(dict(pg.attrib))
            elif ln == "HiddenPages":
                for pg in sec.iter():
                    if localname(pg.tag) == "Page":
                        self.hidden[pg.attrib["Id"].lower()] = dict(pg.attrib)
        self.sf = {}
        for p, c in self.files.items():
            if p.endswith(".xaml") and p.split("/")[0] in ("Pages", "HiddenPages", "MasterPages"):
                self.sf[p] = SourceFile(p, c)
        vis_ids = {p["Id"].lower() for p in self.visible}
        # hidden pages reachable from visible pages (transitively via hidden pages)
        self.linked_hidden = {}
        frontier = [p["Source"] for p in self.visible if p.get("Source") in self.sf]
        seen = set(frontier)
        while frontier:
            src = frontier.pop()
            for pid in self.sf[src].page_links:
                if pid in self.hidden:
                    hs = self.hidden[pid].get("Source")
                    self.linked_hidden.setdefault(hs, set()).add(src)
                    if hs in self.sf and hs not in seen:
                        seen.add(hs)
                        frontier.append(hs)
                elif pid not in vis_ids:
                    self.linked_hidden.setdefault("MISSING:" + pid, set()).add(src)
        # hidden pages linked only from master pages (menu chrome, e.g. disclaimer)
        self.master_hidden = set()
        for p, s in self.sf.items():
            if p.startswith("MasterPages/"):
                for pid in s.page_links:
                    if pid in self.hidden and self.hidden[pid].get("Source") not in self.linked_hidden:
                        self.master_hidden.add(self.hidden[pid].get("Source"))
        self.required = [p["Source"] for p in self.visible if p.get("Source") in self.sf] + \
            [h for h in self.linked_hidden if h in self.sf]

    def vocab(self):
        v = set()
        for c in self.files.values():
            if not isinstance(c, str):
                continue
            s = html.unescape(html.unescape(c))
            v.update(toks(s))
        for k in list(self.images) + list(self.thumbs) + list(self.media):
            v.update(toks(k))
        v.update(toks(self.title))
        return v

    def bigrams(self):
        bg = set()
        for c in self.files.values():
            if isinstance(c, str):
                t = toks(html.unescape(html.unescape(c)))
                bg.update(zip(t, t[1:]))
        return bg


# --------------------------------------------------------------------------
# PDF side
# --------------------------------------------------------------------------
class PDFDoc:
    def __init__(self, path):
        self.path = path
        self.doc = fitz.open(path)
        self.npages = len(self.doc)
        self.lines = []        # list per page of (text, spans)
        self.invisible = collections.Counter()
        for pg in self.doc:
            pl = []
            dct = pg.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)
            for b in dct["blocks"]:
                for ln in b.get("lines", []):
                    spans = []
                    for s in ln["spans"]:
                        if s["size"] < 3.5 or not s["text"].strip():
                            if s["text"].strip():
                                self.invisible[s["text"].strip()] += 1
                            continue
                        spans.append(s)
                    if spans:
                        txt = spans[0]["text"]
                        for a, b2 in zip(spans, spans[1:]):
                            gap = b2["bbox"][0] - a["bbox"][2]
                            if gap > 0.15 * a["size"] and not txt.endswith(" ") \
                                    and not b2["text"].startswith(" "):
                                txt += " "
                            txt += b2["text"]
                        pl.append((txt, spans))
            self.lines.append(pl)
        self._header_footer()
        self._streams()
        self.links = []
        for i, pg in enumerate(self.doc):
            for l in pg.get_links():
                if l.get("uri"):
                    self.links.append((i, l["uri"]))
        self.images = None

    def _header_footer(self):
        cnt = collections.Counter()
        n = max(1, self.npages)
        for pl in self.lines:
            edge = [t for t, _ in pl[:2]] + [t for t, _ in pl[-2:]]
            for t in set(edge):
                cnt[norm(t).strip()] += 1
        self.repeated = {t for t, c in cnt.items() if c >= max(3, 0.5 * n)}
        self.body_lines = []
        for pl in self.lines:
            out = []
            for idx, (t, sp) in enumerate(pl):
                nt = norm(t).strip()
                edge = idx < 2 or idx >= len(pl) - 2
                if re.match(r"^\d+\s*/\s*\d+$", nt) or (edge and nt in self.repeated):
                    continue
                out.append((t, sp))
            self.body_lines.append(out)

    def _streams(self):
        """Token streams: A dehyphenated across line ends, B hyphen as separator."""
        self.page_tokens_A, self.page_tokens_B = [], []
        for pl in self.body_lines:
            a, b = [], []
            prev_hyph = False
            for t, _ in pl:
                tt = toks(t)
                b += tt
                if prev_hyph and a and tt:
                    a[-1] = a[-1] + tt[0]
                    a += tt[1:]
                else:
                    a += tt
                prev_hyph = bool(re.search(r"[^\W\d_]-\s*$", norm(t)))
            self.page_tokens_A.append(a)
            self.page_tokens_B.append(b)
        self.stream_A = [t for p in self.page_tokens_A for t in p]
        self.stream_B = [t for p in self.page_tokens_B for t in p]
        self.ngrams = {n: set() for n in (1, 2, 3)}
        for st in (self.stream_A, self.stream_B):
            for n in (1, 2, 3):
                for i in range(len(st) - n + 1):
                    self.ngrams[n].add(tuple(st[i:i + n]))
        self.flat = "".join(self.stream_B)
        self.page_ngrams = []
        for a, b in zip(self.page_tokens_A, self.page_tokens_B):
            s = set()
            for st in (a, b):
                for i in range(len(st) - 2):
                    s.add(tuple(st[i:i + 3]))
            self.page_ngrams.append(s)

    def load_images(self):
        if self.images is not None:
            return self.images
        self.images = []
        seen = {}
        for pi, pg in enumerate(self.doc):
            for it in pg.get_images(full=True):
                xref, smask = it[0], it[1]
                if xref in seen:
                    self.images.append((pi, seen[xref]))
                    continue
                h = None
                try:
                    pix = fitz.Pixmap(self.doc, xref)
                    if pix.width < 12 or pix.height < 12:
                        seen[xref] = None
                        continue
                    if smask:
                        try:
                            pix = fitz.Pixmap(pix, fitz.Pixmap(self.doc, smask))
                        except Exception:
                            pass
                    if pix.colorspace and pix.colorspace.n not in (1, 3):
                        pix = fitz.Pixmap(fitz.csRGB, pix)
                    mode = {1: "L", 3: "RGB"}.get(pix.n - pix.alpha, "RGB")
                    if pix.alpha:
                        im = Image.frombytes(mode + "A" if mode == "RGB" else "LA",
                                             (pix.width, pix.height), pix.samples)
                    else:
                        im = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
                    h = (dhash(im), pix.width, pix.height)
                except Exception:
                    h = None
                seen[xref] = h
                self.images.append((pi, h))
        self.images = [(p, h) for p, h in self.images if h]
        return self.images


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------
def flatten(im):
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    return im.convert("L")


def dhash(im, size=8):
    g = flatten(im).resize((size + 1, size), Image.LANCZOS)
    a = np.asarray(g, dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def ham(a, b):
    return bin(a ^ b).count("1")


def decode_data_url(u):
    if not isinstance(u, str) or "," not in u:
        return None
    try:
        raw = base64.b64decode(u.split(",", 1)[1])
        im = Image.open(io.BytesIO(raw))
        im.load()
        return im
    except Exception:
        return None


def image_is_blank(im):
    a = np.asarray(flatten(im).resize((64, 64)))
    return a.std() < 2.0


OCR_OK = shutil.which("tesseract") is not None


def ocr(im):
    if not OCR_OK:
        return None
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "i.png")
        g = flatten(im)
        if g.width < 1200:
            f = 1200 / max(1, g.width)
            g = g.resize((int(g.width * f), int(g.height * f)), Image.LANCZOS)
        g.save(p)
        try:
            out = subprocess.run(["tesseract", p, "-", "-l", "nld+eng", "--psm", "3"],
                                 capture_output=True, text=True, timeout=120).stdout
        except Exception:
            return None
    return out


# --------------------------------------------------------------------------
# Matching helpers
# --------------------------------------------------------------------------
def coverage(tokens, pdf):
    """Return list of booleans, True when token is covered by a PDF n-gram."""
    n = len(tokens)
    if n == 0:
        return []
    if n <= 3:
        ok = tuple(tokens) in pdf.ngrams[n]
        return [ok] * n
    cov = [False] * n
    for i in range(n - 2):
        if tuple(tokens[i:i + 3]) in pdf.ngrams[3]:
            cov[i] = cov[i + 1] = cov[i + 2] = True
    return cov


def missing_runs(tokens, cov):
    runs, cur = [], []
    for t, c in zip(tokens, cov):
        if not c:
            cur.append(t)
        elif cur:
            runs.append(" ".join(cur))
            cur = []
    if cur:
        runs.append(" ".join(cur))
    return runs


def phrase_in(text, pdf, frac=0.9, loose=False):
    t = toks(text)
    if not t:
        return True
    cov = coverage(t, pdf)
    if sum(cov) / len(cov) >= frac:
        return True
    if loose:  # ignore all spaces/hyphens/line breaks (titles, file-name captions)
        return "".join(t) in pdf.flat
    return False


def is_praise(text):
    nt = " ".join(toks(text))
    return any(re.fullmatch(p, nt) for p in PRAISE_WHITELIST)


def norm_url(u):
    u = html.unescape(u).strip().rstrip("/.,;")
    u = re.sub(r"^https?://", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    try:
        from urllib.parse import unquote
        u = unquote(u)
    except Exception:
        pass
    host, _, rest = u.partition("/")
    return host.lower() + ("/" + rest if rest else "")


def find_line_spans(pdf, text, pages=None):
    """Find PDF lines whose tokens start with the first tokens of text; return spans."""
    want = toks(text)[:6]
    if len(toks(text)) <= 3:  # short option: the whole line must equal it
        want = toks(text)
    if not want:
        return []
    hits = []
    rng = pages if pages is not None else range(pdf.npages)
    for pi in rng:
        if pi < 0 or pi >= pdf.npages:
            continue
        for t, sp in pdf.body_lines[pi]:
            lt = toks(t)
            k = min(len(want), len(lt))
            if len(want) <= 3 and lt != want:
                continue
            if k and lt[:k] == want[:k] and (k >= 2 or len(want) == 1):
                hits.append((pi, sp))
    return hits


def check_marking(pdf, q):
    """Locate the question's option lines on one page and compare their styles."""
    opts = q["options"]
    cand = None
    for pi in range(pdf.npages):
        if all(find_line_spans(pdf, t, [pi]) for _, t in opts):
            cand = (cand or []) + [pi]
    if not cand:
        return "unverifiable (options not found together on one page)"
    fb = [b for b in q["feedback"].get("T", []) if len(toks(b)) >= 3 and not is_praise(b)]
    if len(cand) > 1 and fb:
        g = tuple(toks(fb[0])[:3])
        narrowed = [pi for pi in cand if g in pdf.page_ngrams[pi]]
        cand = narrowed or cand
    if len(cand) > 1:
        return "unverifiable (options appear together on %d pages)" % len(cand)
    pi = cand[0]
    cid = set(q["correct_ids"])
    cs, ics = collections.Counter(), collections.Counter()
    for oid, t in opts:
        hits = find_line_spans(pdf, t, [pi])
        if len(hits) != 1:
            return "unverifiable (option %r appears %d times on page %d)" % (t[:30], len(hits), pi + 1)
        (cs if oid in cid else ics)[style_of(hits[0][1])] += 1
    if not cs:
        return "unverifiable (no correct option)"
    if set(cs) & set(ics):
        return "NOT MARKED on p%d (correct %s / incorrect %s)" % (pi + 1, dict(cs), dict(ics))
    return "marked on p%d (correct style %s, others %s)" % (pi + 1, list(cs), list(ics))


def style_of(spans):
    s = spans[0]
    return (bool(s["flags"] & 16) or "bold" in s["font"].lower(), s["color"])


# --------------------------------------------------------------------------
# Verification of one module
# --------------------------------------------------------------------------
def verify(mod, pdf_path, do_ocr=True):
    R = {"module": mod.name, "title": mod.title, "pdf": pdf_path, "failures": [], "info": []}

    def fail(cat, **kw):
        kw["category"] = cat
        R["failures"].append(kw)

    if not os.path.exists(pdf_path):
        fail("pdf-missing", detail="PDF not found")
        return R
    try:
        pdf = PDFDoc(pdf_path)
    except Exception as e:
        fail("pdf-unreadable", detail=str(e))
        return R
    R["pdf_pages"] = pdf.npages
    R["source_pages"] = len(mod.visible)
    R["hidden_pages_linked"] = len([h for h in mod.linked_hidden if h in mod.sf])
    R["invisible_pdf_text_samples"] = dict(pdf.invisible.most_common(5))
    R["invisible_pdf_text_count"] = sum(pdf.invisible.values())
    titles = {norm(p.get("Title", "")).strip() for p in mod.visible} | \
             {norm(p.get("Name", "")).strip() for p in mod.visible} | \
             {norm(h.get("Title", "")).strip() for h in mod.hidden.values()}

    # ---------------- 1 / 6 text coverage
    tot_tok = cov_tok = 0
    per_kind = collections.Counter()
    per_kind_cov = collections.Counter()
    inv_seen = collections.Counter()
    inv_found = collections.Counter()
    whitelisted = []
    c1_div = []
    req = set(mod.required)
    html_boxes = set()
    for p in req:
        for u in mod.sf[p].units:
            if u.kind == "html":
                html_boxes.add((p, u.meta.get("box")))
    for p in mod.required:
        s = mod.sf[p]
        page_title = ""
        for vp in mod.visible:
            if vp.get("Source") == p:
                page_title = vp.get("Title", "")
        if p in mod.linked_hidden:
            page_title = mod.hidden.get(p.split("/")[-1][:-5].lower(), {}).get("Title", "")
        for u in s.units:
            t = toks(u.text)
            nt = " ".join(t)
            key = (u.tag, u.attr)
            inv_seen[key] += 1
            cov = coverage(t, pdf)
            ok = all(cov)
            if sum(cov) / max(1, len(cov)) >= 0.9:
                inv_found[key] += 1
            # whitelist
            wl = None
            if u.kind == "qti-feedback" and is_praise(u.text):
                wl = "praise label in question feedback"
            elif nt in DESIGNER_PLACEHOLDERS:
                wl = "designer placeholder"
            elif u.kind == "label" and norm(u.text).strip() in titles:
                wl = "page/menu title duplicated as in-page label"
            if not ok and u.tag == "QTI/inlineChoice" and u.qid:
                qq = [q for q in s.questions if q.get("qid") == u.qid]
                corr = {" ".join(toks(c)) for q in qq for c in q.get("correct", [])}
                if nt not in corr:
                    R.setdefault("inline_alternatives_not_shown", []).append(
                        {"file": p, "text": u.text, "note": "wrong inline-choice alternative; PDF fills in the correct one"})
                    continue
            if not ok and wl:
                whitelisted.append({"file": p, "text": u.text, "reason": wl})
                continue
            if u.kind == "c1doc" and u.meta.get("box") is not None:
                # C1Document is the Silverlight rendering; htmlcontent of the same box is the
                # HTML5 rendering.  Count C1 separately when an htmlcontent exists.
                pass
            per_kind[u.kind] += len(t)
            per_kind_cov[u.kind] += sum(cov)
            tot_tok += len(t)
            cov_tok += sum(cov)
            if not ok and u.kind == "qti-feedback":
                runs = missing_runs(t, cov)
                lead = " ".join(t[:len(runs[0].split())]) == runs[0] if runs else False
                if len(runs) == 1 and lead and re.fullmatch(NEG_LEADIN, runs[0]):
                    fail("feedback-leadin-removed", file=p, page_title=page_title,
                         element=u.tag, text=u.text[:300], removed=runs[0])
                    continue
            if not ok:
                loc = "hidden page (linked from %s)" % ", ".join(sorted(mod.linked_hidden[p])) \
                    if p in mod.linked_hidden else "page"
                fail("text-missing" if u.kind != "c1doc" else "text-missing-c1document",
                     file=p, where=loc, page_title=page_title, element=u.tag, attr=u.attr,
                     kind=u.kind, text=u.text[:400],
                     missing=missing_runs(t, cov), coverage=round(sum(cov) / len(cov), 3))
    R["text_tokens"] = tot_tok
    R["text_coverage"] = round(cov_tok / tot_tok, 4) if tot_tok else 1.0
    R["text_coverage_by_kind"] = {k: round(per_kind_cov[k] / per_kind[k], 4)
                                  for k in per_kind if per_kind[k]}
    R["whitelisted"] = whitelisted
    R["inventory"] = {"%s@%s" % k: [inv_seen[k], inv_found[k]] for k in inv_seen}

    # optional: master-page-only hidden pages (disclaimer etc.) -> info
    for hp in sorted(mod.master_hidden):
        if hp in mod.sf:
            us = mod.sf[hp].units
            c = [phrase_in(u.text, pdf) for u in us]
            R["info"].append({"type": "hidden page linked only from master page (menu chrome)",
                              "file": hp, "units": len(us), "units_present": sum(c)})
    unl = [h.get("Source") for k, h in mod.hidden.items()
           if h.get("Source") not in mod.linked_hidden and h.get("Source") not in mod.master_hidden]
    if unl:
        R["info"].append({"type": "hidden pages not linked from anywhere (not required)",
                          "files": unl})
    for k in mod.linked_hidden:
        if k.startswith("MISSING:"):
            fail("broken-page-link", target=k[8:], linked_from=sorted(mod.linked_hidden[k]))

    # ---------------- 3 images
    pimgs = pdf.load_images()
    R["pdf_images"] = len({h for _, h in pimgs})
    img_refs = collections.OrderedDict()
    for p in mod.required:
        for (r, tag, attr) in mod.sf[p].images:
            img_refs.setdefault(r, []).append((p, tag, attr))
    found = missing = conv = 0
    ocr_vocab = set()
    R["images"] = []
    for r, locs in img_refs.items():
        data = mod.images.get(r)
        if data is None:
            # try case-insensitive / encoded keys
            for k in mod.images:
                if k.lower() == r.lower():
                    data = mod.images[k]
        rec = {"resource": r, "referenced_from": sorted({"%s (%s@%s)" % l for l in locs})}
        im = decode_data_url(data) if data else None
        if im is None:
            rec["status"] = "not in source dump" if data is None else "undecodable"
            R["images"].append(rec)
            if data is None and r not in mod.media:
                fail("image-not-in-dump", resource=r, referenced_from=rec["referenced_from"],
                     detail="referenced but neither in images nor media; cannot verify")
            continue
        if image_is_blank(im):
            rec["status"] = "blank/uniform image (ignored)"
            R["images"].append(rec)
            continue
        h = dhash(im)
        best = min(((ham(h, ph[0]), pi) for pi, ph in pimgs), default=(99, -1))
        rec["best_distance"], rec["pdf_page"] = best[0], best[1] + 1
        if best[0] <= 10:
            rec["status"] = "found"
            found += 1
        else:
            txt = ocr(im) if do_ocr else None
            ow = [w for w in toks(txt or "") if len(w) >= 3 and not w.isdigit()]
            r3 = 0.0
            if ow:
                hit = sum(1 for w in ow if (w,) in pdf.ngrams[1])
                rec["ocr_words"] = len(ow)
                rec["ocr_words_in_pdf"] = hit
                ot = toks(txt)
                cv3 = coverage(ot, pdf) if len(ot) > 3 else []
                r3 = sum(cv3) / len(cv3) if cv3 else 0.0
                rec["ocr_3gram_coverage"] = round(r3, 3)
            # converted-to-text only if the OCR'd word sequence (3-grams) is in the PDF;
            # single-word overlap is not enough (protocol vocabulary recurs in body text)
            if ow and len(ow) >= 3 and r3 >= 0.5:
                rec["status"] = ("converted to text (verified by text check: %d/%d OCR words, "
                                 "%.0f%% OCR 3-grams in PDF)" % (hit, len(ow), 100 * r3))
                conv += 1
                ocr_vocab.update(toks(txt))
                if r3 < 0.9:
                    runs = [x for x in missing_runs(ot, cv3) if len(x.split()) >= 3]
                    if runs:
                        fail("converted-image-text-incomplete", resource=r,
                             referenced_from=rec["referenced_from"], ocr_3gram_coverage=round(r3, 3),
                             ocr_runs_not_in_pdf=runs,
                             detail="image was turned into text, but these OCR'd word runs "
                                    "(from an independent tesseract pass) are not in the PDF")
            else:
                rec["status"] = "missing"
                missing += 1
                icon = any(a == "IconSource" for _, _, a in locs)
                fail("image-missing" if not icon else
                     ("popup-trigger-image-missing" + ("(small icon)" if max(im.size) <= 120 else "")), resource=r, referenced_from=rec["referenced_from"],
                     best_dhash_distance=best[0], size="%dx%d" % im.size,
                     ocr_3gram_coverage=rec.get("ocr_3gram_coverage"),
                     ocr_hint=(re.sub(r"\s+", " ", txt or "").strip()[:200] or None))
        R["images"].append(rec)
    R["images_found"], R["images_missing"], R["images_converted_to_text"] = found, missing, conv
    R["images_referenced"] = len(img_refs)

    # ---------------- 4 videos / youtube / audio
    vids = []
    for p in mod.required:
        for src, mt in mod.sf[p].videos:
            vids.append((p, src, mt))
    R["videos_source"] = len([v for v in vids if v[2].lower() != "audio"])
    R["audio_source"] = len([v for v in vids if v[2].lower() == "audio"])
    vok = 0
    for p, src, mt in vids:
        stem = os.path.splitext(src.split("/")[-1])[0]
        stem = re.sub(r"\.\(\d+\)$", "", stem)
        cap = phrase_in(stem, pdf, 1.0, loose=True)
        th = mod.thumbs.get(src)
        tim = decode_data_url(th) if th else None
        tfound = None
        if tim is not None:
            hh = dhash(tim)
            tfound = min((ham(hh, ph[0]) for _, ph in pimgs), default=99)
        okv = cap and (tim is None or tfound <= 10)
        if mt.lower() == "audio":
            if not cap:
                fail("audio-missing", file=p, resource=src,
                     detail="audio player in source; its name does not appear in the PDF")
            continue
        if okv:
            vok += 1
        else:
            fail("video-missing", file=p, resource=src, caption_with_name=cap,
                 thumbnail_in_dump=tim is not None, thumbnail_best_distance=tfound)
    R["videos_ok"] = vok
    R["thumbs_in_dump"] = len(mod.thumbs)
    pdf_links_n = [(pi, norm_url(u)) for pi, u in pdf.links]
    yt = []
    for p in mod.required:
        for y in mod.sf[p].youtube:
            vid = re.search(r"(?:v/|v=|youtu\.be/|embed/)([\w-]{6,})", y)
            vid = vid.group(1) if vid else y
            ok = any(vid in u for _, u in pdf_links_n)
            yt.append(ok)
            if not ok:
                fail("youtube-missing", file=p, url=y, detail="no link annotation containing the video id")
    R["youtube_source"], R["youtube_ok"] = len(yt), sum(yt)

    # ---------------- 5 links
    urls = collections.OrderedDict()
    for p in mod.required:
        for u, tag, attr in mod.sf[p].urls:
            urls.setdefault(u, []).append("%s (%s@%s)" % (p, tag, attr))
    lok = 0
    pdf_set = {u for _, u in pdf_links_n}
    for u, locs in urls.items():
        nu = norm_url(u)
        if "youtube.com/v/" in u.lower():
            continue  # covered by youtube check
        ok = nu in pdf_set or any(x.startswith(nu) or nu.startswith(x) and len(x) > 10 for x in pdf_set)
        if ok:
            lok += 1
        else:
            in_text = phrase_in(u, pdf, 1.0)
            fail("link-missing", url=u, referenced_from=sorted(set(locs)),
                 url_text_visible=in_text,
                 detail="no link annotation with this URI" + (" (URL text is printed though)" if in_text else ""))
    for p in mod.required:
        for u, tag, attr in mod.sf[p].ext_images:
            fail("external-image-unverifiable", file=p, url=u, element=tag,
                 detail="remote image in htmlcontent; not in the dump, cannot be matched offline")
    R["links_source"] = len([u for u in urls if "youtube.com/v/" not in u.lower()])
    R["links_ok"] = lok
    R["pdf_link_annotations"] = len(pdf.links)

    # ---------------- 7 questions
    qstats = collections.Counter()
    qok = 0
    qlist = []
    for p in mod.required:
        for q in mod.sf[p].questions:
            if "error" in q:
                fail("question-unparsable", file=p, detail=q["error"])
                continue
            kind = ",".join(sorted(set(q["kinds"]))) or (q.get("itype") or "unknown")
            qstats[kind] += 1
            problems = []
            # anchor pages: pages where feedback/options text appear
            anchor = set()
            probe = [b for v in q["feedback"].values() for b in v] + [t for _, t in q["options"]]
            for b in probe:
                tt = toks(b)
                if len(tt) >= 4:
                    for i in range(len(tt) - 2):
                        g = tuple(tt[i:i + 3])
                        for pi, ng in enumerate(pdf.page_ngrams):
                            if g in ng:
                                anchor.add(pi)
                        break
            pages = sorted({a + d for a in anchor for d in (-1, 0, 1)}) if anchor else None
            for oid, t in q["options"]:
                if "inlineChoice" in kind and oid not in q["correct_ids"]:
                    continue  # only the correct alternative is filled in
                if t and not phrase_in(t, pdf, 1.0):
                    problems.append("option missing: %r" % t[:120])
            img_opts = [oid for oid, t in q["options"] if not t]
            if img_opts:
                ims = q["images"]
                absent = [x for x in ims if x not in mod.images]
                if absent or not ims:
                    problems.append("image-only options %s: option images %s are not in the source dump, "
                                    "so the PDF cannot show them (verify visually)" % (img_opts, absent or "(none referenced)"))
            for c in q["correct"]:
                if not c or c.startswith("<image option"):
                    continue
                if "selectPoint" in kind:
                    continue  # coordinates; can only be drawn
                if "graphicGapMatch" in kind:
                    labs = [x for x in c.split(" / ") if x]
                    for x in labs:
                        if not phrase_in(x, pdf, 1.0) and not re.fullmatch(r"[0-9a-f-]{36}", x):
                            problems.append("gap label missing: %r" % x)
                    continue
                if not phrase_in(c, pdf, 1.0):
                    problems.append("correct answer text missing: %r" % c[:120])
            for fid, blocks in q["feedback"].items():
                for b in blocks:
                    if not phrase_in(b, pdf, 1.0) and not is_praise(b):
                        problems.append("feedback[%s] missing: %r" % (fid, b[:120]))
            # marking of correct choice (style differs from incorrect ones)
            mark = "n/a"
            if "choiceInteraction" in kind and q["options"] and all(t for _, t in q["options"]):
                mark = check_marking(pdf, q)
                if mark.startswith("NOT MARKED"):
                    problems.append("correct choice not visually distinguished from wrong choices: " + mark)
            if "orderInteraction" in kind and q["order"] and pages:
                pos = []
                for t in q["order"]:
                    hits = find_line_spans(pdf, t, pages)
                    pos.append(min((h[0], 0) for h in hits) if hits else None)
                # check sequence order within the concatenated stream
                seq = []
                st = [tk for pi in pages if 0 <= pi < pdf.npages for tk in pdf.page_tokens_B[pi]]
                stx = " ".join(st)
                for t in q["order"]:
                    seq.append(stx.find(" ".join(toks(t)[:5])))
                if all(x >= 0 for x in seq) and seq != sorted(seq):
                    problems.append("order question: items not printed in the correct order %s" % seq)
            for im in q["images"]:
                pass  # covered by the image check (QTI object/img refs are included)
            qrec = {"qid": q["qid"], "kind": kind, "options": len(q["options"]),
                    "correct": [c[:80] for c in q["correct"]], "marking": mark,
                    "feedback_ids": sorted(q["feedback"]), "problems": problems}
            qlist.append(qrec)
            if problems:
                fail("question-incomplete", file=p, question=q["qid"], kind=kind,
                     problems=problems)
            else:
                qok += 1
    R["questions_by_type"] = dict(qstats)
    R["questions_ok"] = qok
    R["questions"] = qlist

    # ---------------- 9 structure
    tmiss = []
    for vp in mod.visible:
        t = vp.get("Title", "")
        if t and not phrase_in(t, pdf, 1.0, loose=True):
            tmiss.append(t)
            fail("page-title-missing", title=t, menu_name=vp.get("Name"), file=vp.get("Source"))
        n = vp.get("Name", "")
        if n and n != t and not phrase_in(n, pdf, 1.0, loose=True):
            R["info"].append({"type": "menu name (differs from Title) not in PDF", "name": n,
                              "title": t})
    R["page_titles_missing"] = len(tmiss)

    # ---------------- 2 new / merged words
    vocab = mod.vocab() | ocr_vocab
    bigr = mod.bigrams()
    new = collections.Counter()
    merged = collections.Counter()
    ctx = {}
    for pi, pl in enumerate(pdf.body_lines):
        a = pdf.page_tokens_A[pi]
        b = set(pdf.page_tokens_B[pi])
        chrome_toks = set()
        for t_, _ in pl:
            if any(re.search(rx, t_, re.I) for rx in EXPORTER_CHROME_LINES) or HTTP_RE.search(t_):
                chrome_toks.update(toks(t_))
        for i, t in enumerate(a):
            if t in vocab or t in EXPORTER_CHROME_WORDS or t in chrome_toks:
                continue
            if t.isdigit() and (int(t) <= pdf.npages + 5 or t in vocab):
                continue
            if t not in b:  # produced by de-hyphenation; parts are fine if parts known
                continue_ok = False
                for j in range(1, len(t)):
                    if t[:j] in vocab and t[j:] in vocab:
                        continue_ok = True
                        break
                if continue_ok:
                    continue
            # two source words that are adjacent in the source, glued together?
            split = None
            for j in range(1, len(t)):
                if (t[:j], t[j:]) in bigr:
                    split = (t[:j], t[j:])
                    break
            c = " ".join(a[max(0, i - 4):i + 5])
            if split:
                merged[t] += 1
                ctx.setdefault(t, (pi + 1, c, split))
            else:
                new[t] += 1
                ctx.setdefault(t, (pi + 1, c, None))
    for t, n in sorted(merged.items()):
        pg, c, sp = ctx[t]
        fail("merged-word", token=t, count=n, pdf_page=pg, context=c, parts=list(sp))
    for t, n in sorted(new.items()):
        pg, c, _ = ctx[t]
        near = closest(t, vocab)
        fail("new-word", token=t, count=n, pdf_page=pg, context=c, nearest_source_word=near)
    R["merged_words"] = len(merged)
    R["new_words"] = len(new)
    return R


def closest(t, vocab):
    best, bd = None, 99
    if len(t) < 3:
        return None
    for v in vocab:
        if abs(len(v) - len(t)) > 2 or v[0] != t[0] and v[-1] != t[-1]:
            continue
        d = lev(t, v, bd)
        if d < bd:
            best, bd = v, d
    return "%s (edit distance %d)" % (best, bd) if best and bd <= 2 else None


def lev(a, b, cap=99):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


# --------------------------------------------------------------------------
# Global inventory (check 8)
# --------------------------------------------------------------------------
def global_inventory(mods, results):
    tags = collections.Counter()
    attr = collections.Counter()
    for m in mods:
        for p, s in m.sf.items():
            if p.startswith(("Pages/", "HiddenPages/")):
                tags.update(s.tags)
    seen = collections.Counter()
    found = collections.Counter()
    for r in results:
        for k, (a, b) in r.get("inventory", {}).items():
            seen[k] += a
            found[k] += b
    flagged = [k for k in seen if found[k] == 0]
    return {"tags": dict(tags.most_common()),
            "text_attributes": {k: {"units": seen[k], "units_found_in_pdf": found[k]}
                                for k in sorted(seen, key=lambda x: -seen[x])},
            "never_in_any_pdf": flagged}


# --------------------------------------------------------------------------
def main(argv):
    out = "silverpoint_verify_report.json"
    only = None
    do_ocr = True
    args = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--out":
            out = argv[i + 1]
            i += 2
            continue
        if a == "--only":
            only = argv[i + 1]
            i += 2
            continue
        if a == "--no-ocr":
            do_ocr = False
            i += 1
            continue
        args.append(a)
        i += 1
    if len(args) < 2 or len(args) % 2:
        print(__doc__)
        return 2
    mods, results = [], []
    for bron, pdfdir in zip(args[0::2], args[1::2]):
        for f in sorted(os.listdir(bron)):
            if not f.endswith(".sp.json"):
                continue
            if only and only not in f:
                continue
            m = Module(os.path.join(bron, f))
            mods.append(m)
            print("checking", m.name, file=sys.stderr)
            results.append(verify(m, os.path.join(pdfdir, m.name + ".pdf"), do_ocr))
    inv = global_inventory(mods, results)
    report = {"modules": results, "inventory": inv,
              "whitelists": {"praise": PRAISE_WHITELIST,
                             "designer_placeholders": sorted(DESIGNER_PLACEHOLDERS),
                             "exporter_chrome_words": sorted(EXPORTER_CHROME_WORDS)}}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    # ---- human summary
    hdr = ("module", "pg", "pdfpg", "text%", "miss", "new", "merg", "img ok/miss/txt",
           "vid", "yt", "links", "hid", "q ok/n", "fails")
    print("\n" + " | ".join(hdr))
    for r in results:
        cats = collections.Counter(f["category"] for f in r["failures"])
        print(" | ".join(str(x) for x in (
            r["module"][:34], r.get("source_pages", "-"), r.get("pdf_pages", "-"),
            "%.2f" % (100 * r.get("text_coverage", 0)),
            cats["text-missing"] + cats["text-missing-c1document"], r.get("new_words", "-"),
            r.get("merged_words", "-"),
            "%s/%s/%s of %s" % (r.get("images_found"), r.get("images_missing"),
                                r.get("images_converted_to_text"), r.get("images_referenced")),
            "%s/%s" % (r.get("videos_ok"), r.get("videos_source")),
            "%s/%s" % (r.get("youtube_ok"), r.get("youtube_source")),
            "%s/%s" % (r.get("links_ok"), r.get("links_source")),
            r.get("hidden_pages_linked"),
            "%s/%s" % (r.get("questions_ok"), sum(r.get("questions_by_type", {}).values())),
            len(r["failures"]))))
    print("\nInventory: text attributes never found in any PDF:", inv["never_in_any_pdf"])
    print("\nFAILURES")
    for r in results:
        for f in r["failures"]:
            d = {k: v for k, v in f.items() if k != "category"}
            print("[%s] %s: %s" % (f["category"], r["module"], json.dumps(d, ensure_ascii=False)))
    print("\nreport written to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
