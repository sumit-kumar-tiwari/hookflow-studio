import os
import subprocess

def get_video_duration(video_path: str) -> float:
    """Gets accurate video duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        return float(res.stdout.strip())
    except Exception:
        return 60.0

def get_video_dimensions(video_path: str) -> tuple:
    """Gets video width and height via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0", video_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        w, h = res.stdout.strip().split("x")
        return int(w), int(h)
    except Exception:
        return 1080, 1920

def process_clip(
    input_video: str,
    output_video: str,
    start_time: float,
    end_time: float,
    ass_subtitle_path: str = None,
    aspect_ratio: str = "9:16",
    template: str = "center_crop"
) -> str:
    """
    Cuts video, applies layout template (Smart 9:16 Crop, Blurred Ambient Fill, Headline Frame, Split Duo, or Fit Original),
    and optionally burns formatted animated subtitles using FFmpeg.
    """
    os.makedirs(os.path.dirname(output_video), exist_ok=True)
    duration = max(0.5, end_time - start_time)

    has_subtitles = bool(ass_subtitle_path and os.path.exists(ass_subtitle_path))
    escaped_ass = os.path.abspath(ass_subtitle_path).replace("\\", "/").replace(":", "\\:") if has_subtitles else None

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_time),
        "-t", str(duration),
        "-i", input_video,
    ]

    # Template layout modes
    if template == "blur_fill":
        if has_subtitles:
            fc = f"[0:v]split[v1][v2];[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.15[bg];[v2]scale=1080:-2:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[outv];[outv]ass='{escaped_ass}'"
        else:
            fc = f"[0:v]split[v1][v2];[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5,eq=brightness=-0.15[bg];[v2]scale=1080:-2:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2"
        cmd.extend(["-filter_complex", fc])

    elif template == "headline_top":
        if has_subtitles:
            fc = f"[0:v]split[v1][v2];[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:8,eq=brightness=-0.25[bg];[v2]scale=1020:-2:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2+30[outv];[outv]ass='{escaped_ass}'"
        else:
            fc = f"[0:v]split[v1][v2];[v1]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=30:8,eq=brightness=-0.25[bg];[v2]scale=1020:-2:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2+30"
        cmd.extend(["-filter_complex", fc])

    elif template == "split_screen":
        if has_subtitles:
            fc = f"[0:v]split[v1][v2];[v1]crop=iw:ih/2:0:0,scale=1080:960[top];[v2]crop=iw:ih/2:0:ih/2,scale=1080:960[bot];[top][bot]vstack[outv];[outv]ass='{escaped_ass}'"
        else:
            fc = f"[0:v]split[v1][v2];[v1]crop=iw:ih/2:0:0,scale=1080:960[top];[v2]crop=iw:ih/2:0:ih/2,scale=1080:960[bot];[top][bot]vstack"
        cmd.extend(["-filter_complex", fc])

    else:
        # Standard filters (center_crop, 1:1, fit_original)
        filters = []
        if aspect_ratio == "original":
            filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        elif aspect_ratio == "9:16":
            filters.append("crop='w=min(iw\\,trunc(ih*9/16/2)*2):h=min(ih\\,trunc(iw*16/9/2)*2)'")
            filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        elif aspect_ratio == "1:1":
            filters.append("crop='w=min(iw\\,ih):h=min(iw\\,ih)'")
            filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        else:
            filters.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")

        if has_subtitles:
            filters.append(f"ass='{escaped_ass}'")

        if filters:
            cmd.extend(["-vf", ",".join(filters)])

    cmd.extend([
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        output_video
    ])

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        print(f"[HookFlow] Video processor filter warning: {result.stderr[-200:]}. Running basic fallback cut...")
        fallback_cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time),
            "-t", str(duration),
            "-i", input_video,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-movflags", "+faststart",
            output_video
        ]
        subprocess.run(fallback_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    return output_video
