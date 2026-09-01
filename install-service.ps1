# Install bsl-ls-mcp as a Windows service via NSSM.
# NSSM is a generic service shim: it IS the service and supervises the exe
# (auto-restart, no console, survives logoff). Java is killed with the parent
# via the wrapper's Job Object, so service stop never leaves orphan java.
# ASCII-only on purpose (PowerShell 5.1 reads .ps1 as cp1251 without BOM).
#
# MUST run elevated (Administrator).
#   powershell -ExecutionPolicy Bypass -File install-service.ps1 -Workspace "E:\1c\src\cf"
param(
  [string]$ServiceName = "bsl-ls-mcp",
  [string]$Workspace   = "C:\1c\src\cf",
  [int]   $Port        = 8081,
  [string]$Xmx         = "14g"
)
$ErrorActionPreference = "Continue"
$root = $PSScriptRoot

# locate the bundle (exe) - either dist\bsl-ls-mcp\ or this folder
$bundle = $null
foreach ($c in @((Join-Path $root "dist\bsl-ls-mcp"), $root)) {
  if (Test-Path (Join-Path $c "bsl-ls-mcp.exe")) { $bundle = $c; break }
}
if (-not $bundle) { Write-Host "bsl-ls-mcp.exe not found (build first: build.ps1)" -ForegroundColor Red; exit 1 }
$exe = Join-Path $bundle "bsl-ls-mcp.exe"
$jar = Join-Path $bundle "server\bsl-language-server-0.29.0-exec.jar"
$jre = Join-Path $bundle "jre\bin\java.exe"
# Bundled JRE preferred (self-contained). Иначе — АБСОЛЮТНЫЙ путь системной java, а не
# просто "java": служба бежит от LocalSystem, чей PATH может не содержать java.
if (Test-Path $jre) {
  $java = $jre
} else {
  $sys = (Get-Command java -EA SilentlyContinue).Source
  $java = if ($sys) { $sys } else { "java" }
  if (-not (Test-Path $jre)) { Write-Host "[svc] bundled JRE отсутствует -> системная java: $java" -ForegroundColor Yellow }
}

# admin check
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $admin) { Write-Host "Run elevated (Administrator)." -ForegroundColor Red; exit 1 }

# locate or download NSSM
$nssm = Join-Path $bundle "nssm.exe"
if (-not (Test-Path $nssm)) {
  Write-Host "[svc] downloading NSSM..." -ForegroundColor Cyan
  $zip = Join-Path $env:TEMP "nssm.zip"; $ext = Join-Path $env:TEMP "nssm-ext"
  Invoke-WebRequest "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zip
  Remove-Item $ext -Recurse -Force -EA SilentlyContinue
  Expand-Archive $zip $ext -Force
  Copy-Item (Get-ChildItem $ext -Recurse -Filter nssm.exe | Where-Object { $_.FullName -match "win64" } | Select-Object -First 1).FullName $nssm -Force
}

Write-Host "[svc] installing service '$ServiceName'..." -ForegroundColor Cyan
& $nssm install $ServiceName "$exe"
& $nssm set $ServiceName AppParameters "--transport streamable-http --port $Port"
& $nssm set $ServiceName AppDirectory "$bundle"
& $nssm set $ServiceName AppEnvironmentExtra "BSL_WORKSPACE=$Workspace" "BSL_XMX=$Xmx" "BSL_LS_JAR=$jar" "BSL_JAVA=$java" "BSL_MCP_HOST=127.0.0.1" "BSL_MCP_PORT=$Port"
& $nssm set $ServiceName AppStdout "$bundle\service.out.log"
& $nssm set $ServiceName AppStderr "$bundle\service.err.log"
& $nssm set $ServiceName AppRotateFiles 1
& $nssm set $ServiceName AppExit Default Restart        # auto-restart on crash
& $nssm set $ServiceName AppThrottle 5000
& $nssm set $ServiceName Start SERVICE_AUTO_START        # start at boot
& $nssm start $ServiceName

Write-Host ""
Write-Host "[svc] DONE: service '$ServiceName' -> http://127.0.0.1:$Port/mcp" -ForegroundColor Green
Write-Host "  workspace=$Workspace  java=$java  Xmx=$Xmx"
Write-Host "Manage:  sc query $ServiceName | nssm restart $ServiceName | nssm stop $ServiceName"
Write-Host "Remove:  nssm stop $ServiceName ; nssm remove $ServiceName confirm"
Write-Host "Logs:    $bundle\service.err.log  (Uvicorn) ;  server.log (java)"
Write-Host "First agent call indexes ~1.5 min; then warm. Needs ~$Xmx free RAM."
