#Requires -Version 5.1
<#
.SYNOPSIS
    PII Ghost-Hunter -- one-command demo launcher.

.DESCRIPTION
    1.  Kills processes occupying local app ports (FastAPI :8000, Vite :5173).
    2.  Loads / creates .env; auto-generates JWT_SECRET_KEY if blank.
    3.  Verifies Docker is running.
    4.  Creates a Python virtualenv and installs API-only dependencies
        (excludes torch, pyspark, mlflow -- those are Docker-only).
    5.  Installs Node modules in dashboard/ if missing.
    6.  Starts infrastructure via Docker Compose:
          Zookeeper, Kafka, Schema Registry, PostgreSQL, Redis,
          Airflow (webserver + scheduler).
    7.  Waits for all core services to report healthy.
    8.  Runs Alembic migrations against the pii_hunter database.
    9.  Opens a PowerShell window for the FastAPI server (:8000).
    10. Opens a PowerShell window for the Vite dashboard (:5173).
    11. Waits for the API /health endpoint, then opens the browser.
    12. Prints a full URL summary with demo credentials.

.PARAMETER WithInference
    Also build and start the PII Inference service in Docker (:8001).
    Warning: first build downloads ~3 GB of ML dependencies (10-20 min).
    Subsequent runs use Docker layer cache and take ~1 min.

.PARAMETER SkipBuild
    Skip docker compose build for the inference service.
    Only relevant when -WithInference is set.

.EXAMPLE
    .\demo.ps1

.EXAMPLE
    .\demo.ps1 -WithInference

.EXAMPLE
    .\demo.ps1 -WithInference -SkipBuild
#>

param(
    [switch]$WithInference,
    [switch]$SkipBuild
)

# ---- Bootstrap --------------------------------------------------------------

$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location $RepoRoot

# ---- Color helpers ----------------------------------------------------------

function Write-Step { param([string]$Msg)
    Write-Host ""
    Write-Host "  >> $Msg" -ForegroundColor Cyan
}
function Write-Ok   { param([string]$Msg) Write-Host "  OK  $Msg" -ForegroundColor Green  }
function Write-Warn { param([string]$Msg) Write-Host "  !!  $Msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg) Write-Host "  XX  $Msg" -ForegroundColor Red    }

function Exit-Script {
    param([string]$Reason)
    Write-Fail $Reason
    Read-Host "`n  Press Enter to exit"
    exit 1
}

# ---- Banner -----------------------------------------------------------------

Write-Host ""
Write-Host "  =======================================================" -ForegroundColor DarkCyan
Write-Host "    PII Ghost-Hunter  --  Privacy Risk Inventory Demo    " -ForegroundColor Cyan
Write-Host "  =======================================================" -ForegroundColor DarkCyan
Write-Host ""
if ($WithInference) {
    Write-Host "  Mode: full stack  (API + Dashboard + Airflow + ML Inference)" -ForegroundColor DarkGray
} else {
    Write-Host "  Mode: dashboard demo  (API + Dashboard + Airflow)"            -ForegroundColor DarkGray
    Write-Host "  Tip : add -WithInference to also start the ML service"        -ForegroundColor DarkGray
}
Write-Host ""

# =============================================================================
# STEP 1 -- Kill processes on local app ports
# =============================================================================
#
# Only frees ports 8000 and 5173 -- the ones bound by local processes.
# Docker infrastructure ports (5432, 6379, 9092, 8080, 8081) are left alone
# so a warm Docker stack is not disrupted between demo runs.

$AppPorts = @(8000, 5173)

Write-Step "Clearing local app ports ($($AppPorts -join ', '))"

foreach ($port in $AppPorts) {
    $matchedLines = netstat -ano 2>$null | Where-Object { $_ -match ":$port\s" }

    foreach ($line in $matchedLines) {
        $parts  = ($line.Trim()) -split '\s+'
        $pidStr = $parts[-1]

        if ($pidStr -match '^\d+$') {
            $pidInt = [int]$pidStr
            if ($pidInt -le 4) { continue }

            try {
                Stop-Process -Id $pidInt -Force -ErrorAction SilentlyContinue
                Write-Warn "Killed PID $pidInt (was on :$port)"
            } catch { }
        }
    }
}

Write-Ok "Local app ports cleared"

# =============================================================================
# STEP 2 -- Load / create .env; auto-generate JWT_SECRET_KEY when blank
# =============================================================================

Write-Step "Preparing environment variables"

$EnvFile     = Join-Path $RepoRoot '.env'
$ExampleFile = Join-Path $RepoRoot '.env.example'

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $ExampleFile) {
        Copy-Item $ExampleFile $EnvFile
        Write-Warn ".env not found -- copied from .env.example"
    } else {
        Exit-Script ".env and .env.example are both missing from $RepoRoot"
    }
}

# Auto-fill a blank JWT_SECRET_KEY so the API starts without manual editing
$envContent = Get-Content $EnvFile -Raw
if ($envContent -match '(?m)^JWT_SECRET_KEY\s*=\s*$') {
    $rng    = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $bytes  = New-Object byte[] 32
    $rng.GetBytes($bytes)
    $secret = [Convert]::ToBase64String($bytes)
    $envContent = $envContent -replace '(?m)^JWT_SECRET_KEY\s*=\s*$', "JWT_SECRET_KEY=$secret"
    Set-Content $EnvFile $envContent -NoNewline
    Write-Warn "JWT_SECRET_KEY auto-generated in .env  (replace before production use)"
}

# Load every non-comment key=value line into the current process environment
$envVarsLoaded = 0
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line -match '^\s*#') { return }

    if ($line -match '^([^=]+)=(.*)$') {
        $key   = $matches[1].Trim()
        $value = $matches[2].Trim()
        # Strip surrounding single or double quotes
        $value = $value.Trim('"').Trim("'")
        [System.Environment]::SetEnvironmentVariable($key, $value, 'Process')
        $envVarsLoaded++
    }
}

Write-Ok "$envVarsLoaded variables loaded from .env"

# =============================================================================
# STEP 3 -- Verify Docker daemon
# =============================================================================

Write-Step "Checking Docker daemon"

$dockerReady = $false
for ($attempt = 1; $attempt -le 18; $attempt++) {
    docker info > $null 2> $null
    if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
    $elapsed = ($attempt - 1) * 5
    Write-Warn "Docker not yet ready -- ${elapsed}s elapsed (retrying up to 90s) ..."
    Start-Sleep 5
}
if (-not $dockerReady) {
    Exit-Script "Docker did not become ready after 90s. Start Docker Desktop and retry."
}

Write-Ok "Docker is running"

# =============================================================================
# STEP 4 -- Python virtualenv + API-only dependencies
# =============================================================================
#
# The local venv is used for two purposes only:
#   - Running `uvicorn api.main:app`  (the FastAPI server)
#   - Running `python -m alembic upgrade head`  (migrations)
#
# Heavy packages needed only inside Docker (torch, pyspark, mlflow,
# transformers, scikit-learn) are excluded to keep install time under
# 3 minutes on a clean machine.

Write-Step "Setting up Python environment"

$VenvDir    = Join-Path $RepoRoot '.venv'
$VenvPython = Join-Path $VenvDir  'Scripts\python.exe'
$VenvPip    = Join-Path $VenvDir  'Scripts\pip.exe'

$createdNewVenv = $false

if (-not (Test-Path $VenvPython)) {
    Write-Warn ".venv not found -- creating virtualenv (one-time, ~2-3 min)"

    $sysPy = Get-Command python -ErrorAction SilentlyContinue
    if (-not $sysPy) {
        Exit-Script "Python not found on PATH. Install Python 3.11+ from https://python.org and retry."
    }

    & $sysPy.Source -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Exit-Script "python -m venv failed" }

    $createdNewVenv = $true
    Write-Ok "Virtualenv created at .venv\"
}

$pyVersion = & $VenvPython --version 2>&1
Write-Ok "$pyVersion  ->  $VenvPython"

if ($createdNewVenv) {
    Write-Step "Installing API dependencies (ML/Spark packages excluded -- they run in Docker)"

    # Pinned to the same versions as requirements.txt for compatibility.
    $apiPackages = @(
        'fastapi==0.111.0',
        'uvicorn[standard]==0.29.0',
        'pydantic==2.7.1',
        'pydantic-settings==2.2.1',
        'sqlalchemy==2.0.30',
        'asyncpg==0.29.0',
        'alembic==1.13.1',
        'psycopg2-binary==2.9.9',
        'redis==5.0.4',
        'confluent-kafka==2.4.0',
        'fastavro==1.9.4',
        'python-jose[cryptography]==3.3.0',
        'python-multipart==0.0.9',
        'python-dotenv==1.0.1',
        'structlog==24.1.0',
        'prometheus_client==0.20.0',
        'httpx==0.27.0',
        'jinja2==3.1.4',
        'aiosmtplib==3.0.1',
        'pandas==2.2.2'
    )

    Write-Host "    Installing $($apiPackages.Count) packages ..." -ForegroundColor DarkGray

    & $VenvPip install --quiet $apiPackages
    if ($LASTEXITCODE -ne 0) { Exit-Script "pip install failed (exit code $LASTEXITCODE)" }

    Write-Ok "API dependencies installed"
} else {
    # Quick sanity check -- verify alembic is importable in the existing venv
    $null = & $VenvPython -c "import alembic" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "alembic missing from existing .venv -- running targeted repair"
        & $VenvPip install --quiet alembic==1.13.1 psycopg2-binary==2.9.9 sqlalchemy==2.0.30
        if ($LASTEXITCODE -ne 0) { Exit-Script "pip repair install failed" }
    }
    Write-Ok "Existing .venv is healthy"
}

# =============================================================================
# STEP 5 -- Node.js + npm + dashboard dependencies
# =============================================================================

Write-Step "Checking Node.js and dashboard dependencies"

$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCmd) {
    Exit-Script "node not found on PATH. Install Node.js 20+ from https://nodejs.org and retry."
}
$nodeExe = $nodeCmd.Source
Write-Ok "Node $(& $nodeExe --version)  ->  $nodeExe"

$DashDir = Join-Path $RepoRoot 'dashboard'
$ViteBin = Join-Path $DashDir  'node_modules\vite\bin\vite.js'

if (-not (Test-Path $ViteBin)) {
    Write-Warn "dashboard/node_modules missing -- running npm install"
    $origDir = (Get-Location).Path
    Set-Location $DashDir
    npm install --silent
    if ($LASTEXITCODE -ne 0) { Exit-Script "npm install failed in dashboard/" }
    Set-Location $origDir
    Write-Ok "npm install complete"
} else {
    Write-Ok "dashboard/node_modules present"
}

# Ensure dashboard/.env points at the local API
$DashEnv = Join-Path $DashDir '.env'
if (-not (Test-Path $DashEnv)) {
    'VITE_API_URL=http://localhost:8000' | Set-Content $DashEnv
    Write-Ok "dashboard/.env written  (VITE_API_URL=http://localhost:8000)"
}

# =============================================================================
# STEP 6 -- Remove lingering local-app containers, then start infra
# =============================================================================
#
# gh-api and gh-dashboard are removed so they don't compete with the local
# uvicorn and Vite processes on ports 8000 and 5173.
# Infrastructure containers are preserved between runs so we avoid
# re-running the Airflow init job unnecessarily.

Write-Step "Removing local-app Docker containers (api, dashboard)"

docker compose stop   api dashboard 2>$null
docker compose rm  -f api dashboard 2>$null

Write-Step "Starting infrastructure services via Docker Compose"

# Launched in Docker: infra + orchestration (+ optional ML inference)
# NOT launched in Docker: api, dashboard  (run locally by this script)
$composeServices = @(
    'zookeeper',
    'kafka',
    'schema-registry',
    'postgres',
    'redis',
    'airflow-webserver',
    'airflow-scheduler'
)

if ($WithInference) {
    if (-not $SkipBuild) {
        Write-Step "Building PII Inference Docker image (first build: 10-20 min)"
        docker compose build pii-inference
        if ($LASTEXITCODE -ne 0) {
            Exit-Script "docker compose build pii-inference failed (exit $LASTEXITCODE)"
        }
        Write-Ok "Inference image built"
    }
    $composeServices += 'pii-inference'
}

docker compose up -d $composeServices
if ($LASTEXITCODE -ne 0) {
    Exit-Script "docker compose up failed (exit code $LASTEXITCODE)"
}

Write-Ok "Docker Compose services started"

# =============================================================================
# STEP 7 -- Wait for core services to be healthy
# =============================================================================

function Wait-ContainerHealthy {
    param(
        [Parameter(Mandatory)][string]$ContainerName,
        [int]$TimeoutSec = 120
    )

    Write-Step "Waiting for $ContainerName  (up to ${TimeoutSec}s)"
    $deadline = (Get-Date).AddSeconds($TimeoutSec)

    while ((Get-Date) -lt $deadline) {
        $status = docker inspect --format '{{.State.Health.Status}}' $ContainerName 2>$null
        if ($status) { $status = $status.Trim() }

        switch ($status) {
            'healthy'   { Write-Ok "$ContainerName is healthy"; return }
            'unhealthy' {
                Exit-Script "$ContainerName is unhealthy. Inspect with: docker logs $ContainerName"
            }
            default {
                Write-Host "    $ContainerName  [$status] ..." -ForegroundColor DarkGray
            }
        }

        Start-Sleep 3
    }

    Exit-Script "$ContainerName did not become healthy in ${TimeoutSec}s.  Check: docker logs $ContainerName"
}

# Must be healthy before Alembic runs
Wait-ContainerHealthy -ContainerName 'gh-postgres'        -TimeoutSec 120
Wait-ContainerHealthy -ContainerName 'gh-redis'           -TimeoutSec  60
Wait-ContainerHealthy -ContainerName 'gh-zookeeper'       -TimeoutSec  60
Wait-ContainerHealthy -ContainerName 'gh-kafka'           -TimeoutSec 150
Wait-ContainerHealthy -ContainerName 'gh-schema-registry' -TimeoutSec  90

Write-Ok "All core data services healthy"

# Inference and Airflow have long start periods -- inform but do not block
if ($WithInference) {
    Write-Host ""
    Write-Host "  gh-pii-inference is loading the ML model (~60s) in background." -ForegroundColor DarkGray
    Write-Host "  Watch: docker logs -f gh-pii-inference"                         -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "  gh-airflow-webserver is initialising in background (~90s)." -ForegroundColor DarkGray
Write-Host "  Watch: docker logs -f gh-airflow-webserver"                 -ForegroundColor DarkGray

# =============================================================================
# STEP 8 -- Alembic migrations (pii_hunter database)
# =============================================================================
#
# alembic/env.py already converts postgresql+asyncpg:// to
# postgresql+psycopg2:// so we pass the standard DATABASE_URL format.
# psycopg2-binary is installed in the local venv (Step 4).

Write-Step "Running Alembic migrations"

$env:DATABASE_URL = 'postgresql+asyncpg://airflow:airflow@localhost:5432/pii_hunter'
$env:PYTHONPATH   = $RepoRoot

& $VenvPython -m alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    Exit-Script "alembic upgrade head failed.  Check: docker logs gh-postgres"
}

Write-Ok "Database schema is up to date  (pii_hunter)"

# =============================================================================
# Helper -- open a child PowerShell window via -EncodedCommand
#
# Base64-encoding the command avoids quoting issues when $RepoRoot contains
# spaces, ampersands, or parentheses (common on Windows paths).
# =============================================================================

function Start-EncodedWindow {
    param(
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$Command,
        [string]$WorkDir = $RepoRoot
    )

    $fullCmd = '$Host.UI.RawUI.WindowTitle = ''' + $Title + '''; ' + $Command
    $bytes   = [System.Text.Encoding]::Unicode.GetBytes($fullCmd)
    $encoded = [Convert]::ToBase64String($bytes)

    Start-Process powershell.exe `
        -ArgumentList "-NoExit", "-EncodedCommand", $encoded `
        -WorkingDirectory $WorkDir
}

# =============================================================================
# STEP 9 -- FastAPI server (port 8000)
# =============================================================================
#
# Env vars are injected explicitly into the encoded command so the child
# window works regardless of what is in the system-level environment.

Write-Step "Opening FastAPI server window  (port 8000)"

$jwtSecret = [System.Environment]::GetEnvironmentVariable('JWT_SECRET_KEY', 'Process')

$apiEnv  = '$env:DATABASE_URL               = ''postgresql+asyncpg://airflow:airflow@localhost:5432/pii_hunter''; '
$apiEnv += '$env:REDIS_URL                  = ''redis://localhost:6379/0''; '
$apiEnv += '$env:KAFKA_BOOTSTRAP_SERVERS    = ''localhost:9092''; '
$apiEnv += '$env:KAFKA_SECURITY_PROTOCOL    = ''PLAINTEXT''; '
$apiEnv += '$env:JWT_SECRET_KEY             = ''' + $jwtSecret + '''; '
$apiEnv += '$env:JWT_ALGORITHM              = ''HS256''; '
$apiEnv += '$env:MODEL_CONFIDENCE_THRESHOLD = ''0.85''; '
$apiEnv += '$env:DEMO_MODE                  = ''true''; '
$apiEnv += '$env:PYTHONPATH                 = ''' + $RepoRoot + '''; '

$apiCmd  = $apiEnv
$apiCmd += 'Set-Location ''' + $RepoRoot + '''; '
$apiCmd += '& ''' + $VenvPython + ''' -m uvicorn api.main:app --reload --host 0.0.0.0 --port 8000'

Start-EncodedWindow -Title "GhostHunter  API :8000" -Command $apiCmd -WorkDir $RepoRoot

Write-Ok "FastAPI window opened  ->  http://localhost:8000"

# =============================================================================
# STEP 10 -- Vite dashboard dev server (port 5173)
# =============================================================================
#
# Uses `npm run dev` (mapped to "vite" in dashboard/package.json) so the
# correct platform-specific Vite binary resolves automatically.

Write-Step "Opening Vite dashboard window  (port 5173)"

$feCmd  = '$env:VITE_API_URL = ''http://localhost:8000''; '
$feCmd += 'Set-Location ''' + $DashDir + '''; '
$feCmd += 'npm run dev'

Start-EncodedWindow -Title "GhostHunter  Dashboard :5173" -Command $feCmd -WorkDir $DashDir

Write-Ok "Vite window opened  ->  http://localhost:5173"

# =============================================================================
# STEP 11 -- Wait for API /health, then open browser
# =============================================================================

Write-Step "Waiting for FastAPI server to be ready ..."

$apiReady = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:8000/health" `
                               -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop
        if ($r.StatusCode -eq 200) { $apiReady = $true; break }
    } catch { }
    Write-Host "    API not yet up  ($i / 30) ..." -ForegroundColor DarkGray
    Start-Sleep 2
}

if ($apiReady) {
    Write-Ok "API is healthy at http://localhost:8000/health"
} else {
    Write-Warn "API did not respond after 60s -- check the FastAPI window for errors"
}

Start-Process "http://localhost:5173"
Write-Ok "Browser opened  ->  http://localhost:5173"

# =============================================================================
# STEP 12 -- Summary
# =============================================================================

$div = "  " + ("-" * 60)

Write-Host ""
Write-Host $div -ForegroundColor DarkGray
Write-Host "  Application               URL" -ForegroundColor White
Write-Host $div -ForegroundColor DarkGray
Write-Host "  Dashboard (Vite)          http://localhost:5173"                      -ForegroundColor Green
Write-Host "  API (FastAPI)             http://localhost:8000"                      -ForegroundColor Green
Write-Host "  API Docs (Swagger)        http://localhost:8000/docs"                 -ForegroundColor Green
Write-Host $div -ForegroundColor DarkGray
Write-Host "  Airflow                   http://localhost:8080  (admin / admin)"     -ForegroundColor Cyan
if ($WithInference) {
    Write-Host "  PII Inference             http://localhost:8001/health"            -ForegroundColor Cyan
} else {
    Write-Host "  PII Inference             not started  (.\demo.ps1 -WithInference)" -ForegroundColor DarkGray
}
Write-Host "  Schema Registry           http://localhost:8081"                      -ForegroundColor DarkGray
Write-Host $div -ForegroundColor DarkGray
Write-Host "  PostgreSQL                localhost:5432  (airflow / airflow  |  db: pii_hunter)" -ForegroundColor DarkGray
Write-Host "  Redis                     localhost:6379"                             -ForegroundColor DarkGray
Write-Host "  Kafka                     localhost:9092"                             -ForegroundColor DarkGray
Write-Host $div -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Demo credentials" -ForegroundColor White
Write-Host "    DPO        dpo@company.com      / dpo"     -ForegroundColor Gray
Write-Host "    Auditor    auditor@company.com  / auditor" -ForegroundColor Gray
Write-Host "    Viewer     viewer@company.com   / viewer"  -ForegroundColor Gray
Write-Host "    Admin      admin@company.com    / admin"   -ForegroundColor Gray
Write-Host ""
Write-Host "  Useful commands" -ForegroundColor White
Write-Host "    docker compose logs -f gh-kafka              # Kafka broker logs"   -ForegroundColor Gray
Write-Host "    docker compose logs -f gh-airflow-webserver  # Airflow logs"        -ForegroundColor Gray
Write-Host "    docker compose down                          # stop all services"   -ForegroundColor Gray
Write-Host "    docker compose down -v                       # stop + wipe volumes" -ForegroundColor Gray
Write-Host "    .\demo.ps1 -WithInference                    # start ML service"    -ForegroundColor Gray
Write-Host ""
