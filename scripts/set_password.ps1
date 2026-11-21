# Choose the shared login for the deployed briefing UI.
#
# Only a bcrypt hash is kept (deploy\.auth, gitignored). The password itself never
# leaves this machine and is never written to disk.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "No .venv found. Run scripts\setup_local.ps1 first." }

$user = Read-Host "Username for the briefing UI (letters, digits, . _ -)"
if ($user -notmatch '^[A-Za-z0-9._-]{3,32}$') { throw "Username must be 3-32 characters: letters, digits, . _ -" }
$secure = Read-Host "Password (at least 10 characters)" -AsSecureString
$confirm = Read-Host "Repeat the password" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$bstr2 = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($confirm)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    $again = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr2)
    if ($plain -ne $again) { throw "The two passwords do not match." }
    if ($plain.Length -lt 10) { throw "Use at least 10 characters." }

    & $Python -m pip install -q bcrypt | Out-Null
    # Hand the password to Python in an environment variable of this process only.
    # Piping it would not work: Windows PowerShell prepends a byte-order mark and
    # re-encodes non-ASCII text, so the hash would not match what a browser sends.
    $env:T128_PW = $plain
    $hash = & $Python -c "import os, bcrypt; print(bcrypt.hashpw(os.environ['T128_PW'].encode('utf-8'), bcrypt.gensalt(12)).decode())"
    if ($LASTEXITCODE -ne 0 -or -not $hash.StartsWith('$2')) { throw "Hashing failed." }
} finally {
    Remove-Item Env:T128_PW -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr2)
    $plain = $null; $again = $null
}

[IO.File]::WriteAllText((Join-Path $Root "deploy\.auth"), "$user $hash`n")
Write-Host "Saved the login for '$user' (bcrypt hash only) to deploy\.auth"
Write-Host "Next: scripts\deploy.ps1 -Server <user>@<server-ip> -Key <path-to-key>"
