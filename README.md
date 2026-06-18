# 01 Easton Radar

Easton Radar 是老花的信息简报站。

它不再负责替用户选题，也不判断公众号主文潜力。它只负责每天早报、午报、晚报抓取稳定、公开、可复查的信息源，按分类汇总外部变化，并用一句话讲清楚发生了什么。

## 项目定位

- 运行环境：GitHub Actions + GitHub Pages。
- 模型：DeepSeek v4 Flash，负责信息初筛、分类、一句话摘要和存疑标记。
- 频率：每天早、中、晚 3 次定时采集。
- 输出：静态 Radar 网站、JSON 数据、Telegram 简报通知。

## 页面形态

首页按本批次展示：

- 早报 / 午报 / 晚报。
- `AI前沿`、`工具&规则`、`跨境&出海`、`副业&信息差` 四类。
- 每类最多保留少量信息；没有就显示暂无值得记录的信息。
- 每条信息只展示一句话摘要和原始来源入口。

详情页只用于查证：

- 原始标题和链接。
- 一句话摘要。
- 事实入口和证据入口。
- 存疑点、不能夸大的地方、继续检索词。

## 和其他项目的关系

- `01-easton-radar`：只做信息采集、分类汇总、证据入口和一句话简报。
- 个人资产系统：从用户自身项目、资产、经验、内容缺口出发决定写什么。
- `02-easton-gpt-editor`：在用户明确选择材料后，再做交互式创作。
- `03-easton-cms`：负责内容包、发布、微信草稿和数据复盘。

Radar 不再当主编，只当外部事实传感器。

## 信息分类

- AI前沿：模型、Agent、OpenAI、Claude、Gemini、底层能力和 AI 产品关键动态。
- 工具&规则：开发工具、AI 实操、API、云服务、自动化工作流、平台政策、账号规则、搜索流量和内容分发生态变化。
- 跨境&出海：跨境支付、海外平台、独立站、电商、合规、收款和出海基础设施。
- 副业&信息差：独立开发、副业项目、工具站、微型 SaaS、开源项目、现金流风险、外包回款、信息差机会和风险避坑。

## 信息源原则

Radar 宁可少，也不要脏。

- 优先官方博客、官方文档、changelog、GitHub repo/release/issue、价格页、政策原文。
- 其次使用稳定可访问的高质量开发者和商业信息源。
- 如果 GitHub Actions 抓不到、需要登录、需要代理、需要绕 Cloudflare、需要浏览器指纹或验证码，直接放弃。
- Reddit、封闭论坛和强反爬站点不作为核心抓取源。
- 没有证据链的收入截图、营销话术、二手转述，只能做弱线索，不能写成事实。

## 核心流程

```text
稳定信息源
  -> 拉取 RSS/API/公开 JSON
  -> 去重和基础清洗
  -> DeepSeek v4 Flash 初筛
  -> 判断信息分类和内部报告类型
  -> 生成一句话摘要、证据入口和存疑点
  -> 生成早报/午报/晚报静态网站
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
| `TOPHUBDATA_ACCESS_KEY` | 可选，TopHubData/榜眼数据访问密钥 |
| `TOPHUBDATA_ENABLE_PAID_DETAIL` | 可选，默认 `true`；设为 `false` 可强制只用免费节点发现，不导入热点标题 |
| `TOPHUBDATA_PAID_DETAIL_LIMIT_PER_RUN` | 可选，默认 `11`；限制每次 Radar 运行最多调用多少次 TopHubData 付费 API |
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

默认本地脚本会加 `--no-telegram`，避免测试时发通知；需要测试 Telegram 时加 `-Telegram`。脚本会把输出同步写入 `logs/radar-local-*.log`。

## 非目标

- 不做反爬绕过。
- 不做登录态采集。
- 不用 Playwright 或代理池硬爬论坛。
- 不直接生成内容平台正文。
- 不替用户选题。
- 不判断公众号主文潜力。
