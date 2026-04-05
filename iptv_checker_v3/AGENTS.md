# Developer & AI Instructions for `iptv_checker_v3`

**[ATTENTION AI ASSISTANTS]** Read this document entirely before modifying `iptv_checker_v3.py`. This script handles fragile edge cases in IPTV streams (MPEG-TS, HLS, UDP-Proxy, Geo-blocking). Any careless changes to networking or concurrency logic may result in system hangs, infinite loops, or false positives/negatives.

## 1. System Architecture & Dual-Mode Design

The `IPTVCheckerFinal` class runs in two modes:

1. **CLI Mode** (`main` → `cli_main`): Parses `.m3u` → Calls API → Writes `.m3u`.
2. **API Mode** (`process_channel_list`): Receives `List[Dict]` in RAM → Processes → Returns `List[Dict]`. No disk I/O (except cache/logs) allowed. Used by `tv.py`.

**Rule**: Never add `open(file)` logic directly inside `process_channel_list` or the core probing functions.

## 2. Core Data Structure: The Channel Dictionary

Every channel is a dictionary. When modifying the code, ensure these keys are maintained:

```python
{
    "name": "Channel Name",
    "url": "http://...",          # Original M3U URL (also used as cache key)
    "group": "Group Title",       # M3U group-title
    "needs_check": True/False,    # Skip probing if False
    "is_alive": True/False,       # Result of Step 1 & 2
    "msg": "Reason string",       # Death reason for logs
    "probe_info": "[1080P][H264]",# Final tag to append to title
    "probe_url": "http://...ts",  # The extracted raw segment URL for ffprobe
    "text_res": "1080P"           # Fallback resolution parsed from EXTM3U text
}
```

## 3. Processing Pipeline

### Step 1: 极速测活 (`check_stream_alive`)
- Uses `aiohttp` with configurable concurrency (`workers`, default 30 in CLI, 4 in `tv.py`).
- Detects M3U8 playlists: parses `RESOLUTION=` tag, tests first `.ts` slice.
- Detects raw streams (TS/UDP): checks chunk size > 100 bytes.
- Rejects: HTML pages (anti-leech), empty responses, timeouts.

### Step 2: 终极质量探测 (`probe_video_info`)
- Uses `ffprobe` subprocess with 15s timeout.
- **Three-layer fallback** (do NOT simplify):
  1. Probe `probe_url` (extracted `.ts` slice)
  2. If failed, fallback to original `url` (full M3U8)
  3. If both fail, use `text_res` (regex-parsed resolution from M3U8 text) as last resort
- Cache: `probe_cache.json` with 24h expiry (configurable). Stores UTC+8 formatted timestamps.
- Channels cached as `"Unknown"/"Unknown"` are marked dead and removed.

## 4. Critical Constraints

- **Async safety**: `check_stream_alive` uses `aiohttp.ClientSession` with `TCPConnector(ssl=False)`. Never block the event loop.
- **Magic Bytes detection**: M3U8 detection uses `b'#EXTM3U'`, HTML detection uses `b'<html'` + `b'<body'`. The 100-byte threshold for raw stream detection is tuned for MPEG-TS. Do not change without testing.
- **Semaphore limits**: `probe_worker` uses `Semaphore(4)` — hardcoded to prevent ffprobe overload. Do not increase without good reason.
- **Logger**: Uses `logging.StreamHandler(sys.stdout)` with UTF-8 wrapper. The `if not logger.handlers` guard prevents duplicate handlers when imported by `tv.py`.
- **Windows event loop**: CLI mode sets `WindowsSelectorEventLoopPolicy` on `win32`. API mode relies on caller's event loop setup.

## 5. File Locations

- Cache: `config/probe_cache.json` (preferred) or `probe_cache.json` (fallback).
- Logs: `log/checker_final.log`, `log/failed_channels.txt`.
