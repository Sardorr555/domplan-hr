$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$data = Join-Path $root 'data'
$binary = Join-Path $data 'tools\cloudflared.exe'
if (-not (Test-Path -LiteralPath $binary)) { throw 'Cloudflare client not found in data\tools. Restore it from the official Cloudflare release.' }
$python = (Get-Command python -ErrorAction Stop).Source
if (Test-Path -LiteralPath (Join-Path $root '.venv\Scripts\python.exe')) { $python = Join-Path $root '.venv\Scripts\python.exe' }
$page = $null
try { $page = Invoke-WebRequest 'http://127.0.0.1:8000/' -UseBasicParsing -TimeoutSec 3 } catch {}
if ($page -and -not $page.Content.Contains('DOMPLAN')) { throw 'Unexpected service on port 8000; refusing to expose it.' }
if (-not $page) {
    $server = Start-Process -FilePath $python -ArgumentList 'run.py' -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $data 'public-server.log') -RedirectStandardError (Join-Path $data 'public-server-error.log') -PassThru
    $server.Id | Set-Content -LiteralPath (Join-Path $data 'public-server.pid')
    $ready = $false
    for ($i=0;$i -lt 40;$i++) { Start-Sleep -Milliseconds 300; try { Invoke-WebRequest 'http://127.0.0.1:8000/' -UseBasicParsing -TimeoutSec 2 | Out-Null; $ready=$true; break } catch {} }
    if (-not $ready) { throw 'Public demo server failed to start. See data\public-server-error.log.' }
}
$pidFile = Join-Path $data 'public-tunnel.pid'
$urlFile = Join-Path $data 'public-url.txt'
if ((Test-Path -LiteralPath $pidFile) -and (Test-Path -LiteralPath $urlFile)) {
    $tunnelProcess = Get-Process -Id ([int](Get-Content -LiteralPath $pidFile)) -ErrorAction SilentlyContinue
    if ($tunnelProcess -and $tunnelProcess.ProcessName -eq 'cloudflared') { Write-Output 'Public demo is already running:'; Get-Content -LiteralPath $urlFile; exit 0 }
}
$out = Join-Path $data 'public-tunnel.log'
$err = Join-Path $data 'public-tunnel-error.log'
$tunnel = Start-Process -FilePath $binary -ArgumentList @('tunnel','--no-autoupdate','--url','http://127.0.0.1:8000','--protocol','http2') -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
$tunnel.Id | Set-Content -LiteralPath $pidFile
for ($i=0;$i -lt 80;$i++) {
    Start-Sleep -Milliseconds 500
    $log = Get-Content -LiteralPath $err -Raw -ErrorAction SilentlyContinue
    $match = [regex]::Match([string]$log,'https://[a-z0-9-]+\.trycloudflare\.com')
    if ($match.Success) { $match.Value | Set-Content -LiteralPath $urlFile; Write-Output 'Send this link to other people:'; Write-Output $match.Value; Write-Output 'The link works while this computer and the background processes stay running.'; exit 0 }
}
throw 'Tunnel URL not ready. See data\public-tunnel-error.log.'

