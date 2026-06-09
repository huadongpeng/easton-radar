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
- `BRAVE_SEARCH_API_KEY`（可选但推荐，用于稳定补证搜索）
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 启用 Pages

Settings -> Pages -> Source 选择 GitHub Actions。
