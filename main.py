import os
import subprocess
import random
import string
import threading
import time
import re
import logging
from fastapi import FastAPI, APIRouter, Request, Form, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)
nekoinsta_router = APIRouter()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Directory to store downloads
DOWNLOAD_DIR = "download"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Dictionary to store scheduled deletions
scheduled_deletions = {}

class DownloadItem(BaseModel):
    filename: str
    local_path: str

def generate_random_string(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def rename_file(file_path):
    directory, filename = os.path.split(file_path)
    name, extension = os.path.splitext(filename)
    new_filename = f"NekoInsta_{generate_random_string()}{extension}"
    new_file_path = os.path.join(directory, new_filename)
    os.rename(file_path, new_file_path)
    return new_file_path

def delete_file_after_delay(file_path, delay=600):  # 600 seconds = 10 minutes
    def delete_task():
        time.sleep(delay)
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info(f"Deleted file: {file_path}")
        if file_path in scheduled_deletions:
            del scheduled_deletions[file_path]

    thread = threading.Thread(target=delete_task)
    thread.start()
    scheduled_deletions[file_path] = thread

def is_valid_instagram_url(url):
    # Updated regex pattern to support a wider range of Instagram URLs
    pattern = r'^https?://(?:www\.)?instagram\.com/(?:p|reel|stories)/(?:[\w-]+/)?[\w-]+(?:/[\w-]+)?(?:\?.*)?$'
    return re.match(pattern, url) is not None

@nekoinsta_router.get("/")
async def nekoinsta(request: Request):
    logger.info("Nekoinsta route accessed")
    return templates.TemplateResponse("nekoinsta.html", {"request": request})

@nekoinsta_router.post("/download")
@limiter.limit("5/minute")
async def download_post(request: Request, url: str = Form(...)):
    logger.info(f"Download request received for URL: {url}")
    if not is_valid_instagram_url(url):
        logger.warning(f"Invalid Instagram URL: {url}")
        return JSONResponse(content={"success": False, "error": "Invalid Instagram URL"})

    try:
        command = [
            "gallery-dl",
            url,
            "--cookies", "instagram.txt",
            "--directory", DOWNLOAD_DIR,
            "--range", "1-25"
        ]
        
        result = subprocess.run(command, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"gallery-dl command failed: {result.stderr}")
            raise Exception(f"gallery-dl command failed: {result.stderr}")
        
        file_paths = result.stdout.strip().split('\n')
        post_data = []

        for file_path in file_paths:
            if os.path.exists(file_path):
                new_file_path = rename_file(file_path)
                filename = os.path.basename(new_file_path)
                local_path = os.path.relpath(new_file_path, DOWNLOAD_DIR)
                post_data.append(DownloadItem(filename=filename, local_path=local_path))
                
                # Schedule file for deletion
                delete_file_after_delay(new_file_path)
                logger.info(f"File scheduled for deletion: {new_file_path}")

        logger.info(f"Download successful. {len(post_data)} files processed.")
        return JSONResponse(content={"success": True, "data": [item.dict() for item in post_data]})
    except Exception as e:
        logger.error(f"Error during download: {str(e)}")
        return JSONResponse(content={"success": False, "error": str(e)})

@nekoinsta_router.get("/preview/{filename:path}")
async def preview_file(filename: str):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(file_path):
        logger.info(f"Previewing file: {file_path}")
        return FileResponse(file_path)
    logger.warning(f"File not found for preview: {file_path}")
    return JSONResponse(content={"error": "File not found"}, status_code=404)

@nekoinsta_router.get("/download-file/{filename:path}")
async def download_file(filename: str):
    file_path = os.path.join(DOWNLOAD_DIR, filename)
    if os.path.exists(file_path):
        logger.info(f"Downloading file: {file_path}")
        return FileResponse(file_path, filename=filename)
    logger.warning(f"File not found for download: {file_path}")
    return JSONResponse(content={"error": "File not found"}, status_code=404)

app.mount("/static", StaticFiles(directory="static"), name="static")

# Include the nekoinsta router
app.include_router(nekoinsta_router, prefix="/nekoinsta")
logger.info("Nekoinsta router included")

@app.get("/")
async def read_root(request: Request):
    logger.info("Root route accessed")
    return templates.TemplateResponse("nekoinsta.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)