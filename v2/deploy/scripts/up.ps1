# Production only. Start through the private AWS power controller.
param([ValidateSet('production')][string]$Environment = 'production')
$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/power.ps1" on
