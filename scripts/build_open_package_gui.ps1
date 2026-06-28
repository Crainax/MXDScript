$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "python"
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name MXDScriptOpenPackage `
    --paths (Join-Path $ProjectRoot "src") `
    (Join-Path $ProjectRoot "src\mhscript_yjs\gui\open_package_gui.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$DistDir = Join-Path $ProjectRoot "dist\MXDScriptOpenPackage"
$DistConfig = Join-Path $DistDir "config"
$DistVendor = Join-Path $DistDir "vendor\msdk"

New-Item -ItemType Directory -Force -Path $DistConfig | Out-Null
New-Item -ItemType Directory -Force -Path $DistVendor | Out-Null
Copy-Item -Force -Path (Join-Path $ProjectRoot "config\default.toml") -Destination $DistConfig
Copy-Item -Force -Path (Join-Path $ProjectRoot "vendor\msdk\msdk.dll") -Destination $DistVendor

Write-Host "Built: $(Join-Path $DistDir "MXDScriptOpenPackage.exe")"
