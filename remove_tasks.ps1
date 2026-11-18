$ErrorActionPreference = "Stop"
Unregister-ScheduledTask -TaskName PhantomPunch_In, PhantomPunch_Out -Confirm:$false
Write-Output "PhantomPunch_In / PhantomPunch_Out removed"