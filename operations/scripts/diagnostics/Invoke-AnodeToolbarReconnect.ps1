param([Parameter(Mandatory=$true)][int]$DaemonPid,[Parameter(Mandatory=$true)][long]$WindowHandle,[switch]$InspectOnly)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ToolbarFocusCheck {
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool GetCursorPos(out Point point);
 [StructLayout(LayoutKind.Sequential)] public struct Point { public int X; public int Y; }
}
'@
$foreground=[ToolbarFocusCheck]::GetForegroundWindow()
$point=[ToolbarFocusCheck+Point]::new();[void][ToolbarFocusCheck]::GetCursorPos([ref]$point)
$window=[Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
if($null -eq $window -or $window.Current.ProcessId -ne $DaemonPid -or $window.Current.Name -cne 'Anode seat viewer'){throw 'ANODE_WINDOW_IDENTITY_MISMATCH'}
$toolbarName=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty,'Seat controls')
$bars=$window.FindAll([Windows.Automation.TreeScope]::Children,$toolbarName)
if($bars.Count -ne 1){throw 'ANODE_TOOLBAR_NOT_UNIQUE'}
$buttonName=[Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty,'Reconnect')
$buttons=$bars[0].FindAll([Windows.Automation.TreeScope]::Children,$buttonName)
if($buttons.Count -ne 1){throw 'RECONNECT_NOT_UNIQUE'}
$button=$buttons[0]
if($button.Current.ProcessId -ne $DaemonPid -or -not $button.Current.IsEnabled -or $button.Current.IsOffscreen){throw 'RECONNECT_NOT_ACTIONABLE'}
if(-not $InspectOnly){
 $pattern=[Windows.Automation.InvokePattern]$button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
 $pattern.Invoke()
}
$after=[ToolbarFocusCheck+Point]::new();[void][ToolbarFocusCheck]::GetCursorPos([ref]$after)
[pscustomobject]@{ok=$true;invoked=(-not $InspectOnly);pid=$DaemonPid;matches=$buttons.Count;name=$button.Current.Name;focus_preserved=($foreground -eq [ToolbarFocusCheck]::GetForegroundWindow());cursor_preserved=($point.X -eq $after.X -and $point.Y -eq $after.Y)}|ConvertTo-Json -Compress
