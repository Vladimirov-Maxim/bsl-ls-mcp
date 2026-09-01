@echo off
chcp 65001 >nul
rem Run bsl-ls-mcp as a native daemon (no Docker, no Python/Java install).
rem Sits at the bundle root next to the exe, server\ and (optional) jre\.
rem Args pass through:  run.cmd --selftest   /   run.cmd --transport sse ...
rem ASCII-only on purpose: cmd.exe mis-parses non-ASCII .cmd files.

set "BSL_LS_JAR=%~dp0server\bsl-language-server-0.29.0-exec.jar"

rem Java: bundled JRE if present (target machine without Java), else system java (need 17+).
if exist "%~dp0jre\bin\java.exe" (
  set "BSL_JAVA=%~dp0jre\bin\java.exe"
) else (
  set "BSL_JAVA=java"
)

rem 1C sources: path to the working copy ON THIS MACHINE.
rem Pre-set BSL_WORKSPACE before calling to override the default below.
if "%BSL_WORKSPACE%"=="" set "BSL_WORKSPACE=C:\1c\src\cf"

rem IMPORTANT: 14g, not 12g - corpus index live-set ~12.6 GB; 12g -> endless GC.
set "BSL_XMX=14g"
set "BSL_MCP_TRANSPORT=streamable-http"
set "BSL_MCP_HOST=127.0.0.1"
set "BSL_MCP_PORT=8081"
set "BSL_SERVER_LOG=%~dp0server.log"

"%~dp0bsl-ls-mcp.exe" %*
