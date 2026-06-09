from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
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
MAX_TOTAL_ITEMS = 120


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
                    if item.url not in seen:
                        items.append(item)
                        seen.add(item.url)
            except Exception as exc:
                failures.append({"source_category": source_category, "source": url, "error": str(exc)[:240]})
        for url in group.get("apis", []):
            try:
                for item in parse_hn_api(source_category, url, fetch_url(url)):
                    if item.url not in seen:
                        items.append(item)
                        seen.add(item.url)
            except Exception as exc:
                failures.append({"source_category": source_category, "source": url, "error": str(exc)[:240]})
    return items[:MAX_TOTAL_ITEMS], failures


def infer_report_type(text: str, source_category: str) -> str:
    text = text.lower()
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


def make_report_title(item: SourceItem, report_type: str, site: dict[str, Any]) -> str:
    report_name = site["report_types"].get(report_type, {}).get("title", "线索")
    host = urllib.parse.urlparse(item.url).netloc.replace("www.", "")
    source = item.source_name or host
    lower = f"{item.title} {source}".lower()
    known = ["OpenAI", "Claude", "GitHub", "Copilot", "Cloudflare", "Amazon", "AWS", "Google", "Gemini", "DeepSeek", "Hugging Face", "Vercel", "Stripe"]
    subject = source or host
    for name in known:
        if name.lower() in lower:
            subject = name
            break
    if subject == "Amazon":
        subject = "AWS"
    if subject.lower() in {"artificial intelligence", "machine learning"} and "amazon" in host:
        subject = "AWS"
    actions = {
        "tool-ledger": "工具成本和能力变化",
        "platform-rules": "平台规则变化",
        "case-study": "案例线索",
        "opportunity": "机会线索",
        "risk-warning": "风险线索",
        "investigation": "重要线索",
    }
    return f"{report_name}：{subject} 的{actions.get(report_type, '重要线索')}"


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
                    "你是 Easton Radar 的信息初筛员。网站栏目按报告类型分类，不按数据源分类。"
                    "请只输出 JSON：{\"items\":[...]}。每项包含 id, decision(deep_dive|brief|skip), report_type, report_title"
                    "(investigation|opportunity|tool-ledger|platform-rules|case-study|risk-warning), score(0-100),"
                    "reader_hook, why_now, evidence_level(official|near_source|media|weak), reason, reject_reason,"
                    "collection_fit, investigation_direction, uncertainty_flags。\n"
                    "report_title 必须是中文 Radar 标题，可以保留产品名/公司名，但不能整句英文照搬原题。\n"
                    "硬规则：优先官方/一手/近源；反爬论坛抓不到就放弃；冷门技术没有普通读者入口就 skip；"
                    "先判断是否符合信息收集原则；符合才深挖证据；证据不足必须标记存疑，不能写成结论。\n"
                    f"报告类型规则：{json.dumps(policy['report_type_rules'], ensure_ascii=False)}\n"
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


def build_report(decision: RadarDecision, site: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    item = decision.item
    report_id = f"{now_bj().strftime('%Y%m%d')}-{slugify(item.title)}"
    report_type_meta = site["report_types"][decision.report_type]
    source_title = site.get("source_categories", {}).get(item.source_category, item.source_category)
    return {
        "id": report_id,
        "title": decision.report_title,
        "original_title": item.title,
        "url": item.url,
        "summary": item.summary,
        "source_category": item.source_category,
        "source_category_title": source_title,
        "source_name": item.source_name,
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
        "reason": decision.reason,
        "collection_fit": decision.collection_fit,
        "investigation_direction": decision.investigation_direction,
        "uncertainty_flags": decision.uncertainty_flags,
        "facts": [
            {
                "claim": f"{item.source_name} 发布/收录了这条原始线索：{item.title}",
                "type": "confirmed_fact",
                "source_url": item.url,
                "confidence": 0.8 if decision.evidence_level in {"official", "near_source"} else 0.55
            }
        ],
        "sources": [
            {
                "title": item.source_name,
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
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}.card,.item,.report{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:18px}.section{margin-top:32px}
.list{display:grid;gap:12px}.item h3{margin:0 0 8px;font-size:18px}.item p{margin:8px 0}.meta{color:var(--muted);font-size:13px}.badge{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 9px;font-size:12px;color:#33415f;background:#fafbff;margin:0 6px 6px 0}.score{font-weight:700;color:#0a7f42}.source{word-break:break-all}
.report{max-width:880px}.report h1{line-height:1.25}.report h2{margin-top:28px}.ad-slot{border:1px dashed #c8cfdd;border-radius:8px;color:#7b8496;padding:16px;text-align:center;background:#fff;margin:24px 0}
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
    latest = "".join(report_card(r) for r in reports[:10]) or '<p class="meta">本批次暂无可发布报告。</p>'
    return f"""
<section class="hero"><h1>老花的信息差侦察站</h1><p>按报告类型整理线索：先判断是否符合收集原则，再持续深挖证据；没有证据或仍存疑的地方，必须单独标记。</p></section>
<section class="grid">{''.join(type_cards)}</section>
<div class="ad-slot">AdSense 预留位：后续填入 publisher client 后启用</div>
<section class="section"><h2>最新简报</h2><p class="meta">批次：{html.escape(batch["batch_id"])} · 抓取 {batch["fetched_count"]} 条 · 深挖 {batch["deep_count"]} 条 · 简报 {batch["brief_count"]} 条</p><div class="list">{latest}</div></section>
<section class="section card"><h2>数据源原则</h2><ul>{principles}</ul></section>
"""


def render_briefings(batch: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    items = "".join(report_card(r) for r in reports) or '<p class="meta">本批次暂无条目。</p>'
    failures = "".join(f'<li>{html.escape(f["source"])}：{html.escape(f["error"])}</li>' for f in batch.get("failures", [])[:12]) or "<li>本批次无抓取失败。</li>"
    return f"""
<section class="hero"><h1>简报</h1><p>简报是信息线索和证据入口。每条都会保留报告类型、来源分类、收集判断和溯源信息。</p></section>
<section class="card"><p>批次：{html.escape(batch["batch_id"])}</p><p>抓取 {batch["fetched_count"]} 条；深挖 {batch["deep_count"]} 条；简报 {batch["brief_count"]} 条；跳过 {batch["skipped_count"]} 条。</p></section>
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
  <h2>普通读者入口</h2><p>{html.escape(report["reader_hook"])}</p>
  <h2>为什么现在看</h2><p>{html.escape(report["why_now"])}</p>
  <h2>和老花人设的关系</h2><p>{html.escape(report["persona_connection"]["why_it_matters"])}</p>
  <h2>证据链</h2><ul>{facts}</ul>
  <h2>来源</h2><ul>{sources}</ul>
  <h2>还要补证</h2><ul>{follow}</ul>
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


def render_static(batch: dict[str, Any], reports: list[dict[str, Any]], site: dict[str, Any], policy: dict[str, Any]) -> None:
    static = StaticSite(site)
    static.write_assets()
    static.write_page("index.html", "Easton Radar", render_home(batch, reports, site, policy))
    static.write_page("briefings/index.html", "简报 - Easton Radar", render_briefings(batch, reports))
    static.write_page("about/index.html", "关于 - Easton Radar", render_about(site, policy))
    for key, meta in site["report_types"].items():
        static.write_page(f"reports/{key}/index.html", f"{meta['title']} - Easton Radar", render_report_type(key, meta, [r for r in reports if r["report_type"] == key]))
    for report in reports:
        static.write_page(f"items/{report['id']}/index.html", f"{report['title']} - Easton Radar", render_item(report, site))
    static.write_text("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {site['site_url'].rstrip('/')}/sitemap.xml\n")
    static.write_text("sitemap.xml", sitemap(site, reports))
    static.write_text("llms.txt", llms(site, reports))
    static.write_text("ads.txt", "google.com, pub-0000000000000000, DIRECT, f08c47fec0942fa0\n")


def sitemap(site: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    base = site["site_url"].rstrip("/")
    paths = ["", "briefings/", "about/"] + [f"reports/{k}/" for k in site["report_types"]] + [f"items/{r['id']}/" for r in reports]
    today = now_utc().date().isoformat()
    body = "\n".join(f"<url><loc>{base}/{p}</loc><lastmod>{today}</lastmod></url>" for p in paths)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{body}\n</urlset>\n'


def llms(site: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    base = site["site_url"].rstrip("/")
    lines = ["# Easton Radar", "", site["description"], "", "## Report types"]
    for key, meta in site["report_types"].items():
        lines.append(f"- {meta['title']}: {base}/reports/{key}/")
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
    selected = [d for d in decisions if d.decision in {"deep_dive", "brief"}][:48]
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
        "failures": failures,
    }
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
