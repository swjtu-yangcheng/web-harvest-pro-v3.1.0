"""Explicit search providers, offline URL planning, and reciprocal rank fusion."""
from __future__ import annotations

import json
import os
import re
import urllib.parse as up
from collections import defaultdict

from core import HarvestError, normalize_url
from extract import DOM, compact

ENGINES = {
    "baidu": "https://www.baidu.com/s?wd={q}",
    "bing-cn": "https://cn.bing.com/search?q={q}&ensearch=0",
    "bing": "https://www.bing.com/search?q={q}",
    "google": "https://www.google.com/search?q={q}",
    "ddg": "https://html.duckduckgo.com/html/?q={q}",
    "brave": "https://search.brave.com/search?q={q}",
    "sogou": "https://sogou.com/web?query={q}",
    "weixin": "https://wx.sogou.com/weixin?type=2&query={q}",
}


def plan(query, site=None, filetype=None, exact=False):
    q = f'"{query}"' if exact else query
    if site:
        q = f"site:{site} {q}"
    if filetype:
        q += f" filetype:{filetype}"
    keys = ["baidu", "bing-cn", "sogou", "weixin", "ddg", "google", "brave"] if re.search(r"[\u4e00-\u9fff]", query) else ["ddg", "google", "bing", "brave"]
    return {"query": q, "state": "links_only", "links": [{"engine": k, "url": ENGINES[k].format(q=up.quote_plus(q)), "capability": "search_url_only"} for k in keys], "next": "Use the host's actual search tool or an explicitly configured provider; URL generation is not a search execution"}


def parse_ddg(text, limit=10):
    nodes = list(DOM(text).root.walk())
    rows, seen = [], set()
    # A result container owns its snippet; never zip snippets after deduplication.
    blocks = [n for n in nodes if n.tag == "div" and "result" in n.attrs.get("class", "").split()]
    if not blocks:
        blocks = [n for n in nodes if n.tag == "a" and "result__a" in n.attrs.get("class", "").split()]
    for block in blocks:
        owned = list(block.walk())
        anchor = next((n for n in owned if n.tag == "a" and "result__a" in n.attrs.get("class", "").split()), None)
        if anchor is None:
            continue
        url = up.urljoin("https://html.duckduckgo.com", anchor.attrs.get("href", ""))
        wrapped = dict(up.parse_qsl(up.urlsplit(url).query)).get("uddg")
        if wrapped:
            url = wrapped
        try:
            url = normalize_url(url)
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        snippet = next((compact(n.text()) for n in owned if "result__snippet" in n.attrs.get("class", "").split()), "")
        rows.append({"url": url, "title": compact(anchor.text()), "snippet": snippet[:1000]})
        if len(rows) >= limit:
            break
    return rows


def search(client, query, provider="links", limit=10, site=None, filetype=None, exact=False):
    prepared = plan(query, site, filetype, exact)
    if provider == "links":
        return prepared
    q = prepared["query"]
    if provider == "brave":
        key = os.environ.get("BRAVE_SEARCH_API_KEY")
        if not key:
            raise HarvestError("provider_key_missing", "Set BRAVE_SEARCH_API_KEY in the secret environment")
        url = "https://api.search.brave.com/res/v1/web/search?" + up.urlencode({"q": q, "count": min(limit, 20)})
        res = client.get(url, headers={"X-Subscription-Token": key, "Accept": "application/json"}, purpose="api")
        if res["status"] != 200:
            raise HarvestError("provider_error", f"Brave HTTP {res['status']}", res["status"])
        rows = [{"url": h["url"], "title": h.get("title", ""), "snippet": h.get("description", "")} for h in json.loads(res["body"]).get("web", {}).get("results", [])]
    elif provider == "searxng":
        base = os.environ.get("SEARXNG_URL", "")
        if not base:
            raise HarvestError("provider_config_missing", "Set SEARXNG_URL for a permitted JSON endpoint")
        url = base.rstrip("/") + "/search?" + up.urlencode({"q": q, "format": "json"})
        res = client.get(url, purpose="api")
        if res["status"] != 200:
            raise HarvestError("provider_error", f"SearXNG HTTP {res['status']}", res["status"])
        rows = [{"url": h["url"], "title": h.get("title", ""), "snippet": h.get("content", "")} for h in json.loads(res["body"]).get("results", [])]
    elif provider == "ddg":
        from extract import decode
        url = ENGINES["ddg"].format(q=up.quote_plus(q))
        res = client.get(url)
        if res["status"] != 200:
            raise HarvestError("provider_error", f"DDG HTTP {res['status']}", res["status"])
        rows = parse_ddg(decode(res["body"], res["headers"].get("content-type", ""))[0], limit)
    else:
        raise ValueError("Unsupported search provider")
    if not rows:
        raise HarvestError("search_empty_or_layout_changed", "No parsed search results; this does not prove no relevant sources exist")
    return {"query": q, "provider": provider, "state": "searched", "results": rows[:limit], "evidence_state": "discovery_only; open original sources before citing"}


def fuse(runs):
    grouped = {}
    for i, run in enumerate(runs):
        rows = run if isinstance(run, list) else run.get("results", [])
        provider = str(i) if isinstance(run, list) else run.get("provider", str(i))
        seen = set()
        for rank, hit in enumerate(rows, 1):
            try:
                key = normalize_url(hit.get("canonical_url") or hit["url"])
            except (KeyError, ValueError):
                continue
            if key in seen:
                continue
            seen.add(key)
            r = grouped.setdefault(key, {**hit, "url": key, "rrf_score": 0, "retrievals": []})
            r["rrf_score"] += 1 / (60 + rank)
            r["retrievals"].append({"provider": provider, "rank": rank, "run": i})
    return sorted(grouped.values(), key=lambda h: (-h["rrf_score"], h["url"]))
