# ===================== CONFIG (edit these) ==================================
# Base folders
$TempDir   = 'C:\...'
$DevRoot   = 'C:\...\JBGAnnualReportAnalyzer'

# Base Python interpreter
$BasePython = 'C:\...\python.exe'

# Virtual environment
$VenvName  = 'annual_venv'
$VenvDir   = Join-Path $TempDir $VenvName
$VenvRequirements = '.\requirements.txt' 

# Executables inside the venv (once created)
$Py        = Join-Path $VenvDir 'Scripts\python.exe'
$Pip       = Join-Path $VenvDir 'Scripts\pip.exe'
$Uvicorn   = Join-Path $VenvDir 'Scripts\uvicorn.exe'
$Activate  = Join-Path $VenvDir 'Scripts\Activate.ps1'
$Deactivate= Join-Path $VenvDir 'Scripts\deactivate'

# Notebook + kernel
$App        = 'app.main:app'
$Port       = 8040
#
# ===================== END CONFIG (do not edit) =============================
#
# Helper: run in a directory (like a temporary cd with pushd/popd)
function Invoke-InDir {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][scriptblock]$ScriptBlock
    )
    Push-Location $Path
    try   { & $ScriptBlock }
    finally { Pop-Location }
}

# Get current working directory
$CurrentDir = $PWD.Path

# --- Set up virtual environment ---
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null

Set-Location $TempDir
if (-not (Test-Path $Py)) {
    Write-Host "Creating venv: $VenvDir" -ForegroundColor Yellow
    & $BasePython -m venv $VenvDir
}

# Activate the venv for this session
. $Activate

# --- Upgrade pip ---
& $Py -m pip install --upgrade pip

# --- Update web service code to latest version and install modules ---
Invoke-InDir -Path $DevRoot -ScriptBlock {
    git.exe pull
    & $Pip install -r $VenvRequirements
}

# NOTE: an unconditional "pip install --upgrade uvicorn[standard]" used to sit
# here. requirements.txt now pins uvicorn, so the upgrade immediately undid the
# pin on every start: the log showed 0.38.0 being installed and then replaced
# by 0.52.4 seconds later. Change the pin in requirements.txt instead.

# ===================== OCR TOOLS (tesseract + ghostscript) ==================
# Dot-sourced so that the PATH and TESSDATA_PREFIX it sets apply to this
# session, and uvicorn inherits them.
#
# The logic lives in its own file on purpose. This start script is meant to be
# copied and filled in locally, so a fix to the OCR setup would otherwise only
# reach the template and never your working copy. Keeping it separate means a
# git pull is enough.
#
# It can also be run on its own to install or diagnose:
#     .\scripts\Ensure-OcrTools.ps1

$OcrSetup = Join-Path $DevRoot 'scripts\Ensure-OcrTools.ps1'
if (Test-Path $OcrSetup) {
    . $OcrSetup -TempDir $TempDir
} else {
    Write-Host "Hittade inte $OcrSetup - hoppar over OCR-kontrollen." -ForegroundColor Yellow
}
# ===================== END OCR TOOLS ========================================

# --- Launch uvicorn ---
Invoke-InDir -Path $DevRoot -ScriptBlock {
    & $Uvicorn $App --reload --host 127.0.0.1 --port $Port --log-level debug
}

# --- Deactivate and return to project folder ---
Set-Location $TempDir
& $Deactivate
Set-Location $CurrentDir
