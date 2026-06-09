# src

这里后续放 Radar 代码。

建议拆分：

- `fetchers/`：RSS、API、GitHub、HN 等抓取器。
- `models/`：DeepSeek v4 Flash 调用和 JSON 解析。
- `pipeline/`：初筛、补证、报告生成、质检。
- `render/`：生成 GitHub Pages 静态页面。
- `notify/`：Telegram 通知。

第一版不要过度设计。能稳定抓取、生成报告、发通知，比复杂框架更重要。
