# Build native bsl-ls-mcp bundle (PyInstaller onedir) - no Docker.
# Result: dist\bsl-ls-mcp\ = exe + Python deps + server\jar + run.cmd.
# Portable JRE is added separately (see tail).
# ASCII-only on purpose: Windows PowerShell 5.1 reads .ps1 as cp1251 without BOM.
# ErrorActionPreference=Continue on purpose: PyInstaller/pip log progress to stderr,
# which is NOT an error; we gate on $LASTEXITCODE / Test-Path instead.
#
# Run:  powershell -ExecutionPolicy Bypass -File .\build.ps1
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
Set-Location $root

$bundle = Join-Path $root "dist\bsl-ls-mcp"
# Preserve manually-added extras (downloaded once) across rebuilds: PyInstaller wipes dist\.
$cache = Join-Path $root ".bundle-extras"
$extras = @("jre", "nssm.exe", "bsl-ls-tray.exe")
# RECOVERY: if a PREVIOUS build died AFTER move-to-cache but BEFORE restore, extras are
# left in the cache and missing from the bundle. Restore them from the cache first, else
# the Remove-Item $cache below wipes them for good (this is how jre+nssm were lost once).
if (Test-Path $cache) {
  New-Item -ItemType Directory -Force $bundle | Out-Null
  foreach ($x in $extras) {
    $c = Join-Path $cache $x; $b = Join-Path $bundle $x
    if ((Test-Path $c) -and -not (Test-Path $b)) { Move-Item $c $b -Force }
  }
}
Remove-Item $cache -Recurse -Force -EA SilentlyContinue
New-Item -ItemType Directory -Force $cache | Out-Null
foreach ($x in $extras) {
  $p = Join-Path $bundle $x
  if (Test-Path $p) { Move-Item $p (Join-Path $cache $x) -Force }
}

Write-Host "[build] PyInstaller install..." -ForegroundColor Cyan
# PIN the version, not --upgrade: a past --upgrade left TWO dist-info dirs (6.21 + 6.22)
# in one site-packages -> importlib.metadata.version('pyinstaller')=None -> the pywintypes
# hook crashed on 'Version(None)'. Pin a known-good version.
py -3 -m pip install --quiet "pyinstaller==6.22.2" 2>&1 | Out-Null

Write-Host "[build] running PyInstaller (onedir)..." -ForegroundColor Cyan
py -3 -m PyInstaller --noconfirm --clean `
  --onedir --name bsl-ls-mcp `
  --paths src `
  --collect-submodules mcp.server `
  --collect-submodules mcp.client `
  --collect-data mcp `
  --collect-all httpx `
  --collect-all httpcore `
  --collect-all pydantic `
  --collect-all pydantic_core `
  --collect-all uvicorn `
  --collect-all starlette `
  --collect-all anyio `
  --collect-all sse_starlette `
  --hidden-import mcp.server.sse `
  --hidden-import mcp.server.streamable_http `
  scripts\run_mcp.py 2>&1 | Out-Null

function Restore-Extras {  # move extras from cache back into the bundle (on any exit)
  New-Item -ItemType Directory -Force $bundle | Out-Null
  foreach ($x in $extras) {
    $c = Join-Path $cache $x
    if (Test-Path $c) { Move-Item $c (Join-Path $bundle $x) -Force }
  }
}
if ($LASTEXITCODE -ne 0) {
  Restore-Extras   # do NOT leave extras in the cache on failure, else the next build wipes them
  Write-Host "[build] PyInstaller FAILED (exit $LASTEXITCODE) - extras restored" -ForegroundColor Red
  exit 1
}

if (-not (Test-Path (Join-Path $bundle "bsl-ls-mcp.exe"))) {
  Write-Host "[build] exe not produced" -ForegroundColor Red; exit 1
}

Write-Host "[build] assembling bundle layout..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force (Join-Path $bundle "server") | Out-Null
# Vendor jar (BSL Language Server, LGPL-3.0) is NOT committed to this repo - it is
# downloaded from the upstream release on first build and cached in server\ for reuse.
$jarVer = "0.29.0"
# Pinned SHA256 of bsl-language-server-0.29.0-exec.jar (upstream GitHub release).
$jarSha256 = "D6FA9AD638BA51855E260B88AD1F8CE4E602385845A4EE43600D148F779BCF0B"
$srcJar = "server\bsl-language-server-$jarVer-exec.jar"
if ((-not (Test-Path $srcJar)) -or ((Get-Item $srcJar).Length -lt 1MB)) {
  Write-Host "[build] downloading BSL Language Server $jarVer (upstream release)..." -ForegroundColor Cyan
  New-Item -ItemType Directory -Force "server" | Out-Null
  $url = "https://github.com/1c-syntax/bsl-language-server/releases/download/v$jarVer/bsl-language-server-$jarVer-exec.jar"
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  try { Invoke-WebRequest -Uri $url -OutFile $srcJar -UseBasicParsing }
  catch { Write-Host "[build] FAIL: cannot download jar from $url" -ForegroundColor Red; exit 1 }
  if ((Get-Item $srcJar).Length -lt 1MB) {
    Write-Host "[build] FAIL: downloaded jar is suspiciously small (<1MB)" -ForegroundColor Red; exit 1
  }
}
# Verify integrity of the cached/downloaded jar (upstream could be tampered/mirror-swapped).
$jarGot = (Get-FileHash $srcJar -Algorithm SHA256).Hash
if ($jarGot -ne $jarSha256) {
  Write-Host "[build] FAIL: jar SHA256 mismatch (expected $jarSha256, got $jarGot)" -ForegroundColor Red
  Write-Host "        delete '$srcJar' to force a clean re-download." -ForegroundColor Red
  exit 1
}
Copy-Item $srcJar (Join-Path $bundle "server\") -Force
Copy-Item "run.cmd" $bundle -Force
if (Test-Path "install-service.ps1") { Copy-Item "install-service.ps1" $bundle -Force }
# restore preserved extras (jre, nssm.exe)
foreach ($x in @("jre", "nssm.exe", "bsl-ls-tray.exe")) {
  $c = Join-Path $cache $x
  if (Test-Path $c) { Move-Item $c (Join-Path $bundle $x) -Force }
}
Remove-Item $cache -Recurse -Force -EA SilentlyContinue

Write-Host ""
Write-Host "[build] DONE: $bundle" -ForegroundColor Green
Write-Host "For a machine WITHOUT Java: put portable Temurin JRE 21 into $bundle\jre\"
Write-Host "  (https://adoptium.net/temurin/releases/?version=21, Windows x64 JRE .zip,"
Write-Host "   so that $bundle\jre\bin\java.exe exists). run.cmd picks it up automatically;"
Write-Host "   otherwise run.cmd falls back to system 'java' (need 17+)."
Write-Host "Verify:  $bundle\run.cmd --selftest     (prints [selftest] OK)"
Write-Host "Daemon:  $bundle\run.cmd                (streamable-http on :8081/mcp)"
