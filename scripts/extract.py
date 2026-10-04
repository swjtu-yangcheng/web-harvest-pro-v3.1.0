"""Dependency-free structural extraction with optional article and PDF engines."""
from __future__ import annotations

import csv
import html
import io
import json
import re
import urllib.parse as up
import xml.etree.ElementTree as ET
from html.parser import HTMLParser


def decode(raw, content_type=""):
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", "replace"), "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace"), "utf-16"
    declared = re.search(r"charset\s*=\s*[\"']?([\w.-]+)", content_type, re.I)
    meta = re.search(br"charset\s*=\s*[\"']?([\w.-]+)", raw[:4096], re.I)
    names = [declared.group(1) if declared else "", meta.group(1).decode() if meta else "", "utf-8", "gb18030"]
    for name in dict.fromkeys(names):
        if not name:
            continue
        try:
            return raw.decode(name), name
        except (UnicodeError, LookupError):
            pass
    return raw.decode("utf-8", "replace"), "utf-8-with-replacements"


class Node:
    def __init__(self, tag="root", attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    def text(self):
        return "".join(c.text() if isinstance(c, Node) else c for c in self.children)

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()


class DOM(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root, self.stack = Node(), []
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        # Common HTML optional closing tags; full HTML5 recovery remains optional.
        if tag in {"p", "li", "tr", "td", "th"} and self.stack[-1].tag == tag:
            self.stack.pop()
        n = Node(tag, attrs)
        self.stack[-1].children.append(n)
        if tag not in self.VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def compact(s):
    return re.sub(r"\s+", " ", s).strip()


def table_rows(node):
    rows = []
    for tr in node.walk():
        if tr.tag == "tr":
            cells = [compact(c.text()).replace("|", "\\|") for c in tr.children if isinstance(c, Node) and c.tag in {"td", "th"}]
            if cells:
                rows.append(cells)
    return rows


def markdown(node, base):
    if isinstance(node, str):
        return re.sub(r"\s+", " ", node)
    if node.tag in {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "form"} or "hidden" in node.attrs or node.attrs.get("aria-hidden") == "true":
        return ""
    if node.tag == "pre":
        return "\n\n```\n" + node.text().strip() + "\n```\n\n"
    if node.tag == "table":
        rows = table_rows(node)
        if not rows:
            return ""
        width = max(map(len, rows))
        padded = [r + [""] * (width - len(r)) for r in rows]
        return "\n\n" + "\n".join(["| " + " | ".join(padded[0]) + " |", "| " + " | ".join(["---"] * width) + " |"] + ["| " + " | ".join(r) + " |" for r in padded[1:]]) + "\n\n"
    text = "".join(markdown(c, base) for c in node.children)
    if node.tag == "a" and node.attrs.get("href") and compact(text):
        href = up.urljoin(base, node.attrs["href"])
        if up.urlsplit(href).scheme in {"http", "https"}:
            # Keep labels with no Markdown link syntax; URLs remain in structured links.
            return text
    if re.fullmatch(r"h[1-6]", node.tag):
        return "\n\n" + "#" * int(node.tag[1]) + " " + compact(text) + "\n\n"
    if node.tag == "li":
        return "\n- " + text.strip() + "\n"
    if node.tag in {"p", "div", "section", "article", "main", "blockquote", "ul", "ol", "dl", "dt", "dd"}:
        return "\n\n" + text.strip() + "\n\n"
    if node.tag == "br":
        return "\n"
    return text


def xml_root(text):
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
        raise ValueError("DTD/entity declarations are not accepted")
    return ET.fromstring(text)


def extract(raw, content_type, url, selector=None, use_trafilatura=True):
    out = {"title": "", "markdown": "", "links": [], "tables": [], "structured": [], "canonical_url": None, "published_at": None, "updated_at": None, "authors": [], "language": None, "kind": "", "extractor": "stdlib", "warnings": [], "quality": "complete"}
    if raw.startswith(b"%PDF-") or "application/pdf" in content_type:
        out["kind"] = "pdf"
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                out["quality"] = "encrypted_pdf"
                return out
            if len(reader.pages) > 500:
                out["quality"] = "pdf_page_limit"
                return out
            out["title"] = str((reader.metadata or {}).get("/Title", ""))
            pages = ["## PDF page " + str(i) + "\n\n" + (p.extract_text() or "") for i, p in enumerate(reader.pages, 1)]
            out["markdown"] = "\n\n".join(pages)
            out["extractor"] = "pypdf"
            if sum(len((p.extract_text() or "").strip()) for p in reader.pages) < 20:
                out["quality"] = "ocr_required"
                out["markdown"] = ""
        except ImportError:
            out["quality"] = "pdf_dependency_missing"
        return out
    text, encoding = decode(raw, content_type)
    out["encoding"] = encoding
    if "with-replacements" in encoding:
        out["warnings"].append("encoding_fallback")
    if "json" in content_type or text.lstrip().startswith(("{", "[")):
        out["kind"] = "json"
        obj = json.loads(text)
        out["structured"] = obj if isinstance(obj, list) else [obj]
        out["markdown"] = "```json\n" + json.dumps(obj, ensure_ascii=False, indent=2) + "\n```"
        return out
    if "csv" in content_type:
        out["kind"] = "csv"
        rows = list(csv.DictReader(io.StringIO(text)))
        out["structured"] = rows
        out["markdown"] = "```csv\n" + text + "\n```"
        return out
    if "xml" in content_type or text.lstrip().startswith("<?xml") or re.match(r"\s*<(rss|feed|urlset|sitemapindex)\b", text):
        root = xml_root(text)
        tag = root.tag.split("}")[-1]
        out["kind"] = "sitemap" if tag in {"urlset", "sitemapindex"} else "feed" if tag in {"rss", "feed"} else "xml"
        out["sitemap_index"] = tag == "sitemapindex"
        for e in root.iter():
            local = e.tag.split("}")[-1]
            if local in {"loc", "link"}:
                link = e.get("href") or e.text or ""
                link = up.urljoin(url, link.strip())
                if up.urlsplit(link).scheme in {"http", "https"}:
                    out["links"].append({"url": link, "rel": e.get("rel", ""), "text": ""})
            if local in {"entry", "item"}:
                row = {c.tag.split("}")[-1]: compact("".join(c.itertext())) for c in e}
                out["structured"].append(row)
        out["markdown"] = "\n\n".join(json.dumps(r, ensure_ascii=False) for r in out["structured"]) or text
        return out
    if "html" not in content_type and not re.search(r"<(html|head|body|main|article|div|p|h[1-6])\b", text, re.I):
        if content_type.startswith("text/") or not content_type:
            out["kind"], out["markdown"] = "text", text.strip()
        else:
            out["kind"], out["quality"] = "binary", "unsupported_type"
        return out
    out["kind"] = "html"
    root = DOM(text).root
    nodes = list(root.walk())
    metas = {str(n.attrs.get("property") or n.attrs.get("name") or "").lower(): n.attrs.get("content", "") for n in nodes if n.tag == "meta"}
    titles = [n.text() for n in nodes if n.tag == "title"]
    out["title"] = compact(metas.get("og:title") or (titles[0] if titles else ""))
    out["published_at"] = metas.get("article:published_time") or metas.get("citation_publication_date") or None
    out["updated_at"] = metas.get("article:modified_time") or None
    out["authors"] = [metas[k] for k in ["author", "citation_author"] if metas.get(k)]
    out["language"] = next((n.attrs["lang"] for n in nodes if n.tag == "html" and n.attrs.get("lang")), None)
    for n in nodes:
        if n.tag == "link" and "canonical" in n.attrs.get("rel", "").lower():
            canonical = up.urljoin(url, n.attrs.get("href", ""))
            # Do not trust cross-origin canonical claims for automatic deduplication.
            if up.urlsplit(canonical).netloc == up.urlsplit(url).netloc:
                out["canonical_url"] = canonical
            else:
                out["warnings"].append("cross_origin_canonical_ignored")
        if n.tag == "a" and n.attrs.get("href"):
            link = up.urljoin(url, html.unescape(n.attrs["href"]))
            if up.urlsplit(link).scheme in {"http", "https"}:
                out["links"].append({"url": link, "text": compact(n.text()), "rel": n.attrs.get("rel", "")})
        if n.tag == "table":
            out["tables"].append(table_rows(n))
            if any(c.attrs.get("rowspan") or c.attrs.get("colspan") for c in n.walk()):
                out["warnings"].append("table_spans_require_review")
        if n.tag == "script" and "ld+json" in n.attrs.get("type", ""):
            try:
                data = json.loads(n.text())
                out["structured"].extend(data if isinstance(data, list) else [data])
            except ValueError:
                out["warnings"].append("invalid_jsonld")
    candidates = [n for n in nodes if n.tag in {"article", "main"}]
    best = max(candidates, key=lambda n: len(markdown(n, url)), default=next((n for n in nodes if n.tag == "body"), root))
    out["markdown"] = re.sub(r"\n{3,}", "\n\n", markdown(best, url)).strip()
    if selector:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            raise ValueError("CSS selection requires beautifulsoup4") from None
        selected = BeautifulSoup(text, "html.parser").select(selector)
        if not selected:
            out["markdown"], out["quality"] = "", "selector_not_found"
        else:
            out["markdown"] = "\n\n".join(markdown(DOM(str(n)).root, url).strip() for n in selected)
            out["extractor"] = "beautifulsoup4+stdlib"
    elif use_trafilatura and not out["tables"]:
        try:
            import trafilatura
            clean = trafilatura.extract(text, url=url, include_tables=True, include_comments=False, output_format="markdown")
            if clean and len(clean) >= min(80, len(out["markdown"])):
                out["markdown"], out["extractor"] = clean, "trafilatura"
        except ImportError:
            pass
        except Exception:
            out["warnings"].append("trafilatura_failed_used_stdlib")
    visible = compact(out["markdown"])
    challenge = re.search(r"cf-chl-|cf-browser-verification|g-recaptcha|hcaptcha|验证您是人类|checking your browser|verify you are human", text, re.I)
    if challenge and len(visible) < 1500:
        out["quality"] = "challenge"
    elif len(visible) < 160 and re.search(r"enable javascript|启用.{0,3}javascript|<div[^>]+id=[\"'](?:root|app)[\"']", text, re.I):
        out["quality"] = "js_required"
    elif not visible:
        out["quality"] = "empty"
    elif metas.get("robots", "").lower().find("noarchive") >= 0:
        out["warnings"].append("publisher_noarchive")
    return out
