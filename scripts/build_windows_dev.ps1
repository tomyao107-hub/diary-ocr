# Build Windows development portable package for Diary OCR.
# Output: release/DiaryOCR-<version>-windows-dev.zip

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Missing .venv. Create it and install requirements first."
}

$Version = & $Python -c "from diary_ocr import __version__; print(__version__)"
if (-not $Version) { $Version = "dev" }

Write-Host "==> Installing PyInstaller if needed"
& $Python -m pip install -q "pyinstaller>=6.0"

Write-Host "==> Cleaning previous build"
Remove-Item -Recurse -Force (Join-Path $Root "build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $Root "dist") -ErrorAction SilentlyContinue

$ReleaseDir = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

Write-Host "==> PyInstaller onedir (console off)"
& $Python -m PyInstaller --noconfirm --clean (Join-Path $Root "diary_ocr.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$DistApp = Join-Path $Root "dist\DiaryOCR"
if (-not (Test-Path $DistApp)) { throw "dist/DiaryOCR not found" }

# Bundle docs and launcher notes for the portable folder.
Copy-Item (Join-Path $Root "README.md") $DistApp -Force
Copy-Item (Join-Path $Root "CHANGELOG.md") $DistApp -Force
Copy-Item (Join-Path $Root "LICENSE") $DistApp -Force -ErrorAction SilentlyContinue

$ReadmePortable = @"
Diary OCR $Version — Windows 开发便携版
========================================

双击 DiaryOCR.exe 启动。

说明：
- 本包为开发版便携目录（onedir），便于排查依赖。
- 云端 OCR、项目管理、PDF/图片导入、成册导出均可用。
- 本地 PP-OCR（Paddle）未完整打入此包；需要本地识别请用源码 + .venv：
    run_diary_ocr.cmd
  或：
    pip install paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
    pip install paddleocr

全局配置：%USERPROFILE%\.diary_ocr_config.json
项目目录：默认 %USERPROFILE%\DiaryOCRProjects
"@
Set-Content -Path (Join-Path $DistApp "README_PORTABLE.txt") -Value $ReadmePortable -Encoding UTF8

$ZipName = "DiaryOCR-$Version-windows-dev.zip"
$ZipPath = Join-Path $ReleaseDir $ZipName
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

Write-Host "==> Zipping $ZipName"
Compress-Archive -Path $DistApp -DestinationPath $ZipPath -Force

$Hash = (Get-FileHash -Algorithm SHA256 $ZipPath).Hash
$Hash | Set-Content -Path ($ZipPath + ".sha256") -Encoding ASCII

Write-Host ""
Write-Host "Build OK"
Write-Host "  Version : $Version"
Write-Host "  Zip     : $ZipPath"
Write-Host "  SHA256  : $Hash"
Write-Host "  Dist    : $DistApp"
