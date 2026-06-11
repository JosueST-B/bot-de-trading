param(
    [string]$Message = "Bot de trading: prueba de Telegram OK."
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

function Read-DotEnvValue {
    param([string]$Key)
    $Line = Get-Content (Join-Path $Root ".env") |
        Where-Object { $_ -match "^\s*$Key\s*=" } |
        Select-Object -First 1
    if (-not $Line) { return "" }
    return ($Line -split "=", 2)[1].Trim()
}

$Token = Read-DotEnvValue "TELEGRAM_BOT_TOKEN"
$ChatId = Read-DotEnvValue "TELEGRAM_CHAT_ID"

if (-not $Token -or -not $ChatId) {
    throw "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env"
}

$Body = @{
    chat_id = $ChatId
    text = $Message
    parse_mode = "HTML"
    disable_web_page_preview = $true
}

$Response = Invoke-RestMethod `
    -Method Post `
    -Uri "https://api.telegram.org/bot$Token/sendMessage" `
    -Body $Body

$Response | ConvertTo-Json -Depth 5
