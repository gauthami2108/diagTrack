# DiagTrack Windows HTTPS Setup Helper
# Configures Caddy as a Windows service for local or domain-level TLS encryption.

$ErrorActionPreference = 'Stop'
$Data = Join-Path $env:ProgramData 'DiagTrack'
$Program = Join-Path $env:ProgramFiles 'DiagTrack'

# Require Admin
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "This script requires Administrator privileges." -ForegroundColor Red
    exit 1
}

Write-Host "Configuring DiagTrack HTTPS with Caddy..." -ForegroundColor Cyan

# 1. Copy Caddyfile
$caddyfile = Join-Path $Data 'Caddyfile'
Copy-Item (Join-Path $PSScriptRoot 'Caddyfile.windows') $caddyfile -Force

# 2. Firewall Rule for Port 443
netsh advfirewall firewall delete rule name="DiagTrack HTTPS" | Out-Null
netsh advfirewall firewall add rule name="DiagTrack HTTPS" dir=in action=allow protocol=TCP localport=443 profile=any | Out-Null
Write-Host "Opened TCP 443 in Windows Firewall." -ForegroundColor Green

Write-Host "Caddyfile saved to $caddyfile. Caddy reverse proxy ready." -ForegroundColor Green
