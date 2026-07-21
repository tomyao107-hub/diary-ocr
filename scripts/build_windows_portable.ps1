# Build Windows portable package with bundled PaddleOCR-json (Umi-style OOBE).
# Output: release/DiaryOCR-<version>-windows-portable.zip

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Missing .venv. Create it and install requirements first."
}

$Version = & $Python -c "from diary_ocr import __version__; print(__version__)"
if (-not $Version) { $Version = "dev" }

Write-Host "==> Fetch PaddleOCR-json engine"
$FetchScript = Join-Path $Root "scripts\fetch_paddleocr_json.ps1"
& powershell -ExecutionPolicy Bypass -File $FetchScript | Out-Host
$exe = Get-ChildItem -Path (Join-Path $Root "vendor\PaddleOCR-json") -Filter "PaddleOCR*.exe" -Recurse -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $exe) {
    $exe = Get-ChildItem -Path (Join-Path $Root "engines\PaddleOCR-json") -Filter "PaddleOCR*.exe" -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
}
if (-not $exe) { throw "PaddleOCR-json engine not available under vendor/ or engines/" }
$EngineDir = $exe.DirectoryName
Write-Host "    Engine dir: $EngineDir"

Write-Host "==> Installing PyInstaller if needed"
& $Python -m pip install -q "pyinstaller>=6.0"

Write-Host "==> Cleaning previous build"
Remove-Item -Recurse -Force (Join-Path $Root "build") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $Root "dist") -ErrorAction SilentlyContinue

$ReleaseDir = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null

Write-Host "==> PyInstaller onedir"
& $Python -m PyInstaller --noconfirm --clean (Join-Path $Root "diary_ocr.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$DistApp = Join-Path $Root "dist\DiaryOCR"
if (-not (Test-Path $DistApp)) { throw "dist/DiaryOCR not found" }

# Bundle PaddleOCR-json under engines/
$EnginesOut = Join-Path $DistApp "engines\PaddleOCR-json"
New-Item -ItemType Directory -Force -Path $EnginesOut | Out-Null
Write-Host "==> Copying Paddle engine -> $EnginesOut"
Copy-Item -Path (Join-Path $EngineDir "*") -Destination $EnginesOut -Recurse -Force

Copy-Item (Join-Path $Root "README.md") $DistApp -Force
Copy-Item (Join-Path $Root "CHANGELOG.md") $DistApp -Force
Copy-Item (Join-Path $Root "LICENSE") $DistApp -Force -ErrorAction SilentlyContinue

$ReadmePortable = @"
Diary OCR $Version — Windows 便携版（开箱即用）
================================================

双击 DiaryOCR.exe 即可启动，无需安装 Python。

默认使用「本地 Paddle OCR」（engines/PaddleOCR-json），无需 API Key，可离线识别。

使用步骤：
  1. 新建项目
  2. 导入图片 / 文件夹 / PDF
  3. 单页或批量识别（默认本地）
  4. 校对后合并输出

可选：在设置中切换「云端」或「混合」，并配置 API Key。

注意：
  - 本地 Paddle 引擎要求 CPU 支持 AVX
  - 若缺 VCOMP140.DLL，请安装 VC++ 运行库：
    https://aka.ms/vs/17/release/vc_redist.x64.exe
  - 请勿删除 engines/ 目录

全局配置：%USERPROFILE%\.diary_ocr_config.json
项目目录：默认 %USERPROFILE%\DiaryOCRProjects
"@
Set-Content -Path (Join-Path $DistApp "README_PORTABLE.txt") -Value $ReadmePortable -Encoding UTF8

$ZipName = "DiaryOCR-$Version-windows-portable.zip"
$ZipPath = Join-Path $ReleaseDir $ZipName
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }

Write-Host "==> Zipping $ZipName"
Compress-Archive -Path $DistApp -DestinationPath $ZipPath -Force

$Hash = (Get-FileHash -Algorithm SHA256 $ZipPath).Hash
$Hash | Set-Content -Path ($ZipPath + ".sha256") -Encoding ASCII

Write-Host ""
Write-Host "Portable build OK"
Write-Host "  Version : $Version"
Write-Host "  Zip     : $ZipPath"
Write-Host "  SHA256  : $Hash"
Write-Host "  Dist    : $DistApp"
