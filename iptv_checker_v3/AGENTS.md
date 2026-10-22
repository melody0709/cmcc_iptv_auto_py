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
- Detects M3U8 playlists recursively: prefers the highest-bandwidth playable variant, tests up to three newest media slices, and parses the selected variant's `RESOLUTION=` tag.
- Validates slice bodies instead of trusting HTTP 200 alone; rejects HTML/JSON/text error payloads and empty slices. A stale `Last-Modified` header is only a hint and must not override a valid latest slice.
- Detects raw streams (TS/FLV/fMP4 and related magic bytes). The existing chunk-size > 100 bytes rule remains as weak fallback evidence for unusual raw streams.
- Falls back to a bounded async HTTP/HTTPS socket probe when `aiohttp` rejects a response that a player may still consume.

### Step 2: 终极质量探测 (`probe_video_info`)
- Uses `ffprobe` subprocess with 15s timeout.
- **Three-layer fallback** (do NOT simplify):
  1. Refresh and probe `probe_url` (latest extracted media slice)
  2. If failed, fallback to original `url` (full M3U8)
  3. If both fail, use `text_res` (regex-parsed resolution from M3U8 text) as last resort
- Cache: `probe_cache.json` with 24h expiry (configurable). Stores UTC+8 formatted timestamps.
- Cache only successful ffprobe/text-resolution results. Apply valid positive cache entries before checking ffprobe availability. Legacy `"Unknown"/"Unknown"` entries are invalidated and reprobed; a quality-probe timeout or missing ffprobe must not override strong Step 1 liveness evidence.
- ffprobe subprocesses must be explicitly killed and awaited after timeout so timed-out MPEG-TS probes cannot remain orphaned.

## 4. Critical Constraints

- **Async safety**: `check_stream_alive` uses `aiohttp.ClientSession` with `TCPConnector(ssl=False)`. Never block the event loop.
- **Magic Bytes detection**: M3U8 detection accepts an optional UTF-8 BOM/leading whitespace before `b'#EXTM3U'`; HTML/JSON/text errors are rejected before raw fallback. The 100-byte threshold for raw stream detection is tuned for MPEG-TS and remains in place as weak evidence. Do not change without testing.
- **Semaphore limits**: `probe_worker` uses `Semaphore(4)` — hardcoded to prevent ffprobe overload. Do not increase without good reason.
- **Logger**: Uses `logging.StreamHandler(sys.stdout)` with UTF-8 wrapper. The `if not logger.handlers` guard prevents duplicate handlers when imported by `tv.py`. Tests set `IPTV_CHECKER_DISABLE_FILE_LOG=1` before import so they never truncate the operational log.
- **Windows event loop**: ffprobe uses asyncio subprocesses and therefore requires `WindowsProactorEventLoopPolicy` on `win32`. CLI mode and `tv.py` select Proactor when probing is enabled; non-probe mode may continue using Selector.

## 5. File Locations

- Cache: repository `config/probe_cache.json` (preferred), module `config/probe_cache.json`, or legacy module `probe_cache.json` (read/migration fallback).
- Logs: `log/checker_final.log`, `log/failed_channels.txt`.
