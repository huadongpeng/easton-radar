# 璋冩煡鎶ュ憡 Schema

寤鸿姣忕瘒璋冩煡鎶ュ憡鍚屾椂杈撳嚭 Markdown 鍜?JSON銆?
## JSON 绀轰緥

```json
{
  "id": "2026-06-09-morning-openai-pricing",
  "batch": "2026-06-09-morning",
  "title": "OpenAI API 浠锋牸璋冩暣锛屽灏忓洟闃?AI 搴旂敤鎴愭湰鎰忓懗鐫€浠€涔?,
  "category": "ai-tools",
  "source_urls": [
    "https://example.com/original"
  ],
  "source_level": "official",
  "relevance_score": 86,
  "reader_hook": "鐢?API 鍋氬壇涓氬伐鍏锋垨鍐呴儴鑷姩鍖栫殑浜猴紝闇€瑕侀噸鏂扮畻璐︺€?,
  "article_mode": "鎷嗚处鏈?,
  "evidence_level": "strong",
  "facts": [
    {
      "claim": "瀹樻柟浠锋牸椤垫洿鏂颁簡鏌愭ā鍨嬩环鏍?,
      "type": "confirmed_fact",
      "source_url": "https://example.com/pricing",
      "confidence": 0.95
    }
  ],
  "questions": [
    "鍏嶈垂棰濆害鏄惁鍙樺寲锛?,
    "鏃х敤鎴锋槸鍚︽湁杩佺Щ鏈燂紵"
  ],
  "risks": [
    "涓嶈兘鎶婁环鏍煎彉鍖栫洿鎺ユ帹瀵间负鎵€鏈?AI 搴旂敤鎴愭湰涓嬮檷銆?
  ],
  "writing_recommendation": {
    "should_write_wechat": true,
    "reason": "鏈夋槑纭处鏈拰鏅€氳鑰呭叆鍙ｃ€?,
    "do_not_claim": [
      "涓嶈璇存墍鏈変汉閮借兘闄嶄綆鎴愭湰銆?,
      "涓嶈璇磋繖鏄禋閽辨満浼氥€?
    ]
  }
}
```

## 璇佹嵁绫诲瀷

- `confirmed_fact`锛氭湁涓€鎵嬫垨澶氭簮浜ゅ弶璇佹嵁銆?- `high_probability_inference`锛氶棿鎺ヨ瘉鎹竴鑷达紝浣嗘病鏈夌洿鎺ョ‘璁ゃ€?- `unverified_lead`锛氱嚎绱紝涓嶅彲鍐欐垚浜嬪疄銆?- `opinion`锛氳鐐规垨鍒ゆ柇銆?
## 鏂囩珷妯″紡

- `鍚冪摐鐪嬬儹闂筦
- `璀﹂啋閬垮潙`
- `鎷嗚处鏈琡
- `浣庢垚鏈瘯璺慲
- `妗堜緥澶嶇洏`

鍙湁 `浣庢垚鏈瘯璺慲 闇€瑕佸畬鏁存楠ゃ€佹垚鏈€佸仠姝俊鍙枫€傚叾浠栨ā寮忎笉瑕佺‖濉炴搷浣滄柟妗堛€?'@

Write-Text "01-easton-radar/docs/GITHUB_SETUP.md" @'
# GitHub 浠撳簱鍒涘缓姝ラ

## 1. 鍒涘缓浠撳簱

寤鸿浠撳簱鍚嶏細

```text
easton-radar
```

濡傛灉浣跨敤 GitHub CLI锛?
```powershell
gh repo create huadongpeng/easton-radar --public --source . --remote origin --push
```

濡傛灉鍏堟墜鍔ㄥ湪 GitHub 鍒涘缓浠撳簱锛?
```powershell
git remote add origin git@github.com:huadongpeng/easton-radar.git
git branch -M main
git push -u origin main
```

## 2. 閰嶇疆 Secrets

鍦?GitHub 浠撳簱 Settings -> Secrets and variables -> Actions 涓厤缃細

- `DEEPSEEK_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## 3. 鍚敤 Pages

鎺ㄨ崘鏂瑰紡锛?
- Source: GitHub Actions

鍚庣画鐢?workflow 鐢熸垚闈欐€佺珯鐐瑰苟閮ㄧ讲鍒?Pages銆?
## 4. 妫€鏌ュ畾鏃朵换鍔?
`.github/workflows/radar.yml` 榛樿姣忓ぉ璺戜笁娆★細

- 鍖椾含 08:10
- 鍖椾含 12:10
- 鍖椾含 20:10

涔熷彲浠ユ墜鍔?workflow_dispatch 杩愯銆?'@

Write-Text "01-easton-radar/config/sources.seed.json" @'
{
  "ai_tools": {
    "feeds": [
      "https://openai.com/news/rss.xml",
      "https://blog.google/technology/ai/rss/",
      "https://deepmind.google/blog/rss.xml",
      "https://huggingface.co/blog/feed.xml",
      "https://github.blog/changelog/feed/",
      "https://github.blog/ai-and-ml/feed/",
      "https://developers.cloudflare.com/changelog/rss/developer-platform.xml",
      "https://aws.amazon.com/blogs/machine-learning/feed/",
      "https://simonwillison.net/atom/everything/",
      "https://changelog.com/feed"
    ],
    "apis": [
      "https://hn.algolia.com/api/v1/search_by_date?query=AI+LLM+agent+Claude+ChatGPT+Copilot+pricing+developer&tags=story&hitsPerPage=30"
    ]
  },
  "developer_business": {
    "feeds": [
      "https://hnrss.org/show",
      "https://news.ycombinator.com/rss",
      "https://www.producthunt.com/feed",
      "https://thebootstrappedfounder.com/feed/",
      "https://www.sidehustlenation.com/feed/",
      "https://v2ex.com/feed/create.xml",
      "https://v2ex.com/feed/tab/jobs.xml",
      "https://www.ruanyifeng.com/blog/atom.xml"
    ],
    "apis": [
      "https://hn.algolia.com/api/v1/search_by_date?query=Show+HN+SaaS+side+project+microSaaS&tags=story&hitsPerPage=30",
      "https://hn.algolia.com/api/v1/search_by_date?query=indie+maker+project+revenue+MRR&tags=story&hitsPerPage=30"
    ]
  },
  "overseas_and_platforms": {
    "feeds": [
      "https://restofworld.org/feed/latest/",
      "https://www.practicalecommerce.com/feed",
      "https://www.techinasia.com/feed",
      "https://www.medianama.com/feed",
      "https://36kr.com/feed",
      "https://www.woshipm.com/feed"
    ],
    "apis": [
      "https://hn.algolia.com/api/v1/search_by_date?query=Stripe+Paddle+Lemon+Squeezy+payment+founder&tags=story&hitsPerPage=20",
      "https://hn.algolia.com/api/v1/search_by_date?query=ecommerce+seller+amazon+shopify+developer&tags=story&hitsPerPage=20"
    ]
  },
  "work_and_risk": {
    "feeds": [
      "https://stackoverflow.blog/feed/",
      "https://hnrss.org/whoishiring/jobs",
      "https://queue.acm.org/rss/feeds/queuecontent.xml",
      "https://www.theregister.com/headlines.atom",
      "https://krebsonsecurity.com/feed/",
      "https://www.bleepingcomputer.com/feed/"
    ],
    "apis": [
      "https://hn.algolia.com/api/v1/search_by_date?query=developer+job+market+salary+AI+remote&tags=story&hitsPerPage=30"
    ]
  }
}