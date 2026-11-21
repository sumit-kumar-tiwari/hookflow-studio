import os
import re
import uuid
import subprocess
import requests
import yt_dlp

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).replace(" ", "_")

def extract_youtube_id(url: str) -> str:
    if not url:
        return None
    url = url.strip()
    if re.match(r'^[\w-]{11}$', url):
        return url
    m = re.search(r'(?:v=|\/v\/|\/embed\/|\/shorts\/|\/live\/|youtu\.be\/)([\w-]{11})', url)
    return m.group(1) if m else None

def get_base_ydl_opts(output_dir: str) -> dict:
    """
    Returns rock-solid yt-dlp options configured for cloud datacenters (Render/AWS/etc.)
    and local setups. Uses iOS & Android clients which do NOT require a JS player or PO Token.
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
                'player_client': ['ios', 'android', 'android_vr', 'web'],
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
    with requests.get(url, stream=True, headers=headers, timeout=40) as r:
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

def download_via_cobalt(video_id: str, output_dir: str, quality: str = "video_best", progress_hook=None):
    """Fallback 1: High-speed Cobalt API Stream Resolver"""
    cobalt_endpoints = [
        "https://cobalt-backend.canine.tools/",
        "https://api.cobalt.tools/",
        "https://cobalt.kwiatekm.pl/"
    ]
    is_audio = quality.startswith("audio_")
    url = f"https://www.youtube.com/watch?v={video_id}"
    target_q = "1080" if "1080" in quality or "best" in quality else ("720" if "720" in quality else "480")
    payload = {
        "url": url,
        "videoQuality": target_q,
        "downloadMode": "audio" if is_audio else "auto",
        "audioFormat": "mp3" if is_audio else "best"
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "HookFlow/2.0"
    }
    for endpoint in cobalt_endpoints:
        try:
            r = requests.post(endpoint, json=payload, headers=headers, timeout=12)
            if r.status_code == 200:
                data = r.json()
                stream_url = data.get("url")
                if stream_url:
                    file_id = str(uuid.uuid4())[:8]
                    ext = "mp3" if is_audio else "mp4"
                    final_filename = f"{file_id}_video_{video_id}.{ext}"
                    final_path = os.path.join(output_dir, final_filename)
                    stream_download_file(stream_url, final_path, progress_hook)
                    return {
                        'file_path': os.path.abspath(final_path),
                        'filename': final_filename,
                        'title': f"YouTube Video ({video_id})",
                        'duration': 0,
                        'filesize': os.path.getsize(final_path) if os.path.exists(final_path) else 0
                    }
        except Exception:
            continue
    return None

def download_via_piped(video_id: str, output_dir: str, quality: str = "video_best", progress_hook=None):
    """Fallback 2: Piped API Stream Resolver"""
    piped_endpoints = [
        "https://pipedapi.kavin.rocks",
        "https://api.piped.privacydev.net",
        "https://piped-api.lunar.icu",
        "https://pipedapi.tokhmi.xyz"
    ]
    for endpoint in piped_endpoints:
        try:
            r = requests.get(f"{endpoint}/streams/{video_id}", timeout=10)
            if r.status_code == 200:
                data = r.json()
                title = sanitize_filename(data.get("title", f"video_{video_id}"))[:50]
                duration = data.get("duration", 0)
                file_id = str(uuid.uuid4())[:8]
                is_audio = quality.startswith("audio_")
                ext = "mp3" if is_audio else "mp4"
                final_filename = f"{file_id}_{title}.{ext}"
                final_path = os.path.join(output_dir, final_filename)

                if is_audio:
                    audio_streams = data.get("audioStreams", [])
                    if audio_streams:
                        audio_streams.sort(key=lambda x: int(x.get("bitrate") or 0), reverse=True)
                        stream_url = audio_streams[0].get("url")
                        temp_a = os.path.join(output_dir, f"{file_id}_a.tmp")
                        stream_download_file(stream_url, temp_a, progress_hook)
                        cmd = ["ffmpeg", "-y", "-i", temp_a, "-vn", "-b:a", "320k", final_path]
                        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                        if os.path.exists(temp_a):
                            try: os.remove(temp_a)
                            except: pass
                        return {
                            'file_path': os.path.abspath(final_path),
                            'filename': final_filename,
                            'title': data.get("title", "Audio"),
                            'duration': duration,
                            'filesize': os.path.getsize(final_path) if os.path.exists(final_path) else 0
                        }
                else:
                    video_streams = [s for s in data.get("videoStreams", []) if not s.get("videoOnly")]
                    if not video_streams:
                        video_streams = data.get("videoStreams", [])
                    if video_streams:
                        stream_url = video_streams[0].get("url")
                        stream_download_file(stream_url, final_path, progress_hook)
                        return {
                            'file_path': os.path.abspath(final_path),
                            'filename': final_filename,
                            'title': data.get("title", "Video"),
                            'duration': duration,
                            'filesize': os.path.getsize(final_path) if os.path.exists(final_path) else 0
                        }
        except Exception:
            continue
    return None

def download_via_invidious(video_id: str, output_dir: str, quality: str = "video_best", progress_hook=None):
    """Fallback 3: Invidious Instances Stream Resolver"""
    instances = [
        "https://inv.nadeko.net",
        "https://invidious.nerdvpn.de",
        "https://yewtu.be",
        "https://invidious.jing.rocks",
        "https://vid.priv.au"
    ]
    for inst in instances:
        try:
            r = requests.get(f"{inst}/api/v1/videos/{video_id}", timeout=8)
            if r.status_code == 200:
                data = r.json()
                if not data or "title" not in data:
                    continue
                title = sanitize_filename(data.get("title", f"video_{video_id}"))[:50]
                duration = data.get("lengthSeconds", 0)
                file_id = str(uuid.uuid4())[:8]
                is_audio = quality.startswith("audio_")
                ext = "mp3" if is_audio else "mp4"
                final_filename = f"{file_id}_{title}.{ext}"
                final_path = os.path.join(output_dir, final_filename)

                if is_audio:
                    audio_streams = [f for f in data.get("adaptiveFormats", []) if str(f.get("type", "")).startswith("audio/")]
                    if audio_streams:
                        audio_streams.sort(key=lambda x: int(x.get("bitrate") or 0), reverse=True)
                        stream_url = audio_streams[0].get("url")
                    elif data.get("formatStreams"):
                        stream_url = data["formatStreams"][-1].get("url")
                    else:
                        continue
                    temp_audio = os.path.join(output_dir, f"{file_id}_raw_audio.tmp")
                    stream_download_file(stream_url, temp_audio, progress_hook)
                    cmd = ["ffmpeg", "-y", "-i", temp_audio, "-vn", "-b:a", "320k", final_path]
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
                else:
                    combined = [f for f in data.get("formatStreams", []) if f.get("container") == "mp4" or "mp4" in str(f.get("type", ""))]
                    if combined:
                        stream_download_file(combined[0].get("url"), final_path, progress_hook)
                        return {
                            'file_path': os.path.abspath(final_path),
                            'filename': final_filename,
                            'title': title,
                            'duration': duration,
                            'filesize': os.path.getsize(final_path) if os.path.exists(final_path) else 0
                        }
        except Exception:
            continue
    return None

def download_fallback_engine(video_id: str, output_dir: str, quality: str = "video_best", progress_hook=None) -> dict:
    """Tries Cobalt, Piped, and Invidious fallbacks in sequential order."""
    # 1. Cobalt
    res = download_via_cobalt(video_id, output_dir, quality=quality, progress_hook=progress_hook)
    if res:
        return res
    # 2. Piped
    res = download_via_piped(video_id, output_dir, quality=quality, progress_hook=progress_hook)
    if res:
        return res
    # 3. Invidious
    res = download_via_invidious(video_id, output_dir, quality=quality, progress_hook=progress_hook)
    if res:
        return res

    raise Exception(f"Video stream temporarily unavailable on cloud server for ID: {video_id}. Please try another resolution.")

def download_video(url: str, output_dir: str, progress_hook=None) -> dict:
    """
    Downloads a video from YouTube or any supported platform via yt-dlp,
    with automatic multi-provider fallback.
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
            res = download_fallback_engine(vid, output_dir, quality="video_720", progress_hook=progress_hook)
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
    Uses primary yt-dlp with multi-tiered cloud stream resolvers (Cobalt, Piped, Invidious).
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
        print(f"[HookFlow] yt-dlp direct failed: {e}. Trying multi-tiered stream resolvers...")
        vid = extract_youtube_id(url)
        if vid:
            return download_fallback_engine(vid, output_dir, quality=quality, progress_hook=progress_hook)
        raise e
