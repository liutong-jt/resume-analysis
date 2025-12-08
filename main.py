import shutil
import uuid
import os
import base64
import json
from pathlib import Path
from typing import List, Optional, Dict
from dotenv import load_dotenv
import pypdfium2 as pdfium
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from openai import AsyncOpenAI

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

APP_PASSWORD = os.getenv("APP_PASSWORD", "999888777")
PASSWORD_COOKIE_NAME = "resume_evalate_password"
PROTECTED_PATH_PREFIXES = ("/api", "/data")
EXCLUDED_PATHS = {"/api/login"}
SESSION_MAX_AGE = 3 * 24 * 60 * 60  # 3 days

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


@app.middleware("http")
async def enforce_password(request: Request, call_next):
    """Require the shared password for API and resume assets."""
    path = request.url.path

    if path in EXCLUDED_PATHS:
        return await call_next(request)

    if not any(path.startswith(prefix) for prefix in PROTECTED_PATH_PREFIXES):
        return await call_next(request)

    provided_password = (
        request.headers.get("x-app-password")
        or request.cookies.get(PASSWORD_COOKIE_NAME)
        or request.query_params.get("password")
    )

    if provided_password != APP_PASSWORD:
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    response = await call_next(request)

    if (
        request.query_params.get("password") == APP_PASSWORD
        and request.cookies.get(PASSWORD_COOKIE_NAME) != APP_PASSWORD
    ):
        response.set_cookie(
            PASSWORD_COOKIE_NAME,
            APP_PASSWORD,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
        )

    return response

# ----------------- Models -----------------

class AnalyzeRequest(BaseModel):
    candidate_id: str


class LoginRequest(BaseModel):
    password: str

class ReanalyzeRequest(BaseModel):
    candidate_ids: Optional[List[str]] = None

# ----------------- Helper Functions -----------------

def get_openai_settings() -> Dict[str, str]:
    """
    Resolve OpenAI credentials and model config once so coroutine calls can share it.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")

    return {
        "api_key": api_key,
        "base_url": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "ocr_model": os.getenv("OPENAI_OCR_MODEL_NAME", "gpt-4o"),
        "analysis_model": os.getenv("OPENAI_MODEL_NAME", "gpt-4o"),
    }

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
你是一位以“技术洁癖”和“犀利直觉”著称的骨灰级技术 CTO。你阅人无数，对简历中的“水分”、“关键词堆砌”和“培训班味”有生理性厌恶。你的目标不是为了“过”简历，而是为了**排雷**。你需要透过简历的文字表象，像法医一样解剖候选人的真实工程能力。

**你的核心思维模式（Mental Model）：**
1.  **拒绝名词崇拜**：候选人写 `Kubernetes`，你要判断他是只会 `kubectl apply` 的操作员，还是懂 CNI 网络插件和调度策略的工程师。没有细节支撑的高大上名词，一律视为“仅听说过”。
2.  **寻找“痛苦的记忆”**：真正的工程师在简历中会流露出解决难题后的自豪感（如“排查了一周的内存泄漏”）。只写“参与开发XXX模块”而没有难点描述的，通常是混日子的 CRUD Boy。
3.  **决策 > 行为**：我不在乎他“做了什么”，我在乎他“为什么这么做”。为什么要选 MongoDB 而不是 MySQL？为什么要用 MQ 而不是直接调用？没有体现决策过程的经历价值极低。
4.  **必须有结论**：严禁模棱两可。如果信息太少，就直接判定为“总结能力差”或“经历平庸”，并给出低分。

---

**评分校准标准（Scoring Anchors）：**
*为了保证评估的一致性，请严格对照以下标准打分（1-10分）：*

**1. 技术基础 (Tech Foundation)**
* **[1-3分]**：技术栈混乱（如同时写精通Java/Python/Go但项目全是Demo），无CS基础（不懂网络/OS），培训班痕迹重。
* **[4-6分]**：标准“码农”，熟练使用主流框架（Spring/Vue等）完成业务，但对底层原理（如JVM GC、数据库索引）仅限于背诵八股文。
* **[7-8分]**：基础扎实，能阅读源码，在项目中体现出对数据结构、算法或设计模式的合理运用，有Github高质量提交。
* **[9-10分]**：ACM/ICPC获奖，核心开源项目Contributor，或在项目中手写过底层中间件/框架核心逻辑。

**2. 技术认知 (Tech Cognition)**
* **[1-3分]**：纯执行者，只管功能实现，不管性能和扩展性，简历满屏“实现了增删改查”。
* **[4-6分]**：有基本的优化意识（如“加了Redis缓存加速”），但缺乏深度的权衡思考（如“缓存一致性怎么解”）。
* **[7-8分]**：具备架构思维，能清晰描述技术选型的Trade-off（权衡），解决了具体的高并发/高可用/数据一致性痛点。
* **[9-10分]**：具备系统设计（System Design）的全局观，能从业务推导架构，有处理“降级、熔断、异地多活”等复杂场景的实战经验。

**3. 技术潜力 (Tech Potential)**
* **[1-3分]**：多年工作经验但技术栈停滞不前，项目重复度极高。
* **[4-6分]**：按部就班成长，符合当前年限的预期水平。
* **[7-8分]**：成长曲线陡峭，短时间内掌握跨领域技术，或在项目中承担了超出职级（Owner）的责任。
* **[9-10分]**：极客精神，有技术博客/专利，对新技术有极强的敏感度和落地能力，能定义最佳实践。

**4. 学习能力 (Learning Ability)**
* **[1-3分]**：依赖百度/CSDN解决问题，简历中看不出主动学习痕迹。
* **[4-6分]**：能通过官方文档学习新技术，能够适应业务需求的技术变更。
* **[7-8分]**：深入底层原理，不仅知其然还知其所以然，有解决 "Unknown Unknowns" (未知的未知) 问题的经历。
* **[9-10分]**：具备跨学科/跨语言的快速迁移能力，不仅自己学得快，还能通过分享/Mentorship 提升团队技术水位。

---

**输出任务要求：**

请严格按照以下 JSON 格式输出评估报告。在 `interview_questions` 部分，你必须化身为面试官的**最强辅助**，提供的参考答案必须足够详细，包含**核心概念解释**、**作弊/浅层回答（Red Flags）**以及**专家级回答（Gold Standard）**，以便让我（非全栈精通者）能准确判断候选人水平。

**JSON 结构模版：**
```json
{
  "candidate_name": "姓名（无则空）",
  "domain_classification": "领域（Big Data / AI / Cloud / App Dev / Other）",
  "summary_one_liner": "一句话毒舌或赞赏评价，直击灵魂",
  "evaluation_matrix": {
    "tech_foundation": {
      "score": 0,
      "reason": "60字以内犀利点评，依据Scoring Anchors"
    },
    "tech_cognition": {
      "score": 0,
      "reason": "60字以内犀利点评，指出他是搬砖的还是盖楼的"
    },
    "tech_potential": {
      "score": 0,
      "reason": "60字以内犀利点评，预测他的上限"
    },
    "learning_ability": {
      "score": 0,
      "reason": "60字以内犀利点评，看他解决未知问题的能力"
    }
  },
  "interview_questions": [
    {
      "question": "针对简历中具体项目或技术点的犀利追问（越具体越好）",
      "context_for_interviewer": "简述为什么要问这个问题，考察什么核心能力（如：并发安全、架构设计、底层原理）",
      "reference_answer_detailed": "【核心原理】：通俗解释该技术点。\n【🚩警惕信号(Red Flags)】：如果候选人只回答了X，说明他只是背题/没做过。\n【🏆满分回答(Gold Standard)】：候选人应该提到Y和Z，并解释权衡过程。"
    },
    {
      "question": "问题2...",
      "context_for_interviewer": "...",
      "reference_answer_detailed": "..."
    },
    {
      "question": "问题3...",
      "context_for_interviewer": "...",
      "reference_answer_detailed": "..."
    }
  ],
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

    # Ensure interview_questions exists and is a list
    if 'interview_questions' not in result or not isinstance(result['interview_questions'], list):
        result['interview_questions'] = []

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

async def call_vlm_for_ocr(
    candidate_id: str,
    client: Optional[AsyncOpenAI] = None,
    model_name: Optional[str] = None,
) -> str:
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
    
    settings = None
    if client is None or model_name is None:
        settings = get_openai_settings()
    if client is None:
        client = AsyncOpenAI(api_key=settings["api_key"], base_url=settings["base_url"])
    if model_name is None:
        model_name = settings["ocr_model"]
    
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
    response = await client.chat.completions.create(
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

async def call_llm_for_analysis(
    candidate_id: str,
    ocr_text: str,
    original_filename: str = "Resume",
    client: Optional[AsyncOpenAI] = None,
    model_name: Optional[str] = None,
) -> dict:
    """
    第二次 LLM 调用：基于 OCR 文本进行技术分析评估
    
    Args:
        candidate_id: 候选人 UUID
        ocr_text: OCR 提取的文本内容
        original_filename: 原始文件名（用于候选人姓名的fallback）
        
    Returns:
        dict: 分析结果的 JSON 对象
    """
    settings = None
    if client is None or model_name is None:
        settings = get_openai_settings()
    if client is None:
        client = AsyncOpenAI(api_key=settings["api_key"], base_url=settings["base_url"])
    if model_name is None:
        model_name = settings["analysis_model"]
    
    # Construct messages for analysis
    messages = [
        {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
        {"role": "user", "content": f"以下是候选人的简历文本内容，请进行深度技术评估：\n\n{ocr_text}"}
    ]
    
    # Call LLM for analysis
    response = await client.chat.completions.create(
        model=model_name,
        messages=messages,
        max_tokens=5000,
        response_format={"type": "json_object"},
        extra_body={"reasoning": {"enabled": True}}
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


@app.post("/api/login")
async def login(request: LoginRequest):
    """Validate password and issue a session cookie."""
    if request.password != APP_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")

    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        PASSWORD_COOKIE_NAME,
        APP_PASSWORD,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return response

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

        # 统一初始化异步 OpenAI 客户端，两个阶段可以共享连接
        openai_settings = get_openai_settings()
        async_client = AsyncOpenAI(
            api_key=openai_settings["api_key"],
            base_url=openai_settings["base_url"]
        )

        # 阶段 1：OCR 文本提取
        # Check if OCR already exists (for idempotency)
        ocr_path = OCR_DIR / f"{cid}.txt"
        if ocr_path.exists():
            # Reuse existing OCR
            with open(ocr_path, "r", encoding="utf-8") as f:
                ocr_text = f.read()
        else:
            # Perform OCR
            ocr_text = await call_vlm_for_ocr(
                cid,
                client=async_client,
                model_name=openai_settings["ocr_model"]
            )
        
        # 阶段 2：技术分析评估
        analysis_result = await call_llm_for_analysis(
            cid,
            ocr_text,
            original_filename,
            client=async_client,
            model_name=openai_settings["analysis_model"]
        )
        
        return analysis_result

    except Exception as e:
        print(f"Error analyzing resume: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/candidates/reanalyze")
async def reanalyze_candidates(request: Optional[ReanalyzeRequest] = None):
    """
    根据已存在的 OCR 文本重新评估一个或多个候选人。
    若未提供 candidate_ids，则对所有存在 OCR 结果的候选人执行评估。
    """
    target_ids = []
    if request and request.candidate_ids:
        target_ids = [cid for cid in request.candidate_ids if cid]
    else:
        target_ids = sorted([path.stem for path in OCR_DIR.glob("*.txt")])

    if not target_ids:
        return {
            "status": "success",
            "requested": 0,
            "updated": 0,
            "errors": [],
            "message": "No OCR records available for re-analysis"
        }

    openai_settings = get_openai_settings()
    async_client = AsyncOpenAI(
        api_key=openai_settings["api_key"],
        base_url=openai_settings["base_url"]
    )

    summary = {
        "status": "success",
        "requested": len(target_ids),
        "updated": 0,
        "errors": []
    }

    for cid in target_ids:
        ocr_path = OCR_DIR / f"{cid}.txt"
        if not ocr_path.exists():
            summary["errors"].append({
                "candidate_id": cid,
                "error": "OCR content not found"
            })
            continue

        try:
            with open(ocr_path, "r", encoding="utf-8") as f:
                ocr_text = f.read().strip()
        except Exception as exc:
            summary["errors"].append({
                "candidate_id": cid,
                "error": f"Failed to read OCR text: {exc}"
            })
            continue

        if not ocr_text:
            summary["errors"].append({
                "candidate_id": cid,
                "error": "OCR content is empty"
            })
            continue

        original_filename = "Resume"
        metadata_file = METADATA_DIR / f"{cid}.json"
        if metadata_file.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                    original_filename = meta.get("original_filename", original_filename)
            except Exception:
                pass

        try:
            await call_llm_for_analysis(
                cid,
                ocr_text,
                original_filename,
                client=async_client,
                model_name=openai_settings["analysis_model"]
            )
            summary["updated"] += 1
        except Exception as exc:
            summary["errors"].append({
                "candidate_id": cid,
                "error": str(exc)
            })

    return summary

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
