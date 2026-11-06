# Removes the BraveCast scheduled task, firewall rules, and installed receiver files. Run as Administrator.
Stop-ScheduledTask -TaskName "BraveCast" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "BraveCast" -Confirm:$false -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName "BraveCast*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like '*BraveCast*server.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$installDir = Join-Path $env:LocalAppData "BraveCast"
if (Test-Path $installDir) { Remove-Item -Path $installDir -Recurse -Force -ErrorAction SilentlyContinue }
Write-Host "BraveCast removed."
