# stereo-tv Windows bootstrap: fetches a private, portable Python into .\python, installs the
# dependencies, and runs the setup wizard. No system Python or admin rights needed.
# Run:  right-click > "Run with PowerShell", or from a terminal:  powershell -ExecutionPolicy Bypass -File windows\setup-windows.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
$py = Join-Path $root "python\python.exe"
$ver = "3.12.10"

if (-not (Test-Path $py)) {
    Write-Host "Downloading Python $ver (portable, ~11 MB)..."
    $zip = Join-Path $env:TEMP "python-embed.zip"
    Invoke-WebRequest "https://www.python.org/ftp/python/$ver/python-$ver-embed-amd64.zip" -OutFile $zip
    Expand-Archive $zip -DestinationPath (Join-Path $root "python") -Force
    Remove-Item $zip
    # enable site-packages for pip in the embeddable build
    $pth = Get-ChildItem (Join-Path $root "python") -Filter "python3*._pth" | Select-Object -First 1
    # the embeddable build only searches paths listed here: add the repo root so "-m stereotv" resolves
    ((Get-Content $pth.FullName) -replace "^#import site", "import site") + ".." | Set-Content $pth.FullName
    Write-Host "Installing pip..."
    $gp = Join-Path $env:TEMP "get-pip.py"
    Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $gp
    & $py $gp --no-warn-script-location | Out-Null
    Remove-Item $gp
}
Write-Host "Installing dependencies..."
& $py -m pip install -q --no-warn-script-location -r (Join-Path $root "requirements.txt")
Write-Host ""
& $py -m stereotv.setup
Write-Host ""
Write-Host "Done. Double-click windows\stereo-tv.cmd to start it."
