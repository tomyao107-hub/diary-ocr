# Diary OCR 2.0.0

面向历史手写日记的**项目化** OCR 与校对工具。在 1.0 自包含项目工作区之上，2.0 补齐可恢复批量任务、素材去重、成册导出、凭据保护与多引擎架构。

## 能力概览

| 版本主题 | 功能 |
|----------|------|
| 项目工作区 | 列表 / 新建 / 打开 / 隐藏 / 彻底删除；素材复制进项目 |
| 导入 | 多图、递归文件夹、PDF 页码范围（默认 300 DPI） |
| 批量 OCR | 全部 / 仅未完成 / 仅重试失败；退避重试；崩溃后 `running→pending` |
| 校对台 | 单页识别、拖拽排序、合并 `final_diary_output.md` |
| 成册导出 | Markdown / DOCX / PDF → `output/exports/` |
| 备份 | 项目 ZIP 备份与恢复；脱敏诊断包 |
| 引擎 | 云端（默认）/ 本地 Tesseract / 混合；隐私模式禁上传 |
| 安全 | API Key 优先写入 Windows 凭据管理器 |

## 启动

Windows 下双击 `run_diary_ocr.cmd`。脚本会创建 `.venv`、安装依赖并启动 `diary_ocr_app.py`。

也可手动运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe diary_ocr_app.py
```

本地 PP-OCR（CPU，推荐 PP-OCRv5 mobile）：

```powershell
# 1) 飞桨 CPU 版（Windows 需用官方源）
.\.venv\Scripts\python.exe -m pip install paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
# 2) PaddleOCR 3.x（含 PP-OCRv5 / v6）
.\.venv\Scripts\python.exe -m pip install "paddleocr>=3.0.0"
```

首次本地识别会自动下载模型；之后可离线使用。在设置中将 OCR 模式改为「本地 PP-OCR（CPU）」或「混合」。

其他可选依赖：

```powershell
# HEIC 导入
.\.venv\Scripts\python.exe -m pip install pillow-heif
# Tesseract 备用引擎（可选）
.\.venv\Scripts\python.exe -m pip install pytesseract
```

默认项目目录为 `%USERPROFILE%\DiaryOCRProjects`，可在项目首页更改。全局设置保存在 `%USERPROFILE%\.diary_ocr_config.json`；在 Windows 上 API Key 优先存凭据管理器，**不写入项目**。

## 项目结构

```text
<project>/
  project.json
  session.json          # schema 2：页列表 + OCR 任务状态
  pages_meta.json       # 可选：哈希、来源、预处理
  sources/              # 原图与原 PDF
  pages/                # 校对队列工作页
  output/               # 每页 .md、final_diary_output.md
  output/exports/       # 成册 diary.md / .docx / .pdf
```

## 已知限制

- 本地 OCR 依赖本机 Tesseract；不可用时**不会**静默切云端。
- HEIC 需额外安装 `pillow-heif`。
- 暂不支持加密 PDF、安装包、自动更新、在线协作。
- 0.x 全局会话不会自动迁移；1.x 项目 session schema 1 会自动升级到 schema 2。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 版本与计划

完整路线与出口条件见 [`PLAN.md`](PLAN.md)。变更记录见 [`CHANGELOG.md`](CHANGELOG.md)。
