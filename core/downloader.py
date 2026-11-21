import os
import re
import uuid
import yt_dlp

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name).replace(" ", "_")

def get_base_ydl_opts(output_dir: str) -> dict:
    """
    Returns rock-solid yt-dlp options configured for cloud datacenters (Render/AWS/etc.)
    and local setups:
    - Uses Android & iOS InnerTube player clients to bypass YouTube's datacenter bot checks
    - Sets realistic user-agent headers
    - Handles IPv4 routing
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

def download_video(url: str, output_dir: str, progress_hook=None) -> dict:
    """
    Downloads a video from YouTube or any supported platform via yt-dlp.
    Returns: dict with 'video_path', 'audio_path', 'title', 'duration', 'id'
    """
    os.makedirs(output_dir, exist_ok=True)
    ydl_opts = get_base_ydl_opts(output_dir)
    # Flexible format selection with automatic FFmpeg MP4 remuxing
    ydl_opts['format'] = 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best'
    ydl_opts['merge_output_format'] = 'mp4'

    if progress_hook:
        ydl_opts['progress_hooks'] = [progress_hook]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        
        # Ensure it has mp4/mkv/webm extension or find the merged mp4
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

def download_media_direct(url: str, output_dir: str, quality="video_1080", progress_hook=None) -> dict:
    """
    High-Quality direct media download for YouTube with flexible user-selected resolutions and formats:
    Options:
      - 'video_1080' / 'video_best': 1080p Full HD MP4
      - 'video_720': 720p HD MP4
      - 'video_480': 480p SD MP4
      - 'video_360': 360p Compact MP4
      - 'audio_320' / 'audio_mp3': 320kbps MP3 Audio
      - 'audio_192': 192kbps MP3 Audio
      - 'audio_128': 128kbps MP3 Audio
      - 'audio_m4a': Original AAC/M4A Audio
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
