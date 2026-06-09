# 01 Easton Radar

Easton Radar 是老花的信息差侦察站。

它不负责直接写公众号成稿，只负责从稳定、公开、可复查的信息源里发现线索，保留证据链，生成调查报告，并发布到 GitHub Pages。公众号、小红书、视频号等后续内容生产，由独立的 GPT 编辑应用和 CMS 接手。

## 项目定位

- 运行环境：GitHub Actions + GitHub Pages。
- 模型：DeepSeek v4 Flash，负责初筛、报告类型判断和轻量调查。
- 频率：每天早、中、晚 3 次定时采集。
- 输出：静态 Radar 网站、JSON 数据、Telegram 摘要通知。

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

数据源分类只作为内部字段使用，例如 `ai_tools`、`developer_business`、`overseas_and_platforms`，不能直接变成网站主栏目。

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
  -> DeepSeek v4 Flash 初筛
  -> 判断 report_type
  -> 生成简报和调查报告 JSON
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
- 不直接生成公众号成稿。
- 不强行每天产深度文章。
