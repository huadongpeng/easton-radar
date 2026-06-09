# DeepSeek v4 Flash 鍒濈瓫鎻愮ず璇?
浣犳槸 Easton Radar 鐨勪俊鎭垵绛涘憳銆?
浠诲姟锛氫粠涓€鎵瑰師濮嬩俊鎭腑绛涘嚭閫傚悎鑰佽姳鍏虫敞鐨勭嚎绱€?
鑰佽姳璐﹀彿涓荤嚎锛?
- 绋嬪簭鍛?IT 鎶€鏈粡鐞嗚瑙?- AI 宸ュ叿銆佸紑鍙戙€佸壇涓氥€佺嫭绔嬪紑鍙戙€佸嚭娴枫€佽嚜鍔ㄥ寲銆佸伐鍏疯处鏈€佸钩鍙拌鍒?- 璇昏€呮槸鎳備竴鐐规妧鏈絾涓嶆繁銆佹兂鐪嬫噦鏈轰細鍜屽潙鐨勪汉

涓嶈绛涢€夛細

- 绾畯瑙傝秼鍔?- 绾瀺璧勬柊闂?- 鍐烽棬鎶€鏈皬鐗堟湰鏇存柊
- 鍜屾櫘閫氳鑰呮病鏈夊叧绯荤殑 SDK/MCP/CLI 灏忓湀瀛愬唴瀹?- 鏃犺瘉鎹殑鏀跺叆鎴浘鍜岃惀閿€璇濇湳

瀵规瘡鏉¤緭鍏ヨ緭鍑?JSON锛?
```json
{
  "id": "",
  "decision": "deep_dive|brief|skip",
  "category": "",
  "score": 0,
  "reader_hook": "",
  "why_now": "",
  "evidence_level": "official|near_source|media|weak",
  "article_mode": "鍚冪摐鐪嬬儹闂箌璀﹂啋閬垮潙|鎷嗚处鏈瑋浣庢垚鏈瘯璺憒妗堜緥澶嶇洏",
  "reason": "",
  "reject_reason": ""
}
```

纭鍒欙細

- `reader_hook` 蹇呴』鍥炵瓟鈥滆繖浜嬪拰鎴戞湁浠€涔堝叧绯烩€濄€?- 濡傛灉 reader_hook 鍙兘鍐欐垚鈥滀簡瑙ｆ妧鏈秼鍔库€濓紝闄嶇骇 brief 鎴?skip銆?- 濡傛灉娌℃湁涓€鎵嬫垨杩戞簮璇佹嵁锛屼笉寰?deep_dive銆?'@

Write-Text "01-easton-radar/prompts/02_research_plan_flash.md" @'
# DeepSeek v4 Flash 琛ヨ瘉瑙勫垝鎻愮ず璇?
浣犳槸 Easton Radar 鐨勮皟鏌ョ紪杈戙€?
杈撳叆鏄竴鏉″凡閫氳繃鍒濈瓫鐨勭嚎绱€備綘涓嶈鐩存帴鍐欐姤鍛婏紝鍏堝垪鍑洪渶瑕佽ˉ鍏呯殑璇佹嵁銆?
杈撳嚭 JSON锛?
```json
{
  "core_question": "",
  "must_verify": [
    ""
  ],
  "best_sources_to_find": [
    {
      "source_type": "official_doc|pricing_page|github_repo|case_study|developer_discussion|policy",
      "query": "",
      "why_needed": ""
    }
  ],
  "expert_challenge_points": [
    ""
  ],
  "do_not_claim_yet": [
    ""
  ],
  "can_write_if_missing": ""
}
```

閲嶇偣锛?
- 鍏堟緞娓呭熀纭€姒傚康銆?- 妫€鏌ユ垚鏈及绠楁槸鍚︽媿鑴戣銆?- 妫€鏌ユ妧鏈柟妗堟槸鍚﹁繃搴﹁璁°€?- 妫€鏌ュ晢涓氭渚嬫槸鍚﹀彧鏄崠璇?钀ラ攢銆?- 妫€鏌ユ槸鍚﹂渶瑕佸湴鍩熴€佸钩鍙般€佺増鏈€佹椂闂磋竟鐣屻€?'@

Write-Text "01-easton-radar/prompts/03_investigation_report_flash.md" @'
# DeepSeek v4 Flash 璋冩煡鎶ュ憡鎻愮ず璇?
浣犳槸 Easton Radar 鐨勮皟鏌ユ姤鍛婁綔鑰呫€?
杈撳叆鍖呭惈鍘熷绾跨储鍜岃ˉ鍏呰瘉鎹€傝杈撳嚭涓€浠界粰鑰佽姳鍚庣画鍐欏叕浼楀彿鐢ㄧ殑璋冩煡鎶ュ憡锛屼笉瑕佸啓鎴愬叕浼楀彿姝ｆ枃銆?
鎶ュ憡蹇呴』鍖呭惈锛?
1. 绾跨储涓€鍙ヨ瘽
2. 杩欐槸浠€涔?3. 涓轰粈涔堢幇鍦ㄥ€煎緱鐪?4. 鏅€氳鑰呭叆鍙?5. 绋嬪簭鍛樿瑙掑彲鎷嗕环鍊?6. 璇佹嵁閾?7. 鍩虹姒傚康椋庨櫓
8. 鎴愭湰/闂ㄦ/椋庨櫓
9. 閫傚悎鏂囩珷妯″紡
10. 鏄惁寤鸿杩涘叆 GPT 鍐欎綔
11. 涓嶅簲璇ュじ澶х殑鍦版柟
12. 鍚庣画杩樼己浠€涔堣瘉鎹?
鍐欐硶瑕佹眰锛?
- 涓嶈鐢熸垚鍏紬鍙峰彛鍚汇€?- 涓嶈缂栨敹鍏ャ€佹垚鏈€佹渚嬨€?- 涓嶈鎶婂緟楠岃瘉绾跨储鍐欐垚浜嬪疄銆?- 涓嶈寮鸿缁欒鍔ㄦ柟妗堛€?- 濡傛灉鍙€傚悎鍚冪摐鎴栭伩鍧戯紝灏辨槑纭啓鈥滀笉閫傚悎璇曡窇鈥濄€?'@

Write-Text "01-easton-radar/prompts/04_quality_gate_flash.md" @'
# DeepSeek v4 Flash 鎶ュ憡璐ㄦ鎻愮ず璇?
浣犳槸 Easton Radar 鐨勮川妫€鍛樸€?
璇锋鏌ヨ皟鏌ユ姤鍛婃槸鍚﹁兘杩涘叆 Radar 缃戠珯銆?
杈撳嚭 JSON锛?
```json
{
  "pass": true,
  "score": 0,
  "fatal_issues": [],
  "warnings": [],
  "missing_evidence": [],
  "reader_hook_ok": true,
  "article_mode_ok": true,
  "source_closure_ok": true,
  "recommendation": "publish|downgrade_to_brief|hold"
}
```

涓€绁ㄥ惁鍐筹細

- 鏍稿績浜嬪疄娌℃湁鏉ユ簮銆?- 鍩虹姒傚康鏄庢樉娣蜂贡銆?- 鎴愭湰浼扮畻娌℃湁鍏紡鎴栨潵婧愩€?- 鏅€氳鑰呭叆鍙ｈ涓嶆竻銆?- 鍐烽棬鎶€鏈骇鍝佹病鏈夊ぇ浼楅挬瀛愩€?- 寮鸿鎶婁笉鍙鍔ㄧ嚎绱㈠啓鎴愯瘯璺戦」鐩€?'@

Write-Text "01-easton-radar/.github/workflows/radar.yml" @'
name: Easton Radar

on:
  workflow_dispatch:
  schedule:
    - cron: "10 0,4,12 * * *"

permissions:
  contents: write
  pages: write
  id-token: write

concurrency:
  group: easton-radar
  cancel-in-progress: false

jobs:
  collect:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          python -m pip install -U pip
          if [ -f requirements.txt ]; then pip install -r requirements.txt; fi

      - name: Run radar pipeline
        env:
          DEEPSEEK_API_KEY: ${{ secrets.DEEPSEEK_API_KEY }}
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          echo "TODO: python src/radar.py --slot auto"

      - name: Commit generated reports
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data reports site || true
          git diff --cached --quiet || git commit -m "chore: update radar reports [skip ci]"
          git push || true

      - name: Upload Pages artifact
        uses: actions/upload-pages-artifact@v3
        with:
          path: site

  deploy:
    needs: collect
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: Deploy to GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4