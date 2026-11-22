# Keep one review off the site's "Hear what people think" box (or show it again).
#
#   scripts\hide_review.ps1 -Server ubuntu@203.0.113.10 -Key C:\keys\team128.pem -Id 7
#   scripts\hide_review.ps1 -Server ... -Key ... -Id 7 -Show
#
# Review ids are in the CSV from scripts\get_reviews.ps1. Hidden reviews are kept,
# and also left out of the average rating.
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Key = "",
    [Parameter(Mandatory = $true)][int]$Id,
    [switch]$Show
)
$ErrorActionPreference = "Stop"
$sshOpts = @("-o", "StrictHostKeyChecking=accept-new")
if ($Key) { $sshOpts += @("-i", $Key) }
$action = if ($Show) { "unhide" } else { "hide" }
ssh @sshOpts $Server "cd ~/team128 && sudo docker compose -f docker-compose.prod.yml exec -T orchestrator python -m app.reviews $action $Id"
if ($LASTEXITCODE -ne 0) { throw "Could not $action review $Id (exit $LASTEXITCODE)." }
