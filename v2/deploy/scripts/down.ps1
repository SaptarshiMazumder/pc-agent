# Production only. OFF removes compute and the ALB, preserving saved data.
param(
    [ValidateSet('production')][string]$Environment = 'production',
    [switch]$Full # Kept for compatibility; every OFF is now a full compute shutdown.
)
$ErrorActionPreference = 'Stop'
& "$PSScriptRoot/power.ps1" off
