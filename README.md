# 01 Easton Radar

Easton Radar 是老花的信息差侦察站。

它不负责直接写内容正文，只负责从稳定、公开、可复查的信息源里发现线索，判断是否符合信息收集原则，保留证据链，标记存疑点，生成调查报告，并发布到 GitHub Pages。

## 项目定位

- 运行环境：GitHub Actions + GitHub Pages。
- 模型：DeepSeek v4 Flash，负责初筛、报告类型判断、收集原则判断和轻量调查。
- 频率：每天早、中、晚 3 次定时采集。
- 输出：静态 Radar 网站、JSON 数据、Telegram 摘要通知。

## 三项目协作使命

Easton Radar 是三项目链路的上游情报层。

- `01-easton-radar`：只做信息采集、证据沉淀、存疑标记和调查方向判断。
- `02-easton-gpt-editor`：读取 Radar 链接或 JSON 调查包，再结合人设进行多轮创作。
- `03-easton-cms`：接收标准化内容包，负责发布、推送、归档和复盘。

因此每条 Radar 报告都必须给足后续流程所需资料：

- 给 GPT 编辑应用：来源、事实、存疑点、可展开角度、不可写成结论的点、需要补证的问题。
- 给 CMS：slug、canonical URL、SEO 标题、摘要、标签、报告类型、来源分类、证据等级、发布状态。
- 给研究闭环：继续检索词、证据缺口、停止信号。

## 栏目定义

网站栏目按“报告类型”划分，不按数据源分类划分。

当前报告类型：

- 简报：当天值得扫一眼的线索集合。
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
  -> 判断 report_type
  -> 生成简报、调查报告 JSON 和 downstream_handoff
  -> 生成静态网站
  -> GitHub Pages 发布
  -> Telegram 通知
```

## 环境变量

| 名称 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek v4 Flash 调用 |
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
