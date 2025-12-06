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

# Ensure directories exist
for path in [UPLOAD_DIR, IMAGES_DIR, RESULTS_DIR, METADATA_DIR]:
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
            # Render at 2x scale for better OCR visibility
            bitmap = page.render(scale=2.0)
            pil_image = bitmap.to_pil()

            image_name = f"page_{i}.png"
            image_path = output_dir / image_name
            pil_image.save(image_path, format="PNG")
            
            # Construct URL path relative to the mount point
            relative_path = f"/data/images/{output_dir.name}/{image_name}"
            image_urls.append(relative_path)

        return image_urls
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF processing error: {str(e)}")

SYSTEM_PROMPT = """
你是一位拥有20年经验的资深技术招聘专家。你的任务是阅读提供的一份或多份简历图像（属于同一个候选人），进行深度的技术评估，并以严格的 JSON 格式输出结构化报告。

**核心评估原则：**
- **严谨挖掘**：请仔细阅读简历的每一个细节。即使候选人提及的技术点较少，也要基于仅有的信息进行评估，尽量避免使用“未提供”、“无法评估”等结论。
- **推断能力**：如果候选人未明确说明“技术认知”，请通过其项目描述中的动作（如“优化”、“设计”、“重构”）来推断其思考深度。
- **公正性**：姓名提取失败不影响评估；无论背景如何，都应挖掘其技术亮点。

**你的分析步骤如下：**

1.  **全局阅读与基本信息提取**：阅读所有图像，提取候选人姓名（如果找不到明确的姓名，留空字符串即可）。
2.  **领域分类**：判断候选人的主要技术栈和项目经验最契合以下哪个领域，**必须**只选择一个最相关的：['Big Data', 'AI', 'Cloud', 'App Dev', 'Other']。
3.  **四维深度评估**：基于简历中的教育、项目、实习、技能描述，对以下四个维度进行评估。每个维度给出一个 1-10 的分数（10分最高），并提供 30 字以内的简短理由。

    **评估标准（请充分挖掘简历内容）：**
    * **技术基础 (tech_foundation)**：考察编程基础、算法理解、系统设计能力
        - **挖掘点**：列出的编程语言、熟悉的框架、大学修读的计算机课程、参与的GitHub开源项目等。哪怕只是列出技能清单，也算作有基础。
        - 评分：只要有相关计算机专业背景或列出了主流技术栈，分数不应低于3分。

    * **技术认知 (tech_cognition)**：考察对技术选择的理解、架构思维、问题解决方法
        - **挖掘点**：寻找“为了解决...使用了...”、“优化了...性能”、“设计了...模块”等描述。
        - **策略**：如果项目描述只是流水账，得分为基础分（3-4分）；如果有体现难点攻克和方案选型，酌情加分。尽量避免给出“无信息”的评价，而是指出“项目描述偏重业务实现，技术决策细节较少”等。

    * **技术潜力 (tech_potential)**：考察学习能力、成长速度、技术热情
        - **挖掘点**：从奖项、个人博客、参与开源、短时间内掌握多种技术、跨领域实践等方面判断。

    * **学习能力 (learning_ability)**：考察新技术掌握、文档阅读、问题解决
        - **挖掘点**：自学经历、考取的证书、在项目中应用新技术的经历。

4.  **一句话总结**：生成一个不超过 50 字的核心优势摘要。

**输出要求：**
* **只**输出 JSON 格式的数据。
* 确保输出的 JSON 可以被解析。
* 即使无法提取姓名，也必须完成完整的技术评估。

**JSON 结构模版：**
{
  "candidate_name": "姓名（如无法提取则使用空字符串）",
  "domain_classification": "领域 (Big Data/AI/Cloud/App Dev/Other)",
  "summary_one_liner": "一句话摘要",
  "evaluation_matrix": {
    "tech_foundation": { "score": 0, "reason": "理由" },
    "tech_cognition": { "score": 0, "reason": "理由" },
    "tech_potential": { "score": 0, "reason": "理由" },
    "learning_ability": { "score": 0, "reason": "理由" }
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
    try:
        cid = request.candidate_id
        candidate_images_dir = IMAGES_DIR / cid
        
        if not candidate_images_dir.exists():
             raise HTTPException(status_code=404, detail="Candidate images not found")

        # Load Metadata for filename fallback
        metadata_file = METADATA_DIR / f"{cid}.json"
        original_filename = "Resume"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    original_filename = meta.get("original_filename", "Resume")
            except:
                pass

        # Get API configuration
        api_key = os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model_name = os.getenv("OPENAI_MODEL_NAME", "gpt-4o")

        if not api_key:
            raise HTTPException(status_code=500, detail="OpenAI API key not configured")

        client = OpenAI(api_key=api_key, base_url=base_url)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [{"type": "text", "text": "这是候选人的简历图像，请分析："}]}
        ]

        # Read images from disk and convert to base64 for API call
        image_files = sorted(list(candidate_images_dir.glob("*.png")), key=lambda p: p.stem)
        
        for img_path in image_files:
            with open(img_path, "rb") as image_file:
                b64_str = base64.b64encode(image_file.read()).decode('utf-8')
                messages[1]["content"].append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64_str}"}
                })

        response = client.chat.completions.create(
            model=model_name,
            messages=messages,
            max_tokens=2000,
            response_format={ "type": "json_object" }
        )

        content = response.choices[0].message.content
        parsed_result = json.loads(content)

        # Process result with original filename fallback
        processed_result = process_evaluation_result(parsed_result, filename=original_filename)

        # Save result to disk
        result_path = RESULTS_DIR / f"{cid}.json"
        with open(result_path, "w", encoding='utf-8') as f:
            json.dump(processed_result, f, ensure_ascii=False, indent=2)

        return processed_result

    except Exception as e:
        print(f"Error analyzing resume: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/cache")
async def clear_cache():
    """
    Clear all data.
    """
    try:
        # Re-create directories
        for path in [UPLOAD_DIR, IMAGES_DIR, RESULTS_DIR, METADATA_DIR]:
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