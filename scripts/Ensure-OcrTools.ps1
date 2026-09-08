<#
.SYNOPSIS
    Installerar och konfigurerar de externa program som OCR kräver.

.DESCRIPTION
    ocrmypdf är ett Python-paket och bor i den virtuella miljön, men det
    anropar två program som inte kan göra det: tesseract.exe och
    gswin64c.exe. De hittas via PATH.

    Skriptet är avsett att köras på två sätt:

      .\scripts\Ensure-OcrTools.ps1
          Fristående. Installerar det som saknas. Använd detta för att
          felsöka, eller en gång efter att en ny dator satts upp.

      . .\scripts\Ensure-OcrTools.ps1
          Dot-sourcat från startskriptet. Gör dessutom katalogerna synliga
          för den sessionen, så att uvicorn ärver dem.

    Ligger i en egen fil just för att startskriptet kopieras och fylls i
    lokalt. En rättelse här når din kopia via git pull utan att du behöver
    kopiera om startskriptet och fylla i konfigurationen på nytt.

.PARAMETER TempDir
    Katalog för en privat tessdata-mapp om Program Files inte är skrivbart.

.PARAMETER Language
    Tesseracts språkkod. Standard 'swe'.
#>
[CmdletBinding()]
param(
    [string]$TempDir = $(if ($env:TEMP) { Join-Path $env:TEMP 'jbg-ocr' } else { 'C:\temp' }),
    [string]$Language = 'swe',

    # Skip the direct download of the Ghostscript installer if winget cannot
    # supply it. Useful on a locked-down machine.
    [switch]$NoDownload
)

$TessdataUrl = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$Language.traineddata"
$script:DeclinedElevation = $false

function Find-ToolDir {
    param([string]$Exe, [string[]]$Candidates)

    $found = Get-Command $Exe -ErrorAction SilentlyContinue
    if ($found) { return Split-Path -Parent $found.Source }

    foreach ($pattern in $Candidates) {
        if (-not $pattern) { continue }
        $hit = Get-Item -Path (Join-Path $pattern $Exe) -ErrorAction SilentlyContinue |
               Sort-Object FullName -Descending | Select-Object -First 1
        if ($hit) { return $hit.DirectoryName }
    }
    return $null
}

function Install-WithWinget {
    param([string[]]$Ids, [string]$Label)

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-Host "  winget saknas - kan inte installera $Label automatiskt." -ForegroundColor Yellow
        return $false
    }

    foreach ($id in $Ids) {
        Write-Host "  Installerar $Label via winget ($id)..." -ForegroundColor Cyan

        # --scope user avoids the elevation prompt where the package allows it.
        $out = & winget install --id $id --exact --silent `
                --accept-source-agreements --accept-package-agreements --scope user 2>&1
        if ($LASTEXITCODE -eq 0) { return $true }

        $out = & winget install --id $id --exact --silent `
                --accept-source-agreements --accept-package-agreements 2>&1
        if ($LASTEXITCODE -eq 0) { return $true }

        # Never swallow this. An earlier version piped winget's output to
        # Out-Null, so a wrong package id and a refused elevation looked
        # exactly the same from the outside.
        Write-Host "    winget misslyckades (exit $LASTEXITCODE):" -ForegroundColor DarkYellow
        ($out | Select-Object -Last 8) | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }

        if (-not $script:DeclinedElevation) {
            $answer = Read-Host "    Forsoka installera $Label som administrator? (j/n)"
            if ($answer -match '^[jJyY]') {
                # Not $args: that is an automatic variable in PowerShell.
                $wingetArgs = "install --id $id --exact --silent " +
                              "--accept-source-agreements --accept-package-agreements"
                try {
                    $proc = Start-Process -FilePath 'winget' -ArgumentList $wingetArgs `
                            -Verb RunAs -Wait -PassThru -ErrorAction Stop
                    if ($proc.ExitCode -eq 0) { return $true }
                    Write-Host "    Installationen som administrator gav exit $($proc.ExitCode)." -ForegroundColor DarkYellow
                } catch {
                    Write-Host "    Kunde inte starta som administrator: $($_.Exception.Message)" -ForegroundColor DarkYellow
                }
            } else {
                $script:DeclinedElevation = $true
            }
        }
    }
    return $false
}

function Ensure-Language {
    param([string]$TesseractDir)

    $langs = & (Join-Path $TesseractDir 'tesseract.exe') --list-langs 2>&1
    if ($langs -match "^$Language$") { return $null }

    # Check the private directory before concluding the pack is missing.
    # tesseract is asked before TESSDATA_PREFIX is set, so it only ever sees
    # the system tessdata - which stays empty when Program Files is not
    # writable. Without this the pack was re-downloaded on every single start.
    $userTessdata = Join-Path $TempDir 'tessdata'
    if ((Test-Path (Join-Path $userTessdata "$Language.traineddata")) -and
        (Test-Path (Join-Path $userTessdata 'configs'))) {
        Write-Host "  Sprakpaketet '$Language' finns redan i $userTessdata" -ForegroundColor Gray
        return $userTessdata
    }
    if (Test-Path (Join-Path $userTessdata "$Language.traineddata")) {
        # Left over from the version that copied only the language files.
        Write-Host "  $userTessdata saknar configs\ - bygger om katalogen." -ForegroundColor Yellow
        Remove-Item $userTessdata -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host "  Sprakpaketet '$Language' saknas. Hamtar det..." -ForegroundColor Cyan
    $systemTessdata = Join-Path $TesseractDir 'tessdata'

    try {
        Invoke-WebRequest -Uri $TessdataUrl -OutFile (Join-Path $systemTessdata "$Language.traineddata") `
                          -UseBasicParsing -ErrorAction Stop
        Write-Host "  Sprakpaket installerat i $systemTessdata" -ForegroundColor Green
        return $null
    } catch {
        # No write access to Program Files. Build a private tessdata directory
        # instead and point TESSDATA_PREFIX at it, which needs no admin rights.
        # Note: tesseract 5 expects TESSDATA_PREFIX to be the tessdata folder
        # itself. Tesseract 4 wanted its parent. If OCR reports a missing
        # language despite the file being there, try the parent directory.
        Write-Host "  Kunde inte skriva till $systemTessdata. Anvander en egen katalog." -ForegroundColor Yellow
        New-Item -ItemType Directory -Force -Path $userTessdata | Out-Null
        # The whole tree, not just the language files. tessdata also holds
        # configs\ and tessconfigs\, and ocrmypdf runs tesseract with the
        # "pdf" config. Copying only *.traineddata gave
        #   "Error occurred while parsing a Tesseract configuration file"
        # on every scanned document.
        Copy-Item (Join-Path $systemTessdata '*') $userTessdata -Recurse -Force -ErrorAction SilentlyContinue
        try {
            Invoke-WebRequest -Uri $TessdataUrl -OutFile (Join-Path $userTessdata "$Language.traineddata") `
                              -UseBasicParsing -ErrorAction Stop
            Write-Host "  Sprakpaket installerat i $userTessdata" -ForegroundColor Green
            return $userTessdata
        } catch {
            Write-Host "  Kunde inte hamta sprakpaketet: $($_.Exception.Message)" -ForegroundColor Yellow
            return $null
        }
    }
}

function Find-WingetIds {
    <#
        Ask winget what it actually has, instead of guessing package ids.

        Guessing produced "No package found matching input criteria" twice in
        a row on a real machine, which looks identical to a refused elevation
        until you read winget's output.
    #>
    param([string]$Query, [string]$Match)

    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return @() }

    Write-Host "  Soker efter $Query i winget..." -ForegroundColor Cyan
    $rows = & winget search --query $Query --source winget --accept-source-agreements 2>&1
    if ($LASTEXITCODE -ne 0) { return @() }

    # The output is a fixed-width table. Ids are the dotted tokens, so pull
    # those out rather than trying to parse columns by position.
    $ids = @()
    foreach ($row in $rows) {
        foreach ($token in ($row -split '\s{2,}')) {
            $t = $token.Trim()
            if ($t -match '^[A-Za-z0-9][\w\-]*(\.[\w\-]+)+$' -and $t -match $Match) {
                $ids += $t
            }
        }
    }
    $ids = $ids | Select-Object -Unique
    if ($ids) {
        Write-Host "    Hittade: $($ids -join ', ')" -ForegroundColor Gray
    } else {
        Write-Host "    winget kande inte igen nagot paket for '$Query'." -ForegroundColor DarkYellow
    }
    return $ids
}

function Install-GhostscriptFromGithub {
    <#
        Artifex publishes the official Windows installers as GitHub releases.
        The asset list is read at runtime, so a new version needs no change
        here. The installer is NSIS: /S is silent, /D sets the target and must
        come last and unquoted, which is why the path has no spaces.
    #>
    param([string]$InstallRoot)

    $api = 'https://api.github.com/repos/ArtifexSoftware/ghostpdl-downloads/releases/latest'
    Write-Host "  Hamtar Ghostscript direkt fran Artifex..." -ForegroundColor Cyan

    try {
        $previous = $ProgressPreference
        $ProgressPreference = 'SilentlyContinue'   # otherwise the download crawls
        $release = Invoke-RestMethod -Uri $api -UseBasicParsing -Headers @{
            'User-Agent' = 'JBGAnnualReportAnalyzer'
            'Accept'     = 'application/vnd.github+json'
        } -ErrorAction Stop

        $asset = $release.assets |
                 Where-Object { $_.name -match '^gs\d+w64\.exe$' } |
                 Select-Object -First 1
        if (-not $asset) {
            Write-Host "    Hittade ingen 64-bitars installerare i $($release.tag_name)." -ForegroundColor DarkYellow
            return $null
        }

        $size = [math]::Round($asset.size / 1MB)
        Write-Host "    $($asset.name) ($size MB) fran $($release.tag_name)" -ForegroundColor Gray

        $installer = Join-Path $env:TEMP $asset.name
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $installer `
                          -UseBasicParsing -ErrorAction Stop

        $target = Join-Path $InstallRoot $release.tag_name
        Write-Host "    Installerar tyst till $target..." -ForegroundColor Gray
        # /D must be last and unquoted for NSIS.
        $proc = Start-Process -FilePath $installer -ArgumentList "/S /D=$target" `
                              -Wait -PassThru -ErrorAction Stop
        Remove-Item $installer -Force -ErrorAction SilentlyContinue

        if ($proc.ExitCode -ne 0) {
            Write-Host "    Installeraren gav exit $($proc.ExitCode)." -ForegroundColor DarkYellow
            return $null
        }
        return (Join-Path $target 'bin')
    } catch {
        Write-Host "    Kunde inte hamta eller kora installeraren: $($_.Exception.Message)" -ForegroundColor DarkYellow
        return $null
    } finally {
        if ($previous) { $ProgressPreference = $previous }
    }
}

# ---------------------------------------------------------------------------

Write-Host "Kontrollerar OCR-verktyg..." -ForegroundColor Yellow

$TesseractPaths = @(
    'C:\Program Files\Tesseract-OCR',
    'C:\Program Files (x86)\Tesseract-OCR',
    "$env:LOCALAPPDATA\Programs\Tesseract-OCR",
    "$env:LOCALAPPDATA\Tesseract-OCR"
)
$TesseractDir = Find-ToolDir -Exe 'tesseract.exe' -Candidates $TesseractPaths
if (-not $TesseractDir) {
    Install-WithWinget -Ids @('UB-Mannheim.TesseractOCR') -Label 'tesseract' | Out-Null
    $TesseractDir = Find-ToolDir -Exe 'tesseract.exe' -Candidates $TesseractPaths
}

# Ghostscript installs under a version-numbered directory, and winget may put
# it per-user or per-machine depending on scope and elevation.
# gswin32c is the 32-bit console binary used by older builds.
$GhostscriptPaths = @(
    'C:\Program Files\gs\gs*\bin',
    'C:\Program Files (x86)\gs\gs*\bin',
    "$env:LOCALAPPDATA\Programs\gs\gs*\bin",
    "$env:ProgramW6432\gs\gs*\bin",
    "$env:ProgramFiles\gs\gs*\bin",
    "$env:LOCALAPPDATA\Programs\gs\*\bin"
)
$GhostscriptDir = Find-ToolDir -Exe 'gswin64c.exe' -Candidates $GhostscriptPaths
if (-not $GhostscriptDir) {
    $GhostscriptDir = Find-ToolDir -Exe 'gswin32c.exe' -Candidates $GhostscriptPaths
}
if (-not $GhostscriptDir) {
    # Ask winget what it has rather than guessing. Two hardcoded ids both
    # returned "No package found matching input criteria" on a real machine.
    $gsIds = Find-WingetIds -Query 'ghostscript' -Match 'ghostscript'
    if ($gsIds) {
        Install-WithWinget -Ids $gsIds -Label 'ghostscript' | Out-Null
        $GhostscriptDir = Find-ToolDir -Exe 'gswin64c.exe' -Candidates $GhostscriptPaths
        if (-not $GhostscriptDir) {
            $GhostscriptDir = Find-ToolDir -Exe 'gswin32c.exe' -Candidates $GhostscriptPaths
        }
    }
}

if (-not $GhostscriptDir -and -not $NoDownload) {
    # winget has no usable package on this machine. Go to the source.
    $gsRoot = Join-Path $env:LOCALAPPDATA 'Programs\gs'   # no spaces: NSIS /D needs that
    $installed = Install-GhostscriptFromGithub -InstallRoot $gsRoot
    if ($installed -and (Test-Path (Join-Path $installed 'gswin64c.exe'))) {
        $GhostscriptDir = $installed
    } else {
        $GhostscriptPaths += (Join-Path $gsRoot '*\bin')
        $GhostscriptDir = Find-ToolDir -Exe 'gswin64c.exe' -Candidates $GhostscriptPaths
    }
}

# Visible to this session, and to anything started from it.
foreach ($dir in @($TesseractDir, $GhostscriptDir)) {
    if ($dir -and ($env:PATH -notlike "*$dir*")) { $env:PATH = "$dir;$env:PATH" }
}

if ($TesseractDir) {
    $privateTessdata = Ensure-Language -TesseractDir $TesseractDir
    if ($privateTessdata) { $env:TESSDATA_PREFIX = $privateTessdata }
}

Write-Host ""
if ($TesseractDir -and $GhostscriptDir) {
    $langs = & tesseract --list-langs 2>&1
    $configOk = (-not $env:TESSDATA_PREFIX) -or (Test-Path (Join-Path $env:TESSDATA_PREFIX 'configs'))
    if (-not $configOk) {
        Write-Host "VARNING: $env:TESSDATA_PREFIX saknar configs\. OCR kommer att misslyckas." -ForegroundColor Red
    }
    if (($langs -match "^$Language$") -and $configOk) {
        Write-Host "OCR klart." -ForegroundColor Green
        Write-Host "  tesseract   : $TesseractDir" -ForegroundColor Gray
        Write-Host "  ghostscript : $GhostscriptDir" -ForegroundColor Gray
        if ($env:TESSDATA_PREFIX) {
            Write-Host "  tessdata    : $env:TESSDATA_PREFIX" -ForegroundColor Gray
        }
    } else {
        Write-Host "OCR delvis klart: sprakpaketet '$Language' saknas fortfarande." -ForegroundColor Yellow
    }
} else {
    if (-not $TesseractDir) {
        Write-Host "tesseract hittades inte. Installera med nagot av foljande:" -ForegroundColor Yellow
        Write-Host "  winget install --id UB-Mannheim.TesseractOCR" -ForegroundColor Gray
        Write-Host "  eller installeraren pa https://github.com/UB-Mannheim/tesseract/wiki" -ForegroundColor Gray
    }
    if (-not $GhostscriptDir) {
        Write-Host "ghostscript hittades inte. ocrmypdf kraver det. Prova i tur och ordning:" -ForegroundColor Yellow
        Write-Host "  1. ladda ner installeraren fran" -ForegroundColor Gray
        Write-Host "     https://github.com/ArtifexSoftware/ghostpdl-downloads/releases" -ForegroundColor Gray
        Write-Host "     (filen heter gsXXXXw64.exe) och kor den" -ForegroundColor Gray
        Write-Host "  2. eller https://ghostscript.com/releases/gsdnld.html" -ForegroundColor Gray
        Write-Host "  Kor sedan .\scripts\Ensure-OcrTools.ps1 igen for att verifiera." -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "Tjansten fungerar anda, men inskannade rapporter hoppas over." -ForegroundColor Yellow
}
