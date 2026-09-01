# Build the tray control exe (separate, --noconsole). Copies it into the bundle.
# ASCII-only (PowerShell 5.1 reads .ps1 as cp1251 without BOM).
# Run:  powershell -ExecutionPolicy Bypass -File build-tray.ps1
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot
Set-Location $root

Write-Host "[tray] deps (pyinstaller, pystray, pillow)..." -ForegroundColor Cyan
py -3 -m pip install --quiet --upgrade pyinstaller pystray pillow 2>&1 | Out-Null

# logo.png -> logo.ico (иконка exe в проводнике); требует logo.png в корне
$iconArg = @()
if (Test-Path "logo.png") {
  py -3 -c "from PIL import Image; Image.open('logo.png').save('logo.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(256,256)])" 2>&1 | Out-Null
  if (Test-Path "logo.ico") { $iconArg = @("--icon", "logo.ico") }
}

Write-Host "[tray] building bsl-ls-tray.exe (onefile, noconsole)..." -ForegroundColor Cyan
py -3 -m PyInstaller --noconfirm --clean `
  --onefile --noconsole --name bsl-ls-tray `
  @iconArg `
  --collect-all pystray `
  --collect-all PIL `
  tray\tray.py 2>&1 | Out-Null

if ($LASTEXITCODE -ne 0) { Write-Host "[tray] FAILED (exit $LASTEXITCODE)" -ForegroundColor Red; exit 1 }
$exe = Join-Path $root "dist\bsl-ls-tray.exe"
if (-not (Test-Path $exe)) { Write-Host "[tray] exe not produced" -ForegroundColor Red; exit 1 }

$bundle = Join-Path $root "dist\bsl-ls-mcp"
if (Test-Path (Join-Path $bundle "bsl-ls-mcp.exe")) {
  Copy-Item $exe $bundle -Force
  Write-Host "[tray] copied into bundle: $bundle\bsl-ls-tray.exe" -ForegroundColor Green
}
Write-Host "[tray] DONE: $exe"
Write-Host "Tray controls the NSSM service (status light + start/stop/reindex/logs)."
Write-Host "Run it (after the service is installed):  $bundle\bsl-ls-tray.exe"
