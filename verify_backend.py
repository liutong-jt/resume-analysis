import urllib.request
import urllib.parse
import json
import time
from pathlib import Path
import mimetypes

BASE_URL = "http://localhost:8000"
TEST_PDF_PATH = Path("data/test.pdf")

# Create a dummy PDF if not exists
if not TEST_PDF_PATH.exists():
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(595, 842)
    pdf.save(TEST_PDF_PATH, version=17)

def upload_pdf(filename="test.pdf"):
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

def delete_req(path):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, method='DELETE')
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read().decode('utf-8'))

def test_ocr_storage():
    print("Testing OCR Storage...")
    try:
        data = upload_pdf("ocr_test.pdf")
        cid = data['id']
        ocr_path = Path(f"data/ocr/{cid}.txt")
        
        if ocr_path.exists():
            print(f"PASS: OCR file created at {ocr_path}")
        else:
            print(f"FAIL: OCR file not found at {ocr_path}")
        return cid
    except Exception as e:
        print(f"FAIL: Upload failed {e}")
        return None

def test_delete(cid):
    if not cid: return
    print(f"Testing Delete for {cid}...")
    try:
        res = delete_req(f"/api/candidates/{cid}")
        print("PASS: Delete request successful")
    except Exception as e:
        print(f"FAIL: Delete request failed {e}")

    # Verify removal
    if Path(f"data/ocr/{cid}.txt").exists():
        print("FAIL: OCR file still exists")
    else:
        print("PASS: OCR file removed")

def test_deduplicate():
    print("Testing Deduplicate...")
    try:
        # Upload twice
        data1 = upload_pdf("dup_test.pdf")
        time.sleep(1) # Ensure distinct timestamps
        data2 = upload_pdf("dup_test.pdf")
        
        print(f"Uploaded {data1['id']} and {data2['id']}")
        
        res = delete_req("/api/candidates/deduplicate")
        print(f"Deduplicate result: {res}")
        
        if res.get('deleted_count') == 1:
            print("PASS: Deduplicated 1 item")
        else:
            print(f"FAIL: Expected 1 deleted, got {res.get('deleted_count')}")

        # Check which one remains (should be data2, the newer one)
        if Path(f"data/ocr/{data2['id']}.txt").exists() and not Path(f"data/ocr/{data1['id']}.txt").exists():
             print("PASS: Newer item kept, older removed")
        else:
             # Check if data2 exists
             if not Path(f"data/ocr/{data2['id']}.txt").exists():
                 print("FAIL: Newer item removed")
             if Path(f"data/ocr/{data1['id']}.txt").exists():
                 print("FAIL: Older item kept")
             
    except Exception as e:
        print(f"FAIL: Deduplicate failed {e}")

if __name__ == "__main__":
    cid = test_ocr_storage()
    test_delete(cid)
    test_deduplicate()
