---

### 📄 1. INSTRUCTIONS.md (面向 AI 与开发者的架构文档)

```markdown
# Developer & AI Instructions for `iptv_checker_v3.py`

**[ATTENTION AI ASSISTANTS]** 
Read this document entirely before modifying `iptv_checker_v3.py`. This script handles fragile edge cases in IPTV streams (MPEG-TS, HLS, UDP-Proxy, Geo-blocking). Any careless changes to the networking or concurrency logic may result in system hangs, infinite loops, or false positives/negatives.

## 1. System Architecture & Dual-Mode Design

The `IPTVCheckerFinal` class is designed to run in two modes:
1. **CLI Mode** (`cli_main`): Parses `.m3u` -> Calls API -> Writes `.m3u`.
2. **API Mode** (`process_channel_list`): Receives a `List[Dict]` in RAM -> Processes -> Returns `List[Dict]`. No disk I/O (except cache/logs) is allowed in this mode. Used by `tv.py`.

**Rule**: Never add `open(file)` logic directly inside `process_channel_list` or the core probing functions.

## 2. Core Data Structure: The Channel Dictionary

Every channel is represented as a dictionary. When modifying the code, ensure these keys are strictly maintained:
```python
{
    "name": "Channel Name", 
    "url": "http://...",          # Original M3U URL
    "group": "Group Title",       # M3U group-title
    "needs_check": True/False,    # Skip probing if False
    "is_alive": True/False,       # Result of Step 1 & 2
    "msg": "Reason string",       # Death reason for logs
    "probe_info": "[1080P][H264]",# Final tag to append to title
    "probe_url": "http://...ts",  # EXTREMELY IMPORTANT: The extracted raw segment URL
    "text_res": "1080P"           # Fallback resolution parsed from EXTM3U text
}