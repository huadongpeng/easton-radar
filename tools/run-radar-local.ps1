param(
    [string]$Slot = "auto",
    [switch]$Telegram,
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $root ".env.local"

if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Missing .env.local. Copy .env.local.example to .env.local and fill the API keys."
}

Get-Content -LiteralPath $envPath -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#")) {
        return
    }
    $parts = $line.Split("=", 2)
    if ($parts.Count -ne 2) {
        return
    }
    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    if ($name) {
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

$argsList = @("src\radar.py", "--slot", $Slot)
if (-not $Telegram) {
    $argsList += "--no-telegram"
}

Push-Location $root
try {
    if (-not $LogPath) {
        $logDir = Join-Path $root "logs"
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
        $LogPath = Join-Path $logDir "radar-local-$stamp.log"
    }
    Write-Host "Writing local Radar log to $LogPath"
    py -3.13 @argsList 2>&1 | Tee-Object -FilePath $LogPath
    if ($LASTEXITCODE -ne 0) {
        throw "Radar local run failed with exit code $LASTEXITCODE. See $LogPath"
    }
}
finally {
    Pop-Location
}
