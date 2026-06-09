from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import shutil
import sys
import textwrap
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
SITE_DIR = ROOT / "site"
TOPIC_ARCHIVE_PATH = DATA_DIR / "topic_archive.json"

USER_AGENT = "EastonRadar/0.1 (+https://radar.huadongpeng.com)"
SEARCH_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MAX_ITEMS_PER_SOURCE = 18
MAX_TOTAL_ITEMS = 220
MAX_REPORTS_PER_BATCH = 6
MAX_REPORTS_PER_TOPIC = 3
TRIAGE_BATCH_SIZE = 10
MAX_REPORTS_PER_SOURCE_HOST = 2
MIN_DEEP_DIVE_SCORE = 45
MIN_BRIEF_SCORE = 55
SOURCE_CATEGORY_REPORT_CAPS = {
    "ai_tools": 6,
    "developer_business": 4,
    "overseas_and_platforms": 4,
    "platform_policy": 4,
}
SEARCH_CACHE: dict[str, list[dict[str, str]]] = {}


def info(message: str) -> None:
    print(f"[info] {message}", flush=True)


@dataclass
class SourceItem:
    id: str
    source_category: str
    source_name: str
    source_type: str
    title: str
    url: str
    summary: str = ""
    published_at: str = ""
    fetched_at: str = ""
    feed_url: str = ""


@dataclass
class RadarDecision:
    item: SourceItem
    decision: str
    report_type: str
    report_title: str
    score: int
    reader_hook: str
    why_now: str
    evidence_level: str
    reason: str
    reject_reason: str = ""
    collection_fit: str = ""
    investigation_direction: str = ""
    uncertainty_flags: list[str] = field(default_factory=list)
    traceability: dict[str, Any] = field(default_factory=dict)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def now_bj() -> dt.datetime:
    return now_utc().astimezone(dt.timezone(dt.timedelta(hours=8)))


def current_slot(value: str) -> str:
    if value != "auto":
        return value
    hour = now_bj().hour
    if hour < 11:
        return "morning"
    if hour < 17:
        return "noon"
    return "evening"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def clean_generated_outputs() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for path in REPORTS_DIR.glob("*.json"):
        path.unlink()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for path in DATA_DIR.glob("*.json"):
        if path.name != TOPIC_ARCHIVE_PATH.name:
            path.unlink()
    for rel in ["items", "reports", "topics", "briefings", "archive", "about", "assets"]:
        target = SITE_DIR / rel
        if target.exists():
            shutil.rmtree(target)
    for rel in ["index.html", "sitemap.xml", "robots.txt", "llms.txt", "ads.txt"]:
        target = SITE_DIR / rel
        if target.exists():
            target.unlink()


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def count_configured_sources(sources: dict[str, Any]) -> int:
    count = 0
    for group in sources.values():
        if isinstance(group, dict):
            count += len(group.get("feeds", []))
            count += len(group.get("apis", []))
        elif isinstance(group, list):
            count += len(group)
    return count


def normalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(html.unescape(url).strip())
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = [(k, v) for k, v in query if not k.lower().startswith(("utm_", "fbclid", "gclid"))]
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, urllib.parse.urlencode(query), ""))


def slugify(text: str, fallback: str = "report") -> str:
    lower = text.lower()
    ascii_part = re.sub(r"[^a-z0-9]+", "-", lower).strip("-")[:48].strip("-")
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{ascii_part or fallback}-{digest}"


def topic_fingerprint(text: str) -> str:
    text = html.unescape(text or "").lower()
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    stop_words = {
        "the", "and", "for", "with", "from", "now", "new", "update", "updates",
        "工具账本", "案例复盘", "深度调查", "机会拆解", "平台规则", "风险避坑",
    }
    tokens = [token for token in text.split() if token and token not in stop_words]
    return " ".join(tokens[:14])


def load_topic_archive() -> dict[str, Any]:
    if not TOPIC_ARCHIVE_PATH.exists():
        return rebuild_topic_archive_from_reports()
    try:
        data = json.loads(TOPIC_ARCHIVE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "items": []}
    if not isinstance(data, dict):
        return {"version": 1, "items": []}
    data.setdefault("version", 1)
    data.setdefault("items", [])
    return data


def rebuild_topic_archive_from_reports() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if not REPORTS_DIR.exists():
        return {"version": 1, "items": []}
    for path in sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:240]:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        seen_at = report.get("fetched_at") or report.get("published_at") or now_utc().isoformat()
        dossier = report.get("selection_dossier") or report.get("material_pack") or {}
        verdict = dossier.get("verdict", {}) if isinstance(dossier, dict) else {}
        items.append({
            "id": report.get("id") or path.stem,
            "batch_id": "rebuilt-from-reports",
            "title": report.get("title") or report.get("original_title") or path.stem,
            "original_title": report.get("original_title", ""),
            "url": report.get("url", ""),
            "fingerprint": topic_fingerprint(report.get("title") or report.get("original_title", "")),
            "topic_direction": report.get("topic_direction", ""),
            "topic_direction_title": report.get("topic_direction_title", ""),
            "verdict": verdict.get("label") or verdict.get("status") or "历史",
            "evidence_level": report.get("evidence_level", ""),
            "score": report.get("score", 0),
            "first_seen_at": seen_at,
            "last_seen_at": seen_at,
            "item_url": f"/items/{report.get('id') or path.stem}/",
        })
    return {"version": 1, "updated_at": now_utc().isoformat(), "items": items}


def recent_archive_items(archive: dict[str, Any], days: int = 14) -> list[dict[str, Any]]:
    cutoff = now_utc() - dt.timedelta(days=days)
    recent: list[dict[str, Any]] = []
    for item in archive.get("items", []):
        value = item.get("first_seen_at") or item.get("last_seen_at") or ""
        try:
            seen = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            seen = now_utc()
        if seen >= cutoff:
            recent.append(item)
    return recent


def is_duplicate_topic(decision: "RadarDecision", site: dict[str, Any], archive: dict[str, Any], batch_id: str, days: int = 14) -> tuple[bool, str]:
    topic_key, _ = topic_direction_for_item(decision.item, decision.report_type, site)
    url = normalize_url(decision.item.url)
    fingerprint = topic_fingerprint(decision.report_title or decision.item.title)
    for item in recent_archive_items(archive, days):
        if item.get("batch_id") == batch_id:
            continue
        if item.get("url") and normalize_url(str(item.get("url"))) == url:
            return True, f"近 {days} 天已收录同 URL：{item.get('title', '')}"
        if item.get("fingerprint") and item.get("fingerprint") == fingerprint:
            return True, f"近 {days} 天已收录相似选题：{item.get('title', '')}"
    return False, ""


def archive_entry(report: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    dossier = report.get("selection_dossier") or report.get("material_pack") or {}
    verdict = dossier.get("verdict", {})
    now = batch.get("generated_at") or now_utc().isoformat()
    return {
        "id": report["id"],
        "batch_id": batch["batch_id"],
        "title": display_report_title(report),
        "original_title": report.get("original_title", ""),
        "url": report.get("url", ""),
        "fingerprint": topic_fingerprint(report.get("title") or report.get("original_title", "")),
        "topic_direction": report.get("topic_direction", ""),
        "topic_direction_title": report.get("topic_direction_title", ""),
        "verdict": verdict.get("label") or verdict.get("status") or "待判断",
        "evidence_level": report.get("evidence_level", ""),
        "score": report.get("score", 0),
        "first_seen_at": now,
        "last_seen_at": now,
        "item_url": f"/items/{report['id']}/",
    }


def update_topic_archive(archive: dict[str, Any], reports: list[dict[str, Any]], batch: dict[str, Any]) -> dict[str, Any]:
    existing = archive.get("items", [])
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for item in existing:
        key = (str(item.get("topic_direction", "")), str(item.get("fingerprint", "")))
        if key[1]:
            by_key[key] = item
    for report in reports:
        entry = archive_entry(report, batch)
        key = (entry["topic_direction"], entry["fingerprint"])
        previous = by_key.get(key)
        if previous:
            previous.update({k: v for k, v in entry.items() if k not in {"first_seen_at"}})
            previous["last_seen_at"] = entry["last_seen_at"]
        else:
            existing.append(entry)
            by_key[key] = entry
    existing.sort(key=lambda x: x.get("last_seen_at", ""), reverse=True)
    return {"version": 1, "updated_at": batch.get("generated_at", now_utc().isoformat()), "items": existing[:240]}


def fetch_url(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_search_url(url: str, timeout: int = 18) -> bytes:
    headers = {
        "User-Agent": SEARCH_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Referer": "https://www.google.com/",
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def search_block_reason(raw: str, provider: str) -> str:
    lower = raw.lower()
    if provider == "ddg":
        if "anomaly.js" in lower or "cc=botnet" in lower or "/anomaly" in lower:
            return "duckduckgo anomaly page"
        if "captcha" in lower or "challenge" in lower:
            return "duckduckgo challenge page"
    if provider == "bing":
        if "captcha" in lower or "id=\"bnp_container\"" in lower or "bnp_btn_accept" in lower:
            return "bing captcha/consent page"
        if "unusual traffic" in lower or "verify you are human" in lower:
            return "bing anti-bot page"
    return ""


def brave_search(query: str, limit: int = 5) -> list[dict[str, str]]:
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not query.strip() or not api_key:
        return []
    cache_key = f"brave:{query}:{limit}"
    if cache_key in SEARCH_CACHE:
        return SEARCH_CACHE[cache_key]
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({
        "q": query,
        "count": max(1, min(limit, 20)),
        "text_decorations": "false",
    })
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "User-Agent": USER_AGENT,
                "X-Subscription-Token": api_key,
            },
        )
        with urllib.request.urlopen(req, timeout=18) as resp:
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception as exc:
        print(f"Brave search failed for {query}: {exc}", file=sys.stderr)
        SEARCH_CACHE[cache_key] = []
        return []
    results: list[dict[str, str]] = []
    for row in data.get("web", {}).get("results", []):
        title = clean_text(str(row.get("title", "")))
        url_value = normalize_url(str(row.get("url", "")))
        if title and url_value.startswith(("http://", "https://")):
            results.append({"title": title, "url": url_value, "query": query, "provider": "brave"})
        if len(results) >= limit:
            break
    SEARCH_CACHE[cache_key] = results
    return results


def deepseek_json(messages: list[dict[str, str]], max_tokens: int = 6000, temperature: float = 0.2, timeout: int = 90) -> Any | None:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return None
    body = {
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": messages,
    }
    try:
        req = urllib.request.Request(
            DEEPSEEK_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        return json.loads(content)
    except Exception as exc:
        print(f"DeepSeek JSON call failed: {exc}", file=sys.stderr)
        return None


def ddg_search(query: str, limit: int = 5) -> list[dict[str, str]]:
    if not query.strip():
        return []
    cache_key = f"ddg:{query}:{limit}"
    if cache_key in SEARCH_CACHE:
        return SEARCH_CACHE[cache_key]
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    try:
        raw = fetch_search_url(url, timeout=18).decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"DDG search failed for {query}: {exc}", file=sys.stderr)
        SEARCH_CACHE[cache_key] = []
        return []
    block_reason = search_block_reason(raw, "ddg")
    if block_reason:
        print(f"DDG search blocked for {query}: {block_reason}", file=sys.stderr)
        SEARCH_CACHE[cache_key] = []
        return []
    results: list[dict[str, str]] = []
    for match in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', raw, re.S):
        href = html.unescape(match.group(1))
        title = clean_text(match.group(2))
        parsed = urllib.parse.urlparse(href)
        params = urllib.parse.parse_qs(parsed.query)
        if "uddg" in params:
            href = params["uddg"][0]
        if title and href.startswith(("http://", "https://")):
            results.append({"title": title, "url": normalize_url(href), "query": query, "provider": "ddg"})
        if len(results) >= limit:
            break
    if not results:
        print(f"DDG search returned no parsed results for {query}: html_len={len(raw)}", file=sys.stderr)
    SEARCH_CACHE[cache_key] = results
    return results


def bing_search(query: str, limit: int = 5) -> list[dict[str, str]]:
    cache_key = f"bing:{query}:{limit}"
    if cache_key in SEARCH_CACHE:
        return SEARCH_CACHE[cache_key]
    url = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query})
    try:
        raw = fetch_search_url(url, timeout=18).decode("utf-8", errors="ignore")
    except Exception as exc:
        print(f"Bing search failed for {query}: {exc}", file=sys.stderr)
        SEARCH_CACHE[cache_key] = []
        return []
    block_reason = search_block_reason(raw, "bing")
    if block_reason:
        print(f"Bing search blocked for {query}: {block_reason}", file=sys.stderr)
        SEARCH_CACHE[cache_key] = []
        return []
    results: list[dict[str, str]] = []
    for match in re.finditer(r'<li class="b_algo".*?<h2>.*?<a href="([^"]+)"[^>]*>(.*?)</a>', raw, re.S):
        href = html.unescape(match.group(1))
        title = clean_text(match.group(2))
        if title and href.startswith(("http://", "https://")):
            results.append({"title": title, "url": normalize_url(href), "query": query, "provider": "bing"})
        if len(results) >= limit:
            break
    if not results:
        print(f"Bing search returned no parsed results for {query}: html_len={len(raw)}", file=sys.stderr)
    SEARCH_CACHE[cache_key] = results
    return results


def search_web(query: str, limit: int = 5) -> list[dict[str, str]]:
    providers: list[tuple[str, Any]] = []
    if os.environ.get("BRAVE_SEARCH_API_KEY", "").strip():
        providers.append(("brave", brave_search))
    providers.extend([("ddg", ddg_search), ("bing", bing_search)])
    for name, func in providers:
        results = func(query, limit=limit)
        if results:
            return results
        info(f"Search provider empty: provider={name}, query={query[:80]}")
    return []


def fetch_evidence_pages(search_results: list[dict[str, str]], per_query_limit: int = 3, text_limit: int = 1800) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    seen: set[str] = set()
    per_query_counts: dict[str, int] = {}
    for result in search_results:
        url = result.get("url", "")
        query = result.get("query", "")
        if not url or url in seen:
            continue
        if per_query_counts.get(query, 0) >= per_query_limit:
            continue
        seen.add(url)
        text = ""
        error = ""
        try:
            text = clean_text(fetch_url(url, timeout=16).decode("utf-8", errors="ignore"))[:text_limit]
        except Exception as exc:
            error = str(exc)[:180]
        evidence.append({
            "title": result.get("title", ""),
            "url": url,
            "query": query,
            "fetched_text": text,
            "fetch_error": error,
        })
        per_query_counts[query] = per_query_counts.get(query, 0) + 1
        if len(evidence) >= 18:
            break
    return evidence


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def child_text(node: ET.Element | None, names: list[str]) -> str:
    if node is None:
        return ""
    for child in node:
        if strip_ns(child.tag) in names and child.text:
            return child.text
    return ""


def child_link(node: ET.Element) -> str:
    for child in node:
        if strip_ns(child.tag) == "link":
            if child.attrib.get("href"):
                return child.attrib["href"]
            if child.text:
                return child.text
    return ""


def parse_date(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat()
    except Exception:
        return value


def make_item(source_category: str, source_name: str, source_type: str, title: str, url: str, summary: str, published_at: str, feed_url: str) -> SourceItem:
    normalized = normalize_url(url)
    digest = hashlib.sha1(f"{source_category}:{normalized}".encode("utf-8")).hexdigest()[:12]
    return SourceItem(
        id=digest,
        source_category=source_category,
        source_name=source_name,
        source_type=source_type,
        title=title,
        url=normalized,
        summary=summary[:900],
        published_at=published_at,
        fetched_at=now_utc().isoformat(),
        feed_url=feed_url,
    )


def should_drop_item(item: SourceItem) -> bool:
    text = f"{item.title} {item.summary}".lower()
    host = urllib.parse.urlparse(item.url).netloc.lower()
    noisy_phrases = [
        "氪星晚报",
        "8点1氪",
        "早报",
        "晚报",
        "午报",
        "一周融资",
        "完成融资",
        "获得融资",
        "质押",
        "贷款提供担保",
    ]
    if any(phrase.lower() in text for phrase in noisy_phrases):
        return True
    if "v2ex.com" in host and "jobs.xml" in item.feed_url:
        job_fit_keywords = ["remote", "远程", "兼职", "外包", "副业", "出海", "跨境", "海外", "独立开发", "saas", "shopify", "stripe"]
        if not any(word in text for word in job_fit_keywords):
            return True
    if item.source_category == "overseas_and_platforms" and any(word in text for word in ["融资", "ipo", "财报"]) and not any(word in text for word in ["stripe", "shopify", "支付", "跨境", "出海", "开发者", "ai", "api"]):
        return True
    if len(item.title) > 90 and any(mark in item.title for mark in ["丨", "；", ";"]):
        return True
    return False


def parse_feed(source_category: str, feed_url: str, payload: bytes) -> list[SourceItem]:
    root = ET.fromstring(payload)
    items: list[SourceItem] = []
    if strip_ns(root.tag) == "rss":
        channel = root.find("channel")
        source_name = clean_text(child_text(channel, ["title"])) or urllib.parse.urlparse(feed_url).netloc
        nodes = channel.findall("item") if channel is not None else []
        for node in nodes[:MAX_ITEMS_PER_SOURCE]:
            title = clean_text(child_text(node, ["title"]))
            url = clean_text(child_text(node, ["link"]))
            summary = clean_text(child_text(node, ["description", "summary", "content"]))
            published_at = parse_date(child_text(node, ["pubDate", "published", "updated"]))
            if title and url:
                items.append(make_item(source_category, source_name, "rss", title, url, summary, published_at, feed_url))
        return items

    source_name = clean_text(child_text(root, ["title"])) or urllib.parse.urlparse(feed_url).netloc
    nodes = [node for node in root.iter() if strip_ns(node.tag) in {"entry", "item"}]
    for node in nodes[:MAX_ITEMS_PER_SOURCE]:
        title = clean_text(child_text(node, ["title"]))
        url = child_link(node)
        summary = clean_text(child_text(node, ["summary", "content", "description"]))
        published_at = parse_date(child_text(node, ["published", "updated", "pubDate"]))
        if title and url:
            items.append(make_item(source_category, source_name, "rss", title, url, summary, published_at, feed_url))
    return items


def parse_hn_api(source_category: str, api_url: str, payload: bytes) -> list[SourceItem]:
    data = json.loads(payload.decode("utf-8"))
    items: list[SourceItem] = []
    for hit in data.get("hits", [])[:MAX_ITEMS_PER_SOURCE]:
        title = clean_text(hit.get("title") or hit.get("story_title") or "")
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        summary = clean_text(hit.get("comment_text") or "")
        if title and url:
            items.append(make_item(source_category, "Hacker News", "api", title, url, summary, hit.get("created_at", ""), api_url))
    return items


def collect_items(sources: dict[str, Any]) -> tuple[list[SourceItem], list[dict[str, str]]]:
    seen: set[str] = set()
    items: list[SourceItem] = []
    failures: list[dict[str, str]] = []
    for source_category, group in sources.items():
        for url in group.get("feeds", []):
            try:
                for item in parse_feed(source_category, url, fetch_url(url)):
                    if item.url not in seen and not should_drop_item(item):
                        items.append(item)
                        seen.add(item.url)
            except Exception as exc:
                failures.append({"source_category": source_category, "source": url, "error": str(exc)[:240]})
        for url in group.get("apis", []):
            try:
                for item in parse_hn_api(source_category, url, fetch_url(url)):
                    if item.url not in seen and not should_drop_item(item):
                        items.append(item)
                        seen.add(item.url)
            except Exception as exc:
                failures.append({"source_category": source_category, "source": url, "error": str(exc)[:240]})
    items.sort(key=lambda item: item.published_at or item.fetched_at, reverse=True)
    return items[:MAX_TOTAL_ITEMS], failures


def source_coverage(items: list[SourceItem], failures: list[dict[str, str]], site: dict[str, Any]) -> dict[str, Any]:
    categories = site.get("source_categories", {})
    result: dict[str, Any] = {}
    for key, title in categories.items():
        result[key] = {
            "title": title,
            "items": sum(1 for item in items if item.source_category == key),
            "failures": sum(1 for failure in failures if failure.get("source_category") == key),
        }
    for item in items:
        result.setdefault(item.source_category, {"title": item.source_category, "items": 0, "failures": 0})
    for failure in failures:
        result.setdefault(failure.get("source_category", "unknown"), {"title": failure.get("source_category", "unknown"), "items": 0, "failures": 0})
    return result


def infer_report_type(text: str, source_category: str) -> str:
    text = text.lower()
    if source_category == "platform_policy":
        return "platform-rules"
    if any(w in text for w in ["pricing", "price", "cost", "token", "bill", "revenue", "mrr", "adsense", "价格", "成本", "收入", "账单"]):
        return "tool-ledger"
    if any(w in text for w in ["policy", "rules", "compliance", "seo", "google", "search", "stripe", "paddle", "规则", "合规", "平台"]):
        return "platform-rules"
    if any(w in text for w in ["scam", "risk", "ban", "blocked", "lawsuit", "安全", "风险", "封号", "骗局"]):
        return "risk-warning"
    if any(w in text for w in ["case study", "show hn", "launched", "built", "github", "open source", "开源", "复盘"]):
        return "case-study"
    if source_category in {"developer_business", "overseas_and_platforms"}:
        return "opportunity"
    return "investigation"


def heuristic_decision(item: SourceItem, site: dict[str, Any]) -> RadarDecision:
    text = f"{item.title} {item.summary}".lower()
    groups = {
        "money": ["pricing", "price", "cost", "revenue", "mrr", "adsense", "affiliate", "payment", "stripe", "paddle", "价格", "成本", "收入", "收款", "变现"],
        "ai": ["ai", "llm", "agent", "openai", "claude", "copilot", "deepseek", "model", "token", "cursor", "自动化"],
        "dev": ["github", "developer", "api", "sdk", "cloudflare", "vercel", "database", "server", "开源", "程序员", "开发"],
        "platform": ["policy", "rules", "compliance", "seo", "search", "google", "amazon", "shopify", "tiktok", "合规", "平台", "规则", "出海"]
    }
    hits: dict[str, list[str]] = {}
    score = 0
    for group, words in groups.items():
        matched = [word for word in words if word in text]
        if matched:
            hits[group] = matched[:5]
            score += 12 + min(len(matched), 4) * 3
    host = urllib.parse.urlparse(item.url).netloc
    official_hosts = ["openai.com", "google", "github.blog", "cloudflare.com", "amazon.com", "huggingface.co"]
    evidence_level = "official" if any(x in host for x in official_hosts) else "near_source"
    if evidence_level == "official":
        score += 12
    if item.source_type == "api":
        score += 5
    score = min(score, 100)
    report_type = infer_report_type(text, item.source_category)
    report_title = make_report_title(item, report_type, site)
    if score >= 55:
        decision = "deep_dive"
    elif score >= 32:
        decision = "brief"
    else:
        decision = "skip"
    reader_hook = infer_reader_hook(hits, report_type)
    reject_reason = "" if decision != "skip" else "读者入口、成本/平台/工具链关联不够明确，先不进入候选池。"
    return RadarDecision(
        item=item,
        decision=decision,
        report_type=report_type,
        report_title=report_title,
        score=score,
        reader_hook=reader_hook,
        why_now="来自本批次稳定公开源，适合先进入 Radar 观察。",
        evidence_level=evidence_level,
        reason="命中老花主线关键词，且来源可复查。" if decision != "skip" else reject_reason,
        reject_reason=reject_reason,
        collection_fit=collection_fit_text(score, evidence_level, report_type, site),
        investigation_direction=infer_investigation_direction(report_type),
        uncertainty_flags=default_uncertainty_flags(evidence_level, decision),
        traceability={"matched_keywords": hits, "source_host": host, "source_type": item.source_type, "heuristic": True},
    )


def infer_reader_hook(hits: dict[str, list[str]], report_type: str) -> str:
    if report_type == "tool-ledger":
        return "这条线索可能影响 AI/API/云服务/开发工具成本，适合按普通技术人的账本拆。"
    if report_type == "platform-rules":
        return "这条线索可能影响平台规则、搜索流量、出海路径或合规边界，适合先看清条件。"
    if report_type == "risk-warning":
        return "这条线索可能藏着成本、合规、封号或营销话术风险，适合先避坑。"
    if report_type == "case-study":
        return "这条线索有具体项目或案例，可以拆哪些条件可复制，哪些只是别人自己的局。"
    if report_type == "opportunity":
        return "这条线索可能关联副业、独立开发或出海机会，需要继续拆门槛、用户和现金流。"
    if "ai" in hits and "dev" in hits:
        return "这条线索可能影响 AI 开发工作流、API 使用成本或程序员工具链，适合判断是否值得跟进。"
    return "这条线索需要继续确认它和普通技术人的成本、岗位、工具链或机会有什么关系。"


def display_source_name(item: SourceItem) -> str:
    host = urllib.parse.urlparse(item.url).netloc.lower()
    if "v2ex.com" in host:
        if "create.xml" in item.feed_url:
            return "V2EX 分享创造"
        if "jobs.xml" in item.feed_url:
            return "V2EX 酷工作"
        return "V2EX"
    if "aws.amazon.com" in host:
        return "AWS Machine Learning Blog"
    if "github.blog" in host:
        return "GitHub Blog"
    if "developers.cloudflare.com" in host:
        return "Cloudflare Developers"
    if "vercel.com" in host:
        return "Vercel Changelog"
    if "openai.com" in host:
        return "OpenAI News"
    if "blog.google" in host:
        return "Google AI Blog"
    if "deepmind.google" in host:
        return "Google DeepMind Blog"
    if "huggingface.co" in host:
        return "Hugging Face Blog"
    if "stripe.com" in host:
        return "Stripe Blog"
    if "producthunt.com" in host:
        return "Product Hunt"
    if "news.ycombinator.com" in host:
        return "Hacker News"
    if "shopify.dev" in host:
        return "Shopify Developer Changelog"
    if "developer.apple.com" in host:
        return "Apple Developer News"
    noisy = {"ai", "artificial intelligence", "archive: 2026 - github changelog", "machine learning"}
    if item.source_name.strip().lower() in noisy:
        return urllib.parse.urlparse(item.url).netloc.replace("www.", "")
    return item.source_name


def title_subject(title: str, fallback: str) -> str:
    text = clean_text(title)
    text = re.sub(r"^\[[^\]]+\]\s*", "", text)
    text = re.sub(r"^【[^】]+】\s*", "", text)
    for part in re.split(r"[|｜:：,，;；。!！?？—（(]+", text):
        candidate = part.strip(" -_")
        if len(candidate) >= 2:
            return candidate[:24]
    return (text or fallback)[:24]


def ascii_dominant(text: str) -> bool:
    letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return letters > 0 and cjk == 0


def chinese_topic_hint(title: str, report_type: str) -> str:
    lower = title.lower()
    def has_word(*words: str) -> bool:
        return any(re.search(rf"\b{re.escape(word)}\b", lower) for word in words)

    if any(word in lower for word in ["pricing", "billing", "usage", "cost", "budget", "quota", "api access"]):
        return "计费和成本变化"
    if has_word("search", "seo", "ranking", "ai mode") or "google search" in lower:
        return "搜索生态变化"
    if any(word in lower for word in ["codex", "copilot", "claude code", "agent", "agents", "tool calling"]):
        return "AI 编程工具变化"
    if any(word in lower for word in ["model", "gpt", "gemini", "claude", "nova", "nemotron", "llm", "inference"]):
        return "模型能力变化"
    if any(word in lower for word in ["policy", "license", "terms", "safety", "compliance", "privacy", "risk"]):
        return "平台规则变化"
    if any(word in lower for word in ["stripe", "payment", "billing", "shopify", "storefront", "commerce"]):
        return "支付和电商平台变化"
    if any(word in lower for word in ["cloudflare", "worker", "gateway", "vercel", "aws", "github"]):
        return "开发者平台变化"
    fallback = {
        "tool-ledger": "工具成本变化",
        "platform-rules": "平台规则变化",
        "case-study": "案例线索",
        "opportunity": "机会线索",
        "risk-warning": "风险线索",
        "investigation": "重要线索",
    }
    return fallback.get(report_type, "重要线索")


def make_report_title(item: SourceItem, report_type: str, site: dict[str, Any]) -> str:
    report_name = site["report_types"].get(report_type, {}).get("title", "线索")
    host = urllib.parse.urlparse(item.url).netloc.replace("www.", "")
    source = display_source_name(item) or host
    lower = f"{item.title} {source}".lower()
    known = ["OpenAI", "Claude", "GitHub", "Copilot", "Cloudflare", "Amazon", "AWS", "Google", "Gemini", "DeepSeek", "Hugging Face", "Vercel", "Stripe", "Microsoft", "Apple", "Siri", "Shopify"]
    source_label = source or host
    brand = ""
    for name in known:
        if name.lower() in lower:
            brand = name
            break
    if brand == "Amazon":
        brand = "AWS"
    if brand == "Siri":
        brand = "Apple"
    if source_label.lower() in {"artificial intelligence", "machine learning"} and "amazon" in host:
        brand = "AWS"

    topic = title_subject(item.title, source_label)
    if ascii_dominant(topic):
        label = brand or source_label
        if (source.startswith("V2EX") or source in {"Product Hunt", "Hacker News"}) and len(topic) <= 20 and len(topic.split()) <= 4:
            label = topic
        subject = f"{label} {chinese_topic_hint(item.title, report_type)}"
        return f"{report_name}：{subject}"
    if brand and brand.lower() not in topic.lower():
        subject = f"{brand}：{topic}"
    else:
        subject = topic or brand or source_label
    return f"{report_name}：{subject}"


def collection_fit_text(score: int, evidence_level: str, report_type: str, site: dict[str, Any]) -> str:
    report_name = site["report_types"].get(report_type, {}).get("title", "报告")
    if score >= 55 and evidence_level in {"official", "near_source"}:
        return f"符合收集原则：来源可复查，且具备进入「{report_name}」类报告的分析价值。"
    if score >= 32:
        return "部分符合收集原则：可以进入观察池，但证据链或读者入口还不够完整。"
    return "暂不符合深挖原则：相关性、证据质量或普通读者入口不足。"


def infer_investigation_direction(report_type: str) -> str:
    mapping = {
        "tool-ledger": "优先追价格页、额度、API 文档、替代方案和实际成本边界。",
        "platform-rules": "优先追官方政策、开发者公告、执行范围、受影响人群和合规边界。",
        "case-study": "优先追项目原始页面、代码仓库、作者复盘、收入/增长证据和不可复制条件。",
        "opportunity": "优先追需求是否真实、用户是谁、付费路径、最小验证成本和停止信号。",
        "risk-warning": "优先追反方证据、投诉、政策限制、成本陷阱和营销话术来源。",
        "investigation": "优先追一手来源、概念定义、时间线、证据矛盾和可能影响面。",
    }
    return mapping.get(report_type, "继续补齐一手来源、反方证据和影响边界。")


def default_uncertainty_flags(evidence_level: str, decision: str) -> list[str]:
    flags = ["尚未抓取正文外的补充证据。", "尚未形成多源交叉验证。"]
    if evidence_level not in {"official", "near_source"}:
        flags.append("当前来源不是官方或近源，结论必须降级。")
    if decision == "brief":
        flags.append("当前仅适合观察，不宜写成深度结论。")
    return flags


def source_priority(item: SourceItem, evidence_level: str) -> str:
    host = urllib.parse.urlparse(item.url).netloc.lower()
    if evidence_level == "official":
        return "P0 官方/一手源"
    if any(key in host for key in ["github.com", "github.blog", "developers.", "docs.", "cloudflare.com", "openai.com", "google", "amazon.com", "stripe.com"]):
        return "P0 官方/一手源"
    if item.source_name in {"Hacker News", "Product Hunt"} or any(key in host for key in ["simonwillison.net", "ycombinator.com", "changelog.com"]):
        return "P1 高质量近源"
    if item.source_category in {"overseas_and_platforms", "developer_business"}:
        return "P2 可参考源"
    return "P2 可参考源"


def source_access_assessment(item: SourceItem, failures: list[dict[str, str]] | None = None) -> dict[str, Any]:
    failed = [f for f in failures or [] if f.get("source") == item.feed_url]
    return {
        "access_method": item.source_type,
        "feed_url": item.feed_url,
        "stable_in_github_actions": not failed,
        "failure_reason": failed[0]["error"] if failed else "",
        "anti_scrape_required": False,
        "notes": "来自当前可稳定抓取的公开源；无需登录、代理池或浏览器指纹。"
    }


def evidence_dossier(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "confirmed": report["verification"]["what_is_confirmed"],
        "needs_followup": report["verification"]["what_needs_followup"],
        "uncertain": report["uncertainty_flags"],
        "not_claimable": report["verification"]["do_not_claim"],
        "expert_challenges": report["verification"]["expert_challenge_points"],
    }


def followup_queries(item: SourceItem, report_type: str) -> list[str]:
    title = item.title.strip()
    source = item.source_name.strip()
    base = title or source
    has_chinese = bool(re.search(r"[\u4e00-\u9fff]", base))
    if has_chinese:
        templates = {
            "tool-ledger": [
                f"{base} 官方公告 价格 成本",
                f"{base} API 文档 额度 限制",
                f"{base} 替代方案 真实使用 成本",
            ],
            "platform-rules": [
                f"{base} 官方政策 规则 原文",
                f"{base} 开发者公告 影响范围",
                f"{base} 合规 风险 受影响用户",
            ],
            "case-study": [
                f"{base} 原始项目 GitHub 复盘",
                f"{base} 收入 增长 证据",
                f"{base} 失败 限制 反方证据",
            ],
            "opportunity": [
                f"{base} 用户需求 付费意愿",
                f"{base} 竞品 替代方案",
                f"{base} 最小验证 案例",
            ],
            "risk-warning": [
                f"{base} 投诉 风险 违规",
                f"{base} 骗局 营销话术",
                f"{base} 隐藏成本 失败案例",
            ],
            "investigation": [
                f"{base} 官方来源 原始公告",
                f"{base} 概念解释 证据",
                f"{base} 反方观点 局限",
            ],
        }
        return templates.get(report_type, templates["investigation"])
    templates = {
        "tool-ledger": [
            f"{base} official pricing limits changelog",
            f"{base} API documentation quota cost",
            f"{base} alternative comparison cost",
        ],
        "platform-rules": [
            f"{base} official policy documentation",
            f"{base} developer announcement impact",
            f"{base} compliance risk affected users",
        ],
        "case-study": [
            f"{base} GitHub repository case study",
            f"{base} founder postmortem revenue evidence",
            f"{base} criticism limitations real users",
        ],
        "opportunity": [
            f"{base} user demand evidence pricing",
            f"{base} market alternatives competitors",
            f"{base} time to first dollar case study",
        ],
        "risk-warning": [
            f"{base} complaints risk policy violation",
            f"{base} scam warning criticism",
            f"{base} hidden cost failure case",
        ],
        "investigation": [
            f"{base} official announcement source",
            f"{base} technical explanation evidence",
            f"{base} opposing views limitations",
        ],
    }
    return templates.get(report_type, templates["investigation"])


def angle_candidates(report_type: str) -> list[str]:
    mapping = {
        "tool-ledger": ["成本是否真的下降", "免费额度和爆账单风险", "替代方案值不值得换"],
        "platform-rules": ["规则变动影响谁", "普通技术人的合规边界", "平台红利和封号风险"],
        "case-study": ["可复制条件", "不可复制条件", "失败或成功背后的非技术因素"],
        "opportunity": ["最小验证路径", "真实需求和付费意愿", "停止信号和资金边界"],
        "risk-warning": ["营销话术拆解", "合规和资金风险", "普通人不该硬冲的原因"],
        "investigation": ["事实时间线", "基础概念澄清", "证据矛盾和影响边界"],
    }
    return mapping.get(report_type, mapping["investigation"])


def downstream_handoff(report: dict[str, Any], site: dict[str, Any]) -> dict[str, Any]:
    base = site["site_url"].rstrip("/")
    canonical_url = f"{base}/items/{report['id']}/"
    return {
        "package_version": "radar-handoff-v1",
        "canonical_url": canonical_url,
        "for_gpt_editor": {
            "brief": report["summary"] or report["reader_hook"],
            "title_seed": report["title"],
            "original_title": report.get("original_title", ""),
            "source_url": report["url"],
            "selection_dossier": report.get("selection_dossier", {}),
            "material_pack": report.get("material_pack", {}),
            "angle_candidates": angle_candidates(report["report_type"]),
            "must_keep": [
                "先交代来源和证据等级。",
                "明确哪些是已确认事实，哪些只是推断或待验证线索。",
                "保留普通读者入口，不把冷门技术写成圈内自嗨。"
            ],
            "must_not_claim": report["verification"]["do_not_claim"],
            "questions_to_resolve": report["verification"]["what_needs_followup"],
        },
        "for_research_loop": {
            "followup_queries": followup_queries_from_report(report),
            "evidence_gaps": report["verification"]["what_needs_followup"],
            "stop_conditions": [
                "找不到一手来源或近源证据。",
                "只能证明技术存在，无法证明需求、成本、规则或影响。",
                "所有高价值信息都来自营销页或无法复查的截图。"
            ],
        },
    }


def followup_queries_from_report(report: dict[str, Any]) -> list[str]:
    fake_item = SourceItem(
        id=report["id"],
        source_category=report["source_category"],
        source_name=report["source_name"],
        source_type=report["source_type"],
        title=report["title"],
        url=report["url"],
    )
    return followup_queries(fake_item, report["report_type"])


def topic_direction_for_item(item: SourceItem, report_type: str, site: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    directions = site.get("topic_directions", {})
    if not directions:
        return item.source_category, {"title": item.source_category, "short_title": item.source_category, "description": ""}

    text = f"{item.title} {item.summary} {item.source_name} {item.url}".lower()
    if any(word in text for word in ["freelance", "contract", "invoice", "chargeback", "debt", "lawsuit", "scam", "arbitrage", "外包", "接项目", "合同", "回款", "发票", "催收", "债务", "起诉", "骗局", "套利", "卖课"]):
        meta = directions.get("cashflow-risk")
        if meta:
            return "cashflow-risk", meta
    if any(word in text for word in ["google search", "seo", "ai seo", "ranking", "traffic", "recommendation", "policy", "license", "terms", "compliance", "公众号", "小红书", "视频号", "搜索", "流量", "推荐", "规则", "合规", "账号"]):
        meta = directions.get("traffic-rules")
        if meta:
            return "traffic-rules", meta
    if any(word in text for word in ["stripe", "shopify", "payment", "commerce", "cross-border", "global demand", "出海", "跨境", "支付", "收款", "独立站"]):
        meta = directions.get("cross-border")
        if meta:
            return "cross-border", meta
    if any(word in text for word in ["automation", "workflow", "assistant", "agentic", "template", "no-code", "n8n", "zapier", "个人助手", "自动化", "工作流", "实操", "办公", "内容生产", "健康助手", "客服", "运营工具"]):
        meta = directions.get("ai-practice")
        if meta:
            return "ai-practice", meta
    best_key = ""
    best_score = -1
    for key, meta in directions.items():
        score = 0
        if item.source_category in meta.get("source_categories", []):
            score += 4
            if item.source_category == "overseas_and_platforms" and key == "cross-border":
                score -= 2
        for keyword in meta.get("keywords", []):
            if keyword.lower() in text:
                score += 3
        if key == "traffic-rules" and report_type == "platform-rules":
            score += 2
        if key == "cross-border" and any(word in text for word in ["stripe", "shopify", "payment", "commerce", "cross-border", "global demand", "出海", "跨境", "支付", "收款", "独立站"]):
            score += 10
        if key == "ai-frontier" and any(word in text for word in ["ai", "llm", "agent", "model", "anthropic", "openai", "claude", "codex", "copilot", "frontier", "模型", "智能体"]):
            score += 6
        if key == "ai-practice" and any(word in text for word in ["automation", "workflow", "assistant", "agentic", "template", "no-code", "个人助手", "自动化", "工作流", "实操"]):
            score += 8
        if key == "traffic-rules" and any(word in text for word in ["policy", "license", "terms", "compliance", "regulation", "search", "seo", "traffic", "规则", "合规", "协议", "搜索", "流量", "推荐"]):
            score += 6
        if key == "indie-builder" and any(word in text for word in ["show hn", "producthunt", "product hunt", "v2ex", "mrr", "saas", "独立开发", "副业", "工具站", "开源"]):
            score += 5
        if key == "cashflow-risk" and any(word in text for word in ["freelance", "contract", "invoice", "chargeback", "debt", "lawsuit", "scam", "arbitrage", "外包", "合同", "回款", "催收", "债务", "骗局", "套利"]):
            score += 8
        if score > best_score:
            best_key = key
            best_score = score

    if not best_key:
        best_key = next(iter(directions))
    return best_key, directions[best_key]


def deepseek_triage_batch(items: list[SourceItem], site: dict[str, Any], policy: dict[str, Any]) -> list[RadarDecision]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return []
    sample = [
        {
            "id": item.id,
            "source_category": item.source_category,
            "source_name": item.source_name,
            "title": item.title,
            "url": item.url,
            "summary": item.summary[:220],
            "published_at": item.published_at,
        }
        for item in items
    ]
    body = {
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "temperature": 0.2,
        "max_tokens": 4200,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": (
                    "你是 Easton Radar 的信息初筛员。网站栏目按选题方向分类，报告类型只表示分析方法，不作为主栏目。"
                    "请只输出 JSON：{\"items\":[...]}。每项包含 id, decision(deep_dive|brief|skip), report_type, report_title"
                    "(investigation|opportunity|tool-ledger|platform-rules|case-study|risk-warning), score(0-100),"
                    "reader_hook, why_now, evidence_level(official|near_source|media|weak), reason, reject_reason,"
                    "collection_fit, investigation_direction, uncertainty_flags。\n"
                    "report_title 必须是中文 Radar 标题，可以保留产品名/公司名，但不能整句英文照搬原题。\n"
                    "硬规则：优先官方/一手/近源；反爬论坛抓不到就放弃；冷门技术没有普通读者入口就 skip；"
                    "先判断是否符合信息收集原则；符合才深挖证据；证据不足必须标记存疑，不能写成结论。\n"
                    f"报告类型规则：{json.dumps(policy['report_type_rules'], ensure_ascii=False)}\n"
                    f"下游交接要求：{json.dumps(policy.get('downstream_requirements', {}), ensure_ascii=False)}\n"
                    f"数据源分类：{json.dumps(site.get('source_categories', {}), ensure_ascii=False)}\n"
                    f"选题方向：{json.dumps(site.get('topic_directions', {}), ensure_ascii=False)}\n"
                    f"人设主线：{json.dumps(policy['persona_lines'], ensure_ascii=False)}\n"
                    f"待筛信息：{json.dumps(sample, ensure_ascii=False)}"
                ),
            }
        ],
    }
    try:
        req = urllib.request.Request(
            DEEPSEEK_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)
        rows = parsed if isinstance(parsed, list) else parsed.get("items", [])
        by_id = {item.id: item for item in items}
        decisions: list[RadarDecision] = []
        for row in rows:
            item = by_id.get(str(row.get("id", "")))
            if not item:
                continue
            fallback = heuristic_decision(item, site)
            decisions.append(
                RadarDecision(
                    item=item,
                    decision=row.get("decision", fallback.decision),
                    report_type=row.get("report_type", fallback.report_type),
                    report_title=row.get("report_title") or fallback.report_title,
                    score=int(row.get("score", fallback.score)),
                    reader_hook=row.get("reader_hook", fallback.reader_hook),
                    why_now=row.get("why_now", fallback.why_now),
                    evidence_level=row.get("evidence_level", fallback.evidence_level),
                    reason=row.get("reason", fallback.reason),
                    reject_reason=row.get("reject_reason", ""),
                    collection_fit=row.get("collection_fit", fallback.collection_fit),
                    investigation_direction=row.get("investigation_direction", fallback.investigation_direction),
                    uncertainty_flags=row.get("uncertainty_flags", fallback.uncertainty_flags) or fallback.uncertainty_flags,
                    traceability={**fallback.traceability, "heuristic": False, "model": body["model"]},
                )
            )
        return decisions
    except Exception as exc:
        print(f"DeepSeek triage batch failed, fallback this batch to heuristic: {exc}", file=sys.stderr)
        return []


def deepseek_triage(items: list[SourceItem], site: dict[str, Any], policy: dict[str, Any]) -> list[RadarDecision] | None:
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return None
    decisions: list[RadarDecision] = []
    batch_size = 25
    for offset in range(0, min(len(items), 100), batch_size):
        batch = items[offset:offset + batch_size]
        decisions.extend(deepseek_triage_batch(batch, site, policy))
    return decisions or None


def deepseek_triage_batch(items: list[SourceItem], site: dict[str, Any], policy: dict[str, Any]) -> list[RadarDecision]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return []
    sample = [
        {
            "id": item.id,
            "source_category": item.source_category,
            "source_name": item.source_name,
            "title": item.title,
            "url": item.url,
            "summary": item.summary[:160],
            "published_at": item.published_at,
        }
        for item in items
    ]
    body = {
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "temperature": 0.1,
        "max_tokens": 1800,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": (
                    "You are Easton Radar triage. Return compact JSON only.\n"
                    "Schema: {\"items\":[{\"id\":\"\",\"decision\":\"deep_dive|brief|skip\",\"report_type\":\"investigation|opportunity|tool-ledger|platform-rules|case-study|risk-warning\",\"score\":0,\"evidence_level\":\"official|near_source|media|weak\",\"reason\":\"\"}]}.\n"
                    "Keep reason under 24 Chinese chars. Do not output title, hook, why_now, collection_fit, investigation_direction, uncertainty_flags, markdown, or commentary.\n"
                    "Skip cold niche technical updates unless they affect cost, workflow, platform rules, income, risk, or ordinary tech-adjacent readers.\n"
                    f"Report type rules: {json.dumps(policy.get('report_type_rules', {}), ensure_ascii=False)}\n"
                    f"Topic directions: {json.dumps(site.get('topic_directions', {}), ensure_ascii=False)}\n"
                    f"Persona lines: {json.dumps(policy.get('persona_lines', []), ensure_ascii=False)}\n"
                    f"Items: {json.dumps(sample, ensure_ascii=False)}"
                ),
            }
        ],
    }
    try:
        req = urllib.request.Request(
            DEEPSEEK_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": USER_AGENT},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choice = data["choices"][0]
        finish_reason = choice.get("finish_reason", "")
        content = choice["message"]["content"].strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        if finish_reason == "length":
            raise json.JSONDecodeError("DeepSeek triage response truncated by max_tokens", content, max(0, len(content) - 1))
        parsed = json.loads(content)
        rows = parsed if isinstance(parsed, list) else parsed.get("items", [])
        by_id = {item.id: item for item in items}
        decisions: list[RadarDecision] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = by_id.get(str(row.get("id", "")))
            if not item:
                continue
            fallback = heuristic_decision(item, site)
            decisions.append(
                RadarDecision(
                    item=item,
                    decision=str(row.get("decision") or fallback.decision),
                    report_type=str(row.get("report_type") or fallback.report_type),
                    report_title=fallback.report_title,
                    score=int(row.get("score", fallback.score) or fallback.score),
                    reader_hook=fallback.reader_hook,
                    why_now=fallback.why_now,
                    evidence_level=str(row.get("evidence_level") or fallback.evidence_level),
                    reason=str(row.get("reason") or fallback.reason)[:80],
                    reject_reason=fallback.reject_reason,
                    collection_fit=fallback.collection_fit,
                    investigation_direction=fallback.investigation_direction,
                    uncertainty_flags=fallback.uncertainty_flags,
                    traceability={**fallback.traceability, "heuristic": False, "model": body["model"], "triage_batch_size": len(items)},
                )
            )
        return decisions
    except json.JSONDecodeError as exc:
        if len(items) > 1:
            mid = len(items) // 2
            print(f"DeepSeek triage JSON failed; retry split batch size={len(items)}: {exc}", file=sys.stderr)
            return deepseek_triage_batch(items[:mid], site, policy) + deepseek_triage_batch(items[mid:], site, policy)
        print(f"DeepSeek triage single item failed, fallback to heuristic: {items[0].title[:80]} | {exc}", file=sys.stderr)
        return []
    except Exception as exc:
        print(f"DeepSeek triage unavailable, fallback batch size={len(items)}: {exc}", file=sys.stderr)
        return []


def deepseek_triage(items: list[SourceItem], site: dict[str, Any], policy: dict[str, Any]) -> list[RadarDecision] | None:
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return None
    decisions: list[RadarDecision] = []
    for offset in range(0, min(len(items), 100), TRIAGE_BATCH_SIZE):
        decisions.extend(deepseek_triage_batch(items[offset:offset + TRIAGE_BATCH_SIZE], site, policy))
    return decisions or None


def compact_report_seed(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": report.get("title", ""),
        "original_title": report.get("original_title", ""),
        "url": report.get("url", ""),
        "summary": report.get("summary", "")[:900],
        "source_name": report.get("source_name", ""),
        "source_category": report.get("source_category", ""),
        "topic_direction": report.get("topic_direction", ""),
        "topic_direction_title": report.get("topic_direction_title", ""),
        "report_type": report.get("report_type", ""),
        "score": report.get("score", 0),
        "reader_hook": report.get("reader_hook", ""),
        "why_now": report.get("why_now", ""),
        "reason": report.get("reason", ""),
        "uncertainty_flags": report.get("uncertainty_flags", []),
        "traceability": report.get("traceability", {}),
    }


def fallback_pending_dossier(report: dict[str, Any], reason: str) -> dict[str, Any]:
    title = display_report_title(report)
    return {
        "schema": "topic-selection-dossier-v3",
        "generated_by": "fallback",
        "verdict": {
            "status": "待 LLM 判断",
            "label": "待判断",
            "reason": reason,
        },
        "topic_direction": report.get("topic_direction", ""),
        "topic_direction_title": report.get("topic_direction_title", ""),
        "report_type": report.get("report_type", ""),
        "core_question": f"这条线索是否值得继续做成选题：{title}",
        "why_this_topic_matters": report.get("reader_hook", ""),
        "fact_summary": [
            f"原始来源记录到这条线索：{report.get('original_title', title)}",
            "本次没有完成 LLM 补证和最终判断，因此不能当作可信选题报告。",
        ],
        "evidence_table": [
            {
                "source": report.get("source_name", ""),
                "url": report.get("url", ""),
                "supports": "只能证明线索入口存在。",
                "reliability": report.get("evidence_level", "unknown"),
            }
        ],
        "logic_closure": "证据链尚未闭合。需要先让 LLM 明确应查材料，再执行检索和交叉验证。",
        "writeable_angles": [],
        "missing_basics": ["核心概念、影响对象、成本边界、事实时间线仍待补齐。"],
        "missing_materials": ["官方/近源材料、反方材料、案例或数据材料仍待补齐。"],
        "not_claimable": ["不能写成已验证机会。", "不能写成老花已经实操。", "不能直接给可冲结论。"],
        "followup_queries": followup_queries_from_report(report),
        "stop_conditions": ["补证后仍只有单一来源。", "无法说清楚和老花人设或读者需求的关系。"],
        "confidence": 0,
    }


def plan_topic_research(report: dict[str, Any], site: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any] | None:
    prompt = (
        "你是 Easton Radar 的选题调查编辑。先不要写报告，只判断这条线索还需要查什么。\n"
        "目标：给后续 GPT 写作应用准备可信素材包，而不是把通用规则写进网页。\n"
        "请只输出 JSON：{\"core_question\":\"\",\"must_verify\":[],\"search_queries\":[],\"best_sources_to_find\":[],"
        "\"expert_challenge_points\":[],\"do_not_claim_yet\":[],\"can_publish_as_radar_if_missing\":\"\",\"downstream_materials_needed\":[]}。\n"
        "要求：search_queries 给 4-8 个具体搜索词；优先官方、近源、价格页、文档、GitHub、监管/平台规则、真实案例、反方材料。"
        "如果这个题太窄、太冷、和读者关系弱，要明确写出来。"
    )
    data = deepseek_json(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "site_topic_directions": site.get("topic_directions", {}),
                "persona_lines": policy.get("persona_lines", []),
                "fatal_filters": policy.get("fatal_filters", []),
                "candidate": compact_report_seed(report),
            }, ensure_ascii=False)},
        ],
        max_tokens=2600,
        timeout=80,
    )
    return data if isinstance(data, dict) else None


def collect_research_evidence(plan: dict[str, Any], max_queries: int = 6) -> list[dict[str, str]]:
    queries: list[str] = []
    for query in plan.get("search_queries", []):
        if isinstance(query, str):
            queries.append(query)
    for item in plan.get("best_sources_to_find", []):
        if isinstance(item, dict) and item.get("query"):
            queries.append(str(item["query"]))
    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        key = query.strip().lower()
        if key and key not in seen:
            seen.add(key)
            deduped.append(query.strip())
    search_results: list[dict[str, str]] = []
    for query in deduped[:max_queries]:
        search_results.extend(search_web(query, limit=5))
    provider_counts: dict[str, int] = {}
    for result in search_results:
        provider = result.get("provider", "unknown")
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
    provider_text = ", ".join(f"{key}:{value}" for key, value in sorted(provider_counts.items())) or "none"
    info(f"Research search complete: queries={len(deduped[:max_queries])}, results={len(search_results)}, providers={provider_text}")
    evidence = fetch_evidence_pages(search_results)
    info(f"Evidence fetch complete: pages={len(evidence)}, fetched={sum(1 for item in evidence if item.get('fetched_text'))}")
    return evidence


def confidence_score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if 0 < number <= 1:
        return number * 100
    return number


def enforce_evidence_gate(dossier: dict[str, Any], evidence: list[dict[str, str]]) -> dict[str, Any]:
    if evidence:
        return dossier
    verdict = dossier.setdefault("verdict", {})
    verdict["status"] = "暂缓"
    verdict["label"] = "暂缓"
    verdict["reason"] = "补证搜索没有拿到可用结果，当前不能给可选或高可信判断。"
    dossier["confidence"] = min(confidence_score(dossier.get("confidence", 0)), 30)
    missing = dossier.setdefault("missing_materials", [])
    if isinstance(missing, list):
        missing.append("补证搜索结果为 0，需要先解决搜索后端或改用官方/近源材料补证。")
    not_claimable = dossier.setdefault("not_claimable", [])
    if isinstance(not_claimable, list):
        not_claimable.append("不能在无补证结果时声称该选题已经具备可写条件。")
    return dossier


def compose_topic_dossier(report: dict[str, Any], site: dict[str, Any], policy: dict[str, Any], plan: dict[str, Any], evidence: list[dict[str, str]], previous: dict[str, Any] | None = None) -> dict[str, Any] | None:
    prompt = (
        "你是 Easton Radar 的最终选题质检员。你要基于原始线索、补证计划、搜索结果和抓取正文，生成一份自然可读的选题报告。\n"
        "这不是公众号正文，不要写口水开头，不要强行写“如果是我我会怎么做”。\n"
        "报告必须回答：这题值不值得选，为什么，事实是否清楚，证据够不够，逻辑能否闭环，和老花/读者有什么关系，后续能写哪些方向，还缺哪些材料。\n"
        "不要把通用判断规则原样写出来。所有判断都要落到这个具体题上。\n"
        "请只输出 JSON：{\"schema\":\"topic-selection-dossier-v3\",\"generated_by\":\"deepseek\","
        "\"verdict\":{\"status\":\"可选|观察|暂缓|放弃\",\"label\":\"可选|观察|暂缓|放弃\",\"reason\":\"\"},"
        "\"core_question\":\"\",\"why_this_topic_matters\":\"\",\"fact_summary\":[],"
        "\"timeline\":[],\"evidence_table\":[{\"source\":\"\",\"url\":\"\",\"supports\":\"\",\"reliability\":\"official|near_source|media|weak\"}],"
        "\"logic_closure\":\"\",\"writeable_angles\":[{\"angle\":\"\",\"why\":\"\",\"needs\":\"\"}],"
        "\"missing_basics\":[],\"missing_materials\":[],\"not_claimable\":[],\"followup_queries\":[],"
        "\"additional_search_queries\":[],\"stop_conditions\":[],\"confidence\":0}。\n"
        "如果证据不足，verdict 不得写可选；如果需要更多证据，把 additional_search_queries 写清楚。"
    )
    data = deepseek_json(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "site_topic_directions": site.get("topic_directions", {}),
                "persona_lines": policy.get("persona_lines", []),
                "candidate": compact_report_seed(report),
                "research_plan": plan,
                "evidence": evidence,
                "previous_dossier": previous or {},
            }, ensure_ascii=False)},
        ],
        max_tokens=5200,
        timeout=110,
    )
    if not isinstance(data, dict):
        return None
    data.setdefault("schema", "topic-selection-dossier-v3")
    data.setdefault("generated_by", "deepseek")
    data.setdefault("topic_direction", report.get("topic_direction", ""))
    data.setdefault("topic_direction_title", report.get("topic_direction_title", ""))
    data.setdefault("report_type", report.get("report_type", ""))
    return data


def enrich_selection_dossier(report: dict[str, Any], site: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        info(f"Skip LLM dossier: missing DeepSeek key | {report.get('title', '')}")
        return fallback_pending_dossier(report, "本次运行没有 DeepSeek API Key，只能保留线索，不能生成可信选题判断。")
    info(f"LLM dossier start: {report.get('title', '')}")
    plan = plan_topic_research(report, site, policy)
    if not plan:
        info(f"LLM dossier failed at research plan: {report.get('title', '')}")
        return fallback_pending_dossier(report, "DeepSeek 没有成功生成补证计划，暂不输出可信选题判断。")
    info(f"Research plan ready: queries={len(plan.get('search_queries', []))}, source_targets={len(plan.get('best_sources_to_find', []))}")
    evidence = collect_research_evidence(plan)
    dossier = compose_topic_dossier(report, site, policy, plan, evidence)
    extra_queries = []
    if dossier:
        extra_queries = [q for q in dossier.get("additional_search_queries", []) if isinstance(q, str) and q.strip()]
    low_confidence = bool(dossier and confidence_score(dossier.get("confidence", 0)) < 55)
    no_search_evidence = bool(dossier and not evidence)
    if extra_queries and (low_confidence or no_search_evidence):
        info(f"Evidence/low-confidence dossier, running extra search: confidence={dossier.get('confidence', '')}, evidence={len(evidence)}, extra_queries={len(extra_queries[:4])}")
        extra_plan = {"search_queries": extra_queries[:4], "best_sources_to_find": []}
        evidence.extend(collect_research_evidence(extra_plan, max_queries=4))
        dossier = compose_topic_dossier(report, site, policy, plan, evidence, previous=dossier)
    if not dossier:
        info(f"LLM dossier failed at final composition: {report.get('title', '')}")
        return fallback_pending_dossier(report, "DeepSeek 没有成功生成最终选题报告，暂不输出可信结论。")
    dossier = enforce_evidence_gate(dossier, evidence)
    dossier["research_plan"] = plan
    dossier["research_evidence"] = evidence
    verdict = dossier.get("verdict", {}) if isinstance(dossier, dict) else {}
    info(f"LLM dossier done: verdict={verdict.get('status', '')}, confidence={dossier.get('confidence', '')}, evidence={len(evidence)}")
    return dossier


def report_template_key(report: dict[str, Any]) -> str:
    topic = report.get("topic_direction", "")
    report_type = report.get("report_type", "")
    text = f"{report.get('title', '')} {report.get('original_title', '')} {report.get('summary', '')}".lower()
    if topic == "ai-frontier" and any(word in text for word in ["release", "launch", "update", "available", "announce", "introduce", "new", "发布", "更新", "上线", "推出"]):
        return "ai_major_update"
    if topic == "ai-practice":
        return "ai_practice"
    if topic in {"indie-builder", "cross-border"} or report_type in {"opportunity", "case-study"}:
        return "business_teardown"
    if topic == "traffic-rules" or report_type == "platform-rules":
        return "platform_rule_change"
    if topic == "cashflow-risk" or report_type == "risk-warning":
        return "risk_case"
    if report_type == "tool-ledger":
        return "tool_cost_ledger"
    return "general_investigation"


def material_dimensions(template: str) -> list[dict[str, str]]:
    templates: dict[str, list[tuple[str, str]]] = {
        "ai_major_update": [
            ("时间线", "这次更新最早从哪里出现，发布时间、开放范围、后续可能变化是什么。"),
            ("本次更新摘要", "新增了什么、改变了什么、没有改变什么，别只写产品名。"),
            ("影响对象", "普通用户、开发者、独立开发者、企业团队分别会受到什么影响。"),
            ("成本和门槛", "价格、额度、API、账号、地区、设备、学习成本是否变化。"),
            ("替代和竞争", "和已有工具或模型相比，替代了什么，没替代什么。"),
            ("老花切入口", "从程序员、AI 工具重度用户、副业工具链角度能拆出什么。"),
        ],
        "ai_practice": [
            ("具体需求", "这个 AI 场景到底解决谁的什么问题，是真需求还是演示需求。"),
            ("输入输出", "需要哪些数据、素材、账号、工具，输出物是否可直接使用。"),
            ("工作流步骤", "从触发、处理、审核到交付，最小流程怎么跑。"),
            ("工具组合", "可以用哪些现成工具或 API，哪些环节必须人工兜底。"),
            ("验证成本", "一天内能否做最小验证，时间、API、订阅和维护成本是多少。"),
            ("失败信号", "什么时候说明这个自动化不值得继续做。"),
        ],
        "business_teardown": [
            ("需求真实性", "谁会用，痛点是否高频，是否愿意付费或持续使用。"),
            ("用户和场景", "目标用户是谁，使用场景在哪里，国内/海外是否不同。"),
            ("流量来源", "SEO、社区、平台推荐、付费投放、内容引流哪条可能成立。"),
            ("变现路径", "广告、订阅、一次性付费、Affiliate、服务费哪条更现实。"),
            ("技术和运营", "技术实现难不难，真正麻烦的是维护、客服、销售还是合规。"),
            ("最小 MVP", "如果只验证一件事，应该验证需求、流量、付费还是交付。"),
            ("停止信号", "什么数据出现就应该停，不要继续堆功能。"),
        ],
        "platform_rule_change": [
            ("规则变化", "平台改了什么，原规则是什么，新规则影响哪部分人。"),
            ("影响范围", "影响流量、账号、支付、分发、SEO、AI 内容还是开发者生态。"),
            ("证据边界", "哪些来自官方，哪些只是社区推断或媒体解释。"),
            ("应对材料", "后续写文需要准备官方原文、案例、反例、时间点和执行口径。"),
            ("风险信号", "哪些行为可能被限流、封号、拒付、降权或触发合规问题。"),
        ],
        "risk_case": [
            ("诱饵话术", "它是怎么让人上头的，收益、截图、案例、焦虑分别怎么包装。"),
            ("利益关系", "谁赚钱，谁承担成本，卖铲子、卖课、卖服务还是卖工具。"),
            ("成本清单", "钱、时间、账号、合规、机会成本分别在哪里。"),
            ("证据缺口", "哪些关键事实还没证据，哪些说法不能当真。"),
            ("避坑判断", "普通技术人应该查什么，什么信号出现就别碰。"),
        ],
        "tool_cost_ledger": [
            ("能力变化", "工具/API/云服务新增了什么能力，适合什么任务。"),
            ("价格和额度", "价格、免费额度、API 限制、地区限制和隐藏成本是什么。"),
            ("替代方案", "能否用开源、低价模型、传统脚本或手工方案替代。"),
            ("爆账单风险", "哪类调用、任务规模或配置最容易造成成本失控。"),
            ("老花用法", "如果只是作为 AI 工具重度用户，应该怎么观察和试用。"),
        ],
        "general_investigation": [
            ("核心事实", "这件事目前确认了什么。"),
            ("证据链", "证据来自哪里，是否一手，是否可复查。"),
            ("影响边界", "影响谁，不影响谁，别扩大。"),
            ("存疑点", "哪些地方还不能下结论。"),
            ("可写切口", "后续写博客或公众号时，最自然的入口是什么。"),
        ],
    }
    return [{"name": name, "how_to_use": how} for name, how in templates.get(template, templates["general_investigation"])]


def material_pack(report: dict[str, Any]) -> dict[str, Any]:
    template = report_template_key(report)
    original_title = report.get("original_title", "")
    published = report.get("published_at") or "原文未提供发布时间"
    fetched = report.get("fetched_at") or ""
    timeline = [
        {"time": published, "event": f"原始来源发布/收录线索：{original_title}", "evidence": report.get("url", "")},
        {"time": fetched, "event": "Easton Radar 抓取并归档该线索。", "evidence": report.get("feed_url", report.get("url", ""))},
        {"time": "待补证", "event": "继续查官方文档、价格页、案例、反方观点或真实使用反馈。", "evidence": "见 followup_queries"},
    ]
    evidence_map = [
        {
            "source": report.get("source_name", ""),
            "url": report.get("url", ""),
            "supports": "原始线索存在、标题、来源和基础事实入口。",
            "evidence_level": report.get("evidence_level", ""),
            "confidence": 0.8 if report.get("evidence_level") in {"official", "near_source"} else 0.55,
        }
    ]
    title = display_report_title(report)
    return {
        "template": template,
        "topic_direction": report.get("topic_direction", ""),
        "topic_direction_title": report.get("topic_direction_title", ""),
        "report_type": report.get("report_type", ""),
        "must_answer": [d["name"] for d in material_dimensions(template)],
        "timeline": timeline,
        "fact_sheet": [
            f"原始标题：{original_title}",
            f"来源：{report.get('source_name', '')}",
            f"证据等级：{report.get('evidence_level', '')}",
            f"当前判断：{report.get('collection_fit', '')}",
        ],
        "evidence_map": evidence_map,
        "analysis_dimensions": material_dimensions(template),
        "writing_materials": {
            "title_seeds": [
                title,
                f"{title}，和普通技术人有什么关系",
                f"{title}：机会、成本和风险先拆清楚",
            ],
            "reader_questions": [
                "这事和我有什么关系？",
                "它到底改变了什么，还是只是包装换了个名字？",
                "如果我只是懂一点技术，能不能低成本验证？",
                "最大的坑是技术，还是流量、合规、成本、回款和交付？",
            ],
            "opening_hooks": [
                f"今天这条线索看起来是 {report.get('report_type_title', '报告')}，但我更关心它能不能成为一个真正有用的选题。",
                "先别急着下结论，咱们还是先看来源、证据和利益关系。",
            ],
            "angle_seeds": angle_candidates(report.get("report_type", "investigation")),
        },
        "evidence_gaps": report["verification"]["what_needs_followup"],
        "not_claimable": report["verification"]["do_not_claim"],
        "followup_queries": followup_queries_from_report(report),
        "stop_conditions": [
            "找不到一手来源或近源证据。",
            "只能证明概念存在，无法证明需求、成本、规则、收入或影响。",
            "需要用大量想象补齐关键事实，读者看完只会觉得虚。",
        ],
    }


def topic_selection_rules(topic_key: str) -> dict[str, list[str]]:
    rules: dict[str, dict[str, list[str]]] = {
        "ai-frontier": {
            "selection_questions": [
                "这是不是明确发生的 AI 能力、价格、生态或平台规则变化，而不是普通产品小更新？",
                "普通读者能不能听懂它改变了什么，程序员能不能看出技术和工具链价值？",
                "有没有官方公告、文档、价格页、发布说明或可信近源材料？",
                "能不能讲清楚发布时间线、能力变化、影响对象、成本门槛和替代关系？",
            ],
            "valuable_signals": ["官方发布", "影响开发者或高频 AI 用户", "改变成本结构", "改变工作流", "有明确对比对象"],
            "reject_signals": ["只有产品名", "只是 SDK 小版本", "没有大众钩子", "只能写成又一个工具更新"],
            "writeable_angles": ["这次更新到底改变了什么", "普通技术人该不该关注", "成本和工具链会不会变化", "它替代了谁又替代不了谁"],
            "missing_basics": ["产品/模型/Agent 基础概念", "本次更新前后的差异", "开放范围和使用门槛", "价格、额度和地区限制"],
            "missing_materials": ["官方公告", "文档或 changelog", "价格页", "真实使用反馈", "竞品对比材料"],
        },
        "ai-practice": {
            "selection_questions": [
                "它是不是一个具体需求场景，而不是空泛地说 AI 很强？",
                "读者能不能想象自己也有类似需求，比如个人助手、办公、健康、内容、客服或运营？",
                "能不能拆出输入、处理流程、输出、人工兜底和失败信号？",
                "能不能低成本验证，而不是必须完整开发一个大系统？",
            ],
            "valuable_signals": ["需求具体", "输入输出清楚", "能一两天做 MVP", "有人工兜底", "非技术读者也能理解"],
            "reject_signals": ["只有工具名", "只有炫技", "需求不真实", "必须编一堆操作细节"],
            "writeable_angles": ["如果是我会怎么把需求说清楚", "AI 能放大的到底是哪一段", "最低成本验证流程", "哪些环节必须人工兜底"],
            "missing_basics": ["具体使用者是谁", "输入数据从哪里来", "输出物怎么验收", "失败后怎么回滚"],
            "missing_materials": ["工具文档", "示例工作流", "输入输出样例", "成本估算", "失败案例"],
        },
        "cross-border": {
            "selection_questions": [
                "它是不是会影响出海、收款、支付、合规、独立站或海外平台运营？",
                "国内普通技术人有没有低成本理解或试错入口？",
                "钱从哪里来，手续费、汇率、税务、账号和合规边界是否能说清楚？",
                "有没有官方规则、平台文档、费率页或真实案例？",
            ],
            "valuable_signals": ["影响收款或支付", "影响出海基础设施", "有明确平台规则", "有费率和成本材料", "有合规边界"],
            "reject_signals": ["只是一条海外公司新闻", "没有中国技术人切口", "只靠 Reddit 口述", "合规风险说不清"],
            "writeable_angles": ["普通技术人能不能用", "成本到底省在哪里", "合规和账号风险在哪里", "最小验证应该查哪些材料"],
            "missing_basics": ["支付链路", "手续费和汇率", "税务/合规边界", "平台账号要求", "地域限制"],
            "missing_materials": ["官方费率页", "服务条款", "合规说明", "真实用户案例", "反方风险材料"],
        },
        "indie-builder": {
            "selection_questions": [
                "它是不是一个真实项目、开源项目、工具站、SaaS 或副业案例？",
                "需求、用户、流量、变现、交付和运营能不能拆清楚？",
                "技术是不是只是其中一环，非技术门槛能不能说出来？",
                "有没有数据、代码、用户反馈、收入证据或失败证据？",
            ],
            "valuable_signals": ["真实项目", "有用户或数据", "能拆需求和变现", "技术人有切口", "失败信号清楚"],
            "reject_signals": ["只有 idea", "只有 GitHub 星数", "没有用户证据", "只能强行写我会怎么做"],
            "writeable_angles": ["技术之外真正难的是什么", "这个项目为什么有人用", "流量和变现能不能闭环", "最小验证应该验证哪一件事"],
            "missing_basics": ["目标用户", "核心需求", "流量来源", "收费方式", "运营和客服成本"],
            "missing_materials": ["项目仓库", "产品页面", "用户反馈", "收入或订阅证据", "竞品和失败案例"],
        },
        "traffic-rules": {
            "selection_questions": [
                "它是不是平台规则、搜索、推荐、账号、SEO、AI SEO 或内容分发生态变化？",
                "它会不会影响公众号、小红书、视频号、博客、独立站或后续变现？",
                "规则来自官方、近源实测还是社区猜测？证据边界能不能标清？",
                "能不能形成时间线、规则变化、影响范围和应对材料？",
            ],
            "valuable_signals": ["官方规则变化", "影响流量和账号", "影响内容分发", "有实测材料", "有明确应对边界"],
            "reject_signals": ["只有玄学猜测", "只有营销号二手解读", "没有平台或账号关系", "不能落到内容生产"],
            "writeable_angles": ["平台到底改了什么", "对普通创作者和技术人有什么影响", "哪些行为可能失效", "哪些材料还不能当结论"],
            "missing_basics": ["原规则", "新规则", "影响对象", "执行时间", "处罚或收益机制"],
            "missing_materials": ["官方公告", "帮助中心", "社区实测", "流量数据", "反例和边界案例"],
        },
        "cashflow-risk": {
            "selection_questions": [
                "它是不是会影响技术人的钱、合同、回款、债务、催收、外包、骗局或套利风险？",
                "利益关系能不能拆清楚：谁赚钱，谁承担成本，谁承担法律和时间风险？",
                "有没有合同、条款、真实案例、监管材料或可核验事实？",
                "能不能给出避坑判断，而不是制造焦虑？",
            ],
            "valuable_signals": ["利益关系清楚", "有真实案例", "能帮读者避坑", "成本和责任边界明确", "和技术人现金流相关"],
            "reject_signals": ["只有情绪", "只有截图", "只有吓唬人", "无法核验", "和技术人关系弱"],
            "writeable_angles": ["钱从哪里来又从哪里没掉", "技术人接项目最容易忽略什么", "什么信号出现就该停", "为什么不能只看表面收益"],
            "missing_basics": ["合同边界", "回款路径", "责任归属", "税务和发票", "法律或平台规则"],
            "missing_materials": ["条款原文", "案例材料", "监管/法院/平台资料", "费用清单", "反方说明"],
        },
    }
    return rules.get(topic_key, {
        "selection_questions": [
            "这件事到底是什么？",
            "它和老花的人设、读者需求、技术人视角有什么关系？",
            "事实是否清楚，证据是否可靠，逻辑能不能闭环？",
        ],
        "valuable_signals": ["事实清楚", "读者能理解", "有证据", "能形成判断"],
        "reject_signals": ["只能泛泛而谈", "没有证据", "没有读者关系"],
        "writeable_angles": ["事实拆解", "价值判断", "风险边界"],
        "missing_basics": ["核心概念", "影响对象", "证据边界"],
        "missing_materials": ["一手来源", "近源证据", "反方材料"],
    })


def selection_verdict(report: dict[str, Any]) -> dict[str, str]:
    score = int(report.get("score", 0))
    evidence = report.get("evidence_level", "")
    decision = report.get("decision", "")
    gaps = len(report.get("uncertainty_flags", []))
    if decision == "deep_dive" and evidence in {"official", "near_source"} and score >= 63 and gaps <= 3:
        return {
            "status": "可进入选题池",
            "label": "可选",
            "reason": "当前线索有明确来源、读者关系和分析空间，可以作为候选选题继续补证。",
        }
    if decision == "deep_dive" or score >= 58:
        return {
            "status": "待补证观察",
            "label": "观察",
            "reason": "线索有方向价值，但事实、概念、案例或成本材料还不足，暂时不能直接写成正文。",
        }
    return {
        "status": "不建议写成正文",
        "label": "暂缓",
            "reason": "当前材料不足以支撑一个有用选题，最多保留为观察或后续检索线索。",
    }


def selection_dossier(report: dict[str, Any]) -> dict[str, Any]:
    topic_key = report.get("topic_direction", "")
    rules = topic_selection_rules(topic_key)
    verdict = selection_verdict(report)
    evidence = report.get("evidence_level", "")
    summary = report.get("summary", "")
    confirmed = (report.get("verification", {}) or {}).get("what_is_confirmed", [])
    followup = (report.get("verification", {}) or {}).get("what_needs_followup", [])
    do_not_claim = (report.get("verification", {}) or {}).get("do_not_claim", [])
    original_title = report.get("original_title", "")
    title = display_report_title(report)
    fact_status = "基本清楚" if original_title and report.get("url") else "事实入口不足"
    summary_status = "有摘要" if summary else "缺少原文摘要"
    closure = [
        {
            "node": "事件是否存在",
            "status": "已确认" if report.get("url") else "缺证据",
            "note": f"来源入口：{report.get('source_name', '')}",
        },
        {
            "node": "和读者是否有关",
            "status": "初步成立" if report.get("reader_hook") else "缺判断",
            "note": report.get("reader_hook", ""),
        },
        {
            "node": "事实是否清楚",
            "status": fact_status,
            "note": f"原始标题：{original_title}",
        },
        {
            "node": "材料是否足够写正文",
            "status": "不足，需要补证" if verdict["label"] != "可选" else "可以继续扩写",
            "note": "Radar 只负责给出选题档案，不在证据不足时强行生成公众号正文。",
        },
    ]
    return {
        "schema": "topic-selection-dossier-v2",
        "verdict": verdict,
        "topic_direction": topic_key,
        "topic_direction_title": report.get("topic_direction_title", ""),
        "report_type": report.get("report_type", ""),
        "core_question": f"这条线索能不能成为一个值得老花继续写的选题：{title}",
        "human_judgment_path": [
            "先确认这件事是不是真的发生，而不是只看标题兴奋。",
            "再确认它和读者有什么关系：能不能帮读者理解机会、成本、规则、坑或工具变化。",
            "然后看证据是否够：有没有官方/近源材料，是否需要二次补证。",
            "再看逻辑是否闭环：事实、原因、影响、边界、可写角度能不能连起来。",
            "最后决定：可选、观察、暂缓，而不是把所有线索都写成同一套报告。",
        ],
        "selection_questions": rules["selection_questions"],
        "value_signals": rules["valuable_signals"],
        "reject_signals": rules["reject_signals"],
        "topic_value_assessment": [
            {"question": "这是什么？", "judgment": original_title or title},
            {"question": "和读者有什么关系？", "judgment": report.get("reader_hook", "暂无明确读者关系")},
            {"question": "为什么现在看？", "judgment": report.get("why_now", "暂无明确时间价值")},
            {"question": "事实清楚吗？", "judgment": fact_status},
            {"question": "材料可靠吗？", "judgment": f"当前证据等级：{evidence}"},
            {"question": "逻辑能闭环吗？", "judgment": "需要补齐材料后才能闭环" if followup else "当前基础链路可继续扩写"},
        ],
        "fact_clarity": {
            "status": fact_status,
            "summary_status": summary_status,
            "confirmed": confirmed,
            "uncertainty": report.get("uncertainty_flags", []),
        },
        "evidence_reliability": {
            "level": evidence,
            "primary_source": report.get("url", ""),
            "source_name": report.get("source_name", ""),
            "cross_check_status": "待二次交叉验证",
            "weak_points": report.get("uncertainty_flags", []) or ["当前只有基础来源，仍需补充反方和近源材料。"],
        },
        "logic_closure": closure,
        "writeable_angles": [
            {
                "angle": angle,
                "why": "这个角度能服务读者判断，而不是单纯复述新闻。",
                "needs": "补齐事实、概念、证据、成本和边界后再写。",
            }
            for angle in rules["writeable_angles"]
        ],
        "missing_basics": rules["missing_basics"],
        "missing_materials": rules["missing_materials"] + followup,
        "not_claimable": do_not_claim,
        "followup_queries": followup_queries_from_report(report),
        "stop_conditions": [
            "找不到一手来源或近源证据。",
            "无法说明这件事和读者有什么关系。",
            "关键概念、成本、合规、收益或影响对象说不清。",
            "逻辑必须靠想象补齐，写出来只会像假大空。",
        ],
    }


def material_pack(report: dict[str, Any]) -> dict[str, Any]:
    return selection_dossier(report)


def build_report(decision: RadarDecision, site: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    item = decision.item
    report_id = f"{now_bj().strftime('%Y%m%d')}-{slugify(item.title)}"
    report_type_meta = site["report_types"][decision.report_type]
    source_title = site.get("source_categories", {}).get(item.source_category, item.source_category)
    source_name = display_source_name(item)
    topic_key, topic_meta = topic_direction_for_item(item, decision.report_type, site)
    report = {
        "id": report_id,
        "title": decision.report_title,
        "original_title": item.title,
        "url": item.url,
        "summary": item.summary,
        "source_category": item.source_category,
        "source_category_title": source_title,
        "topic_direction": topic_key,
        "topic_direction_title": topic_meta.get("title", topic_key),
        "topic_direction_short_title": topic_meta.get("short_title", topic_meta.get("title", topic_key)),
        "topic_direction_description": topic_meta.get("description", ""),
        "source_name": source_name,
        "original_source_name": item.source_name,
        "source_type": item.source_type,
        "feed_url": item.feed_url,
        "published_at": item.published_at,
        "fetched_at": item.fetched_at,
        "decision": decision.decision,
        "report_type": decision.report_type,
        "report_type_title": report_type_meta["title"],
        "score": decision.score,
        "reader_hook": decision.reader_hook,
        "why_now": decision.why_now,
        "evidence_level": decision.evidence_level,
        "source_priority": source_priority(item, decision.evidence_level),
        "reason": decision.reason,
        "collection_fit": decision.collection_fit,
        "investigation_direction": decision.investigation_direction,
        "uncertainty_flags": decision.uncertainty_flags,
        "facts": [
            {
                "claim": f"{source_name} 发布/收录了这条原始线索：{item.title}",
                "type": "confirmed_fact",
                "source_url": item.url,
                "confidence": 0.8 if decision.evidence_level in {"official", "near_source"} else 0.55
            }
        ],
        "sources": [
            {
                "title": source_name,
                "url": item.url,
                "source_type": decision.evidence_level,
                "used_for": "原始线索和事实入口"
            }
        ],
        "traceability": decision.traceability,
        "verification": {
            "what_is_confirmed": [
                "标题、来源 URL、来源类型、抓取时间已记录。",
                "该条线索来自稳定公开源，而不是强反爬论坛或截图转述。"
            ],
            "what_needs_followup": [
                "继续追官方文档、价格页、GitHub 仓库、真实用户案例或反方证据。",
                "确认成本、门槛、合规、平台规则或岗位影响的具体边界。",
                "把所有无证据、弱证据和推断点显式标记，等待补证后再升级结论。"
            ],
            "expert_challenge_points": [
                "不能把单条线索写成已验证机会。",
                "不能把技术可实现直接推导为商业可赚钱。",
                "涉及价格、收益、比例时必须继续找来源或公式。"
            ],
            "do_not_claim": [
                "不要声称老花已经实操验证。",
                "不要声称普通人都能复制。",
                "不要在证据不足时给完整行动方案。"
            ]
        },
        "persona_connection": {
            "line": "程序员技术视角下的信息差、成本、平台规则和副业机会判断。",
            "why_it_matters": decision.reader_hook,
            "fit_for_radar": decision.decision == "deep_dive"
        }
    }
    report["source_assessment"] = source_access_assessment(item)
    report["evidence_dossier"] = evidence_dossier(report)
    report["selection_dossier"] = enrich_selection_dossier(report, site, policy)
    report["material_pack"] = report["selection_dossier"]
    report["downstream_handoff"] = downstream_handoff(report, site)
    return report


def select_reports(decisions: list[RadarDecision], site: dict[str, Any], archive: dict[str, Any], batch_id: str, limit: int = MAX_REPORTS_PER_BATCH) -> tuple[list[RadarDecision], list[dict[str, str]]]:
    eligible = [d for d in decisions if d.decision == "deep_dive" and d.score >= MIN_DEEP_DIVE_SCORE]
    eligible.extend(d for d in decisions if d.decision == "brief" and d.score >= MIN_BRIEF_SCORE)
    if not eligible:
        eligible = [d for d in decisions if d.decision != "skip" and d.score >= MIN_BRIEF_SCORE]
    selected: list[RadarDecision] = []
    category_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    source_host_counts: dict[str, int] = {}
    seen_fingerprints: set[str] = set()
    duplicate_skips: list[dict[str, str]] = []

    for decision in eligible:
        category = decision.item.source_category
        topic_key, _ = topic_direction_for_item(decision.item, decision.report_type, site)
        source_host = urllib.parse.urlparse(decision.item.url).netloc.lower()
        fingerprint = topic_fingerprint(decision.report_title or decision.item.title)
        if fingerprint in seen_fingerprints:
            duplicate_skips.append({"title": decision.report_title or decision.item.title, "url": decision.item.url, "reason": "本批次相似选题已入池"})
            continue
        duplicate, reason = is_duplicate_topic(decision, site, archive, batch_id)
        if duplicate:
            duplicate_skips.append({"title": decision.report_title or decision.item.title, "url": decision.item.url, "reason": reason})
            continue
        cap = SOURCE_CATEGORY_REPORT_CAPS.get(category, limit)
        if category_counts.get(category, 0) >= cap:
            continue
        if topic_counts.get(topic_key, 0) >= MAX_REPORTS_PER_TOPIC:
            continue
        if source_host_counts.get(source_host, 0) >= MAX_REPORTS_PER_SOURCE_HOST:
            continue
        selected.append(decision)
        category_counts[category] = category_counts.get(category, 0) + 1
        topic_counts[topic_key] = topic_counts.get(topic_key, 0) + 1
        source_host_counts[source_host] = source_host_counts.get(source_host, 0) + 1
        seen_fingerprints.add(fingerprint)
        if len(selected) >= limit:
            return selected, duplicate_skips

    return selected, duplicate_skips


class StaticSite:
    def __init__(self, site: dict[str, Any]) -> None:
        self.site = site
        self.base_url = site["site_url"].rstrip("/")

    def write_text(self, rel: str, text: str) -> None:
        path = SITE_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def write_page(self, rel: str, title: str, body: str, description: str | None = None) -> None:
        self.write_text(rel, self.layout(title, body, description or self.site["description"]))

    def write_assets(self) -> None:
        self.write_text("assets/style.css", STYLE)
        self.write_text("favicon.svg", FAVICON_SVG)

    def layout(self, title: str, body: str, description: str) -> str:
        nav = "".join(f'<a href="{item["href"]}">{html.escape(item["label"])}</a>' for item in self.site["nav"])
        verify = self.site.get("google_site_verification", "")
        adsense = self.site.get("adsense_client", "")
        verify_meta = f'<meta name="google-site-verification" content="{html.escape(verify)}">\n' if verify else ""
        adsense_script = f'<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client={html.escape(adsense)}" crossorigin="anonymous"></script>\n' if adsense else ""
        schema = {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": self.site["site_name"],
            "url": self.site["site_url"],
            "description": self.site["description"],
            "inLanguage": self.site.get("language", "zh-CN")
        }
        return f"""<!doctype html>
<html lang="{html.escape(self.site.get("language", "zh-CN"))}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <meta name="description" content="{html.escape(description)}">
  {verify_meta}  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/assets/style.css">
  {adsense_script}  <script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
</head>
<body>
  <header><nav><a class="brand" href="/">Easton Radar</a>{nav}</nav></header>
  <main>{body}</main>
  <footer>Easton Radar · 老花的信息差侦察站 · 内容用于信息收集、证据沉淀和方向判断</footer>
</body>
</html>
"""


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="12" fill="#101828"/>
<circle cx="32" cy="32" r="22" fill="none" stroke="#57d6a3" stroke-width="3"/>
<circle cx="32" cy="32" r="12" fill="none" stroke="#9ee7c7" stroke-width="2" opacity=".75"/>
<path d="M32 32 50 18" stroke="#ffd166" stroke-width="4" stroke-linecap="round"/>
<circle cx="32" cy="32" r="4" fill="#fff"/>
<path d="M10 46c8 9 23 12 36 3" fill="none" stroke="#57d6a3" stroke-width="3" stroke-linecap="round" opacity=".65"/>
</svg>
"""


STYLE = textwrap.dedent("""
:root{--bg:#f6f7fb;--card:#fff;--text:#172033;--muted:#657086;--line:#e4e8f0;--accent:#1f6feb;--warn:#a15c00}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.72}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
header{background:#fff;border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
nav{max-width:1120px;margin:0 auto;padding:14px 20px;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
.brand{font-weight:750;color:var(--text);margin-right:auto}nav a{font-size:14px;color:#2c3852}
main{max-width:1120px;margin:0 auto;padding:28px 20px 56px}.hero{padding:34px 0 18px}.hero h1{font-size:38px;line-height:1.16;margin:0 0 14px}.hero p{max-width:790px;color:var(--muted);font-size:17px;margin:0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}.card,.item,.report,.callout{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:18px}.section{margin-top:32px}
.list{display:grid;gap:12px}.flow{display:grid;gap:10px}.flow-item{display:grid;grid-template-columns:120px 1fr auto;gap:14px;align-items:start;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px}.flow-item h3{margin:0 0 6px;font-size:17px}.flow-item p{margin:5px 0}.topic-strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}.topic-chip{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}.topic-chip strong{display:block;font-size:22px}.archive-day{margin-top:18px}.item h3{margin:0 0 8px;font-size:18px}.item p{margin:8px 0}.meta{color:var(--muted);font-size:13px}.badge{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 9px;font-size:12px;color:#33415f;background:#fafbff;margin:0 6px 6px 0}.score{font-weight:700;color:#0a7f42}.source{word-break:break-all}
.topic-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.topic-head h2{margin:0}.kicker{font-size:13px;color:#44516b;font-weight:650;margin:0 0 6px}.topic-list{margin-top:14px}.callout{border-color:#bfd1f8;background:#f8fbff}.evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}.evidence-grid h3{margin-top:0}
.report{max-width:920px}.report h1{line-height:1.25}.report h2{margin-top:28px}.ad-slot{border:1px dashed #c8cfdd;border-radius:8px;color:#7b8496;padding:16px;text-align:center;background:#fff;margin:24px 0}
footer{border-top:1px solid var(--line);color:var(--muted);font-size:13px;padding:22px 20px;text-align:center}
@media(max-width:760px){.flow-item{grid-template-columns:1fr}.hero h1{font-size:30px}.brand{width:100%;margin-right:0}.report{padding:16px}}
""").strip() + "\n"


def render_about(site: dict[str, Any], policy: dict[str, Any]) -> str:
    lines = "".join(f"<li>{html.escape(x)}</li>" for x in policy["persona_lines"])
    filters = "".join(f"<li>{html.escape(x)}</li>" for x in policy["fatal_filters"])
    return f"""
<section class="hero"><h1>关于 Easton Radar</h1><p>这里是信息雷达和证据层，不是自动写稿机。</p></section>
<section class="card"><h2>关注主线</h2><ul>{lines}</ul></section>
<section class="card section"><h2>直接跳过</h2><ul>{filters}</ul></section>
<section class="card section"><h2>SEO / AI SEO</h2><p>站点输出结构化 HTML、JSON-LD、sitemap.xml、robots.txt、llms.txt，并保留 Google Search Console 和 AdSense 配置位。</p></section>
"""


def list_html(items: list[str], empty: str = "暂无") -> str:
    values = [x for x in items if x]
    if not values:
        values = [empty]
    return "".join(f"<li>{html.escape(x)}</li>" for x in values)


def display_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        preferred = []
        for key in ["time", "date", "event", "title", "source", "supports", "evidence", "note", "why", "needs", "query"]:
            if value.get(key):
                preferred.append(str(value[key]))
        if preferred:
            return " - ".join(preferred)
        return "；".join(f"{k}: {display_value(v)}" for k, v in value.items() if v)
    if isinstance(value, list):
        return "；".join(display_value(item) for item in value if item)
    return str(value)


def list_html(items: Any, empty: str = "暂无") -> str:
    if isinstance(items, list):
        values = [display_value(x) for x in items if display_value(x)]
    elif items:
        values = [display_value(items)]
    else:
        values = []
    if not values:
        values = [empty]
    return "".join(f"<li>{html.escape(x)}</li>" for x in values)


def topic_href(key: str) -> str:
    return f"/topics/{html.escape(key)}/"


def reports_for_topic(reports: list[dict[str, Any]], topic_key: str) -> list[dict[str, Any]]:
    return [report for report in reports if report.get("topic_direction") == topic_key]


def display_report_title(report: dict[str, Any]) -> str:
    title = report.get("title", "")
    prefixes = [report.get("report_type_title", ""), "深度调查", "机会拆解", "工具账本", "平台规则", "案例复盘", "风险避坑"]
    for prefix in prefixes:
        if prefix and title.startswith(prefix + "："):
            return title[len(prefix) + 1:].strip()
    return title


def report_card(report: dict[str, Any]) -> str:
    topic_title = report.get("topic_direction_short_title") or report.get("topic_direction_title") or report.get("source_category_title", "")
    title = display_report_title(report)
    dossier = report.get("selection_dossier") or report.get("material_pack") or {}
    verdict = dossier.get("verdict", {})
    verdict_label = verdict.get("label") or verdict.get("status") or "待判断"
    core_question = dossier.get("core_question") or report.get("reader_hook", "")
    return f"""
<article class="item">
  <p class="kicker">{html.escape(topic_title)}</p>
  <h3><a href="/items/{html.escape(report['id'])}/">{html.escape(title)}</a></h3>
  <p>{html.escape(core_question)}</p>
  <p><span class="badge">{html.escape(verdict_label)}</span><span class="badge">{html.escape(report['evidence_level'])}</span><span class="badge">{html.escape(report['report_type_title'])}</span><span class="score">Score {report['score']}</span></p>
  <p class="meta source">来源：<a href="{html.escape(report['url'])}" rel="nofollow noopener">{html.escape(report['source_name'])}</a> · {html.escape(report.get('original_title', ''))}</p>
</article>
"""


def topic_summary_chips(reports: list[dict[str, Any]], site: dict[str, Any]) -> str:
    chips = []
    for key, meta in site.get("topic_directions", {}).items():
        count = len(reports_for_topic(reports, key))
        chips.append(f"""
<a class="topic-chip" href="{topic_href(key)}">
  <span class="meta">{html.escape(meta.get("short_title", meta.get("title", key)))}</span>
  <strong>{count}</strong>
  <span class="meta">本批次候选</span>
</a>
""")
    return "".join(chips)


def report_flow_item(report: dict[str, Any]) -> str:
    dossier = report.get("selection_dossier") or report.get("material_pack") or {}
    verdict = dossier.get("verdict", {})
    label = verdict.get("label") or verdict.get("status") or "待判断"
    reason = verdict.get("reason", "")
    topic = report.get("topic_direction_short_title") or report.get("topic_direction_title") or report.get("source_category_title", "")
    title = display_report_title(report)
    return f"""
<article class="flow-item">
  <div><span class="badge">{html.escape(label)}</span><p class="meta">{html.escape(topic)}</p></div>
  <div>
    <h3><a href="/items/{html.escape(report['id'])}/">{html.escape(title)}</a></h3>
    <p>{html.escape(reason or report.get("reader_hook", ""))}</p>
    <p class="meta source">来源：<a href="{html.escape(report['url'])}" rel="nofollow noopener">{html.escape(report['source_name'])}</a> · {html.escape(report.get('original_title', ''))}</p>
  </div>
  <div class="meta">Score {report["score"]}<br>{html.escape(report["evidence_level"])}</div>
</article>
"""


def render_archive_preview(archive: dict[str, Any], limit_days: int = 5) -> str:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in archive.get("items", []):
        day = str(item.get("last_seen_at") or item.get("first_seen_at") or "")[:10] or "unknown"
        grouped.setdefault(day, []).append(item)
    sections = []
    for day in sorted(grouped.keys(), reverse=True)[:limit_days]:
        rows = []
        for item in grouped[day][:8]:
            rows.append(f'<li><span class="badge">{html.escape(item.get("verdict", "待判断"))}</span><a href="{html.escape(item.get("item_url", ""))}">{html.escape(item.get("title", ""))}</a> <span class="meta">· {html.escape(item.get("topic_direction_title", ""))}</span></li>')
        sections.append(f'<section class="archive-day"><h3>{html.escape(day)}</h3><ul>{"".join(rows)}</ul></section>')
    return "".join(sections) or '<p class="meta">暂无历史选题归档。</p>'


def render_home(batch: dict[str, Any], reports: list[dict[str, Any]], site: dict[str, Any], policy: dict[str, Any], archive: dict[str, Any]) -> str:
    principles = "".join(f"<li>{html.escape(x)}</li>" for x in policy["source_principles"])
    coverage = "".join(
        f'<span class="badge">{html.escape(v["title"])}：{v["items"]} 条 / 失败 {v["failures"]}</span>'
        for v in batch.get("source_coverage", {}).values()
    )
    flow = "".join(report_flow_item(r) for r in reports) or '<p class="meta">本批次暂无入池候选选题。</p>'
    verdict_counts: dict[str, int] = {}
    for report in reports:
        dossier = report.get("selection_dossier") or report.get("material_pack") or {}
        verdict = dossier.get("verdict", {})
        label = verdict.get("label") or verdict.get("status") or "待判断"
        verdict_counts[label] = verdict_counts.get(label, 0) + 1
    verdict_summary = " / ".join(f"{html.escape(k)} {v}" for k, v in verdict_counts.items()) or "暂无候选"
    duplicate_count = len(batch.get("duplicate_skips", []))
    return f"""
<section class="hero"><h1>老花的选题雷达站</h1><p>这里不是公众号成稿库，而是上游情报台。每条线索先按选题方向沉淀，再保留来龙去脉、事实、证据、存疑点和与老花人设相关的切入口，给后续博客、公众号、视频和小红书做素材底座。</p></section>
<section class="section">
  <h2>大选题方向聚合</h2>
  <div class="topic-strip">{topic_summary_chips(reports, site)}</div>
</section>
<div class="ad-slot">AdSense 预留位：后续填入 publisher client 后启用</div>
<section class="section card"><h2>本批次概况</h2><p class="meta">批次：{html.escape(batch["batch_id"])} · 抓取 {batch["fetched_count"]} 条 · 入池 {len(reports)} 个候选 · {verdict_summary} · 近期重复跳过 {duplicate_count} 个</p><p>{coverage}</p></section>
<section class="section"><h2>候选选题信息流</h2><div class="flow">{flow}</div></section>
<section class="section card"><h2>历史选题库</h2><p class="meta">按日期回看已入池选题，用来判断哪些题已经看过、哪些方向反复出现。<a href="/archive/">查看完整历史</a></p>{render_archive_preview(archive)}</section>
<section class="section card"><h2>数据源原则</h2><ul>{principles}</ul></section>
"""


def render_briefings(batch: dict[str, Any], reports: list[dict[str, Any]], site: dict[str, Any]) -> str:
    topic_sections = []
    for key, meta in site.get("topic_directions", {}).items():
        topic_reports = reports_for_topic(reports, key)
        items = "".join(report_card(r) for r in topic_reports) or '<p class="meta">本方向暂无高价值线索。</p>'
        topic_sections.append(f'<section class="section"><h2>{html.escape(meta["title"])}</h2><p>{html.escape(meta.get("description", ""))}</p><div class="list">{items}</div></section>')
    failures = "".join(f'<li>{html.escape(f["source"])}：{html.escape(f["error"])}</li>' for f in batch.get("failures", [])[:12]) or "<li>本批次无抓取失败。</li>"
    coverage = "".join(f'<li>{html.escape(v["title"])}：{v["items"]} 条，失败 {v["failures"]}</li>' for v in batch.get("source_coverage", {}).values())
    return f"""
<section class="hero"><h1>本批次候选选题</h1><p>候选选题按方向归档。先看方向，再看具体话题，最后进入报告确认事实、证据和可写切入口。</p></section>
<section class="card"><p>批次：{html.escape(batch["batch_id"])}</p><p>抓取 {batch["fetched_count"]} 条；入池 {len(reports)} 条；跳过 {batch["skipped_count"]} 条。</p></section>
<section class="section card"><h2>数据源覆盖</h2><ul>{coverage}</ul></section>
{''.join(topic_sections)}
<section class="section card"><h2>抓取失败</h2><ul>{failures}</ul></section>
"""


def render_topic_direction(key: str, meta: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    items = "".join(report_card(r) for r in reports) or '<p class="meta">本方向暂无报告。</p>'
    return f"""
<section class="hero"><p class="kicker">选题方向</p><h1>{html.escape(meta["title"])}</h1><p>{html.escape(meta.get("description", ""))}</p></section>
<section class="callout"><h2>这个方向怎么读</h2><p>先看线索和原始来源，再进入详情页看事实收集、证据收集、存疑点和与老花相关的切入口。报告类型只是分析方法，不代表主栏目。</p></section>
<section class="section list">{items}</section>
"""


def render_archive(archive: dict[str, Any], site: dict[str, Any]) -> str:
    directions = site.get("topic_directions", {})
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in archive.get("items", [])[:180]:
        day = str(item.get("last_seen_at") or item.get("first_seen_at") or "")[:10] or "unknown"
        grouped.setdefault(day, []).append(item)
    sections = []
    for day in sorted(grouped.keys(), reverse=True):
        rows = []
        for item in grouped[day]:
            topic_key = item.get("topic_direction", "")
            topic_title = item.get("topic_direction_title") or directions.get(topic_key, {}).get("title", topic_key)
            rows.append(f"""
<article class="item">
  <p class="kicker">{html.escape(topic_title)} · {html.escape(item.get("verdict", "待判断"))}</p>
  <h3><a href="{html.escape(item.get("item_url", ""))}">{html.escape(item.get("title", ""))}</a></h3>
  <p class="meta">Score {html.escape(str(item.get("score", "")))} · {html.escape(item.get("evidence_level", ""))} · 首次入池 {html.escape(item.get("first_seen_at", ""))}</p>
  <p class="meta source"><a href="{html.escape(item.get("url", ""))}">{html.escape(item.get("original_title", "") or item.get("url", ""))}</a></p>
</article>
""")
        sections.append(f'<section class="archive-day"><h2>{html.escape(day)}</h2><div class="list">{"".join(rows)}</div></section>')
    items = "".join(sections) or '<p class="meta">暂无历史选题归档。</p>'
    return f"""
<section class="hero"><h1>历史选题归档</h1><p>这里记录曾经进入候选池的选题，用于回看、去重和判断哪些方向已经反复出现。新的候选选题会和近 14 天归档做重复检查。</p></section>
<section class="section list">{items}</section>
"""


def render_report_type(key: str, meta: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    items = "".join(report_card(r) for r in reports) or '<p class="meta">本类型暂无报告。</p>'
    return f'<section class="hero"><p class="kicker">分析方法</p><h1>{html.escape(meta["title"])}</h1><p>{html.escape(meta["description"])}</p></section><section class="list">{items}</section>'


def render_material_pack(pack: dict[str, Any]) -> str:
    if not pack:
        return ""
    verdict = pack.get("verdict", {})
    judgment_path = list_html(pack.get("human_judgment_path", []))
    selection_questions = list_html(pack.get("selection_questions", []))
    value_signals = list_html(pack.get("value_signals", []))
    reject_signals = list_html(pack.get("reject_signals", []))
    missing_basics = list_html(pack.get("missing_basics", []))
    missing_materials = list_html(pack.get("missing_materials", []))
    not_claimable = list_html(pack.get("not_claimable", []))
    followup_queries = list_html(pack.get("followup_queries", []))
    stop = list_html(pack.get("stop_conditions", []))
    topic_value = "".join(
        f'<li><strong>{html.escape(item.get("question", ""))}</strong>：{html.escape(item.get("judgment", ""))}</li>'
        for item in pack.get("topic_value_assessment", [])
    )
    logic = "".join(
        f'<li><strong>{html.escape(item.get("node", ""))}</strong> <span class="badge">{html.escape(item.get("status", ""))}</span><br>{html.escape(item.get("note", ""))}</li>'
        for item in pack.get("logic_closure", [])
    )
    angles = "".join(
        f'<li><strong>{html.escape(item.get("angle", ""))}</strong>：{html.escape(item.get("why", ""))}<br><span class="meta">还需要：{html.escape(item.get("needs", ""))}</span></li>'
        for item in pack.get("writeable_angles", [])
    )
    fact_clarity = pack.get("fact_clarity", {})
    reliability = pack.get("evidence_reliability", {})
    confirmed = list_html(fact_clarity.get("confirmed", []))
    uncertainty = list_html(fact_clarity.get("uncertainty", []), "当前暂无额外存疑，但仍要继续补交叉证据。")
    weak_points = list_html(reliability.get("weak_points", []))
    return f"""
<section class="section callout">
  <h2>选题判断结论</h2>
  <p><strong>{html.escape(verdict.get("status", "待判断"))}</strong>：{html.escape(verdict.get("reason", ""))}</p>
  <p class="meta">档案版本：{html.escape(pack.get("schema", ""))} · 选题方向：{html.escape(pack.get("topic_direction_title", ""))}</p>
  <h3>旧版判断链路</h3><ul>{judgment_path}</ul>
</section>
<section class="section card">
  <h2>旧版选题判断字段</h2>
  <div class="evidence-grid">
    <section><h3>旧版问题清单</h3><ul>{selection_questions}</ul></section>
    <section><h3>有价值的信号</h3><ul>{value_signals}</ul><h3>应该放弃的信号</h3><ul>{reject_signals}</ul></section>
  </div>
</section>
<section class="section card">
  <h2>当前线索的价值判断</h2>
  <ul>{topic_value}</ul>
  <h3>逻辑能不能闭环</h3><ul>{logic}</ul>
</section>
<section class="section card">
  <h2>事实和证据是否可靠</h2>
  <div class="evidence-grid">
    <section><h3>事实清晰度</h3><p>{html.escape(fact_clarity.get("status", ""))} · {html.escape(fact_clarity.get("summary_status", ""))}</p><h3>已确认内容</h3><ul>{confirmed}</ul></section>
    <section><h3>证据等级</h3><p>{html.escape(reliability.get("level", ""))} · {html.escape(reliability.get("cross_check_status", ""))}</p><p class="meta source">主来源：<a href="{html.escape(reliability.get("primary_source", ""))}">{html.escape(reliability.get("source_name", ""))}</a></p><h3>薄弱点</h3><ul>{weak_points}</ul></section>
  </div>
</section>
<section class="section card">
  <h2>可以写的方向</h2>
  <ul>{angles}</ul>
  <h3>还缺哪些基础概念</h3><ul>{missing_basics}</ul>
  <h3>还缺哪些资料素材</h3><ul>{missing_materials}</ul>
  <h3>不能写成结论的地方</h3><ul>{not_claimable}</ul>
  <h3>下一步补证检索词</h3><ul>{followup_queries}</ul>
  <h3>停止信号</h3><ul>{stop}</ul>
</section>
"""


def render_material_pack(pack: dict[str, Any]) -> str:
    if not pack:
        return ""
    verdict = pack.get("verdict", {})
    facts = list_html(pack.get("fact_summary", []))
    timeline = list_html(pack.get("timeline", []), "暂无明确时间线。")
    missing_basics = list_html(pack.get("missing_basics", []))
    missing_materials = list_html(pack.get("missing_materials", []))
    not_claimable = list_html(pack.get("not_claimable", []))
    followup_queries = list_html(pack.get("followup_queries", []))
    stop = list_html(pack.get("stop_conditions", []))
    logic = html.escape(str(pack.get("logic_closure", "")))
    angles = "".join(
        f'<li><strong>{html.escape(item.get("angle", ""))}</strong>：{html.escape(item.get("why", ""))}<br><span class="meta">还需要：{html.escape(item.get("needs", ""))}</span></li>'
        for item in pack.get("writeable_angles", [])
        if isinstance(item, dict)
    )
    if not angles:
        angles = "<li>暂无可直接展开的写作方向，优先继续补证。</li>"
    evidence_rows = "".join(
        f"""
<article class="item">
  <h3>{html.escape(item.get("source", "") or item.get("title", "") or "证据来源")}</h3>
  <p>{html.escape(item.get("supports", ""))}</p>
  <p class="meta">{html.escape(item.get("reliability", ""))} · <a href="{html.escape(item.get("url", ""))}">{html.escape(item.get("url", ""))}</a></p>
</article>
"""
        for item in pack.get("evidence_table", [])
        if isinstance(item, dict)
    )
    if not evidence_rows:
        evidence_rows = '<p class="meta">暂无交叉证据。当前页面只能作为待补证线索。</p>'
    confidence = pack.get("confidence", "")
    generated_by = pack.get("generated_by", "")
    return f"""
<section class="section callout">
  <h2>选题判断</h2>
  <p><strong>{html.escape(verdict.get("status", "待判断"))}</strong>：{html.escape(verdict.get("reason", ""))}</p>
  <p>{html.escape(pack.get("why_this_topic_matters", ""))}</p>
  <p class="meta">报告来源：{html.escape(generated_by)} · 可信度 {html.escape(str(confidence))} · {html.escape(pack.get("schema", ""))}</p>
</section>
<section class="section card">
  <h2>这件事目前能确认什么</h2>
  <p><strong>核心问题：</strong>{html.escape(pack.get("core_question", ""))}</p>
  <ul>{facts}</ul>
  <h3>时间线</h3><ul>{timeline}</ul>
</section>
<section class="section card">
  <h2>证据与依据</h2>
  <div class="list">{evidence_rows}</div>
</section>
<section class="section card">
  <h2>逻辑能不能闭环</h2>
  <p>{logic}</p>
</section>
<section class="section card">
  <h2>可以继续写的方向</h2>
  <ul>{angles}</ul>
  <h3>还缺哪些基础概念</h3><ul>{missing_basics}</ul>
  <h3>还缺哪些资料素材</h3><ul>{missing_materials}</ul>
  <h3>不能写成结论的地方</h3><ul>{not_claimable}</ul>
  <h3>下一步补证检索词</h3><ul>{followup_queries}</ul>
  <h3>停止信号</h3><ul>{stop}</ul>
</section>
"""


def render_item(report: dict[str, Any], site: dict[str, Any]) -> str:
    facts = "".join(f'<li><strong>{html.escape(f["type"])}</strong>：{html.escape(f["claim"])} <a href="{html.escape(f["source_url"])}">来源</a></li>' for f in report["facts"])
    sources = "".join(f'<li><a href="{html.escape(s["url"])}">{html.escape(s["title"])}</a> · {html.escape(s["source_type"])} · {html.escape(s["used_for"])}</li>' for s in report["sources"])
    verification = report.get("verification", {})
    follow = list_html(verification.get("what_needs_followup", []))
    challenge = list_html(verification.get("expert_challenge_points", []))
    dont = list_html(verification.get("do_not_claim", []))
    confirmed = list_html(verification.get("what_is_confirmed", []))
    uncertainty = list_html(report.get("uncertainty_flags", []), "当前暂无额外存疑标记，但仍要以原始证据为准。")
    handoff = report.get("downstream_handoff", {})
    research = handoff.get("for_research_loop", {})
    queries = list_html(research.get("followup_queries", []))
    source_assessment = report.get("source_assessment", {})
    dossier = report.get("selection_dossier") or report.get("material_pack", {})
    verdict = dossier.get("verdict", {})
    pack_html = render_material_pack(dossier)
    topic_key = report.get("topic_direction", "")
    topic_title = report.get("topic_direction_title") or report.get("source_category_title", "")
    topic_link = topic_href(topic_key) if topic_key else "/"
    visible_title = display_report_title(report)
    schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": visible_title,
        "author": {"@type": "Person", "name": site["author"]},
        "datePublished": report.get("published_at") or report.get("fetched_at"),
        "mainEntityOfPage": f"{site['site_url'].rstrip('/')}/items/{report['id']}/"
    }
    summary = f"<p>{html.escape(report['summary'])}</p>" if report.get("summary") else '<p class="meta">原始来源未提供摘要，优先查看证据链和原文。</p>'
    return f"""
<article class="report">
  <script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
  <p class="meta"><a href="{topic_link}">{html.escape(topic_title)}</a> · {html.escape(report["report_type_title"])} · {html.escape(report["evidence_level"])} · Score {report["score"]}</p>
  <h1>{html.escape(visible_title)}</h1>
  <p class="meta">原始标题：{html.escape(report.get("original_title", report["title"]))}</p>

  <section class="callout">
    <h2>这是不是一个值得进入写作池的选题</h2>
    <p><strong>{html.escape(verdict.get("status", "待判断"))}</strong>：{html.escape(verdict.get("reason", ""))}</p>
    <p>{html.escape(report["persona_connection"]["why_it_matters"])}</p>
  </section>

  <section class="section card">
    <h2>原始线索</h2>
    {summary}
    <p><strong>为什么现在看：</strong>{html.escape(report["why_now"])}</p>
    <p><strong>收集原则判断：</strong>{html.escape(report.get("collection_fit", ""))}</p>
    <p class="meta source">原始链接：<a href="{html.escape(report["url"])}">{html.escape(report["url"])}</a></p>
  </section>

  {pack_html}

  <section class="section card">
    <h2>原始事实和证据入口</h2>
    <div class="evidence-grid">
      <section><h3>事实入口</h3><ul>{facts}</ul><h3>已确认部分</h3><ul>{confirmed}</ul></section>
      <section><h3>证据入口</h3><ul>{sources}</ul><p class="meta">来源优先级：{html.escape(report.get("source_priority", ""))}</p><p class="meta">GitHub Actions 稳定抓取：{html.escape(str(source_assessment.get("stable_in_github_actions", "")))}</p></section>
    </div>
  </section>

  <section class="section card">
    <h2>给 GPT 前必须知道的边界</h2>
    <h3>存疑点</h3><ul>{uncertainty}</ul>
    <h3>继续深挖方向</h3><p>{html.escape(report.get("investigation_direction", ""))}</p><ul>{follow}</ul>
    <h3>懂行人可能会挑刺</h3><ul>{challenge}</ul>
    <h3>不能写成结论</h3><ul>{dont}</ul>
  </section>

  <section class="section card">
    <h2>交付给 GPT 的使用入口</h2>
    <p>后续 GPT 应用应优先读取本静态页里的选题结论、判断链路、证据入口、缺口和可写方向；如果读取 JSON，则优先读取 <code>selection_dossier</code> 和 <code>material_pack</code>。</p>
    <p><strong>继续检索词：</strong></p><ul>{queries}</ul>
  </section>
</article>
"""


def render_static(batch: dict[str, Any], reports: list[dict[str, Any]], site: dict[str, Any], policy: dict[str, Any], archive: dict[str, Any]) -> None:
    static = StaticSite(site)
    static.write_assets()
    static.write_page("index.html", "Easton Radar", render_home(batch, reports, site, policy, archive))
    static.write_page("archive/index.html", "历史选题归档 - Easton Radar", render_archive(archive, site))
    static.write_page("about/index.html", "关于 - Easton Radar", render_about(site, policy))
    for key, meta in site.get("topic_directions", {}).items():
        static.write_page(f"topics/{key}/index.html", f"{meta['title']} - Easton Radar", render_topic_direction(key, meta, reports_for_topic(reports, key)))
    for report in reports:
        static.write_page(f"items/{report['id']}/index.html", f"{display_report_title(report)} - Easton Radar", render_item(report, site))
    static.write_text("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {site['site_url'].rstrip('/')}/sitemap.xml\n")
    static.write_text("sitemap.xml", sitemap(site, reports))
    static.write_text("llms.txt", llms(site, reports))
    static.write_text("ads.txt", "google.com, pub-0000000000000000, DIRECT, f08c47fec0942fa0\n")


def sitemap(site: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    base = site["site_url"].rstrip("/")
    paths = ["", "archive/", "about/"] + [f"topics/{k}/" for k in site.get("topic_directions", {})] + [f"items/{r['id']}/" for r in reports]
    today = now_utc().date().isoformat()
    body = "\n".join(f"<url><loc>{base}/{p}</loc><lastmod>{today}</lastmod></url>" for p in paths)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}\n</urlset>\n'


def llms(site: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    base = site["site_url"].rstrip("/")
    lines = ["# Easton Radar", "", site["description"], "", f"Archive: {base}/archive/", "", "## Topic directions"]
    for key, meta in site.get("topic_directions", {}).items():
        lines.append(f"- {meta['title']}: {base}/topics/{key}/")
    lines.extend(["", "## Analysis methods"])
    for key, meta in site["report_types"].items():
        lines.append(f"- {meta['title']}: internal report_type={key}, used only as an analysis method")
    lines.extend(["", "## Latest items"])
    for report in reports[:30]:
        lines.append(f"- {report['title']}: {base}/items/{report['id']}/")
    return "\n".join(lines) + "\n"


def send_telegram(batch: dict[str, Any], reports: list[dict[str, Any]], site: dict[str, Any]) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("Telegram not configured; skip notification.")
        return
    base = site["site_url"].rstrip("/")
    verdict_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    for report in reports:
        dossier = report.get("selection_dossier") or report.get("material_pack") or {}
        verdict = dossier.get("verdict", {})
        label = verdict.get("label") or verdict.get("status") or "待判断"
        topic = report.get("topic_direction_short_title") or report.get("topic_direction_title") or report.get("source_category_title", "未分类")
        verdict_counts[label] = verdict_counts.get(label, 0) + 1
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

    verdict_summary = " / ".join(f"{key} {value}" for key, value in verdict_counts.items()) or "暂无候选"
    topic_summary = " / ".join(f"{key} {value}" for key, value in topic_counts.items()) or "暂无方向"
    lines = [
        f"Easton Radar {batch['slot_label']}｜候选选题池",
        f"抓取 {batch['fetched_count']} 条，入池 {len(reports)} 个候选选题（{verdict_summary}）。",
        f"方向分布：{topic_summary}",
        "",
        "本批次候选：",
    ]
    for report in reports[:8]:
        dossier = report.get("selection_dossier") or report.get("material_pack") or {}
        verdict = dossier.get("verdict", {})
        label = verdict.get("label") or verdict.get("status") or "待判断"
        reason = verdict.get("reason", "")
        topic = report.get("topic_direction_short_title") or report.get("topic_direction_title") or report.get("source_category_title", "")
        title = display_report_title(report)
        lines.append(f"- [{label}] {title}")
        lines.append(f"  {topic} | {report['evidence_level']} | Score {report['score']}")
        if reason:
            lines.append(f"  判断：{reason[:80]}")
        lines.append(f"  {base}/items/{report['id']}/")
    if not reports:
        lines.append("本批次没有达到入池标准的候选选题，只保留抓取数据和源覆盖统计。")
    payload = urllib.parse.urlencode({"chat_id": chat_id, "text": "\n".join(lines)[:3900], "disable_web_page_preview": "true"}).encode("utf-8")
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=payload, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        print("Telegram notification sent.")
    except Exception as exc:
        print(f"Telegram notification failed: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", default="auto")
    parser.add_argument("--no-telegram", action="store_true")
    args = parser.parse_args()
    slot = current_slot(args.slot)
    bj = now_bj()
    batch_id = f"{bj.strftime('%Y-%m-%d')}-{slot}"
    info(f"Easton Radar pipeline start: batch={batch_id}, slot={slot}")
    site = load_json(CONFIG_DIR / "site.json")
    policy = load_json(CONFIG_DIR / "radar_policy.json")
    sources = load_json(CONFIG_DIR / "sources.seed.json")
    info(
        "Runtime config: "
        f"deepseek={'yes' if os.environ.get('DEEPSEEK_API_KEY') else 'no'}, "
        f"telegram={'yes' if os.environ.get('TELEGRAM_BOT_TOKEN') and os.environ.get('TELEGRAM_CHAT_ID') else 'no'}, "
        f"sources={count_configured_sources(sources)}, "
        f"brave_search={'yes' if os.environ.get('BRAVE_SEARCH_API_KEY') else 'no'}"
    )
    archive = load_topic_archive()
    info(f"Loaded topic archive entries: {len(archive.get('items', []))}")
    items, failures = collect_items(sources)
    info(f"Fetched source items: items={len(items)}, failures={len(failures)}")
    if failures:
        for failure in failures[:10]:
            info(f"Source failure: {failure.get('source', '')} | {failure.get('error', '')}")
    decisions = deepseek_triage(items, site, policy) or [heuristic_decision(item, site) for item in items]
    known = {d.item.id for d in decisions}
    decisions.extend(heuristic_decision(item, site) for item in items if item.id not in known)
    decisions.sort(key=lambda x: x.score, reverse=True)
    llm_count = sum(1 for d in decisions if not d.traceability.get("heuristic"))
    heuristic_count = sum(1 for d in decisions if d.traceability.get("heuristic"))
    info(
        "Triage complete: "
        f"decisions={len(decisions)}, llm={llm_count}, heuristic={heuristic_count}, "
        f"deep={sum(1 for d in decisions if d.decision == 'deep_dive')}, "
        f"brief={sum(1 for d in decisions if d.decision == 'brief')}, "
        f"skip={sum(1 for d in decisions if d.decision == 'skip')}"
    )
    selected, duplicate_skips = select_reports(decisions, site, archive, batch_id)
    info(f"Selection complete: selected={len(selected)}, duplicate_skips={len(duplicate_skips)}")
    for index, decision in enumerate(selected, 1):
        topic_key, _ = topic_direction_for_item(decision.item, decision.report_type, site)
        info(f"Selected #{index}: score={decision.score}, decision={decision.decision}, topic={topic_key}, title={decision.report_title}")
    for skipped in duplicate_skips[:10]:
        info(f"Duplicate skip: {skipped.get('title', '')} | {skipped.get('reason', '')}")
    info("Building enriched topic reports...")
    reports = [build_report(d, site, policy) for d in selected]
    info(f"Report build complete: reports={len(reports)}")
    selected_deep_count = sum(1 for d in selected if d.decision == "deep_dive")
    selected_brief_count = sum(1 for d in selected if d.decision == "brief")
    batch = {
        "batch_id": batch_id,
        "slot": slot,
        "slot_label": {"morning": "早报", "noon": "午报", "evening": "晚报"}.get(slot, slot),
        "generated_at": now_utc().isoformat(),
        "fetched_count": len(items),
        "deep_count": selected_deep_count,
        "brief_count": selected_brief_count,
        "skipped_count": sum(1 for d in decisions if d.decision == "skip"),
        "candidate_deep_count": sum(1 for d in decisions if d.decision == "deep_dive"),
        "candidate_brief_count": sum(1 for d in decisions if d.decision == "brief"),
        "duplicate_skip_count": len(duplicate_skips),
        "duplicate_skips": duplicate_skips[:30],
        "source_coverage": source_coverage(items, failures, site),
        "failures": failures,
    }
    archive = update_topic_archive(archive, reports, batch)
    info(f"Updated topic archive entries: {len(archive.get('items', []))}")
    clean_generated_outputs()
    info("Cleaned generated output directories.")
    write_json(DATA_DIR / f"{batch_id}.json", {"batch": batch, "items": [item.__dict__ for item in items], "reports": reports})
    write_json(DATA_DIR / "latest.json", {"batch": batch, "reports": reports})
    write_json(TOPIC_ARCHIVE_PATH, archive)
    for report in reports:
        write_json(REPORTS_DIR / f"{report['id']}.json", report)
    info("Wrote JSON outputs.")
    render_static(batch, reports, site, policy, archive)
    info("Rendered static site.")
    if not args.no_telegram:
        send_telegram(batch, reports, site)
    info("Pipeline done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
