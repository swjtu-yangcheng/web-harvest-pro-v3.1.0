#!/usr/bin/env python3
"""Offline regression suite using a disposable local server; never contact real sites."""
import concurrent.futures as cf
import contextlib
import dataclasses
import gzip
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse as up
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from core import Client, Config, HarvestError, Robots, digest, guard, normalize_url, retry_after, safe_url
from collect import batch, crawl, discover, fetch, public_url
from extract import decode, extract
from academic import compare_reference, doi_norm, lookup_doi, report, verify, arxiv
from research import fuse, parse_ddg, plan

SCRIPT = Path(__file__).with_name("harvest.py")


class Handler(BaseHTTPRequestHandler):
    hits, starts, lock = {}, [], threading.Lock()
    def log_message(self, *_):
        pass
    def do_GET(self):
        path = up.urlsplit(self.path).path
        with self.lock:
            self.hits[path] = self.hits.get(path, 0) + 1
            count = self.hits[path]
            self.starts.append((path, time.monotonic()))
        code, typ, data, headers = 200, "text/html; charset=utf-8", b"", {}
        if path == "/robots.txt":
            typ, data = "text/plain", b"User-agent: *\nDisallow: /blocked\nDisallow: /secret*\nAllow: /secret/public$\n"
        elif path == "/blocked":
            data = b"must never be fetched"
        elif path == "/redirect":
            code, headers = 302, {"Location": "/short"}
        elif path == "/cross-redirect":
            code, headers = 302, {"Location": self.server.base.replace("127.0.0.1", "localhost") + "/short"}
        elif path == "/redirect-blocked":
            code, headers = 302, {"Location": "/blocked"}
        elif path == "/loop":
            code, headers = 302, {"Location": "/loop"}
        elif path == "/rate" and count == 1:
            code, headers = 429, {"Retry-After": "1"}
        elif path == "/always-rate":
            code, headers = 429, {"Retry-After": "120"}
        elif path == "/missing":
            code = 404
        elif path == "/login":
            code = 403
        elif path == "/etag":
            headers = {"ETag": '"v1"'}
            if self.headers.get("If-None-Match") == '"v1"':
                code = 304
            data = b"<main>Stable cached body</main>"
        elif path == "/gb":
            typ, data = "text/html; charset=gb18030", "<main>镁基水泥碳化与结构行为</main>".encode("gb18030")
        elif path == "/gzip":
            data = gzip.compress(b"<main>compressed</main>")
            headers = {"Content-Encoding": "gzip"}
        elif path == "/gzip-bomb":
            data, headers = gzip.compress(b"x" * 10000), {"Content-Encoding": "gzip"}
        elif path == "/large":
            data = b"x" * 10000
        elif path == "/json":
            typ, data = "application/json", b'{"value":42}'
        elif path == "/js":
            data = b'<html><body><div id="root"></div><script>loadContent()</script></body></html>'
        elif path == "/challenge":
            data = b'<html><body>Verify you are human<div class="g-recaptcha"></div></body></html>'
        elif path == "/sitemap.xml":
            typ, data = "application/xml", ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>' + self.server.base + '/short</loc></url></urlset>').encode()
        elif path == "/sitemap.xml.gz":
            typ, data = "application/gzip", gzip.compress(('<urlset><url><loc>' + self.server.base + '/short</loc></url></urlset>').encode())
        elif path == "/start":
            data = b'<main><h1>Start</h1><a href="/a">A</a><a href="/b">B</a><a href="/blocked">Denied</a><a href="https://example.net/out">Other site</a></main>'
        elif path == "/a":
            data = b'<main><h1>A</h1><p>First result</p><a href="/deep">Depth extension</a></main>'
        elif path == "/b":
            data = b'<main><h1>B</h1><p>Second result</p></main>'
        else:
            data = b'<html><title>Short</title><main><p>A &amp; B</p></main></html>'
        self.send_response(code)
        self.send_header("Content-Type", typ)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Suite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.server.base = cls.base
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(cache_dir=self.tmp.name + "/cache", allow_private=True, proxy_mode="direct", delay=0.02, retries=0, page_budget=6)
        self.client = Client(self.cfg)
    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()
    def page(self, path, **kwargs):
        return fetch(self.client, self.base + path, **kwargs)[0]
    def test_short_page_success(self):
        r = self.page("/short")
        self.assertTrue(r["ok"])
        self.assertIn("A & B", r["markdown"])
    def test_chinese_encoding(self):
        self.assertIn("镁基水泥", self.page("/gb")["markdown"])
    def test_robots_path_never_requested(self):
        old = Handler.hits.get("/blocked", 0)
        self.assertEqual(self.page("/blocked")["error_kind"], "robots_denied")
        self.assertEqual(Handler.hits.get("/blocked", 0), old)
    def test_redirect_checks_robots(self):
        self.assertEqual(self.page("/redirect-blocked")["error_kind"], "robots_denied")
    def test_redirect_success(self):
        self.assertTrue(self.page("/redirect")["final_url"].endswith("/short"))
    def test_redirect_loop_bounded(self):
        self.assertEqual(self.page("/loop")["error_kind"], "redirect_error")
    def test_status_not_fabricated(self):
        self.assertEqual(self.page("/login", browser=True)["status"], 403)
    def test_404_not_success(self):
        self.assertFalse(self.page("/missing")["ok"])
    def test_http_cache_hit(self):
        self.page("/short")
        self.assertEqual(self.page("/short")["cache"], "hit")
    def test_etag_revalidate(self):
        self.page("/etag")
        r = self.page("/etag", refresh=True)
        self.assertEqual(r["cache"], "revalidated")
        self.assertIn("Stable cached body", r["markdown"])
    def test_gzip(self):
        self.assertIn("compressed", self.page("/gzip")["markdown"])
    def test_compressed_byte_limit(self):
        self.client.cfg.max_bytes = 512
        self.assertEqual(self.page("/gzip-bomb")["error_kind"], "size_limit")
    def test_raw_byte_limit(self):
        self.client.cfg.max_bytes = 512
        self.assertEqual(self.page("/large")["error_kind"], "size_limit")
    def test_retry_after_budget(self):
        self.client.cfg.retries = 1
        self.client.cfg.page_budget = 0.5
        started = time.monotonic()
        self.assertEqual(self.page("/always-rate")["error_kind"], "budget_exhausted")
        self.assertLess(time.monotonic() - started, 1)
    def test_retry_after_success(self):
        Handler.hits["/rate"] = 0
        self.client.cfg.retries = 1
        self.assertTrue(self.page("/rate")["ok"])
        starts = [t for p, t in Handler.starts if p == "/rate"][-2:]
        self.assertGreaterEqual(starts[-1] - starts[-2], 0.98)
    def test_spacing_before_requests(self):
        self.client.cfg.delay = 0.08
        with cf.ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda n: self.client.get(self.base + f"/timing-{n}", purpose="api", cache=False), range(4)))
        times = sorted(t for p, t in Handler.starts if p.startswith("/timing-"))[-4:]
        self.assertTrue(all(b - a >= 0.06 for a, b in zip(times, times[1:])))
    def test_batch_dedup_evidence(self):
        result = batch(self.client, [self.base + "/short", self.base + "/short#x", self.base + "/json"], self.tmp.name + "/batch")
        self.assertEqual(result["stats"]["pages"], 2)
        rows = Path(self.tmp.name + "/batch/records.jsonl").read_text().splitlines()
        self.assertEqual(len(rows), 2)
        self.assertTrue(json.loads(rows[0])["markdown_path"].endswith(".md"))
    def test_crawl_resume(self):
        out = self.tmp.name + "/crawl"
        with contextlib.redirect_stderr(io.StringIO()):
            a = crawl(self.client, [self.base + "/start"], out, max_pages=1)
            b = crawl(self.client, [self.base + "/start"], out, max_pages=4)
        self.assertEqual(a["stats"]["pages"], 1)
        self.assertEqual(b["stats"]["pages"], 4)
        self.assertEqual(b["stats"]["success"], 3)
        self.assertTrue(Path(out + "/state.sqlite3").exists())
    def test_resume_scope_mismatch(self):
        with contextlib.redirect_stderr(io.StringIO()):
            crawl(self.client, [self.base + "/start"], self.tmp.name + "/crawl", max_pages=1)
        with self.assertRaises(ValueError):
            crawl(self.client, [self.base + "/other"], self.tmp.name + "/crawl", max_pages=2)
    def test_sitemap_discovery(self):
        r = discover(self.client, self.base + "/sitemap.xml")
        self.assertEqual(r["urls"], [self.base + "/short"])
    def test_json_structured(self):
        self.assertEqual(self.page("/json")["structured"], [{"value": 42}])
    def test_js_shell_does_not_fake_success(self):
        self.assertEqual(self.page("/js")["error_kind"], "js_required")
    def test_challenge_does_not_escalate(self):
        with patch("collect.subprocess.run", side_effect=AssertionError("Must not launch browser")):
            self.assertEqual(self.page("/challenge", browser=True)["quality"], "challenge")
    def test_invalid_url_recorded(self):
        self.assertFalse(fetch(self.client, "file:///etc/passwd")[0]["ok"])
    def test_private_address_blocked_by_default(self):
        with self.assertRaises(HarvestError):
            guard(self.base)
    def test_url_userinfo_blocked(self):
        with self.assertRaises(ValueError):
            normalize_url("https://user:pass@example.com/")
    def test_secret_url_blocked(self):
        with self.assertRaises(HarvestError):
            public_url("https://example.com/?api_key=secret")
    def test_secret_redaction(self):
        self.assertNotIn("VALUE", safe_url("https://example.com/?api_key=VALUE"))
    def test_normalization_keeps_semantics(self):
        self.assertNotEqual(normalize_url("https://e.com/a"), normalize_url("https://e.com/a/"))
        self.assertTrue(normalize_url("https://e.com/?q=a&q=b&empty=&utm_source=x#frag").endswith("?q=a&q=b&empty="))
    def test_robots_longest_allow(self):
        p = Robots("User-agent: *\nDisallow: /secret*\nAllow: /secret/public$\n")
        self.assertTrue(p.allowed("https://e.com/secret/public"))
        self.assertFalse(p.allowed("https://e.com/secret/public/more"))
    def test_robots_equal_allow_tie(self):
        self.assertTrue(Robots("User-agent: *\nAllow: /a\nDisallow: /a").allowed("https://e.com/a"))
    def test_robots_specific_group(self):
        p = Robots("User-agent: *\nDisallow: /\nUser-agent: WebHarvest\nAllow: /\n")
        self.assertTrue(p.allowed("https://e.com/a"))
    def test_robots_percent_unreserved(self):
        self.assertFalse(Robots("User-agent: *\nDisallow: /a").allowed("https://e.com/%61"))
    def test_retry_after_date(self):
        self.assertGreaterEqual(retry_after("Wed, 21 Oct 2099 07:28:00 GMT"), 1)
    def test_table_and_metadata(self):
        r = extract(b'<html lang="zh"><head><title>T</title><meta property="article:published_time" content="2026-01-02"></head><body><nav>Noise</nav><main><h2>Table</h2><table><tr><th>x</th><th>y</th></tr><tr><td>1</td><td>2</td></tr></table></main></body></html>', "text/html", "https://e.com/")
        self.assertEqual(r["tables"], [[["x", "y"], ["1", "2"]]])
        self.assertIn("| x | y |", r["markdown"])
        self.assertNotIn("Noise", r["markdown"])
        self.assertEqual(r["published_at"], "2026-01-02")
    def test_jsonld(self):
        r = extract(b'<main>Hello</main><script type="application/ld+json">{"@type":"Article"}</script>', "text/html", "https://e.com/")
        self.assertEqual(r["structured"][0]["@type"], "Article")
    def test_cross_origin_canonical_ignored(self):
        r = extract(b'<link rel="canonical" href="https://evil.example/a"><main>Text</main>', "text/html", "https://e.com/")
        self.assertIsNone(r["canonical_url"])
    def test_plain_text_keeps_angle_brackets(self):
        r = extract(b"sigma < 10 and x > 2", "text/plain", "https://e.com/")
        self.assertIn("sigma < 10", r["markdown"])
    def test_feed(self):
        r = extract(b'<rss><channel><item><title>T</title><link>https://e.com/a</link></item></channel></rss>', "application/xml", "https://e.com/")
        self.assertEqual(r["kind"], "feed")
        self.assertEqual(r["structured"][0]["title"], "T")
    def test_xml_entities_rejected(self):
        with self.assertRaises(ValueError):
            extract(b'<!DOCTYPE x [<!ENTITY a "bad">]><rss/>', "application/xml", "https://e.com/")
    def test_ddg_attribute_order_and_snippet_alignment(self):
        s = '<div class="result"><a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fe.com%2Fa" class="result__a extra">A</a><a class="result__snippet">S1</a></div><div class="result"><a href="https://e.com/b" class="result__a">B</a><a class="result__snippet">S2</a></div>'
        r = parse_ddg(s)
        self.assertEqual([x["snippet"] for x in r], ["S1", "S2"])
    def test_search_url_is_not_execution(self):
        self.assertEqual(plan("水泥 carbon")['state'], 'links_only')
    def test_fusion_dedup(self):
        r = fuse([[{"url": "https://e.com/a?utm_source=x", "title": "A"}], [{"url": "https://e.com/a", "title": "A"}]])
        self.assertEqual(len(r), 1)
        self.assertEqual(len(r[0]["retrievals"]), 2)
    def test_doi_url_normalization(self):
        self.assertEqual(doi_norm("https://doi.org/10.1234/ABC(1)"), "10.1234/abc(1)")
    def metadata(self):
        return {"title": "Carbonation mechanisms", "authors": ["Jane Smith"], "journal": "Cement Research", "year": 2025, "years": [2024, 2025], "doi": "10.1234/test"}
    def test_five_fields_verified(self):
        self.assertEqual(compare_reference(self.metadata(), self.metadata())["status"], "VERIFIED_METADATA")
    def test_doi_only_not_full_verification(self):
        self.assertEqual(compare_reference({"doi": "10.1234/test"}, self.metadata())["status"], "PARTIAL")
    def test_wrong_author_detected(self):
        r = self.metadata()
        r["authors"] = ["Robert Jones"]
        self.assertEqual(compare_reference(r, self.metadata())["status"], "MISMATCH")
    def test_wrong_journal_detected(self):
        r = self.metadata()
        r["journal"] = "Made Up Journal"
        self.assertEqual(compare_reference(r, self.metadata())["status"], "MISMATCH")
    def test_wrong_year_detected(self):
        r = self.metadata()
        r["year"] = 1900
        self.assertEqual(compare_reference(r, self.metadata())["status"], "MISMATCH")
    def test_online_print_year_review(self):
        r = self.metadata()
        r["year"] = 2024
        self.assertEqual(compare_reference(r, self.metadata())["status"], "REVIEW")
    def test_title_fuzzy_not_verified(self):
        r = self.metadata()
        r["title"] += "s"
        self.assertEqual(compare_reference(r, self.metadata())["status"], "REVIEW")
    def test_network_failure_not_hallucination(self):
        with patch("academic.lookup_doi", return_value=(None, [{"provider": "crossref", "error_kind": "timeout"}])):
            self.assertEqual(verify(self.client, [self.metadata()])[0]["status"], "UNVERIFIABLE")
    def test_crossref_404_datacite_fallback(self):
        with patch("academic.get_json", side_effect=[None, {"data": {"attributes": {"titles": [{"title": "Dataset"}], "creators": [{"name": "Smith"}], "publicationYear": 2025, "doi": "10.1234/test"}}}]):
            m, _ = lookup_doi(self.client, "10.1234/test")
            self.assertEqual(m["source"], "datacite")
    def test_markdown_report_escape(self):
        s = report([{"n": 1, "status": "PARTIAL", "input": {"title": "A|B", "doi": "10.1234/test"}}])
        self.assertIn("A\\|B", s)
    def test_arxiv_correct_query_param(self):
        response = {"status": 200, "body": b'<feed xmlns="http://www.w3.org/2005/Atom"/>'}
        with patch.object(self.client, "get", return_value=response) as get:
            arxiv(self.client, "cement")
            self.assertIn("search_query=", get.call_args.args[0])
            self.assertNotIn("?query=", get.call_args.args[0])
    def test_cli_json_both_positions(self):
        for args in [["--json", "search", "碳化"], ["search", "碳化", "--json"]]:
            r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout)["result"]["state"], "links_only")
    def test_cli_invalid_argument_json(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "fetch", "https://e.com", "--timeout", "-1"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertEqual(json.loads(r.stdout)["error_kind"], "argument_error")
    def test_pdf_missing_or_ocr(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf optional")
        writer, buf = PdfWriter(), io.BytesIO()
        writer.add_blank_page(width=72, height=72)
        writer.write(buf)
        self.assertEqual(extract(buf.getvalue(), "application/pdf", "https://e.com/a.pdf")["quality"], "ocr_required")
    def test_429_cooldown_after_last_attempt(self):
        r = self.client.get(self.base + "/always-rate", purpose="api")
        self.assertEqual(r["status"], 429)
        self.client.cfg.page_budget = 0.1
        with self.assertRaises(HarvestError) as caught:
            self.client.get(self.base + "/another-api", purpose="api")
        self.assertEqual(caught.exception.kind, "budget_exhausted")
    def test_cached_redirect_scope(self):
        self.page("/short")
        old = self.client.cache.get(digest(self.base + "/short"))
        self.client.cache.put(digest(self.base + "/short"), {"headers": old["headers"], "body": old["body"], "final_url": "http://other.example/"})
        with self.assertRaises(HarvestError) as caught:
            self.client.get(self.base + "/short", allowed_hosts={up.urlsplit(self.base).netloc})
        self.assertEqual(caught.exception.kind, "out_of_scope")
    def test_resume_raise_depth(self):
        out = self.tmp.name + "/deeper"
        with contextlib.redirect_stderr(io.StringIO()):
            crawl(self.client, [self.base + "/start"], out, max_pages=4, max_depth=1)
            r = crawl(self.client, [self.base + "/start"], out, max_pages=8, max_depth=2)
        self.assertEqual(r["stats"]["pages"], 5)
    def test_compressed_sitemap(self):
        self.assertEqual(discover(self.client, self.base + "/sitemap.xml.gz")["urls"], [self.base + "/short"])
    def test_discovery_failure_is_recorded(self):
        result = discover(self.client, self.base + "/missing")
        self.assertEqual(result["errors"][0]["status"], 404)
    def test_robot_server_error_postponed(self):
        with patch.object(self.client, "get", return_value={"status": 503, "body": b"", "headers": {}}):
            with self.assertRaises(HarvestError) as caught:
                self.client.check_robots(self.base + "/short", time.monotonic() + 1)
            self.assertEqual(caught.exception.kind, "robots_unavailable")
    def test_robot_404_allows_public_site(self):
        with patch.object(self.client, "get", return_value={"status": 404, "body": b"", "headers": {}}):
            self.client.check_robots(self.base + "/short", time.monotonic() + 1)
    def test_nonfinite_cli_timeout_rejected(self):
        r = subprocess.run([sys.executable, str(SCRIPT), "fetch", "https://e.com", "--timeout", "nan"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
    def test_invalid_reference_cli_still_json(self):
        path = Path(self.tmp.name) / "badrefs.json"
        path.write_text('["not an object"]')
        r = subprocess.run([sys.executable, str(SCRIPT), "verify", str(path), "-o", self.tmp.name + "/bad.md", "--cache-dir", self.tmp.name + "/v-cache"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stdout)["result"]["references"][0]["status"], "UNVERIFIABLE")
    def test_cross_origin_redirect_drops_auth(self):
        with patch("core.ur.Request", wraps=__import__('urllib.request',fromlist=['Request']).Request) as request:
            self.client.get(self.base + "/cross-redirect", headers={"Authorization": "Bearer opaque"}, purpose="api")
            self.assertEqual(len(request.call_args_list), 2)
            self.assertNotIn("Authorization", request.call_args_list[1].kwargs["headers"])
        self.assertIsNone(self.client.cache.get(digest(self.base + "/cross-redirect")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
