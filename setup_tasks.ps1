$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv python missing: $py" }
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew `
    -RestartCount 0 -WakeToRun:$false
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$tIn = New-ScheduledTaskTrigger -Daily -At "13:00";  $tIn.RandomDelay = "PT1H20M"
$tOut = New-ScheduledTaskTrigger -Daily -At "22:00"; $tOut.RandomDelay = "PT2H"
$aIn = New-ScheduledTaskAction -Execute $py -Argument "main.py signin" -WorkingDirectory $root
$aOut = New-ScheduledTaskAction -Execute $py -Argument "main.py signout" -WorkingDirectory $root
Register-ScheduledTask -TaskName "PhantomPunch_In" -Action $aIn -Trigger $tIn -Settings $settings -Principal $principal -Force
Register-ScheduledTask -TaskName "PhantomPunch_Out" -Action $aOut -Trigger $tOut -Settings $settings -Principal $principal -Force
Get-ScheduledTask -TaskName PhantomPunch_In, PhantomPunch_Out | Format-List TaskName, State