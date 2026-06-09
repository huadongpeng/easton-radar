# 调查报告 Schema

每条报告以 JSON 为主，静态页面由 JSON 渲染生成。

```json
{
  "id": "20260609-openai-pricing",
  "batch_id": "2026-06-09-evening",
  "title": "OpenAI API 价格调整，对小团队 AI 应用成本意味着什么",
  "original_title": "OpenAI updates API pricing",
  "report_type": "tool-ledger",
  "report_type_title": "工具账本",
  "source_category": "ai_tools",
  "source_name": "OpenAI",
  "source_url": "https://example.com/original",
  "published_at": "2026-06-09T08:00:00Z",
  "score": 86,
  "evidence_level": "official",
  "reader_hook": "用 API 做副业工具或内部自动化的人，需要重新算账。",
  "why_now": "官方价格页刚更新，可能影响模型选型和成本结构。",
  "collection_fit": "符合收集原则：来源可复查，且具备进入「工具账本」类报告的分析价值。",
  "investigation_direction": "优先追价格页、额度、API 文档、替代方案和实际成本边界。",
  "uncertainty_flags": [
    "尚未形成多源交叉验证。",
    "尚未拿到账单样本。"
  ],
  "summary": "这是一条和 AI 工具成本相关的官方更新。",
  "persona_connection": [
    "AI 工具与开发成本",
    "程序员副业与独立开发"
  ],
  "facts": [
    {
      "claim": "官方价格页更新了某模型价格。",
      "type": "confirmed_fact",
      "source_url": "https://example.com/pricing",
      "confidence": 0.95
    }
  ],
  "verification": {
    "evidence_level": "official",
    "source_closure": "single source",
    "missing_evidence": [
      "实际账单样本",
      "第三方迁移案例"
    ]
  },
  "boundaries": [
    "不能把价格变化直接推导为所有 AI 应用成本下降。"
  ],
  "writing_notes": [
    "适合后续写成工具账本，不适合写成情绪化热点。"
  ]
}
```

## 字段边界

- `report_type`：网站栏目，表示这篇报告的类型。
- `title`：中文 Radar 标题，可以保留产品名/公司名，但不能整句照搬英文原题。
- `original_title`：原始来源标题。
- `source_category`：内部数据源分类，表示线索来自哪类源。
- `evidence_level`：证据等级，不等于可信结论。
- `reader_hook`：必须回答“这事和普通读者有什么关系”。
- `collection_fit`：先判断它是否符合信息收集原则。
- `investigation_direction`：粗略说明后续应该沿什么方向深挖。
- `uncertainty_flags`：没有证据、证据弱或仍存疑的地方。
- `boundaries`：必须写明不能夸大的地方。

## 证据类型

- `confirmed_fact`：有一手或多源交叉证据。
- `high_probability_inference`：间接证据一致，但没有直接确认。
- `unverified_lead`：线索，不可写成事实。
- `opinion`：观点或判断。
