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
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

可选 Variables：

- `TAVILY_SEARCH_DEPTH`：默认 `basic`，可设 `advanced`。
- `TAVILY_INCLUDE_RAW_CONTENT`：默认 `false`，可设 `markdown` 或 `text`。
- `SEARCH_API_CALL_LIMIT_PER_RUN`：默认 `18`，限制每次 Action 中每个搜索后端的 API 调用数；Tavily 达到本轮上限后，Brave 仍可作为备用搜索后端。

## 启用 Pages

Settings -> Pages -> Source 选择 GitHub Actions。
