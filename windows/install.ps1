<#
  BraveCast - Windows Installer (Supports GUI Double-Click & CLI)
  Double-click "Install BraveCast (Windows).bat", or run from Administrator PowerShell:
      powershell -ExecutionPolicy Bypass -File windows\install.ps1
#>
param(
    [switch]$Gui,
    [string]$Name = ""
)

$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" $(if ($Gui) { '-Gui' })"
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction SilentlyContinue

function Show-Box($msg, $title = "BraveCast Installer", $icon = "Information") {
    if ($Gui) {
        [System.Windows.Forms.MessageBox]::Show($msg, $title, "OK", $icon) | Out-Null
    } else {
        Write-Host "$title : $msg"
    }
}

$repo = Split-Path -Parent $PSScriptRoot
$srcReceiver = Join-Path $repo "receiver"
$installDir = Join-Path $env:LocalAppData "BraveCast\receiver"

# 1. Ask for TV Name in GUI mode
if ($Gui -and [string]::IsNullOrWhiteSpace($Name)) {
    $Name = [Microsoft.VisualBasic.Interaction]::InputBox(
        "Welcome to BraveCast! 🦁📺`n`nWhat would you like to name this TV receiver?",
        "Install BraveCast",
        "Living Room TV (PC)"
    )
    if ([string]::IsNullOrWhiteSpace($Name)) { exit 0 }
}
if ([string]::IsNullOrWhiteSpace($Name)) { $Name = "Living Room TV (PC)" }

# 2. Check / Auto-install Python 3 via winget if missing
$py = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command pyw.exe -ErrorAction SilentlyContinue }
if (-not $py) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "==> Installing Python 3 via winget..."
        & winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
        $py = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    }
}
if (-not $py) {
    Show-Box "Python 3 was not found. Please install Python 3 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH') and re-run." "BraveCast Setup" "Warning"
    exit 1
}

# 3. Check / Auto-install Brave Browser via winget if missing
$brave = @(
    "$env:ProgramFiles\BraveSoftware\Brave-Browser\Application\brave.exe",
    "${env:ProgramFiles(x86)}\BraveSoftware\Brave-Browser\Application\brave.exe",
    "$env:LocalAppData\BraveSoftware\Brave-Browser\Application\brave.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $brave) {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "==> Installing Brave Browser via winget..."
        & winget install --id Brave.Brave -e --accept-package-agreements --accept-source-agreements
    } else {
        Write-Warning "Brave not found. Install it from https://brave.com/download/ before casting."
    }
}

# 4. Copy receiver to %LocalAppData%\BraveCast\receiver so deleting Downloads won't break it
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item -Path "$srcReceiver\*" -Destination $installDir -Recurse -Force
$server = Join-Path $installDir "server.py"

# 5. Set network profile to Private (so phones can discover DIAL/HTTP on LAN) & open Firewall
Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq "Public" } | ForEach-Object {
    try { Set-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -NetworkCategory Private -ErrorAction SilentlyContinue } catch {}
}
Get-NetFirewallRule -DisplayName "BraveCast*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "BraveCast HTTP (TCP 8099)" -Direction Inbound -Protocol TCP -LocalPort 8099 -Action Allow -Profile Private,Domain | Out-Null
New-NetFirewallRule -DisplayName "BraveCast DIAL/SSDP (UDP 1900)" -Direction Inbound -Protocol UDP -LocalPort 1900 -Action Allow -Profile Private,Domain | Out-Null

# 6. Persist BRAVECAST_NAME environment variable & register Scheduled Task
[System.Environment]::SetEnvironmentVariable("BRAVECAST_NAME", $Name, "User")
$env:BRAVECAST_NAME = $Name

Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like '*BraveCast*server.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$user      = "$env:USERDOMAIN\$env:USERNAME"
$action    = New-ScheduledTaskAction -Execute $py.Source -Argument "`"$server`"" -WorkingDirectory $installDir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
               -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Unregister-ScheduledTask -TaskName "BraveCast" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "BraveCast" -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
Start-ScheduledTask -TaskName "BraveCast"

Start-Sleep -Seconds 3
try {
    $r = Invoke-RestMethod "http://localhost:8099/api/ping"
    $remoteUrl = "http://$($r.localIp):8099/"
    Write-Host "`nBraveCast is running as '$Name' at $remoteUrl" -ForegroundColor Green
    if ($Gui) {
        Start-Process $remoteUrl
        Show-Box "BraveCast is now running in the background and will start automatically when you log in!`n`nCast Target Name: $Name`nPhone Jam Remote: $remoteUrl" "BraveCast Installed! 🎉"
    }
} catch {
    Show-Box "BraveCast task was registered, but port 8099 did not respond yet." "BraveCast Setup" "Warning"
}
