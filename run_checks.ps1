# Kör samma kontroller som CI, lokalt.
#
#   .\run_checks.ps1          – lint och tester
#   .\run_checks.ps1 -Fix     – rätta det ruff kan rätta själv först
#
# Kräver utvecklingsberoendena:  pip install -e ".[dev]"

param(
    [switch]$Fix
)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if ($Fix) {
    Write-Host "== ruff check --fix ==" -ForegroundColor Cyan
    python -m ruff check app tests --fix
}

Write-Host "== ruff check ==" -ForegroundColor Cyan
python -m ruff check app tests
if ($LASTEXITCODE -ne 0) {
    Write-Host "Lintfel. Kör .\run_checks.ps1 -Fix for att rätta det som går automatiskt." -ForegroundColor Yellow
    exit $LASTEXITCODE
}

Write-Host "== pytest ==" -ForegroundColor Cyan
python -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Allt gront." -ForegroundColor Green
