# Diary OCR 1.0.1

面向历史手写日记的项目化 OCR 与校对工具。1.0 将原先的单次会话升级为自包含项目工作区：

- 项目列表、新建、打开、隐藏与二次确认彻底删除
- 图片复制导入、文件夹递归导入，外部原文件删除后项目仍可用
- PDF 页码范围导入，默认 300 DPI，逐页渲染并支持取消
- 云端单页/批量 OCR、逐页校对、拖拽排序和 Markdown 合并
- 项目级自动保存，页面路径使用项目相对路径，项目文件夹可整体迁移

## 启动

Windows 下双击 `run_diary_ocr.cmd`。脚本会创建 `.venv`、安装依赖并启动新入口 `diary_ocr_app.py`。

也可手动运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe diary_ocr_app.py
```

默认项目目录为 `%USERPROFILE%\DiaryOCRProjects`，可在项目首页更改。全局 API 设置保存在 `%USERPROFILE%\.diary_ocr_config.json`，API Key 不写入项目。

## 项目结构

```text
<project>/
  project.json
  session.json
  sources/       # 导入的原图和原 PDF
  pages/         # 校对队列使用的图片及 PDF 渲染页
  output/        # 每页 Markdown 和 final_diary_output.md
```

## 已知限制

- 仅提供云端 OCR，不含本地 OCR。
- 暂不支持加密 PDF、HEIC、安装包和项目级模型/提示词覆盖。
- 从 0.x 的全局会话不会自动迁移；原始素材可重新导入到新项目。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

