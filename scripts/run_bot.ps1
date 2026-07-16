param(
    [Parameter(Mandatory = $false)]
    [string]$PythonExecutable = "python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DataDirectory = Join-Path $ProjectRoot "data"
$LogPath = Join-Path $DataDirectory "bot.task.log"

New-Item -ItemType Directory -Force -Path $DataDirectory | Out-Null
Set-Location -LiteralPath $ProjectRoot

try {
    & $PythonExecutable -m eslee_bot *>> $LogPath
    exit $LASTEXITCODE
}
catch {
    "$(Get-Date -Format o) | ERROR | Scheduled bot launcher failed: $($_.Exception.Message)" |
        Out-File -LiteralPath $LogPath -Append -Encoding utf8
    exit 1
}
