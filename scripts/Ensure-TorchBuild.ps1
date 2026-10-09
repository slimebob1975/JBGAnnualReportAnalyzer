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
    [string]$Gpu = 'auto',

    # Tvinga ett visst CUDA-index, till exempel 'cu126'. Normalt behovs det
    # inte: versionen harleds ur nvidia-smi och provas mot kortet. Finns for
    # en driftsattning dar installationen ska vara forutsagbar i stallet for
    # upptackt, och for det fall harledningen har fel.
    #
    # Skriptet skriver ut vilket index det valde, sa vardet att pinna har gar
    # att lasa ur en korning som fungerat.
    [string]$CudaIndex = ''
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

$Skip = $null
if ($Build -ne 'cpu') {
    $Reachable = & $Py -c "import torch; print(torch.cuda.is_available())" 2>$null
    if ($Reachable -eq 'True') {
        Write-Host "torch ar byggd for CUDA $Build, och kortet gar att na." -ForegroundColor Green
        return
    }
    # Ratt sorts bygge men fel version: hjulet kraver nyare drivrutiner an
    # maskinen har. Det ar inget skal att ge upp - ett aldre CUDA-hjul kan
    # fungera. Forsta versionen av det har skriptet stannade har, sa ett
    # cu130-bygge som inte gick att anvanda blev kvar run efter run.
    Write-Host "torch ar byggd for CUDA $Build, men kortet gar inte att na." -ForegroundColor Yellow
    Write-Host "  Drivrutinerna ar troligen aldre. Soker ett aldre hjul." -ForegroundColor Yellow
    $Skip = "cu$($Build -replace '\.', '')"
}

# Versionen hamtas ur requirements.txt sa att de tva inte kan glida isar.
$Pinned = Select-String -Path (Join-Path $DevRoot 'requirements.txt') `
                        -Pattern '^torch==(.+)$' |
          ForEach-Object { $_.Matches[0].Groups[1].Value } |
          Select-Object -First 1
if ([string]::IsNullOrWhiteSpace($Pinned)) {
    Write-Host "Hittade ingen torch-pinne i requirements.txt. Hoppar over." -ForegroundColor Yellow
    return
}

# Vilket CUDA-index som har ett hjul for en viss torch-version gar inte att
# rakna ut: det foljer varken drivrutinerna eller torch-versionen pa nagot
# forutsagbart satt. cu124 visade sig bara ha torch 2.6.0 medan pinnen var
# 2.14.0, och att gissa en gang till vore att gissa.
#
# Darfor provas de i tur och ordning, nyast forst, tills ett har hjulet.
# Nagra sekunder per miss, och svaret blir ratt utan att nagon behover veta
# det i forvag.
if ($CudaIndex) {
    Write-Host "  anvander $CudaIndex (angivet med -CudaIndex)." -ForegroundColor Gray
    $Indexes = @($CudaIndex)
    $Skip = $null
} else {
    $Indexes = @('cu130', 'cu129', 'cu128', 'cu126', 'cu124')

    # nvidia-smi sager vilken CUDA-version drivrutinerna stoder, och hogre an
    # sa gar inte att anvanda. Utan det provas varje nyare hjul i tur och
    # ordning, och varje forsok laddar ner ett par gigabyte som sedan visar
    # sig obrukbart: pa en maskin med CUDA 12.6 blev det cu130 och cu128 i
    # onodan innan cu126.
    $Supported = & nvidia-smi 2>$null |
                 Select-String -Pattern 'CUDA Version:\s*(\d+)\.(\d+)'
    if ($Supported) {
        $Major = [int]$Supported.Matches[0].Groups[1].Value
        $Minor = [int]$Supported.Matches[0].Groups[2].Value
        $Ceiling = $Major * 10 + $Minor
        Write-Host "  drivrutinerna stoder CUDA $Major.$Minor." -ForegroundColor Gray
        $Indexes = $Indexes | Where-Object {
            [int]($_ -replace '^cu', '') -le $Ceiling
        }
    }

    if ($Skip) {
        # Den versionen ar redan installerad och bevisat onabar.
        $Indexes = $Indexes | Where-Object { $_ -ne $Skip }
    }
}

if (-not $Indexes) {
    Write-Host "Inget av de kanda CUDA-hjulen passar drivrutinerna." -ForegroundColor Red
    Write-Host "  Uppdatera drivrutinerna, eller lat maskeringen ga pa processorn." -ForegroundColor Red
    $Indexes = @()
}

if (-not $Skip) {
    Write-Host "Grafikkort hittat, men torch ar byggd for processorn." -ForegroundColor Yellow
}
Write-Host "Soker ett anvandbart CUDA-hjul for torch $Pinned ..." -ForegroundColor Yellow

$Installed = $false
foreach ($Index in $Indexes) {
    $Url = "https://download.pytorch.org/whl/$Index"
    Write-Host "  provar $Index ..." -ForegroundColor Gray

    # --force-reinstall behovs: processor- och CUDA-hjulen har samma
    # versionsnummer, sa pip ser annars "already satisfied" och hamtar
    # ingenting. --no-deps for att bara torch ska bytas; pa Windows ligger
    # CUDA-biblioteken inne i sjalva hjulet.
    & $Py -m pip install --force-reinstall --no-deps `
          --index-url $Url "torch==$Pinned" 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $Build = & $Py -c "import torch; print(torch.version.cuda or 'cpu')" 2>$null
        if ($Build -ne 'cpu' -and -not [string]::IsNullOrWhiteSpace($Build)) {
            # Ratt bygge racker inte: drivrutinerna maste ocksa klara den
            # CUDA-versionen. Ett cu130-hjul installerades utan knot pa en
            # maskin vars drivrutiner stannade vid 12.x, och tjansten foll
            # tillbaka pa processorn utan att nagon forstod varfor.
            $Reachable = & $Py -c "import torch; print(torch.cuda.is_available())" 2>$null
            if ($Reachable -eq 'True') {
                Write-Host "torch $Pinned ar nu byggd for CUDA $Build ($Index), och kortet gar att na." -ForegroundColor Green
                $Installed = $true
                break
            }
            Write-Host "    bygget gick in, men kortet gar inte att na med CUDA $Build." -ForegroundColor Gray
            Write-Host "    drivrutinerna ar troligen aldre. Provar nasta." -ForegroundColor Gray
        }
    }
}

if (-not $Installed) {
    Write-Host "Hittade inget anvandbart CUDA-hjul for torch $Pinned." -ForegroundColor Red
    Write-Host "  Provade: $($Indexes -join ', ')" -ForegroundColor Red
    Write-Host "  Se https://download.pytorch.org/whl/torch for vilka som finns," -ForegroundColor Red
    Write-Host "  och nvidia-smi for vilken CUDA drivrutinerna stoder." -ForegroundColor Red
    Write-Host "  Maskeringen kors pa processorn sa lange." -ForegroundColor Red
    # Lamna inte kvar ett CUDA-bygge som inte gar att anvanda: det ar storre,
    # langsammare att ladda och ser i loggen ut som om kortet borde fungera.
    # Processorbygget ar det arligare slutlaget.
    $Final = & $Py -c "import torch; print(torch.version.cuda or 'cpu')" 2>$null
    if ($Final -ne 'cpu') {
        Write-Host "  Gar tillbaka till processorbygget ..." -ForegroundColor Yellow
        & $Py -m pip install --force-reinstall --no-deps `
              --index-url https://pypi.org/simple "torch==$Pinned"
    }
}
