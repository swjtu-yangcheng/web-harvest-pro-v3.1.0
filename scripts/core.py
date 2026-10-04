"""Bounded HTTP, explicit network policy, robots, rate limits and SQLite cache."""
from __future__ import annotations

import dataclasses
import datetime as dt
import email.utils
import gzip
import hashlib
import ipaddress
import io
import json
import os
import random
import re
import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.parse as up
import urllib.request as ur
from pathlib import Path

VERSION = "3.1.0"
UA = "WebHarvest/3.0 (+public information research)"
SECRET_KEYS = re.compile(r"^(api[_-]?key|access[_-]?token|token|key|password|secret|auth|signature|sig|session|sid|credential)$", re.I)
TRACKING = {"fbclid", "gclid", "msclkid", "spm", "igshid"}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode("utf-8")).hexdigest()


def normalize_url(url):
    """Keep semantic query order, blank values, and trailing slash; strip tracking only."""
    p = up.urlsplit(url.strip())
    if p.scheme.lower() not in {"http", "https"} or not p.hostname:
        raise ValueError("URL must be absolute HTTP(S)")
    if p.username or p.password:
        raise ValueError("URL userinfo is not allowed; use the connector secret store")
    host = p.hostname.encode("idna").decode("ascii").lower()
    host = f"[{host}]" if ":" in host else host
    port = p.port
    netloc = host + (f":{port}" if port and port != (443 if p.scheme.lower() == "https" else 80) else "")
    # Do not decode/re-encode a signed URL's query string.
    parts = [v for v in p.query.split("&") if v and not up.unquote_plus(v.split("=", 1)[0]).lower().startswith("utm_") and up.unquote_plus(v.split("=", 1)[0]).lower() not in TRACKING]
    path = up.quote(p.path or "/", safe="/%:@!$&'()*+,;=-._~")
    return up.urlunsplit((p.scheme.lower(), netloc, path, "&".join(parts), ""))


def safe_url(url):
    try:
        p = up.urlsplit(url)
        host = p.hostname or ""
        host = f"[{host}]" if ":" in host else host
        if p.port:
            host += f":{p.port}"
        query = "&".join(k + "=[REDACTED]" if SECRET_KEYS.match(up.unquote_plus(k)) or k.lower().startswith("x-amz-") else part for part in p.query.split("&") for k in [part.split("=", 1)[0]] if part)
        return up.urlunsplit((p.scheme, host, p.path, query, ""))
    except (ValueError, TypeError):
        return "[invalid URL]"


class HarvestError(Exception):
    def __init__(self, kind, message, status=0):
        super().__init__(message)
        self.kind, self.status = kind, status


@dataclasses.dataclass
class Config:
    timeout: float = 15
    page_budget: float = 45
    retries: int = 2
    max_bytes: int = 8 * 1024 * 1024
    delay: float = 0.8
    per_host: int = 2
    proxy_mode: str = "env"  # env | direct | explicit
    proxy: str = ""
    allow_private: bool = False
    cache_dir: str = ".web-harvest-cache"
    cache_ttl: float = 3600


def guard(url, allow_private=False):
    url = normalize_url(url)
    p = up.urlsplit(url)
    if not allow_private:
        try:
            addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
        except socket.gaierror:
            raise HarvestError("dns_error", "DNS lookup failed") from None
        if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise HarvestError("private_address", "Non-public target blocked; private targets require explicit scope")
    return url


def retry_after(value):
    try:
        return max(0, float(value))
    except (ValueError, TypeError):
        try:
            return max(0, email.utils.parsedate_to_datetime(value).timestamp() - time.time())
        except (ValueError, TypeError, OverflowError):
            return 0


class HostGate:
    def __init__(self, cfg):
        self.cfg, self.lock, self.states = cfg, threading.RLock(), {}

    def state(self, origin):
        with self.lock:
            return self.states.setdefault(origin, {"sem": threading.BoundedSemaphore(self.cfg.per_host), "next": 0, "delay": self.cfg.delay})

    def enter(self, origin, deadline):
        state = self.state(origin)
        if not state["sem"].acquire(timeout=max(0, deadline - time.monotonic())):
            raise HarvestError("budget_exhausted", "Per-host queue time exceeded page budget")
        try:
            while True:
                with self.lock:
                    wait = state["next"] - time.monotonic()
                    if wait <= 0:
                        state["next"] = time.monotonic() + state["delay"]
                        return
                if time.monotonic() + wait >= deadline:
                    raise HarvestError("budget_exhausted", "Host backoff exceeds page budget")
                time.sleep(min(wait, 0.2))
        except BaseException:
            state["sem"].release()
            raise

    def exit(self, origin):
        self.state(origin)["sem"].release()

    def slow(self, origin, seconds):
        with self.lock:
            s = self.state(origin)
            s["next"] = max(s["next"], time.monotonic() + seconds)


def origin(url):
    p = up.urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


class NoRedirect(ur.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Cache:
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(Path(directory) / "http.sqlite3"), check_same_thread=False)
        self.lock = threading.RLock()
        self.db.execute("CREATE TABLE IF NOT EXISTS response (key TEXT PRIMARY KEY, saved REAL, headers TEXT, body BLOB, final TEXT)")
        self.db.commit()

    def get(self, key):
        with self.lock:
            row = self.db.execute("SELECT saved,headers,body,final FROM response WHERE key=?", (key,)).fetchone()
        return {"saved": row[0], "headers": json.loads(row[1]), "body": row[2], "final": row[3]} if row else None

    def put(self, key, response):
        headers = {k: v for k, v in response["headers"].items() if k in {"content-type", "etag", "last-modified", "date"}}
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO response VALUES (?,?,?,?,?)", (key, time.time(), json.dumps(headers), response["body"], response["final_url"]))
            self.db.commit()

    def close(self):
        self.db.close()


class Robots:
    """Longest match wins, '*' and terminal '$'; support crawl-delay as an extension."""
    def __init__(self, text):
        groups, agents, rules, delay = [], [], [], 0.0
        seen_rules = False
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, val = [v.strip() for v in line.split(":", 1)]
            key = key.lower()
            if key == "user-agent":
                if seen_rules:
                    groups.append((agents, rules, delay))
                    agents, rules, delay, seen_rules = [], [], 0.0, False
                agents.append(val.lower())
            elif agents and key in {"allow", "disallow"}:
                seen_rules = True
                if val:
                    rules.append((key == "allow", val))
            elif agents and key == "crawl-delay":
                seen_rules = True
                try:
                    delay = max(delay, float(val))
                except ValueError:
                    pass
        if agents:
            groups.append((agents, rules, delay))
        matching = [(a, r, d) for a, r, d in groups if any(v != "*" and v in "webharvest" for v in a)]
        chosen = matching or [(a, r, d) for a, r, d in groups if "*" in a]
        self.rules = [v for _, r, _ in chosen for v in r]
        self.delay = max([d for _, _, d in chosen] or [0])

    @staticmethod
    def comparable(path):
        # Decode percent-encoded unreserved bytes; keep reserved bytes encoded.
        def replace(m):
            c = chr(int(m.group(1), 16))
            return c if c.isascii() and (c.isalnum() or c in "-._~") else "%" + m.group(1).upper()
        return re.sub(r"%([0-9a-fA-F]{2})", replace, up.quote(path, safe="/%?&=:@!$'()*+,;~-._"))

    def allowed(self, url):
        p = up.urlsplit(url)
        path = self.comparable(p.path + ("?" + p.query if p.query else ""))
        matched = []
        for allow, rule in self.rules:
            rule = self.comparable(rule)
            terminal = rule.endswith("$")
            pattern = re.escape(rule[:-1] if terminal else rule).replace(r"\*", ".*")
            if re.match("^" + pattern + ("$" if terminal else ""), path):
                matched.append((len(rule.replace("*", "").rstrip("$")), allow))
        return max(matched, default=(0, True))[1]


class Client:
    def __init__(self, cfg=None):
        self.cfg = cfg or Config()
        self.gate, self.cache = HostGate(self.cfg), Cache(self.cfg.cache_dir)
        self.robots, self.robots_lock, self.robots_locks = {}, threading.RLock(), {}
        self.count, self.count_lock = 0, threading.Lock()
        if self.cfg.proxy_mode == "direct":
            proxy = ur.ProxyHandler({})
        elif self.cfg.proxy_mode == "explicit":
            p = up.urlsplit(self.cfg.proxy)
            if p.scheme not in {"http", "https"} or not p.hostname:
                raise HarvestError("proxy_config", "Explicit proxy requires an HTTP(S) proxy URL")
            proxy = ur.ProxyHandler({"http": self.cfg.proxy, "https": self.cfg.proxy})
        else:
            proxy = ur.ProxyHandler()
        self.opener = ur.build_opener(proxy, NoRedirect())

    def close(self):
        self.cache.close()

    def check_robots(self, url, deadline):
        base = origin(url)
        with self.robots_lock:
            lock = self.robots_locks.setdefault(base, threading.RLock())
        if not lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise HarvestError("budget_exhausted", "robots policy queue exceeded page budget")
        try:
            saved = self.robots.get(base)
            if saved and time.monotonic() - saved[0] < 3600:
                policy = saved[1]
            else:
                res = self.get(base + "/robots.txt", purpose="control", deadline=deadline, cache=False)
                code = res["status"]
                if code in {404, 410}:
                    policy = Robots("")
                elif code in {401, 403}:
                    raise HarvestError("robots_denied", "robots.txt access denied", code)
                elif not 200 <= code < 300:
                    raise HarvestError("robots_unavailable", "robots.txt unavailable; postpone this site", code)
                else:
                    from extract import decode
                    policy = Robots(decode(res["body"], res["headers"].get("content-type", ""))[0])
                self.robots[base] = (time.monotonic(), policy)
                self.gate.state(base)["delay"] = max(self.cfg.delay, policy.delay)
        finally:
            lock.release()
        if not policy.allowed(url):
            raise HarvestError("robots_denied", "robots.txt disallows this path")

    def get(self, url, headers=None, purpose="page", deadline=None, cache=True, refresh=False, allowed_hosts=None):
        deadline = deadline or time.monotonic() + self.cfg.page_budget
        url = guard(url, self.cfg.allow_private)
        original = url
        sensitive = bool(headers) or any(SECRET_KEYS.match(k) or k.lower().startswith("x-amz-") for k, _ in up.parse_qsl(up.urlsplit(url).query))
        use_cache = cache and not sensitive and purpose != "control"
        key = digest(original)
        old = self.cache.get(key) if use_cache else None
        # Recheck current robots even when content is cached.
        if purpose == "page":
            self.check_robots(url, deadline)
        if old and not refresh and time.time() - old["saved"] < self.cfg.cache_ttl:
            if allowed_hosts is not None and up.urlsplit(old["final"]).netloc not in allowed_hosts:
                raise HarvestError("out_of_scope", "Cached redirect left permitted hosts")
            if purpose == "page" and old["final"] != original:
                guard(old["final"], self.cfg.allow_private)
                self.check_robots(old["final"], deadline)
            return {"status": 200, "body": old["body"], "headers": old["headers"], "final_url": old["final"], "cache": "hit", "attempts": 0}
        req_headers = {"User-Agent": UA, "Accept": "text/html,application/json,application/xml,application/pdf;q=0.9,*/*;q=0.5", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8", "Accept-Encoding": "gzip"}
        req_headers.update(headers or {})
        if old:
            for h, dst in [("etag", "If-None-Match"), ("last-modified", "If-Modified-Since")]:
                if old["headers"].get(h):
                    req_headers[dst] = old["headers"][h]
        redirects, attempts = 0, 0
        while True:
            if time.monotonic() >= deadline:
                raise HarvestError("budget_exhausted", "Page time budget exhausted")
            url = guard(url, self.cfg.allow_private)
            if allowed_hosts is not None and up.urlsplit(url).netloc not in allowed_hosts:
                raise HarvestError("out_of_scope", "Redirect left the permitted hosts")
            if purpose == "page":
                self.check_robots(url, deadline)
            base = origin(url)
            self.gate.enter(base, deadline)
            try:
                with self.count_lock:
                    self.count += 1
                req = ur.Request(url, headers=req_headers)
                try:
                    response = self.opener.open(req, timeout=min(self.cfg.timeout, max(0.1, deadline - time.monotonic())))
                except urllib.error.HTTPError as e:
                    response = e
                with response:
                    code = response.code
                    rh = {k.lower(): v for k, v in response.headers.items()}
                    chunks, size = [], 0
                    while True:
                        if time.monotonic() >= deadline:
                            raise HarvestError("budget_exhausted", "Response exceeded page time budget")
                        block = response.read(min(65536, self.cfg.max_bytes + 1 - size))
                        if not block:
                            break
                        chunks.append(block)
                        size += len(block)
                        if size > self.cfg.max_bytes:
                            raise HarvestError("size_limit", "Response exceeded configured byte limit", code)
                    body = b"".join(chunks)
            except HarvestError:
                raise
            except (urllib.error.URLError, OSError, TimeoutError) as e:
                detail = str(getattr(e, "reason", e)).lower()
                kind = "proxy_error" if any(k in detail for k in {"tunnel", "proxy"}) else "tls_error" if any(k in detail for k in {"certificate", "ssl"}) else "timeout" if "timed out" in detail else "network_error"
                # Do not change the user's network route, nor retry invalid TLS.
                if attempts >= self.cfg.retries or kind == "tls_error":
                    raise HarvestError(kind, "HTTP transport failed (route unchanged)") from None
                attempts += 1
                self.gate.slow(base, 2 ** attempts + random.random() * 0.2)
                continue
            finally:
                self.gate.exit(base)
            if code in {301, 302, 303, 307, 308}:
                redirects += 1
                if redirects > 5 or not rh.get("location"):
                    raise HarvestError("redirect_error", "Redirect chain invalid or exceeds five hops", code)
                nxt = normalize_url(up.urljoin(url, rh["location"]))
                if origin(nxt) != base:
                    req_headers = {k: v for k, v in req_headers.items() if k.lower() in {"user-agent", "accept", "accept-language", "accept-encoding"}}
                url = nxt
                continue
            if code in {429, 502, 503, 504}:
                pause = max(retry_after(rh.get("retry-after")), 2 ** attempts + random.random() * 0.2)
                self.gate.slow(base, pause)
                if attempts < self.cfg.retries:
                    attempts += 1
                    continue
            if code == 304 and old:
                result = {"status": 200, "body": old["body"], "headers": {**old["headers"], **rh}, "final_url": url, "cache": "revalidated", "attempts": attempts + 1}
            else:
                sitemap_gz = up.urlsplit(url).path.lower().endswith(".xml.gz") and body.startswith(b"\x1f\x8b")
                if rh.get("content-encoding", "").lower() == "gzip" or sitemap_gz:
                    try:
                        with gzip.GzipFile(fileobj=io.BytesIO(body)) as z:
                            body = z.read(self.cfg.max_bytes + 1)
                    except (OSError, EOFError):
                        raise HarvestError("decode_error", "Invalid gzip response", code) from None
                    if len(body) > self.cfg.max_bytes:
                        raise HarvestError("size_limit", "Decompressed response exceeded byte limit", code)
                    if sitemap_gz:
                        rh["content-type"] = "application/xml"
                result = {"status": code, "body": body, "headers": rh, "final_url": url, "cache": "miss", "attempts": attempts + 1}
            if use_cache and result["status"] == 200 and "no-store" not in rh.get("cache-control", "").lower() and not rh.get("set-cookie") and not sensitive:
                self.cache.put(key, result)
            return result
