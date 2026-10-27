#!/usr/bin/env python3
"""Independent, adversarial completeness test for Articulate Rise PDF exports.

Written WITHOUT reading the exporter (rise.js, book-to-print.py, console-export.js...).
Works only from the raw *.rise.json course data and the produced PDFs.

Usage:
    python3 tests/rise_verify.py <rise-json-dir> <pdf-dir> [<pdf-dir> ...]
        [--out report.json] [--only NAME]

Every <rise-json-dir>/*.rise.json is matched to a PDF (any of the pdf dirs) by
normalised title. Writes a JSON report (default ./rise_verify_report.json) and
prints a per-module summary plus every concrete failure, grouped by cause.

Checks
  1 text coverage: every learner-visible text field (found by a generic walk of
    the block JSON; HTML stripped, entities decoded, tiptap JSON flattened) must be
    covered by word 3-grams of the PDF body, preferably inside its own chapter.
  2 new / merged / altered words in the PDF body vs. the module's source vocabulary.
  3 per-block-type checks: knowledge checks (question, all options, correct option
    visibly marked, correct feedback), matching pairs on the same page, sorting cards
    under their pile, flashcard backs, marker pop-ups, process/timeline steps,
    accordion/tab bodies, mondrian (custom) blocks incl. initially hidden layers.
  4 external links present as link annotations.
  5 lesson (chapter) titles present as chapter headings.
  6 inventory of every block type/variant and every field path carrying text,
    flagging paths whose text never appears in any PDF.
  7 image count per lesson (source vs PDF image placements).
"""
import collections
import glob
import html
import html.parser
import json
import os
import re
import sys
import unicodedata
from urllib.parse import unquote

import fitz  # PyMuPDF

# --------------------------------------------------------------------------
# Explicit whitelists (the product's intentional omissions)
# --------------------------------------------------------------------------
# Field paths (signature suffixes) that are never learner-visible / are file names.
NON_TEXT_KEYS = {
    "id", "key", "crushedKey", "originalUrl", "src", "url", "inputKey", "thumbnail",
    "poster", "type", "icon", "filename", "mimeType", "favicon", "providerUrl",
    "transcodeId", "buttonColor", "contentPrefix", "x", "y", "number", "variant",
    "family", "createdAt", "updatedAt", "globalBlockId", "blockumentId", "author",
    "courseId", "embedType", "sourcedFrom", "align", "overlayColor", "color",
    "clonedFromId", "parentId", "initialState", "version", "course_id", "scene_index",
    "sourceType", "html", "provider", "lastUpdatedBy", "selectedAuthorId",
    "duplicatedFromId", "originalId", "fontFamily", "textAlign", "feedbackType",
    "accordionBehavior", "backgroundColor", "backgroundType", "existingMediaType",
    "createdVia", "path",
}
# Whitelisted (intentionally omitted) reasons, keyed by a short name.
WL_WRONG_FEEDBACK = "feedback shown only for a wrong answer"
WL_DIVIDER = "divider / continue-button label"
WL_HIDDEN_HEADING = "unused heading field of a paragraph-variant text block"
WL_EXTRA_ITEM = "text-block item beyond the rendered column count"
WL_HIDDEN_STEP = "process step marked isHidden"
WL_CHROME = "Rise UI chrome"
WL_LAYER_NAME = "mondrian layer/template name (authoring only)"
WL_STORYLINE_META = "Storyline package metadata (scene/slide names)"
WL_EMPTY_PLACEHOLDER = "Rise default placeholder text"
WL_STALE_KC = "legacy correct/corrects strings of a knowledge check (player uses answers[].correct)"
WL_IMAGE_ONLY_CARD = "flashcard side of type image/fullimage (description not rendered)"
WL_HIDDEN_PARAGRAPH = "paragraph field of a block variant that does not render it"

# Rise UI chrome that may appear in data but is never content.
RISE_CHROME = {"continue", "start", "submit", "next", "previous", "take again",
               "complete the content above before moving on", "heading",
               "doorgaan", "start opnieuw", "verzenden"}
PLACEHOLDERS = {"heading", "subheading", "paragraph", "lorem ipsum"}

# Text-block variants that render a heading field.
HEADING_VARIANTS = {"heading paragraph", "heading", "subheading paragraph", "subheading"}

# Words/lines the exporter adds (used only by the new-word check).
EXPORTER_CHROME_WORDS = set("""
inhoud hoofdstuk vraag voorbeeld e module pdf versie van de nipv gemaakt op is
gewijzigd neem dan contact via support oefio nl zodat deze kan worden bijgewerkt
sorteeroefening elke kaart staat hieronder in juiste categorie iframe januari februari
toelichting koppeling begrip
maart april mei juni juli augustus september oktober november december
""".split())
EXPORTER_CHROME_LINE_RES = [
    r"^hoofdstuk \d+$", r"^inhoud$", r"^e-module$", r"^vraag$", r"^voorbeeld \d+$",
    r"pdf-versie van de nipv", r"gemaakt op \d+ \w+ \d{4}", r"is de e-module gewijzigd",
    r"neem dan contact op", r"^bijgewerkt\.?$", r"sorteeroefening", r"^\[iframe:",
    r"^cpeopt\d+$", r"^i$", r"^juiste koppeling$", r"^begrip$", r"^toelichting:",
]

HEADER_Y, FOOTER_Y = 40, 795       # PDF points; running header/footer outside
INVISIBLE_SIZE = 4.0               # spans smaller than this are not readable text
MIN_IMG_PT = 24                    # ignore image placements smaller than this (icons)

# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
def norm(s):
    s = unicodedata.normalize("NFKC", s or "")
    s = s.replace("­", "").replace("​", "").replace("﻿", "")
    s = re.sub("[‘’‚‛´`]", "'", s)
    s = re.sub("[“”„‟]", '"', s)
    s = re.sub("[‐-―−]", "-", s)
    return s.lower()


TOK_RE = re.compile(r"[^\W_]+", re.U)


def toks(s):
    return TOK_RE.findall(norm(s))


def normtitle(s):
    return re.sub(r"[^a-z0-9]", "", norm(unquote(s)))


class _HTMLText(html.parser.HTMLParser):
    BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "td",
             "th", "ul", "ol", "blockquote", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links = [""], []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK:
            self.parts.append("")
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("")

    def handle_data(self, d):
        self.parts[-1] += d


def html_text(s):
    """Return (list of paragraph strings, list of hrefs)."""
    if "<" not in s and "&" not in s:
        return [s.strip()] if s.strip() else [], []
    p = _HTMLText()
    try:
        p.feed(s)
        p.close()
    except Exception:
        return [re.sub(r"<[^>]+>", " ", html.unescape(s))], []
    return [x.strip() for x in p.parts if x.strip()], p.links


def tiptap_text(node, out, links):
    """Flatten tiptap JSON: one string per block node."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            out[-1] += node.get("text", "")
            for m in node.get("marks") or []:
                if m.get("type") == "link":
                    links.append((m.get("attrs") or {}).get("href", ""))
            return
        block = node.get("type") in ("paragraph", "heading", "listItem", "bulletList",
                                     "orderedList", "blockquote", "tableCell", "hardBreak")
        if block:
            out.append("")
        for c in node.get("content") or []:
            tiptap_text(c, out, links)
        if block:
            out.append("")
    elif isinstance(node, list):
        for c in node:
            tiptap_text(c, out, links)


def has_word(s):
    return bool(re.search(r"[^\W\d_]", s or ""))


# --------------------------------------------------------------------------
# Source model
# --------------------------------------------------------------------------
class Unit:
    """One learner-visible text field."""
    __slots__ = ("lesson", "block", "btype", "sig", "text", "status", "reason", "check")

    def __init__(self, lesson, block, btype, sig, text, status="required", reason="", check=""):
        self.lesson, self.block, self.btype, self.sig = lesson, block, btype, sig
        self.text, self.status, self.reason, self.check = text, status, reason, check


def walk_strings(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk_strings(v, path + "." + k if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk_strings(v, path + "[%d]" % i)
    elif isinstance(o, str):
        yield path, o


def sig_of(path):
    return re.sub(r"\[\d+\]", "[]", path)


def lastkey(path):
    return re.split(r"[.\[]", re.sub(r"\[\d+\]$", "", path))[-1]


def item_index(path):
    m = re.match(r"items\[(\d+)\]", path)
    return int(m.group(1)) if m else None


def classify(block, path, item):
    """Decide whether a text field is shown to the learner. Returns (status, reason)."""
    t, v = block.get("type"), block.get("variant")
    k = lastkey(path)
    if path.startswith("settings") or path.startswith("metadata"):
        return "skip", ""
    if ".captions[" in path:
        return "skip", ""  # subtitle track names
    if t == "knowledgeCheck" and re.search(r"items\[\d+\]\.(correct|corrects(\[\d+\])?)$", path):
        return "whitelist", WL_STALE_KC
    if t == "knowledgeCheck" and re.search(r"\.answers\[(\d+)\]\.feedback$", path):
        ai = int(re.search(r"\.answers\[(\d+)\]\.feedback$", path).group(1))
        ans = ((item or {}).get("answers") or [])[ai] if item else {}
        return ("required", "") if ans.get("correct") else ("whitelist", WL_WRONG_FEEDBACK)
    m = re.search(r"\.(front|back)\.description$", path)
    if m and item and ((item.get(m.group(1)) or {}).get("type") in ("image", "fullimage")):
        return "whitelist", WL_IMAGE_ONLY_CARD
    if t == "image" and block.get("family") == "image" and (item_index(path) or 0) >= 1:
        return "whitelist", "extra item of a single-image block (only items[0] is rendered)"
    if t == "image" and k == "paragraph" and v != "text aside":
        return "whitelist", WL_HIDDEN_PARAGRAPH
    if t == "text" and k == "paragraph" and v in ("heading", "subheading"):
        return "whitelist", WL_HIDDEN_PARAGRAPH
    if t == "divider":
        return "whitelist", WL_DIVIDER
    if k == "completeHint":
        return "whitelist", WL_CHROME
    if t == "text":
        idx = item_index(path)
        ncols = 2 if v == "two column" else 1
        if idx is not None and idx >= ncols:
            return "whitelist", WL_EXTRA_ITEM
        if k == "heading" and v not in HEADING_VARIANTS:
            return "whitelist", WL_HIDDEN_HEADING
    if t == "knowledgeCheck" and k in ("feedback", "feedbackCorrect", "feedbackIncorrect") \
            and ".answers[" not in path:
        mode = (item or {}).get("feedbackType") or "CORRECT_INCORRECT"
        if mode == "CORRECT_INCORRECT":
            if k == "feedbackCorrect":
                return "required", ""
            if k == "feedbackIncorrect":
                return "whitelist", WL_WRONG_FEEDBACK
            return "whitelist", "generic feedback field unused in CORRECT_INCORRECT mode"
        if mode == "ANY":
            if k == "feedback":
                return "required", ""
            return "whitelist", "correct/incorrect feedback unused in ANY (same feedback) mode"
        if mode == "CHOICE":
            return "whitelist", "item-level feedback unused in CHOICE (per-answer) mode"
        return "soft", "feedback in unknown mode %s" % mode
    if v == "process" and (item or {}).get("isHidden"):
        return "whitelist", WL_HIDDEN_STEP
    if ".storyline.meta" in path:
        return "whitelist", WL_STORYLINE_META
    if ".media." in "." + path and k in ("description",) and ".embed." in path:
        return "soft", "embed card description"
    if ".media." in "." + path and k == "title" and (".embed." in path or ".storyline." in path):
        return "required", ""
    if ".attachment." in path:
        return "soft", "attachment"
    return "required", ""


def check_name(block, path):
    t, v = block.get("type"), block.get("variant")
    k = lastkey(path)
    if t == "knowledgeCheck":
        if ".answers[" in path:
            return {"title": "kc-option", "matchTitle": "kc-match-target",
                    "feedback": "kc-answer-feedback"}.get(k, "kc-other")
        return {"title": "kc-question", "feedbackCorrect": "kc-correct-feedback",
                "feedback": "kc-feedback"}.get(k, "kc-other")
    if t == "interactive":
        if v in ("flashcard", "stack"):
            return "flashcard-" + ("back" if ".back." in path else "front")
        if v == "labeledgraphic":
            return "marker-" + k
        if v == "sorting":
            return "sorting-" + ("pile" if path.startswith("piles") else "card")
        return v + "-" + k
    if ".embed." in path or ".video" in path or ".audio" in path:
        return "media-" + k
    return ""


def load_course(fp):
    d = json.load(open(fp, encoding="utf-8"))
    co = d.get("course", d) if isinstance(d, dict) else d
    labels = {}
    ls = d.get("labelSet") if isinstance(d, dict) else None
    if isinstance(ls, dict):
        labels = ls.get("labels") or {}
    return d, co, labels


def image_keys_of(obj):
    """Return all media.image objects (not originalImage duplicates) inside obj."""
    out = []

    def rec(o, path):
        if isinstance(o, dict):
            for kk, vv in o.items():
                if kk == "image" and isinstance(vv, dict) and (vv.get("key") or vv.get("src")) \
                        and not path.endswith("originalImage"):
                    out.append((path, vv))
                elif kk != "originalImage":
                    rec(vv, path + "." + kk)
        elif isinstance(o, list):
            for i, vv in enumerate(o):
                rec(vv, path + "[]")
    rec(obj, "")
    return out


def required_images(block):
    """(required, optional) counts of images a learner sees in this block."""
    t, v = block.get("type"), block.get("variant")
    req, opt = 0, 0
    _counted = {}
    for path, im in image_keys_of(block):
        default = im.get("sourcedFrom") == "DEFAULT"
        if path.startswith(".settings"):
            continue
        if t == "image":
            # single-image variants (family 'image': hero/full/text aside) render items[0]
            # only; galleries render every item
            if block.get("family") == "gallery" or path.startswith(".items[]") and \
                    not _counted.setdefault("first", False):
                req += 1
                if block.get("family") != "gallery":
                    _counted["first"] = True
            else:
                opt += 0
        elif t == "knowledgeCheck":
            req += 1
        elif t == "interactive" and v == "labeledgraphic":
            if path.startswith(".media"):
                req += 1          # the base graphic
            else:
                opt += 1          # marker pop-up media
        elif t == "interactive" and v in ("process", "timeline"):
            req += 1
        elif t == "interactive" and v in ("flashcard", "stack"):
            opt += 0 if default else 1
        elif t == "quote":
            opt += 0 if default else 1
        else:
            opt += 1
    return req, opt


def mondrian_units(co, block, li, bi, links):
    """Text units of a custom (mondrian) block, walked from course.mondrian."""
    m = co.get("mondrian") or {}
    bid = block.get("blockumentId")
    bdoc = (m.get("blockuments") or {}).get(bid) or {}
    items = [it for it in (m.get("items") or {}).values() if it.get("blockumentId") == bid]
    units, nimg = [], 0
    if bdoc.get("title"):
        units.append(("blockument.title", bdoc["title"], "whitelist", WL_LAYER_NAME, ""))
    for it in items:
        if it.get("removed"):
            continue
        hidden = it.get("initialVisible") is False or it.get("authoringVisible") is False
        if it.get("type") == "image":
            nimg += 1
        if it.get("name"):
            units.append(("item.name", it["name"], "whitelist", WL_LAYER_NAME, ""))
        for sn, st in (it.get("states") or {}).items():
            if not isinstance(st, dict):
                continue
            txt = st.get("text")
            if isinstance(txt, dict) and txt.get("json"):
                parts = [""]
                tiptap_text(txt["json"], parts, links)
                for p in parts:
                    if p.strip():
                        chk = "mondrian-hidden-layer" if hidden else (
                            "mondrian-state-" + sn if sn != "default" else "mondrian-text")
                        units.append(("item.states.%s.text" % ("default" if sn == "default" else "*"),
                                      p.strip(), "required", "", chk))
            if st.get("altText") and has_word(st["altText"]):
                units.append(("item.states.altText", st["altText"], "soft", "alt text", "mondrian-alt"))
        for path, s in walk_strings({k: v for k, v in it.items() if k not in ("states",)}):
            if lastkey(path) in NON_TEXT_KEYS or lastkey(path) == "name" or ".assets." in path:
                continue
            if has_word(s) and not re.match(r"^[0-9a-f-]{20,}$|^\d{4}-\d\d-\d\dT", s):
                units.append(("item." + sig_of(path), s, "inventory", "unknown mondrian field", ""))
    return units, nimg


def build_source(fp):
    d, co, labels = load_course(fp)
    units, links, lessons, inventory_types = [], [], [], collections.Counter()
    chrome = {norm(v).strip() for v in labels.values() if isinstance(v, str)}
    # course level
    for k in ("title", "description"):
        v = co.get(k)
        if isinstance(v, str) and has_word(v):
            paras, hl = html_text(v)
            links += hl
            for p in paras:
                units.append(Unit(-1, -1, "course", "course." + k, p, check="course-" + k))
    for li, L in enumerate(co.get("lessons") or []):
        if L.get("deleted"):
            continue
        lrec = {"index": li, "title": L.get("title") or "", "type": L.get("type"),
                "img_req": 0, "img_opt": 0, "blocks": []}
        lessons.append(lrec)
        units.append(Unit(li, -1, "lesson", "lesson.title", lrec["title"], check="lesson-title"))
        if isinstance(L.get("description"), str) and has_word(L["description"]):
            for p in html_text(L["description"])[0]:
                units.append(Unit(li, -1, "lesson", "lesson.description", p))
        for bi, b in enumerate(L.get("items") or []):
            btype = "%s/%s" % (b.get("type"), b.get("variant"))
            inventory_types[btype] += 1
            lrec["blocks"].append(btype)
            r, o = required_images(b)
            if b.get("type") == "custom":
                mu, nimg = mondrian_units(co, b, li, bi, links)
                r += nimg
                for sig, text, st, why, chk in mu:
                    units.append(Unit(li, bi, btype, "mondrian." + sig, text, st, why, chk))
            lrec["img_req"] += r
            lrec["img_opt"] += o
            for path, s in walk_strings({k: v for k, v in b.items() if k not in ("id",)}):
                k = lastkey(path)
                if k in NON_TEXT_KEYS:
                    if k in ("originalUrl", "src", "url") and re.match(r"^(https?:)?//", s):
                        if ".embed." in path and k == "originalUrl":
                            links.append(s)
                    continue
                if not has_word(s):
                    continue
                if re.match(r"^\d{4}-\d\d-\d\dT|^[A-Za-z0-9_-]{16,}$", s):
                    continue
                item = None
                ii = item_index(path)
                if ii is not None and isinstance(b.get("items"), list) and ii < len(b["items"]):
                    item = b["items"][ii]
                status, reason = classify(b, path, item)
                if status == "skip":
                    continue
                paras, hl = html_text(s)
                links += hl
                for p in paras:
                    n = norm(p).strip().strip(".!:")
                    st, rs = status, reason
                    if st == "required" and n in PLACEHOLDERS:
                        st, rs = "whitelist", WL_EMPTY_PLACEHOLDER
                    elif st == "required" and (n in RISE_CHROME or n in chrome):
                        st, rs = "whitelist", WL_CHROME
                    units.append(Unit(li, bi, btype, btype + ":" + sig_of(path), p, st, rs,
                                      check_name(b, path)))
    vocab = set()
    for _, s in walk_strings(d):
        vocab |= set(toks(html.unescape(s)))
        for p in html_text(s)[0]:
            vocab |= set(toks(p))
    # mondrian tiptap text
    for u in units:
        vocab |= set(toks(u.text))
    return {"data": d, "course": co, "units": units, "links": links, "lessons": lessons,
            "types": inventory_types, "vocab": vocab, "chrome": chrome}


# --------------------------------------------------------------------------
# PDF model
# --------------------------------------------------------------------------
class PDF:
    def __init__(self, path, sort_blocks=False):
        self.path = path
        doc = fitz.open(path)
        self.npages = doc.page_count
        self.tokens, self.tok_page = [], []      # body token stream
        self.lines = []                          # (page, y, x, text, spans)
        self.links, self.images = [], collections.Counter()
        self.invisible = collections.Counter()
        for pi, page in enumerate(doc):
            for ln in page.get_links():
                if ln.get("uri"):
                    self.links.append((pi, ln["uri"]))
            for info in page.get_image_info():
                x0, y0, x1, y1 = info["bbox"]
                if x1 - x0 >= MIN_IMG_PT and y1 - y0 >= MIN_IMG_PT:
                    self.images[pi] += 1
            raw = []
            # reading order: Chrome's block order, or (sort_blocks) text blocks sorted by
            # their top edge; Chrome's order puts e.g. knowledge-check options after the
            # text that follows the question, the sorted order interleaves columns.
            # Text alignment tries both.
            tblocks = [b for b in page.get_text("dict")["blocks"] if b["type"] == 0]
            if sort_blocks:
                tblocks.sort(key=lambda b: (round(b["bbox"][1], 0), b["bbox"][0]))
            for b in tblocks:
                for l in b["lines"]:
                    y = l["bbox"][1]
                    if y < HEADER_Y or y > FOOTER_Y:
                        continue
                    spans = [s for s in l["spans"] if s["text"].strip()]
                    vis = []
                    for s in spans:
                        if s["size"] < INVISIBLE_SIZE:
                            self.invisible[s["text"].strip()] += 1
                        else:
                            vis.append(s)
                    if vis:
                        raw.append((round(y, 1), l["bbox"][0], "".join(s["text"] for s in vis), vis))
            # reading order: blocks as given (Chrome emits in DOM order); keep order
            for y, x, t, sp in raw:
                self.lines.append((pi, y, x, t, sp))
        # main token stream + an alternative stream in which a word broken by a
        # trailing hyphen at a line end is re-joined ("voor-" / "beeld" -> "voorbeeld")
        self.alt, self.alt_page, self.tok_line = [], [], []
        for li, (pi, y, x, t, sp) in enumerate(self.lines):
            tk = toks(t)
            prev = self.lines[li - 1][3] if li else ""
            join = bool(self.alt and tk and re.search(r"[^\W\d_]-\s*$", prev))
            for j, w in enumerate(tk):
                self.tokens.append(w)
                self.tok_page.append(pi)
                self.tok_line.append(li)
                if j == 0 and join:
                    self.alt[-1] += w
                else:
                    self.alt.append(w)
                    self.alt_page.append(pi)
        self.flat = "".join(self.tokens)
        self._index()
        self.chapters = self._chapters()

    def _index(self):
        self.ngram_pos = collections.defaultdict(list)      # main stream positions
        self.alt_pos = collections.defaultdict(list)        # alt stream positions
        self.ngram_pages = collections.defaultdict(set)     # both streams -> pages
        for T, PG, main in ((self.tokens, self.tok_page, True), (self.alt, self.alt_page, False)):
            for n in (1, 2, 3):
                for i in range(len(T) - n + 1):
                    g = tuple(T[i:i + n])
                    (self.ngram_pos if main else self.alt_pos)[g].append(i)
                    self.ngram_pages[g].add(PG[i])

    def _chapters(self):
        """Detect 'Hoofdstuk N' + title chapter starts: list of (n, title, page, line_idx)."""
        out = []
        for i, (pi, y, x, t, sp) in enumerate(self.lines):
            m = re.fullmatch(r"\s*Hoofdstuk\s+(\d+)\s*", t)
            if m and i + 1 < len(self.lines) and self.lines[i + 1][0] == pi:
                title = self.lines[i + 1][3]
                # titles may wrap over several bold lines of the same size
                j = i + 2
                sz = self.lines[i + 1][4][0]["size"]
                while j < len(self.lines) and self.lines[j][0] == pi and \
                        abs(self.lines[j][4][0]["size"] - sz) < 0.5 and sz >= 16:
                    title += " " + self.lines[j][3]
                    j += 1
                out.append((int(m.group(1)), title.strip(), pi, i))
        return out



def covered(tokens, pdf, allowed=None):
    """Per-token coverage by PDF 3-grams (or exact n-gram for n<=3).
    allowed: set of pages that count (chapter range) or None."""
    n = len(tokens)
    if n == 0:
        return []

    def hit(g):
        pg = pdf.ngram_pages.get(g)
        if not pg:
            return False
        return allowed is None or bool(pg & allowed)
    if n <= 3:
        return [hit(tuple(tokens))] * n
    cov = [False] * n
    for i in range(n - 2):
        if hit(tuple(tokens[i:i + 3])):
            cov[i] = cov[i + 1] = cov[i + 2] = True
    return cov


def align(tk, P, pages):
    """Best in-order alignment of tokens tk against the PDF body stream (main and
    hyphen-joined alternative), optionally restricted to pages.
    Returns (matched_count, missing_tokens, pdf_window_text)."""
    import difflib
    best = (0, list(tk), "", 0)
    n = len(tk)
    for T, PG, IDX in ((P.tokens, P.tok_page, P.ngram_pos), (P.alt, P.alt_page, P.alt_pos)):
        votes = collections.Counter()
        g = 3 if n >= 3 else n
        for i in range(n - g + 1):
            for p in IDX.get(tuple(tk[i:i + g]), [])[:400]:
                if pages is None or PG[p] in pages:
                    votes[p - i] += 1
        if not votes and n >= 2:
            for i in range(n):
                for p in IDX.get((tk[i],), [])[:200]:
                    if pages is None or PG[p] in pages:
                        votes[p - i] += 1
        for start, _ in votes.most_common(4):
            lo, hi = max(0, start - 3), min(len(T), start + n + 3 + n // 4)
            win = T[lo:hi]
            sm = difflib.SequenceMatcher(None, tk, win, autojunk=False)
            blocks = sm.get_matching_blocks()
            m = sum(bl.size for bl in blocks)
            if m > best[0]:
                longest = max(bl.size for bl in blocks)
                hit = set()
                for bl in blocks:
                    hit.update(range(bl.a, bl.a + bl.size))
                miss = [tk[i] for i in range(n) if i not in hit]
                best = (m, miss, " ".join(win), longest)
            if best[0] == n:
                return best
    return best


def align2(tk, P, P2, pages):
    a = align(tk, P, pages)
    if a[0] == len(tk):
        return a
    b = align(tk, P2, pages)
    return b if b[0] > a[0] else a


def runs(tokens, cov):
    out, cur = [], []
    for t, c in zip(tokens, cov):
        if not c:
            cur.append(t)
        elif cur:
            out.append(" ".join(cur))
            cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def norm_url(u):
    u = html.unescape(u or "").strip().rstrip("/.,;")
    u = re.sub(r"^(https?:)?//", "", u, flags=re.I)
    u = re.sub(r"^www\.", "", u, flags=re.I)
    u = unquote(u)
    host, _, rest = u.partition("/")
    return host.lower() + ("/" + rest.rstrip("/") if rest else "")


def lev(a, b, cap=3):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def style_sig(spans):
    s = spans[0]
    bold = "bold" in s["font"].lower() or bool(s["flags"] & 16)
    return (bold, "%06x" % s["color"])


# --------------------------------------------------------------------------
# Verification of one module
# --------------------------------------------------------------------------
def verify(src_path, pdf_path):
    S = build_source(src_path)
    P = PDF(pdf_path)
    P2 = PDF(pdf_path, sort_blocks=True)
    co = S["course"]
    fails = []
    stats = collections.Counter()

    def fail(cause, u_or_none, detail, text=None, lesson=None, btype=None, field=None):
        if u_or_none is not None:
            u = u_or_none
            lesson = lesson if lesson is not None else u.lesson
            btype, field, text = u.btype, u.sig, text if text is not None else u.text
        lt = ""
        if lesson is not None and lesson >= 0:
            lt = next((l["title"] for l in S["lessons"] if l["index"] == lesson), "")
        fails.append({"cause": cause, "lesson": lt, "lesson_index": lesson, "block": btype,
                      "field": field, "text": (text or "")[:400], "detail": detail})

    # ---- chapter ranges (check 5) ----
    lesson_by_idx = {l["index"]: l for l in S["lessons"]}
    chap_pages = {}
    chaps = sorted(P.chapters, key=lambda c: c[2])
    first_body_page = chaps[0][2] if chaps else 0
    used = set()
    for k, (n, title, pg, _) in enumerate(chaps):
        end = chaps[k + 1][2] if k + 1 < len(chaps) else P.npages
        # match by title (normalised), else by ordinal
        cand = [l for l in S["lessons"] if normtitle(l["title"]) == normtitle(title)
                and l["index"] not in used]
        if not cand and n - 1 < len(S["lessons"]):
            cand = [S["lessons"][n - 1]]
        if cand:
            used.add(cand[0]["index"])
            pages = set(range(pg, end if end > pg else pg + 1))
            if chaps[k + 1:] and chaps[k + 1][2] == pg:
                pages = {pg}
            chap_pages.setdefault(cand[0]["index"], set()).update(pages)
    for l in S["lessons"]:
        if l["index"] not in chap_pages:
            fail("lesson-title-missing", None, "no 'Hoofdstuk N' chapter heading with this title",
                 text=l["title"], lesson=l["index"], btype="lesson", field="lesson.title")
    # chapter heading text vs lesson title exactness
    for n, title, pg, _ in chaps:
        if not any(normtitle(l["title"]) == normtitle(title) for l in S["lessons"]):
            fail("lesson-title-altered", None, "chapter heading on p%d matches no lesson title" % (pg + 1),
                 text=title, lesson=None, btype="lesson", field="lesson.title")
    lesson_tokpos = chap_pages
    cover_tokpos = set(range(0, max(1, first_body_page)))

    # ---- text coverage (check 1) ----
    # A unit is found when an *aligned* window of the PDF body token stream contains all
    # its tokens in order (difflib matching blocks), preferably inside its own chapter.
    sig_found = collections.defaultdict(lambda: [0, 0])  # sig -> [found, total]
    for u in S["units"]:
        tk = toks(u.text)
        if not tk:
            continue
        allowed = cover_tokpos if u.lesson == -1 else lesson_tokpos.get(u.lesson)
        m_ch, miss_ch, win_ch, lb_ch = align2(tk, P, P2, allowed) if allowed else (0, tk, "", 0)
        m_any, miss_any, win_any, lb_any = (m_ch, miss_ch, win_ch, lb_ch) if m_ch == len(tk) \
            else align2(tk, P, P2, None)
        flat_ok = len("".join(tk)) >= 6 and "".join(tk) in P.flat
        found = m_any == len(tk) or flat_ok
        sig_found[u.sig][1] += 1
        if m_any >= len(tk) / 2 or flat_ok:
            sig_found[u.sig][0] += 1
        if u.status != "required":
            stats["units_" + u.status] += 1
            if u.status == "soft" and not found:
                fail("soft-missing (%s)" % u.reason, u, "%d/%d words aligned" % (m_any, len(tk)))
            continue
        stats["units_required"] += 1
        if u.check == "lesson-title":
            continue
        ck = u.check or u.btype
        CHECK_STATS[ck][1] += 1
        tag = (" [%s]" % u.check) if u.check else ""
        if not found:
            stats["units_failed"] += 1
            best_m, best_miss, best_win, best_lb = max((m_ch, miss_ch, win_ch, lb_ch),
                                                       (m_any, miss_any, win_any, lb_any),
                                                       key=lambda x: x[0])
            # coincidental matches (scattered common words) count as missing: require a
            # contiguous aligned run of at least 60% of the unit
            if best_m == 0 or best_lb < max(1, 0.6 * len(tk)):
                cause = "text-missing"
                det = "no aligned occurrence in the PDF (best %d/%d words)" % (best_m, len(tk))
            elif u.check.startswith("kc-") and is_leadin_drop(tk, best_miss):
                cause = "feedback-praise-leadin-removed"
                det = "leading praise word(s) %r dropped; rest present" % " ".join(best_miss)
            elif len(best_miss) <= 2:
                cause = "word-altered-or-missing"
                det = "missing/altered word(s): %s; PDF reads: \"%s\"" % (", ".join(best_miss), best_win)
            else:
                cause = "text-partly-missing"
                det = "%d/%d words aligned; missing: %s; PDF reads: \"%s\"" % (
                    best_m, len(tk), " ".join(best_miss)[:200], best_win[:200])
            fail(cause, u, det + tag)
            continue
        CHECK_STATS[ck][0] += 1
        if m_any == len(tk) and allowed is not None and m_ch < len(tk) and len(tk) >= 3:
            fail("text-in-wrong-chapter", u, "found in PDF but not inside its own chapter/cover pages" + tag)
        if m_any < len(tk) and flat_ok:
            fail("spacing-differs", u, "only found after removing all spaces (merged/split words)" + tag)

    # ---- knowledge checks and interactive structure (check 3) ----
    kc_results = []
    for li, L in enumerate(co.get("lessons") or []):
        pages = chap_pages.get(li, set(range(P.npages)))
        for bi, b in enumerate(L.get("items") or []):
            btype = "%s/%s" % (b.get("type"), b.get("variant"))
            if b.get("type") == "knowledgeCheck":
                for it in b.get("items") or []:
                    kc_results.append(check_kc(P, it, li, btype, pages, fail))
            if b.get("variant") == "sorting":
                check_sorting(P, b, li, btype, pages, fail)
            if b.get("type") == "interactive" and b.get("variant") == "storyline":
                fail("storyline-in-rise-not-expanded", None,
                     "embedded Storyline interaction: only its title can be referenced; content is "
                     "not in the course JSON (manual check)",
                     text=((b.get("items") or [{}])[0].get("media", {}).get("storyline", {})
                           .get("title", "")), lesson=li, btype=btype, field="media.storyline")
    stats["kc"] = sum(1 for r in kc_results if r in ("marked", "not-marked", "unverifiable"))
    stats["kc_all"] = len(kc_results)
    stats["kc_marked"] = sum(1 for r in kc_results if r == "marked")

    # ---- links (check 4) ----
    pdf_urls = {norm_url(u) for _, u in P.links}
    src_links = []
    for u in S["links"]:
        if not u or u.startswith("#") or u.lower().startswith(("mailto:", "javascript:")):
            continue
        if not re.match(r"^(https?:)?//", u):
            continue
        src_links.append(u)
    for u in sorted(set(src_links)):
        nu = norm_url(u)
        ok = nu in pdf_urls or any(nu.split("?")[0] == p.split("?")[0] for p in pdf_urls)
        if not ok:
            # youtube watch vs youtu.be
            m = re.search(r"(?:v=|youtu\.be/|embed/)([\w-]{11})", u)
            ok = bool(m) and any(m.group(1) in p for p in pdf_urls)
        if not ok:
            fail("link-missing", None, "no link annotation with this URL", text=u, lesson=None,
                 btype="link", field="href")
    stats["links"] = len(set(src_links))

    # ---- new / merged words (check 2) ----
    vocab = S["vocab"] | EXPORTER_CHROME_WORDS | {w for c in S["chrome"] for w in toks(c)}
    new = collections.Counter()
    ctx = {}
    for pi, y, x, t, sp in P.lines:
        nt = norm(t).strip()
        if any(re.search(r, nt) for r in EXPORTER_CHROME_LINE_RES):
            continue
        for w in toks(t):
            if w in vocab or w.isdigit():
                continue
            new[w] += 1
            ctx.setdefault(w, "p%d: %s" % (pi + 1, t.strip()[:120]))
    for w, n in new.items():
        split = split_merged(w, S["vocab"])
        if split:
            cause, det = "merged-words", "PDF word %r = %r + %r (x%d)" % (w, split[0], split[1], n)
        else:
            near = [v for v in S["vocab"] if abs(len(v) - len(w)) <= 1 and lev(v, w, 1) <= 1]
            cause = "altered-word" if near else "new-word"
            det = "PDF word %r not in source (x%d)%s" % (w, n, (" ~ " + ", ".join(near[:3])) if near else "")
        fail(cause, None, det + "; " + ctx[w], text=w, lesson=None, btype="pdf-body", field="")

    # ---- invisible text in PDF text layer ----
    if P.invisible:
        fail("invisible-text-layer", None,
             "%d text spans < %.0fpt (unreadable) in the text layer, e.g. %s" % (
                 sum(P.invisible.values()), INVISIBLE_SIZE, ", ".join(list(P.invisible)[:5])),
             text=" ".join(list(P.invisible)[:8]), lesson=None, btype="pdf-body", field="")

    # ---- images (check 7) ----
    img_rows = []
    for l in S["lessons"]:
        pages = chap_pages.get(l["index"], set())
        got = sum(P.images[p] for p in pages)
        img_rows.append({"lesson": l["title"], "src_required": l["img_req"],
                         "src_optional": l["img_opt"], "pdf": got})
        if got < l["img_req"]:
            fail("images-fewer", None, "source has %d required (+%d optional) images, PDF chapter has %d"
                 % (l["img_req"], l["img_opt"], got), text=l["title"], lesson=l["index"],
                 btype="images", field="")
    stats["img_src"] = sum(r["src_required"] for r in img_rows)
    stats["img_pdf"] = sum(r["pdf"] for r in img_rows)
    return S, P, fails, stats, sig_found, img_rows, kc_results


def nearest(w, P):
    best, bd = "", 9
    for v in set(P.tokens):
        if abs(len(v) - len(w)) <= 2:
            d = lev(v, w, 2)
            if d < bd:
                best, bd = v, d
    return best if bd <= 2 else "?"


PRAISE_WORDS = {"goed", "juist", "correct", "klopt", "dat", "dit", "is", "zo", "inderdaad", "heel",
                "gedaan", "gezien", "geredeneerd", "prima", "precies"}


def is_leadin_drop(tk, miss):
    """All missing tokens are praise words forming a prefix of the unit."""
    if not miss or len(miss) > 4:
        return False
    return all(w in PRAISE_WORDS for w in miss) and tk[:len(miss)] == miss


def split_merged(w, vocab):
    for i in range(2, len(w) - 1):
        a, b = w[:i], w[i:]
        if a in vocab and b in vocab and len(a) >= 2 and len(b) >= 2:
            return a, b
    return None


def find_option_lines(P, text, pages):
    want = toks(text)
    if not want:
        return []
    hits = []
    for i, (pi, y, x, t, sp) in enumerate(P.lines):
        if pi not in pages:
            continue
        lt = toks(t)
        k = min(len(want), len(lt))
        if len(want) <= 3:
            if lt == want:
                hits.append(i)
        elif k >= 2 and lt[:k] == want[:k]:
            hits.append(i)
    return hits


def marker_near(P, idx):
    """Is a check-mark glyph on the same visual line (left of the option)?"""
    pi, y, x, t, sp = P.lines[idx]
    for pj, yj, xj, tj, spj in P.lines:
        if pj == pi and abs(yj - y) < 4 and xj < x:
            f = spj[0]["font"]
            if "Dingbat" in f or tj.strip() in ("✓", "✔", "☑"):
                return True
    return t.strip().startswith(("✓", "✔"))


USED_Q = set()
CHECK_STATS = collections.defaultdict(lambda: [0, 0])   # check/block -> [found, total]


def question_window(P, title_html, pages, used):
    """Line-index window (start, end) of a knowledge check in the PDF: from the line
    where the question starts to the next 'VRAAG' label (or 120 lines)."""
    want = toks(" ".join(html_text(title_html)[0]))[:12]
    if not want:
        return None
    T = P.tokens
    cands = []
    for pos in P.ngram_pos.get(tuple(want[:3]) if len(want) >= 3 else tuple(want), []):
        if T[pos:pos + len(want)] != want:
            continue
        i = P.tok_line[pos]
        pi, y = P.lines[i][0], P.lines[i][1]
        if pi not in pages or (P.path, i) in used:
            continue
        labelled = any(P.lines[j][0] == pi and P.lines[j][3].strip() == "VRAAG" and 0 < y - P.lines[j][1] < 60
                       for j in range(max(0, i - 80), min(len(P.lines), i + 80)))
        cands.append((not labelled, pi, y, i))
    if not cands:
        return None
    _, pi, y, i = min(cands)
    used.add((P.path, i))
    start = (pi, y)
    labels = sorted((P.lines[j][0], P.lines[j][1]) for j in range(len(P.lines))
                    if P.lines[j][3].strip() == "VRAAG" and (P.lines[j][0], P.lines[j][1]) > start)
    end = labels[0] if labels else (pi + 2, 0)
    return (start, min(end, (pi + 2, 0)))


def check_kc(P, it, li, btype, pages, fail):
    kind = it.get("type")
    q = " ".join(html_text(it.get("title") or "")[0])
    answers = it.get("answers") or []

    def F(cause, detail, text):
        fail(cause, None, detail, text=text, lesson=li, btype=btype, field="knowledgeCheck")
    if kind in ("MULTIPLE_CHOICE", "MULTIPLE_RESPONSE"):
        win = question_window(P, it.get("title") or "", pages, USED_Q)
        if win is None:
            F("kc-question-not-located", "question start not found as a line in its chapter", q)
            win = None
        opt_lines = []
        for a in answers:
            at = " ".join(html_text(a.get("title") or "")[0])
            hits = find_option_lines(P, at, pages)
            if win is not None:
                hits = [h for h in hits if win[0] < (P.lines[h][0], P.lines[h][1]) < win[1]]
            opt_lines.append((a, at, hits))
        missing = [at for _, at, h in opt_lines if not h]
        if missing:
            for at in missing:
                F("kc-option-not-found-as-line", "option not found as its own line after the question "
                  "(question: %s)" % q[:80], at)
            return "unverifiable"
        chosen = []
        for _, _, h in opt_lines:   # identical option texts: take successive hits
            free = [x for x in h if x not in chosen]
            chosen.append(free[0] if free else h[0])
        if len(set(chosen)) != len(chosen):
            F("kc-options-ambiguous", "two options resolve to the same PDF line", q)
            return "unverifiable"
        corr = [style_sig(P.lines[i][4]) for (a, _, _), i in zip(opt_lines, chosen) if a.get("correct")]
        inc = [style_sig(P.lines[i][4]) for (a, _, _), i in zip(opt_lines, chosen) if not a.get("correct")]
        mk_c = [marker_near(P, i) for (a, _, _), i in zip(opt_lines, chosen) if a.get("correct")]
        mk_i = [marker_near(P, i) for (a, _, _), i in zip(opt_lines, chosen) if not a.get("correct")]
        if not corr:
            F("kc-no-correct-in-source", "source has no correct option", q)
            return "unverifiable"
        if any(mk_i):
            F("kc-wrong-option-marked", "a wrong option carries a check mark; correct=%s others=%s"
              % (mk_c, mk_i), q)
            return "not-marked"
        style_ok = not (set(corr) & set(inc))
        if not (all(mk_c) or style_ok):
            F("kc-correct-not-marked", "correct option styles %s / others %s; checkmarks correct=%s"
              % (corr, inc, mk_c), q)
            return "not-marked"
        return "marked"
    if kind == "MATCHING":
        for a in answers:
            l, r = a.get("title") or "", a.get("matchTitle") or ""
            hl, hr = find_option_lines(P, l, pages), find_option_lines(P, r, pages)
            if not hl or not hr:
                # fall back to n-gram presence
                if not (all(covered(toks(l), P)) and all(covered(toks(r), P))):
                    F("kc-matching-pair-missing", "pair %r -> %r not found" % (l, r), l)
                continue
            near = any(P.lines[i][0] == P.lines[j][0] and abs(P.lines[i][1] - P.lines[j][1]) < 40
                       for i in hl for j in hr)
            if not near:
                F("kc-matching-pair-not-adjacent", "pair %r -> %r: sides not on the same row" % (l, r), l)
        return "matching"
    if kind == "FILL_IN_THE_BLANK":
        for a in answers:
            t = (a.get("title") or "").strip()
            if t and not all(covered(toks(t), P)):
                F("kc-fillin-answer-missing", "accepted answer not shown", t)
        return "fillin"
    F("kc-unknown-type", "unknown knowledge-check type %r" % kind, q)
    return "unknown"


def check_sorting(P, b, li, btype, pages, fail):
    piles = {p.get("id"): (p.get("title") or "").strip() for p in b.get("piles") or []}
    pile_lines = []
    for pid, pt in piles.items():
        want = toks(pt)
        for i, (pi, y, x, t, sp) in enumerate(P.lines):
            if pi not in pages:
                continue
            acc = toks(t)
            if not acc or want[:len(acc)] != acc:
                continue
            j, ly = i, y
            while len(acc) < len(want):
                nxt = [k for k in range(j + 1, min(j + 40, len(P.lines)))
                       if P.lines[k][0] == pi and abs(P.lines[k][2] - x) < 2 and 0 < P.lines[k][1] - ly < 22]
                if not nxt:
                    break
                j = nxt[0]
                ly = P.lines[j][1]
                acc = acc + toks(P.lines[j][3])
            if acc == want:
                pile_lines.append((i, pid))
    if not pile_lines:
        fail("sorting-piles-not-found", None, "no pile heading lines found", text=" / ".join(piles.values()),
             lesson=li, btype=btype, field="sorting")
        return
    for it in b.get("items") or []:
        t = it.get("title") or ""
        hits = find_option_lines(P, t, pages)
        if not hits:
            continue  # reported by coverage
        first_pile = min(i for i, _ in pile_lines)
        later = [x for x in hits if x > first_pile and P.lines[x][0] == P.lines[first_pile][0]] or \
            [x for x in hits if x > first_pile] or hits
        h = later[0]
        pi, y, x = P.lines[h][0], P.lines[h][1], P.lines[h][2]
        # piles may be laid out as columns: the pile is the header on the same page above
        # the card whose column (x start) is the closest one left of / at the card.
        same = [(P.lines[i][2], -P.lines[i][1], pid) for i, pid in pile_lines
                if P.lines[i][0] == pi and P.lines[i][1] < y and P.lines[i][2] <= x + 2]
        if same:
            got = max(same)[2]
        else:
            before = [(i, pid) for i, pid in pile_lines if i < h]
            got = max(before)[1] if before else None
        if got != it.get("pileId"):
            fail("sorting-card-under-wrong-pile", None,
                 "card listed under %r, source pile is %r" % (piles.get(got), piles.get(it.get("pileId"))),
                 text=t, lesson=li, btype=btype, field="sorting")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(argv):
    args, out, only = [], "rise_verify_report.json", None
    it = iter(argv)
    for a in it:
        if a == "--out":
            out = next(it)
        elif a == "--only":
            only = next(it)
        else:
            args.append(a)
    if len(args) < 2:
        print(__doc__)
        return 2
    src_dir, pdf_dirs = args[0], args[1:]
    pdfs = {}
    for d in pdf_dirs:
        for fp in glob.glob(os.path.join(os.path.expanduser(d), "*.pdf")):
            base = re.sub(r"-\d{4}-\d\d-\d\d$", "", os.path.basename(fp)[:-4])
            pdfs[normtitle(base)] = fp
    sources = sorted(glob.glob(os.path.join(os.path.expanduser(src_dir), "*.rise.json")))
    report = {"modules": {}, "unmatched_sources": [], "unmatched_pdfs": []}
    matched = set()
    all_fails, inv_types, inv_sigs = [], collections.Counter(), collections.defaultdict(lambda: [0, 0])
    sig_status = collections.defaultdict(collections.Counter)
    summary = []
    for sp in sources:
        name = os.path.basename(sp)[:-len(".rise.json")]
        if only and only not in name:
            continue
        _, co, _ = load_course(sp)
        key = normtitle(name)
        fp = pdfs.get(key) or pdfs.get(normtitle(co.get("title", "")))
        if not fp:
            report["unmatched_sources"].append(name)
            continue
        matched.add(fp)
        S, P, fails, stats, sig_found, img_rows, kc = verify(sp, fp)
        inv_types.update(S["types"])
        for s, (f, t) in sig_found.items():
            inv_sigs[s][0] += f
            inv_sigs[s][1] += t
        for u in S["units"]:
            sig_status[u.sig][u.status + (":" + u.reason if u.reason else "")] += 1
        for f in fails:
            f["module"] = name
        all_fails += fails
        causes = collections.Counter(f["cause"] for f in fails)
        report["modules"][name] = {"pdf": fp, "pages": P.npages, "stats": dict(stats),
                                   "fail_counts": dict(causes), "failures": fails,
                                   "images": img_rows, "chapters": [c[:3] for c in P.chapters]}
        summary.append((name, P.npages, len(S["lessons"]), stats, causes))
    for k, fp in pdfs.items():
        if fp not in matched and not only:
            report["unmatched_pdfs"].append(os.path.basename(fp))
    # inventory (check 6)
    inv = []
    for s, (f, t) in sorted(inv_sigs.items()):
        inv.append({"field": s, "units": t, "found": f, "status": dict(sig_status[s]),
                    "never_in_pdf": f == 0})
    report["inventory"] = {"block_types": dict(inv_types), "fields": inv}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    # ---- print ----
    print("columns: pages, lessons, required text units, text units failed, MC/MR questions with the "
          "correct option marked / total, required images source/PDF, links checked, other failure causes")
    print("%-55s %4s %3s %6s %5s %6s %8s %5s %s" % ("module", "pp", "les", "units", "fail", "KCmark", "img s/p", "links", "other"))
    for name, pp, nl, st, causes in summary:
        print("%-55s %4d %3d %6d %5d %3d/%-3d %4d/%-4d %5d %s" % (
            name[:55], pp, nl, st["units_required"], st["units_failed"], st["kc_marked"], st["kc"],
            st["img_src"], st["img_pdf"], st["links"],
            ",".join("%s:%d" % kv for kv in sorted(causes.items()) if kv[0] not in ("text-missing",))))
    print("\nunmatched sources:", report["unmatched_sources"])
    print("unmatched PDFs (expected: Storyline modules):", report["unmatched_pdfs"])
    print("\nblock types:", dict(inv_types))
    print("\nfields whose text never appears in any PDF:")
    for r in inv:
        if r["never_in_pdf"]:
            print("  %-70s units=%d %s" % (r["field"][:70], r["units"], r["status"]))
    print("\nper check / block type: required text units fully found / total")
    for ck, (f, t) in sorted(CHECK_STATS.items()):
        print("  %-45s %4d / %-4d%s" % (ck, f, t, "" if f == t else "   <-- %d missing" % (t - f)))
    report["check_stats"] = {k: {"found": v[0], "total": v[1]} for k, v in CHECK_STATS.items()}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    by = collections.defaultdict(list)
    for f in all_fails:
        by[f["cause"]].append(f)
    print("\n==== FAILURES BY CAUSE ====")
    for cause in sorted(by, key=lambda c: -len(by[c])):
        print("\n## %s (%d)" % (cause, len(by[cause])))
        for f in by[cause]:
            print("- [%s] L%s %r | %s | %s\n    text: %s\n    why: %s" % (
                f["module"], f["lesson_index"], f["lesson"][:40], f["block"], f["field"],
                f["text"][:200].replace("\n", " "), f["detail"][:300]))
    print("\nreport written to", out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
