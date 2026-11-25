param(
    [string[]]$Stage = @('preflight', 'build', 'smoke'),
    [string]$Odin = 'C:\Users\david\AppData\Local\Programs\Odin\dev-2026-11\odin.exe',
    [string]$Python = 'C:\Users\david\AppData\Local\Python\pythoncore-3.15-64\python.exe'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
if (!(Test-Path -LiteralPath $Odin)) { throw "Odin executable not found: $Odin" }
if (!(Test-Path -LiteralPath $Python)) { throw "Python executable not found: $Python" }
$prepend = @((Join-Path $root 'bin'), (Split-Path -Parent $Odin), (Split-Path -Parent $Python), "$env:USERPROFILE\.cargo\bin")
$remaining = @($env:PATH -split ';' | Where-Object { $_ -notin $prepend })
$env:PATH = ($prepend + $remaining) -join ';'
$developer = 'C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat'
if ((Test-Path -LiteralPath $developer) -and $env:VSCMD_ARG_TGT_ARCH -ne 'x64') {
    # Import MSVC's environment into this process only; do not change the machine.
    $configuration = & cmd.exe /d /c ('call "' + $developer + '" -arch=x64 -host_arch=x64 >nul && set')
    if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the MSVC build environment.' }
    foreach ($line in $configuration) {
        if ($line -match '^([^=]+)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
        }
    }
}
$env:THOR_ROOT = $root
$env:THOR_ODIN = $Odin
$env:THOR_TEST_COMPILER = Join-Path $root 'rust\target\release\thor-rust.exe'
$env:THOR_TEST_PREBUILT = '1'
$env:THOR_TEST_TLS_PREBUILT = '1'
$env:PYTHONUTF8 = '1'
$env:CARGO_BUILD_JOBS = '2'
foreach ($name in $Stage) {
    & $Python -u (Join-Path $PSScriptRoot 'windows_validation.py') --stage $name
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
