# Native Windows host prerequisites. Keep elevation limited to Microsoft's
# installer; Thor, editor configuration, and source builds run as the user.
param([switch]$SourceBuild, [switch]$VSCode, [switch]$DryRun)
$ErrorActionPreference='Stop'
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') {throw 'Odin currently supports native Windows x64 hosts only.'}
if ($DryRun) {Write-Output "Prerequisites: Windows x64; source build=$SourceBuild; VS Code=$VSCode. Reuse existing installations."; exit 0}
function Download($uri,$file) {
    # Windows PowerShell's per-chunk progress rendering can dominate large
    # downloads. Keep this preference local to the download, not the caller.
    $ProgressPreference='SilentlyContinue'
    Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $file
}
function SignedInstaller($file) {
    $signature=Get-AuthenticodeSignature -LiteralPath $file
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {throw "Microsoft installer signature verification failed: $file"}
}
function InvokeChecked($file, $arguments) {
    & $file @arguments
    if ($LASTEXITCODE -ne 0) {throw "$file failed ($LASTEXITCODE)"}
}
function MergeProcessPaths($values) {
    $seen=[System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $paths=[System.Collections.Generic.List[string]]::new()
    foreach($value in $values) {foreach($part in ($value -split ';')) {
        if ($part -and $seen.Add($part)) {$paths.Add($part)}
    }}
    return ($paths -join ';')
}
function InvokeVcVars($dev,$tail) {
    # cmd/vcvars has an 8191-character expansion limit. Do not feed it the
    # caller's potentially long PATH or append its entries repeatedly.
    $original=$env:PATH
    try {
        $env:PATH="$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\WindowsPowerShell\v1.0;$env:SystemRoot\System32\Wbem"
        $lines=& cmd.exe /d /s /c "`"$dev`" >nul && $tail"
        if ($LASTEXITCODE -ne 0) {throw 'Could not initialize the MSVC build environment'}
        return $lines
    } finally {$env:PATH=$original}
}
function FindSourcePython {
    $candidates=[System.Collections.Generic.List[string]]::new()
    $command=Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command -and $command.Source -notmatch '\\Microsoft\\WindowsApps\\') {$candidates.Add($command.Source)}
    foreach($key in @('HKCU:\Software\Python\PythonCore','HKLM:\Software\Python\PythonCore')) {
        foreach($version in Get-ChildItem $key -ErrorAction SilentlyContinue) {
            $install=Get-Item (Join-Path $version.PSPath 'InstallPath') -ErrorAction SilentlyContinue
            if($install -and $install.GetValue('')){$candidates.Add((Join-Path $install.GetValue('') 'python.exe'))}
        }
    }
    foreach($file in Get-ChildItem -Path "$env:LOCALAPPDATA\Programs\Python\Python*\python.exe","$env:ProgramFiles\Python*\python.exe" -ErrorAction SilentlyContinue) {$candidates.Add($file.FullName)}
    foreach($candidate in $candidates) {
        if(Test-Path -LiteralPath $candidate) {
            & $candidate -c 'import sys; sys.exit(sys.version_info < (3,11))' 2>$null
            if($LASTEXITCODE -eq 0){return $candidate}
        }
    }
    return $null
}
$work=Join-Path ([IO.Path]::GetTempPath()) ('thor-prerequisites-'+[Guid]::NewGuid())
[IO.Directory]::CreateDirectory($work)|Out-Null
try {
    $vswhere=Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    $vs=''
    if (Test-Path $vswhere) {$vs=(& $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)}
    function TestBuildTools($directory) {
        if (!$directory) {return $false}
        $file=Join-Path $work 'check.c'
        [IO.File]::WriteAllText($file,'#include <windows.h>'+[Environment]::NewLine+'int main(void) {return 0;}')
        $dev=Join-Path $directory 'VC\Auxiliary\Build\vcvars64.bat'
        $old=Get-Location
        try {
            Set-Location $work
            $cl=Get-Command cl.exe -ErrorAction SilentlyContinue
            if ($cl -and $cl.Source.StartsWith($directory,[StringComparison]::OrdinalIgnoreCase) -and $env:INCLUDE -and $env:LIB) {
                & cl.exe /nologo check.c /Fe:check.exe *> $null
                if ($LASTEXITCODE -ne 0) {return $false}
            } else {
                try {InvokeVcVars $dev 'cl /nologo check.c /Fe:check.exe >nul 2>&1' | Out-Null}
                catch {return $false}
            }
            & (Join-Path $work 'check.exe')
            return $LASTEXITCODE -eq 0
        } finally {Set-Location $old}
    }
    if ($vs -and !(TestBuildTools $vs)) {$vs=''}
    if (!$vs) {
        $setup=Join-Path $work 'vs_buildtools.exe'
        Download 'https://aka.ms/vs/17/release/vs_buildtools.exe' $setup
        SignedInstaller $setup
        Write-Output 'Installing MSVC and Windows SDK. Windows may request administrator approval.'
        $process=Start-Process -FilePath $setup -Verb RunAs -ArgumentList '--passive --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended' -Wait -PassThru
        if ($process.ExitCode -in @(3010,1641)) {throw 'Microsoft Build Tools requires a restart. Restart Windows and rerun thor-install to continue.'}
        if ($process.ExitCode -ne 0) {throw "Microsoft Build Tools installation failed ($($process.ExitCode))"}
        $vs=(& $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)
        if (!$vs) {throw 'MSVC was not found after installation.'}
    }
    if (!(TestBuildTools $vs)) {throw 'MSVC/Windows SDK was installed but cannot compile, link and run a native program. Restart if required, then rerun.'}
    Write-Output "PASS: MSVC installation at $vs"
    if ($SourceBuild) {
        # winget is an acquisition helper, not a prerequisite for normal installs.
        # Official Python/Rust/Node/Git packages have different update mechanisms;
        # use their registered package identities and never upgrade existing tools.
        $tools=@(@('python','Python.Python.3.12'),@('node','OpenJS.NodeJS.LTS'),@('git','Git.Git'),@('cargo','Rustlang.Rustup'),@('git-lfs','GitHub.GitLFS'))
        foreach($tool in $tools) {
            $available=if($tool[0] -eq 'python'){FindSourcePython}else{Get-Command $tool[0] -ErrorAction SilentlyContinue}
            if (!$available) {
                if (!(Get-Command winget -ErrorAction SilentlyContinue)) {throw "Source builds need $($tool[0]). Install it or install Microsoft App Installer (winget), then rerun. Binary installation does not need winget."}
                $arguments=@('install','--id',$tool[1],'--exact','--silent','--accept-package-agreements','--accept-source-agreements')
                if($tool[0] -eq 'cargo') {$arguments+=@('--override','-y --profile minimal --no-modify-path')}
                InvokeChecked 'winget' $arguments
            }
        }
        # Registry refresh must retain process-only entries (CI tool caches,
        # activated environments and user shell tools).
        $env:PATH=MergeProcessPaths @($env:PATH,[Environment]::GetEnvironmentVariable('Path','User'),[Environment]::GetEnvironmentVariable('Path','Machine'))
        $ThorSourcePython=FindSourcePython
        if(!$ThorSourcePython){throw 'Python 3.11+ was not found after installation. Restart the terminal and retry.'}
        # Export the selected MSVC environment only into the source-build child.
        # The caller imports this snapshot rather than changing global PATH.
        $dev=Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
        $variables=InvokeVcVars $dev 'set'
        $original=$env:PATH
        foreach($line in $variables) {if($line -match '^([^=]+)=(.*)$') {[Environment]::SetEnvironmentVariable($Matches[1],$Matches[2],'Process')}}
        $env:PATH=MergeProcessPaths @($env:PATH,$original)
    }
    if ($VSCode -and !(Get-Command code -ErrorAction SilentlyContinue) -and !(Test-Path "$env:LOCALAPPDATA\Programs\Microsoft VS Code\bin\code.cmd") -and !(Test-Path "$env:ProgramFiles\Microsoft VS Code\bin\code.cmd")) {
        $setup=Join-Path $work 'vscode-setup.exe'
        Download 'https://update.code.visualstudio.com/latest/win32-x64-user/stable' $setup
        SignedInstaller $setup
        $process=Start-Process -FilePath $setup -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /MERGETASKS=!runcode /COPILOTCLI=none' -Wait -PassThru
        if ($process.ExitCode -ne 0) {throw "VS Code installation failed ($($process.ExitCode))"}
        Write-Output 'PASS: VS Code installed'
    }
} finally {Remove-Item -LiteralPath $work -Recurse -Force}
