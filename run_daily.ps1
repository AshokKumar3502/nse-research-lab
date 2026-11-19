<#
  Daily schedule for the NSE research system. Paper research only: nothing here places an order.

  .\run_daily.ps1 -Phase morning   # 09:31 IST: today's intraday ranks, then the monitor
  .\run_daily.ps1 -Phase evening   # 18:45 IST: data -> lab -> minute bars -> swing plan -> monitor
  .\run_daily.ps1 -Phase monthly   # first weekend of the month: retrain and re-validate the models

  Logs: data\logs\<phase>-YYYY-MM-DD.log
#>
param([Parameter(Mandatory = $true)][ValidateSet("morning", "evening", "monthly")][string]$Phase)

$ErrorActionPreference = "Continue"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$py = Join-Path $here ".venv\Scripts\python.exe"
$logDir = Join-Path $here "data\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ("{0}-{1}.log" -f $Phase, (Get-Date -Format "yyyy-MM-dd"))
$env:PYTHONIOENCODING = "utf-8"

function Invoke-Step([string]$name, [string[]]$pyArgs) {
    "`n=== $name  $(Get-Date -Format 'HH:mm:ss') ===" | Out-File -Append -Encoding utf8 $log
    & $py @pyArgs *>> $log
    "exit $LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
    return $LASTEXITCODE
}

function Publish-Site {
    # rebuild docs/ from the latest picks and reports, then push only docs/ to GitHub Pages
    Invoke-Step "site build" @("-m", "site_build") | Out-Null
    "`n=== publish site  $(Get-Date -Format 'HH:mm:ss') ===" | Out-File -Append -Encoding utf8 $log
    git -C $here add docs *>> $log
    git -C $here diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        git -C $here commit -m ("Site update: {0} {1}" -f $Phase, (Get-Date -Format "yyyy-MM-dd HH:mm")) *>> $log
        git -C $here push origin main *>> $log
        "push exit $LASTEXITCODE" | Out-File -Append -Encoding utf8 $log
    } else {
        "site unchanged" | Out-File -Append -Encoding utf8 $log
    }
}

if ($Phase -eq "monthly" -and (Get-Date).Day -gt 7) {
    "monthly: runs on the first Saturday of the month only" | Out-File -Append -Encoding utf8 $log
    exit 0
}

if ((Get-Date).DayOfWeek -in @("Saturday", "Sunday") -and $Phase -ne "monthly") {
    "weekend: nothing to do" | Out-File -Append -Encoding utf8 $log
    exit 0
}

switch ($Phase) {
    "morning" {
        Invoke-Step "intraday live" @("-m", "intraday", "live") | Out-Null
        Invoke-Step "monitor" @("-m", "monitor", "run") | Out-Null
        Invoke-Step "paper trades" @("-m", "papertrade") | Out-Null
        Publish-Site
    }
    "evening" {
        $rc = Invoke-Step "lab nightly (sync, checks, test, paper)" @("-m", "lab", "nightly", "--fetch")
        $since = (Get-Date -Day 1).AddMonths(-1).ToString("yyyy-MM-dd")
        Invoke-Step "minute bars" @("-m", "warehouse", "minute", "--universe", "fo", "--since", $since) | Out-Null
        if ($rc -eq 0) { Invoke-Step "swing scan" @("-m", "swing", "scan") | Out-Null }
        Invoke-Step "monitor" @("-m", "monitor", "run") | Out-Null
        Invoke-Step "paper trades" @("-m", "papertrade") | Out-Null
        Publish-Site
    }
    "monthly" {
        Invoke-Step "results filings (every universe stock)" @("-m", "warehouse", "results") | Out-Null
        Invoke-Step "point-in-time industries" @("-m", "swing", "industries") | Out-Null
        Invoke-Step "intraday validate" @("-m", "intraday", "validate", "--t", "09:30") | Out-Null
        Invoke-Step "swing validate h10" @("-m", "swing", "validate", "--hold", "10") | Out-Null
        Invoke-Step "lab refresh" @("-m", "lab", "refresh") | Out-Null
        Invoke-Step "lab test" @("-m", "lab", "test", "--force") | Out-Null
        Invoke-Step "monitor" @("-m", "monitor", "run") | Out-Null
        # hand the new models to the GitHub runner (state/), then publish
        git -C $here pull --rebase -X theirs *>> $log
        Invoke-Step "export to state/" @("-m", "cloud", "export") | Out-Null
        git -C $here add state *>> $log
        Publish-Site
    }
}
