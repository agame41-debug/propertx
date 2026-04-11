# Rentero — spuštění webového rozhraní na Windows (ekvivalent start_web.sh)
#Requires -Version 5.1
$ErrorActionPreference = "Stop"

$PROJECT_DIR = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location -LiteralPath $PROJECT_DIR

# Bez toho padá run_web.py na UnicodeEncodeError (české znaky v print) při přesměrování stdout do souboru (cp125x)
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$PORT = 8000
# 127.0.0.1 — na Windows často "localhost" míří na ::1, zatímco uvicorn naslouchá jen na IPv4
$URL = "http://127.0.0.1:$PORT"
$cacheDir = Join-Path $PROJECT_DIR "cache"
$LOG = Join-Path $cacheDir "web.log"
$ERR_LOG = Join-Path $cacheDir "web.err"

New-Item -ItemType Directory -Force -Path $cacheDir | Out-Null

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return }
    Get-Content -LiteralPath $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { return }
        if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }
        $eq = $line.IndexOf("=")
        if ($eq -lt 1) { return }
        $key = $line.Substring(0, $eq).Trim()
        $val = $line.Substring($eq + 1).Trim()
        if (($val.StartsWith('"') -and $val.EndsWith('"')) -or ($val.StartsWith("'") -and $val.EndsWith("'"))) {
            $val = $val.Substring(1, $val.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($key, $val, "Process")
    }
}

Import-DotEnv (Join-Path $PROJECT_DIR ".env")

$needFallback =
    [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable("RENTERO_SESSION_SECRET", "Process")) -or
    [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable("RENTERO_USERNAME", "Process")) -or
    [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable("RENTERO_PASSWORD", "Process"))

if ($needFallback) {
    [Environment]::SetEnvironmentVariable("RENTERO_ALLOW_INSECURE_DEFAULTS", "1", "Process")
    Write-Host "Používám localhost-only fallback auth/session defaults (RENTERO_ALLOW_INSECURE_DEFAULTS=1)."
}

function Stop-ListenerOnPort {
    param([int]$Port)
    netstat -ano | ForEach-Object {
        $line = $_
        if ($line -notmatch "LISTENING") { return }
        if ($line -notmatch ":$Port\s") { return }
        $parts = @($line -split "\s+" | Where-Object { $_ -ne "" })
        if ($parts.Count -lt 1) { return }
        $last = $parts[-1]
        if ($last -match "^\d+$") {
            $procId = [int]$last
            if ($procId -gt 4) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            }
        }
    }
    Start-Sleep -Milliseconds 500
}

Stop-ListenerOnPort -Port $PORT

function Resolve-PythonExe {
    # py -3.14 píše na stderr při neexistující verzi — při $ErrorActionPreference Stop to jinak ukončí celý skript
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            foreach ($ver in @("-3.14", "-3.13", "-3.12", "-3.11", "-3")) {
                $out = & py $ver -c "import sys; print(sys.executable)" 2>&1
                if ($LASTEXITCODE -eq 0 -and $out) {
                    $line = if ($out -is [System.Array]) { $out[-1] } else { $out }
                    $exe = "$line".Trim()
                    if ($exe -and (Test-Path -LiteralPath $exe)) { return $exe }
                }
            }
            # py bez čísla verze (výchozí)
            $out = & py -c "import sys; print(sys.executable)" 2>&1
            if ($LASTEXITCODE -eq 0 -and $out) {
                $line = if ($out -is [System.Array]) { $out[-1] } else { $out }
                $exe = "$line".Trim()
                if ($exe -and (Test-Path -LiteralPath $exe)) { return $exe }
            }
        }
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        return $null
    } finally {
        $ErrorActionPreference = $prevEap
    }
}

$pythonExe = Resolve-PythonExe
if (-not $pythonExe) {
    Write-Host "CHYBA: Nenalezen Python. Nainstalujte Python 3 z https://www.python.org/downloads/ a zaškrtněte ""Add to PATH"", nebo použijte ""py"" launcher."
    exit 1
}

Write-Host "Python: $pythonExe"
Write-Host "Instalace závislostí (pip)..."
# Bez roury — jinak se v PowerShellu kazí $LASTEXITCODE
& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r (Join-Path $PROJECT_DIR "requirements.txt")
$pipDeps = $LASTEXITCODE
if ($pipDeps -ne 0) {
    Write-Host "CHYBA: pip install selhal (exit $pipDeps)."
    exit 1
}

Remove-Item -LiteralPath $LOG -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $ERR_LOG -Force -ErrorAction SilentlyContinue

Write-Host "Spouštím Rentero na $URL ..."

# Default to detached background mode so the web server survives after the
# launcher window closes. Set RENTERO_SHOW_LOGS=1 when you explicitly want
# an attached console session for debugging.
$showLogs = [Environment]::GetEnvironmentVariable("RENTERO_SHOW_LOGS", "Process") -eq "1"

if ($showLogs) {
    # Start process in a new console window to see logs
    Write-Host "Spouštím se zobrazenými logy..."
    $proc = Start-Process -FilePath $pythonExe `
        -ArgumentList @("-u", "run_web.py", "--port", "$PORT") `
        -WorkingDirectory $PROJECT_DIR `
        -PassThru `
        -NoNewWindow
    
    Write-Host "Rentero běží (PID $($proc.Id))"
    Write-Host "Zastavit: Stop-Process -Id $($proc.Id) -Force"
} else {
    # Start process hidden with log redirection (old behavior)
    $proc = Start-Process -FilePath $pythonExe `
        -ArgumentList @("-u", "run_web.py", "--port", "$PORT") `
        -WorkingDirectory $PROJECT_DIR `
        -RedirectStandardOutput $LOG `
        -RedirectStandardError $ERR_LOG `
        -WindowStyle Hidden `
        -PassThru

    $started = $false
    for ($i = 0; $i -lt 16; $i++) {
        if ($proc.HasExited) { break }
        try {
            $r = Invoke-WebRequest -Uri "$URL/login" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { $started = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 500
    }

    if (-not $started) {
        Write-Host "Rentero se nespustilo korektně."
        Write-Host "Poslední logy z $LOG :"
        if (Test-Path $LOG) { Get-Content $LOG -Tail 80 -ErrorAction SilentlyContinue }
        Write-Host "--- stderr ($ERR_LOG) ---"
        if (Test-Path $ERR_LOG) { Get-Content $ERR_LOG -Tail 40 -ErrorAction SilentlyContinue }
        if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
        exit 1
    }

    Start-Process $URL
    Write-Host "Rentero běží (PID $($proc.Id))"
    Write-Host "Logy: $LOG (stdout), $ERR_LOG (stderr)"
    Write-Host "Zastavit: Stop-Process -Id $($proc.Id) -Force"

    Write-Host ""
    Write-Host "Server běží na pozadí. Okno můžete zavřít. Ukončení: Stop-Process -Id $($proc.Id) -Force nebo Správce úloh (PID $($proc.Id))."
}
