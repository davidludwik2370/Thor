# Native Windows bootstrap; does not change execution policy or require Python.
$ErrorActionPreference = 'Stop'
$forward = [System.Collections.Generic.List[string]]::new()
$version = (Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'release/version')).Trim()
$bundle = ''; $checksum = ''; $base = $env:THOR_RELEASE_BASE
for ($i = 0; $i -lt $args.Count; $i++) {
    $arg = $args[$i]
    if ($arg -in @('--help', '-h')) {
        Write-Output 'Usage: .\thor-install --vscode --profile Thor [--bundle FILE] [--release-base URL] [--prefix DIR] [--colors|--no-colors] [--path] [--dry-run]'
        Write-Output 'Existing settings and keybindings are preserved. See docs/installing.md for all options.'
        exit 0
    }
    if ($arg -in @('--bundle','--sha256','--release-base','--version')) {
        $i++; if ($i -ge $args.Count) { throw "$arg requires a value" }
        switch ($arg) { '--bundle' {$bundle=$args[$i]} '--sha256' {$checksum=$args[$i]} '--release-base' {$base=$args[$i]} '--version' {$version=$args[$i]} }
    } else { $forward.Add($arg) }
}
if ($version -notmatch '^[a-zA-Z0-9._-]+$') { throw 'Invalid version' }
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This release bootstrap supports native Windows x64 only.' }
$temporary = Join-Path ([IO.Path]::GetTempPath()) ('thor-bootstrap-' + [Guid]::NewGuid())
[IO.Directory]::CreateDirectory($temporary) | Out-Null
try {
    if (!$bundle) {
        if (!$base) {
            $origin = (& git -C $PSScriptRoot remote get-url origin 2>$null)
            if ($origin -match '^(https://github.com/|git@github.com:)([^/]+/[^/]+?)(\.git)?$') { $base="https://github.com/$($Matches[2])/releases/download/v$version" }
            else { throw 'No release location configured. Use --bundle FILE or --release-base URL until releases are published.' }
        }
        if ($base -notmatch '^https://' -and $base -notmatch '^http://(localhost|127\.0\.0\.1):') { throw 'Release URLs must use HTTPS (localhost HTTP is supported for testing).' }
        $name = "thor-$version-windows-x86_64.tar.gz"
        $bundle = Join-Path $temporary $name
        Invoke-WebRequest -UseBasicParsing -Uri "$base/$name" -OutFile $bundle
        if (!$checksum) { Invoke-WebRequest -UseBasicParsing -Uri "$base/$name.sha256" -OutFile "$bundle.sha256" }
    }
    $bundle = (Resolve-Path -LiteralPath $bundle).Path
    if (!$checksum) { $checksum=((Get-Content -Raw -LiteralPath "$bundle.sha256").Trim() -split '\s+')[0] }
    if ($checksum -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid SHA-256' }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $bundle).Hash -ne $checksum) { throw 'Bundle SHA-256 mismatch; nothing installed.' }
    # Use the binary stream: Windows PowerShell's > redirection corrupts binaries.
    $info=[Diagnostics.ProcessStartInfo]::new();$info.FileName='tar.exe'
    if ($bundle.Contains('"')) { throw 'Invalid archive path' }
    $info.Arguments='-xOzf "'+$bundle+'" thor-install.exe'
    $info.UseShellExecute=$false;$info.RedirectStandardOutput=$true
    $process=[Diagnostics.Process]::Start($info)
    $native=Join-Path $temporary 'thor-install.exe'
    $stream=[IO.File]::Create($native)
    try {$process.StandardOutput.BaseStream.CopyTo($stream)} finally {$stream.Dispose()}
    $process.WaitForExit();if ($process.ExitCode -ne 0) {throw 'Could not extract the native installer'}
    & $native --bundle $bundle --sha256 $checksum @forward
    if ($LASTEXITCODE -ne 0) {throw "Native installer failed ($LASTEXITCODE)"}
} finally {Remove-Item -LiteralPath $temporary -Recurse -Force}
