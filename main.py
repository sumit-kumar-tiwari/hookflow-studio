import os
import uuid
import json
import shutil
import asyncio
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, BackgroundTasks, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.downloader import download_video, download_media_direct
from core.transcriber import transcribe_video
from core.ai_hooks import find_viral_hooks
from core.subtitles import generate_ass_subtitles
from core.video_processor import process_clip, get_video_duration, get_video_dimensions
from core.analytics import get_client_ip, log_visitor_event

app = FastAPI(title="HookFlow AI", description="Turn Long Videos into Viral Shorts in 1 Click")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
CLIPS_DIR = os.path.join(STORAGE_DIR, "clips")
UPLOADS_DIR = os.path.join(STORAGE_DIR, "uploads")
DOWNLOADS_DIR = os.path.join(STORAGE_DIR, "downloads")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
CONFIG_PATH = os.path.join(STORAGE_DIR, "config.json")

os.makedirs(CLIPS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Jobs tracking with persistence across server reloads
jobs = {}
JOBS_STATE_FILE = os.path.join(STORAGE_DIR, "temp", "jobs_state.json")

def save_jobs_state():
    try:
        os.makedirs(os.path.dirname(JOBS_STATE_FILE), exist_ok=True)
        with open(JOBS_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(jobs, f)
    except Exception:
        pass

def load_jobs_state():
    global jobs
    if os.path.exists(JOBS_STATE_FILE):
        try:
            with open(JOBS_STATE_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    jobs.update(saved)
        except Exception:
            pass

load_jobs_state()

def load_config() -> dict:
    today_str = datetime.now().strftime("%Y-%m-%d")
    default_cfg = {
        "gemini_key": "",
        "groq_key": "",
        "usage": {
            "date": today_str,
            "gemini_used_today": 0,
            "groq_used_today": 0,
            "total_clips_generated": 0
        }
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data.get("usage"), dict):
                    data["usage"] = {}
                # Rollover usage when date changes
                if data["usage"].get("date") != today_str:
                    data["usage"]["date"] = today_str
                    data["usage"]["gemini_used_today"] = 0
                    data["usage"]["groq_used_today"] = 0
                return data
        except Exception:
            return default_cfg
    return default_cfg

def save_config(cfg: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[HookFlow Config Save Error] {e}")

def record_api_call(engine: str, clips_count: int = 1):
    cfg = load_config()
    usage = cfg.setdefault("usage", {})
    if engine == "gemini":
        usage["gemini_used_today"] = usage.get("gemini_used_today", 0) + 1
    elif engine == "groq":
        usage["groq_used_today"] = usage.get("groq_used_today", 0) + 1
    usage["total_clips_generated"] = usage.get("total_clips_generated", 0) + clips_count
    save_config(cfg)

class URLJobRequest(BaseModel):
    url: str
    aspect_ratio: str = "9:16"
    subtitle_style: str = "hormozi"
    template: Optional[str] = "center_crop"
    gemini_key: Optional[str] = None
    groq_key: Optional[str] = None
    num_clips: int = 3
    mode: str = "clip" # "clip" or "caption_only"
    analysis_window: Optional[str] = "full" # "full", "first_15", "first_30"

def run_pipeline(job_id: str, video_path: str, is_local_upload: bool, options: dict):
    try:
        job = jobs[job_id]
        temp_dir = os.path.join(STORAGE_DIR, "temp", job_id)
        os.makedirs(temp_dir, exist_ok=True)
        is_caption_only = options.get("mode") == "caption_only"

        # Step 1: Ingestion
        if not is_local_upload:
            job["step"] = "Downloading YouTube video..."
            job["progress"] = 10
            save_jobs_state()

            def dl_hook(d):
                if d['status'] == 'downloading':
                    total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                    downloaded = d.get('downloaded_bytes', 0)
                    pct = int(downloaded / total * 100) if total > 0 else 40
                    job["step"] = f"Downloading video: {pct}%"
                    job["progress"] = min(28, 10 + int(pct * 0.18))
                    save_jobs_state()
                elif d['status'] == 'finished':
                    job["step"] = "Download complete. Extracting audio..."
                    job["progress"] = 30
                    save_jobs_state()

            dl_info = download_video(options["url"], temp_dir, progress_hook=dl_hook)
            video_path = dl_info["video_path"]
            duration = dl_info.get("duration", 0)
        else:
            job["step"] = "Processing uploaded video..."
            job["progress"] = 20
            save_jobs_state()
            duration = get_video_duration(video_path)

        if not duration or duration <= 0:
            duration = get_video_duration(video_path)

        # Handle analysis window (e.g. for long podcasts)
        aw = options.get("analysis_window", "full")
        max_duration = None
        if not is_caption_only:
            if aw == "first_15" and duration > 900:
                max_duration = 900.0
            elif aw == "first_30" and duration > 1800:
                max_duration = 1800.0

        effective_duration = max_duration if (max_duration and max_duration < duration) else duration
        dur_mins = round(effective_duration / 60, 1)

        if dur_mins > 15:
            job["step"] = f"Long video detected ({dur_mins} mins). Faster-Whisper preparing audio..."
        else:
            job["step"] = f"Video loaded ({dur_mins} mins). Faster-Whisper preparing audio..."
        job["progress"] = 32
        save_jobs_state()

        # Step 2: Speech-to-Text Transcription with LIVE SEGMENT STREAMING
        def on_transcribe_progress(pct, cur_s, tot_s):
            cur_m, cur_sec = int(cur_s // 60), int(cur_s % 60)
            tot_m, tot_sec = int(tot_s // 60), int(tot_s % 60)
            # Smoothly progress from 35% to 64% as whisper transcribes
            job["progress"] = min(64, 35 + int(pct * 0.29))
            job["step"] = f"Transcribing audio: {cur_m:02d}:{cur_sec:02d} / {tot_m:02d}:{tot_sec:02d} ({int(pct)}%)"
            save_jobs_state()

        transcript_data = transcribe_video(
            video_path=video_path,
            temp_dir=temp_dir,
            progress_callback=on_transcribe_progress,
            max_duration=max_duration
        )

        # Step 3: AI Hook Analysis or Full Duration for Caption-Only Mode
        if is_caption_only:
            job["step"] = "Synchronizing animated word subtitles..."
            job["progress"] = 65
            save_jobs_state()
            hooks = [{
                "start_time": 0.0,
                "end_time": duration,
                "hook_title": "Full Captioned Video",
                "viral_score": 10.0,
                "reason": "Complete video with synchronized word-by-word animated subtitles"
            }]
        else:
            job["step"] = "AI finding top viral hooks & retention spikes..."
            job["progress"] = 65
            save_jobs_state()
            
            cfg = load_config()
            gemini_key = options.get("gemini_key") or cfg.get("gemini_key")
            groq_key = options.get("groq_key") or cfg.get("groq_key")

            # Persist newly provided keys
            if options.get("gemini_key") and options.get("gemini_key") != cfg.get("gemini_key"):
                cfg["gemini_key"] = options["gemini_key"]
                save_config(cfg)
            if options.get("groq_key") and options.get("groq_key") != cfg.get("groq_key"):
                cfg["groq_key"] = options["groq_key"]
                save_config(cfg)

            hooks = find_viral_hooks(
                transcript_data=transcript_data,
                total_duration=effective_duration,
                gemini_key=gemini_key,
                groq_key=groq_key,
                num_clips=options.get("num_clips", 3)
            )

            # Record API usage
            engine_used = "gemini" if gemini_key else ("groq" if groq_key else "heuristic")
            record_api_call(engine_used, clips_count=len(hooks))

        # Step 4: Render Clips with crop & subtitles
        job["step"] = f"Rendering {len(hooks)} viral clips with 9:16 crop & animated subtitles..."
        job["progress"] = 72
        save_jobs_state()

        rendered_clips = []
        words = transcript_data.get("words", [])

        for i, hook in enumerate(hooks):
            job["step"] = f"Rendering clip {i+1} of {len(hooks)} (9:16 crop + karaoke subs)..."
            save_jobs_state()
            start_t = float(hook.get("start_time", 0))
            end_t = float(hook.get("end_time", start_t + 35))
            if end_t <= start_t:
                end_t = start_t + 35
            if is_caption_only and end_t > duration:
                end_t = duration

            clip_filename = f"{job_id}_clip_{i+1}.mp4"
            output_clip_path = os.path.join(CLIPS_DIR, clip_filename)

            # Get video dimensions for pixel-perfect subtitle resolution
            vid_w, vid_h = get_video_dimensions(video_path)
            chosen_aspect = options.get("aspect_ratio", "9:16")
            chosen_template = options.get("template", "center_crop")

            if chosen_aspect == "9:16" or chosen_template in ["center_crop", "blur_fill", "headline_top", "split_screen"]:
                res_w, res_h = 1080, 1920
            elif chosen_aspect == "1:1":
                res_w, res_h = 1080, 1080
            else:
                res_w, res_h = vid_w, vid_h

            # Generate subtitles (supports 'none' / 'off' option)
            ass_path = None
            sub_style = options.get("subtitle_style", "hormozi")
            if sub_style and sub_style not in ["none", "off", "clean"]:
                ass_path = os.path.join(temp_dir, f"sub_{i+1}.ass")
                generate_ass_subtitles(
                    words=words,
                    clip_start=start_t,
                    clip_end=end_t,
                    output_ass_path=ass_path,
                    style=sub_style,
                    res_x=res_w,
                    res_y=res_h
                )

            # Process FFmpeg cut + template layout + subtitle burn
            process_clip(
                input_video=video_path,
                output_video=output_clip_path,
                start_time=start_t,
                end_time=end_t,
                ass_subtitle_path=ass_path,
                aspect_ratio=chosen_aspect,
                template=chosen_template
            )

            title = "Your Captioned Video" if is_caption_only else hook.get("hook_title", f"Viral Moment #{i+1}")
            rendered_clips.append({
                "id": str(i + 1),
                "hook_title": title,
                "viral_score": hook.get("viral_score", 9.0),
                "reason": hook.get("reason", "High retention hook"),
                "duration": round(end_t - start_t, 1),
                "filename": clip_filename,
                "preview_url": f"/api/media/preview/{clip_filename}",
                "download_url": f"/api/media/download/{clip_filename}"
            })

            job["progress"] = min(95, 72 + int((i + 1) / len(hooks) * 23))
            save_jobs_state()

        # Cleanup temp files
        shutil.rmtree(temp_dir, ignore_errors=True)

        job["step"] = "Complete! Your viral shorts are ready."
        job["progress"] = 100
        job["status"] = "completed"
        job["clips"] = rendered_clips
        save_jobs_state()

    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        jobs[job_id]["step"] = f"Failed: {str(e)}"
        save_jobs_state()
        print(f"[HookFlow Pipeline Error] {e}")

@app.get("/", response_class=HTMLResponse)
def serve_index(request: Request, background_tasks: BackgroundTasks):
    headers_dict = dict(request.headers)
    client_ip = get_client_ip(headers_dict, request.client.host if request.client else "Unknown")
    user_agent = request.headers.get("user-agent", "")
    background_tasks.add_task(log_visitor_event, STORAGE_DIR, client_ip, "/", user_agent, headers_dict)

    index_file = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(index_file):
        with open(index_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>HookFlow Downloader is running.</h1>"

@app.get("/captions", response_class=HTMLResponse)
def serve_captions(request: Request, background_tasks: BackgroundTasks):
    headers_dict = dict(request.headers)
    client_ip = get_client_ip(headers_dict, request.client.host if request.client else "Unknown")
    user_agent = request.headers.get("user-agent", "")
    background_tasks.add_task(log_visitor_event, STORAGE_DIR, client_ip, "/captions", user_agent, headers_dict)

    captions_file = os.path.join(TEMPLATES_DIR, "captions.html")
    if os.path.exists(captions_file):
        with open(captions_file, "r", encoding="utf-8") as f:
            return f.read()
    return serve_index(request, background_tasks)

@app.get("/robots.txt", response_class=HTMLResponse)
def serve_robots():
    content = """User-agent: *
Allow: /
Disallow: /admin
Disallow: /api/

Sitemap: https://hookflow-studio.onrender.com/sitemap.xml
"""
    return HTMLResponse(content=content, media_type="text/plain")

@app.get("/sitemap.xml", response_class=HTMLResponse)
def serve_sitemap():
    content = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://hookflow-studio.onrender.com/</loc>
    <lastmod>2026-11-21</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>https://hookflow-studio.onrender.com/captions</loc>
    <lastmod>2026-11-21</lastmod>
    <changefreq>daily</changefreq>
    <priority>0.9</priority>
  </url>
</urlset>
"""
    return HTMLResponse(content=content, media_type="application/xml")

@app.get("/admin")
def admin_portal():
    """Admin convenience route: Redirects to home with the secret admin sheet modal opened."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/?admin=1")

@app.get("/admin/visitors")
def export_visitors_excel():
    """Downloads or exports the real-time visitor log directly as an Excel CSV file."""
    csv_path = os.path.join(STORAGE_DIR, "visitors_log.csv")
    if not os.path.exists(csv_path):
        os.makedirs(STORAGE_DIR, exist_ok=True)
        with open(csv_path, mode="w", newline="", encoding="utf-8-sig") as f:
            import csv
            writer = csv.writer(f)
            writer.writerow([
                "Timestamp (Date & Time)",
                "Country",
                "City",
                "State / Region",
                "Tool Opened",
                "IP Address",
                "Device / OS",
                "User Agent"
            ])
    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename="HookFlow_Visitors_Log.csv",
        headers={"Content-Disposition": "attachment; filename=HookFlow_Visitors_Log.csv"}
    )

class SaveConfigRequest(BaseModel):
    gemini_key: Optional[str] = None
    groq_key: Optional[str] = None

@app.get("/api/config/status")
def get_config_status():
    cfg = load_config()
    gemini_key = (cfg.get("gemini_key") or "").strip()
    groq_key = (cfg.get("groq_key") or "").strip()
    usage = cfg.get("usage", {})

    active_engine = "heuristic"
    daily_limit = 0
    used_today = 0

    if gemini_key:
        active_engine = "gemini"
        daily_limit = 1500 # Free daily requests on Gemini 2.0 Flash
        used_today = usage.get("gemini_used_today", 0)
    elif groq_key:
        active_engine = "groq"
        daily_limit = 14400 # Free daily requests on Groq
        used_today = usage.get("groq_used_today", 0)

    remaining = max(0, daily_limit - used_today) if daily_limit > 0 else "Unlimited (Heuristic Free)"

    masked_gemini = f"{gemini_key[:5]}...{gemini_key[-4:]}" if len(gemini_key) > 9 else ("Connected" if gemini_key else "")
    masked_groq = f"{groq_key[:5]}...{groq_key[-4:]}" if len(groq_key) > 9 else ("Connected" if groq_key else "")

    return {
        "has_gemini": bool(gemini_key),
        "has_groq": bool(groq_key),
        "gemini_key": gemini_key,
        "groq_key": groq_key,
        "masked_gemini": masked_gemini,
        "masked_groq": masked_groq,
        "active_engine": active_engine,
        "daily_limit": daily_limit,
        "used_today": used_today,
        "remaining_today": remaining,
        "total_clips_generated": usage.get("total_clips_generated", 0)
    }

@app.post("/api/config/save")
def save_api_keys_endpoint(req: SaveConfigRequest):
    cfg = load_config()
    if req.gemini_key is not None:
        cfg["gemini_key"] = req.gemini_key.strip()
    if req.groq_key is not None:
        cfg["groq_key"] = req.groq_key.strip()
    save_config(cfg)
    return {"status": "ok", "message": "API keys saved successfully!"}

class SaveSheetWebhookRequest(BaseModel):
    webhook_url: str

@app.get("/api/analytics/google-sheet-status")
def get_sheet_status():
    cfg = load_config()
    webhook = (cfg.get("google_sheet_webhook") or os.environ.get("GOOGLE_SHEET_WEBHOOK") or "").strip()
    return {
        "is_connected": bool(webhook),
        "masked_url": f"{webhook[:28]}...{webhook[-8:]}" if len(webhook) > 36 else (webhook if webhook else "")
    }

@app.post("/api/analytics/save-google-sheet")
def save_sheet_webhook(req: SaveSheetWebhookRequest):
    cfg = load_config()
    cfg["google_sheet_webhook"] = req.webhook_url.strip()
    save_config(cfg)
    return {"status": "ok", "message": "Google Sheet Webhook URL saved successfully!"}

@app.post("/api/analytics/test-google-sheet")
def test_sheet_connection(req: SaveSheetWebhookRequest):
    url = req.webhook_url.strip()
    if not url.startswith("http"):
        raise HTTPException(status_code=400, detail="Invalid Webhook URL. It must start with https://script.google.com")
    try:
        import requests
        test_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "country": "India (Test)",
            "city": "Connection Verified",
            "region": "Live Sheet OK",
            "tool": "YouTube Downloader (Test)",
            "page": "YouTube Downloader (Test)",
            "ip": "127.0.0.1",
            "device": "HookFlow Live Tester"
        }
        res = requests.post(url, json=test_data, timeout=8.0, allow_redirects=True)
        if res.status_code < 400:
            return {"status": "ok", "message": "Row successfully added to your Google Sheet!"}
        else:
            return {"status": "warning", "message": f"Google returned HTTP status {res.status_code}. Ensure 'Who has access' is set to 'Anyone'."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to connect to Google Sheet: {str(e)}")

@app.post("/api/jobs/create-url")
def create_job_from_url(req: URLJobRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "id": job_id,
        "status": "processing",
        "progress": 5,
        "step": "Queued for processing...",
        "clips": [],
        "error": None
    }
    save_jobs_state()
    background_tasks.add_task(
        run_pipeline,
        job_id=job_id,
        video_path="",
        is_local_upload=False,
        options=req.dict()
    )
    return {"job_id": job_id}

@app.post("/api/jobs/upload")
async def create_job_from_upload(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    aspect_ratio: str = Form("9:16"),
    subtitle_style: str = Form("hormozi"),
    template: str = Form("center_crop"),
    gemini_key: Optional[str] = Form(None),
    groq_key: Optional[str] = Form(None),
    num_clips: int = Form(3),
    mode: str = Form("clip"),
    analysis_window: str = Form("full")
):
    job_id = str(uuid.uuid4())[:8]
    ext = os.path.splitext(file.filename)[1] or ".mp4"
    save_path = os.path.join(UPLOADS_DIR, f"{job_id}{ext}")

    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    jobs[job_id] = {
        "id": job_id,
        "status": "processing",
        "progress": 5,
        "step": "File uploaded. Starting processing...",
        "clips": [],
        "error": None
    }
    save_jobs_state()

    options = {
        "aspect_ratio": aspect_ratio,
        "subtitle_style": subtitle_style,
        "template": template,
        "gemini_key": gemini_key,
        "groq_key": groq_key,
        "num_clips": int(num_clips),
        "mode": mode,
        "analysis_window": analysis_window
    }

    background_tasks.add_task(
        run_pipeline,
        job_id=job_id,
        video_path=save_path,
        is_local_upload=True,
        options=options
    )

    return {"job_id": job_id}

def format_bytes(size_bytes: float) -> str:
    if not size_bytes or size_bytes <= 0:
        return "Unknown"
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{int(size_bytes)} B"

def extract_format_sizes(info: dict) -> dict:
    duration = info.get("duration") or 0
    formats = info.get("formats") or []
    
    best_audio_bytes = 0
    for f in formats:
        if f.get("vcodec") == "none" and f.get("acodec") != "none":
            sz = f.get("filesize") or f.get("filesize_approx")
            if not sz and f.get("tbr") and duration:
                sz = int(f["tbr"] * 1000 / 8 * duration)
            if sz and sz > best_audio_bytes:
                best_audio_bytes = sz
                
    if not best_audio_bytes and duration:
        best_audio_bytes = int(128 * 1000 / 8 * duration)

    def get_stream_size(target_h: int, default_tbr: int) -> int:
        best_v_sz = 0
        found = False
        for f in formats:
            h = f.get("height")
            if h and h <= target_h:
                sz = f.get("filesize") or f.get("filesize_approx")
                if not sz and f.get("tbr") and duration:
                    sz = int(f["tbr"] * 1000 / 8 * duration)
                if sz and sz > best_v_sz:
                    best_v_sz = sz
                    found = True
        if not found and duration:
            best_v_sz = int(default_tbr * 1000 / 8 * duration)
        return best_v_sz

    sz_1080 = get_stream_size(1080, 2500) + best_audio_bytes
    sz_720 = get_stream_size(720, 1400) + best_audio_bytes
    sz_480 = get_stream_size(480, 700) + best_audio_bytes
    sz_360 = get_stream_size(360, 400) + best_audio_bytes

    sz_mp3_320 = int(320 * 1000 / 8 * duration) if duration else int(best_audio_bytes * 2.5)
    sz_mp3_192 = int(192 * 1000 / 8 * duration) if duration else int(best_audio_bytes * 1.5)
    sz_mp3_128 = int(128 * 1000 / 8 * duration) if duration else best_audio_bytes
    sz_m4a = best_audio_bytes

    return {
        "video_best": format_bytes(sz_1080),
        "video_720": format_bytes(sz_720),
        "video_480": format_bytes(sz_480),
        "video_360": format_bytes(sz_360),
        "audio_320": format_bytes(sz_mp3_320),
        "audio_192": format_bytes(sz_mp3_192),
        "audio_128": format_bytes(sz_mp3_128),
        "audio_m4a": format_bytes(sz_m4a),
        "default_size": format_bytes(sz_1080)
    }

@app.get("/api/youtube-meta")
def get_youtube_meta(url: str):
    """Fetches YouTube video title, thumbnail, duration, and calculated file sizes."""
    try:
        import yt_dlp
        ydl_opts = {'quiet': True, 'no_warnings': True, 'skip_download': True, 'source_address': '0.0.0.0'}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            sizes = extract_format_sizes(info)
            return {
                "title": info.get("title", "YouTube Video"),
                "thumbnail": info.get("thumbnail"),
                "duration": info.get("duration", 0),
                "channel": info.get("uploader") or info.get("channel", "YouTube Channel"),
                "id": info.get("id"),
                "sizes": sizes,
                "default_size": sizes.get("default_size", "Unknown")
            }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class DirectDownloadRequest(BaseModel):
    url: str
    quality: str = "video_best" # "video_best", "video_720", "audio_mp3"

def run_direct_download(job_id: str, url: str, quality: str):
    try:
        job = jobs[job_id]
        job["step"] = f"Connecting to YouTube & preparing {quality}..."
        job["progress"] = 15

        def dl_hook(d):
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
                downloaded = d.get('downloaded_bytes', 0)
                pct = int(downloaded / total * 100) if total > 0 else 50
                job["step"] = f"Downloading media: {pct}%"
                job["progress"] = min(92, 15 + int(pct * 0.77))
            elif d['status'] == 'finished':
                job["step"] = "Finalizing file..."
                job["progress"] = 95

        res = download_media_direct(url, DOWNLOADS_DIR, quality=quality, progress_hook=dl_hook)
        job["progress"] = 100
        job["status"] = "completed"
        job["step"] = "Download ready!"
        job["download_info"] = {
            "title": res["title"],
            "filename": res["filename"],
            "filesize": res["filesize"],
            "download_url": f"/api/media/get-download/{res['filename']}"
        }
    except Exception as e:
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(e)
        job["step"] = f"Download failed: {str(e)}"

@app.post("/api/downloader/start")
def start_direct_download(req: DirectDownloadRequest, background_tasks: BackgroundTasks):
    job_id = "dl_" + str(uuid.uuid4())[:8]
    jobs[job_id] = {
        "id": job_id,
        "status": "processing",
        "progress": 10,
        "step": "Initializing YouTube download...",
        "error": None
    }
    background_tasks.add_task(run_direct_download, job_id=job_id, url=req.url, quality=req.quality)
    return {"job_id": job_id}

@app.get("/api/media/get-download/{filename}")
def get_download_file(filename: str):
    file_path = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    media_type = "audio/mpeg" if filename.endswith(".mp3") else "video/mp4"
    return FileResponse(file_path, media_type=media_type, filename=filename)

@app.get("/api/jobs/{job_id}/status")
def get_job_status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return jobs[job_id]

@app.get("/api/media/preview/{clip_filename}")
def preview_video(clip_filename: str):
    clip_path = os.path.join(CLIPS_DIR, clip_filename)
    if not os.path.exists(clip_path):
        raise HTTPException(status_code=404, detail="Clip not found")
    return FileResponse(clip_path, media_type="video/mp4")

@app.get("/api/media/download/{clip_filename}")
def download_video_clip(clip_filename: str):
    clip_path = os.path.join(CLIPS_DIR, clip_filename)
    if not os.path.exists(clip_path):
        raise HTTPException(status_code=404, detail="Clip not found")
    return FileResponse(clip_path, media_type="video/mp4", filename=f"HookFlow_{clip_filename}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
