# Radar 流程拆解

## Step 1：抓取

输入：`config/sources.seed.json`

支持类型：RSS/Atom、公开 JSON API、HN Algolia API 等稳定公开源。

每条原始记录保留：

- 标题
- URL
- 来源名称
- 数据源分类 `source_category`
- 来源类型 `source_type`
- 发布时间
- 抓取时间
- 摘要

注意：`source_category` 只用于内部溯源和后续统计，不是网站栏目。

## Step 2：去重

按 URL、标题相似度和同一公告转载关系去重，优先保留更接近一手的来源。

## Step 3：初筛

模型：DeepSeek v4 Flash。

输出：

- `decision`: `deep_dive` / `brief` / `skip`
- `topic_direction`: 选题方向，也是网站主栏目归属
- `report_type`: 报告类型，只表示分析方法
- `score`: 相关性和深挖潜力
- `reader_hook`: 普通读者入口
- `why_now`: 为什么现在值得看
- `evidence_level`: 证据等级
- `collection_fit`: 是否符合信息收集原则
- `investigation_direction`: 后续深挖方向
- `uncertainty_flags`: 存疑点
- `reject_reason`: 跳过理由

初筛不是写稿判断，而是资料价值判断：这条线索是否值得进入 Radar、后续还缺什么证据、能否给下游项目提供足够材料。

`topic_direction` 当前可选方向：

- `ai-frontier`
- `cross-border`
- `indie-builder`
- `platform-rules`

`report_type` 当前可选分析方法：

- `investigation`
- `opportunity`
- `tool-ledger`
- `platform-rules`
- `case-study`
- `risk-warning`

## Step 4：报告生成

当前第一版为轻量调查报告，必须包含：

- 线索是什么
- 是否符合信息收集原则
- 应该沿哪个方向深挖
- 哪些地方没有证据或仍然存疑
- 为什么现在值得看
- 和老花人设的关系
- 普通读者入口
- 程序员/IT 视角
- 已确认事实
- 证据链
- 基础概念和边界
- 风险和缺口
- 不应夸大的地方

报告不写成内容平台正文，只作为 Radar 的证据沉淀和方向判断。

## Step 5：后续流程交接包

每条报告必须生成 `downstream_handoff`：

- `for_gpt_editor`: 标题种子、原始标题、来源 URL、角度候选、必须保留的信息、不能写成结论的点、待解决问题。
- `for_cms`: slug、canonical URL、SEO 标题、SEO 描述、标签、选题方向、报告类型、来源分类、证据等级、发布状态。
- `for_research_loop`: 继续检索词、证据缺口、停止信号。

## Step 6：发布

生成内容：

- `data/{batch_id}.json`
- `data/latest.json`
- `reports/{report_id}.json`
- `site/index.html`
- `site/briefings/index.html`
- `site/topics/{topic_direction}/index.html`
- `site/reports/{report_type}/index.html`
- `site/items/{report_id}/index.html`
- `site/robots.txt`
- `site/sitemap.xml`
- `site/llms.txt`
- `site/ads.txt`

GitHub Pages 只发布 `site/` 目录。

## Step 7：Telegram 通知

Telegram 只发摘要和 Radar 链接，不发长文。

## 失败处理

- 单个源失败：记录失败，不中断全局流程。
- GitHub Actions 长期抓不到的源：从配置中移除。
- DeepSeek 不可用：降级为规则初筛，但保留 `traceability` 标记。
- 没有高潜力线索：只发简报，不强行生成深度报告。
