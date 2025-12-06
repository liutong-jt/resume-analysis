# Resume Evaluator · 智能简历视觉分析平台

> 以 VLM 视觉理解为核心的多模态简历评估工具，提供拖拽上传、自动打分与可视化对比体验。

## 功能亮点
- **端到端视觉理解**：PDF 会被拆分为逐页 PNG 并交给多模态模型（VLM）处理，无需传统 OCR。
- **双阶段 AI 管线**：第一次调用用于高保真文本提取，第二次调用使用 CTO 风格提示词输出严格 JSON 评分矩阵。
- **候选人工作台**：Vue + Tailwind 单页应用，可按时间 / 得分 / 领域排序，查看历史记录并一键清除缓存。
- **审计友好**：原始图像、OCR 文本、模型输出 JSON 全量落盘，便于回溯分析与二次处理。
- **配额节省**：复用 OCR 结果、支持去重和单个候选人删除，避免重复计费。

## 架构概览
```
浏览器 (Vue SPA) ──上传PDF────► FastAPI 后端
                                │
                                ├─ pdf_to_images：pypdfium2 渲染成 PNG
                                ├─ call_vlm_for_ocr：OpenAI 兼容接口，提取纯文本
                                ├─ call_llm_for_analysis：分析评分并落盘 JSON
                                └─ /static & /data：提供图像、结果与 OCR
```

## 安装与使用

### 1. 环境准备
- Python 3.10+

### 2. 安装项目依赖
```bash
pip3 install uv
uv venv
source .venv/bin/activate
uv sync
uv run uvicorn main:app --reload
```

### 3. 配置 API 密钥
在项目根目录创建 `.env` 并写入：
```
OPENAI_API_KEY=sk-********
OPENAI_BASE_URL=https://api.openai.com/v1          # 自建网关请替换
OPENAI_OCR_MODEL_NAME=gpt-4o                      # OCR 阶段模型
OPENAI_MODEL_NAME=gpt-4o                          # 分析阶段模型
```
若仅使用官方接口，只需配置 `OPENAI_API_KEY` 即可，其余保持默认。

### 4. 启动服务
```bash
uvicorn main:app --reload
```
- 默认监听 `http://127.0.0.1:8000`
- FastAPI 同时托管静态资源（`/static`）与运行时数据（`/data`），无需额外前端构建步骤

### 5. 页面交互流程
1. 打开 `http://127.0.0.1:8000`，点击 “Upload PDF” 或拖拽文件夹上传多份简历。
2. 后端为每份文档生成 UUID，完成 PDF→PNG 渲染后自动调用 VLM 进行 OCR 和评估。
3. 界面中可按时间 / 得分 / 领域排序查看候选人；点击某一项可预览原始图像与 AI 输出。
4. “Clear Cache” 会删除 `data/` 目录下的缓存；也可在命令行调用 `DELETE /api/candidates/<id>` 删除单个候选人。
5. 需要批量去重时，请调用 `DELETE /api/candidates/deduplicate`，系统会以原始文件名为键，保留最新上传记录。

## 目录结构
```
.
├── main.py                # FastAPI 服务、PDF 处理与 OpenAI 调用
├── static/                # Vue + Tailwind 单页应用
│   ├── index.html
│   ├── app.js
│   └── styles.css
├── data/                  # 运行时生成：uploads / images / results / metadata / ocr
├── pyproject.toml         # Python 依赖声明
└── README.md
```

