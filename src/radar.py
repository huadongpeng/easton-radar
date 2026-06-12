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
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
PROMPTS_DIR = ROOT / "prompts"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
SITE_DIR = ROOT / "site"
TOPIC_ARCHIVE_PATH = DATA_DIR / "topic_archive.json"
SEARCH_USAGE_PATH = DATA_DIR / "search_usage.json"

USER_AGENT = "EastonRadar/0.1 (+https://radar.huadongpeng.com)"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
TAVILY_URL = "https://api.tavily.com/search"
TAVILY_USAGE_URL = "https://api.tavily.com/usage"
TOPHUBDATA_URL = "https://api.tophubdata.com"
MAX_ITEMS_PER_SOURCE = 18
MAX_TOTAL_ITEMS = 220
MAX_REPORTS_PER_BATCH = 5
MAX_REPORTS_PER_TOPIC = 2
TRIAGE_BATCH_SIZE = 10
MAX_REPORTS_PER_SOURCE_HOST = 2
MIN_DEEP_DIVE_SCORE = 45
MIN_BRIEF_SCORE = 38
DUPLICATE_LOOKBACK_DAYS = 30
SOURCE_CATEGORY_REPORT_CAPS = {
    "ai_tools": 6,
    "hot_events": 3,
    "developer_business": 4,
    "overseas_and_platforms": 4,
    "platform_policy": 4,
}
TOPHUBDATA_DEFAULT_CID_PLAN = [
    {"cid": "7", "name": "developer", "limit": 2},
    {"cid": "2", "name": "tech", "limit": 2},
    {"cid": "10", "name": "blog", "limit": 1},
    {"cid": "11", "name": "wxmp", "limit": 1},
    {"cid": "6", "name": "finance", "limit": 1},
]
TOPHUBDATA_PAID_DETAIL_DEFAULT_LIMIT = 11
SEARCH_CACHE: dict[str, list[dict[str, str]]] = {}
SEARCH_USAGE_STATE: dict[str, Any] = {}
SEARCH_NOTICE_KEYS: set[str] = set()
TAVILY_USAGE_FETCHED = False
SEARCH_API_CALLS_USED = 0
TOPHUBDATA_PAID_DETAIL_CALLS_USED = 0
BJ_TZ = dt.timezone(dt.timedelta(hours=8))


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
    return now_utc().astimezone(BJ_TZ)


def parse_timestamp(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = email.utils.parsedate_to_datetime(text)
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def bj_iso(value: Any) -> str:
    parsed = parse_timestamp(value)
    if not parsed:
        return str(value or "")
    return parsed.astimezone(BJ_TZ).isoformat()


def bj_day(value: Any) -> str:
    parsed = parse_timestamp(value)
    if parsed:
        return parsed.astimezone(BJ_TZ).date().isoformat()
    text = str(value or "").strip()
    return text[:10] if text else "unknown"


def bj_time(value: Any, fallback: str = "") -> str:
    parsed = parse_timestamp(value)
    if not parsed:
        return str(value or fallback)
    return parsed.astimezone(BJ_TZ).strftime("%Y-%m-%d %H:%M")


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


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def info_once(key: str, message: str) -> None:
    if key in SEARCH_NOTICE_KEYS:
        return
    SEARCH_NOTICE_KEYS.add(key)
    info(message)


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def search_api_call_limit_per_run() -> int:
    return env_int("SEARCH_API_CALL_LIMIT_PER_RUN", 60)


def search_run_has_budget(provider: str, cost: int = 1) -> bool:
    limit = search_api_call_limit_per_run()
    if limit <= 0:
        info_once("search-run-disabled", "Search budget: SEARCH_API_CALL_LIMIT_PER_RUN<=0, skip all web search calls.")
        return False
    if SEARCH_API_CALLS_USED + cost > limit:
        info_once(
            "search-run-exhausted",
            f"Search budget: per-run API call limit reached, used={SEARCH_API_CALLS_USED}, limit={limit}.",
        )
        return False
    return True


def record_search_api_call(provider: str, cost: int = 1) -> None:
    global SEARCH_API_CALLS_USED
    SEARCH_API_CALLS_USED += cost
    state = load_search_usage_state()
    state["current_run"] = {
        "api_calls_used": SEARCH_API_CALLS_USED,
        "api_call_limit": search_api_call_limit_per_run(),
        "last_provider": provider,
        "updated_at": now_utc().isoformat(),
    }
    save_search_usage_state()
    info(f"Search budget: provider={provider}, run_calls_used={SEARCH_API_CALLS_USED}/{search_api_call_limit_per_run()}.")


def numeric_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_search_usage_state() -> dict[str, Any]:
    global SEARCH_USAGE_STATE
    if SEARCH_USAGE_STATE:
        return SEARCH_USAGE_STATE
    try:
        state = json.loads(SEARCH_USAGE_PATH.read_text(encoding="utf-8")) if SEARCH_USAGE_PATH.exists() else {}
    except Exception as exc:
        print(f"Search usage state load failed: {exc}", file=sys.stderr)
        state = {}
    state.setdefault("version", 1)
    state.setdefault("updated_at", now_utc().isoformat())
    state.setdefault("providers", {})
    SEARCH_USAGE_STATE = state
    return SEARCH_USAGE_STATE


def save_search_usage_state() -> None:
    if not SEARCH_USAGE_STATE:
        return
    SEARCH_USAGE_STATE["updated_at"] = now_utc().isoformat()
    write_json(SEARCH_USAGE_PATH, SEARCH_USAGE_STATE)


def update_search_provider_state(provider: str, values: dict[str, Any]) -> None:
    state = load_search_usage_state()
    providers = state.setdefault("providers", {})
    record = providers.setdefault(provider, {})
    record.update(values)
    record["checked_at"] = now_utc().isoformat()


def tavily_search_cost() -> int:
    depth = os.environ.get("TAVILY_SEARCH_DEPTH", "basic").strip().lower() or "basic"
    return 2 if depth == "advanced" else 1


def tavily_usage() -> dict[str, Any] | None:
    global TAVILY_USAGE_FETCHED
    cached = load_search_usage_state().get("providers", {}).get("tavily", {})
    if TAVILY_USAGE_FETCHED and cached.get("source") == "usage_api" and cached.get("checked_at"):
        return cached
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        req = urllib.request.Request(
            TAVILY_USAGE_URL,
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=18) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        print(f"Tavily usage check failed: {exc}", file=sys.stderr)
        update_search_provider_state("tavily", {"enabled": False, "source": "usage_api", "error": str(exc)[:180]})
        TAVILY_USAGE_FETCHED = True
        return None
    key_usage = data.get("key", {}) if isinstance(data, dict) else {}
    account_usage = data.get("account", {}) if isinstance(data, dict) else {}
    key_limit = key_usage.get("limit")
    account_plan_limit = account_usage.get("plan_limit")
    used = numeric_value(key_usage.get("usage") if key_usage.get("usage") is not None else account_usage.get("plan_usage"))
    limit = numeric_value(key_limit if key_limit is not None else account_plan_limit)
    remaining = max(0.0, limit - used) if limit else 0.0
    record = {
        "enabled": True,
        "source": "usage_api",
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "search_usage": numeric_value(key_usage.get("search_usage")),
        "account_plan": account_usage.get("current_plan", ""),
        "account_plan_usage": numeric_value(account_usage.get("plan_usage")),
        "account_plan_limit": numeric_value(account_plan_limit),
        "account_paygo_usage": numeric_value(account_usage.get("paygo_usage")),
        "raw": data,
    }
    update_search_provider_state("tavily", record)
    TAVILY_USAGE_FETCHED = True
    return load_search_usage_state().get("providers", {}).get("tavily", {})


def tavily_has_budget() -> bool:
    usage = tavily_usage()
    if not usage:
        info_once("tavily-no-usage", "Search budget: Tavily usage check unavailable, skip Tavily to avoid paid usage.")
        return False
    if usage.get("enabled") is False:
        info_once("tavily-disabled", f"Search budget: Tavily disabled for this run, error={usage.get('error', '')}.")
        return False
    cost = tavily_search_cost()
    if not search_run_has_budget("tavily", cost):
        return False
    remaining = numeric_value(usage.get("remaining"))
    if numeric_value(usage.get("limit")) <= 0:
        info_once("tavily-no-limit", "Search budget: Tavily limit is unavailable or zero, skip Tavily.")
        return False
    if numeric_value(usage.get("account_paygo_usage")) > 0:
        info_once("tavily-paygo", "Search budget: Tavily pay-as-you-go usage detected, skip Tavily.")
        return False
    if remaining < cost:
        info_once("tavily-exhausted", f"Search budget: Tavily free credits exhausted or too low, remaining={remaining}, cost={cost}.")
        return False
    return True


def record_tavily_success() -> None:
    usage = tavily_usage()
    if not usage:
        return
    cost = tavily_search_cost()
    usage["used"] = numeric_value(usage.get("used")) + cost
    usage["remaining"] = max(0.0, numeric_value(usage.get("remaining")) - cost)
    usage["search_usage"] = numeric_value(usage.get("search_usage")) + cost


def parse_rate_header_numbers(value: str) -> list[float]:
    numbers: list[float] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            numbers.append(float(part))
        except ValueError:
            continue
    return numbers


def update_brave_rate_state(headers: Any) -> None:
    remaining_values = parse_rate_header_numbers(headers.get("X-RateLimit-Remaining", "") if headers else "")
    limit_values = parse_rate_header_numbers(headers.get("X-RateLimit-Limit", "") if headers else "")
    reset_values = parse_rate_header_numbers(headers.get("X-RateLimit-Reset", "") if headers else "")
    second_remaining = remaining_values[0] if remaining_values else None
    second_limit = limit_values[0] if limit_values else None
    monthly_remaining = remaining_values[-1] if remaining_values else None
    monthly_limit = limit_values[-1] if limit_values else None
    reset_seconds = reset_values[-1] if reset_values else None
    monthly_unlimited = monthly_limit == 0
    monthly_used = None
    if monthly_limit is not None and monthly_remaining is not None and monthly_limit > 0:
        monthly_used = max(0.0, monthly_limit - monthly_remaining)
    update_search_provider_state("brave", {
        "enabled": True,
        "source": "rate_limit_headers",
        "second_remaining": second_remaining,
        "second_limit": second_limit,
        "monthly_remaining": monthly_remaining,
        "monthly_limit": monthly_limit,
        "monthly_used": monthly_used,
        "monthly_unlimited": monthly_unlimited,
        "reset_seconds": reset_seconds,
        "raw_headers": {
            "X-RateLimit-Remaining": headers.get("X-RateLimit-Remaining", "") if headers else "",
            "X-RateLimit-Limit": headers.get("X-RateLimit-Limit", "") if headers else "",
            "X-RateLimit-Reset": headers.get("X-RateLimit-Reset", "") if headers else "",
        },
    })
    if monthly_remaining is not None:
        monthly_text = "unlimited" if monthly_unlimited else str(monthly_remaining)
        used_text = "unknown" if monthly_used is None else str(monthly_used)
        info(f"Search budget: Brave second_remaining={second_remaining}, monthly_used={used_text}, monthly_remaining={monthly_text}, monthly_limit={monthly_limit}.")


def brave_has_budget() -> bool:
    record = load_search_usage_state().get("providers", {}).get("brave", {})
    if record.get("enabled") is False and str(record.get("error", "")).startswith(("HTTP 401", "HTTP 403")):
        info_once("brave-disabled", f"Search budget: Brave disabled for this run, error={record.get('error', '')}.")
        return False
    if not search_run_has_budget("brave", 1):
        return False
    second_remaining = record.get("second_remaining")
    if second_remaining is not None and numeric_value(second_remaining) < 1:
        info_once("brave-second-window", f"Search budget: Brave one-second window exhausted, second_remaining={second_remaining}; try next query later.")
        return False
    monthly_limit = record.get("monthly_limit")
    remaining = record.get("monthly_remaining")
    if monthly_limit is not None and numeric_value(monthly_limit) == 0:
        return True
    if remaining is None:
        return True
    if numeric_value(remaining) < 1:
        info_once("brave-exhausted", f"Search budget: Brave free quota exhausted or too low, monthly_remaining={remaining}.")
        return False
    return True


def log_search_budget_preflight() -> None:
    parts: list[str] = [f"run_call_limit={search_api_call_limit_per_run()}"]
    if os.environ.get("TAVILY_API_KEY", "").strip():
        usage = tavily_usage()
        if usage:
            parts.append(f"tavily={usage.get('used', 0)}/{usage.get('limit', 0)} remaining={usage.get('remaining', 0)} source=usage_api")
        else:
            parts.append("tavily=unavailable")
    else:
        parts.append("tavily=missing-key")
    if os.environ.get("BRAVE_SEARCH_API_KEY", "").strip():
        brave = load_search_usage_state().get("providers", {}).get("brave", {})
        if brave.get("monthly_unlimited"):
            parts.append("brave_monthly=unlimited source=rate_limit_headers")
        elif brave.get("monthly_remaining") is not None:
            parts.append(f"brave={brave.get('monthly_used', 'unknown')}/{brave.get('monthly_limit')} remaining={brave.get('monthly_remaining')} source=rate_limit_headers")
        else:
            parts.append("brave=enabled remaining=unknown-until-first-response")
    else:
        parts.append("brave=missing-key")
    info("Search budget preflight: " + "; ".join(parts))


def clean_generated_outputs() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
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
    text = re.sub(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", " ", text)
    text = re.sub(r"\b20\d{2}\b", " ", text)
    text = re.sub(r"\bv?\d+(?:\.\d+){1,3}\b", " ", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    stop_words = {
        "the", "and", "for", "with", "from", "now", "new", "update", "updates",
        "launch", "launches", "release", "released", "announces", "announced",
        "introducing", "preview", "beta", "ga", "today", "official", "blog",
        "发布", "推出", "上线", "更新", "新版", "新功能", "官方", "公告", "预览",
        "工具账本", "案例复盘", "深度调查", "机会拆解", "平台规则", "风险避坑",
    }
    tokens = [token for token in text.split() if token and token not in stop_words]
    return " ".join(tokens[:14])


def fingerprint_tokens(fingerprint: str) -> set[str]:
    tokens: set[str] = set()
    for token in fingerprint.split():
        if len(token) <= 1:
            continue
        tokens.add(token)
        cjk = "".join(ch for ch in token if "\u4e00" <= ch <= "\u9fff")
        if len(cjk) >= 2:
            tokens.update(cjk[index:index + 2] for index in range(0, len(cjk) - 1))
        ascii_parts = re.findall(r"[a-z0-9]{2,}", token)
        tokens.update(ascii_parts)
    return tokens


def similar_fingerprint(left: str, right: str, threshold: float = 0.72) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    left_tokens = fingerprint_tokens(left)
    right_tokens = fingerprint_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    overlap = len(left_tokens & right_tokens)
    smaller = min(len(left_tokens), len(right_tokens))
    union = len(left_tokens | right_tokens)
    containment = overlap / smaller if smaller else 0.0
    jaccard = overlap / union if union else 0.0
    return containment >= threshold or (overlap >= 4 and jaccard >= 0.55)


GENERIC_REPORT_TITLE_PATTERNS = [
    "重要线索",
    "平台规则变化",
    "ai 编程工具变化",
    "模型能力变化",
    "开发者平台变化",
    "工具成本变化",
    "案例线索",
    "机会线索",
    "风险线索",
    "热点争议",
]


def is_generic_report_title(title: str) -> bool:
    title = clean_text(title).lower()
    stripped = title
    for prefix in ["深度调查：", "机会拆解：", "工具账本：", "平台规则：", "案例复盘：", "风险避坑：", "热点观点："]:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):].strip()
            break
    return any(stripped.endswith(pattern) for pattern in GENERIC_REPORT_TITLE_PATTERNS)


def same_host_recent_generic_topic(decision: "RadarDecision", site: dict[str, Any], archive: dict[str, Any], days: int = 7) -> str:
    if not is_generic_report_title(decision.report_title):
        return ""
    host = urllib.parse.urlparse(decision.item.url).netloc.lower().replace("www.", "")
    if not host:
        return ""
    topic_key, _ = topic_direction_for_item(decision.item, decision.report_type, site)
    for item in recent_archive_items(archive, days):
        item_host = urllib.parse.urlparse(str(item.get("url", ""))).netloc.lower().replace("www.", "")
        if item_host == host and item.get("topic_direction") == topic_key:
            return f"近 {days} 天同来源/同栏目已有泛标题选题：{item.get('title', '')}"
    return ""


def report_fingerprints(title: str, original_title: str = "") -> dict[str, str]:
    title_fp = topic_fingerprint(title)
    original_fp = topic_fingerprint(original_title)
    combined_fp = topic_fingerprint(f"{original_title} {title}")
    return {
        "fingerprint": original_fp or title_fp,
        "title_fingerprint": title_fp,
        "original_fingerprint": original_fp,
        "combined_fingerprint": combined_fp or original_fp or title_fp,
    }


def decision_fingerprints(decision: "RadarDecision") -> set[str]:
    values = report_fingerprints(decision.report_title or "", decision.item.title or "")
    return {value for value in values.values() if value}


def archive_fingerprints(item: dict[str, Any]) -> set[str]:
    candidates = {
        str(item.get("fingerprint", "")),
        str(item.get("title_fingerprint", "")),
        str(item.get("original_fingerprint", "")),
        str(item.get("combined_fingerprint", "")),
        topic_fingerprint(str(item.get("title", ""))),
        topic_fingerprint(str(item.get("original_title", ""))),
        topic_fingerprint(f"{item.get('original_title', '')} {item.get('title', '')}"),
    }
    return {value for value in candidates if value}


def any_similar_fingerprint(left_values: set[str], right_values: set[str], threshold: float = 0.72) -> bool:
    return any(similar_fingerprint(left, right, threshold) for left in left_values for right in right_values)


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
    return merge_topic_archive_with_reports(data)


def rebuild_topic_archive_from_reports() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if not REPORTS_DIR.exists():
        return {"version": 1, "items": []}
    for path in sorted(REPORTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:240]:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        seen_at = bj_iso(report.get("fetched_at") or report.get("published_at") or now_utc().isoformat())
        dossier = report.get("selection_dossier") or report.get("material_pack") or {}
        verdict = dossier.get("verdict", {}) if isinstance(dossier, dict) else {}
        fingerprints = report_fingerprints(report.get("title") or "", report.get("original_title", ""))
        items.append({
            "id": report.get("id") or path.stem,
            "batch_id": "rebuilt-from-reports",
            "title": report.get("title") or report.get("original_title") or path.stem,
            "original_title": report.get("original_title", ""),
            "url": report.get("url", ""),
            **fingerprints,
            "topic_direction": report.get("topic_direction", ""),
            "topic_direction_title": report.get("topic_direction_title", ""),
            "verdict": verdict.get("label") or verdict.get("status") or "历史",
            "evidence_level": report.get("evidence_level", ""),
            "score": report.get("score", 0),
            "first_seen_at": seen_at,
            "last_seen_at": seen_at,
            "item_url": f"/items/{report.get('id') or path.stem}/",
        })
    return {"version": 1, "updated_at": now_bj().isoformat(), "items": items}


def merge_topic_archive_with_reports(archive: dict[str, Any]) -> dict[str, Any]:
    rebuilt = rebuild_topic_archive_from_reports()
    existing = archive.get("items", [])
    keys: set[str] = set()
    for item in existing:
        for key in [item.get("url"), item.get("original_fingerprint"), item.get("fingerprint"), item.get("id")]:
            if key:
                keys.add(str(key))
    for item in rebuilt.get("items", []):
        item_keys = {str(key) for key in [item.get("url"), item.get("original_fingerprint"), item.get("fingerprint"), item.get("id")] if key}
        if not item_keys or keys.isdisjoint(item_keys):
            existing.append(item)
            keys.update(item_keys)
    existing.sort(key=lambda x: x.get("last_seen_at", "") or x.get("first_seen_at", ""), reverse=True)
    archive["items"] = existing[:240]
    archive["updated_at"] = archive.get("updated_at") or rebuilt.get("updated_at") or now_bj().isoformat()
    return archive


def recent_archive_items(archive: dict[str, Any], days: int = 14) -> list[dict[str, Any]]:
    cutoff = now_utc() - dt.timedelta(days=days)
    recent: list[dict[str, Any]] = []
    for item in archive.get("items", []):
        value = item.get("last_seen_at") or item.get("first_seen_at") or ""
        seen = parse_timestamp(value)
        if seen is None:
            seen = now_utc()
        if seen >= cutoff:
            recent.append(item)
    return recent


def today_archive_items(archive: dict[str, Any]) -> list[dict[str, Any]]:
    today = now_bj().date().isoformat()
    result: list[dict[str, Any]] = []
    for item in archive.get("items", []):
        value = item.get("last_seen_at") or item.get("first_seen_at") or ""
        if bj_day(value) == today:
            result.append(item)
    return result


def is_duplicate_topic(decision: "RadarDecision", site: dict[str, Any], archive: dict[str, Any], batch_id: str, days: int = DUPLICATE_LOOKBACK_DAYS) -> tuple[bool, str]:
    url = normalize_url(decision.item.url)
    fingerprints = decision_fingerprints(decision)
    for item in today_archive_items(archive):
        if item.get("url") and normalize_url(str(item.get("url"))) == url:
            return True, f"今日已收录同 URL：{item.get('title', '')}"
        if any_similar_fingerprint(archive_fingerprints(item), fingerprints, threshold=0.62):
            return True, f"今日已收录相似选题：{item.get('title', '')}"
    generic_reason = same_host_recent_generic_topic(decision, site, archive)
    if generic_reason:
        return True, generic_reason
    for item in recent_archive_items(archive, days):
        if item.get("url") and normalize_url(str(item.get("url"))) == url:
            return True, f"近 {days} 天已收录同 URL：{item.get('title', '')}"
        if any_similar_fingerprint(archive_fingerprints(item), fingerprints):
            return True, f"近 {days} 天已收录相似选题：{item.get('title', '')}"
    return False, ""


def archive_entry(report: dict[str, Any], batch: dict[str, Any]) -> dict[str, Any]:
    dossier = report.get("selection_dossier") or report.get("material_pack") or {}
    verdict = dossier.get("verdict", {})
    now = bj_iso(batch.get("generated_at") or now_utc().isoformat())
    return {
        "id": report["id"],
        "batch_id": batch["batch_id"],
        "title": display_report_title(report),
        "original_title": report.get("original_title", ""),
        "url": report.get("url", ""),
        **report_fingerprints(display_report_title(report), report.get("original_title", "")),
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
    by_key: dict[str, dict[str, Any]] = {}
    for item in existing:
        key = str(item.get("url") or item.get("original_fingerprint") or item.get("fingerprint") or "")
        if key:
            by_key[key] = item
    for report in reports:
        entry = archive_entry(report, batch)
        key = str(entry.get("url") or entry.get("original_fingerprint") or entry.get("fingerprint") or "")
        previous = by_key.get(key)
        if previous:
            previous.update({k: v for k, v in entry.items() if k not in {"first_seen_at"}})
            previous["last_seen_at"] = entry["last_seen_at"]
        else:
            existing.append(entry)
            by_key[key] = entry
    existing.sort(key=lambda x: x.get("last_seen_at", ""), reverse=True)
    return {"version": 1, "updated_at": bj_iso(batch.get("generated_at", now_utc().isoformat())), "items": existing[:240]}


def report_time_key(report: dict[str, Any]) -> float:
    parsed = parse_timestamp(report.get("fetched_at") or report.get("published_at") or report.get("generated_at"))
    return parsed.timestamp() if parsed else 0.0


def load_report_archive() -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    if not REPORTS_DIR.exists():
        return reports
    for path in sorted(REPORTS_DIR.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Archived report load failed: {path.name} | {exc}", file=sys.stderr)
            continue
        if isinstance(report, dict) and report.get("id"):
            reports.append(report)
    reports.sort(key=report_time_key, reverse=True)
    return reports


def merge_report_archive(current_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for report in load_report_archive():
        by_id[str(report.get("id"))] = report
    for report in current_reports:
        by_id[str(report.get("id"))] = report
    merged = list(by_id.values())
    merged.sort(key=report_time_key, reverse=True)
    return merged[:240]


def fetch_url(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def tavily_search(query: str, limit: int = 5) -> list[dict[str, str]]:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not query.strip() or not api_key:
        return []
    depth = os.environ.get("TAVILY_SEARCH_DEPTH", "basic").strip() or "basic"
    raw_content = os.environ.get("TAVILY_INCLUDE_RAW_CONTENT", "").strip().lower()
    cache_key = f"tavily:{query}:{limit}:{depth}:{raw_content}"
    if cache_key in SEARCH_CACHE:
        return SEARCH_CACHE[cache_key]
    if not tavily_has_budget():
        return []
    body: dict[str, Any] = {
        "query": query,
        "topic": "general",
        "search_depth": depth,
        "max_results": max(1, min(limit, 20)),
        "include_answer": False,
        "include_images": False,
    }
    if raw_content in {"true", "markdown", "text"}:
        body["include_raw_content"] = raw_content
    else:
        body["include_raw_content"] = False
    try:
        req = urllib.request.Request(
            TAVILY_URL,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
        record_search_api_call("tavily", tavily_search_cost())
        record_tavily_success()
    except urllib.error.HTTPError as exc:
        print(f"Tavily search failed for {query}: HTTP {exc.code} {exc.reason}", file=sys.stderr)
        if exc.code in {401, 403, 429}:
            update_search_provider_state("tavily", {
                "enabled": False,
                "source": "usage_api",
                "error": f"HTTP {exc.code} {exc.reason}",
            })
        SEARCH_CACHE[cache_key] = []
        return []
    except Exception as exc:
        print(f"Tavily search failed for {query}: {exc}", file=sys.stderr)
        SEARCH_CACHE[cache_key] = []
        return []
    results: list[dict[str, str]] = []
    for row in data.get("results", []):
        if not isinstance(row, dict):
            continue
        title = clean_text(str(row.get("title", "")))
        url_value = normalize_url(str(row.get("url", "")))
        content = clean_text(str(row.get("raw_content") or row.get("content") or ""))
        if title and url_value.startswith(("http://", "https://")):
            results.append({
                "title": title,
                "url": url_value,
                "query": query,
                "provider": "tavily",
                "content": content[:2400],
                "score": str(row.get("score", "")),
            })
        if len(results) >= limit:
            break
    usage = tavily_usage() or {}
    info(f"Search provider result: provider=tavily, results={len(results)}, remaining={usage.get('remaining', '')}, query={query[:80]}")
    SEARCH_CACHE[cache_key] = results
    return results


def brave_search(query: str, limit: int = 5) -> list[dict[str, str]]:
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not query.strip() or not api_key:
        return []
    cache_key = f"brave:{query}:{limit}"
    if cache_key in SEARCH_CACHE:
        return SEARCH_CACHE[cache_key]
    if not brave_has_budget():
        return []
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
            update_brave_rate_state(resp.headers)
            raw = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode("utf-8", errors="ignore"))
        record_search_api_call("brave", 1)
    except urllib.error.HTTPError as exc:
        update_brave_rate_state(exc.headers)
        print(f"Brave search failed for {query}: HTTP {exc.code} {exc.reason}", file=sys.stderr)
        if exc.code in {401, 403}:
            update_search_provider_state("brave", {
                "enabled": False,
                "source": "rate_limit_headers",
                "error": f"HTTP {exc.code} {exc.reason}",
            })
        elif exc.code == 429:
            update_search_provider_state("brave", {
                "enabled": True,
                "source": "rate_limit_headers",
                "rate_limited": True,
                "error": f"HTTP {exc.code} {exc.reason}",
            })
        SEARCH_CACHE[cache_key] = []
        return []
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
    brave = load_search_usage_state().get("providers", {}).get("brave", {})
    remaining = "unlimited" if brave.get("monthly_unlimited") else brave.get("monthly_remaining", "")
    info(f"Search provider result: provider=brave, results={len(results)}, monthly_remaining={remaining}, query={query[:80]}")
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


def search_web(query: str, limit: int = 5) -> list[dict[str, str]]:
    providers: list[tuple[str, Any]] = []
    if os.environ.get("TAVILY_API_KEY", "").strip():
        providers.append(("tavily", tavily_search))
    if os.environ.get("BRAVE_SEARCH_API_KEY", "").strip():
        providers.append(("brave", brave_search))
    if not providers:
        info_once("search-no-provider", "Search provider unavailable: missing both TAVILY_API_KEY and BRAVE_SEARCH_API_KEY.")
        return []
    for name, func in providers:
        results = func(query, limit=limit)
        if results:
            return results
        info(f"Search provider empty: provider={name}, query={query[:80]}")
    info(f"Search skipped: Tavily/Brave unavailable, exhausted, or returned no result for query={query[:80]}")
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
        provider_content = clean_text(result.get("content", ""))[:text_limit]
        try:
            text = clean_text(fetch_url(url, timeout=16).decode("utf-8", errors="ignore"))[:text_limit]
        except Exception as exc:
            error = str(exc)[:180]
        if not text and provider_content:
            text = provider_content
            if error:
                error = f"{error}; used {result.get('provider', 'provider')} content fallback"[:180]
        evidence.append({
            "title": result.get("title", ""),
            "url": url,
            "query": query,
            "provider": result.get("provider", ""),
            "score": result.get("score", ""),
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


def fetch_tophubdata_json(path: str) -> dict[str, Any]:
    access_key = os.environ.get("TOPHUBDATA_ACCESS_KEY", "").strip()
    if not access_key:
        raise RuntimeError("TOPHUBDATA_ACCESS_KEY is not set")
    url = f"{TOPHUBDATA_URL}{path}"
    req = urllib.request.Request(url, headers={"Authorization": access_key, "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def tophubdata_paid_detail_enabled() -> bool:
    return env_bool("TOPHUBDATA_ENABLE_PAID_DETAIL", True)


def tophubdata_paid_detail_limit_per_run() -> int:
    return env_int("TOPHUBDATA_PAID_DETAIL_LIMIT_PER_RUN", TOPHUBDATA_PAID_DETAIL_DEFAULT_LIMIT)


def tophubdata_paid_detail_has_budget(cost: int = 1) -> bool:
    limit = tophubdata_paid_detail_limit_per_run()
    if limit <= 0:
        info_once("tophubdata-paid-disabled-limit", "TopHubData paid detail: TOPHUBDATA_PAID_DETAIL_LIMIT_PER_RUN<=0, skip paid detail calls.")
        return False
    if TOPHUBDATA_PAID_DETAIL_CALLS_USED + cost > limit:
        info_once(
            "tophubdata-paid-exhausted",
            f"TopHubData paid detail: per-run call limit reached, used={TOPHUBDATA_PAID_DETAIL_CALLS_USED}, limit={limit}.",
        )
        return False
    return True


def record_tophubdata_paid_detail_call(path: str) -> None:
    global TOPHUBDATA_PAID_DETAIL_CALLS_USED
    TOPHUBDATA_PAID_DETAIL_CALLS_USED += 1
    info(f"TopHubData paid API: path={path}, run_calls_used={TOPHUBDATA_PAID_DETAIL_CALLS_USED}/{tophubdata_paid_detail_limit_per_run()}.")


def fetch_tophubdata_paid_path(path: str) -> dict[str, Any]:
    if not tophubdata_paid_detail_enabled():
        raise RuntimeError("TOPHUBDATA_ENABLE_PAID_DETAIL is not enabled")
    if not tophubdata_paid_detail_has_budget():
        raise RuntimeError("TopHubData paid API budget exhausted")
    data = fetch_tophubdata_json(path)
    record_tophubdata_paid_detail_call(path)
    if data.get("error"):
        raise RuntimeError(str(data)[:240])
    return data


def fetch_tophubdata_paid_detail(hashid: str) -> dict[str, Any]:
    return fetch_tophubdata_paid_path(f"/nodes/{urllib.parse.quote(hashid)}")


def fetch_tophubdata_nodes(max_pages: int = 5) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        data = fetch_tophubdata_json(f"/nodes?p={page}")
        if data.get("error"):
            raise RuntimeError(str(data)[:240])
        rows = data.get("data", [])
        if not isinstance(rows, list):
            break
        nodes.extend(row for row in rows if isinstance(row, dict))
        if len(rows) < 100:
            break
    return nodes


def parse_tophubdata_nodes(source_category: str, group: dict[str, Any]) -> list[SourceItem]:
    """Free endpoint: node discovery only. It does not include hot item titles."""
    nodes = fetch_tophubdata_nodes(int(group.get("tophubdata_node_pages", 5) or 5))
    info(f"TopHubData free node discovery returned {len(nodes)} nodes; free mode does not import node names as topic items.")
    if os.environ.get("TOPHUBDATA_INCLUDE_FREE_NODE_ITEMS", "").strip().lower() not in {"1", "true", "yes"}:
        return []
    items: list[SourceItem] = []
    for node in nodes[:MAX_ITEMS_PER_SOURCE]:
        name = clean_text(node.get("name", ""))
        display = clean_text(node.get("display", ""))
        hashid = clean_text(node.get("hashid", ""))
        domain = clean_text(node.get("domain", ""))
        if not name or not hashid:
            continue
        title = f"{name}{display} 热榜节点"
        summary = f"TopHubData 免费节点发现：{name} / {display} / {domain}。免费模式只用于确认可用榜单，不拉取会扣费的热点详情。"
        url = f"https://tophub.today/n/{hashid}"
        items.append(make_item(source_category, "TopHubData", "api-free", title, url, summary, "", "tophubdata:/nodes"))
    return items


def select_tophubdata_nodes(nodes: list[dict[str, Any]], group: dict[str, Any]) -> list[dict[str, Any]]:
    cid_plan = group.get("tophubdata_cid_plan") or TOPHUBDATA_DEFAULT_CID_PLAN
    if not isinstance(cid_plan, list):
        cid_plan = TOPHUBDATA_DEFAULT_CID_PLAN
    exclude_keywords = [str(value).lower() for value in group.get("tophubdata_exclude_node_keywords", []) if str(value).strip()]

    def node_allowed(node: dict[str, Any]) -> bool:
        text = f"{node.get('name', '')} {node.get('display', '')} {node.get('domain', '')}".lower()
        return not any(keyword and keyword in text for keyword in exclude_keywords)

    def node_priority(node: dict[str, Any]) -> tuple[int, int, str]:
        display = str(node.get("display", ""))
        name = str(node.get("name", ""))
        domain = str(node.get("domain", ""))
        text = f"{name} {display} {domain}".lower()
        hot_rank = 0 if any(word in display for word in ["热榜", "热搜", "热门", "热议", "趋势"]) else 1
        persona_rank = 0 if any(word in text for word in ["v2ex", "github", "少数派", "it之家", "36氪", "机器之心", "量子位"]) else 1
        return hot_rank, persona_rank, f"{name}/{display}/{domain}"

    selected: list[dict[str, Any]] = []
    used_hashids: set[str] = set()
    for plan in cid_plan:
        if not isinstance(plan, dict):
            continue
        cid = str(plan.get("cid", "")).strip()
        limit = max(0, int(plan.get("limit", 0) or 0))
        if not cid or limit <= 0:
            continue
        matches = [node for node in nodes if str(node.get("cid", "")) == cid and node_allowed(node)]
        matches.sort(key=node_priority)
        accepted = 0
        for node in matches:
            hashid = first_text(node, ["hashid", "id"])
            if hashid and hashid not in used_hashids:
                selected.append(node)
                used_hashids.add(hashid)
                accepted += 1
            if accepted >= limit:
                break
    info(f"TopHubData selected cid-plan nodes: {', '.join(str(node.get('cid', '')) + ':' + str(node.get('name', '')) + '/' + str(node.get('display', '')) for node in selected)}")
    return selected


def first_text(data: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None:
            text = clean_text(str(value))
            if text:
                return text
    return ""


def tophubdata_hot_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    payload: Any = data.get("data", data)
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ["items", "list", "data", "hot_list", "hotList", "posts", "topics"]:
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def tophubdata_topic_keywords(group: dict[str, Any]) -> list[str]:
    values = group.get("tophubdata_topic_keywords", [])
    if not isinstance(values, list):
        return []
    return [str(value).lower() for value in values if str(value).strip()]


def tophubdata_row_text(row: dict[str, Any]) -> str:
    return " ".join(
        first_text(row, keys)
        for keys in [
            ["title", "name", "word", "query", "keyword"],
            ["summary", "desc", "description", "abstract", "extra"],
            ["url", "link", "source_url", "sourceUrl"],
            ["domain", "sitename", "views"],
        ]
    ).lower()


def tophubdata_row_matches_direction(node: dict[str, Any], row: dict[str, Any], group: dict[str, Any]) -> bool:
    cid = str(node.get("cid", ""))
    domain = str(node.get("domain", "")).lower()
    node_name = str(node.get("name", "")).lower()
    keywords = tophubdata_topic_keywords(group)
    if not keywords:
        return True
    text = tophubdata_row_text(row)
    if any(keyword and keyword in text for keyword in keywords):
        return True
    if cid == "7" or any(value in domain or value in node_name for value in ["v2ex", "github", "segmentfault"]):
        developer_terms = ["api", "sdk", "github", "开源", "程序员", "开发", "后端", "前端", "架构", "数据库", "远程", "外包", "岗位", "agent", "ai"]
        return any(term_in_text(text, term) for term in developer_terms)
    return False


def make_tophubdata_hot_item(source_category: str, node: dict[str, Any], row: dict[str, Any], index: int) -> SourceItem | None:
    node_name = first_text(node, ["name", "display", "title"]) or "TopHubData"
    hashid = first_text(node, ["hashid", "id"])
    title = first_text(row, ["title", "name", "word", "query", "keyword"])
    if not title:
        return None
    url = first_text(row, ["url", "mobileUrl", "mobile_url", "link", "source_url", "sourceUrl"])
    if not url and hashid:
        url = f"https://tophub.today/n/{hashid}"
    if not url:
        url = f"tophubdata:{node_name}:{title}"
    summary_parts = [
        f"TopHubData 热榜：{node_name}",
        f"排名：{first_text(row, ['rank', 'index']) or str(index + 1)}",
    ]
    hot_value = first_text(row, ["hot", "hotValue", "hot_value", "score", "views", "comment"])
    if hot_value:
        summary_parts.append(f"热度：{hot_value}")
    description = first_text(row, ["summary", "desc", "description", "abstract", "extra"])
    if description:
        summary_parts.append(description)
    published_at = first_text(row, ["created_at", "createdAt", "updated_at", "updatedAt", "publish_time", "publishTime"])
    feed_url = f"tophubdata:/nodes/{hashid}" if hashid else "tophubdata:/nodes"
    return make_item(source_category, f"TopHubData/{node_name}", "api-paid", title, url, "。".join(summary_parts), published_at, feed_url)


def parse_tophubdata_search_items(source_category: str, group: dict[str, Any]) -> list[SourceItem]:
    if not group.get("tophubdata_search_enabled", False) or not tophubdata_paid_detail_enabled():
        return []
    queries = group.get("tophubdata_search_queries", [])
    if not isinstance(queries, list):
        return []
    item_limit = max(1, int(group.get("tophubdata_search_limit_per_query", 4) or 4))
    items: list[SourceItem] = []
    for query_config in queries:
        if not isinstance(query_config, dict):
            continue
        query = clean_text(str(query_config.get("q", "")))
        if not query or not tophubdata_paid_detail_has_budget():
            continue
        params = urllib.parse.urlencode({"q": query, "p": 1})
        try:
            data = fetch_tophubdata_paid_path(f"/search?{params}")
        except Exception as exc:
            info(f"TopHubData search failed for {query}: {str(exc)[:160]}")
            continue
        rows = tophubdata_hot_rows(data)
        accepted = 0
        for index, row in enumerate(rows):
            if not tophubdata_row_matches_direction({}, row, group):
                continue
            item = make_tophubdata_hot_item(source_category, {"name": f"Search:{query}", "hashid": ""}, row, index)
            if item:
                item.source_name = f"TopHubData/Search:{query}"
                item.feed_url = f"tophubdata:/search?q={query}"
                items.append(item)
                accepted += 1
            if accepted >= item_limit:
                break
    info(f"TopHubData search imported {len(items)} hot items from configured queries.")
    return items


def parse_tophubdata_hot_items(source_category: str, group: dict[str, Any]) -> list[SourceItem]:
    if not tophubdata_paid_detail_enabled():
        info_once("tophubdata-paid-disabled", "TopHubData latest hot list disabled: set TOPHUBDATA_ENABLE_PAID_DETAIL=true or unset it to import hot list items.")
        return []
    nodes = fetch_tophubdata_nodes(int(group.get("tophubdata_node_pages", 5) or 5))
    selected_nodes = select_tophubdata_nodes(nodes, group)
    items: list[SourceItem] = []
    item_limit_per_node = max(1, env_int("TOPHUBDATA_ITEM_LIMIT_PER_NODE", int(group.get("tophubdata_item_limit_per_node", 4) or 4)))
    for node in selected_nodes:
        if len(items) >= MAX_ITEMS_PER_SOURCE or not tophubdata_paid_detail_has_budget():
            break
        hashid = first_text(node, ["hashid", "id"])
        if not hashid:
            continue
        try:
            data = fetch_tophubdata_paid_detail(hashid)
        except Exception as exc:
            info(f"TopHubData paid detail failed for {hashid}: {str(exc)[:160]}")
            continue
        accepted_for_node = 0
        for index, row in enumerate(tophubdata_hot_rows(data)):
            if not tophubdata_row_matches_direction(node, row, group):
                continue
            item = make_tophubdata_hot_item(source_category, node, row, index)
            if item:
                items.append(item)
                accepted_for_node += 1
            if accepted_for_node >= item_limit_per_node:
                break
            if len(items) >= MAX_ITEMS_PER_SOURCE:
                break
    info(f"TopHubData paid detail imported {len(items)} hot items from {TOPHUBDATA_PAID_DETAIL_CALLS_USED} paid calls.")
    items.extend(parse_tophubdata_search_items(source_category, group))
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
        if group.get("tophubdata_free_nodes") and os.environ.get("TOPHUBDATA_ACCESS_KEY", "").strip():
            try:
                for item in parse_tophubdata_nodes(source_category, group):
                    if item.url not in seen and not should_drop_item(item):
                        items.append(item)
                        seen.add(item.url)
            except Exception as exc:
                failures.append({"source_category": source_category, "source": "tophubdata:/nodes", "error": str(exc)[:240]})
        if group.get("tophubdata_hot_details") and os.environ.get("TOPHUBDATA_ACCESS_KEY", "").strip():
            try:
                for item in parse_tophubdata_hot_items(source_category, group):
                    if item.url not in seen and not should_drop_item(item):
                        items.append(item)
                        seen.add(item.url)
            except Exception as exc:
                failures.append({"source_category": source_category, "source": "tophubdata:/nodes/@hashid", "error": str(exc)[:240]})
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
    if source_category == "hot_events" or any_term_in_text(text, ["backlash", "controversy", "outrage", "protest", "criticism", "lawsuit", "ban", "blocked", "remove", "cap usage", "limit usage", "反弹", "争议", "吐槽", "封禁", "下架", "限制", "起诉", "不满"]):
        return "hot-event"
    if source_category == "platform_policy":
        return "platform-rules"
    if any(w in text for w in ["pricing", "price", "cost", "token", "bill", "revenue", "mrr", "adsense", "价格", "成本", "收入", "账单"]):
        return "tool-ledger"
    if any(w in text for w in ["policy", "rules", "compliance", "seo", "google", "search", "stripe", "paddle", "规则", "合规", "平台"]):
        return "platform-rules"
    if any_term_in_text(text, ["scam", "risk", "ban", "blocked", "lawsuit", "安全", "风险", "封号", "骗局"]):
        return "risk-warning"
    if any(w in text for w in ["case study", "show hn", "launched", "built", "github", "open source", "开源", "复盘"]):
        return "case-study"
    if source_category in {"developer_business", "overseas_and_platforms"}:
        return "opportunity"
    return "investigation"


MINOR_CHANGELOG_PATTERNS = [
    "changelog",
    "now available",
    "available through",
    "manage ",
    "with wrangler",
    "rollback support",
    "smtp submission",
    "scatter plots",
    "radar charts",
    "cost centers",
    "domain search",
    "hosted images",
    "deprecating",
    "sdk features",
    "binding",
    "cli",
]

MAJOR_IMPACT_TERMS = [
    "pricing",
    "price",
    "cost",
    "bill",
    "billing",
    "limit",
    "quota",
    "retention",
    "privacy",
    "policy",
    "compliance",
    "ban",
    "remove",
    "breaking",
    "migration",
    "security",
    "lawsuit",
    "封号",
    "下架",
    "涨价",
    "成本",
    "账单",
    "额度",
    "隐私",
    "合规",
    "迁移",
    "安全",
]


def term_in_text(text: str, term: str) -> bool:
    if term.isascii() and re.search(r"[a-zA-Z]", term):
        return re.search(rf"(?<![a-zA-Z0-9]){re.escape(term)}(?![a-zA-Z0-9])", text) is not None
    return term in text


def any_term_in_text(text: str, terms: list[str]) -> bool:
    return any(term_in_text(text, term.lower()) for term in terms)


def is_minor_changelog_text(text: str) -> bool:
    lower = text.lower()
    if not any_term_in_text(lower, MINOR_CHANGELOG_PATTERNS):
        return False
    return not any_term_in_text(lower, MAJOR_IMPACT_TERMS)


def heuristic_decision(item: SourceItem, site: dict[str, Any]) -> RadarDecision:
    text = f"{item.title} {item.summary}".lower()
    groups = {
        "money": ["pricing", "price", "cost", "revenue", "mrr", "adsense", "affiliate", "payment", "stripe", "paddle", "价格", "成本", "收入", "收款", "变现"],
        "ai": ["ai", "llm", "agent", "openai", "claude", "copilot", "deepseek", "model", "token", "cursor", "自动化"],
        "dev": ["github", "developer", "api", "sdk", "cloudflare", "vercel", "database", "server", "开源", "程序员", "开发"],
        "platform": ["policy", "rules", "compliance", "seo", "search", "google", "amazon", "shopify", "tiktok", "合规", "平台", "规则", "出海"],
        "heat": ["backlash", "controversy", "outrage", "criticism", "lawsuit", "ban", "blocked", "remove", "cap usage", "limit usage", "反弹", "争议", "吐槽", "不满", "封禁", "下架", "限制", "起诉"],
    }
    hits: dict[str, list[str]] = {}
    score = 0
    for group, words in groups.items():
        matched = [word for word in words if term_in_text(text, word)]
        if matched:
            hits[group] = matched[:5]
            score += 12 + min(len(matched), 4) * 3
    host = urllib.parse.urlparse(item.url).netloc
    official_hosts = ["openai.com", "google", "github.blog", "cloudflare.com", "amazon.com", "huggingface.co"]
    evidence_level = "official" if any(x in host for x in official_hosts) else "near_source"
    if evidence_level == "official":
        score += 12
    if item.source_type.startswith("api"):
        score += 5
    if "heat" in hits:
        score += 10
    if is_minor_changelog_text(text):
        score = min(score, 28)
    score = min(score, 100)
    report_type = infer_report_type(text, item.source_category)
    report_title = make_report_title(item, report_type, site)
    if score >= 55 and not is_minor_changelog_text(text):
        decision = "deep_dive"
    elif score >= 32:
        decision = "brief"
    else:
        decision = "skip"
    reader_hook = infer_reader_hook(hits, report_type)
    reject_reason = "" if decision != "skip" else "老花人设解读角度、成本/平台/工具链关联不够明确，先不进入候选池。"
    return normalize_triage_decision(RadarDecision(
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
    ))


def infer_reader_hook(hits: dict[str, list[str]], report_type: str) -> str:
    if report_type == "tool-ledger":
        return "适合按老花的技术人账本视角拆：AI/API/云服务/开发工具成本到底怎么变。"
    if report_type == "platform-rules":
        return "适合按老花的规则避坑视角拆：平台规则、搜索流量、出海路径或合规边界怎么影响小团队。"
    if report_type == "risk-warning":
        return "适合按老花的避坑视角拆：成本、合规、封号或营销话术里有哪些坑。"
    if report_type == "hot-event":
        return "适合按老花的热点观点视角拆：这件事里谁在包装叙事、谁承担成本，普通技术人容易在哪一步误判。"
    if report_type == "case-study":
        return "适合按老花的案例复盘视角拆：哪些条件可复制，哪些只是别人自己的局。"
    if report_type == "opportunity":
        return "适合按老花的副业和信息差视角拆：门槛、用户、流量和现金流是否真实。"
    if "ai" in hits and "dev" in hits:
        return "适合按老花的 AI 开发视角拆：它会不会改变工作流、API 成本或程序员工具链。"
    return "需要继续确认它是否符合老花的人设主线，以及目标读者会不会关心其中的成本、岗位、工具链或机会变化。"


def normalize_triage_decision(decision: RadarDecision) -> RadarDecision:
    text = f"{decision.item.title} {decision.item.summary}".lower()
    mismatched_brands = ["aws", "amazon", "openai", "stripe", "github", "cloudflare", "vercel", "google", "apple", "shopify"]
    if decision.report_type == "hot-event":
        title_lower = decision.report_title.lower()
        for brand in mismatched_brands:
            if term_in_text(title_lower, brand) and not term_in_text(text, brand):
                decision.report_title = make_report_title(decision.item, decision.report_type, load_json(CONFIG_DIR / "site.json"))
                break
    tension = topic_tension({
        "title": decision.report_title,
        "summary": decision.item.summary,
        "reader_hook": decision.reader_hook,
        "report_type": decision.report_type,
        "evidence_level": decision.evidence_level,
    })
    if is_minor_changelog_text(text):
        decision.score = min(decision.score, 28)
        decision.decision = "skip"
        decision.reject_reason = decision.reject_reason or "只是官方 changelog/CLI/API/SDK 单点更新，暂未发现迁移成本、账单风险、用户反弹或普通技术人的选择题。"
    elif decision.evidence_level == "weak":
        decision.score = min(decision.score, 50)
        if decision.decision == "deep_dive":
            decision.decision = "brief"
    elif tension.get("score", 0) < 7 and decision.decision == "deep_dive":
        decision.decision = "brief"
        decision.score = min(decision.score, 54)
    if is_generic_report_title(decision.report_title):
        decision.score = min(decision.score, 37)
        if decision.decision != "skip":
            decision.decision = "skip"
        decision.reject_reason = decision.reject_reason or "标题仍停留在泛化公司/平台变化层面，未形成具体冲突、成本、规则或选择题。"
    if decision.decision == "deep_dive" and decision.score < MIN_DEEP_DIVE_SCORE:
        decision.decision = "brief" if decision.score >= MIN_BRIEF_SCORE else "skip"
    if decision.decision == "brief" and decision.score < MIN_BRIEF_SCORE:
        decision.decision = "skip"
        decision.reject_reason = decision.reject_reason or "分数低于 brief 入池线，未达到选题候选标准。"
    decision.traceability = {**decision.traceability, "topic_tension_score": tension.get("score", 0)}
    return decision


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
        "hot-event": "热点争议",
        "investigation": "重要线索",
    }
    return fallback.get(report_type, "重要线索")


def make_report_title(item: SourceItem, report_type: str, site: dict[str, Any]) -> str:
    report_name = site["report_types"].get(report_type, {}).get("title", "线索")
    host = urllib.parse.urlparse(item.url).netloc.replace("www.", "")
    source = display_source_name(item) or host
    lower = f"{item.title} {source}".lower()
    known = ["xAI", "Grok", "OpenAI", "Claude", "GitHub", "Copilot", "Cloudflare", "Amazon", "AWS", "Google", "Gemini", "DeepSeek", "Hugging Face", "Vercel", "Stripe", "Microsoft", "Apple", "Siri", "Shopify"]
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
    if report_type == "hot-event":
        if any(word in lower for word in ["xai", "grok"]):
            return f"{report_name}：xAI Grok 安全风波"
        if any(word in lower for word in ["lawsuit", "fired", "whistleblower", "retaliation", "safety"]):
            return f"{report_name}：{brand or source_label} 员工与安全争议"
        if ascii_dominant(topic):
            return f"{report_name}：{brand or source_label} 热点争议"
        return f"{report_name}：{topic}"
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
        return "部分符合收集原则：可以进入可选池，但证据链、人设解读角度或目标读者兴趣还不够完整。"
    return "暂不符合深挖原则：相关性、证据质量、人设解读角度或目标读者兴趣不足。"


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
                "保留老花人设下的解读角度，不把冷门技术写成圈内自嗨。"
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
    minor_changelog = is_minor_changelog_text(text)
    if any(word in text for word in ["freelance", "contract", "invoice", "chargeback", "debt", "lawsuit", "scam", "arbitrage", "外包", "接项目", "合同", "回款", "发票", "催收", "债务", "起诉", "骗局", "套利", "卖课", "信息差"]):
        meta = directions.get("side-info")
        if meta:
            return "side-info", meta
    if any(word in text for word in ["google search", "seo", "ai seo", "ranking", "traffic", "recommendation", "policy", "license", "terms", "compliance", "公众号", "小红书", "视频号", "搜索", "流量", "推荐", "规则", "合规", "账号"]):
        meta = directions.get("tools-rules")
        if meta:
            return "tools-rules", meta
    if any(word in text for word in ["stripe", "shopify", "payment", "commerce", "cross-border", "global demand", "出海", "跨境", "支付", "收款", "独立站"]):
        meta = directions.get("cross-border")
        if meta:
            return "cross-border", meta
    if any(word in text for word in ["automation", "workflow", "assistant", "agentic", "template", "no-code", "n8n", "zapier", "个人助手", "自动化", "工作流", "实操", "办公", "内容生产", "健康助手", "客服", "运营工具"]):
        meta = directions.get("tools-rules")
        if meta:
            return "tools-rules", meta
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
        if key == "tools-rules" and report_type == "platform-rules":
            score += 2
        if key == "cross-border" and any(word in text for word in ["stripe", "shopify", "payment", "commerce", "cross-border", "global demand", "出海", "跨境", "支付", "收款", "独立站"]):
            score += 10
        if key == "ai-frontier" and any(word in text for word in ["ai", "llm", "agent", "model", "anthropic", "openai", "claude", "codex", "copilot", "frontier", "模型", "智能体"]):
            score += 6
        if key == "ai-frontier" and minor_changelog:
            score -= 8
        if key == "tools-rules" and minor_changelog:
            score += 3
        if key == "tools-rules" and any(word in text for word in ["automation", "workflow", "assistant", "agentic", "template", "no-code", "个人助手", "自动化", "工作流", "实操"]):
            score += 8
        if key == "tools-rules" and any(word in text for word in ["policy", "license", "terms", "compliance", "regulation", "search", "seo", "traffic", "规则", "合规", "协议", "搜索", "流量", "推荐"]):
            score += 6
        if key == "side-info" and any(word in text for word in ["show hn", "producthunt", "product hunt", "v2ex", "mrr", "saas", "独立开发", "副业", "工具站", "开源", "信息差"]):
            score += 5
        if key == "side-info" and any(word in text for word in ["freelance", "contract", "invoice", "chargeback", "debt", "lawsuit", "scam", "arbitrage", "外包", "合同", "回款", "催收", "债务", "骗局", "套利"]):
            score += 8
        if score > best_score:
            best_key = key
            best_score = score

    if not best_key:
        best_key = next(iter(directions))
    return best_key, directions[best_key]


def legacy_deepseek_triage_batch(items: list[SourceItem], site: dict[str, Any], policy: dict[str, Any]) -> list[RadarDecision]:
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
                    "(investigation|opportunity|tool-ledger|platform-rules|case-study|risk-warning|hot-event), score(0-100),"
                    "reader_hook, why_now, evidence_level(official|near_source|media|weak), reason, reject_reason,"
                    "collection_fit, investigation_direction, uncertainty_flags。\n"
                    "report_title 必须是中文 Radar 标题，可以保留产品名/公司名，但不能整句英文照搬原题。\n"
                    "硬规则：优先官方/一手/近源；反爬论坛抓不到就放弃；冷门技术没有老花人设解读角度就 skip；"
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
                normalize_triage_decision(RadarDecision(
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
                ))
            )
        return decisions
    except Exception as exc:
        print(f"DeepSeek triage batch failed, fallback this batch to heuristic: {exc}", file=sys.stderr)
        return []


def legacy_deepseek_triage(items: list[SourceItem], site: dict[str, Any], policy: dict[str, Any]) -> list[RadarDecision] | None:
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return None
    decisions: list[RadarDecision] = []
    batch_size = 25
    for offset in range(0, min(len(items), 100), batch_size):
        batch = items[offset:offset + batch_size]
        decisions.extend(legacy_deepseek_triage_batch(batch, site, policy))
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
        "max_tokens": 4200,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": (
                    f"{load_prompt('01_triage_flash.md')}\n\n"
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
                normalize_triage_decision(RadarDecision(
                    item=item,
                    decision=str(row.get("decision") or fallback.decision),
                    report_type=str(row.get("report_type") or fallback.report_type),
                    report_title=str(row.get("report_title") or fallback.report_title),
                    score=int(row.get("score", fallback.score) or fallback.score),
                    reader_hook=str(row.get("reader_hook") or fallback.reader_hook),
                    why_now=str(row.get("why_now") or fallback.why_now),
                    evidence_level=str(row.get("evidence_level") or fallback.evidence_level),
                    reason=str(row.get("reason") or fallback.reason)[:80],
                    reject_reason=str(row.get("reject_reason") or fallback.reject_reason),
                    collection_fit=str(row.get("collection_fit") or fallback.collection_fit),
                    investigation_direction=str(row.get("investigation_direction") or fallback.investigation_direction),
                    uncertainty_flags=row.get("uncertainty_flags", fallback.uncertainty_flags) or fallback.uncertainty_flags,
                    traceability={**fallback.traceability, "heuristic": False, "model": body["model"], "triage_batch_size": len(items)},
                ))
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
        "topic_tension": report.get("topic_tension") or topic_tension(report),
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
        "audience_fit": audience_fit(report),
        "mass_interest_hook": mass_interest_hook(report),
        "topic_tension": topic_tension(report),
        "persona_discussion_question": persona_discussion_question(report),
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
        f"{load_prompt('02_research_plan_flash.md')}\n\n"
        "请只输出 JSON：{\"core_question\":\"\",\"persona_discussion_question\":\"\",\"hidden_public_issue\":\"\",\"must_verify\":[],\"search_queries\":[],\"best_sources_to_find\":[],"
        "\"expert_challenge_points\":[],\"do_not_claim_yet\":[],\"can_publish_as_radar_if_missing\":\"\",\"downstream_materials_needed\":[]}。\n"
        "要求：search_queries 给 4-8 个具体搜索词；优先官方、近源、价格页、文档、GitHub、监管/平台规则、真实案例、反方材料。"
        "如果这个题太窄、太冷、人设解读角度弱或目标读者兴趣低，要明确写出来。"
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


def topic_tension_score(value: Any) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(10, number))


def topic_tension_from(dossier: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    tension = dossier.get("topic_tension", {}) if isinstance(dossier, dict) else {}
    if not isinstance(tension, dict):
        tension = {}
    fallback = topic_tension(report)
    merged = {**fallback, **{key: value for key, value in tension.items() if value not in (None, "", [])}}
    merged["score"] = topic_tension_score(merged.get("score", fallback.get("score", 0)))
    return merged


def persona_discussion_question(report: dict[str, Any], tension: dict[str, Any] | None = None) -> str:
    tension = tension or topic_tension(report)
    report_type = report.get("report_type", "")
    title = display_report_title(report)
    if report_type == "hot-event":
        return "这件事里，普通技术人应该相信平台/厂商的说法，还是先按成本、规则和风险重新算一遍？"
    if report_type == "tool-ledger":
        return "这笔工具账到底是提高效率，还是把不可见成本转嫁给普通程序员和小团队？"
    if report_type == "platform-rules":
        return "小团队该不该继续把关键工作流押在这个平台规则上？"
    if report_type == "risk-warning":
        return "这是真机会，还是又一个普通技术人容易被包装话术带偏的坑？"
    if report_type == "opportunity":
        return "这个机会普通技术人能低成本验证，还是只适合少数有资源的人？"
    if tension.get("debate_question"):
        return str(tension["debate_question"])
    return f"{title} 这件事，普通技术人到底该跟进、观望，还是直接避开？"


def topic_level(report: dict[str, Any], dossier: dict[str, Any] | None = None) -> str:
    dossier = dossier or report.get("selection_dossier") or report.get("material_pack") or {}
    verdict = dossier.get("verdict", {}) if isinstance(dossier, dict) else {}
    label = str(verdict.get("label") or verdict.get("status") or "").strip()
    if label in {"推荐", "可选"}:
        if label == "推荐" and topic_tension_from(dossier, report).get("score", 0) < 7:
            return "可选"
        return label
    score = int(report.get("score", 0) or 0)
    confidence = confidence_score(dossier.get("confidence", 0)) if isinstance(dossier, dict) else 0
    evidence = report.get("evidence_level", "")
    decision = report.get("decision", "")
    quality_gate = dossier.get("quality_gate", {}) if isinstance(dossier, dict) else {}
    tension = topic_tension_from(dossier, report) if isinstance(dossier, dict) else topic_tension(report)
    gaps = len(report.get("uncertainty_flags", []))
    if (
        decision == "deep_dive"
        and evidence in {"official", "near_source"}
        and score >= 68
        and (confidence >= 62 or quality_gate.get("pass") is True)
        and tension.get("score", 0) >= 7
        and gaps <= 3
    ):
        return "推荐"
    return "可选"


def normalize_selection_dossier(report: dict[str, Any], dossier: dict[str, Any]) -> dict[str, Any]:
    verdict = dossier.setdefault("verdict", {})
    previous = str(verdict.get("label") or verdict.get("status") or "").strip()
    level = topic_level(report, dossier)
    verdict["status"] = "推荐选题" if level == "推荐" else "可选选题"
    verdict["label"] = level
    if previous and previous not in {"推荐", "可选"}:
        verdict["previous_label"] = previous
    if not verdict.get("reason"):
        verdict["reason"] = "证据、人设解读角度、目标读者兴趣和传播张力较完整，建议优先进入写作框架。" if level == "推荐" else "可以保留为候选选题，写作前需要按缺口继续补证，尤其确认是否有真实冲突点和讨论空间。"
    dossier.setdefault("audience_fit", audience_fit(report))
    dossier.setdefault("mass_interest_hook", mass_interest_hook(report))
    dossier["topic_tension"] = topic_tension_from(dossier, report)
    dossier.setdefault("persona_discussion_question", persona_discussion_question(report, dossier["topic_tension"]))
    return dossier


def enforce_evidence_gate(dossier: dict[str, Any], evidence: list[dict[str, str]]) -> dict[str, Any]:
    if evidence:
        return dossier
    verdict = dossier.setdefault("verdict", {})
    verdict["status"] = "可选选题"
    verdict["label"] = "可选"
    verdict["reason"] = "补证搜索没有拿到可用结果，只能保留为可选线索；写作前必须补官方或近源材料。"
    dossier["confidence"] = min(confidence_score(dossier.get("confidence", 0)), 30)
    missing = dossier.setdefault("missing_materials", [])
    if isinstance(missing, list):
        missing.append("补证搜索结果为 0，需要先解决搜索后端或改用官方/近源材料补证。")
    not_claimable = dossier.setdefault("not_claimable", [])
    if isinstance(not_claimable, list):
        not_claimable.append("不能在无补证结果时声称该选题已经具备可写条件。")
    return dossier


def apply_quality_gate(report: dict[str, Any], dossier: dict[str, Any], evidence: list[dict[str, str]]) -> dict[str, Any]:
    data = deepseek_json(
        [
            {"role": "system", "content": load_prompt("04_quality_gate_flash.md")},
            {"role": "user", "content": json.dumps({
                "candidate": compact_report_seed(report),
                "selection_dossier": dossier,
                "evidence_count": len(evidence),
                "evidence": evidence,
            }, ensure_ascii=False)},
        ],
        max_tokens=2200,
        temperature=0.1,
        timeout=70,
    )
    if not isinstance(data, dict):
        dossier["quality_gate"] = {"pass": False, "recommendation": "hold", "error": "quality gate LLM call failed"}
        verdict = dossier.setdefault("verdict", {})
        verdict["status"] = "可选选题"
        verdict["label"] = "可选"
        verdict["reason"] = "质量闸调用失败，只保留为可选线索；写作前需要人工复核证据和缺口。"
        return dossier
    dossier["quality_gate"] = data
    quality_tension_ok = data.get("topic_tension_ok", True) is not False
    dossier_tension = topic_tension_from(dossier, report)
    if data.get("pass") is True and data.get("recommendation") == "publish" and quality_tension_ok and dossier_tension.get("score", 0) >= 7:
        verdict = dossier.setdefault("verdict", {})
        verdict["status"] = "推荐选题"
        verdict["label"] = "推荐"
        return dossier
    recommendation = data.get("recommendation", "hold")
    verdict = dossier.setdefault("verdict", {})
    if recommendation == "downgrade_to_brief":
        verdict["status"] = "可选选题"
        verdict["label"] = "可选"
    else:
        verdict["status"] = "可选选题"
        verdict["label"] = "可选"
    issues = data.get("fatal_issues") or data.get("missing_evidence") or data.get("warnings") or []
    if isinstance(issues, list) and issues:
        verdict["reason"] = f"质量闸提示缺口：{str(issues[0])[:120]}"
    else:
        verdict["reason"] = "质量闸没有给出推荐结论，保留为可选线索，写作前继续补证。"
    dossier["confidence"] = min(confidence_score(dossier.get("confidence", 0)), numeric_value(data.get("score"), 50))
    return dossier


def compose_topic_dossier(report: dict[str, Any], site: dict[str, Any], policy: dict[str, Any], plan: dict[str, Any], evidence: list[dict[str, str]], previous: dict[str, Any] | None = None) -> dict[str, Any] | None:
    prompt = (
        f"{load_prompt('03_investigation_report_flash.md')}\n\n"
        "请只输出 JSON：{\"schema\":\"topic-selection-dossier-v3\",\"generated_by\":\"deepseek\","
        "\"verdict\":{\"status\":\"推荐选题|可选选题\",\"label\":\"推荐|可选\",\"reason\":\"\"},"
        "\"core_question\":\"\",\"why_this_topic_matters\":\"\",\"fact_summary\":[],"
        "\"persona_discussion_question\":\"\",\"old_flower_stance\":\"\","
        "\"audience_fit\":{\"primary_layer\":\"\",\"secondary_layers\":[],\"interest_score\":0,\"why_interested\":\"\",\"reader_risk\":\"\"},"
        "\"mass_interest_hook\":{\"score\":0,\"hook_type\":\"故事|反差|争议|数字|踩坑|普通人关系\",\"why_non_technical_people_may_click\":\"\",\"story_seed\":\"\",\"do_not_overhype\":\"\"},"
        "\"topic_tension\":{\"score\":0,\"conflict_point\":\"\",\"debate_question\":\"\",\"stakeholders\":[],\"why_people_would_comment\":\"\",\"traffic_risk\":\"\"},"
        "\"timeline\":[],\"evidence_table\":[{\"source\":\"\",\"url\":\"\",\"supports\":\"\",\"reliability\":\"official|near_source|media|weak\"}],"
        "\"logic_closure\":\"\",\"writeable_angles\":[{\"angle\":\"\",\"why\":\"\",\"needs\":\"\"}],"
        "\"missing_basics\":[],\"missing_materials\":[],\"not_claimable\":[],\"followup_queries\":[],"
        "\"additional_search_queries\":[],\"stop_conditions\":[],\"confidence\":0}。\n"
        "只有证据较完整、老花人设解读角度清楚、目标读者分层明确、泛兴趣故事钩子不夸张、传播张力成立且逻辑可闭环时才写推荐；证据不足、缺少真实冲突点或缺少讨论空间时写可选，并把 additional_search_queries 和缺口写清楚。"
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
    return normalize_selection_dossier(report, data)


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
    dossier = apply_quality_gate(report, dossier, evidence)
    dossier = normalize_selection_dossier(report, dossier)
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
    if topic == "tools-rules":
        return "ai_practice" if report_type != "platform-rules" else "platform_rule_change"
    if topic in {"side-info", "cross-border"} or report_type in {"opportunity", "case-study"}:
        return "business_teardown"
    if topic == "side-info" and report_type == "risk-warning":
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


def audience_fit(report: dict[str, Any]) -> dict[str, Any]:
    topic = report.get("topic_direction", "")
    report_type = report.get("report_type", "")
    score = int(report.get("score", 0) or 0)
    layers = {
        "mass-interest": "泛兴趣普通人",
        "starter": "学生/新人/1-5年入门读者",
        "core-tech": "核心技术人",
        "commercial": "独立开发/出海/SEO/SaaS 高价值读者",
        "side-hustle": "想副业但技术能力有限的人",
        "mid-career": "30+/35岁焦虑的 IT 打工人",
    }
    primary = "core-tech"
    secondary = ["mass-interest"]
    if topic == "cross-border":
        primary = "commercial"
        secondary = ["core-tech", "side-hustle", "mass-interest"]
    elif topic == "side-info":
        primary = "side-hustle" if report_type in {"opportunity", "risk-warning"} else "commercial"
        secondary = ["core-tech", "mid-career", "mass-interest"]
    elif topic == "tools-rules":
        primary = "core-tech"
        secondary = ["commercial", "side-hustle", "mass-interest"]
    elif topic == "ai-frontier":
        primary = "core-tech"
        secondary = ["starter", "mid-career", "mass-interest"]
    interest_score = max(4, min(10, round(score / 12) + (2 if report.get("evidence_level") in {"official", "near_source"} else 0)))
    return {
        "primary_layer": layers[primary],
        "secondary_layers": [layers[key] for key in secondary],
        "interest_score": interest_score,
        "why_interested": report.get("reader_hook", ""),
        "reader_risk": "如果只复述新闻或产品功能，会失去普通读者；必须落到故事冲突、成本、门槛、规则、坑或机会。",
    }


def mass_interest_hook(report: dict[str, Any]) -> dict[str, Any]:
    title = display_report_title(report)
    report_type = report.get("report_type", "")
    tension = topic_tension(report)
    hook_type = "故事"
    if report_type == "risk-warning":
        hook_type = "踩坑"
    elif report_type in {"tool-ledger", "platform-rules"}:
        hook_type = "数字/规则反差"
    elif report_type in {"opportunity", "case-study"}:
        hook_type = "机会反差"
    elif tension.get("score", 0) >= 7:
        hook_type = "争议/反差"
    return {
        "score": max(4, min(10, tension.get("score", 0))),
        "hook_type": hook_type,
        "why_non_technical_people_may_click": tension.get("why_people_would_comment") or "它需要先找到普通人也看得懂的冲突：钱、规则、门槛、风险或机会变化，否则只适合作为专业沉淀。",
        "story_seed": f"{title} 背后真正值得看的，不只是技术本身，而是谁会受影响、谁可能踩坑、谁又可能误判。",
        "do_not_overhype": "标题和开头可以有故事感，但不能把线索写成确定机会、不能制造恐慌，必须用来源和证据接住。",
    }


def topic_tension(report: dict[str, Any]) -> dict[str, Any]:
    title = display_report_title(report)
    text = f"{title} {report.get('summary', '')} {report.get('reader_hook', '')}".lower()
    report_type = report.get("report_type", "")
    minor_changelog = is_minor_changelog_text(text)
    signal_groups = {
        "money": ["price", "pricing", "cost", "bill", "revenue", "mrr", "token", "adsense", "价格", "成本", "账单", "收入", "涨价", "免费", "付费", "流量主"],
        "rules": ["policy", "rule", "compliance", "ban", "search", "seo", "license", "terms", "规则", "合规", "封号", "搜索", "流量", "协议"],
        "risk": ["risk", "scam", "lawsuit", "warning", "failed", "风险", "骗局", "踩坑", "翻车", "失败", "避坑"],
        "people": ["developer", "founder", "user", "creator", "程序员", "开发者", "小团队", "独立开发", "普通人", "新人"],
        "contrast": ["but", "however", "vs", "against", "反而", "但是", "争议", "反差", "冲突", "没想到", "看起来"],
        "heat": ["backlash", "controversy", "outrage", "criticism", "lawsuit", "ban", "blocked", "remove", "cap usage", "limit usage", "反弹", "争议", "吐槽", "不满", "封禁", "下架", "限制", "起诉"],
    }
    matched = {name: [word for word in words if term_in_text(text, word)] for name, words in signal_groups.items()}
    matched = {name: words[:4] for name, words in matched.items() if words}
    base = 3 + min(len(matched), 4)
    if report_type in {"tool-ledger", "platform-rules", "risk-warning", "opportunity", "case-study"}:
        base += 1
    if report_type == "hot-event" or "heat" in matched:
        base += 2
    if report.get("evidence_level") in {"official", "near_source"}:
        base += 1
    if {"money", "rules"} & set(matched) and {"risk", "people", "contrast"} & set(matched):
        base += 1
    score = max(3, min(10, base))
    if minor_changelog and "heat" not in matched:
        score = min(score, 5)
    if "heat" in matched:
        conflict = "热点事件里的平台叙事、用户反弹和普通技术人的真实成本之间有冲突。"
        debate = "这件事到底是合理规则/正常成本控制，还是平台把风险和成本转嫁给普通使用者？"
    elif "rules" in matched:
        conflict = "平台/规则变化和普通开发者、小团队的成本或操作预期之间的冲突。"
        debate = "规则变化到底是在保护生态，还是把小团队的试错成本继续抬高？"
    elif "money" in matched:
        conflict = "表面价格、额度或收益预期和真实成本之间的冲突。"
        debate = "这笔账普通技术人还能不能算得过来，还是只适合少数人？"
    elif "risk" in matched:
        conflict = "看起来有机会的路径和实际风险、合规边界之间的冲突。"
        debate = "这是值得试的小机会，还是又一个容易被包装的话术？"
    elif "people" in matched and "contrast" in matched:
        conflict = "不同人群对同一件事的预期和实际结果之间有反差。"
        debate = "这类变化到底利好谁，又会让谁误判？"
    else:
        conflict = ""
        debate = ""
        score = min(score, 5)
    return {
        "score": score,
        "conflict_point": conflict,
        "debate_question": debate,
        "stakeholders": ["普通技术人", "小团队/独立开发者", "平台/工具厂商"],
        "why_people_would_comment": "读者可以围绕成本、规则、风险、机会真假或自己是否遇到类似情况发表观点。" if score >= 7 else "",
        "traffic_risk": "传播张力不足，容易写成专业说明或小圈子自嗨；需要补真实冲突、反方材料或读者案例。" if score < 7 else "有讨论空间，但标题不能夸大，必须用证据接住冲突。",
    }


def material_pack(report: dict[str, Any]) -> dict[str, Any]:
    template = report_template_key(report)
    original_title = report.get("original_title", "")
    published = bj_time(report.get("published_at"), "原文未提供发布时间")
    fetched = bj_time(report.get("fetched_at"), "")
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
    audience = audience_fit(report)
    mass_hook = mass_interest_hook(report)
    return {
        "template": template,
        "topic_direction": report.get("topic_direction", ""),
        "topic_direction_title": report.get("topic_direction_title", ""),
        "report_type": report.get("report_type", ""),
        "audience_fit": audience,
        "mass_interest_hook": mass_hook,
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
                mass_hook["story_seed"],
                f"{title}，和普通技术人有什么关系",
                f"{title}：机会、成本和风险先拆清楚",
            ],
            "reader_questions": [
                f"泛兴趣读者为什么会点：{mass_hook['why_non_technical_people_may_click']}",
                f"主要服务哪层读者：{audience['primary_layer']}，兴趣 {audience['interest_score']}/10",
                "这事和我有什么关系？",
                "它到底改变了什么，还是只是包装换了个名字？",
                "如果我只是懂一点技术，能不能低成本验证？",
                "最大的坑是技术，还是流量、合规、成本、回款和交付？",
            ],
            "opening_hooks": [
                mass_hook["story_seed"],
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
                "能不能形成老花的人设解读角度，目标读者会不会关心其中的工具链、成本或能力变化？",
                "有没有官方公告、文档、价格页、发布说明或可信近源材料？",
                "能不能讲清楚发布时间线、能力变化、影响对象、成本门槛和替代关系？",
            ],
            "valuable_signals": ["官方发布", "影响开发者或高频 AI 用户", "改变成本结构", "改变工作流", "有明确对比对象"],
            "reject_signals": ["只有产品名", "只是 SDK 小版本", "没有人设解读角度", "只能写成又一个工具更新"],
            "writeable_angles": ["这次更新到底改变了什么", "普通技术人该不该关注", "成本和工具链会不会变化", "它替代了谁又替代不了谁"],
            "missing_basics": ["产品/模型/Agent 基础概念", "本次更新前后的差异", "开放范围和使用门槛", "价格、额度和地区限制"],
            "missing_materials": ["官方公告", "文档或 changelog", "价格页", "真实使用反馈", "竞品对比材料"],
        },
        "tools-rules": {
            "selection_questions": [
                "它是不是一个具体工具、工作流、平台规则或搜索流量变化，而不是空泛地说 AI 很强？",
                "能不能从老花的人设视角说清它怎么用、怎么变、会影响什么成本或边界？",
                "能不能拆出输入输出、执行规则、人工兜底、失败信号或账号/合规风险？",
                "有没有官方文档、帮助中心、价格页、规则原文、实测材料或反例？",
            ],
            "valuable_signals": ["工具或规则明确", "输入输出清楚", "影响成本/效率/流量/账号", "有官方或近源材料", "目标读者会关心"],
            "reject_signals": ["只有工具名", "只有炫技", "只有玄学猜测", "没有规则原文", "必须编一堆操作细节"],
            "writeable_angles": ["这个工具或规则到底改变了什么", "最低成本怎么验证", "哪些环节必须人工兜底", "哪些行为可能失效或踩坑"],
            "missing_basics": ["具体使用者是谁", "输入输出或规则原文", "影响对象", "执行时间", "失败后怎么回滚"],
            "missing_materials": ["工具文档", "示例工作流", "价格页", "帮助中心/规则原文", "社区实测和反例"],
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
        "side-info": {
            "selection_questions": [
                "它是不是一个真实项目、副业机会、工具站、SaaS、开源项目、信息差线索或风险案例？",
                "需求、用户、流量、变现、交付、现金流和风险能不能拆清楚？",
                "技术是不是只是其中一环，非技术门槛、失败信号和停止条件能不能说出来？",
                "有没有数据、代码、用户反馈、收入证据、合同/条款、投诉或失败证据？",
            ],
            "valuable_signals": ["真实项目或案例", "有用户/数据/收入/条款证据", "能拆需求和变现", "技术人有切口", "失败信号清楚"],
            "reject_signals": ["只有 idea", "只有 GitHub 星数", "没有用户证据", "只有情绪或截图", "只能强行写我会怎么做"],
            "writeable_angles": ["技术之外真正难的是什么", "这个项目为什么有人用", "钱从哪里来又从哪里没掉", "最小验证应该验证哪一件事", "什么信号出现就该停"],
            "missing_basics": ["目标用户", "核心需求", "流量来源", "收费方式", "合同/回款/责任边界"],
            "missing_materials": ["项目仓库", "产品页面", "用户反馈", "收入或订阅证据", "条款原文", "竞品和失败案例"],
        },
    }
    return rules.get(topic_key, {
        "selection_questions": [
            "这件事到底是什么？",
            "它和老花的人设、读者需求、技术人视角有什么关系？",
            "事实是否清楚，证据是否可靠，逻辑能不能闭环？",
        ],
        "valuable_signals": ["事实清楚", "读者能理解", "有证据", "能形成判断"],
        "reject_signals": ["只能泛泛而谈", "没有证据", "没有人设解读角度"],
        "writeable_angles": ["事实拆解", "价值判断", "风险边界"],
        "missing_basics": ["核心概念", "影响对象", "证据边界"],
        "missing_materials": ["一手来源", "近源证据", "反方材料"],
    })


def selection_verdict(report: dict[str, Any]) -> dict[str, str]:
    score = int(report.get("score", 0))
    evidence = report.get("evidence_level", "")
    decision = report.get("decision", "")
    gaps = len(report.get("uncertainty_flags", []))
    tension = topic_tension(report)
    if decision == "deep_dive" and evidence in {"official", "near_source"} and score >= 68 and gaps <= 3 and tension["score"] >= 7:
        return {
            "status": "推荐选题",
            "label": "推荐",
            "reason": "当前线索有明确来源、人设解读角度、目标读者兴趣、传播张力和分析空间，建议优先进入写作框架。",
        }
    if decision != "skip" and score >= MIN_BRIEF_SCORE:
        return {
            "status": "可选选题",
            "label": "可选",
            "reason": "线索有方向价值，但事实、概念、案例、成本材料或传播张力还需要继续补证。",
        }
    return {
        "status": "可选选题",
        "label": "可选",
        "reason": "当前材料偏弱，只作为候选线索保留；写作前必须补足来源、人设解读角度和目标读者兴趣理由。",
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
    audience = audience_fit(report)
    mass_hook = mass_interest_hook(report)
    tension = topic_tension(report)
    discussion_question = persona_discussion_question(report, tension)
    fact_status = "基本清楚" if original_title and report.get("url") else "事实入口不足"
    summary_status = "有摘要" if summary else "缺少原文摘要"
    closure = [
        {
            "node": "事件是否存在",
            "status": "已确认" if report.get("url") else "缺证据",
            "note": f"来源入口：{report.get('source_name', '')}",
        },
        {
            "node": "人设解读角度是否成立",
            "status": "初步成立" if report.get("reader_hook") else "缺判断",
            "note": report.get("reader_hook", ""),
        },
        {
            "node": "传播张力是否成立",
            "status": "初步成立" if tension["score"] >= 7 else "不足，需要补冲突点",
            "note": tension["conflict_point"] or tension["traffic_risk"],
        },
        {
            "node": "事实是否清楚",
            "status": fact_status,
            "note": f"原始标题：{original_title}",
        },
        {
            "node": "材料是否足够写正文",
            "status": "建议优先扩写" if verdict["label"] == "推荐" else "可选，需要补证",
            "note": "Radar 只负责给出选题档案，不在证据不足时强行生成公众号正文。",
        },
    ]
    return {
        "schema": "topic-selection-dossier-v2",
        "verdict": verdict,
        "topic_direction": topic_key,
        "topic_direction_title": report.get("topic_direction_title", ""),
        "report_type": report.get("report_type", ""),
        "audience_fit": audience,
        "mass_interest_hook": mass_hook,
        "topic_tension": tension,
        "persona_discussion_question": discussion_question,
        "core_question": f"这条线索能不能成为一个值得老花继续写的选题：{title}",
        "human_judgment_path": [
            "先确认这件事是不是真的发生，而不是只看标题兴奋。",
            "再确认泛兴趣普通人能不能从故事、反差、数字、踩坑或普通人关系进入。",
            "再确认是否有真实传播张力：冲突点、讨论点、利益拉扯、身份代入或读者愿意评论的问题。",
            "再确认老花能不能表达一个有辨识度的判断，而不是只复述新闻或官方公告。",
            "再确认它是否符合老花人设：能不能用技术人视角解释机会、成本、规则、坑或工具变化。",
            "然后确认主要服务哪层读者，不能把所有人都当成同一个读者。",
            "然后看证据是否够：有没有官方/近源材料，是否需要二次补证。",
            "再看逻辑是否闭环：事实、原因、影响、边界、可写角度能不能连起来。",
            "最后决定：推荐或可选，而不是把所有线索都写成同一套报告。",
        ],
        "selection_questions": rules["selection_questions"],
        "value_signals": rules["valuable_signals"],
        "reject_signals": rules["reject_signals"],
        "topic_value_assessment": [
            {"question": "这是什么？", "judgment": original_title or title},
            {"question": "普通人为什么可能点进来？", "judgment": mass_hook["why_non_technical_people_may_click"]},
            {"question": "这篇有什么冲突点或讨论点？", "judgment": tension["conflict_point"] or tension["traffic_risk"]},
            {"question": "老花的人设讨论问题是什么？", "judgment": discussion_question},
            {"question": "读者为什么愿意评论？", "judgment": tension["why_people_would_comment"] or "当前还缺少可评论的公共问题。"},
            {"question": "老花可以从什么角度解读？", "judgment": report.get("reader_hook", "暂无明确人设解读角度")},
            {"question": "主要服务哪层读者？", "judgment": f"{audience['primary_layer']}；兴趣 {audience['interest_score']}/10"},
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
            "无法说明老花能从什么角度解读，以及目标读者为什么会关心。",
            "找不到真实冲突点、讨论点或利益拉扯，只能写成专业介绍。",
            "关键概念、成本、合规、收益或影响对象说不清。",
            "逻辑必须靠想象补齐，写出来只会像假大空。",
        ],
    }


def material_pack(report: dict[str, Any]) -> dict[str, Any]:
    return selection_dossier(report)


def build_report(decision: RadarDecision, site: dict[str, Any], policy: dict[str, Any], batch_id: str) -> dict[str, Any]:
    item = decision.item
    report_id = f"{now_bj().strftime('%Y%m%d')}-{slugify(item.title)}"
    report_type_meta = site["report_types"][decision.report_type]
    source_title = site.get("source_categories", {}).get(item.source_category, item.source_category)
    source_name = display_source_name(item)
    topic_key, topic_meta = topic_direction_for_item(item, decision.report_type, site)
    report = {
        "id": report_id,
        "batch_id": batch_id,
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
        "topic_tension": topic_tension({
            "title": decision.report_title,
            "summary": item.summary,
            "reader_hook": decision.reader_hook,
            "report_type": decision.report_type,
            "evidence_level": decision.evidence_level,
        }),
        "persona_discussion_question": persona_discussion_question({
            "title": decision.report_title,
            "summary": item.summary,
            "reader_hook": decision.reader_hook,
            "report_type": decision.report_type,
            "evidence_level": decision.evidence_level,
        }),
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
    report["selection_dossier"] = normalize_selection_dossier(report, enrich_selection_dossier(report, site, policy))
    report["material_pack"] = report["selection_dossier"]
    report["downstream_handoff"] = downstream_handoff(report, site)
    return report


def select_reports(decisions: list[RadarDecision], site: dict[str, Any], archive: dict[str, Any], batch_id: str, limit: int = MAX_REPORTS_PER_BATCH) -> tuple[list[RadarDecision], list[dict[str, str]]]:
    eligible = [d for d in decisions if d.decision == "deep_dive" and d.score >= MIN_DEEP_DIVE_SCORE]
    eligible.extend(d for d in decisions if d.decision == "brief" and d.score >= MIN_BRIEF_SCORE)
    if not eligible:
        eligible = [d for d in decisions if d.decision != "skip" and d.score >= MIN_BRIEF_SCORE]
    eligible_ids = {d.item.id for d in eligible}
    for decision in decisions:
        if decision.item.id in eligible_ids:
            continue
        topic_key, _ = topic_direction_for_item(decision.item, decision.report_type, site)
        reason = decision.reject_reason or "未达到入池分数线或已被初筛判定为 skip"
        info(
            "Selection filter: "
            f"stage=eligibility, score={decision.score}, decision={decision.decision}, topic={topic_key}, "
            f"source={decision.item.source_name}/{decision.item.source_type}, title={decision.report_title}, "
            f"original={decision.item.title}, reason={reason}"
        )
    selected: list[RadarDecision] = []
    category_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    source_host_counts: dict[str, int] = {}
    seen_fingerprints: set[str] = set()
    selected_urls: set[str] = set()
    duplicate_skips: list[dict[str, str]] = []
    topic_order = list(site.get("topic_directions", {}).keys())
    eligible_by_topic: dict[str, list[RadarDecision]] = {key: [] for key in topic_order}
    for decision in eligible:
        topic_key, _ = topic_direction_for_item(decision.item, decision.report_type, site)
        eligible_by_topic.setdefault(topic_key, []).append(decision)

    def try_select(decision: RadarDecision) -> bool:
        category = decision.item.source_category
        topic_key, _ = topic_direction_for_item(decision.item, decision.report_type, site)
        source_host = urllib.parse.urlparse(decision.item.url).netloc.lower()
        fingerprints = decision_fingerprints(decision)
        url = normalize_url(decision.item.url)
        if url in selected_urls:
            info(
                "Selection filter: "
                f"stage=batch-url, score={decision.score}, decision={decision.decision}, topic={topic_key}, "
                f"source={decision.item.source_name}/{decision.item.source_type}, title={decision.report_title}, reason=本批次同 URL 已入池"
            )
            return False
        if any_similar_fingerprint(seen_fingerprints, fingerprints):
            duplicate_skips.append({"title": decision.report_title or decision.item.title, "url": decision.item.url, "reason": "本批次相似选题已入池"})
            info(
                "Selection filter: "
                f"stage=batch-similar, score={decision.score}, decision={decision.decision}, topic={topic_key}, "
                f"source={decision.item.source_name}/{decision.item.source_type}, title={decision.report_title}, reason=本批次相似选题已入池"
            )
            return False
        duplicate, reason = is_duplicate_topic(decision, site, archive, batch_id)
        if duplicate:
            duplicate_skips.append({"title": decision.report_title or decision.item.title, "url": decision.item.url, "reason": reason})
            info(
                "Selection filter: "
                f"stage=archive-duplicate, score={decision.score}, decision={decision.decision}, topic={topic_key}, "
                f"source={decision.item.source_name}/{decision.item.source_type}, title={decision.report_title}, reason={reason}"
            )
            return False
        cap = SOURCE_CATEGORY_REPORT_CAPS.get(category, limit)
        if category_counts.get(category, 0) >= cap:
            info(
                "Selection filter: "
                f"stage=source-category-cap, score={decision.score}, decision={decision.decision}, topic={topic_key}, "
                f"source={decision.item.source_name}/{decision.item.source_type}, title={decision.report_title}, "
                f"reason=source_category {category} reached {cap}"
            )
            return False
        if topic_counts.get(topic_key, 0) >= MAX_REPORTS_PER_TOPIC:
            info(
                "Selection filter: "
                f"stage=topic-cap, score={decision.score}, decision={decision.decision}, topic={topic_key}, "
                f"source={decision.item.source_name}/{decision.item.source_type}, title={decision.report_title}, "
                f"reason=topic {topic_key} reached {MAX_REPORTS_PER_TOPIC}"
            )
            return False
        if source_host_counts.get(source_host, 0) >= MAX_REPORTS_PER_SOURCE_HOST:
            info(
                "Selection filter: "
                f"stage=source-host-cap, score={decision.score}, decision={decision.decision}, topic={topic_key}, "
                f"source={decision.item.source_name}/{decision.item.source_type}, title={decision.report_title}, "
                f"reason=source host {source_host} reached {MAX_REPORTS_PER_SOURCE_HOST}"
            )
            return False
        selected.append(decision)
        category_counts[category] = category_counts.get(category, 0) + 1
        topic_counts[topic_key] = topic_counts.get(topic_key, 0) + 1
        source_host_counts[source_host] = source_host_counts.get(source_host, 0) + 1
        seen_fingerprints.update(fingerprints)
        selected_urls.add(url)
        info(
            "Selection accept: "
            f"score={decision.score}, decision={decision.decision}, topic={topic_key}, "
            f"source={decision.item.source_name}/{decision.item.source_type}, title={decision.report_title}, original={decision.item.title}"
        )
        return True

    for topic_key in topic_order:
        for decision in eligible_by_topic.get(topic_key, []):
            if try_select(decision):
                break
        if len(selected) >= limit:
            return selected, duplicate_skips

    for decision in eligible:
        if try_select(decision) and len(selected) >= limit:
            return selected, duplicate_skips

    return selected, duplicate_skips


def is_tophubdata_item(item: SourceItem) -> bool:
    return item.source_type.startswith("api-paid") or item.source_name.startswith("TopHubData")


def decision_summary(decisions: list[RadarDecision]) -> dict[str, Any]:
    return {
        "total": len(decisions),
        "deep_dive": sum(1 for d in decisions if d.decision == "deep_dive"),
        "brief": sum(1 for d in decisions if d.decision == "brief"),
        "skip": sum(1 for d in decisions if d.decision == "skip"),
        "llm": sum(1 for d in decisions if not d.traceability.get("heuristic")),
        "heuristic": sum(1 for d in decisions if d.traceability.get("heuristic")),
    }


def decision_log_sample(decisions: list[RadarDecision], limit: int = 8) -> list[dict[str, Any]]:
    sample: list[dict[str, Any]] = []
    for decision in sorted(decisions, key=lambda d: d.score, reverse=True)[:limit]:
        sample.append({
            "score": decision.score,
            "decision": decision.decision,
            "report_type": decision.report_type,
            "title": decision.report_title,
            "original_title": decision.item.title,
            "source": decision.item.source_name,
            "url": decision.item.url,
        })
    return sample


def log_triage_decisions(decisions: list[RadarDecision], site: dict[str, Any]) -> None:
    for index, decision in enumerate(decisions, 1):
        topic_key, _ = topic_direction_for_item(decision.item, decision.report_type, site)
        source_mode = "heuristic" if decision.traceability.get("heuristic") else "llm"
        tension = decision.traceability.get("topic_tension_score", "")
        reason = decision.reject_reason or decision.reason
        info(
            "Triage decision: "
            f"rank={index}, score={decision.score}, decision={decision.decision}, report_type={decision.report_type}, "
            f"topic={topic_key}, mode={source_mode}, tension={tension}, "
            f"source={decision.item.source_name}/{decision.item.source_type}, "
            f"title={decision.report_title}, original={decision.item.title}, reason={reason}"
        )


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
.topic-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.topic-head h2{margin:0}.kicker{font-size:13px;color:#44516b;font-weight:650;margin:0 0 6px}.topic-list{margin-top:14px}.filter-tabs{position:sticky;top:54px;z-index:4;display:flex;gap:8px;flex-wrap:wrap;background:rgba(246,247,251,.94);padding:10px 0;border-bottom:1px solid var(--line)}.filter-tabs a{border:1px solid var(--line);border-radius:999px;background:#fff;color:#2c3852;padding:6px 12px;font-size:14px}.callout{border-color:#bfd1f8;background:#f8fbff}.evidence-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px}.evidence-grid h3{margin-top:0}
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
    prefixes = [report.get("report_type_title", ""), "深度调查", "机会拆解", "工具账本", "平台规则", "案例复盘", "风险避坑", "热点观点"]
    for prefix in prefixes:
        if prefix and title.startswith(prefix + "："):
            return title[len(prefix) + 1:].strip()
    return title


def report_card(report: dict[str, Any]) -> str:
    topic_title = report.get("topic_direction_short_title") or report.get("topic_direction_title") or report.get("source_category_title", "")
    title = display_report_title(report)
    dossier = report.get("selection_dossier") or report.get("material_pack") or {}
    verdict = dossier.get("verdict", {})
    verdict_label = report_verdict_label(report)
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
    label = report_verdict_label(report)
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


def report_verdict_label(report: dict[str, Any]) -> str:
    dossier = report.get("selection_dossier") or report.get("material_pack") or {}
    return topic_level(report, dossier if isinstance(dossier, dict) else {})


def archive_item_level(item: dict[str, Any]) -> str:
    verdict = str(item.get("verdict", "")).strip()
    if verdict in {"推荐", "可选"}:
        return verdict
    score = int(item.get("score", 0) or 0)
    evidence = item.get("evidence_level", "")
    if evidence in {"official", "near_source"} and score >= 68:
        return "推荐"
    return "可选"


def pack_verdict_display(pack: dict[str, Any]) -> tuple[str, str]:
    verdict = pack.get("verdict", {}) if isinstance(pack, dict) else {}
    label = str(verdict.get("label") or verdict.get("status") or "").strip()
    if label == "推荐":
        status = "推荐选题"
    else:
        status = "可选选题"
    return status, str(verdict.get("reason", ""))


def render_archive_preview(archive: dict[str, Any], limit_days: int = 5) -> str:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in archive.get("items", []):
        day = bj_day(item.get("last_seen_at") or item.get("first_seen_at"))
        grouped.setdefault(day, []).append(item)
    sections = []
    for day in sorted(grouped.keys(), reverse=True)[:limit_days]:
        rows = []
        for item in grouped[day][:8]:
            rows.append(f'<li><span class="badge">{html.escape(archive_item_level(item))}</span><a href="{html.escape(item.get("item_url", ""))}">{html.escape(item.get("title", ""))}</a> <span class="meta">· {html.escape(item.get("topic_direction_title", ""))}</span></li>')
        sections.append(f'<section class="archive-day"><h3>{html.escape(day)}</h3><ul>{"".join(rows)}</ul></section>')
    return "".join(sections) or '<p class="meta">暂无历史选题归档。</p>'


def render_home(batch: dict[str, Any], reports: list[dict[str, Any]], site: dict[str, Any], policy: dict[str, Any], archive: dict[str, Any]) -> str:
    principles = "".join(f"<li>{html.escape(x)}</li>" for x in policy["source_principles"])
    coverage = "".join(
        f'<span class="badge">{html.escape(v["title"])}：{v["items"]} 条 / 失败 {v["failures"]}</span>'
        for v in batch.get("source_coverage", {}).values()
    )
    recommended_reports = [r for r in reports if report_verdict_label(r) == "推荐"]
    optional_reports = [r for r in reports if report_verdict_label(r) == "可选"]
    recommended_flow = "".join(report_flow_item(r) for r in recommended_reports) or '<p class="meta">本批次暂无推荐选题。</p>'
    optional_flow = "".join(report_flow_item(r) for r in optional_reports) or '<p class="meta">本批次暂无可选选题。</p>'
    verdict_counts: dict[str, int] = {}
    for report in reports:
        label = report_verdict_label(report)
        verdict_counts[label] = verdict_counts.get(label, 0) + 1
    verdict_summary = " / ".join(f"{html.escape(k)} {v}" for k, v in verdict_counts.items()) or "暂无候选"
    duplicate_count = len(batch.get("duplicate_skips", []))
    return f"""
<section class="hero"><h1>老花的选题雷达站</h1><p>这里不是公众号成稿库，而是上游情报台。每条线索先按选题方向沉淀，再保留来龙去脉、事实、证据、存疑点和与老花人设相关的切入口，给后续博客、公众号、视频和小红书做素材底座。</p></section>
<section class="section"><h2>推荐选题</h2><div class="flow">{recommended_flow}</div></section>
<section class="section"><h2>可选选题</h2><p class="meta">可选不等于可直接成稿，进入写作前仍要按详情页里的缺口继续补证。</p><div class="flow">{optional_flow}</div></section>
<section class="section">
  <h2>大选题方向聚合</h2>
  <div class="topic-strip">{topic_summary_chips(reports, site)}</div>
</section>
<div class="ad-slot">AdSense 预留位：后续填入 publisher client 后启用</div>
<section class="section card"><h2>本批次概况</h2><p class="meta">批次：{html.escape(batch["batch_id"])} · 抓取 {batch["fetched_count"]} 条 · 入池 {len(reports)} 个候选 · {verdict_summary} · 近期重复跳过 {duplicate_count} 个</p><p>{coverage}</p></section>
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
    sorted_reports = sorted(reports, key=report_time_key, reverse=True)
    recommended = [report for report in sorted_reports if report_verdict_label(report) == "推荐"]
    optional = [report for report in sorted_reports if report_verdict_label(report) == "可选"]
    optional_items = "".join(report_card(r) for r in optional) or '<p class="meta">本方向暂无可选选题。</p>'
    recommended_items = "".join(report_card(r) for r in recommended) or '<p class="meta">本方向暂无推荐选题。</p>'
    all_items = "".join(report_card(r) for r in sorted_reports) or '<p class="meta">本方向暂无历史选题。</p>'
    return f"""
<section class="hero"><p class="kicker">选题方向</p><h1>{html.escape(meta["title"])}</h1><p>{html.escape(meta.get("description", ""))}</p></section>
<section class="callout"><h2>这个方向怎么读</h2><p>先看线索和原始来源，再进入详情页看事实收集、证据收集、存疑点和与老花相关的切入口。报告类型只是分析方法，不代表主栏目。</p></section>
<nav class="filter-tabs" aria-label="选题筛选"><a href="#optional">可选</a><a href="#recommended">推荐</a><a href="#all">全部</a></nav>
<section id="optional" class="section"><h2>可选</h2><div class="list">{optional_items}</div></section>
<section id="recommended" class="section"><h2>推荐</h2><div class="list">{recommended_items}</div></section>
<section id="all" class="section"><h2>全部历史</h2><p class="meta">按时间由近及远排序。</p><div class="list">{all_items}</div></section>
"""


def render_archive(archive: dict[str, Any], site: dict[str, Any]) -> str:
    directions = site.get("topic_directions", {})
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in archive.get("items", [])[:180]:
        day = bj_day(item.get("last_seen_at") or item.get("first_seen_at"))
        grouped.setdefault(day, []).append(item)
    sections = []
    for day in sorted(grouped.keys(), reverse=True):
        rows = []
        for item in grouped[day]:
            topic_key = item.get("topic_direction", "")
            topic_title = item.get("topic_direction_title") or directions.get(topic_key, {}).get("title", topic_key)
            rows.append(f"""
<article class="item">
  <p class="kicker">{html.escape(topic_title)} · {html.escape(archive_item_level(item))}</p>
  <h3><a href="{html.escape(item.get("item_url", ""))}">{html.escape(item.get("title", ""))}</a></h3>
  <p class="meta">Score {html.escape(str(item.get("score", "")))} · {html.escape(item.get("evidence_level", ""))} · 首次入池 {html.escape(bj_time(item.get("first_seen_at")))}</p>
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
    verdict_status, verdict_reason = pack_verdict_display(pack)
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
  <p><strong>{html.escape(verdict_status)}</strong>：{html.escape(verdict_reason)}</p>
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
    verdict_status, verdict_reason = pack_verdict_display(pack)
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
  <p><strong>{html.escape(verdict_status)}</strong>：{html.escape(verdict_reason)}</p>
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
    verdict_status = "推荐选题" if report_verdict_label(report) == "推荐" else "可选选题"
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
        "datePublished": report.get("published_at") or bj_iso(report.get("fetched_at")),
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
    <p><strong>{html.escape(verdict_status)}</strong>：{html.escape(verdict.get("reason", ""))}</p>
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


def render_static(batch: dict[str, Any], reports: list[dict[str, Any]], all_reports: list[dict[str, Any]], site: dict[str, Any], policy: dict[str, Any], archive: dict[str, Any]) -> None:
    static = StaticSite(site)
    static.write_assets()
    static.write_page("index.html", "Easton Radar", render_home(batch, reports, site, policy, archive))
    static.write_page("archive/index.html", "历史选题归档 - Easton Radar", render_archive(archive, site))
    static.write_page("about/index.html", "关于 - Easton Radar", render_about(site, policy))
    for key, meta in site.get("topic_directions", {}).items():
        static.write_page(f"topics/{key}/index.html", f"{meta['title']} - Easton Radar", render_topic_direction(key, meta, reports_for_topic(all_reports, key)))
    for report in all_reports:
        static.write_page(f"items/{report['id']}/index.html", f"{display_report_title(report)} - Easton Radar", render_item(report, site))
    static.write_text("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {site['site_url'].rstrip('/')}/sitemap.xml\n")
    static.write_text("sitemap.xml", sitemap(site, all_reports))
    static.write_text("llms.txt", llms(site, all_reports))
    static.write_text("ads.txt", "google.com, pub-0000000000000000, DIRECT, f08c47fec0942fa0\n")


def sitemap(site: dict[str, Any], reports: list[dict[str, Any]]) -> str:
    base = site["site_url"].rstrip("/")
    paths = ["", "archive/", "about/"] + [f"topics/{k}/" for k in site.get("topic_directions", {})] + [f"items/{r['id']}/" for r in reports]
    today = now_bj().date().isoformat()
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
        label = report_verdict_label(report)
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
        label = report_verdict_label(report)
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
        f"tavily_search={'yes' if os.environ.get('TAVILY_API_KEY') else 'no'}, "
        f"tavily_depth={os.environ.get('TAVILY_SEARCH_DEPTH', 'basic') or 'basic'}, "
        f"brave_search={'yes' if os.environ.get('BRAVE_SEARCH_API_KEY') else 'no'}, "
        f"search_call_limit={search_api_call_limit_per_run()}"
    )
    log_search_budget_preflight()
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
    tophubdata_decisions = [d for d in decisions if is_tophubdata_item(d.item)]
    tophubdata_summary = decision_summary(tophubdata_decisions)
    tophubdata_sample = decision_log_sample(tophubdata_decisions)
    info(
        "Triage complete: "
        f"decisions={len(decisions)}, llm={llm_count}, heuristic={heuristic_count}, "
        f"deep={sum(1 for d in decisions if d.decision == 'deep_dive')}, "
        f"brief={sum(1 for d in decisions if d.decision == 'brief')}, "
        f"skip={sum(1 for d in decisions if d.decision == 'skip')}"
    )
    log_triage_decisions(decisions, site)
    info(
        "TopHubData triage: "
        f"items={tophubdata_summary['total']}, llm={tophubdata_summary['llm']}, heuristic={tophubdata_summary['heuristic']}, "
        f"deep={tophubdata_summary['deep_dive']}, brief={tophubdata_summary['brief']}, skip={tophubdata_summary['skip']}"
    )
    for sample in tophubdata_sample[:5]:
        info(
            "TopHubData candidate: "
            f"score={sample['score']}, decision={sample['decision']}, source={sample['source']}, "
            f"title={sample['title']}, original={sample['original_title']}"
        )
    selected, duplicate_skips = select_reports(decisions, site, archive, batch_id)
    info(f"Selection complete: selected={len(selected)}, duplicate_skips={len(duplicate_skips)}")
    selected_tophubdata = [d for d in selected if is_tophubdata_item(d.item)]
    info(f"TopHubData selection: selected={len(selected_tophubdata)}")
    for index, decision in enumerate(selected, 1):
        topic_key, _ = topic_direction_for_item(decision.item, decision.report_type, site)
        info(f"Selected #{index}: score={decision.score}, decision={decision.decision}, topic={topic_key}, title={decision.report_title}")
    for skipped in duplicate_skips[:10]:
        info(f"Duplicate skip: {skipped.get('title', '')} | {skipped.get('reason', '')}")
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
        "tophubdata_decision_summary": tophubdata_summary,
        "tophubdata_decision_sample": tophubdata_sample,
        "tophubdata_selected_count": len(selected_tophubdata),
        "source_coverage": source_coverage(items, failures, site),
        "failures": failures,
    }
    info("Building enriched topic reports...")
    reports = [build_report(d, site, policy, batch_id) for d in selected]
    info(f"Report build complete: reports={len(reports)}")
    archive = update_topic_archive(archive, reports, batch)
    info(f"Updated topic archive entries: {len(archive.get('items', []))}")
    clean_generated_outputs()
    info("Cleaned generated output directories.")
    write_json(DATA_DIR / f"{batch_id}.json", {"batch": batch, "items": [item.__dict__ for item in items], "reports": reports})
    write_json(DATA_DIR / "latest.json", {"batch": batch, "reports": reports})
    write_json(TOPIC_ARCHIVE_PATH, archive)
    save_search_usage_state()
    for report in reports:
        write_json(REPORTS_DIR / f"{report['id']}.json", report)
    all_reports = merge_report_archive(reports)
    info(f"Wrote JSON outputs and search usage state. current_reports={len(reports)}, archive_reports={len(all_reports)}")
    render_static(batch, reports, all_reports, site, policy, archive)
    info("Rendered static site.")
    if not args.no_telegram:
        send_telegram(batch, reports, site)
    info("Pipeline done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
