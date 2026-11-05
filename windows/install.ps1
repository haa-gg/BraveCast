<#
  BraveCast - Windows installer
  Run from an elevated (Administrator) PowerShell:
      powershell -ExecutionPolicy Bypass -File windows\install.ps1

  What it does:
    1. Checks for Python 3 and Brave
    2. Opens the Windows Firewall for the receiver (TCP 8099) and DIAL discovery (UDP 1900)
    3. Registers a "BraveCast" scheduled task that starts at logon in YOUR desktop session
       (it must NOT be a Windows service - the kiosk window needs an interactive session)
#>
$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Write-Error "Please run this script as Administrator."; exit 1 }

$repo   = Split-Path -Parent $PSScriptRoot
$server = Join-Path $repo "receiver\server.py"
if (-not (Test-Path $server)) { Write-Error "Cannot find $server"; exit 1 }

# --- Python ---
$py = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command pyw.exe -ErrorAction SilentlyContinue }
if (-not $py) { Write-Error "Python 3 not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')."; exit 1 }
Write-Host "Python  : $($py.Source)"

# --- Brave ---
$brave = @(
    "$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe",
    "${env:ProgramFiles(x86)}\BraveSoftware\Brave-Browser\Application\brave.exe",
    "$env:LocalAppData\BraveSoftware\Brave-Browser\Application\brave.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $brave) { Write-Warning "Brave not found. Install it from https://brave.com/download/ before casting." }
else { Write-Host "Brave   : $brave" }

# --- Firewall ---
Get-NetFirewallRule -DisplayName "BraveCast*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "BraveCast HTTP (TCP 8099)" -Direction Inbound -Protocol TCP -LocalPort 8099 -Action Allow -Profile Private,Domain | Out-Null
New-NetFirewallRule -DisplayName "BraveCast DIAL/SSDP (UDP 1900)" -Direction Inbound -Protocol UDP -LocalPort 1900 -Action Allow -Profile Private,Domain | Out-Null
Write-Host "Firewall: rules added (Private/Domain networks)"
$net = Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq "Public" }
if ($net) { Write-Warning "Your network '$($net.Name)' is set to Public, so phones won't discover this PC. Set it to Private: Settings > Network & internet > (your network) > Network profile type." }

# --- Scheduled task (starts at logon, runs in the user's interactive session) ---
$user    = "$env:USERDOMAIN\$env:USERNAME"
$action  = New-ScheduledTaskAction -Execute $py.Source -Argument "`"$server`"" -WorkingDirectory (Split-Path $server)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Unregister-ScheduledTask -TaskName "BraveCast" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "BraveCast" -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
Start-ScheduledTask -TaskName "BraveCast"
Write-Host "Task    : 'BraveCast' registered and started"

Start-Sleep -Seconds 3
try {
    $r = Invoke-RestMethod "http://localhost:8099/api/ping"
    Write-Host "`nBraveCast is running as '$($r.name)' at $($r.localIp):8099" -ForegroundColor Green
    Write-Host "Remote: http://$($r.localIp):8099/"
} catch {
    Write-Warning "Receiver didn't answer on :8099 yet. Try: python receiver\server.py  (to see errors)"
}
