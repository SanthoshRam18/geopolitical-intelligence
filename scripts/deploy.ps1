# Deploy (or update) the whole system on one Linux server over SSH.
#
#   scripts\set_password.ps1                                   once: choose the login
#   scripts\deploy.ps1 -Server ubuntu@203.0.113.10 -Key C:\keys\team128.pem
#   scripts\deploy.ps1 -Server ubuntu@203.0.113.10 -Key ... -SkipData    code-only update
#   scripts\deploy.ps1 -Server ubuntu@203.0.113.10 -Key ... -Public      no login: anyone with the link
#
# Uploads the current working tree (tracked + new, not ignored files) and, unless
# -SkipData, the datasets that are not in git. The server needs nothing but Ubuntu
# and sudo; it never needs access to GitHub. See docs/DEPLOY.md.
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Key = "",
    [string]$Site = "",
    [switch]$SkipData,
    [switch]$Public
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Native([string]$What, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit $LASTEXITCODE)" }
}

$auth = Join-Path $Root "deploy\.auth"
if (-not $Public -and -not (Test-Path $auth)) { throw "No login chosen yet. Run scripts\set_password.ps1 first, or pass -Public to serve the site without one." }
$access = if ($Public) { "public" } else { "login" }
if ($Key -and -not (Test-Path $Key)) { throw "SSH key not found: $Key" }

$hostPart = $Server.Split("@")[-1]
if (-not $Site) {
    # sslip.io maps <ip>.sslip.io to <ip>, which lets Caddy get a real HTTPS certificate without a domain.
    $Site = if ($hostPart -match '^\d{1,3}(\.\d{1,3}){3}$') { "$hostPart.sslip.io" } else { $hostPart }
}
$sshOpts = @("-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30")
if ($Key) { $sshOpts += @("-i", $Key) }

$out = Join-Path $Root ".run\deploy"
New-Item -ItemType Directory -Force $out | Out-Null

Write-Host "== Packaging code"
$files = git -c core.quotepath=false ls-files -co --exclude-standard | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
[IO.File]::WriteAllLines((Join-Path $out "files.txt"), [string[]]$files)
Invoke-Native "packaging code" { tar -czf (Join-Path $out "code.tar.gz") -T (Join-Path $out "files.txt") }
Write-Host ("   {0} files, {1:N1} MB" -f $files.Count, ((Get-Item (Join-Path $out "code.tar.gz")).Length / 1MB))

$upload = @((Join-Path $out "code.tar.gz"), (Join-Path $Root "deploy\remote_setup.sh"))
if (-not $Public) { $upload += $auth }
if (-not $SkipData) {
    Write-Host "== Packaging data"
    $data = @("services/trade_intelligence/cache", "services/policy_stance/data", "services/event_summarization/data/dyad_geopolitical_scores.csv")
    foreach ($item in $data) { if (-not (Test-Path $item)) { throw "Missing $item - see docs/DATA.md" } }
    Invoke-Native "packaging data" { tar -czf (Join-Path $out "data.tar.gz") @data }
    Write-Host ("   {0:N1} MB" -f ((Get-Item (Join-Path $out "data.tar.gz")).Length / 1MB))
    $upload += (Join-Path $out "data.tar.gz")
}

Write-Host "== Uploading to $Server"
Invoke-Native "ssh" { ssh @sshOpts $Server "mkdir -p ~/team128-upload" }
Invoke-Native "scp" { scp @sshOpts @upload "${Server}:team128-upload/" }

Write-Host "== Installing and starting on the server"
Invoke-Native "remote setup" { ssh @sshOpts $Server "bash ~/team128-upload/remote_setup.sh '$Site' $access" }

$url = if ($Site -like "http://*") { $Site } else { "https://$Site" }
Write-Host "`nDeployed: $url"
