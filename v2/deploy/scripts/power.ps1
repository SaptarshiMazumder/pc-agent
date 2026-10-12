param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('on', 'off', 'status', 'keep-on', 'automatic', 'capture', 'validate')]
    [string]$Action,
    [string]$Region = 'ap-northeast-1',
    [string]$Stack = 'agentd-staging'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path "$PSScriptRoot/../../..").Path
$python = Join-Path $repo '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
& $python "$PSScriptRoot/../aws_power/cli.py" $Action --region $Region --stack $Stack
if ($LASTEXITCODE -ne 0) { throw "Power $Action failed. Check power status before retrying." }
