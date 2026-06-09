# src

杩欓噷鍚庣画鏀?Radar 浠ｇ爜銆?
寤鸿鎷嗗垎锛?
- `fetchers/`锛歊SS銆丄PI銆丟itHub銆丠N 绛夋姄鍙栧櫒銆?- `models/`锛欴eepSeek v4 Flash 璋冪敤鍜?JSON 瑙ｆ瀽銆?- `pipeline/`锛氬垵绛涖€佽ˉ璇併€佹姤鍛婄敓鎴愩€佽川妫€銆?- `render/`锛氱敓鎴?GitHub Pages 闈欐€侀〉闈€?- `notify/`锛歍elegram 閫氱煡銆?
绗竴鐗堜笉瑕佽繃搴﹁璁°€傝兘绋冲畾鎶撳彇銆佺敓鎴愭姤鍛娿€佸彂閫氱煡锛屾瘮澶嶆潅妗嗘灦鏇撮噸瑕併€?'@

Ensure-Dir "01-easton-radar/data"
Ensure-Dir "01-easton-radar/reports"
Ensure-Dir "01-easton-radar/site"
Write-Text "01-easton-radar/data/.gitkeep" ""
Write-Text "01-easton-radar/reports/.gitkeep" ""
Write-Text "01-easton-radar/site/.gitkeep" ""
Write-Text "01-easton-radar/.gitignore" @'
.env
__pycache__/
.venv/
node_modules/
.DS_Store