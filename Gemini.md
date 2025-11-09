# IPTV Channel and EPG Management Script

This Python script (`tv.py`) is designed to manage IPTV channel lists (M3U) and Electronic Program Guide (EPG) data (XMLTV). It automates the process of downloading, filtering, categorizing, and generating these files based on a set of configurable parameters.

## Features

*   **Channel Data Acquisition:** Downloads channel information from a specified JSON URL.
*   **Channel Filtering:** Supports blacklisting channels by title, code, or playback URL.
*   **Channel Deduplication:** Intelligently removes duplicate channels, prioritizing High Definition (HD) and 4K versions, especially for CCTV channels.
*   **Channel Name Mapping:** Maps various channel names (e.g., "CCTV-1高清" to "CCTV-1综合") for consistency.
*   **Channel Categorization:** Groups channels into user-defined categories (e.g., "央视", "卫视", "少儿") based on keywords and a defined priority.
*   **Custom Channels:** Allows users to add their own custom channels via a JSON configuration file.
*   **Custom Channel Ordering:** Provides functionality to define a specific display order for channels within each group.
*   **M3U Playlist Generation:** Generates three M3U files:
    *   `tv.m3u`: Original multicast addresses.
    *   `tv2.m3u`: Unicast addresses (converted using UDPXY if configured).
    *   `ku9.m3u`: Unicast addresses with KU9-specific catch-up parameters.
*   **EPG Data Generation:** Downloads EPG data for channels and generates an XMLTV formatted EPG file (`t.xml`) and its gzipped version (`t.xml.gz`).
*   **Catch-up (回看) Support:** Integrates catch-up URLs into the M3U files with configurable prefixes and templates.
*   **Nginx Proxy Support:** Allows for Nginx proxying of catch-up sources and channel logos for external playback.
*   **Detailed Logging:** Generates `channel_processing.log` for channel filtering and deduplication details, and `epg_statistics.log` for EPG download and synthesis statistics.
*   **Robust Downloading:** Includes a retry mechanism for downloading JSON and EPG data.

## Configuration

All primary configurations are located at the beginning of the `tv.py` script under the `===================== 自定义配置区域 =====================` section.

### EPG Download Retry Configuration

*   `EPG_DOWNLOAD_RETRY_COUNT`: Number of retry attempts for EPG downloads (default: 3).
*   `EPG_DOWNLOAD_RETRY_DELAY`: Delay between retries in seconds (default: 2).
*   `EPG_DOWNLOAD_TIMEOUT`: Timeout for a single EPG request in seconds (default: 15).

### Output Filenames

*   `TV_M3U_FILENAME`: Output filename for the multicast M3U playlist (default: "tv.m3u").
*   `TV2_M3U_FILENAME`: Output filename for the unicast M3U playlist (default: "tv2.m3u").
*   `KU9_M3U_FILENAME`: Output filename for the KU9 catch-up M3U playlist (default: "ku9.m3u").
*   `XML_FILENAME`: Output filename for the XMLTV EPG file (default: "t.xml").

### URLs and Prefixes

*   `REPLACEMENT_IP`: UDPXY address for converting multicast to unicast (e.g., "http://op.bennq.top:7088/udp").
*   `CATCHUP_SOURCE_PREFIX`: Base URL for catch-up content (e.g., "http://183.235.162.80:6610/190000002005").
*   `NGINX_PROXY_PREFIX`: Nginx proxy address for external access (e.g., "http://op.bennq.top:7077/").
*   `JSON_URL`: URL to download the main channel list in JSON format (e.g., "http://183.235.16.92:8082/epg/api/custom/getAllChannel.json").
*   `M3U_EPG_URL`: URL for the EPG source to be included in the M3U header (e.g., "https://epg.112114.xyz/pp.xml").
*   `CATCHUP_URL_TEMPLATE`: Template for standard catch-up URLs.
*   `CATCHUP_URL_KU9`: Template for KU9-specific catch-up URLs.

## 确保
*   确保生成m3u单播地址是 REPLACEMENT_IP + rtp 组播地址 ,并忽略因为REPLACEMENT_IP 最后是否带有/ 的问题导致错误
*   其他回看 ,nginx 生成的地址同上,忽略 /,以下是ku9 生成示例
    *   #EXTINF:-1 tvg-id="CCTV13" tvg-name="CCTV-13高清" tvg-logo="http://op.bennq.top:7077/183.235.16.92:8081/pics/micro-picture/channel/2021-04-23/d1db76fe-8f2f-47c3-b1dd-7245ce6755b2.png" catchup="default" catchup-source="http://op.bennq.top:7077/183.235.162.80:6610/190000002005/ch000000000000329/index.m3u8?starttime=${(b)yyyyMMddHHmmss|UTC}&endtime=${(e)yyyyMMddHHmmss|UTC}" group-title="央视",CCTV-13新闻
        http://op.bennq.top:7088/udp/239.21.0.88:3692
*   输出尽量使用中文



