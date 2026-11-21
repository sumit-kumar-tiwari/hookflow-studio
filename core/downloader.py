import os
import re
import uuid
import subprocess
import requests
import yt_dlp

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).replace(" ", "_")

def extract_youtube_id(url: str) -> str:
    m = re.search(r'(?:youtu\.be/|youtube\.com/(?:embed/|v/|watch\?v=|watch\?.+&v=|shorts/))([\w-]{11})', url)
    return m.group(1) if m else None

def get_base_ydl_opts(output_dir: str) -> dict:
    """
    Returns rock-solid yt-dlp options configured for cloud datacenters (Render/AWS/etc.)
    and local setups.
    """
    opts = {
        'outtmpl': os.path.join(output_dir, '%(id)s.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 5,
        'fragment_retries': 5,
        'http_chunk_size': 10485760, # 10MB chunk size prevents throttling
        'geo_bypass': True,
        'nocheckcertificate': True,
        'extractor_args': {
            'youtube': {
                'player_client': ['tv_samsung', 'android', 'ios', 'web'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }
    if os.name == 'nt':
        opts['source_address'] = '0.0.0.0'
    return opts

def stream_download_file(url: str, out_path: str, progress_hook=None):
    """Downloads a file via streaming HTTP chunks with real-time progress callbacks."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    }
    with requests.get(url, stream=True, headers=headers, timeout=35) as r:
        r.raise_for_status()
        total_size = int(r.headers.get('content-length', 0))
        downloaded = 0
        chunk_size = 1024 * 1024 # 1MB chunks
        with open(out_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_hook and total_size > 0:
                        progress_hook({
                            'status': 'downloading',
                            'total_bytes': total_size,
                            'downloaded_bytes': downloaded
                        })
    if progress_hook:
        progress_hook({'status': 'finished'})

def download_via_invidious(video_id: str, output_dir: str, quality: str = "video_best", progress_hook=None) -> dict:
    """
    High-reliability fallback downloader using public Invidious instances.
    Bypasses datacenter bot blocks and extracts clean video and audio streams directly.
    """
    instances = [
        "https://inv.nadeko.net",
        "https://invidious.nerdvpn.de",
        "https://yewtu.be",
        "https://invidious.jing.rocks",
        "https://vid.priv.au"
    ]
    data = None
    for inst in instances:
        try:
            r = requests.get(f"{inst}/api/v1/videos/{video_id}", timeout=6)
            if r.status_code == 200:
                data = r.json()
                if data and "title" in data:
                    break
        except Exception:
            continue

    if not data:
        raise Exception(f"Video stream temporarily unavailable on cloud server for ID: {video_id}.")

    title = data.get("title", f"Video_{video_id}")
    clean_title = sanitize_filename(title)[:50]
    duration = data.get("lengthSeconds", 0)
    file_id = str(uuid.uuid4())[:8]

    is_audio = quality.startswith("audio_")
    out_ext = "mp3" if is_audio else "mp4"
    final_filename = f"{file_id}_{clean_title}.{out_ext}"
    final_path = os.path.join(output_dir, final_filename)

    # 1. Audio Download Mode
    if is_audio:
        stream_url = None
        audio_streams = [f for f in data.get("adaptiveFormats", []) if str(f.get("type", "")).startswith("audio/")]
        if audio_streams:
            audio_streams.sort(key=lambda x: int(x.get("bitrate") or 0), reverse=True)
            stream_url = audio_streams[0].get("url")
        elif data.get("formatStreams"):
            stream_url = data["formatStreams"][-1].get("url")

        if not stream_url:
            raise Exception("No audio stream available.")

        temp_audio = os.path.join(output_dir, f"{file_id}_raw_audio.tmp")
        stream_download_file(stream_url, temp_audio, progress_hook)

        bitrate = "320k" if "320" in quality else ("192k" if "192" in quality else "128k")
        cmd = ["ffmpeg", "-y", "-i", temp_audio, "-vn", "-b:a", bitrate, final_path]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if os.path.exists(temp_audio):
            try: os.remove(temp_audio)
            except: pass

        return {
            'file_path': os.path.abspath(final_path),
            'filename': final_filename,
            'title': title,
            'duration': duration,
            'filesize': os.path.getsize(final_path) if os.path.exists(final_path) else 0
        }

    # 2. Video Download Mode
    target_res = "1080p" if "1080" in quality or "best" in quality else ("720p" if "720" in quality else ("480p" if "480" in quality else "360p"))

    combined = [f for f in data.get("formatStreams", []) if f.get("container") == "mp4" or "mp4" in str(f.get("type", ""))]
    matched = None
    for f in combined:
        if target_res in str(f.get("resolution", "")) or target_res in str(f.get("qualityLabel", "")):
            matched = f
            break
    if not matched and combined:
        matched = combined[0]

    if matched and (target_res in ["720p", "480p", "360p"] or not data.get("adaptiveFormats")):
        stream_download_file(matched.get("url"), final_path, progress_hook)
    else:
        # Separate video + audio streams muxing
        video_streams = [f for f in data.get("adaptiveFormats", []) if "video/mp4" in str(f.get("type", ""))]
        if not video_streams:
            video_streams = [f for f in data.get("adaptiveFormats", []) if "video/" in str(f.get("type", ""))]

        v_url = None
        for v in video_streams:
            if target_res in str(v.get("resolution", "")) or target_res in str(v.get("qualityLabel", "")):
                v_url = v.get("url")
                break
        if not v_url and video_streams:
            v_url = video_streams[0].get("url")

        audio_streams = [f for f in data.get("adaptiveFormats", []) if "audio/" in str(f.get("type", ""))]
        a_url = audio_streams[0].get("url") if audio_streams else None

        if v_url and a_url:
            temp_v = os.path.join(output_dir, f"{file_id}_v.tmp")
            temp_a = os.path.join(output_dir, f"{file_id}_a.tmp")
            stream_download_file(v_url, temp_v, progress_hook)
            stream_download_file(a_url, temp_a, None)
            cmd = ["ffmpeg", "-y", "-i", temp_v, "-i", temp_a, "-c:v", "copy", "-c:a", "aac", final_path]
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            for t in [temp_v, temp_a]:
                if os.path.exists(t):
                    try: os.remove(t)
                    except: pass
        elif matched:
            stream_download_file(matched.get("url"), final_path, progress_hook)
        else:
            raise Exception("No suitable video stream found.")

    return {
        'file_path': os.path.abspath(final_path),
        'filename': final_filename,
        'title': title,
        'duration': duration,
        'filesize': os.path.getsize(final_path) if os.path.exists(final_path) else 0
    }

def download_video(url: str, output_dir: str, progress_hook=None) -> dict:
    """
    Downloads a video from YouTube or any supported platform via yt-dlp,
    with automatic fallback to Invidious stream resolver if yt-dlp is bot-blocked.
    """
    os.makedirs(output_dir, exist_ok=True)
    ydl_opts = get_base_ydl_opts(output_dir)
    ydl_opts['format'] = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
    ydl_opts['merge_output_format'] = 'mp4'

    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

            base, _ = os.path.splitext(filename)
            if os.path.exists(base + '.mp4'):
                filename = base + '.mp4'
            elif not os.path.exists(filename):
                for ext in ['.mp4', '.mkv', '.webm']:
                    if os.path.exists(base + ext):
                        filename = base + ext
                        break

            if not os.path.exists(filename):
                for f in os.listdir(output_dir):
                    if f.lower().endswith(('.mp4', '.mkv', '.webm')):
                        filename = os.path.join(output_dir, f)
                        break

            return {
                'video_path': os.path.abspath(filename),
                'title': info.get('title', 'Video'),
                'duration': info.get('duration', 0),
                'id': info.get('id', 'video')
            }
    except Exception as e:
        print(f"[HookFlow] yt-dlp download failed: {e}. Attempting stream fallback...")
        vid = extract_youtube_id(url)
        if vid:
            res = download_via_invidious(vid, output_dir, quality="video_720", progress_hook=progress_hook)
            return {
                'video_path': res['file_path'],
                'title': res['title'],
                'duration': res['duration'],
                'id': vid
            }
        raise e

def download_media_direct(url: str, output_dir: str, quality="video_1080", progress_hook=None) -> dict:
    """
    Direct media download for YouTube with flexible resolutions and formats:
    Uses primary yt-dlp with automatic high-availability Invidious fallback.
    """
    os.makedirs(output_dir, exist_ok=True)
    file_id = str(uuid.uuid4())[:8]
    out_template = os.path.join(output_dir, f"{file_id}_%(title)s.%(ext)s")

    ydl_opts = get_base_ydl_opts(output_dir)
    ydl_opts['outtmpl'] = out_template
    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    is_audio = quality.startswith("audio_")

    if quality in ["audio_320", "audio_mp3"]:
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '320',
        }]
    elif quality == "audio_192":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    elif quality == "audio_128":
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }]
    elif quality == "audio_m4a":
        ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'
    elif quality == "video_720":
        ydl_opts['format'] = 'bestvideo[height<=720]+bestaudio/best[height<=720]/best'
        ydl_opts['merge_output_format'] = 'mp4'
    elif quality == "video_480":
        ydl_opts['format'] = 'bestvideo[height<=480]+bestaudio/best[height<=480]/best'
        ydl_opts['merge_output_format'] = 'mp4'
    elif quality == "video_360":
        ydl_opts['format'] = 'bestvideo[height<=360]+bestaudio/best[height<=360]/best'
        ydl_opts['merge_output_format'] = 'mp4'
    else: # 1080p / best
        ydl_opts['format'] = 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
        ydl_opts['merge_output_format'] = 'mp4'

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)

            if is_audio and quality != "audio_m4a":
                base, _ = os.path.splitext(filename)
                filename = base + ".mp3"
            elif not is_audio:
                base, _ = os.path.splitext(filename)
                if os.path.exists(base + '.mp4'):
                    filename = base + '.mp4'

            if not os.path.exists(filename):
                base, _ = os.path.splitext(filename)
                for ext in ['.mp4', '.mp3', '.mkv', '.webm', '.m4a']:
                    if os.path.exists(base + ext):
                        filename = base + ext
                        break

            if not os.path.exists(filename):
                for f in os.listdir(output_dir):
                    if f.startswith(file_id) or f.lower().endswith(('.mp4', '.mp3', '.m4a', '.mkv', '.webm')):
                        filename = os.path.join(output_dir, f)
                        break

            return {
                'file_path': os.path.abspath(filename),
                'filename': os.path.basename(filename),
                'title': info.get('title', 'Media'),
                'duration': info.get('duration', 0),
                'filesize': os.path.getsize(filename) if os.path.exists(filename) else 0
            }
    except Exception as e:
        print(f"[HookFlow] yt-dlp direct failed: {e}. Trying secondary stream resolvers...")
        vid = extract_youtube_id(url)
        if vid:
            return download_via_invidious(vid, output_dir, quality=quality, progress_hook=progress_hook)
        raise e
