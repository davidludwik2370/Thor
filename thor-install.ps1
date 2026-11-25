# Native Windows bootstrap; does not change execution policy or require Python.
$ErrorActionPreference = 'Stop'
$checkoutRoot=$PSScriptRoot
$ThorSourcePython=$null
function InvokeThorDownload($uri,$file) {
    $ProgressPreference='SilentlyContinue'
    Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $file
}
$forward = [System.Collections.Generic.List[string]]::new()
$version = (Get-Content -Raw -LiteralPath (Join-Path $checkoutRoot 'release/version')).Trim()
$checkoutVersion=$version
$bundle = ''; $checksum = ''; $base = $env:THOR_RELEASE_BASE
$sourceBuild=$false; $noDeps=$false; $maintenance=''; $prefix=''; $dry=$false
$reuseOdin=$false
for ($i = 0; $i -lt $args.Count; $i++) {
    $arg = $args[$i]
    if ($arg -in @('--help', '-h')) {
        Write-Output 'Usage: .\thor-install --vscode --profile Thor [--bundle FILE] [--release-base URL] [--prefix DIR] [--colors|--no-colors] [--path] [--from-source] [--uninstall|--doctor] [--no-install-deps] [--dry-run]'
        Write-Output 'Existing settings and keybindings are preserved. See docs/installing.md for all options.'
        exit 0
    }
    if ($arg -eq '--from-source') {$sourceBuild=$true; continue}
    if ($arg -eq '--odin') {$reuseOdin=$true}
    if ($arg -eq '--no-install-deps') {$noDeps=$true}
    if ($arg -in @('--doctor','--uninstall')) {$maintenance=$arg}
    if ($arg -eq '--dry-run') {$dry=$true}
    if ($arg -eq '--prefix') {if ($i+1 -ge $args.Count) {throw '--prefix requires a value'}; $prefix=$args[$i+1]}
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
    if ($maintenance) {
        if ($sourceBuild -or $bundle -or $checksum) {throw 'Maintenance cannot use --from-source, --bundle or --sha256'}
        if (!$prefix) {$prefix=Join-Path $env:LOCALAPPDATA 'Thor'}
        $installed=Join-Path $prefix 'bin/thor-install.cmd'
        # Resolve the registered native executable, never execute a source launcher recursively.
        $recordPath=Join-Path $env:LOCALAPPDATA 'Thor/installation.json'
        if (!(Test-Path $recordPath)) {if($maintenance -eq '--uninstall'){Write-Output 'Thor is not installed.'; exit 0};throw 'Thor is not installed'}
        $record=Get-Content -Raw -LiteralPath $recordPath | ConvertFrom-Json
        if ($record.managed_by -ne 'thor-install') {throw 'Unrecognized installation record'}
        $native=Join-Path $temporary 'thor-install.exe'
        Copy-Item -LiteralPath (Join-Path $record.root 'thor-install.exe') -Destination $native
        & $native @forward
        if ($LASTEXITCODE -ne 0) {throw "Maintenance failed ($LASTEXITCODE)"}
        exit 0
    }
    if ($sourceBuild) {
        if ($bundle -or $checksum) {throw '--from-source cannot use --bundle/--sha256'}
        if ($version -ne $checkoutVersion) {throw 'A source build uses the checked-out version. Check out the requested release first.'}
        if ($dry) {Write-Output 'Source install plan: pinned tools/Odin, Thor/TLS/editor release builds, inventoried install and verification. No changes made.'; exit 0}
        if (!$noDeps) {. (Join-Path $checkoutRoot 'install/prerequisites.ps1') -SourceBuild -ReuseOdin:$reuseOdin}
        $python=if($ThorSourcePython){$ThorSourcePython}else{'python'}
        & $python (Join-Path $checkoutRoot 'install/from_source.py') @forward
        if ($LASTEXITCODE -ne 0) {throw "Source build failed ($LASTEXITCODE)"}
        exit 0
    }
    if (!$bundle) {
        if (!$base) {
            $origin = (& git -C $checkoutRoot remote get-url origin 2>$null)
            if ($origin -match '^(https://github.com/|git@github.com:)([^/]+/[^/]+?)(\.git)?$') { $base="https://github.com/$($Matches[2])/releases/download/v$version" }
            else { throw 'No release location configured. Use --bundle FILE or --release-base URL until releases are published.' }
        }
        if ($base -notmatch '^https://' -and $base -notmatch '^http://(localhost|127\.0\.0\.1):') { throw 'Release URLs must use HTTPS (localhost HTTP is supported for testing).' }
        $name = "thor-$version-windows-x86_64.tar.gz"
        $bundle = Join-Path $temporary $name
        try {InvokeThorDownload "$base/$name" $bundle}
        catch {
            if (!$_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 404) {throw}
            if ($version -ne $checkoutVersion) {throw 'No binary for this version. Check out that release before building it from source.'}
            if ($dry) {Write-Output 'Source fallback would be used; no changes made.'; exit 0}
            Write-Output 'No release binary for this host; building the checked-out source.'
            if (!$noDeps) {. (Join-Path $checkoutRoot 'install/prerequisites.ps1') -SourceBuild -ReuseOdin:$reuseOdin}
            $python=if($ThorSourcePython){$ThorSourcePython}else{'python'}
            & $python (Join-Path $checkoutRoot 'install/from_source.py') @forward
            if ($LASTEXITCODE -ne 0) {throw "Source build failed ($LASTEXITCODE)"}
            exit 0
        }
        if (!$checksum) { InvokeThorDownload "$base/$name.sha256" "$bundle.sha256" }
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
