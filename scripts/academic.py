"""Scholarly lookup and honest five-element metadata checks. No inferred citations."""
from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
import urllib.parse as up
import xml.etree.ElementTree as ET

from core import HarvestError, now


def norm(text):
    s = unicodedata.normalize("NFKD", str(text or "")).casefold()
    return "".join(c for c in s if c.isalnum())


def doi_norm(doi):
    s = str(doi or "").strip()
    s = re.sub(r"^https?://(?:dx\.)?doi\.org/|^doi:\s*", "", s, flags=re.I)
    s = up.unquote(s)
    if not re.fullmatch(r"10\.\d{4,9}/\S+", s):
        raise ValueError("Invalid DOI syntax")
    return s.lower()


def crossref_item(m):
    dates = []
    for key in ["issued", "published-print", "published-online", "published"]:
        try:
            dates.append(int(m[key]["date-parts"][0][0]))
        except (KeyError, TypeError, IndexError, ValueError):
            pass
    return {"source": "crossref", "title": (m.get("title") or [""])[0], "authors": [(a.get("given", "") + " " + a.get("family", "")).strip() for a in m.get("author", [])], "journal": (m.get("container-title") or [""])[0], "year": dates[0] if dates else None, "years": sorted(set(dates)), "doi": m.get("DOI", "").lower(), "volume": m.get("volume"), "issue": m.get("issue"), "pages": m.get("page"), "type": m.get("type"), "publisher": m.get("publisher"), "relation": m.get("relation", {}), "updates": m.get("update-to", []), "url": m.get("URL")}


def get_json(client, url, headers=None):
    res = client.get(url, headers=headers, purpose="api")
    if res["status"] == 404:
        return None
    if res["status"] != 200:
        raise HarvestError("metadata_http_error", f"Metadata HTTP {res['status']}", res["status"])
    return json.loads(res["body"])


def lookup_doi(client, doi):
    doi = doi_norm(doi)
    errors = []
    try:
        m = get_json(client, "https://api.crossref.org/works/" + up.quote(doi, safe=""))
        if m:
            return crossref_item(m["message"]), errors
    except Exception as e:
        errors.append({"provider": "crossref", "error_kind": getattr(e, "kind", type(e).__name__)})
    try:
        m = get_json(client, "https://api.datacite.org/dois/" + up.quote(doi, safe=""))
        if m:
            a = m["data"]["attributes"]
            return {"source": "datacite", "title": (a.get("titles") or [{}])[0].get("title", ""), "authors": [c.get("name", "") for c in a.get("creators", [])], "journal": a.get("container", {}).get("title", "") if a.get("container") else "", "year": a.get("publicationYear"), "years": [a.get("publicationYear")], "doi": a.get("doi", doi).lower(), "type": a.get("types", {}).get("resourceTypeGeneral"), "url": a.get("url")}, errors
    except Exception as e:
        errors.append({"provider": "datacite", "error_kind": getattr(e, "kind", type(e).__name__)})
    try:
        m = get_json(client, "https://doi.org/" + up.quote(doi, safe="/"), {"Accept": "application/vnd.citationstyles.csl+json"})
        if m:
            year = (m.get("issued", {}).get("date-parts", [[None]])[0] or [None])[0]
            return {"source": "doi-csl", "title": m.get("title", ""), "authors": [(a.get("given", "") + " " + a.get("family", "")).strip() for a in m.get("author", [])], "journal": m.get("container-title", ""), "year": year, "years": [year], "doi": m.get("DOI", doi).lower(), "type": m.get("type")}, errors
    except Exception as e:
        errors.append({"provider": "doi-csl", "error_kind": getattr(e, "kind", type(e).__name__)})
    return None, errors


def openalex(client, query="", doi="", limit=5):
    params = {"per_page": min(limit, 100)}
    if doi:
        params["filter"] = "doi:https://doi.org/" + doi_norm(doi)
    else:
        params["search"] = query
    headers = {"Authorization": "Bearer " + os.environ["OPENALEX_API_KEY"]} if os.environ.get("OPENALEX_API_KEY") else None
    m = get_json(client, "https://api.openalex.org/works?" + up.urlencode(params), headers)
    rows = []
    for w in (m or {}).get("results", []):
        loc = w.get("primary_location") or {}
        rows.append({"source": "openalex", "title": w.get("display_name", ""), "authors": [a.get("author", {}).get("display_name", "") for a in w.get("authorships", [])], "year": w.get("publication_year"), "journal": (loc.get("source") or {}).get("display_name", ""), "doi": (w.get("doi") or "").removeprefix("https://doi.org/").lower(), "oa": w.get("open_access"), "is_retracted": w.get("is_retracted"), "id": w.get("id")})
    return rows


def arxiv(client, query, limit=5):
    base = "https://export.arxiv.org"
    client.gate.state(base)["delay"] = max(client.gate.state(base)["delay"], 3)
    url = base + "/api/query?" + up.urlencode({"search_query": 'all:"' + query.replace('"', "") + '"', "max_results": limit})
    r = client.get(url, purpose="api")
    if r["status"] != 200:
        raise HarvestError("metadata_http_error", f"arXiv HTTP {r['status']}", r["status"])
    from extract import xml_root
    root = xml_root(r["body"].decode("utf-8"))
    ns = {"a": "http://www.w3.org/2005/Atom"}
    rows = []
    for e in root.findall("a:entry", ns):
        title = " ".join(e.findtext("a:title", "", ns).split())
        if title.lower() == "error":
            raise HarvestError("arxiv_error_entry", "arXiv returned an error entry under HTTP 200")
        rows.append({"source": "arxiv", "title": title, "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)], "url": e.findtext("a:id", "", ns), "published_at": e.findtext("a:published", "", ns), "version_state": "preprint", "abstract": " ".join(e.findtext("a:summary", "", ns).split())})
    return rows


def paper(client, query, provider="crossref", limit=5):
    try:
        doi = doi_norm(query)
    except ValueError:
        doi = ""
    if doi:
        match, errors = lookup_doi(client, doi)
        return {"query": query, "matches": [match] if match else [], "provider_errors": errors, "state": "metadata_found" if match else "unverifiable" if errors else "not_found"}
    if provider == "openalex":
        rows = openalex(client, query=query, limit=limit)
    elif provider == "arxiv":
        rows = arxiv(client, query, limit)
    else:
        params = {"query.bibliographic": query, "rows": limit}
        if os.environ.get("CROSSREF_MAILTO"):
            params["mailto"] = os.environ["CROSSREF_MAILTO"]
        m = get_json(client, "https://api.crossref.org/works?" + up.urlencode(params))
        rows = [crossref_item(w) for w in (m or {}).get("message", {}).get("items", [])]
    return {"query": query, "provider": provider, "matches": rows, "state": "candidates_only"}


def compare_reference(ref, metadata):
    checks = {}
    for key in ["title", "authors", "journal", "year", "doi"]:
        given, actual = ref.get(key), metadata.get(key)
        if not given or not actual:
            checks[key] = "missing"
            continue
        if key == "doi":
            checks[key] = "match" if doi_norm(given) == doi_norm(actual) else "mismatch"
        elif key == "year":
            try:
                checks[key] = "match" if int(given) == int(actual) else "review_online_print_year" if int(given) in metadata.get("years", []) else "mismatch"
            except (ValueError, TypeError):
                checks[key] = "mismatch"
        elif key == "authors":
            given = given if isinstance(given, list) else [given]
            actual = actual if isinstance(actual, list) else [actual]
            checks[key] = "match" if list(map(norm, given)) == list(map(norm, actual)) else "review_author_names_or_order"
            # Clearly absent family names are a mismatch; abbreviations need human review.
            for name in given:
                tokens = re.findall(r"[^\W\d_]+", str(name), re.UNICODE)
                if tokens and not any(norm(t) in norm(" ".join(actual)) for t in tokens if len(t) > 1):
                    checks[key] = "mismatch"
        else:
            a, b = norm(given), norm(actual)
            similarity = difflib.SequenceMatcher(None, a, b).ratio()
            checks[key] = "match" if a == b else "review_fuzzy_match" if similarity >= 0.90 else "mismatch"
    values = list(checks.values())
    status = "MISMATCH" if "mismatch" in values else "PARTIAL" if "missing" in values else "REVIEW" if any(v != "match" for v in values) else "VERIFIED_METADATA"
    return {"status": status, "checks": checks, "metadata": metadata, "qualification": "Registry metadata checks only; source text must independently support each scientific claim"}


def verify(client, refs, secondary=False):
    results = []
    for i, ref in enumerate(refs, 1):
        result = {"n": i, "input": ref, "checked_at": now()}
        try:
            if not isinstance(ref, dict):
                raise ValueError("Each reference must be an object")
            if not ref.get("doi"):
                result.update(status="PARTIAL", message="No DOI supplied; resolve candidates before five-element checks")
            else:
                match, errors = lookup_doi(client, ref["doi"])
                result["provider_errors"] = errors
                if not match:
                    result.update(status="UNVERIFIABLE" if errors else "NOT_FOUND", message="No usable registry metadata; do not infer fabrication from a lookup failure")
                else:
                    result.update(compare_reference(ref, match))
                    if secondary:
                        try:
                            secondary_rows = openalex(client, doi=ref["doi"])
                            result["secondary"] = secondary_rows
                            result["secondary_note"] = "OpenAlex may inherit registry metadata; agreement is corroboration, not source independence"
                            if secondary_rows:
                                sec = compare_reference(ref, secondary_rows[0])
                                result["secondary_checks"] = sec["checks"]
                                if sec["status"] == "MISMATCH" and result["status"] == "VERIFIED_METADATA":
                                    result["status"] = "REVIEW"
                        except Exception as e:
                            result["secondary_error"] = getattr(e, "kind", type(e).__name__)
        except Exception as e:
            result.update(status="UNVERIFIABLE", message="Verification failed: " + getattr(e, "kind", type(e).__name__))
        results.append(result)
    return results


def report(results):
    def cell(s):
        return str(s or "").replace("|", "\\|").replace("\n", " ")
    lines = ["# 参考文献五要素核验", "", "本报告核对登记元数据；引用是否支持正文论断仍需阅读原文。", "", "| 序号 | 状态 | 标题 | DOI | 五要素检查 |", "|---|---|---|---|---|"]
    for r in results:
        ref = r.get("input") if isinstance(r.get("input"), dict) else {"title": str(r.get("input") or "")}
        lines.append(f"| {r['n']} | {r['status']} | {cell(ref.get('title'))} | {cell(ref.get('doi'))} | {cell(json.dumps(r.get('checks', {}), ensure_ascii=False))} |")
    lines += ["", "VERIFIED_METADATA：五项登记字段一致；PARTIAL：字段缺失；REVIEW：缩写/模糊或版本差异待核；MISMATCH：已发现字段冲突；UNVERIFIABLE：网络/接口/输入使核验无法完成；NOT_FOUND：本轮未获取记录，不等于虚构。", "", "逐条元数据和接口状态见同名 JSON 输出。"]
    return "\n".join(lines) + "\n"
