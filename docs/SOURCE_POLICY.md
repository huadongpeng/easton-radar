# 信息源策略

## 核心原则

Radar 追求可信、稳定、可复查，不追求全网抓取。

只要一个源需要大量反爬绕过、登录态、代理池、验证码、浏览器指纹或手工维持 cookie，就不作为核心来源。

GitHub Actions 能稳定抓到，是第一版信息源的硬门槛。

## 优先级

### P0 官方和一手源

- 官方博客
- 官方 changelog
- 官方文档
- GitHub repo/release/issue/discussion
- 平台价格页
- API 文档
- 政策和规则原文

### P1 高质量近源

- Hacker News
- Product Hunt
- Simon Willison
- GitHub Blog
- Cloudflare/AWS/Vercel/OpenAI/Google 等开发者博客
- 有代码、有复盘、有实测的独立开发者文章

### P2 可参考但要打折的源

- 行业媒体
- 商业公司报告
- Newsletter
- 中文技术博客
- V2EX 等可稳定访问社区

### P3 不作为核心源

- Reddit 直抓
- 需要登录的海外论坛
- 反爬严格的网站
- 只有截图和二手转述的爆料
- 纯营销软文

## 深挖门槛

至少满足 2 个条件：

1. 和老花人设主线相关：AI、开发、副业、独立开发、出海、自动化、工具账本、平台规则、程序员现金流。
2. 有普通读者入口：懂一点技术但不深的人也能看懂这事和自己有什么关系。
3. 有一手或近源证据：官方、文档、仓库、价格页、真实案例。
4. 有可拆的成本、门槛、风险、机会或平台规则变化。

如果只能写成“趋势来了”“值得关注”“行业信号”，降级为观察线索，不进入可信选题报告。

## 选题方向、报告类型和数据源分类的区别

网站主栏目使用选题方向：

- AI 前沿与工具链
- AI 实操与自动化
- 跨境出海与支付
- 独立开发与副业实验
- 平台规则与流量生态
- 技术人现金流与风险避坑

报告类型只表示分析方法：

- 深度调查
- 机会拆解
- 工具账本
- 平台规则
- 案例复盘
- 风险避坑

数据源分类只用于内部管理，例如：

- `ai_tools`
- `developer_business`
- `overseas_and_platforms`
- `platform_policy`

不要把报告类型或数据源分类当成网站主栏目。

## 当前数据源覆盖

第一版只保留 GitHub Actions 可稳定抓取的公开源。

- `ai_tools`：OpenAI、Google AI、DeepMind、Hugging Face、Microsoft AI、GitHub Blog、Cloudflare Changelog、Vercel Changelog、AWS ML、Simon Willison、Changelog。
- `developer_business`：Hacker News、HN Show、Product Hunt、YC Blog、The Bootstrapped Founder、Failory、Side Hustle Nation、V2EX、阮一峰。
- `overseas_and_platforms`：Rest of World、Practical Ecommerce、Stripe Blog、Medianama，以及支付/出海相关 HN 查询。
- `platform_policy`：Shopify Developer Changelog、Apple Developer News，以及搜索生态、支付合规相关 HN 查询。

如果新增源在本地或 GitHub Actions 返回 403/404/429，先移除，不为抓取炫技牺牲稳定性。

## 批次覆盖策略

每个批次最多保留 48 条报告。为了避免 AI 平台 changelog 把页面刷屏，入选报告按数据源分类设置上限：

- `ai_tools`：最多 20 条。
- `developer_business`：最多 14 条。
- `overseas_and_platforms`：最多 8 条。
- `platform_policy`：最多 8 条。

如果某个分类当批次没有足够高质量线索，宁可低于 48 条，也不让单一分类继续补位刷屏。这样可以保证 Radar 既跟住 AI/开发者平台，又不丢掉副业、出海、支付、搜索生态和平台规则这些后续写作更容易展开的源头。
