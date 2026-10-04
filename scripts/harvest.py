#!/usr/bin/env python3
"""Web Harvest Pro: portable search, extraction, bounded crawl and evidence checks."""
from __future__ import annotations

import argparse
import dataclasses
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from core import Client, Config, HarvestError, VERSION, now, safe_url
from collect import batch, crawl, discover, export_run, fetch, write_evidence
import academic
import research


def positive(value):
    x = float(value)
    if not math.isfinite(x) or x <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return x


def integer(value):
    x = int(value)
    if x <= 0:
        raise argparse.ArgumentTypeError("Must be a positive integer")
    return x


def nonnegative(value):
    x = int(value)
    if x < 0:
        raise argparse.ArgumentTypeError("Must be nonnegative")
    return x


class Parser(argparse.ArgumentParser):
    def error(self, message):
        print(json.dumps({"ok": False, "version": VERSION, "error_kind": "argument_error", "message": message}, ensure_ascii=False))
        raise SystemExit(2)


def doctor(args, cfg):
    packages = {}
    for mod, dist in [("trafilatura", "trafilatura"), ("bs4", "beautifulsoup4"), ("pypdf", "pypdf"), ("playwright", "playwright"), ("crawl4ai", "crawl4ai")]:
        present = importlib.util.find_spec(mod) is not None
        try:
            version = importlib.metadata.version(dist) if present else None
        except importlib.metadata.PackageNotFoundError:
            version = "available-version-unknown"
        packages[mod] = {"installed": present, "version": version}
    result = {"python": platform.python_version(), "interpreter": sys.executable, "platform": platform.system(), "stdlib_core": sys.version_info >= (3, 10), "packages": packages, "executables": {k: bool(shutil.which(k)) for k in ["agent-reach", "mcporter", "yt-dlp", "node", "pdftotext", "tesseract"]}, "secret_presence": {k: bool(os.environ.get(k)) for k in ["OPENALEX_API_KEY", "BRAVE_SEARCH_API_KEY", "FIRECRAWL_API_KEY", "CROSSREF_MAILTO", "SEARXNG_URL"]}, "proxy_mode": cfg.proxy_mode, "note": "Package presence is not browser readiness or endpoint reachability; native host tools must be inspected by the agent"}
    if args.network:
        probes = args.probe or ["https://api.crossref.org/works?rows=1", "https://api.openalex.org/works?per_page=1"]
        result["probes"] = []
        modes = [cfg.proxy_mode]
        if args.compare_routes and "direct" not in modes:
            modes.append("direct")
        for mode in modes:
            client = Client(dataclasses.replace(cfg, proxy_mode=mode, retries=0, page_budget=min(cfg.page_budget, 15)))
            try:
                for url in probes:
                    started = time.monotonic()
                    row = {"url": safe_url(url), "route": mode}
                    try:
                        r = client.get(url, purpose="api", cache=False)
                        row.update(status=r["status"], ok=200 <= r["status"] < 300)
                    except Exception as e:
                        row.update(ok=False, error_kind=getattr(e, "kind", type(e).__name__))
                    row["elapsed_ms"] = round((time.monotonic() - started) * 1000)
                    result["probes"].append(row)
            finally:
                client.close()
    return result


def parser():
    p = Parser(description=__doc__)
    # Shared options belong to each command; --json is a no-op accepted anywhere.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--timeout", type=positive, default=15)
    common.add_argument("--page-budget", type=positive, default=45)
    common.add_argument("--retries", type=nonnegative, default=2)
    common.add_argument("--max-bytes", type=integer, default=8 * 1024 * 1024)
    common.add_argument("--delay", type=float, default=0.8)
    common.add_argument("--per-host", type=integer, default=2)
    common.add_argument("--proxy-mode", choices=["env", "direct", "explicit"], default=os.environ.get("HARVEST_PROXY_MODE", "env"))
    common.add_argument("--allow-private", action="store_true", help="Only for explicitly scoped private sites or local tests")
    common.add_argument("--cache-dir", default=os.environ.get("HARVEST_CACHE_DIR", ".web-harvest-cache"))
    common.add_argument("--cache-ttl", type=float, default=3600)
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("doctor", parents=[common])
    d.add_argument("--network", action="store_true")
    d.add_argument("--probe", action="append")
    d.add_argument("--compare-routes", action="store_true", help="Explicitly test direct route as well")
    for name in ["fetch", "quick", "md", "render"]:
        s = sub.add_parser(name, parents=[common])
        s.add_argument("url")
        s.add_argument("--browser", action="store_true")
        s.add_argument("--no-browser", action="store_true")
        s.add_argument("--selector")
        s.add_argument("--screenshot")
        s.add_argument("--output", "-o")
        s.add_argument("--raw", action="store_true")
        s.add_argument("--refresh", action="store_true")
    b = sub.add_parser("batch", parents=[common])
    b.add_argument("file")
    b.add_argument("--output", "-o", required=True)
    b.add_argument("--concurrency", type=integer, default=4)
    b.add_argument("--raw", action="store_true")
    b.add_argument("--refresh", action="store_true")
    c = sub.add_parser("crawl", parents=[common])
    c.add_argument("--seed", action="append", required=True)
    c.add_argument("--output", "-o", required=True)
    c.add_argument("--max-pages", type=integer, default=30)
    c.add_argument("--max-depth", type=nonnegative, default=2)
    c.add_argument("--include")
    c.add_argument("--exclude")
    c.add_argument("--retry-failed", action="store_true")
    c.add_argument("--raw", action="store_true")
    ds = sub.add_parser("discover", parents=[common])
    ds.add_argument("url")
    ds.add_argument("--limit", type=integer, default=200)
    ds.add_argument("--max-maps", type=integer, default=10)
    s = sub.add_parser("search", parents=[common])
    s.add_argument("query")
    s.add_argument("--provider", choices=["links", "ddg", "brave", "searxng"], default="links")
    s.add_argument("--fetch", action="store_true", help="Compatibility: use DDG when provider is links")
    s.add_argument("--limit", type=integer, default=10)
    s.add_argument("--site")
    s.add_argument("--filetype")
    s.add_argument("--exact", action="store_true")
    f = sub.add_parser("fuse", parents=[common])
    f.add_argument("file", help="JSON array of search runs")
    pp = sub.add_parser("paper", parents=[common])
    pp.add_argument("query")
    pp.add_argument("--provider", choices=["crossref", "openalex", "arxiv"], default="crossref")
    pp.add_argument("--limit", type=integer, default=5)
    v = sub.add_parser("verify", parents=[common])
    v.add_argument("file", help="UTF-8 JSON array with title/authors/journal/year/doi")
    v.add_argument("--secondary", action="store_true")
    v.add_argument("--output", "-o", required=True, help="Markdown report; a companion JSON is written")
    sub.add_parser("selftest")
    return p


def main(argv=None):
    if sys.version_info < (3, 10):
        print('{"ok":false,"error_kind":"python_version","message":"Python 3.10+ required"}')
        return 2
    for stream in [sys.stdout, sys.stderr]:
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parser().parse_args([x for x in (argv if argv is not None else sys.argv[1:]) if x != "--json"])
    client = None
    try:
        if args.command == "selftest":
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("selftest.py"))], capture_output=True, text=True)
            print(json.dumps({"ok": result.returncode == 0, "version": VERSION, "result": {"log": result.stdout + result.stderr}}, ensure_ascii=False))
            return result.returncode
        if not math.isfinite(args.delay) or not math.isfinite(args.cache_ttl) or args.delay < 0 or args.cache_ttl < 0 or args.per_host > 16:
            raise ValueError("delay/cache-ttl must be nonnegative; per-host must be <=16")
        cfg = Config(timeout=args.timeout, page_budget=args.page_budget, retries=args.retries, max_bytes=args.max_bytes, delay=args.delay, per_host=args.per_host, proxy_mode=args.proxy_mode, proxy=os.environ.get("HARVEST_PROXY", ""), allow_private=args.allow_private, cache_dir=args.cache_dir, cache_ttl=args.cache_ttl)
        if args.command == "doctor":
            data = doctor(args, cfg)
            success = data["stdlib_core"]
        elif args.command == "search" and not args.fetch and args.provider == "links":
            data, success = research.plan(args.query, args.site, args.filetype, args.exact), True
        elif args.command == "fuse":
            data = {"results": research.fuse(json.loads(Path(args.file).read_text(encoding="utf-8"))), "method": "RRF k=60", "note": "Retrieval rank is not evidence quality or source independence"}
            success = True
        else:
            client = Client(cfg)
            if args.command in {"fetch", "quick", "md", "render"}:
                if args.no_browser and (args.browser or args.command == "render" or args.screenshot):
                    raise ValueError("Conflicting browser flags")
                if args.screenshot:
                    Path(args.screenshot).parent.mkdir(parents=True, exist_ok=True)
                data, raw = fetch(client, args.url, browser=(args.browser or args.command == "render" or bool(args.screenshot)) and not args.no_browser, selector=args.selector, refresh=args.refresh, screenshot=args.screenshot, force_browser=args.command == "render")
                success = data["ok"]
                if args.output:
                    write_evidence(args.output, data, raw, args.raw)
                    export_run(args.output, [data], {"mode": "fetch", "network_requests": client.count})
            elif args.command == "batch":
                urls = [s.strip() for s in Path(args.file).read_text(encoding="utf-8").splitlines() if s.strip() and not s.strip().startswith("#")]
                data = batch(client, urls, args.output, min(args.concurrency, 32), args.raw, args.refresh)
                success = data["stats"]["success"] == data["stats"]["pages"]
            elif args.command == "crawl":
                data = crawl(client, args.seed, args.output, args.max_pages, args.max_depth, args.include, args.exclude, args.retry_failed, args.raw)
                success = data["stats"]["success"] == data["stats"]["pages"]
            elif args.command == "discover":
                data = discover(client, args.url, args.limit, args.max_maps)
                success = bool(data["urls"])
            elif args.command == "search":
                data = research.search(client, args.query, "ddg" if args.fetch and args.provider == "links" else args.provider, args.limit, args.site, args.filetype, args.exact)
                success = bool(data.get("results"))
            elif args.command == "paper":
                data = academic.paper(client, args.query, args.provider, args.limit)
                success = bool(data["matches"])
            elif args.command == "verify":
                refs = json.loads(Path(args.file).read_text(encoding="utf-8"))
                if not isinstance(refs, list):
                    raise ValueError("Reference file must contain an array")
                results = academic.verify(client, refs, args.secondary)
                out = Path(args.output)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(academic.report(results), encoding="utf-8")
                out.with_suffix(".json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
                data = {"references": results, "report": str(out.resolve()), "json_report": str(out.with_suffix('.json').resolve())}
                success = all(r["status"] == "VERIFIED_METADATA" for r in results) and bool(results)
            else:
                raise ValueError("Unknown command")
        print(json.dumps({"ok": success, "version": VERSION, "result": data}, ensure_ascii=False, indent=2))
        return 0 if success else 1
    except Exception as e:
        print(json.dumps({"ok": False, "version": VERSION, "error_kind": getattr(e, "kind", "input_or_environment_error"), "message": str(e) if isinstance(e, (HarvestError, ValueError)) else "Input/configuration failed: " + type(e).__name__}, ensure_ascii=False))
        return 2
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
