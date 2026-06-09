# 01 Easton Radar

Easton Radar 是老花的信息差侦察站。

它不负责直接写内容正文，只负责综合稳定、公开、可复查的信息流，初筛出候选选题，判断这些选题是否值得继续写，保留证据链、基础概念、材料缺口、逻辑闭环和可写方向，并发布到 GitHub Pages。

## 项目定位

- 运行环境：GitHub Actions + GitHub Pages。
- 模型：DeepSeek v4 Flash，负责初筛、选题价值判断、报告类型判断、收集原则判断和轻量调查。
- 频率：每天早、中、晚 3 次定时采集。
- 输出：静态 Radar 网站、JSON 数据、Telegram 摘要通知。

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

- AI 前沿与工具链：模型、Agent、开发工具、API、云服务和自动化能力变化。
- AI 实操与自动化：个人助手、内容生产、办公自动化、数据分析、客服、运营工具和工作流落地。
- 跨境出海与支付：跨境支付、海外平台、独立站、电商、合规、收款和出海基础设施。
- 独立开发与副业实验：独立开发、开源项目、工具站、微型 SaaS、副业实验和真实项目复盘。
- 平台规则与流量生态：公众号、小红书、视频号、Google SEO、AI SEO、推荐机制和分发生态变化。
- 技术人现金流与风险避坑：接项目、外包、远程工作、合同、回款、债务、法律风险、卖课骗局和套利骗局。

报告类型只表示分析方法，不承担主栏目职责，也不生成公开栏目页。公开网站只按 `topic_direction` 组织，避免后续 GPT 应用被“分析方法”误导。

当前报告类型：

- 候选选题池：当天值得进入雷达观察、继续补证或暂缓的线索集合。
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
| `TELEGRAM_BOT_TOKEN` | Telegram bot |
| `TELEGRAM_CHAT_ID` | Telegram 接收频道或用户 |

## 本地验证

```powershell
py -3.13 -m py_compile src\radar.py
py -3.13 src\radar.py --slot auto --no-telegram
```

## 非目标

- 不做反爬绕过。
- 不做登录态采集。
- 不用 Playwright 或代理池硬爬论坛。
- 不直接生成内容平台正文。
- 不强行每天产深度文章。
