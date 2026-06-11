param(
    [string]$Token = "",
    [string]$ChatId = "",
    [string]$TestMessage = "Bot de trading: Telegram configurado correctamente.",
    [switch]$RestartLive
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvPath = Join-Path $Root ".env"
$Python = Join-Path $Root "venv\Scripts\python.exe"

function Set-DotEnvValue {
    param(
        [string]$Key,
        [string]$Value
    )

    $Lines = @()
    if (Test-Path $EnvPath) {
        $Lines = Get-Content $EnvPath
    }

    $Pattern = "^\s*$([regex]::Escape($Key))\s*="
    $Found = $false
    $Updated = foreach ($Line in $Lines) {
        if ($Line -match $Pattern) {
            $Found = $true
            "$Key=$Value"
        }
        else {
            $Line
        }
    }

    if (-not $Found) {
        $Updated += "$Key=$Value"
    }

    $Updated | Set-Content $EnvPath -Encoding UTF8
}

function Invoke-TelegramApi {
    param(
        [string]$Method,
        [hashtable]$Body = @{}
    )

    $Uri = "https://api.telegram.org/bot$Token/$Method"
    if ($Body.Count -gt 0) {
        return Invoke-RestMethod -Method Post -Uri $Uri -Body $Body -TimeoutSec 20
    }
    return Invoke-RestMethod -Method Get -Uri $Uri -TimeoutSec 20
}

if (-not $Token) {
    $Token = Read-Host "Pega el TELEGRAM_BOT_TOKEN de BotFather"
}
$Token = $Token.Trim()
if (-not $Token) {
    throw "Token vacio. Crea un bot con @BotFather y vuelve a ejecutar este script."
}

$Me = Invoke-TelegramApi -Method "getMe"
if (-not $Me.ok) {
    throw "Telegram rechazo el token."
}
Write-Host "Bot detectado: @$($Me.result.username)"

if (-not $ChatId) {
    Write-Host ""
    Write-Host "Ahora abre Telegram, busca @$($Me.result.username), pulsa Start o envia cualquier mensaje."
    Write-Host "Cuando ya lo hayas enviado, presiona Enter aqui."
    Read-Host | Out-Null

    $Updates = Invoke-TelegramApi -Method "getUpdates"
    if (-not $Updates.ok -or -not $Updates.result -or $Updates.result.Count -eq 0) {
        throw "No encontre mensajes. Envia /start al bot y ejecuta otra vez."
    }

    $Candidates = @()
    foreach ($Update in $Updates.result) {
        $Msg = $Update.message
        if (-not $Msg) { $Msg = $Update.channel_post }
        if (-not $Msg) { continue }
        $Chat = $Msg.chat
        if (-not $Chat) { continue }
        $Title = ""
        if ($Chat.title) {
            $Title = [string]$Chat.title
        }
        elseif ($Chat.username) {
            $Title = [string]$Chat.username
        }
        elseif ($Chat.first_name) {
            $Title = [string]$Chat.first_name
        }

        $Candidates += [pscustomobject]@{
            ChatId = [string]$Chat.id
            Type = [string]$Chat.type
            Title = $Title
            Date = [int]$Msg.date
        }
    }

    if ($Candidates.Count -eq 0) {
        throw "Recibi updates, pero ninguno tenia chat_id usable."
    }

    $Selected = $Candidates | Sort-Object Date -Descending | Select-Object -First 1
    $ChatId = $Selected.ChatId
    Write-Host "Chat detectado: $ChatId ($($Selected.Type) $($Selected.Title))"
}

$ChatId = $ChatId.Trim()
if (-not $ChatId) {
    throw "ChatId vacio."
}

Set-DotEnvValue "TELEGRAM_ENABLED" "true"
Set-DotEnvValue "TELEGRAM_BOT_TOKEN" $Token
Set-DotEnvValue "TELEGRAM_CHAT_ID" $ChatId

Write-Host ".env actualizado. Enviando prueba..."
Set-Location $Root
& $Python "main.py" "telegram-test" "--message" $TestMessage

if ($RestartLive) {
    Write-Host "Reiniciando live-loop para cargar Telegram..."
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\stop_bot.ps1") -Live
    & powershell -ExecutionPolicy Bypass -File (Join-Path $Root "scripts\start_live_guarded.ps1") -Cycles 0 -SleepSeconds 300
}

Write-Host "Telegram quedo configurado."
