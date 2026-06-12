# 信息源策略

## 核心原则

Radar 追求可信、稳定、可复查，不追求全网抓取。

只要一个源需要大量反爬绕过、登录态、代理池、验证码、浏览器指纹或手工维持 cookie，就不作为核心来源。

GitHub Actions 能稳定抓到，是第一版信息源的硬门槛。

## 优先级

### P0 官方和一手源

- 官方博客
- 官方 changelog（只作为事实源；除非涉及价格、额度、封禁、隐私、合规、迁移成本、工作流替代或大面积开发者影响，否则不单独构成选题）
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

至少满足 2 个条件，但推荐选题必须同时满足第 2、4、7 条：

1. 和老花人设主线相关：AI、开发、副业、独立开发、出海、自动化、工具账本、平台规则、程序员现金流。
2. 有老花人设解读角度：能用技术人、AI 工具、副业、出海、规则、成本或避坑视角解释清楚。
3. 目标读者有兴趣：能明确服务某一层读者，而不是笼统写给所有人。
4. 泛兴趣普通人有入口：标题、开头或故事里有普通人能看懂的冲突、反差、数字、踩坑或普通人关系。
5. 有一手或近源证据：官方、文档、仓库、价格页、真实案例。
6. 有可拆的成本、门槛、风险、机会或平台规则变化。
7. 有隐含的可讨论问题：读者能围绕“谁受益、谁吃亏、谁误判、谁被迫改变、值不值得跟、普通技术人该不该信”发表观点。

读者分层：

- 泛兴趣普通人：不懂技术，但会被标题、故事、反差、数字、踩坑和普通人关系吸引，决定传播上限。
- 入门读者：学生、刚毕业、1-5 年新人，想知道行业变化和避坑。
- 核心技术人：程序员、测试、运维、实施、小公司技术负责人，是账号基本盘。
- 高价值商业读者：独立开发、出海、SEO、SaaS、副业探索者，关心路径、规则、账本和流量。
- 副业普通人：技术不强但想找机会，必须讲清技术门槛、资金门槛和失败信号。
- 共鸣读者：30+/35 岁焦虑的 IT 打工人，关心现金流、技能迁移和低成本验证，但不能把账号写成情绪号。

如果只能写成“趋势来了”“值得关注”“行业信号”，降级为可选线索，不进入推荐选题。

如果只能写成“某产品新增/废弃了一个功能”“某 CLI 支持了一个命令”“某 SDK 有 breaking change”，默认不进入候选池。只有当它能继续追出迁移成本、账单风险、平台绑定、合规风险、用户反弹或普通技术人的真实选择题时，才进入可选。

Radar 不是官方更新聚合器。官方源的价值是提供证据，选题价值必须来自“这件事背后有什么人会产生分歧、利益变化或错误预期”。

## 选题方向、报告类型和数据源分类的区别

网站主栏目使用选题方向：

- AI前沿
- 工具&规则
- 跨境&出海
- 副业&信息差

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
- `hot_events`：TechCrunch AI、The Verge AI、The Register AI/ML、MIT Technology Review AI、Wired AI、Medianama，TopHubData 免费榜单节点发现，以及 HN 上围绕 AI/平台/开发者工具的 backlash、controversy、pricing、ban、privacy、lawsuit 查询。

TopHubData 使用边界：只调用免费接口。官方成本表显示“全部榜单列表”和“单个榜单快照列表”为免费；但快照列表只返回快照 ID/时间戳，不返回热点标题。“单个榜单最新详细”“全网热点内容搜索”“今日热榜榜中榜”“单个榜单快照详情”“热点日历事件”均会消耗 u，默认流程禁止调用。

中文热榜不能直接等同于可写选题。入选必须满足：能从技术经理、程序员、AI 工具、自动化、平台规则、内容流量、账号风险、职业变化、现金流或副业试错角度提出老花的独特判断。否则即使热度高，也只能跳过或作为弱线索沉淀。
- `developer_business`：Hacker News、HN Show、Product Hunt、YC Blog、The Bootstrapped Founder、Failory、Side Hustle Nation、V2EX、阮一峰。
- `overseas_and_platforms`：Rest of World、Practical Ecommerce、Stripe Blog、Medianama，以及支付/出海相关 HN 查询。
- `platform_policy`：Shopify Developer Changelog、Apple Developer News，以及搜索生态、支付合规相关 HN 查询。

如果新增源在本地或 GitHub Actions 返回 403/404/429，先移除，不为抓取炫技牺牲稳定性。

## 批次覆盖策略

每个批次最多保留 48 条报告。为了避免 AI 平台 changelog 把页面刷屏，入选报告按数据源分类设置上限：

- `ai_tools`：最多 20 条。
- `hot_events`：最多 8 条。
- `developer_business`：最多 14 条。
- `overseas_and_platforms`：最多 8 条。
- `platform_policy`：最多 8 条。

如果某个分类当批次没有足够高质量线索，宁可低于 48 条，也不让单一分类继续补位刷屏。这样可以保证 Radar 既跟住 AI/开发者平台，又不丢掉副业、出海、支付、搜索生态和平台规则这些后续写作更容易展开的源头。
