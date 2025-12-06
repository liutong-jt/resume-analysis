import shutil
import uuid
import os
import base64
import json
from pathlib import Path
from typing import List, Optional, Dict
from dotenv import load_dotenv
import pypdfium2 as pdfium
from fastapi import FastAPI, UploadFile, File, HTTPException, Body
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import OpenAI

# Load environment variables from .env file
load_dotenv()

# Define paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
IMAGES_DIR = DATA_DIR / "images"
RESULTS_DIR = DATA_DIR / "results"
METADATA_DIR = DATA_DIR / "metadata"
OCR_DIR = DATA_DIR / "ocr"

# Ensure directories exist
for path in [UPLOAD_DIR, IMAGES_DIR, RESULTS_DIR, METADATA_DIR, OCR_DIR]:
    path.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Resume Evaluator")

# Enable CORS for development flexibility
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")
# Mount data directory to serve images
app.mount("/data", StaticFiles(directory="data"), name="data")

# ----------------- Models -----------------

class AnalyzeRequest(BaseModel):
    candidate_id: str

# ----------------- Helper Functions -----------------

def pdf_to_images(pdf_path: Path, output_dir: Path) -> List[str]:
    """
    Convert PDF on disk to PNG images on disk.
    Returns a list of relative URL paths to the images.
    """
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
        image_urls = []
        
        # Clear existing images if any (for re-processing case, though UUIDs avoid this usually)
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True)

        for i in range(len(pdf)):
            page = pdf[i]
            # Render at 1x scale for smaller file size while maintaining good OCR quality
            bitmap = page.render(scale=1.0)
            pil_image = bitmap.to_pil()

            # Optional: Resize to half the original dimensions for even smaller files
            # width, height = pil_image.size
            # resized_image = pil_image.resize((width // 2, height // 2), Image.LANCZOS)

            image_name = f"page_{i}.png"
            image_path = output_dir / image_name
            pil_image.save(image_path, format="PNG", optimize=True)

            # Construct URL path relative to the mount point
            relative_path = f"/data/images/{output_dir.name}/{image_name}"
            image_urls.append(relative_path)

        return image_urls
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF processing error: {str(e)}")

# OCR 专用 System Prompt - 第一次调用
OCR_SYSTEM_PROMPT = """
你是一个专业的 OCR 文本提取助手。你必须将其中的所有文字内容完整准确地提取出来。

**重要说明：**
- 你正在接收的消息中包含了图像数据，你可以看到这些图像
- 不要说你无法查看图像，你必须直接提取图像中的文字
- 不要询问用户上传图像，图像已经在消息中了

**提取要求：**
- 保留原文的段落结构和换行
- 不要遗漏任何细节等
- 如果有表格或列表，请尽量保持其结构
- 保持原文的完整性，不要进行任何分析或总结

**输出格式：**
只输出提取的纯文本内容，无需任何 JSON 包装或 markdown 标记。
直接输出简历的文字内容，从第一个字开始。

"""

# 分析专用 System Prompt - 第二次调用
ANALYSIS_SYSTEM_PROMPT = """
你是一位以“犀利、直觉敏锐”著称的资深技术 CTO。你阅人无数，极度反感简历中的“水分”和“关键词堆砌”。你的任务是像剥洋葱一样，透过简历的表面描述，洞察候选人真实的工程能力，并生成一份严格的 JSON 评估报告。

**你的核心思维模式（Mental Model）：**
1.  **拒绝表象**：候选人列出 `Kubernetes`，你要问自己“他是只会部署 YAML，还是懂集群调度？”；列出 `微服务`，你要看他是否解决了拆分后的数据一致性痛点。如果没有细节支撑，默认视为“仅了解皮毛”。
2.  **寻找“思考的痕迹”**：只看“做了什么（What）”是不够的，必须寻找“为什么这么做（Why）”和“怎么解决难题的（How）”。
3.  **怀疑精神**：对于流水账式的项目描述，给予低分。只有当看到量化的性能提升、复杂的架构决策或对底层原理的运用时，才给予认可。
4.  **必须有结论**：即使简历信息很少，也要基于“信息稀缺本身代表了总结能力不足或经历平淡”这一事实进行评估，严禁回答“无法评估”。

**分析步骤与评分标准（1-10分，请严格控制分值分布，普通人为 4-6 分）：**

1.  **基本信息与领域**：
    * 提取姓名（无则空）。
    * 判断领域：['Big Data', 'AI', 'Cloud', 'App Dev', 'Other'] (必选其一)。

2.  **四维深度评估（Evaluation Matrix）：**

    * **技术基础 (tech_foundation)**：*不仅看由于会什么，更看技术栈的合理性*
        - **洞察方向**：考察计算机基础（OS、网络、数据结构）。如果候选人是转行且无补修基础课经历，扣分。如果技术栈杂乱无章（如同时列出5种不相关的语言但无一精通），扣分。
        - **高分信号**：Github 源码贡献、对底层原理的描述、算法竞赛奖项。

    * **技术认知 (tech_cognition)**：*区分“代码搬运工”和“工程师”的关键*
        - **洞察方向**：寻找项目中的**决策点**。使用了 Redis？是跟风用，还是为了解决特定的 QPS 瓶颈？
        - **评分逻辑**：
            - 3-5分：流水账，"使用了 Spring Boot + MySQL 实现了增删改查"。
            - 6-7分：提到了优化，"通过索引优化将查询降低至 100ms"。
            - 8-10分：体现了权衡与架构思维，"在一致性和可用性之间选择了最终一致性，采用了 MQ 削峰..."。

    * **技术潜力 (tech_potential)**：*透过现在的能力看未来*
        - **洞察方向**：看成长的“斜率”。是不是在短时间内掌握了跨领域的技术？是否有技术博客、开源热情？是否在项目中承担了超出职级的工作？

    * **学习能力 (learning_ability)**：*不仅是学得快，还要看学得深*
        - **洞察方向**：是否通过官方文档而非二手教程学习？是否有解决 "Unknown Unknowns" (未知的未知) 问题的经历？

3.  **总结**：生成一句话的“毒舌”或“赞赏”摘要。

**输出格式要求：**
* **Reason 字段风格**：请使用**简练、辛辣、一针见血**的专业评审语气。不要写“该候选人展示了...”，直接写“项目描述流于表面，缺乏高并发场景下的深层思考”或“技术栈扎实，但对分布式事务的处理缺乏细节”。
* 严格输出 JSON。

**JSON 结构模版：**
{
  "candidate_name": "姓名",
  "domain_classification": "领域",
  "summary_one_liner": "一句话核心评价",
  "evaluation_matrix": {
    "tech_foundation": { "score": 0, "reason": "60字以内犀利点评" },
    "tech_cognition": { "score": 0, "reason": "60字以内犀利点评" },
    "tech_potential": { "score": 0, "reason": "60字以内犀利点评" },
    "learning_ability": { "score": 0, "reason": "60字以内犀利点评" }
  },
  "total_score": 0
}
"""

def process_evaluation_result(result: dict, filename: str = None) -> dict:
    """
    Process and validate evaluation results to handle edge cases
    """
    # Handle empty or "Unknown" names
    if not result.get('candidate_name') or result['candidate_name'] == 'Unknown':
        if filename:
            # Use filename without extension as fallback
            result['candidate_name'] = filename.replace('.pdf', '').replace('.PDF', '')
        else:
            result['candidate_name'] = 'Candidate'

    # Ensure total score is calculated correctly
    total = 0
    if 'evaluation_matrix' in result:
        for dimension in ['tech_foundation', 'tech_cognition', 'tech_potential', 'learning_ability']:
            if dimension in result['evaluation_matrix'] and 'score' in result['evaluation_matrix'][dimension]:
                score = result['evaluation_matrix'][dimension]['score']
                # Ensure score is within valid range
                if isinstance(score, (int, float)) and 1 <= score <= 10:
                    total += int(score)
                else:
                    # Default to 1 if invalid score
                    result['evaluation_matrix'][dimension]['score'] = 1
                    total += 1

    result['total_score'] = total

    return result

def call_vlm_for_ocr(candidate_id: str) -> str:
    """
    第一次 LLM 调用：使用 VLM 从简历图像中提取 OCR 文本
    
    Args:
        candidate_id: 候选人 UUID
        
    Returns:
        str: 提取的纯文本内容
    """
    candidate_images_dir = IMAGES_DIR / candidate_id
    
    if not candidate_images_dir.exists():
        raise HTTPException(status_code=404, detail="Candidate images not found")
    
    # Get API configuration
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model_name = os.getenv("OPENAI_OCR_MODEL_NAME", "gpt-4o")
    
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    # Construct messages for OCR
    messages = [
        {"role": "system", "content": OCR_SYSTEM_PROMPT},
        {"role": "user", "content": [{"type": "text", "text": "请提取以下简历图像中的所有文字内容："}]}
    ]
    
    # Read images from disk and convert to base64
    image_files = sorted(list(candidate_images_dir.glob("*.png")), key=lambda p: p.stem)
    
    for img_path in image_files:
        with open(img_path, "rb") as image_file:
            b64_str = base64.b64encode(image_file.read()).decode('utf-8')
            messages[1]["content"].append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64_str}"}
            })
    
    # Call VLM for OCR
    response = client.chat.completions.create(
        model=model_name,
        messages=messages,
        max_tokens=4000
    )
    
    ocr_text = response.choices[0].message.content
    
    # Save OCR result to disk
    ocr_path = OCR_DIR / f"{candidate_id}.txt"
    with open(ocr_path, "w", encoding='utf-8') as f:
        f.write(ocr_text)
    
    return ocr_text

def call_llm_for_analysis(candidate_id: str, ocr_text: str, original_filename: str = "Resume") -> dict:
    """
    第二次 LLM 调用：基于 OCR 文本进行技术分析评估
    
    Args:
        candidate_id: 候选人 UUID
        ocr_text: OCR 提取的文本内容
        original_filename: 原始文件名（用于候选人姓名的fallback）
        
    Returns:
        dict: 分析结果的 JSON 对象
    """
    # Get API configuration
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model_name = os.getenv("OPENAI_MODEL_NAME", "gpt-4o")
    
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")
    
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    # Construct messages for analysis
    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
        {"role": "user", "content": f"以下是候选人的简历文本内容，请进行深度技术评估：\n\n{ocr_text}"}
    ]
    
    # Call LLM for analysis
    response = client.chat.completions.create(
        model=model_name,
        messages=messages,
        max_tokens=2000,
        response_format={"type": "json_object"}
    )
    
    content = response.choices[0].message.content
    parsed_result = json.loads(content)
    
    # Process result with original filename fallback
    processed_result = process_evaluation_result(parsed_result, filename=original_filename)
    
    # Save result to disk
    result_path = RESULTS_DIR / f"{candidate_id}.json"
    with open(result_path, "w", encoding='utf-8') as f:
        json.dump(processed_result, f, ensure_ascii=False, indent=2)
    
    return processed_result


# ----------------- API Endpoints -----------------

@app.get("/api/candidates")
async def get_candidates():
    """
    Retrieve all processed/uploaded candidates from disk.
    """
    candidates = []
    
    # Iterate through Uploads to find candidates
    for pdf_file in UPLOAD_DIR.glob("*.pdf"):
        cid = pdf_file.stem # UUID
        
        # Check if analyzed
        result_file = RESULTS_DIR / f"{cid}.json"
        metadata_file = METADATA_DIR / f"{cid}.json"
        
        # Default metadata
        original_filename = "Unknown.pdf"
        
        # Try to load metadata
        if metadata_file.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    original_filename = meta.get("original_filename", original_filename)
            except:
                pass

        candidate_data = {
            "id": cid,
            "filename": original_filename,
            "name": None,  # Will populate from result if available
            "status": "pending",
            "image_urls": [],
            "result": None
        }

        # Try to find images
        candidate_images_dir = IMAGES_DIR / cid
        if candidate_images_dir.exists():
            # Get all pngs, sorted
            imgs = sorted(list(candidate_images_dir.glob("*.png")), key=lambda p: p.stem)
            candidate_data["image_urls"] = [f"/data/images/{cid}/{p.name}" for p in imgs]
        
        if result_file.exists():
             with open(result_file, 'r', encoding='utf-8') as f:
                 try:
                     res = json.load(f)
                     candidate_data["result"] = res
                     candidate_data["status"] = "done"
                     # Set name from result
                     if res.get("candidate_name"):
                        candidate_data["name"] = res["candidate_name"]
                 except:
                     candidate_data["status"] = "error"
        elif candidate_data["image_urls"]:
            candidate_data["status"] = "parsed" # Has images but no result
            
        candidates.append(candidate_data)
        
    # Sort by creation time (newest first)
    candidates.sort(key=lambda x: (UPLOAD_DIR / f"{x['id']}.pdf").stat().st_ctime, reverse=True)
    
    return candidates


@app.post("/api/upload_pdf")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    cid = str(uuid.uuid4())
    pdf_path = UPLOAD_DIR / f"{cid}.pdf"
    
    # Save PDF
    with open(pdf_path, "wb") as buffer:
        content = await file.read()
        buffer.write(content)
        
    # Save Metadata
    metadata_path = METADATA_DIR / f"{cid}.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump({"original_filename": file.filename}, f, ensure_ascii=False)
        
    # Process images
    candidate_images_dir = IMAGES_DIR / cid
    image_urls = pdf_to_images(pdf_path, candidate_images_dir)

    return {
        "id": cid,
        "filename": file.filename,
        "page_count": len(image_urls),
        "image_urls": image_urls
    }

@app.post("/api/analyze")
async def analyze_resume(request: AnalyzeRequest):
    """
    分析简历 - 两阶段处理：
    1. 第一次调用 VLM 进行 OCR 文本提取
    2. 第二次调用 LLM 进行技术评估分析
    """
    try:
        cid = request.candidate_id
        
        # Load metadata for filename fallback
        metadata_file = METADATA_DIR / f"{cid}.json"
        original_filename = "Resume"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    original_filename = meta.get("original_filename", "Resume")
            except:
                pass

        # 阶段 1：OCR 文本提取
        # Check if OCR already exists (for idempotency)
        ocr_path = OCR_DIR / f"{cid}.txt"
        if ocr_path.exists():
            # Reuse existing OCR
            with open(ocr_path, "r", encoding="utf-8") as f:
                ocr_text = f.read()
        else:
            # Perform OCR
            ocr_text = call_vlm_for_ocr(cid)
        
        # 阶段 2：技术分析评估
        analysis_result = call_llm_for_analysis(cid, ocr_text, original_filename)
        
        return analysis_result

    except Exception as e:
        print(f"Error analyzing resume: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/candidates/{candidate_id}/ocr")
async def get_ocr_content(candidate_id: str):
    """
    获取候选人的 OCR 文本内容（用于调试和审计）
    """
    ocr_path = OCR_DIR / f"{candidate_id}.txt"
    if not ocr_path.exists():
        raise HTTPException(status_code=404, detail="OCR content not found")
    
    with open(ocr_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    return {"candidate_id": candidate_id, "ocr_content": content}


@app.delete("/api/candidates/deduplicate")
async def deduplicate_candidates():
    """
    Remove duplicate candidates based on original filename.
    Keeps the most recent upload.
    """
    try:
        # Gather all candidates with their metadata
        candidates_map = {} # filename -> list of (timestamp, cid)
        
        for pdf_file in UPLOAD_DIR.glob("*.pdf"):
            cid = pdf_file.stem
            metadata_file = METADATA_DIR / f"{cid}.json"
            original_filename = "Unknown.pdf"
            
            if metadata_file.exists():
                try:
                    with open(metadata_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                        original_filename = meta.get("original_filename", original_filename)
                except:
                    pass
            
            # Get creation time
            ctime = pdf_file.stat().st_ctime
            
            if original_filename not in candidates_map:
                candidates_map[original_filename] = []
            candidates_map[original_filename].append({"cid": cid, "ctime": ctime})
        
        deleted_count = 0
        
        for filename, items in candidates_map.items():
            if len(items) > 1:
                # Sort by ctime descending (newest first)
                items.sort(key=lambda x: x["ctime"], reverse=True)
                
                # Keep the first one, delete the rest
                for item in items[1:]:
                    await delete_candidate(item["cid"])
                    deleted_count += 1
                    
        return {"status": "success", "deleted_count": deleted_count}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/candidates/{candidate_id}")
async def delete_candidate(candidate_id: str):
    """
    Delete a specific candidate and all associated data.
    """
    try:
        cid = candidate_id
        
        # Paths to remove
        paths_to_remove = [
            UPLOAD_DIR / f"{cid}.pdf",
            IMAGES_DIR / cid,
            RESULTS_DIR / f"{cid}.json",
            METADATA_DIR / f"{cid}.json",
            OCR_DIR / f"{cid}.txt"
        ]
        
        for path in paths_to_remove:
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                    
        return {"status": "success", "id": cid}
    except Exception as e:
        print(f"Error deleting candidate {candidate_id}: {e}")
        # Build a more robust error response or just 500
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/cache")
async def clear_cache():
    """
    Clear all data.
    """
    try:
        # Re-create directories
        for path in [UPLOAD_DIR, IMAGES_DIR, RESULTS_DIR, METADATA_DIR, OCR_DIR]:
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True, exist_ok=True)
        return {"status": "success", "message": "Cache cleared"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Serve index.html at root path
@app.get("/", include_in_schema=False)
async def read_index():
    return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)