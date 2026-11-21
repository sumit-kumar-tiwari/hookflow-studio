import os
import subprocess
import wave
import numpy as np

# Fix PyAV 14+ / 19+ incompatibility where metadata_errors was removed from av.open()
try:
    import av
    _orig_av_open = av.open
    def _safe_av_open(*args, **kwargs):
        kwargs.pop("metadata_errors", None)
        return _orig_av_open(*args, **kwargs)
    av.open = _safe_av_open
except Exception:
    pass

from faster_whisper import WhisperModel

_cached_model = None

def get_whisper_model(model_size="tiny.en", device="cpu"):
    global _cached_model
    if _cached_model is None:
        compute_type = "int8" if device == "cpu" else "float16"
        threads = min(os.cpu_count() or 8, 12)
        # Prioritize locally cached tiny.en first for instant offline loading (0 download delay)
        models_to_try = [model_size, "tiny.en", "tiny", "base"]
        for m in models_to_try:
            if not m:
                continue
            try:
                _cached_model = WhisperModel(
                    m,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=threads,
                    num_workers=1,
                    local_files_only=False
                )
                print(f"[HookFlow] Whisper model ready: {m} (threads={threads})")
                break
            except Exception as e:
                print(f"[HookFlow] Model {m} note: {e}. Trying fallback...")
        
        if _cached_model is None:
            _cached_model = WhisperModel("tiny.en", device=device, compute_type=compute_type, cpu_threads=threads)
    return _cached_model

def extract_audio(video_path: str, output_audio_path: str, max_duration: float = None) -> str:
    """Extract 16kHz mono audio from video using FFmpeg for optimal Whisper processing."""
    os.makedirs(os.path.dirname(output_audio_path), exist_ok=True)
    cmd = ["ffmpeg", "-y", "-i", video_path]
    if max_duration and max_duration > 0:
        cmd.extend(["-t", str(max_duration)])
    cmd.extend([
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        output_audio_path
    ])
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return output_audio_path

def transcribe_video(video_path: str, temp_dir: str, model_size="tiny.en", progress_callback=None, max_duration=None) -> dict:
    """
    Transcribes audio with word-level timestamps using faster-whisper.
    Supports real-time progress callbacks for live UI streaming.
    Returns:
      {
        'full_text': str,
        'words': [{'word': str, 'start': float, 'end': float}, ...],
        'segments': [{'text': str, 'start': float, 'end': float}, ...]
      }
    """
    audio_path = os.path.join(temp_dir, "extracted_audio.wav")
    extract_audio(video_path, audio_path, max_duration=max_duration)

    # Convert 16kHz mono WAV directly to float32 numpy array to bypass PyAV decoder
    try:
        with wave.open(audio_path, 'rb') as wf:
            frames = wf.readframes(wf.getnframes())
            audio_input = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    except Exception:
        audio_input = audio_path

    model = get_whisper_model(model_size=model_size)
    segments, info = model.transcribe(audio_input, word_timestamps=True, vad_filter=True)

    words_list = []
    segments_list = []
    full_text_parts = []
    total_duration = getattr(info, "duration", 0) or (len(audio_input) / 16000.0 if isinstance(audio_input, np.ndarray) else 1.0)
    last_reported_time = 0.0

    for seg in segments:
        seg_text = seg.text.strip()
        full_text_parts.append(seg_text)
        segments_list.append({
            "text": seg_text,
            "start": round(seg.start, 2),
            "end": round(seg.end, 2)
        })
        if seg.words:
            for w in seg.words:
                words_list.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 2),
                    "end": round(w.end, 2)
                })

        # Report live progress to callback
        if progress_callback and total_duration > 0:
            if seg.end - last_reported_time >= 3.0 or seg.end >= total_duration - 1:
                last_reported_time = seg.end
                pct = min(100.0, (seg.end / max(1.0, total_duration)) * 100.0)
                progress_callback(pct, seg.end, total_duration)

    # Final callback report at 100%
    if progress_callback and total_duration > 0:
        progress_callback(100.0, total_duration, total_duration)

    return {
        "full_text": " ".join(full_text_parts),
        "words": words_list,
        "segments": segments_list,
        "language": info.language
    }
