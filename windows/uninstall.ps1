# Removes the BraveCast scheduled task and firewall rules. Run as Administrator.
Stop-ScheduledTask -TaskName "BraveCast" -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName "BraveCast" -Confirm:$false -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName "BraveCast*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like '*BraveCast*receiver*server.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host "BraveCast removed."
