# Download every visitor review from the server as a CSV.
#
#   scripts\get_reviews.ps1 -Server ubuntu@203.0.113.10 -Key C:\keys\team128.pem
#   scripts\get_reviews.ps1 -Server ... -Key ... -Out reviews.csv
#
# Reviews are stored by the orchestrator (orchestrator/app/reviews.py) in a Docker
# volume on the server; the site only shows their count and average, so this is
# how the team reads them.
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Key = "",
    [string]$Out = "reviews.csv"
)
$ErrorActionPreference = "Stop"
$sshOpts = @("-o", "StrictHostKeyChecking=accept-new")
if ($Key) { $sshOpts += @("-i", $Key) }

$csv = ssh @sshOpts $Server "cd ~/team128 && sudo docker compose -f docker-compose.prod.yml exec -T orchestrator python -m app.reviews export"
if ($LASTEXITCODE -ne 0) { throw "Could not read the reviews (exit $LASTEXITCODE)." }
[IO.File]::WriteAllLines((Join-Path (Get-Location) $Out), [string[]]$csv)
$count = [Math]::Max(0, @($csv).Count - 1)
Write-Host "Saved $count review(s) to $Out"
