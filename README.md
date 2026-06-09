# 01 Easton Radar

Easton Radar 是老花的信息差侦察站。

它不负责写公众号成稿，只负责从高质量公开源里发现线索、形成证据链、生成调查报告，并发布到 GitHub Pages。

## 项目定位

用 GitHub Actions 免费资源，每天早中晚 3 次抓取官方/高质量订阅源，用 DeepSeek v4 Flash 做初筛和调查报告，最后把结果发到 Radar 网站和 Telegram。

## 非目标

- 不抓 Reddit 直链。
- 不绕 Cloudflare。
- 不做登录态抓取。
- 不用 Playwright/代理池硬爬论坛。
- 不直接生成公众号文章。
- 不强行每天产深度成稿。

## 核心流程

```text
稳定信息源
  -> 拉取 RSS/API/公开 JSON
  -> 去重和基础清洗
  -> DeepSeek v4 Flash 初筛
  -> 对高潜力线索生成补证查询
  -> 执行补证搜索/抓取公开资料
  -> DeepSeek v4 Flash 生成调查报告
  -> 生成静态页面
  -> GitHub Pages 发布
  -> Telegram 通知
```

## 环境变量

| 名称 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek v4 Flash 调用 |
| `TELEGRAM_BOT_TOKEN` | Telegram bot |
| `TELEGRAM_CHAT_ID` | Telegram 接收频道/用户 |

## 仓库建议

这个项目适合放 GitHub，因为它要使用 GitHub Actions 和 GitHub Pages。
