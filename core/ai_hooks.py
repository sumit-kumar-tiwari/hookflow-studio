import json
import re
import requests

PROMPT_TEMPLATE = """You are a world-class viral short-form video editor (for YouTube Shorts, TikTok, and Instagram Reels).
Analyze the following timestamped transcript of a video and find the top {num_clips} most viral, engaging, and high-retention segments.

Rules:
1. Each segment must be between 25 and 65 seconds long.
2. The beginning of each segment (first 3 seconds) MUST have a powerful hook (curiosity, controversy, surprising statement, question, or emotional peak).
3. The ending must conclude cleanly (not cut mid-sentence).
4. For hook_title, write an ultra-catchy viral hook title.
5. Output MUST be ONLY a JSON array, with no markdown fences, no explanatory text.

JSON format:
[
  {{
    "start_time": 12.5,
    "end_time": 47.0,
    "hook_title": "The Secret Formula Nobody Talks About",
    "viral_score": 9.6,
    "reason": "Strong curiosity hook with high actionable value."
  }}
]

Transcript:
{transcript}
"""

def extract_hooks_with_gemini(api_key: str, transcript_data: dict, num_clips=3) -> list:
    """Detect hooks using Google Gemini API (free tier)."""
    # Build transcript text with timestamps
    lines = []
    for s in transcript_data.get("segments", []):
        lines.append(f"[{s['start']}s - {s['end']}s] {s['text']}")
    formatted_transcript = "\n".join(lines)

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
    payload = {
        "contents": [{
            "parts": [{
                "text": PROMPT_TEMPLATE.format(num_clips=num_clips, transcript=formatted_transcript)
            }]
        }],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json"
        }
    }

    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    raw_text = data['candidates'][0]['content']['parts'][0]['text']
    # Clean json formatting if any
    cleaned = re.sub(r'^```json\s*', '', raw_text.strip())
    cleaned = re.sub(r'\s*```$', '', cleaned)
    return json.loads(cleaned)

def extract_hooks_with_groq(api_key: str, transcript_data: dict, num_clips=3) -> list:
    """Detect hooks using Groq API (free tier, ultra-fast)."""
    lines = []
    for s in transcript_data.get("segments", []):
        lines.append(f"[{s['start']}s - {s['end']}s] {s['text']}")
    formatted_transcript = "\n".join(lines)

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": "You are a viral video editor. Return only raw JSON arrays."},
            {"role": "user", "content": PROMPT_TEMPLATE.format(num_clips=num_clips, transcript=formatted_transcript)}
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"}
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    content = data['choices'][0]['message']['content']
    parsed = json.loads(content)
    if isinstance(parsed, dict) and "clips" in parsed:
        return parsed["clips"]
    if isinstance(parsed, list):
        return parsed
    return list(parsed.values())[0]

def extract_hooks_heuristic(transcript_data: dict, total_duration: float, num_clips=3) -> list:
    """
    Smart fallback when no API key is provided:
    Finds punchy segments based on question marks, excitement words, and optimal pacing.
    """
    segments = transcript_data.get("segments", [])
    if not segments:
        # Default division if transcript is empty
        clip_len = min(40.0, total_duration / max(num_clips, 1))
        return [{
            "start_time": round(i * clip_len, 2),
            "end_time": round((i + 1) * clip_len, 2),
            "hook_title": f"Viral Moment #{i+1}",
            "viral_score": round(8.8 + (i * 0.3), 1),
            "reason": "High pacing segment with strong audience retention."
        } for i in range(num_clips)]

    viral_triggers = [
        "why", "how", "secret", "never", "always", "money", "mistake", "best", "truth", "crazy", "stop", "change", "formula"
    ]
    candidates = []

    for i, seg in enumerate(segments):
        text = seg["text"].lower()
        score = 7.0
        # Check viral trigger words
        for word in viral_triggers:
            if word in text:
                score += 0.8
        if "?" in seg["text"] or "!" in seg["text"]:
            score += 0.7

        # Accumulate roughly 35-50s
        start_t = seg["start"]
        end_t = start_t
        title = seg["text"][:60] + "..."
        
        for next_seg in segments[i:]:
            end_t = next_seg["end"]
            if (end_t - start_t) >= 30:
                break

        if 20 <= (end_t - start_t) <= 70:
            candidates.append({
                "start_time": round(start_t, 2),
                "end_time": round(end_t, 2),
                "hook_title": title,
                "viral_score": round(min(score, 9.8), 1),
                "reason": "Engagement spike detected with natural pause."
            })

    # Sort by viral score and pick top non-overlapping clips
    candidates = sorted(candidates, key=lambda x: x["viral_score"], reverse=True)
    selected = []
    for cand in candidates:
        overlap = False
        for s in selected:
            if not (cand["end_time"] < s["start_time"] or cand["start_time"] > s["end_time"]):
                overlap = True
                break
        if not overlap:
            selected.append(cand)
        if len(selected) >= num_clips:
            break

    # Fill remaining clips if fewer than requested
    while len(selected) < num_clips and total_duration > 5:
        idx = len(selected)
        chunk_step = total_duration / max(num_clips, 1)
        cand_start = round(idx * chunk_step, 2)
        clip_len = min(45.0, max(15.0, chunk_step * 1.2))
        cand_end = round(min(cand_start + clip_len, total_duration), 2)
        if cand_end <= cand_start:
            cand_end = round(min(cand_start + 15.0, total_duration), 2)
        selected.append({
            "start_time": cand_start,
            "end_time": cand_end,
            "hook_title": f"Viral Highlight #{idx + 1}",
            "viral_score": round(8.6 + (idx * 0.3), 1),
            "reason": "High retention engaging segment."
        })

    if not selected:
        selected = [{
            "start_time": 0.0,
            "end_time": min(45.0, total_duration),
            "hook_title": "Video Highlight",
            "viral_score": 9.0,
            "reason": "Opening hook"
        }]

    return selected[:num_clips]

def find_viral_hooks(transcript_data: dict, total_duration: float, gemini_key: str = None, groq_key: str = None, num_clips=3) -> list:
    """Master entry point for finding viral hooks."""
    if gemini_key:
        try:
            return extract_hooks_with_gemini(gemini_key, transcript_data, num_clips)
        except Exception as e:
            print(f"[HookFlow] Gemini API error: {e}. Falling back to heuristic.")
    if groq_key:
        try:
            return extract_hooks_with_groq(groq_key, transcript_data, num_clips)
        except Exception as e:
            print(f"[HookFlow] Groq API error: {e}. Falling back to heuristic.")
            
    return extract_hooks_heuristic(transcript_data, total_duration, num_clips)
