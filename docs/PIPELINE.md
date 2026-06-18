# Radar 流程拆解

Radar 现在是早报、午报、晚报式信息简报站，不再承担自动选题职责。

## Step 1：抓取

输入：`config/sources.seed.json`

支持类型：RSS/Atom、公开 JSON API、HN Algolia API、TopHubData 节点和榜单详情等稳定公开源。

每条原始记录保留：

- 标题
- URL
- 来源名称
- 数据源分类 `source_category`
- 来源类型 `source_type`
- 发布时间
- 抓取时间
- 摘要

`source_category` 只用于内部溯源和统计，不是网站栏目。

## Step 2：去重

按 URL、标题相似度、主题簇和同一公告转载关系去重，优先保留更接近一手的来源。

跨批次去重状态保存在 `radar-archive` 分支，避免早报、午报、晚报反复推同一条信息。

## Step 3：初筛

模型：DeepSeek v4 Flash。

输出：

- `decision`: `deep_dive` / `brief` / `skip`。其中 `deep_dive` 现在只表示“适合进入简报并保留详情页”，不表示推荐写文章。
- `topic_direction`: 信息分类，也是网站主栏目归属。
- `report_type`: 内部分析方法，兼容旧字段。
- `score`: 信息相关性和可查证价值。
- `reader_hook`: 兼容旧字段名，实际表示这条信息可能关联的关注方向。
- `why_now`: 为什么现在记录。
- `evidence_level`: 证据等级。
- `collection_fit`: 是否符合信息收集原则。
- `investigation_direction`: 后续补证方向。
- `uncertainty_flags`: 存疑点。
- `reject_reason`: 跳过理由。

初筛不再判断“是否值得写公众号主文”，只判断这条信息是否真实、可复查、能否用一句话讲清楚发生了什么。

## Step 4：简报条目生成

每条入选信息生成一个详情 JSON 和静态页。公开页面优先展示一句话摘要：

```text
【来源/主体】一句话讲清楚发生了什么。
```

详情页保留：

- 原始线索
- 原始链接
- 证据入口
- 已确认内容
- 存疑点
- 不能夸大的地方
- 继续检索词

内部仍保留 `selection_dossier` / `material_pack` 字段，目的是兼容历史 JSON 和旧归档，不代表当前产品仍在输出选题判断。

## Step 5：发布

生成内容：

- `data/{batch_id}.json`
- `data/latest.json`
- `reports/{report_id}.json`
- `site/index.html`
- `site/briefings/index.html`
- `site/topics/{topic_direction}/index.html`
- `site/items/{report_id}/index.html`
- `site/archive/index.html`
- `site/robots.txt`
- `site/sitemap.xml`
- `site/llms.txt`
- `site/ads.txt`

GitHub Pages 只发布本次 Action 工作区里的 `site/` 目录。`site/*`、`data/latest.json`、`data/search_usage.json` 和批次 JSON 都是运行时产物，不提交回 `main`。

首页展示本批早报/午报/晚报，按四类信息汇总。分类页展示该类历史信息，归档页按日期回看。

## Step 6：Telegram 通知

Telegram 只发摘要和 Radar 链接，不发长文。

通知格式：

```text
Easton Radar 早报｜信息简报
抓取 120 条，汇总 8 条。

AI前沿：
- 【OpenAI】一句话讲清楚发生了什么。
  https://radar.huadongpeng.com/items/...
```

## 失败处理

- 单个源失败：记录失败，不中断全局流程。
- GitHub Actions 长期抓不到的源：从配置中移除。
- 补证搜索只使用 Tavily 和 Brave，不使用易被 GitHub Actions 机房 IP 拦截的页面搜索。
- DeepSeek 不可用：使用本地启发式规则保留可查证信息，但不得伪装成完整判断。
- 没有可记录信息：发布空简报和数据源覆盖情况，不硬凑条目。
