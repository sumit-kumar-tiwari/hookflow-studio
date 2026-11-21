import os

def format_ass_time(seconds: float) -> str:
    """Convert seconds float to ASS timestamp format: H:MM:SS.cs"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"

def smart_chunk_words(words_list: list, target_words=3, max_gap=0.28, max_duration=2.2) -> list:
    """
    Groups words into short, high-retention viral phrases based on:
    1. Speech silence gaps (gap >= max_gap) -> cuts chunk so subtitle cleanly disappears during speech pauses
    2. Sentence end punctuation (. ? ! :) -> cuts chunk immediately so sentences don't spill
    3. Natural phrase commas -> cuts chunk if 2+ words or if followed by pause
    4. Target words (3 words typical, 4 max if sentence finishes)
    """
    chunks = []
    current_chunk = []

    for i, w in enumerate(words_list):
        current_chunk.append(w)
        w_text = str(w["word"]).strip()

        if i == len(words_list) - 1:
            chunks.append(current_chunk)
            break

        next_w = words_list[i + 1]
        gap = float(next_w["start"]) - float(w["end"])
        chunk_dur = float(w["end"]) - float(current_chunk[0]["start"])

        is_silence_gap = gap >= max_gap
        is_sentence_end = any(w_text.endswith(p) for p in [".", "?", "!", ":"])
        is_comma_break = w_text.endswith(",") and (len(current_chunk) >= 2 or gap >= 0.20)

        next_text = str(next_w["word"]).strip()
        next_ends = any(next_text.endswith(p) for p in [".", "?", "!", ":"])
        can_absorb = (
            len(current_chunk) == target_words
            and next_ends
            and (gap < max_gap)
            and (chunk_dur + float(next_w["end"]) - float(next_w["start"]) < max_duration)
        )

        is_max_len = len(current_chunk) >= 4 or (len(current_chunk) >= target_words and not can_absorb)
        is_max_dur = chunk_dur >= max_duration

        if is_silence_gap or is_sentence_end or is_comma_break or is_max_len or is_max_dur:
            chunks.append(current_chunk)
            current_chunk = []

    # Merge isolated single-word fragments only if gap is tiny (< 0.18s)
    refined = []
    for c in chunks:
        if len(c) == 1 and refined:
            prev = refined[-1]
            prev_end = float(prev[-1]["end"])
            cur_start = float(c[0]["start"])
            if (cur_start - prev_end) < 0.18 and len(prev) <= 2:
                refined[-1] = prev + c
                continue
        refined.append(c)

    return refined

def generate_ass_subtitles(
    words: list,
    clip_start: float,
    clip_end: float,
    output_ass_path: str,
    style="hormozi",
    res_x=1080,
    res_y=1920
):
    """
    Generates ASS subtitle with synchronized word-by-word active highlight animation.
    As each word is spoken, it lights up in the vibrant style highlight color while
    the rest of the phrase remains in crisp base color.
    When speech stops, captions cleanly disappear.
    """
    if not style or style in ["none", "off", "clean"]:
        return None

    if not words:
        return None

    # Filter words within clip range and compute relative timestamps
    clip_words = []
    for w in words:
        w_start = float(w["start"])
        w_end = float(w["end"])
        if w_start >= (clip_start - 0.05) and w_start < clip_end:
            rel_start = max(0.0, w_start - clip_start)
            rel_end = max(rel_start + 0.08, w_end - clip_start)
            if rel_end > (clip_end - clip_start + 0.1):
                rel_end = clip_end - clip_start
            clip_words.append({
                "word": str(w["word"]).strip().upper() if style in ["hormozi", "beast", "fire", "cyber"] else str(w["word"]).strip(),
                "start": rel_start,
                "end": rel_end
            })

    if not clip_words:
        return None

    # Dynamic layout and font sizing proportional to canvas height
    if res_y >= 1500: # 9:16 vertical 1080x1920
        font_size = 64 if style != "minimal" else 52
        margin_v = 330
        outline = 7 if style != "minimal" else 4
        shadow = 3 if style != "minimal" else 1
    elif res_y >= 1000: # 1080p horizontal or 1:1 square (1080x1080)
        font_size = int(res_y * 0.048) if style != "minimal" else int(res_y * 0.040)
        margin_v = int(res_y * 0.16)
        outline = 5 if style != "minimal" else 3
        shadow = 2 if style != "minimal" else 1
    else: # 720p or smaller
        font_size = int(res_y * 0.054) if style != "minimal" else int(res_y * 0.044)
        margin_v = int(res_y * 0.15)
        outline = 4 if style != "minimal" else 2
        shadow = 2 if style != "minimal" else 1

    # Style definitions (ASS Colors: &HAABBGGRR)
    if style == "hormozi":
        # Viral Yellow highlight: &H0000FFFF
        highlight_color = "&H0000FFFF"
        base_color = "&H00FFFFFF"
        outline_color = "&H00000000"
    elif style == "beast":
        # Neon Green highlight: &H0000FF55
        highlight_color = "&H0000FF55"
        base_color = "&H00FFFFFF"
        outline_color = "&H00000000"
    elif style == "fire":
        # Fire Red highlight: &H003344FF
        highlight_color = "&H003344FF"
        base_color = "&H00FFFFFF"
        outline_color = "&H00000000"
    elif style == "cyber":
        # Electric Cyan highlight: &H00FFFF00
        highlight_color = "&H00FFFF00"
        base_color = "&H00FFFFFF"
        outline_color = "&H00000000"
    else: # minimal
        # Clean white highlight with dim silver inactive words
        highlight_color = "&H00FFFFFF"
        base_color = "&H009E9E9E"
        outline_color = "&H00222222"

    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {res_x}
PlayResY: {res_y}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},{base_color},&H000000FF,{outline_color},&H80000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,30,30,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    chunks = smart_chunk_words(clip_words)
    events = []

    for c_idx, chunk in enumerate(chunks):
        n_words = len(chunk)
        if c_idx < len(chunks) - 1:
            next_chunk_start = float(chunks[c_idx + 1][0]["start"])
        else:
            next_chunk_start = 999999.0

        for j, word_obj in enumerate(chunk):
            w_start = float(word_obj["start"])

            # Determine end of active highlight for word j
            if j < n_words - 1:
                next_word_start = float(chunk[j + 1]["start"])
                w_end = next_word_start if next_word_start > w_start else max(float(word_obj["end"]), w_start + 0.08)
            else:
                # Last word in chunk: disappears right as speech finishes (+0.05s buffer), strictly before next chunk
                w_end = min(float(word_obj["end"]) + 0.05, next_chunk_start)

            if w_end <= w_start:
                w_end = w_start + 0.08

            start_t = format_ass_time(w_start)
            end_t = format_ass_time(w_end)

            # Build phrase with active word highlighted
            line_parts = []
            for idx, w in enumerate(chunk):
                wt = w["word"]
                if idx == j:
                    line_parts.append(f"{{\\c{highlight_color}&}}{wt}{{\\c{base_color}&}}")
                else:
                    line_parts.append(wt)

            line_text = " ".join(line_parts)
            events.append(f"Dialogue: 0,{start_t},{end_t},Default,,0,0,0,,{line_text}")

    os.makedirs(os.path.dirname(output_ass_path), exist_ok=True)
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.write("\n".join(events))

    return output_ass_path
