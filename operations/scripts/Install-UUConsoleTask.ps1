#requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$Preview,
    [switch]$Elevated,
    [ValidatePattern('^S-1-5-21-[0-9-]+$')][string]$AllowedUserSid,
    [ValidatePattern('^[0-9a-f]{32}$')][string]$InstallId=([Guid]::NewGuid().ToString('N'))
)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $PSScriptRoot 'UUConsoleTask.psm1') -Force
$config=Get-Content -LiteralPath (Join-Path $root 'config\uu-console-task.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin=([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if(-not $AllowedUserSid) { $AllowedUserSid=$identity.User.Value }
$payloadRoot=[IO.Path]::GetFullPath((Join-Path $root $config.payloadRoot))
$evidenceRoot=[IO.Path]::GetFullPath((Join-Path $root $config.evidenceRoot))
foreach($path in @($payloadRoot,$evidenceRoot)) {
    if(-not $path.StartsWith($root.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Deployment must remain inside this project.' }
}
$sources=@('Set-UUConsoleMode.ps1','Start-UUConsoleTrial.ps1','Invoke-UUConsoleTrial.ps1','UUConsoleTask.psm1')
$files=@(foreach($name in $sources) {
    [pscustomobject]@{name=$name;path=(Join-Path (Join-Path $payloadRoot 'scripts') $name);sha256=(Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $name) -Algorithm SHA256).Hash}
})
if($Preview) {
    [ordered]@{result='UU_TASK_INSTALL_PREVIEW';administratorRequiredOnce=$true;allowedUserSid=$AllowedUserSid;payloadRoot=$payloadRoot;evidenceRoot=$evidenceRoot;tasks=$config.taskNames;automaticTriggers=$false;files=$files} | ConvertTo-Json -Depth 6
    exit 0
}
$receiptRoot=Join-Path $root 'artifacts\uu-console-task-install'
$receiptPath=Join-Path $receiptRoot ($InstallId+'.json')
if(-not $isAdmin) {
    if($Elevated) { throw 'UU_TASK_INSTALL_ADMIN_REQUIRED: the helper did not receive administrator authorization.' }
    Write-Host 'One-time setup requires Windows administrator approval. Daily launch will not request elevation.'
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Elevated -AllowedUserSid '+$AllowedUserSid+' -InstallId '+$InstallId
    $helper=Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -ArgumentList $arguments -WorkingDirectory $root -Verb RunAs -WindowStyle Hidden -PassThru
    if(-not $helper.WaitForExit(180000)) { throw 'UU_TASK_INSTALL_RESULT_UNKNOWN: setup may still be running. Check its receipt; do not automatically repeat.' }
    if(-not (Test-Path -LiteralPath $receiptPath)) { throw ('UU_TASK_INSTALL_NO_RECEIPT: helper exit='+$helper.ExitCode) }
    Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8
    exit $helper.ExitCode
}

function Protect-NewDirectory([string]$Path) {
    if(Test-Path -LiteralPath $Path) {
        $item=Get-Item -LiteralPath $Path
        if($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw ('Reparse point refused: '+$Path) }
        $existing=Get-Acl -LiteralPath $Path
        $owner=([Security.Principal.NTAccount]$existing.Owner).Translate([Security.Principal.SecurityIdentifier]).Value
        if($owner -notin @('S-1-5-32-544','S-1-5-18') -or -not $existing.AreAccessRulesProtected) { throw ('Existing directory is not a protected deployment: '+$Path) }
        foreach($rule in $existing.Access) {
            $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
            if($rule.AccessControlType -ne 'Allow' -or $sid -notin @('S-1-5-32-544','S-1-5-18',$AllowedUserSid)) { throw ('Unexpected deployment ACL: '+$Path) }
            if($sid -eq $AllowedUserSid -and ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::Write)) { throw ('Ordinary-user write access refused: '+$Path) }
        }
        return
    }
    [void][IO.Directory]::CreateDirectory($Path)
    $acl=New-Object Security.AccessControl.DirectorySecurity
    $acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    $acl.SetAccessRuleProtection($true,$false)
    foreach($sid in @('S-1-5-18','S-1-5-32-544',$AllowedUserSid)) {
        $rights=if($sid -eq $AllowedUserSid){[Security.AccessControl.FileSystemRights]::ReadAndExecute}else{[Security.AccessControl.FileSystemRights]::FullControl}
        $rule=[Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.SecurityIdentifier]::new($sid),$rights,[Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',[Security.AccessControl.PropagationFlags]::None,[Security.AccessControl.AccessControlType]::Allow)
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

try {
    [void][IO.Directory]::CreateDirectory($receiptRoot)
    # Preflight is read-only and never attempts a service control or worker launch.
    $inspection=((& (Join-Path $PSScriptRoot 'Invoke-UUConsoleTrial.ps1') -Mode Inspect) -join [Environment]::NewLine) | ConvertFrom-Json
    if(-not $inspection.serviceHashMatchesInspectedCode) { throw 'UU version is not inspected. No tasks were installed.' }
    Protect-NewDirectory $payloadRoot
    Protect-NewDirectory $evidenceRoot
    $scriptRoot=Join-Path $payloadRoot 'scripts'
    [void][IO.Directory]::CreateDirectory($scriptRoot)
    foreach($file in $files) {
        if(Test-Path -LiteralPath $file.path) {
            if((Get-FileHash -LiteralPath $file.path).Hash -ine $file.sha256) { throw ('Existing payload differs; no overwrite: '+$file.path) }
        } else { Copy-Item -LiteralPath (Join-Path $PSScriptRoot $file.name) -Destination $file.path -ErrorAction Stop }
        if((Get-FileHash -LiteralPath $file.path).Hash -ine $file.sha256) { throw ('Deployment hash mismatch: '+$file.path) }
    }
    $scheduler=New-Object -ComObject Schedule.Service
    $scheduler.Connect()
    $folder=$scheduler.GetFolder('\')
    $sddl='O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;'+$AllowedUserSid+')'
    $tasks=@(foreach($action in @('Enable','Restore')) {
        $bootstrap=New-UUConsoleTaskBootstrap -Files $files -EntryScript (Join-Path $scriptRoot 'Set-UUConsoleMode.ps1') -EvidenceRoot $evidenceRoot -Action $action
        $definition=New-UUConsoleTaskDefinition -Scheduler $scheduler -Bootstrap $bootstrap
        $name=$config.taskNames.$action
        $existing=$null
        try { $existing=$folder.GetTask($name) }
        catch {
            if($_.Exception.HResult -ne -2147024894 -and $_.Exception.InnerException.HResult -ne -2147024894) { throw }
        }
        if($existing) {
            if($existing.Definition.Actions.Count -ne 1 -or $existing.Definition.Actions.Item(1).Arguments -cne $definition.Actions.Item(1).Arguments -or
                $existing.Definition.Actions.Item(1).Path -ine $definition.Actions.Item(1).Path -or $existing.Definition.Triggers.Count -ne 0 -or
                $existing.Definition.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $existing.Definition.Principal.LogonType -ne 5) {
                throw ('Existing task differs; no overwrite: '+$name)
            }
            $registered=$existing
        } else {
            $registered=$folder.RegisterTaskDefinition($name,$definition,2,'SYSTEM',$null,5,$sddl)
        }
        [pscustomobject]@{name=$name;definitionXml=$registered.Xml;securityDescriptor=$registered.GetSecurityDescriptor(7);state=$registered.State}
    })
    [ordered]@{result='UU_TASK_SETUP_OK';success=$true;installedAtUtc=[DateTime]::UtcNow.ToString('o');allowedUserSid=$AllowedUserSid;tasks=$tasks;files=$files;payloadRoot=$payloadRoot;evidenceRoot=$evidenceRoot;workersChanged=$false;automaticTriggers=$false} |
        ConvertTo-Json -Depth 8 | Out-File -LiteralPath $receiptPath -Encoding UTF8 -NoClobber
    Get-Content -LiteralPath $receiptPath -Raw -Encoding UTF8
} catch {
    if(-not (Test-Path -LiteralPath $receiptPath)) {
        [ordered]@{result='UU_TASK_SETUP_FAILED';success=$false;exception=$_.Exception.ToString();position=$_.InvocationInfo.PositionMessage} |
            ConvertTo-Json -Depth 4 | Out-File -LiteralPath $receiptPath -Encoding UTF8 -NoClobber
    }
    Write-Error $_ -ErrorAction Continue
    exit 1
}
