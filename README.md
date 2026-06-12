# 01 Easton Radar

Easton Radar 是老花的信息差侦察站。

它不负责直接写内容正文，只负责综合稳定、公开、可复查的信息流，初筛出候选选题，判断这些选题是否值得继续写，保留证据链、基础概念、材料缺口、逻辑闭环和可写方向，并发布到 GitHub Pages。

## 项目定位

- 运行环境：GitHub Actions + GitHub Pages。
- 模型：DeepSeek v4 Flash，负责初筛、选题价值判断、报告类型判断、收集原则判断和轻量调查。
- 频率：每天早、中、晚 3 次定时采集。
- 输出：静态 Radar 网站、JSON 数据、Telegram 摘要通知。公开选题级别只分为 `推荐` 和 `可选`，推荐优先进入写作框架，可选用于保留有方向价值但仍需补证的线索。

## 三项目协作使命

Easton Radar 是三项目链路的上游情报层。

- `01-easton-radar`：只做信息采集、候选选题、证据沉淀、存疑标记、选题价值判断和调查方向判断。
- `02-easton-gpt-editor`：读取 Radar 静态网页链接或 JSON 选题包，再结合人设进行多轮创作。

因此每条 Radar 报告都必须给足后续流程所需资料，而不是给一篇模板化文章：

- 给 GPT 编辑应用：选题结论、来源、事实、证据、基础概念、逻辑闭环、可写方向、材料缺口、存疑点、不可写成结论的点、需要补证的问题。
- 给研究闭环：继续检索词、证据缺口、停止信号。

## 栏目定义

网站主栏目按“选题方向”划分，不按报告类型，也不按数据源分类划分。

当前选题方向：

- AI前沿：模型、Agent、OpenAI/Claude/Gemini、底层能力和 AI 产品关键动态。
- 工具&规则：开发工具、AI 实操、API、云服务、自动化工作流、平台政策、账号规则、搜索流量和内容分发生态变化。
- 跨境&出海：跨境支付、海外平台、独立站、电商、合规、收款和出海基础设施。
- 副业&信息差：独立开发、副业项目、工具站、微型 SaaS、开源项目、现金流风险、外包回款、信息差机会和风险避坑。

报告类型只表示分析方法，不承担主栏目职责，也不生成公开栏目页。公开网站只按 `topic_direction` 组织，避免后续 GPT 应用被“分析方法”误导。

当前报告类型：

- 候选选题池：当天值得推荐或作为可选线索继续补证的集合。
- 深度调查：事实、证据、概念和影响都需要展开的主题。
- 机会拆解：看起来能做项目、搞副业、做产品的线索。
- 工具账本：AI/API/云服务/开发工具的成本、能力和替代方案。
- 平台规则：支付、账号、广告、分发、合规、生态规则变化。
- 案例复盘：独立开发、产品增长、失败复盘、真实运营案例。
- 风险避坑：容易误导、夸大、踩坑或割韭菜的线索。

数据源分类只作为内部字段使用，例如 `ai_tools`、`developer_business`、`overseas_and_platforms`、`platform_policy`，不能直接变成网站主栏目。

## 信息源原则

Radar 宁可少，也不要脏。

- 优先官方博客、官方文档、changelog、GitHub repo/release/issue、价格页、政策原文。
- 其次使用稳定可访问的高质量开发者和商业信息源。
- 如果 GitHub Actions 抓不到、需要登录、需要代理、需要绕 Cloudflare、需要浏览器指纹或验证码，直接放弃。
- Reddit、封闭论坛和强反爬站点不作为核心抓取源。
- 没有证据链的收入截图、营销话术、二手转述，只能做线索，不能写成事实。

## 核心流程

```text
稳定信息源
  -> 拉取 RSS/API/公开 JSON
  -> 去重和基础清洗
  -> 统计数据源覆盖
  -> DeepSeek v4 Flash 初筛
  -> 判断 topic_direction
  -> 判断 report_type
  -> LLM 生成补证计划
  -> 执行搜索和正文抓取
  -> LLM 生成选题报告 JSON、selection_dossier/material_pack 和 downstream_handoff
  -> 生成静态网站
  -> GitHub Pages 发布
  -> Telegram 通知
```

## 环境变量

| 名称 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek v4 Flash 调用 |
| `TAVILY_API_KEY` | 推荐，Tavily Search API，用于 GitHub Actions 上稳定执行补证搜索 |
| `TAVILY_SEARCH_DEPTH` | 可选，默认 `basic`；可设 `advanced` 提高相关性但消耗更多额度 |
| `TAVILY_INCLUDE_RAW_CONTENT` | 可选，默认 `false`；可设 `markdown` 或 `text` 让 Tavily 返回正文内容兜底 |
| `BRAVE_SEARCH_API_KEY` | 可选，Brave Search API，作为 Tavily 之外的备用搜索后端 |
| `TOPHUBDATA_ACCESS_KEY` | 可选，TopHubData/榜眼数据访问密钥；配置后会先调用免费 `/nodes` 获取 cid 分类和 hashid，再按 `hot_events.tophubdata_cid_plan` 调用 `/nodes/@hashid` 最新榜单详情，并按 `hot_events.tophubdata_search_queries` 调用 `/search` 导入更细粒度热点内容 |
| `TOPHUBDATA_ENABLE_PAID_DETAIL` | 可选，默认 `true`；设为 `false` 可强制只用免费节点发现，不导入热点标题 |
| `TOPHUBDATA_PAID_DETAIL_LIMIT_PER_RUN` | 可选，默认 `11`；限制每次 Radar 运行最多调用多少次 TopHubData 付费 API，默认覆盖 cid 计划和关键词搜索计划，约等于最多 11u/轮 |
| `TOPHUBDATA_ITEM_LIMIT_PER_NODE` | 可选，默认 `4`；限制每个 TopHubData 榜单最多导入多少条热点标题 |
| `SEARCH_API_CALL_LIMIT_PER_RUN` | 可选，默认 `60`；限制每次 Action 的 Tavily/Brave 搜索 API 调用总数 |
| `TELEGRAM_BOT_TOKEN` | Telegram bot |
| `TELEGRAM_CHAT_ID` | Telegram 接收频道或用户 |

## 本地验证

```powershell
py -3.13 -m py_compile src\radar.py
py -3.13 src\radar.py --slot auto --no-telegram
```

如果要在本地跑完整 LLM 和搜索补证链路：

```powershell
Copy-Item .env.local.example .env.local
# 编辑 .env.local，填入 DeepSeek、Tavily/Brave 等 key
powershell -ExecutionPolicy Bypass -File tools\run-radar-local.ps1 -Slot auto
```

默认本地脚本会加 `--no-telegram`，避免测试时发通知；需要测试 Telegram 时加 `-Telegram`。脚本会把输出同步写入 `logs/radar-local-*.log`，便于排查 LLM 和搜索补证耗时。

## 非目标

- 不做反爬绕过。
- 不做登录态采集。
- 不用 Playwright 或代理池硬爬论坛。
- 不直接生成内容平台正文。
- 不强行每天产深度文章。
