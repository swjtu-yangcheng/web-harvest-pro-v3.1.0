"""Isolated optional Playwright worker. Reads one JSON payload on stdin."""
import dataclasses
import json
import sys
import time
import urllib.parse as up
import urllib.request as ur

from core import Client, Config, HarvestError, UA, guard, origin


def run(payload):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ok": False, "error_kind": "browser_dependency_missing", "message": "Install playwright and Chromium in the active interpreter"}
    cfg = Config(**payload["config"])
    client = Client(cfg)
    url = guard(payload["url"], cfg.allow_private)
    deadline = time.monotonic() + cfg.page_budget
    client.check_robots(url, deadline)
    proxy_url = ""
    if cfg.proxy_mode == "explicit":
        proxy_url = cfg.proxy
    elif cfg.proxy_mode == "env" and not ur.proxy_bypass(up.urlsplit(url).hostname):
        proxy_url = ur.getproxies().get("https") or ur.getproxies().get("http") or ""
    proxy = None
    if proxy_url:
        p = up.urlsplit(proxy_url)
        proxy = {"server": up.urlunsplit((p.scheme, (p.hostname or "") + (f":{p.port}" if p.port else ""), "", "", ""))}
        if p.username:
            proxy.update(username=up.unquote(p.username), password=up.unquote(p.password or ""))
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, proxy=proxy)
        context = browser.new_context(user_agent=UA, locale="zh-CN", service_workers="block", accept_downloads=False)
        try:
            def route_request(route):
                req = route.request
                try:
                    if req.method not in {"GET", "HEAD"}:
                        route.abort()
                        return
                    guard(req.url, cfg.allow_private)
                    if req.is_navigation_request():
                        client.check_robots(req.url, deadline)
                        if origin(req.url) != origin(url):
                            route.abort()
                            return
                    if req.resource_type in {"media", "font"} and not payload.get("screenshot"):
                        route.abort()
                        return
                    route.continue_()
                except Exception:
                    route.abort()
            context.route("**/*", route_request)
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=round(cfg.timeout * 1000))
            status = response.status if response else 0
            if not 200 <= status < 300:
                return {"ok": False, "error_kind": "http_error", "status": status, "message": f"Browser HTTP {status}"}
            if payload.get("selector"):
                page.locator(payload["selector"]).first.wait_for(timeout=round(cfg.timeout * 1000))
            else:
                try:
                    page.wait_for_function("document.body && document.body.innerText.trim().length >= 80", timeout=3000)
                except Exception:
                    pass
            source = page.content()
            if len(source.encode("utf-8")) > cfg.max_bytes:
                return {"ok": False, "error_kind": "size_limit", "message": "Rendered HTML exceeded byte budget"}
            if payload.get("screenshot"):
                page.screenshot(path=payload["screenshot"], full_page=False)
            return {"ok": True, "status": status, "final_url": page.url, "html": source}
        finally:
            context.close()
            browser.close()
            client.close()


if __name__ == "__main__":
    try:
        result = run(json.load(sys.stdin))
    except Exception as e:
        result = {"ok": False, "error_kind": getattr(e, "kind", "browser_error"), "message": "Browser failed: " + type(e).__name__}
    print(json.dumps(result, ensure_ascii=False))
