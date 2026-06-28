param(
    [string]$FinalExePath = "D:\MXDScript.exe",
    [switch]$InstallFrontendDeps
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptDir "..")
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$GuiWebDir = Join-Path $ProjectRoot "gui_web"
$DistDir = Join-Path $ProjectRoot "dist"
$DistExe = Join-Path $DistDir "MXDScript.exe"
$ScoopNodeDir = Join-Path $env:USERPROFILE "scoop\apps\nodejs-lts\current"

if (Test-Path $VenvPython) {
    $Python = $VenvPython
} else {
    $Python = "python"
}

if (Test-Path (Join-Path $ScoopNodeDir "node.exe")) {
    $env:PATH = "$ScoopNodeDir;$env:PATH"
}

$NpmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $NpmCommand) {
    throw "npm was not found on PATH. Install Node.js first, then rerun this script."
}

foreach ($ExePath in @($DistExe, $FinalExePath)) {
    if (Test-Path $ExePath) {
        $ResolvedExePath = (Resolve-Path $ExePath).Path
        $RunningProcesses = Get-Process -Name "MXDScript" -ErrorAction SilentlyContinue | Where-Object {
            try {
                $_.Path -ieq $ResolvedExePath
            } catch {
                $false
            }
        }
        if ($RunningProcesses) {
            $ProcessIds = ($RunningProcesses | ForEach-Object { $_.Id }) -join ", "
            throw "$ResolvedExePath is still running (PID: $ProcessIds). Close it before rebuilding."
        }
    }
}

Push-Location $GuiWebDir
try {
    $NodeModules = Join-Path $GuiWebDir "node_modules"
    if ($InstallFrontendDeps -or -not (Test-Path $NodeModules)) {
        if (Test-Path "package-lock.json") {
            & $NpmCommand.Source ci
        } else {
            & $NpmCommand.Source install
        }
    }
    & $NpmCommand.Source run build
} finally {
    Pop-Location
}

$PyInstallerArgs = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--uac-admin",
    "--name", "MXDScript",
    "--distpath", $DistDir,
    "--workpath", (Join-Path $ProjectRoot "build"),
    "--version-file", (Join-Path $ScriptDir "MXDScript.version"),
    "--paths", (Join-Path $ProjectRoot "src"),
    "--collect-submodules", "webview",
    "--add-data", "$(Join-Path $GuiWebDir "dist");gui_web\dist",
    "--add-data", "$(Join-Path $ProjectRoot "config\default.toml");config",
    (Join-Path $ProjectRoot "src\mhscript_yjs\gui\web_app.py")
)

$DllPath = Join-Path $ProjectRoot "vendor\msdk\msdk.dll"
if (Test-Path $DllPath) {
    $InsertAt = $PyInstallerArgs.Count - 1
    $PyInstallerArgs = @(
        $PyInstallerArgs[0..($InsertAt - 1)] +
        @("--add-binary", "$DllPath;vendor\msdk") +
        $PyInstallerArgs[$InsertAt]
    )
}

& $Python @PyInstallerArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$FinalParent = Split-Path -Parent $FinalExePath
if ($FinalParent -and -not (Test-Path $FinalParent)) {
    New-Item -ItemType Directory -Force -Path $FinalParent | Out-Null
}

Copy-Item -Force -Path $DistExe -Destination $FinalExePath

Write-Host "Built: $FinalExePath"
