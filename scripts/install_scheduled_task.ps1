param(
    [Parameter(Mandatory = $false)]
    [string]$TaskName = "eslee Discord Bot",

    [Parameter(Mandatory = $false)]
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunnerPath = Join-Path $PSScriptRoot "run_bot.ps1"

if (-not $PythonExecutable) {
    $PythonExecutable = (Get-Command python -ErrorAction Stop).Source
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot ".env"))) {
    throw ".env file is required before installing the scheduled task."
}

$PowerShellExecutable = (Get-Command powershell.exe -ErrorAction Stop).Source
$ActionArguments = @(
    "-NoProfile"
    "-ExecutionPolicy Bypass"
    "-WindowStyle Hidden"
    "-File `"$RunnerPath`""
    "-PythonExecutable `"$PythonExecutable`""
) -join " "

$ActionParameters = @{
    Execute = $PowerShellExecutable
    Argument = $ActionArguments
    WorkingDirectory = $ProjectRoot
}
$Action = New-ScheduledTaskAction @ActionParameters
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$SettingsParameters = @{
    AllowStartIfOnBatteries = $true
    DontStopIfGoingOnBatteries = $true
    StartWhenAvailable = $true
    RestartCount = 999
    RestartInterval = (New-TimeSpan -Minutes 1)
    ExecutionTimeLimit = [TimeSpan]::Zero
    MultipleInstances = "IgnoreNew"
}
$Settings = New-ScheduledTaskSettingsSet @SettingsParameters
$CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$PrincipalParameters = @{
    UserId = $CurrentUser
    LogonType = "Interactive"
    RunLevel = "Limited"
}
$Principal = New-ScheduledTaskPrincipal @PrincipalParameters

$RegisterParameters = @{
    TaskName = $TaskName
    Description = "Runs eslee Discord Bot independently and restarts it after failures."
    Action = $Action
    Trigger = $Trigger
    Settings = $Settings
    Principal = $Principal
    Force = $true
}
Register-ScheduledTask @RegisterParameters | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Output "Scheduled task '$TaskName' was installed and started."
