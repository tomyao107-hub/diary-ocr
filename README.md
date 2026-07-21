# Diary OCR 2.1.1

面向历史手写日记的**项目化** OCR 与校对工具。

**2.1 开箱即用（对齐 Umi-OCR 体验）**：Windows 便携包内置 **PaddleOCR-json**，解压后双击即可本地离线识别，**无需安装 Python、无需 API Key**。

## 能力概览

| 主题 | 功能 |
|------|------|
| 项目工作区 | 列表 / 新建 / 打开 / 隐藏 / 彻底删除；素材复制进项目 |
| 导入 | 多图、递归文件夹、PDF 页码范围（默认 300 DPI） |
| 本地 Paddle | 便携默认 **PaddleOCR-json**；源码可选进程内 PP-OCRv5/v6 |
| 云端 | 可选；混合模式先本地再云端候选 |
| 批量 OCR | 全部 / 仅未完成 / 仅重试失败；可恢复任务队列 |
| 校对台 | 单页识别、拖拽排序、预览叠框、合并 `final_diary_output.md` |
| 成册导出 | Markdown / DOCX / PDF → `output/exports/` |
| 备份 | 项目 ZIP 备份与恢复；脱敏诊断包 |
| 安全 | API Key 优先写入 Windows 凭据管理器 |

## 开箱即用（Windows 便携版）

1. 从 [Releases](https://github.com/tomyao107-hub/diary-ocr/releases) 下载 `DiaryOCR-*-windows-portable.zip`
2. 解压到任意目录
3. 双击 **`DiaryOCR.exe`**
4. 新建项目 → 导入图片/PDF → 直接本地识别

便携目录结构：

```text
DiaryOCR/
  DiaryOCR.exe
  engines/
    PaddleOCR-json/     # 内置本地 Paddle 引擎（勿删）
  README_PORTABLE.txt
```

注意：Paddle 引擎需要 **AVX** CPU；若缺 `VCOMP140.DLL`，安装 [VC++ 运行库](https://aka.ms/vs/17/release/vc_redist.x64.exe)。

构建便携包（开发者）：

```powershell
.\scripts\build_windows_portable.ps1
```

## 源码启动

```powershell
.\run_diary_ocr.cmd
# 或
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe diary_ocr_app.py
```

### 本地引擎（源码）

**方式 A — 与便携版相同（推荐测试开箱路径）**

将 [PaddleOCR-json](https://github.com/hiroi-sora/PaddleOCR-json/releases) Windows 包解压到：

```text
<repo>/engines/PaddleOCR-json/PaddleOCR-json.exe
```

或运行：

```powershell
.\scripts\fetch_paddleocr_json.ps1
# 再复制 vendor/PaddleOCR-json 到 engines/PaddleOCR-json
```

**方式 B — 进程内 PP-OCR（开发机）**

```powershell
.\.venv\Scripts\python.exe -m pip install paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.\.venv\Scripts\python.exe -m pip install "paddleocr>=3.0.0"
```

默认 OCR 模式为 **本地**。云端仅在设置中切换后需要 API Key。

## 项目结构

```text
<project>/
  project.json
  session.json          # schema 2：页列表 + OCR 任务状态
  pages_meta.json       # 可选：哈希、来源、预处理
  sources/              # 原图与原 PDF
  pages/                # 校对队列工作页
  output/               # 每页 .md、final_diary_output.md
  output/exports/       # 成册导出
```

## 已知限制

- 便携本地 OCR 使用 PaddleOCR-json（Paddle 系）；CPU 需 AVX。
- 进程内 PP-OCRv5/v6 仅源码环境；未打进 PyInstaller 主包。
- 暂不支持加密 PDF、MSI 安装包、自动更新。
- 隐私模式禁止任何云端上传；本地不可用时**不会**静默切云端。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe diary_ocr_app.py --check-environment
```

## 版本与计划

变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。路线见 [`PLAN.md`](PLAN.md)。
