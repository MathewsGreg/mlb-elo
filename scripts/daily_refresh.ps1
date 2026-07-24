# Daily refresh: fetch last night's results, log today's predictions, publish
# the dashboard. Run by a Windows Task Scheduler job (see README) - not meant
# to be run twice in the same day (predict.py/fetch_odds.py are write-once
# per game, so a second run just no-ops on already-logged games).

$Repo = "C:\Users\Diggs\Dropbox\PC\Documents\Claude\mlb_elo"
$Python = "C:\Users\Diggs\venvs\mlb_elo\Scripts\python.exe"
$Git = "C:\Program Files\Git\cmd\git.exe"

$LogDir = "$env:LOCALAPPDATA\mlb_elo\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("refresh_{0}.log" -f (Get-Date -Format "yyyy-MM-dd_HHmmss"))

function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Output $line | Tee-Object -FilePath $LogFile -Append
}

function Run-Step($label, $scriptArgs) {
    Log "--- $label ---"
    & $Python @scriptArgs *>> $LogFile
    if ($LASTEXITCODE -ne 0) {
        Log "FAILED: $label (exit $LASTEXITCODE) - aborting, nothing will be committed/pushed."
        exit 1
    }
}

Set-Location $Repo
Log "Starting daily refresh in $Repo"

Run-Step "fetch_history.py 2026"      @("scripts/fetch_history.py", "2026")
Run-Step "backfill_pitchers.py"       @("scripts/backfill_pitchers.py")
Run-Step "fetch_pitcher_stats.py"     @("scripts/fetch_pitcher_stats.py")
Run-Step "predict.py"                 @("scripts/predict.py")
Run-Step "fetch_odds.py"              @("scripts/fetch_odds.py")
Run-Step "export_dashboard.py"        @("scripts/export_dashboard.py")

$changes = & $Git status --porcelain docs/data.json
if (-not $changes) {
    Log "No change in docs/data.json - nothing to commit. Done."
    exit 0
}

Log "Committing and pushing docs/data.json"
& $Git add docs/data.json *>> $LogFile
$dateStr = Get-Date -Format "yyyy-MM-dd"
& $Git commit -m "Automated daily refresh: $dateStr" *>> $LogFile
if ($LASTEXITCODE -ne 0) {
    Log "FAILED: git commit (exit $LASTEXITCODE)"
    exit 1
}
& $Git push *>> $LogFile
if ($LASTEXITCODE -ne 0) {
    Log "FAILED: git push (exit $LASTEXITCODE) - commit succeeded locally but did NOT reach GitHub."
    exit 1
}

Log "Done - pushed successfully."
