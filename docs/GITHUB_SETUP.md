# GitHub 仓库创建步骤

## 建议仓库名

`easton-radar`

## 使用 GitHub CLI

```powershell
gh repo create huadongpeng/easton-radar --public --source . --remote origin --push
```

## 手动创建仓库后绑定远程

```powershell
git remote add origin git@github.com:huadongpeng/easton-radar.git
git branch -M main
git push -u origin main
```

## 配置 Secrets

- `DEEPSEEK_API_KEY`
- `TAVILY_API_KEY`（推荐，用于稳定补证搜索）
- `BRAVE_SEARCH_API_KEY`（可选，备用搜索后端）
- `TOPHUBDATA_ACCESS_KEY`（可选，TopHubData/榜眼数据；默认免费模式只用于榜单节点发现）
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

可选 Variables：

- `TAVILY_SEARCH_DEPTH`：默认 `basic`，可设 `advanced`。
- `TAVILY_INCLUDE_RAW_CONTENT`：默认 `false`，可设 `markdown` 或 `text`。
- `SEARCH_API_CALL_LIMIT_PER_RUN`：默认 `18`，限制每次 Action 的 Tavily/Brave 搜索 API 调用总数；核心控量优先通过减少每批入池选题实现。
- `TOPHUBDATA_ENABLE_PAID_DETAIL`：默认 `false`。设为 `true` 才会调用 TopHubData 单个榜单最新详细接口，官方成本表为 1u/节点。
- `TOPHUBDATA_DETAIL_LIMIT_PER_RUN`：默认 `4`，控制每轮最多拉取多少个 TopHubData 榜单详情。
- `TOPHUBDATA_ITEMS_PER_NODE`：默认 `8`，控制每个榜单最多导入多少条热点。

## 启用 Pages

Settings -> Pages -> Source 选择 GitHub Actions。
