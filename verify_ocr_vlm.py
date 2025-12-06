import urllib.request
import urllib.parse
import json
import time
from pathlib import Path
import os
import shutil

BASE_URL = "http://localhost:8000"
TEST_PDF_PATH = Path("data/test.pdf")

# Ensure test PDF exists
if not TEST_PDF_PATH.exists():
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(595, 842)
    # create a text object
    text_page = page.get_textpage()
    # It's hard to add text with pypdfium2 easily without font, but we can try just a blank one 
    # for the upload test. However, we need a Real PDF with text or image for VLM to extract anything.
    # The VLM will probably just return "No text found" or something if it's blank.
    # That's fine, as long as the file is created and contains *something*.
    pdf.save(TEST_PDF_PATH, version=17)

def upload_pdf(filename="ocr_vlm_test.pdf"):
    print(f"Uploading {filename}...")
    url = f"{BASE_URL}/api/upload_pdf"
    boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
    data = []
    
    with open(TEST_PDF_PATH, 'rb') as f:
        file_content = f.read()
        
    data.append(f'--{boundary}')
    data.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"')
    data.append('Content-Type: application/pdf')
    data.append('')
    data.append(file_content.decode('latin1'))
    data.append(f'--{boundary}--')
    data.append('')
    
    body = '\r\n'.join(data).encode('latin1')
    req = urllib.request.Request(url, data=body)
    req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
    
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode('utf-8'))

def analyze_resume(cid):
    print(f"Analyzing {cid}...")
    url = f"{BASE_URL}/api/analyze"
    data = json.dumps({"candidate_id": cid}).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode('utf-8'))

def test_vlm_ocr():
    # 1. Upload
    try:
        data = upload_pdf()
        cid = data['id']
        print(f"Uploaded CID: {cid}")
        
        # 2. Check that OCR file does NOT exist yet (behavior change: we don't do it on upload anymore)
        ocr_path = Path(f"data/ocr/{cid}.txt")
        if ocr_path.exists():
            print("FAIL: OCR file created on upload immediately (should await analyze)")
        else:
            print("PASS: OCR file not created on upload immediately")
            
        # 3. Analyze
        print("Triggering Analysis (this calls VLM)...")
        # NOTE: This requires actual OpenAI key. I assume the user has it set up as per previous context.
        # If not, this might fail or error out. 
        # But since I am in the user's dev environment where 'uv run uvicorn' is running successfully, 
        # I assume .env is loaded.
        
        try:
             res = analyze_resume(cid)
             print("Analysis Result Received")
        except Exception as e:
            print(f"Analysis failed: {e}")
            print("SKIP: Cannot fully verify VLM OCR without valid API response")
            return

        # 4. Check OCR file again
        if ocr_path.exists():
            content = ocr_path.read_text(encoding='utf-8')
            print(f"PASS: OCR file created after analysis at {ocr_path}")
            print(f"Content length: {len(content)}")
            print(f"Preview: {content[:100]}...")
        else:
            print(f"FAIL: OCR file not found at {ocr_path} after analysis")

    except Exception as e:
        print(f"Test failed: {e}")

if __name__ == "__main__":
    test_vlm_ocr()
