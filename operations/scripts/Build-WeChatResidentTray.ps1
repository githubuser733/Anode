param()
$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
$compiler='C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$directory=Join-Path $root ('work\wechat-resident-tray-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[Guid]::NewGuid().ToString('N').Substring(0,8))
[void][IO.Directory]::CreateDirectory($directory)
$exe=Join-Path $directory 'WeChatResidentTray.exe'
$source=Join-Path $root 'scripts\wechat_monitor\WeChatResidentTray.cs'
& $compiler /nologo /target:winexe /platform:x64 /codepage:65001 ('/out:'+$exe) /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:Microsoft.CSharp.dll $source
if($LASTEXITCODE -ne 0){throw 'Tray build failed; the running service was not changed.'}
$result=[pscustomobject]@{result='TRAY_BUILD_OK';executable=$exe;sha256=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant();source=$source;sourceSha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()}
[IO.File]::WriteAllText((Join-Path $directory 'build.json'),($result|ConvertTo-Json))
$result|ConvertTo-Json -Compress
