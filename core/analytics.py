import os
import csv
import json
import threading
from datetime import datetime
import requests

_GEO_CACHE = {}
_LOCK = threading.Lock()

def get_client_ip(headers: dict, client_host: str = "Unknown") -> str:
    """Extracts real public client IP even behind proxies like Cloudflare or Hugging Face."""
    cf_ip = headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()

    forwarded = headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()

    real_ip = headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()

    return client_host or "Unknown"

def lookup_ip_details(ip: str, headers: dict = None) -> dict:
    """Resolves IP to Country, City, and Region."""
    if not ip or ip in ["127.0.0.1", "localhost", "::1"] or ip.startswith("192.168.") or ip.startswith("10.") or ip.startswith("172."):
        return {
            "country": "Localhost / Developer",
            "city": "Local PC",
            "region": "Local"
        }

    # 1. First check if proxy (Cloudflare) passed direct geo headers
    if headers:
        cf_country = headers.get("cf-ipcountry")
        cf_city = headers.get("cf-ipcity")
        if cf_country and cf_city:
            return {
                "country": cf_country,
                "city": cf_city,
                "region": headers.get("cf-region", "Cloudflare")
            }

    if ip in _GEO_CACHE:
        return _GEO_CACHE[ip]

    # 2. Query free ip-api.com
    try:
        url = f"http://ip-api.com/json/{ip}?fields=status,country,city,regionName"
        res = requests.get(url, timeout=2.5)
        if res.status_code == 200:
            data = res.json()
            if data.get("status") == "success":
                geo = {
                    "country": data.get("country", "Unknown"),
                    "city": data.get("city", "Unknown"),
                    "region": data.get("regionName", "Unknown")
                }
                _GEO_CACHE[ip] = geo
                return geo
    except Exception:
        pass

    # 3. Fallback to free ipapi.co
    try:
        url = f"https://ipapi.co/{ip}/json/"
        res = requests.get(url, headers={"User-Agent": "HookFlowAnalytics/1.0"}, timeout=2.5)
        if res.status_code == 200:
            data = res.json()
            if "country_name" in data:
                geo = {
                    "country": data.get("country_name", "Unknown"),
                    "city": data.get("city", "Unknown"),
                    "region": data.get("region", "Unknown")
                }
                _GEO_CACHE[ip] = geo
                return geo
    except Exception:
        pass

    return {
        "country": "Global Web",
        "city": "Internet Visitor",
        "region": "Online"
    }

def simplify_device(user_agent: str) -> str:
    """Detects broad device/OS type from User-Agent string."""
    ua = (user_agent or "").lower()
    if "android" in ua:
        return "Android Mobile"
    elif "iphone" in ua:
        return "iPhone"
    elif "ipad" in ua:
        return "iPad"
    elif "windows" in ua:
        return "Windows PC"
    elif "macintosh" in ua or "mac os" in ua:
        return "Mac"
    elif "linux" in ua:
        return "Linux"
    return "Web Browser"

def format_tool_name(page: str) -> str:
    """Converts page URL to clear readable tool name for Excel/Google Sheets."""
    p = (page or "").strip()
    if p in ["/", "/index", "downloader", "home"]:
        return "YouTube Downloader"
    elif "caption" in p:
        return "Auto-Caption Studio"
    return p or "YouTube Downloader"

def sync_to_google_sheet(webhook_url: str, payload: dict):
    """Posts visitor event to user's Google Sheet Apps Script Webhook asynchronously."""
    if not webhook_url or not webhook_url.startswith("http"):
        return
    try:
        res = requests.post(webhook_url, json=payload, timeout=6.0, allow_redirects=True)
        if res.status_code >= 400:
            print(f"[HookFlow Google Sheet] Status code: {res.status_code}")
    except Exception as e:
        print(f"[HookFlow Google Sheet Sync Note] {e}")

def log_visitor_event(storage_dir: str, ip: str, page: str, user_agent: str, headers: dict = None):
    """Appends visitor record into local CSV and syncs live to Google Sheet if configured."""
    try:
        csv_path = os.path.join(storage_dir, "visitors_log.csv")
        file_exists = os.path.exists(csv_path)

        geo = lookup_ip_details(ip, headers=headers)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        device = simplify_device(user_agent)
        tool_name = format_tool_name(page)

        # 1. Local CSV write with Excel UTF-8 BOM
        with _LOCK:
            os.makedirs(storage_dir, exist_ok=True)
            with open(csv_path, mode="a", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                if not file_exists:
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

                writer.writerow([
                    now_str,
                    geo.get("country", "Unknown"),
                    geo.get("city", "Unknown"),
                    geo.get("region", "Unknown"),
                    tool_name,
                    ip,
                    device,
                    user_agent[:120] if user_agent else ""
                ])

        # 2. Live Sync to Google Sheets Webhook
        webhook_url = os.environ.get("GOOGLE_SHEET_WEBHOOK")
        if not webhook_url:
            config_path = os.path.join(storage_dir, "config.json")
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as cf:
                        cfg = json.load(cf)
                        webhook_url = cfg.get("google_sheet_webhook")
                except Exception:
                    pass

        if webhook_url and webhook_url.strip():
            sheet_payload = {
                "timestamp": now_str,
                "country": geo.get("country", "Unknown"),
                "city": geo.get("city", "Unknown"),
                "region": geo.get("region", "Unknown"),
                "tool": tool_name,
                "page": tool_name, # Also set page as tool_name for backward compatibility with existing sheet scripts
                "ip": ip,
                "device": device
            }
            threading.Thread(target=sync_to_google_sheet, args=(webhook_url.strip(), sheet_payload), daemon=True).start()

    except Exception as e:
        print(f"[HookFlow Analytics Error] {e}")
