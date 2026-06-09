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

USER_AGENT = "EastonRadar/0.1 (+https://radar.huadongpeng.com)"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
MAX_ITEMS_PER_SOURCE = 18
MAX_TOTAL_ITEMS = 220
MAX_REPORTS_PER_BATCH = 48
SOURCE_CATEGORY_REPORT_CAPS = {
    "ai_tools": 20,
    "developer_business": 14,
    "overseas_and_platforms": 8,
    "platform_policy": 8,
}


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
    for rel in ["items", "reports", "topics", "briefings", "about", "assets"]:
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


def fetch_url(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


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
    reject_reason = "" if decision != "skip" else "读者入口、成本/平台/工具链关联不够明确，先不进入简报。"
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
        return "部分符合收集原则：可以进入简报观察，但证据链或读者入口还不够完整。"
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
        flags.append("当前仅适合简报观察，不宜写成深度结论。")
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
    tags = [report.get("topic_direction_title", ""), report["report_type_title"], report["source_category_title"], report["evidence_level"], report["source_name"]]
    return {
        "package_version": "radar-handoff-v1",
        "canonical_url": canonical_url,
        "for_gpt_editor": {
            "brief": report["summary"] or report["reader_hook"],
            "title_seed": report["title"],
            "original_title": report.get("original_title", ""),
            "source_url": report["url"],
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
        "for_cms": {
            "slug": report["id"],
            "canonical_url": canonical_url,
            "seo_title": report["title"],
            "seo_description": report["reader_hook"],
            "tags": [tag for tag in tags if tag],
            "report_type": report["report_type"],
            "topic_direction": report.get("topic_direction", ""),
            "topic_direction_title": report.get("topic_direction_title", ""),
            "source_category": report["source_category"],
            "evidence_level": report["evidence_level"],
            "material_template": report.get("material_pack", {}).get("template", ""),
            "publish_status": "radar_published",
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


def deepseek_triage(items: list[SourceItem], site: dict[str, Any], policy: dict[str, Any]) -> list[RadarDecision] | None:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return None
    sample = [
        {
            "id": item.id,
            "source_category": item.source_category,
            "source_name": item.source_name,
            "title": item.title,
            "url": item.url,
            "summary": item.summary[:360],
            "published_at": item.published_at,
        }
        for item in items[:80]
    ]
    body = {
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "temperature": 0.2,
        "max_tokens": 6000,
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
        parsed = json.loads(data["choices"][0]["message"]["content"])
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
        return decisions or None
    except Exception as exc:
        print(f"DeepSeek triage failed, fallback to heuristic: {exc}", file=sys.stderr)
        return None


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
    report["material_pack"] = material_pack(report)
    report["downstream_handoff"] = downstream_handoff(report, site)
    return report


def select_reports(decisions: list[RadarDecision], limit: int = MAX_REPORTS_PER_BATCH) -> list[RadarDecision]:
    eligible = [d for d in decisions if d.decision in {"deep_dive", "brief"}]
    selected: list[RadarDecision] = []
    category_counts: dict[str, int] = {}

    for decision in eligible:
        category = decision.item.source_category
        cap = SOURCE_CATEGORY_REPORT_CAPS.get(category, limit)
        if category_counts.get(category, 0) >= cap:
            continue
        selected.append(decision)
        category_counts[category] = category_counts.get(category, 0) + 1
        if len(selected) >= limit:
            return selected

    return selected


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
  {verify_meta}  <link rel="stylesheet" href="/assets/style.css">
  {adsense_script}  <script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
</head>
<body>
  <header><nav><a class="brand" href="/">Easton Radar</a>{nav}</nav></header>
  <main>{body}</main>
  <footer>Easton Radar · 老花的信息差侦察站 · 内容用于信息收集、证据沉淀和方向判断</footer>
</body>
</html>
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
.list{display:grid;gap:12px}.item h3{margin:0 0 8px;font-size:18px}.item p{margin:8px 0}.meta{color:var(--muted);font-size:13px}.badge{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 9px;font-size:12px;color:#33415f;background:#fafbff;margin:0 6px 6px 0}.score{font-weight:700;color:#0a7f42}.source{word-break:break-all}
.topic-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.topic-head h2{margin:0}.kicker{font-size:13px;color:#44516b;font-weight:650;margin:0 0 6px}.topic-list{margin-top:14px}.callout{border-color:#bfd1f8;background:#f8fbff}.evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}.evidence-grid h3{margin-top:0}
.report{max-width:920px}.report h1{line-height:1.25}.report h2{margin-top:28px}.ad-slot{border:1px dashed #c8cfdd;border-radius:8px;color:#7b8496;padding:16px;text-align:center;background:#fff;margin:24px 0}
footer{border-top:1px solid var(--line);color:var(--muted);font-size:13px;padding:22px 20px;text-align:center}
@media(max-width:640px){.hero h1{font-size:30px}.brand{width:100%;margin-right:0}.report{padding:16px}}
""").strip() + "\n"


def report_card(report: dict[str, Any]) -> str:
    return f"""
<article class="item">
  <h3><a href="/items/{html.escape(report["id"])}/">{html.escape(report["title"])}</a></h3>
  <p>{html.escape(report["reader_hook"])}</p>
  <p><span class="badge">{html.escape(report["report_type_title"])}</span><span class="badge">{html.escape(report["source_category_title"])}</span><span class="badge">{html.escape(report["evidence_level"])}</span><span class="score">Score {report["score"]}</span></p>
  <p class="meta source">来源：<a href="{html.escape(report["url"])}" rel="nofollow noopener">{html.escape(report["source_name"])}</a></p>
</article>
"""


def render_home(batch: dict[str, Any], reports: list[dict[str, Any]], site: dict[str, Any], policy: dict[str, Any]) -> str:
    type_cards = []
    for key, meta in site["report_types"].items():
        count = sum(1 for r in reports if r["report_type"] == key)
        type_cards.append(f'<article class="card"><h3><a href="/reports/{key}/">{html.escape(meta["title"])}</a></h3><p>{html.escape(meta["description"])}</p><p class="meta">本批次 {count} 条</p></article>')
    principles = "".join(f"<li>{html.escape(x)}</li>" for x in policy["source_principles"])
    coverage = "".join(
        f'<span class="badge">{html.escape(v["title"])}：{v["items"]} 条 / 失败 {v["failures"]}</span>'
        for v in batch.get("source_coverage", {}).values()
    )
    latest = "".join(report_card(r) for r in reports[:10]) or '<p class="meta">本批次暂无可发布报告。</p>'
    return f"""
<section class="hero"><h1>老花的信息差侦察站</h1><p>按报告类型整理线索：先判断是否符合收集原则，再持续深挖证据；没有证据或仍存疑的地方，必须单独标记。</p></section>
<section class="grid">{''.join(type_cards)}</section>
<div class="ad-slot">AdSense 预留位：后续填入 publisher client 后启用</div>
<section class="section card"><h2>数据源覆盖</h2><p>{coverage}</p></section>
<section class="section"><h2>最新简报</h2><p class="meta">批次：{html.escape(batch["batch_id"])} · 抓取 {batch["fetched_count"]} 条 · 深挖 {batch["deep_count"]} 条 · 简报 {batch["brief_count"]} 条</p><div class="list">{latest}</div></section>
<section class="section card"><h2>数据源原则</h2><ul>{principles}</ul></section>
"""


def render_briefings(batch: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    items = "".join(report_card(r) for r in reports) or '<p class="meta">本批次暂无条目。</p>'
    failures = "".join(f'<li>{html.escape(f["source"])}：{html.escape(f["error"])}</li>' for f in batch.get("failures", [])[:12]) or "<li>本批次无抓取失败。</li>"
    coverage = "".join(f'<li>{html.escape(v["title"])}：{v["items"]} 条，失败 {v["failures"]}</li>' for v in batch.get("source_coverage", {}).values())
    return f"""
<section class="hero"><h1>简报</h1><p>简报是信息线索和证据入口。每条都会保留报告类型、来源分类、收集判断和溯源信息。</p></section>
<section class="card"><p>批次：{html.escape(batch["batch_id"])}</p><p>抓取 {batch["fetched_count"]} 条；深挖 {batch["deep_count"]} 条；简报 {batch["brief_count"]} 条；跳过 {batch["skipped_count"]} 条。</p></section>
<section class="section card"><h2>数据源覆盖</h2><ul>{coverage}</ul></section>
<section class="section list">{items}</section>
<section class="section card"><h2>抓取失败</h2><ul>{failures}</ul></section>
"""


def render_report_type(key: str, meta: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    items = "".join(report_card(r) for r in reports) or '<p class="meta">本类型暂无报告。</p>'
    return f'<section class="hero"><h1>{html.escape(meta["title"])}</h1><p>{html.escape(meta["description"])}</p></section><section class="list">{items}</section>'


def render_item(report: dict[str, Any], site: dict[str, Any]) -> str:
    facts = "".join(f'<li><strong>{html.escape(f["type"])}</strong>：{html.escape(f["claim"])} <a href="{html.escape(f["source_url"])}">来源</a></li>' for f in report["facts"])
    sources = "".join(f'<li><a href="{html.escape(s["url"])}">{html.escape(s["title"])}</a> · {html.escape(s["source_type"])} · {html.escape(s["used_for"])}</li>' for s in report["sources"])
    follow = "".join(f"<li>{html.escape(x)}</li>" for x in report["verification"]["what_needs_followup"])
    challenge = "".join(f"<li>{html.escape(x)}</li>" for x in report["verification"]["expert_challenge_points"])
    dont = "".join(f"<li>{html.escape(x)}</li>" for x in report["verification"]["do_not_claim"])
    uncertainty = "".join(f"<li>{html.escape(x)}</li>" for x in report.get("uncertainty_flags", []))
    handoff = report.get("downstream_handoff", {})
    editor = handoff.get("for_gpt_editor", {})
    cms = handoff.get("for_cms", {})
    research = handoff.get("for_research_loop", {})
    angles = "".join(f"<li>{html.escape(x)}</li>" for x in editor.get("angle_candidates", []))
    queries = "".join(f"<li>{html.escape(x)}</li>" for x in research.get("followup_queries", []))
    cms_tags = "、".join(html.escape(x) for x in cms.get("tags", []))
    source_assessment = report.get("source_assessment", {})
    schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": report["title"],
        "author": {"@type": "Person", "name": site["author"]},
        "datePublished": report.get("published_at") or report.get("fetched_at"),
        "mainEntityOfPage": f"{site['site_url'].rstrip('/')}/items/{report['id']}/"
    }
    summary = f"<p>{html.escape(report['summary'])}</p>" if report.get("summary") else ""
    return f"""
<article class="report">
  <script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>
  <p class="meta"><a href="/reports/{html.escape(report["report_type"])}/">{html.escape(report["report_type_title"])}</a> · {html.escape(report["source_category_title"])} · {html.escape(report["evidence_level"])} · Score {report["score"]}</p>
  <h1>{html.escape(report["title"])}</h1>
  <p class="meta">原始标题：{html.escape(report.get("original_title", report["title"]))}</p>
  {summary}
  <h2>收集原则判断</h2><p>{html.escape(report.get("collection_fit", ""))}</p>
  <h2>深挖方向</h2><p>{html.escape(report.get("investigation_direction", ""))}</p>
  <h2>存疑标记</h2><ul>{uncertainty}</ul>
  <h2>来源评估</h2><p>{html.escape(report.get("source_priority", ""))} · GitHub Actions 稳定抓取：{html.escape(str(source_assessment.get("stable_in_github_actions", "")))}</p>
  <h2>普通读者入口</h2><p>{html.escape(report["reader_hook"])}</p>
  <h2>为什么现在看</h2><p>{html.escape(report["why_now"])}</p>
  <h2>和老花人设的关系</h2><p>{html.escape(report["persona_connection"]["why_it_matters"])}</p>
  <h2>证据链</h2><ul>{facts}</ul>
  <h2>来源</h2><ul>{sources}</ul>
  <h2>还要补证</h2><ul>{follow}</ul>
  <h2>后续流程交接包</h2>
  <p><strong>GPT 编辑应用角度候选：</strong></p><ul>{angles}</ul>
  <p><strong>继续检索词：</strong></p><ul>{queries}</ul>
  <p><strong>CMS 标签：</strong>{cms_tags}</p>
  <h2>懂行人可能挑刺的地方</h2><ul>{challenge}</ul>
  <h2>不能夸大的地方</h2><ul>{dont}</ul>
  <p class="meta source">原始链接：<a href="{html.escape(report["url"])}">{html.escape(report["url"])}</a></p>
</article>
"""


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
    return f"""
<article class="item">
  <p class="kicker">{html.escape(topic_title)}</p>
  <h3><a href="/items/{html.escape(report['id'])}/">{html.escape(title)}</a></h3>
  <p>{html.escape(report['reader_hook'])}</p>
  <p><span class="badge">{html.escape(report['report_type_title'])}</span><span class="badge">{html.escape(report['evidence_level'])}</span><span class="score">Score {report['score']}</span></p>
  <p class="meta source">来源：<a href="{html.escape(report['url'])}" rel="nofollow noopener">{html.escape(report['source_name'])}</a> · {html.escape(report.get('original_title', ''))}</p>
</article>
"""


def render_home(batch: dict[str, Any], reports: list[dict[str, Any]], site: dict[str, Any], policy: dict[str, Any]) -> str:
    topic_cards = []
    for key, meta in site.get("topic_directions", {}).items():
        topic_reports = reports_for_topic(reports, key)
        preview = "".join(report_card(r) for r in topic_reports[:2]) or '<p class="meta">本批次暂无高价值线索。</p>'
        topic_cards.append(f"""
<section class="card">
  <div class="topic-head"><div><p class="kicker">选题方向</p><h2><a href="{topic_href(key)}">{html.escape(meta['title'])}</a></h2></div><span class="badge">本批次 {len(topic_reports)} 条</span></div>
  <p>{html.escape(meta.get('description', ''))}</p>
  <div class="topic-list list">{preview}</div>
</section>
""")
    principles = "".join(f"<li>{html.escape(x)}</li>" for x in policy["source_principles"])
    coverage = "".join(
        f'<span class="badge">{html.escape(v["title"])}：{v["items"]} 条 / 失败 {v["failures"]}</span>'
        for v in batch.get("source_coverage", {}).values()
    )
    latest = "".join(report_card(r) for r in reports[:8]) or '<p class="meta">本批次暂无可发布报告。</p>'
    return f"""
<section class="hero"><h1>老花的选题雷达站</h1><p>这里不是公众号成稿库，而是上游情报台。每条线索先按选题方向沉淀，再保留来龙去脉、事实、证据、存疑点和与老花人设相关的切入口，给后续博客、公众号、视频和小红书做素材底座。</p></section>
<section class="grid">{''.join(topic_cards)}</section>
<div class="ad-slot">AdSense 预留位：后续填入 publisher client 后启用</div>
<section class="section card"><h2>本批次数据源覆盖</h2><p>{coverage}</p></section>
<section class="section"><h2>最新值得扫一眼的线索</h2><p class="meta">批次：{html.escape(batch["batch_id"])} · 抓取 {batch["fetched_count"]} 条 · 深挖 {batch["deep_count"]} 条 · 简报 {batch["brief_count"]} 条</p><div class="list">{latest}</div></section>
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
<section class="hero"><h1>本批次简报</h1><p>简报按选题方向归档。先看方向，再看具体话题，最后进入深度报告确认事实、证据和可写切入口。</p></section>
<section class="card"><p>批次：{html.escape(batch["batch_id"])}</p><p>抓取 {batch["fetched_count"]} 条；深挖 {batch["deep_count"]} 条；简报 {batch["brief_count"]} 条；跳过 {batch["skipped_count"]} 条。</p></section>
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


def render_report_type(key: str, meta: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    items = "".join(report_card(r) for r in reports) or '<p class="meta">本类型暂无报告。</p>'
    return f'<section class="hero"><p class="kicker">分析方法</p><h1>{html.escape(meta["title"])}</h1><p>{html.escape(meta["description"])}</p></section><section class="list">{items}</section>'


def render_material_pack(pack: dict[str, Any]) -> str:
    if not pack:
        return ""
    timeline = "".join(
        f'<li><strong>{html.escape(str(item.get("time", "")))}</strong>：{html.escape(item.get("event", ""))} <span class="meta">{html.escape(item.get("evidence", ""))}</span></li>'
        for item in pack.get("timeline", [])
    )
    fact_sheet = list_html(pack.get("fact_sheet", []))
    dimensions = "".join(
        f'<li><strong>{html.escape(item.get("name", ""))}</strong>：{html.escape(item.get("how_to_use", ""))}</li>'
        for item in pack.get("analysis_dimensions", [])
    )
    evidence_map = "".join(
        f'<li><strong>{html.escape(item.get("source", ""))}</strong>：{html.escape(item.get("supports", ""))} <a href="{html.escape(item.get("url", ""))}">证据</a> <span class="meta">{html.escape(item.get("evidence_level", ""))}</span></li>'
        for item in pack.get("evidence_map", [])
    )
    writing = pack.get("writing_materials", {})
    title_seeds = list_html(writing.get("title_seeds", []))
    questions = list_html(writing.get("reader_questions", []))
    hooks = list_html(writing.get("opening_hooks", []))
    angles = list_html(writing.get("angle_seeds", []))
    gaps = list_html(pack.get("evidence_gaps", []))
    stop = list_html(pack.get("stop_conditions", []))
    return f"""
<section class="section callout">
  <h2>完整资料和素材包</h2>
  <p class="meta">结构模板：{html.escape(pack.get("template", ""))} · 选题方向：{html.escape(pack.get("topic_direction_title", ""))}</p>
  <div class="evidence-grid">
    <section><h3>时间线</h3><ul>{timeline}</ul></section>
    <section><h3>事实速记</h3><ul>{fact_sheet}</ul></section>
  </div>
  <h3>本类报告必须拆的维度</h3><ul>{dimensions}</ul>
  <h3>证据地图</h3><ul>{evidence_map}</ul>
  <div class="evidence-grid">
    <section><h3>标题种子</h3><ul>{title_seeds}</ul><h3>读者会问的问题</h3><ul>{questions}</ul></section>
    <section><h3>开头切入素材</h3><ul>{hooks}</ul><h3>可展开角度</h3><ul>{angles}</ul></section>
  </div>
  <h3>证据缺口</h3><ul>{gaps}</ul>
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
    editor = handoff.get("for_gpt_editor", {})
    cms = handoff.get("for_cms", {})
    research = handoff.get("for_research_loop", {})
    angles = list_html(editor.get("angle_candidates", []))
    queries = list_html(research.get("followup_queries", []))
    cms_tags = "、".join(html.escape(x) for x in cms.get("tags", []))
    source_assessment = report.get("source_assessment", {})
    pack_html = render_material_pack(report.get("material_pack", {}))
    topic_key = report.get("topic_direction", "")
    topic_title = report.get("topic_direction_title") or report.get("source_category_title", "")
    topic_link = topic_href(topic_key) if topic_key else "/briefings/"
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

  <section class="callout"><h2>和老花相关的切入口</h2><p>{html.escape(report["persona_connection"]["why_it_matters"])}</p></section>

  {pack_html}

  <h2>事情的来龙去脉</h2>
  {summary}
  <p>{html.escape(report["why_now"])}</p>
  <p>{html.escape(report.get("collection_fit", ""))}</p>

  <div class="evidence-grid">
    <section><h3>事实收集</h3><ul>{facts}</ul><h3>已确认部分</h3><ul>{confirmed}</ul></section>
    <section><h3>证据收集</h3><ul>{sources}</ul><p class="meta">来源优先级：{html.escape(report.get("source_priority", ""))}</p><p class="meta">GitHub Actions 稳定抓取：{html.escape(str(source_assessment.get("stable_in_github_actions", "")))}</p></section>
  </div>

  <h2>存疑点和证据缺口</h2><ul>{uncertainty}</ul>
  <h2>下一步应该怎么深挖</h2><p>{html.escape(report.get("investigation_direction", ""))}</p><ul>{follow}</ul>

  <h2>后续写作可用材料</h2>
  <p><strong>可展开角度：</strong></p><ul>{angles}</ul>
  <p><strong>继续检索词：</strong></p><ul>{queries}</ul>
  <p><strong>CMS 标签：</strong>{cms_tags}</p>

  <h2>懂行人可能会挑刺的地方</h2><ul>{challenge}</ul>
  <h2>不能写成结论的地方</h2><ul>{dont}</ul>
  <p class="meta source">原始链接：<a href="{html.escape(report["url"])}">{html.escape(report["url"])}</a></p>
</article>
"""


def render_static(batch: dict[str, Any], reports: list[dict[str, Any]], site: dict[str, Any], policy: dict[str, Any]) -> None:
    static = StaticSite(site)
    static.write_assets()
    static.write_page("index.html", "Easton Radar", render_home(batch, reports, site, policy))
    static.write_page("briefings/index.html", "简报 - Easton Radar", render_briefings(batch, reports, site))
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
    paths = ["", "briefings/", "about/"] + [f"topics/{k}/" for k in site.get("topic_directions", {})] + [f"items/{r['id']}/" for r in reports]
    today = now_utc().date().isoformat()
    body = "\n".join(f"<url><loc>{base}/{p}</loc><lastmod>{today}</lastmod></url>" for p in paths)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}\n</urlset>\n'


def llms(site: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    base = site["site_url"].rstrip("/")
    lines = ["# Easton Radar", "", site["description"], "", "## Topic directions"]
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
    lines = [f"Easton Radar {batch['slot_label']}", f"抓取 {batch['fetched_count']} 条，深挖 {batch['deep_count']} 条，简报 {batch['brief_count']} 条。", ""]
    for report in reports[:8]:
        lines.append(f"- {report['title']}")
        lines.append(f"  {report['report_type_title']} | Score {report['score']}")
        lines.append(f"  {base}/items/{report['id']}/")
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
    site = load_json(CONFIG_DIR / "site.json")
    policy = load_json(CONFIG_DIR / "radar_policy.json")
    sources = load_json(CONFIG_DIR / "sources.seed.json")
    items, failures = collect_items(sources)
    print(f"Fetched {len(items)} items, failures {len(failures)}")
    decisions = deepseek_triage(items, site, policy) or [heuristic_decision(item, site) for item in items]
    known = {d.item.id for d in decisions}
    decisions.extend(heuristic_decision(item, site) for item in items if item.id not in known)
    decisions.sort(key=lambda x: x.score, reverse=True)
    selected = select_reports(decisions)
    reports = [build_report(d, site, policy) for d in selected]
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
        "source_coverage": source_coverage(items, failures, site),
        "failures": failures,
    }
    clean_generated_outputs()
    write_json(DATA_DIR / f"{batch_id}.json", {"batch": batch, "items": [item.__dict__ for item in items], "reports": reports})
    write_json(DATA_DIR / "latest.json", {"batch": batch, "reports": reports})
    for report in reports:
        write_json(REPORTS_DIR / f"{report['id']}.json", report)
    render_static(batch, reports, site, policy)
    if not args.no_telegram:
        send_telegram(batch, reports, site)
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
