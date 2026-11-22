---
title: HookFlow Studio
emoji: 🎬
colorFrom: purple
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# HookFlow Studio ⚡

An open-source media toolkit featuring:
1. **YouTube HQ Video & Audio Downloader**: Download YouTube videos in Full HD 1080p 60fps MP4 or studio-quality 320kbps MP3 audio with accurate file size estimates.
2. **AI Auto-Caption Studio**: Word-by-word animated karaoke subtitles for Shorts, Reels, and TikToks in Alex Hormozi and MrBeast styles with speech silence detection.

> 📖 **Architecture Analysis & Post-Mortem:**  
> Please read [PROJECT_POSTMORTEM.md](PROJECT_POSTMORTEM.md) for a comprehensive deep-dive into why free-tier cloud deployment (Render/AWS) triggers YouTube datacenter bot-blocks (SABR / PO Token), and how to run this project smoothly locally or with residential proxies.

## Quickstart (Local Run — 100% Free & Zero Cookies)
Aapke local machine/laptop par bina kisi cookie ke full speed me chalta hai:
```bash
run.bat
```
Server boots locally at `http://localhost:8000`.

### Public Access via Free Cloudflare Tunnel
Apne laptop server ko poori duniya ke liye free me live karne ke liye:
```bash
cloudflared tunnel --url http://localhost:8000
```
