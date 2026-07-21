# Download and extract PaddleOCR-json into vendor/ for portable builds.
# Version pinned for reproducible packaging.

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Version = "v1.4.1"
$Asset = "PaddleOCR-json_v1.4.1_windows_x64.7z"
$Url = "https://github.com/hiroi-sora/PaddleOCR-json/releases/download/$Version/$Asset"

$Vendor = Join-Path $Root "vendor"
$Target = Join-Path $Vendor "PaddleOCR-json"
$Archive = Join-Path $Vendor $Asset

New-Item -ItemType Directory -Force -Path $Vendor | Out-Null

# Already extracted?
$existing = Get-ChildItem -Path $Target -Filter "PaddleOCR*.exe" -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($existing) {
    Write-Host "PaddleOCR-json already present: $($existing.FullName)"
    Write-Output $existing.DirectoryName
    exit 0
}

if (-not (Test-Path $Archive)) {
    Write-Host "==> Downloading $Url"
    Invoke-WebRequest -Uri $Url -OutFile $Archive
}

Write-Host "==> Extracting to $Target"
if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
New-Item -ItemType Directory -Force -Path $Target | Out-Null

# Prefer bundled 7zr / system 7-Zip. (py7zr cannot handle BCJ2 used by PaddleOCR-json.)
$bundled = Join-Path $Root "dev-tools\7z\7zr.exe"
$seven = $null
foreach ($c in @(
    $bundled,
    "7z",
    "7zr",
    "${env:ProgramFiles}\7-Zip\7z.exe",
    "${env:ProgramFiles(x86)}\7-Zip\7z.exe"
)) {
    if ($c -and (Test-Path $c)) { $seven = $c; break }
    if ($c -and (Get-Command $c -ErrorAction SilentlyContinue)) { $seven = $c; break }
}
if (-not $seven) {
    throw "Need 7-Zip (or dev-tools/7z/7zr.exe) to extract .7z. Place engines under vendor/PaddleOCR-json manually if needed."
}
Write-Host "==> Extracting with $seven"
& $seven x $Archive "-o$Target" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw "7z extraction failed" }

# Flatten nested single directory if present.
$exe = Get-ChildItem -Path $Target -Filter "PaddleOCR*.exe" -Recurse |
    Where-Object { $_.Name -match "PaddleOCR" } |
    Select-Object -First 1
if (-not $exe) {
    throw "PaddleOCR-json.exe not found after extract"
}

Write-Host "OK: $($exe.FullName)"
Write-Output $exe.DirectoryName
