import importlib.util
import sys
import unittest
from pathlib import Path


RADAR_PATH = Path(__file__).resolve().parents[1] / "src" / "radar.py"
SPEC = importlib.util.spec_from_file_location("radar", RADAR_PATH)
radar = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = radar
SPEC.loader.exec_module(radar)


SITE = {
    "site_url": "https://radar.example.com",
    "report_types": {
        "investigation": {"title": "深度调查"},
        "opportunity": {"title": "机会拆解"},
        "tool-ledger": {"title": "工具账本"},
        "platform-rules": {"title": "平台规则"},
        "case-study": {"title": "案例复盘"},
        "risk-warning": {"title": "风险避坑"},
        "hot-event": {"title": "热点观点"},
    },
    "source_categories": {
        "ai_tools": "AI 工具与开发者平台",
        "hot_events": "热点事件与争议",
    },
    "topic_directions": {
        "ai-frontier": {"title": "AI前沿", "short_title": "AI前沿", "keywords": ["ai", "模型", "anthropic", "google"]},
        "tools-rules": {"title": "工具&规则", "short_title": "工具&规则", "keywords": ["规则", "合规", "搜索"]},
        "side-info": {"title": "副业&信息差", "short_title": "副业&信息差", "keywords": ["副业", "独立开发"]},
    }
}


def decision(title: str, original: str, url: str, report_type: str = "hot-event"):
    item = radar.SourceItem(
        id=str(abs(hash(url or title))),
        source_category="hot_events",
        source_name="AI News & Artificial Intelligence | TechCrunch",
        source_type="rss",
        title=original,
        url=url,
        summary="",
    )
    return radar.RadarDecision(
        item=item,
        decision="deep_dive",
        report_type=report_type,
        report_title=title,
        score=82,
        reader_hook="",
        why_now="",
        evidence_level="media",
        reason="",
    )


def archive_item(title: str, original: str, url: str, topic_direction: str = "ai-frontier"):
    fingerprints = radar.report_fingerprints(title, original)
    seen_at = radar.now_bj().isoformat()
    return {
        "title": title,
        "original_title": original,
        "url": url,
        **fingerprints,
        "topic_cluster": radar.topic_cluster_key(title, original, url, topic_direction),
        "topic_direction": topic_direction,
        "last_seen_at": seen_at,
        "first_seen_at": seen_at,
    }


class DuplicatePolicyTest(unittest.TestCase):
    def test_media_host_is_not_entity_for_archive_dedupe(self):
        archive = {
            "items": [
                archive_item(
                    "xAI Grok 安全风波",
                    "Grok is hosting deepfake content",
                    "https://techcrunch.com/2026/06/10/grok-safety/",
                )
            ]
        }
        candidate = decision(
            "KPMG 用 AI 写报告翻车：普通程序员还敢信吗？",
            "KPMG pulls report on AI usage due to apparent hallucinations",
            "https://techcrunch.com/2026/06/15/kpmg-ai-report/",
        )

        duplicate, reason = radar.is_duplicate_topic(candidate, SITE, archive, "2026-06-15-morning")

        self.assertFalse(duplicate, reason)

    def test_ios_app_story_does_not_become_apple_entity(self):
        self.assertEqual(radar.canonical_entity("我开发了个 iOS 话术键盘", "https://v2ex.com/t/123"), "v2ex")
        self.assertEqual(radar.canonical_entity("WWDC26 Apple 智能大升级", "https://sspai.com/post/123"), "apple")

    def test_same_entity_same_angle_still_dedupes(self):
        archive = {
            "items": [
                archive_item(
                    "Anthropic最强模型被政府强制下线，AI安全警告反噬？",
                    "Anthropic cuts off Fable 5 and Mythos 5 access following government order",
                    "https://www.theverge.com/2026/06/14/anthropic-fable-access",
                )
            ]
        }
        candidate = decision(
            "美国政府封禁Fable 5和Mythos 5：普通程序员该不该慌？",
            "Statement on the US government directive to suspend access to Fable 5 and Mythos 5",
            "https://simonwillison.net/2026/Jun/15/fable-directive/",
            report_type="risk-warning",
        )

        duplicate, reason = radar.is_duplicate_topic(candidate, SITE, archive, "2026-06-15-morning")

        self.assertTrue(duplicate)
        self.assertTrue(any(text in reason for text in ["同主体/同产品", "同主体/同角度", "相似选题"]))

    def test_optional_verdict_is_preserved(self):
        report = {
            "decision": "deep_dive",
            "score": 70,
            "evidence_level": "media",
            "uncertainty_flags": ["缺少反方材料"],
            "title": "一个值得补证的需求解决类选题",
            "report_type": "case-study",
        }
        dossier = {"verdict": {"label": "可选", "status": "可选选题"}}

        normalized = radar.normalize_selection_dossier(report, dossier)

        self.assertEqual(normalized["verdict"]["label"], "可选")

    def test_topic_level_keeps_two_public_levels(self):
        base_report = {
            "decision": "deep_dive",
            "score": 82,
            "evidence_level": "near_source",
            "uncertainty_flags": [],
            "title": "强事实入口选题",
            "report_type": "hot-event",
        }
        strong_dossier = {
            "confidence": 78,
            "quality_gate": {"pass": True},
            "topic_tension": {"score": 8, "conflict_point": "平台承诺和实际风险冲突"},
        }
        secondary_report = {**base_report, "score": 72, "uncertainty_flags": ["缺少反方材料"]}
        secondary_dossier = {
            "confidence": 65,
            "quality_gate": {"pass": True},
            "topic_tension": {"score": 7, "conflict_point": "有讨论空间"},
        }
        optional_report = {**base_report, "score": 70, "evidence_level": "media"}

        self.assertEqual(radar.topic_level(base_report, strong_dossier), "推荐")
        self.assertEqual(radar.topic_level(secondary_report, secondary_dossier), "推荐")
        self.assertEqual(radar.topic_level(optional_report, {"topic_tension": {"score": 6}}), "可选")

    def test_optional_topic_can_upgrade_only_with_stronger_evidence(self):
        archived = archive_item(
            "KPMG AI 报告翻车",
            "KPMG pulls report on AI usage due to apparent hallucinations",
            "https://techcrunch.com/2026/06/15/kpmg-ai-report/",
        )
        archived["verdict"] = "可选"
        archived["evidence_level"] = "media"
        archived["score"] = 58
        archived["first_seen_at"] = "2026-06-10T08:00:00+08:00"
        archived["last_seen_at"] = "2026-06-10T08:00:00+08:00"
        archive = {"items": [archived]}
        upgraded = decision(
            "KPMG AI 报告翻车",
            "KPMG pulls report on AI usage due to apparent hallucinations",
            "https://techcrunch.com/2026/06/15/kpmg-ai-report/",
        )
        upgraded.evidence_level = "near_source"
        upgraded.score = 72
        upgraded.reader_hook = "有明确证据补足和传播张力。"
        stale = decision(
            "KPMG AI 报告翻车",
            "KPMG pulls report on AI usage due to apparent hallucinations",
            "https://techcrunch.com/2026/06/15/kpmg-ai-report/",
        )
        stale.evidence_level = "media"
        stale.score = 60

        duplicate, reason = radar.is_duplicate_topic(upgraded, SITE, archive, "2026-06-16-morning")
        stale_duplicate, stale_reason = radar.is_duplicate_topic(stale, SITE, archive, "2026-06-16-morning")

        self.assertFalse(duplicate, reason)
        self.assertTrue(stale_duplicate, stale_reason)

    def test_invalid_report_type_from_llm_is_coerced(self):
        candidate = decision(
            "GitHub 开发者工具变化",
            "GitHub Copilot improves context handling and model routing",
            "https://github.blog/example",
            report_type="ai-frontier",
        )

        report = radar.build_report(candidate, SITE, {"report_type_rules": {}}, "2026-06-18-morning")

        self.assertIn(report["report_type"], SITE["report_types"])
        self.assertEqual(candidate.traceability["invalid_report_type"], "ai-frontier")


if __name__ == "__main__":
    unittest.main()
