# 调查报告 Schema

建议每篇调查报告同时输出 Markdown 和 JSON。

```json
{
  "id": "2026-06-09-morning-openai-pricing",
  "batch": "2026-06-09-morning",
  "title": "OpenAI API 价格调整，对小团队 AI 应用成本意味着什么",
  "category": "ai-tools",
  "source_urls": ["https://example.com/original"],
  "source_level": "official",
  "relevance_score": 86,
  "reader_hook": "用 API 做副业工具或内部自动化的人，需要重新算账。",
  "article_mode": "拆账本",
  "evidence_level": "strong",
  "facts": [
    {
      "claim": "官方价格页更新了某模型价格",
      "type": "confirmed_fact",
      "source_url": "https://example.com/pricing",
      "confidence": 0.95
    }
  ],
  "questions": ["免费额度是否变化？"],
  "risks": ["不能把价格变化直接推导为所有 AI 应用成本下降。"]
}
```

## 证据类型

- `confirmed_fact`：有一手或多源交叉证据。
- `high_probability_inference`：间接证据一致，但没有直接确认。
- `unverified_lead`：线索，不可写成事实。
- `opinion`：观点或判断。
