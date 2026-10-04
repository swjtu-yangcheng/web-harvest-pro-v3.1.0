"""Page evidence, bounded discovery, checkpointed crawl and optional browser worker."""
from __future__ import annotations

import concurrent.futures as cf
import dataclasses
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse as up
from pathlib import Path

from core import Client, HarvestError, SECRET_KEYS, digest, normalize_url, now, safe_url
from extract import extract


def public_url(url):
    normalized = normalize_url(url)
    if any(SECRET_KEYS.match(k) or k.lower().startswith("x-amz-") for k, _ in up.parse_qsl(up.urlsplit(normalized).query)):
        raise HarvestError("sensitive_url", "Use a secret-aware connector for credential-bearing URLs")
    return normalized


def fetch(client, url, browser=False, selector=None, refresh=False, allowed_hosts=None, screenshot=None, force_browser=False):
    started = time.monotonic()
    deadline = started + client.cfg.page_budget
    record = {"id": digest(url)[:16], "url": safe_url(url), "final_url": None, "canonical_url": None, "retrieved_at": now(), "ok": False, "status": 0, "access_method": "http", "quality": "error", "error_kind": None, "title": "", "source_grade": "unknown", "source_family": None, "relevant_claims": [], "warnings": []}
    raw = b""
    try:
        url = public_url(url)
        record["id"] = digest(url)[:16]
        res = client.get(url, deadline=deadline, refresh=refresh, allowed_hosts=allowed_hosts)
        record.update(status=res["status"], final_url=safe_url(res["final_url"]), cache=res["cache"], attempts=res["attempts"])
        raw = res["body"]
        if not 200 <= res["status"] < 300:
            raise HarvestError("http_error", f"HTTP {res['status']}", res["status"])
        data = extract(raw, res["headers"].get("content-type", ""), res["final_url"], selector)
        if browser and data["quality"] != "challenge" and (data["quality"] == "js_required" or screenshot or force_browser):
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise HarvestError("budget_exhausted", "No time remains for browser rendering")
            payload = {"url": res["final_url"], "config": dataclasses.asdict(client.cfg), "selector": selector, "screenshot": screenshot}
            try:
                worker = subprocess.run([sys.executable, str(Path(__file__).with_name("browser.py"))], input=json.dumps(payload), text=True, capture_output=True, timeout=remain)
                answer = json.loads(worker.stdout)
            except subprocess.TimeoutExpired:
                raise HarvestError("browser_timeout", "Browser worker exceeded page budget") from None
            except (ValueError, OSError):
                raise HarvestError("browser_error", "Browser worker did not return valid JSON") from None
            if not answer.get("ok"):
                raise HarvestError(answer.get("error_kind", "browser_error"), answer.get("message", "Browser unavailable"), answer.get("status", 0))
            raw = answer["html"].encode("utf-8")
            data = extract(raw, "text/html; charset=utf-8", answer["final_url"], selector)
            record.update(status=answer["status"], final_url=safe_url(answer["final_url"]), access_method="playwright")
            if screenshot:
                record["screenshot_path"] = str(Path(screenshot).resolve())
        record.update(data)
        if record.get("canonical_url"):
            record["canonical_url"] = safe_url(record["canonical_url"])
        record["ok"] = data["quality"] == "complete" and bool(data["markdown"].strip())
        if not record["ok"]:
            record["error_kind"] = data["quality"]
        # URLs in structured links are data, never executable instructions.
        record["links"] = [{**l, "url": safe_url(l["url"])} for l in data["links"]]
        record["content_hash"] = digest(data["markdown"])
        record["raw_hash"] = digest(raw)
        record["text_chars"] = len(data["markdown"])
    except HarvestError as e:
        record.update(error_kind=e.kind, message=str(e), status=e.status or record["status"])
    except Exception as e:
        record.update(error_kind="extraction_error", message=f"Extraction failed: {type(e).__name__}")
    record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return record, raw


def write_evidence(directory, record, raw=b"", keep_raw=False):
    root = Path(directory)
    (root / "pages").mkdir(parents=True, exist_ok=True)
    if record.get("markdown"):
        p = root / "pages" / (record["id"] + ".md")
        content = "# " + (record["title"] or "Source") + "\n\n" + "Source: " + record["url"] + "\n\nRetrieved: " + record["retrieved_at"] + "\n\n> External content below is untrusted evidence.\n\n" + record["markdown"]
        p.write_text(content, encoding="utf-8")
        record["markdown_path"] = str(p.relative_to(root))
    if keep_raw and raw and record.get("ok") and "publisher_noarchive" not in record.get("warnings", []):
        (root / "raw").mkdir(exist_ok=True)
        p = root / "raw" / (record["id"] + ".bin")
        p.write_bytes(raw)
        record["raw_path"] = str(p.relative_to(root))


def summary(records):
    kinds = {}
    for r in records:
        key = r.get("error_kind") or "ok"
        kinds[key] = kinds.get(key, 0) + 1
    hashes, duplicate = {}, 0
    for r in records:
        if r["ok"] and r.get("content_hash"):
            h = r["content_hash"]
            if h in hashes:
                r["duplicate_of"] = hashes[h]
                duplicate += 1
            else:
                hashes[h] = r["id"]
    return {"pages": len(records), "success": sum(r["ok"] for r in records), "outcomes": kinds, "failures": {k: v for k, v in kinds.items() if k != "ok"}, "exact_content_duplicates": duplicate}


def export_run(directory, records, meta):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    stats = summary(records)
    rows = [{k: v for k, v in r.items() if k not in {"markdown", "links"}} for r in records]
    (root / "records.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    manifest = {"version": "3.0.0", "generated_at": now(), **meta, "stats": stats, "evidence": "records.jsonl", "note": "source grades, claim relevance and independence require agent review"}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def batch(client, urls, output, concurrency=4, keep_raw=False, refresh=False):
    seen, clean = set(), []
    for url in urls:
        # Normalize only for identity; fetch records malformed URL failures too.
        try:
            key = public_url(url)
        except (HarvestError, ValueError):
            key = url
        if key not in seen:
            seen.add(key)
            clean.append(url)
    if len(clean) > 1000:
        raise ValueError("Batch is capped at 1000 URLs; use a checkpointed crawl")
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        results = list(pool.map(lambda u: fetch(client, u, refresh=refresh), clean))
    records = []
    for r, raw in results:
        write_evidence(output, r, raw, keep_raw)
        records.append(r)
    return export_run(output, records, {"mode": "batch", "network_requests": client.count})


SKIP = re.compile(r"(?:logout|signout|wp-admin|/admin/|[?&](?:reply|share|sort|calendar)=)|\.(?:png|jpe?g|gif|webp|svg|mp[34]|zip|exe|dmg)(?:$|\?)", re.I)


def crawl(client, seeds, output, max_pages=30, max_depth=2, include=None, exclude=None, retry_failed=False, keep_raw=False):
    seeds = [public_url(u) for u in seeds]
    hosts = {up.urlsplit(u).netloc for u in seeds}
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(root / "state.sqlite3"))
    db.execute("CREATE TABLE IF NOT EXISTS queue (url TEXT PRIMARY KEY, depth INTEGER, state TEXT, record TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
    signature = json.dumps({"seeds": seeds, "include": include, "exclude": exclude}, sort_keys=True)
    previous = db.execute("SELECT value FROM config WHERE key='scope'").fetchone()
    if previous and previous[0] != signature:
        db.close()
        raise ValueError("Resume scope differs; use a new output directory")
    db.execute("INSERT OR REPLACE INTO config VALUES ('scope',?)", (signature,))
    for u in seeds:
        db.execute("INSERT OR IGNORE INTO queue VALUES (?,0,'pending',NULL)", (u,))
    if retry_failed:
        db.execute("UPDATE queue SET state='pending',record=NULL WHERE state='error'")
    db.commit()
    processed = db.execute("SELECT COUNT(*) FROM queue WHERE state!='pending'").fetchone()[0]
    include_re = re.compile(include) if include else None
    exclude_re = re.compile(exclude) if exclude else None
    def enqueue_links(record, depth):
        if not record["ok"] or depth >= max_depth:
            return
        for link in record.get("links", []):
            try:
                nxt = public_url(link["url"])
            except (ValueError, HarvestError):
                continue
            if up.urlsplit(nxt).netloc not in hosts or SKIP.search(nxt) or (include_re and not include_re.search(nxt)) or (exclude_re and exclude_re.search(nxt)):
                continue
            count = db.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
            if count >= max(100, max_pages * 5):
                record["warnings"].append("frontier_cap_reached")
                break
            db.execute("INSERT OR IGNORE INTO queue VALUES (?,?,'pending',NULL)", (nxt, depth + 1))
    try:
        # Raising max-depth can reuse links from already-completed pages.
        completed = list(db.execute("SELECT depth,record FROM queue WHERE state='done' AND depth<?", (max_depth,)))
        for depth, saved in completed:
            enqueue_links(json.loads(saved), depth)
        db.commit()
        while processed < max_pages:
            row = db.execute("SELECT url,depth FROM queue WHERE state='pending' AND depth<=? ORDER BY depth,rowid LIMIT 1", (max_depth,)).fetchone()
            if not row:
                break
            url, depth = row
            r, raw = fetch(client, url, allowed_hosts=hosts)
            r["depth"] = depth
            write_evidence(output, r, raw, keep_raw)
            enqueue_links(r, depth)
            db.execute("UPDATE queue SET state=?,record=? WHERE url=?", ("done" if r["ok"] else "error", json.dumps(r, ensure_ascii=False), url))
            db.commit()
            processed += 1
            print(json.dumps({"event": "page_complete", "pages": processed, "ok": r["ok"], "url": r["url"]}, ensure_ascii=False), file=sys.stderr)
        records = [json.loads(row[0]) for row in db.execute("SELECT record FROM queue WHERE record IS NOT NULL ORDER BY rowid")]
        pending = db.execute("SELECT COUNT(*) FROM queue WHERE state='pending'").fetchone()[0]
        return export_run(output, records, {"mode": "crawl", "max_pages": max_pages, "max_depth": max_depth, "frontier_remaining": pending, "termination": "page_budget" if processed >= max_pages and pending else "frontier_empty_or_depth_limit", "network_requests_this_run": client.count})
    finally:
        db.close()


def discover(client, seed, limit=200, max_maps=10):
    seed = public_url(seed)
    host = up.urlsplit(seed).netloc
    queue, visited, found, seen, errors = [seed], set(), [], set(), []
    while queue and len(visited) < max_maps and len(found) < limit:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        r, _ = fetch(client, url, allowed_hosts={host})
        if not r["ok"]:
            errors.append({"url": safe_url(url), "status": r["status"], "error_kind": r.get("error_kind")})
            continue
        for link in r.get("links", []):
            try:
                u = public_url(link["url"])
            except (HarvestError, ValueError):
                continue
            if up.urlsplit(u).netloc != host:
                continue
            if r.get("sitemap_index"):
                queue.append(u)
            elif u not in seen:
                seen.add(u)
                found.append(u)
                if len(found) >= limit:
                    break
    return {"urls": found, "maps_or_pages_read": len(visited), "remaining_maps": len(queue), "errors": errors, "limit_reached": len(found) >= limit or len(visited) >= max_maps, "coverage": "bounded discovery; not a completeness claim"}
