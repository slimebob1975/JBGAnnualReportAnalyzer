# Ser till att torch ar byggd for maskinen den kor pa.
#
# Ligger i en egen fil av samma skal som Ensure-OcrTools.ps1: startskriptet ar
# en mall som kopieras och fylls i lokalt, sa en rattelse har skulle annars
# bara na mallen och aldrig din arbetskopia. Med en egen fil racker git pull.
#
# Kan ocksa koras fristaende:
#     .\scripts\Ensure-TorchBuild.ps1 -Py C:\temp\annual_venv\Scripts\python.exe
#
# Bakgrund: PyPI:s torch-hjul for Windows ar processorbundna. Startskriptet kor
# "pip install -r requirements.txt" vid varje start, sa ett CUDA-bygge som
# installerats for hand byts tillbaka nasta gang tjansten startar. Sonden sag
# cuda:0, tjansten skrev "NER-modellen kors pa processorn", och skillnaden
# mellan dem var en omstart.

param(
    # Python i den virtuella miljon.
    [Parameter(Mandatory = $true)]
    [string]$Py,

    # Projektets rot, dar requirements-gpu.txt ligger.
    [Parameter(Mandatory = $true)]
    [string]$DevRoot,

    # auto: anvand kortet om det finns
    # on:   installera CUDA-bygget aven om nvidia-smi inte hittas
    # off:  kor pa processorn, utan att avinstallera nagot
    [ValidateSet('auto', 'on', 'off')]
    [string]$Gpu = 'auto'
)

if ($Gpu -eq 'off') {
    # Stanger av kortet i tjansten utan att nagot avinstalleras, sa att det gar
    # att jamfora de tva utan att vanta pa en ominstallation.
    $env:JBG_USE_GPU = '0'
    Write-Host "Grafikkortet avstangt med -Gpu off. Maskeringen kors pa processorn." -ForegroundColor Gray
    return
}

$env:JBG_USE_GPU = '1'

$SmiFound = $null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
if ($Gpu -eq 'auto' -and -not $SmiFound) {
    Write-Host "Inget grafikkort hittat (nvidia-smi saknas). Maskeringen kors pa processorn." -ForegroundColor Gray
    Write-Host "  Finns ett kort anda: starta med -Gpu on." -ForegroundColor Gray
    return
}

# Fraga torch sjalv vad den ar byggd for. "None" betyder processorbygget.
$Build = & $Py -c "import torch; print(torch.version.cuda or 'cpu')" 2>$null
if ([string]::IsNullOrWhiteSpace($Build)) {
    Write-Host "Kunde inte fraga torch om sitt bygge. Hoppar over." -ForegroundColor Yellow
    return
}

if ($Build -ne 'cpu') {
    Write-Host "torch ar byggd for CUDA $Build." -ForegroundColor Green
    $CudaOk = & $Py -c "import torch; print(torch.cuda.is_available())" 2>$null
    if ($CudaOk -ne 'True') {
        # Ratt bygge men kortet gar anda inte att na. Nastan alltid for gamla
        # drivrutiner i forhallande till den CUDA-version bygget kraver.
        Write-Host "  ... men kortet gar inte att na. Kontrollera drivrutinerna." -ForegroundColor Yellow
        Write-Host "  nvidia-smi visar vilken CUDA-version de stoder." -ForegroundColor Yellow
    }
    return
}

$GpuRequirements = Join-Path $DevRoot 'requirements-gpu.txt'
if (-not (Test-Path $GpuRequirements)) {
    Write-Host "Hittade inte $GpuRequirements - hoppar over." -ForegroundColor Yellow
    return
}

Write-Host "Grafikkort hittat, men torch ar byggd for processorn." -ForegroundColor Yellow
Write-Host "Installerar CUDA-bygget (ett par GB, tar nagra minuter)..." -ForegroundColor Yellow
& $Py -m pip install -r $GpuRequirements

$Build = & $Py -c "import torch; print(torch.version.cuda or 'cpu')" 2>$null
if ($Build -eq 'cpu' -or [string]::IsNullOrWhiteSpace($Build)) {
    Write-Host "Installationen gav fortfarande processorbygget." -ForegroundColor Red
    Write-Host "  Kontrollera att indexet i requirements-gpu.txt matchar" -ForegroundColor Red
    Write-Host "  drivrutinerna. nvidia-smi visar vilken CUDA-version de stoder." -ForegroundColor Red
} else {
    Write-Host "torch ar nu byggd for CUDA $Build." -ForegroundColor Green
}
