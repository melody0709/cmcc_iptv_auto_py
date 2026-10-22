import asyncio
import aiohttp
import argparse
import logging
import re
import sys
import io
import time
import json
import os
import shutil
import ssl
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone, timedelta  # <-- 新增：用于处理 UTC+8 时间格式

# ================= 动态路径与日志配置 =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "log")
os.makedirs(LOG_DIR, exist_ok=True)  # 自动创建 log 目录
DISABLE_FILE_LOG = os.environ.get("IPTV_CHECKER_DISABLE_FILE_LOG") == "1"

# 统一配置 Logger
logger = logging.getLogger("IPTV_Checker_Final")
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# 防止在被 tv.py 导入时重复添加 Handler
if not logger.handlers:
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.stream = io.TextIOWrapper(console_handler.stream.buffer, encoding='utf-8', errors='replace')
    logger.addHandler(console_handler)

    if not DISABLE_FILE_LOG:
        file_handler = logging.FileHandler(os.path.join(LOG_DIR, "checker_final.log"), mode='w', encoding='utf-8')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
# ======================================================

class IPTVCheckerFinal:
    def __init__(self, target_group=None, timeout=10, workers=30, enable_probe=False, cache_expire_hours=24):
        # 作为库被调用时，不再强依赖输入/输出文件路径
        self.target_group = target_group
        self.timeout = timeout
        self.workers = workers
        
        self.enable_probe = enable_probe
        self.cache_expire_sec = cache_expire_hours * 3600
        self.probe_timeout = 15.0
        self.ffprobe_path = shutil.which('ffprobe') if enable_probe else None
        
        # 缓存文件优先放在 config 目录，没有则放当前目录
        project_config_dir = os.path.join(os.path.dirname(BASE_DIR), "config")
        module_config_dir = os.path.join(BASE_DIR, "config")
        if os.path.isdir(project_config_dir):
            self.cache_file = os.path.join(project_config_dir, "probe_cache.json")
        elif os.path.isdir(module_config_dir):
            self.cache_file = os.path.join(module_config_dir, "probe_cache.json")
        else:
            self.cache_file = os.path.join(BASE_DIR, "probe_cache.json")
        self.legacy_cache_files = [
            os.path.join(BASE_DIR, "probe_cache.json"),
            os.path.join(module_config_dir, "probe_cache.json"),
        ]
            
        self.cache_data = self.load_cache()
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Connection": "keep-alive"
        }

    def load_cache(self):
        cache_candidates = [self.cache_file]
        cache_candidates.extend(
            path for path in getattr(self, 'legacy_cache_files', [])
            if path not in cache_candidates
        )
        for cache_path in cache_candidates:
            if not os.path.exists(cache_path):
                continue
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cache_data = json.load(f)
                if isinstance(cache_data, dict):
                    return cache_data
            except Exception as e:
                logger.warning(f"画质缓存读取失败，将忽略并重建: {cache_path} ({type(e).__name__}: {e})")
        return {}

    def save_cache(self):
        try:
            os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"画质缓存写入失败: {self.cache_file} ({type(e).__name__}: {e})")

    def _is_playlist_like_url(self, url):
        url_lower = str(url or "").lower()
        return any(token in url_lower for token in (".m3u8", ".m3u", "playlist", "manifest"))

    def _is_html_payload(self, data):
        data_lower = (data or b"").lstrip().lower()
        return (
            data_lower.startswith(b'<!doctype html')
            or data_lower.startswith(b'<html')
            or (b'<html' in data_lower and b'<body' in data_lower)
        )

    def _is_hls_playlist_payload(self, data):
        sample = data or b""
        if sample.startswith(b'\xef\xbb\xbf'):
            sample = sample[3:]
        return sample.lstrip(b' \t\r\n').startswith(b'#EXTM3U')

    def _get_header(self, headers, name, default=""):
        if not headers:
            return default
        return headers.get(name, headers.get(name.lower(), default))

    def _is_mostly_text(self, data):
        sample = (data or b"")[:512]
        if not sample:
            return False
        printable = sum(
            1 for value in sample
            if value in (9, 10, 13) or 32 <= value <= 126
        )
        return printable / len(sample) >= 0.90

    def _is_text_error_payload(self, data, content_type=""):
        if not data or self._is_hls_playlist_payload(data):
            return False
        content_type = (content_type or "").split(';', 1)[0].strip().lower()
        sample = data[:512].lstrip().lower()
        if self._is_html_payload(data):
            return True
        if content_type.startswith('text/'):
            return True
        if content_type in {
            'application/json', 'application/problem+json',
            'application/xml', 'application/xhtml+xml',
        }:
            return True
        if sample.startswith((b'{', b'[', b'<?xml')):
            return True
        if self._is_mostly_text(data) and sample.startswith(
            (b'error', b'forbidden', b'unauthorized', b'access denied')
        ):
            return True
        return False

    def _looks_like_ts_payload(self, data):
        if not data:
            return False
        sample = data[:188 * 4]
        if len(sample) < 188:
            return False
        for offset in range(min(188, len(sample))):
            if sample[offset:offset + 1] != b'\x47':
                continue
            sync_hits = 0
            if len(sample) > offset + 188 and sample[offset + 188:offset + 189] == b'\x47':
                sync_hits += 1
            if len(sample) > offset + 376 and sample[offset + 376:offset + 377] == b'\x47':
                sync_hits += 1
            if sync_hits >= 2:
                return True
        return False

    def _looks_like_media_payload(self, data):
        if not data:
            return False
        if self._looks_like_ts_payload(data):
            return True

        sample = data[:32]
        if sample.startswith((b'FLV', b'OggS', b'ID3', b'RIFF')):
            return True
        if sample.startswith((b'\x1a\x45\xdf\xa3', b'\x30\x26\xb2\x75\x8e\x66\xcf\x11')):
            return True
        if sample.startswith((b'\x00\x00\x01\xba', b'\x00\x00\x00\x01', b'\x00\x00\x01')):
            return True
        if len(sample) >= 8 and sample[4:8] in (b'ftyp', b'styp', b'moof', b'mdat'):
            return True
        if len(sample) >= 2 and sample[0] == 0xff and sample[1] & 0xf0 == 0xf0:
            return True
        return False

    def _classify_media_payload(self, data, content_type="", hls_segment=False):
        if not data:
            return False, "none", "未返回媒体数据"
        if self._looks_like_media_payload(data):
            if len(data) <= 100:
                return False, "none", f"数据异常过小: {len(data)} bytes"
            return True, "strong", "命中媒体魔法字节"
        if self._is_text_error_payload(data, content_type):
            return False, "text", "响应为文本/错误页"

        content_type = (content_type or "").split(';', 1)[0].strip().lower()
        strong_media_content_type = (
            content_type.startswith(('video/', 'audio/'))
            or content_type in {
                'application/mp2t', 'application/x-mpegts',
                'application/vnd.rn-realmedia',
            }
        )
        if len(data) > 100 and strong_media_content_type:
            return True, "weak", f"仅命中媒体类型 {content_type}"
        if len(data) > 100 and hls_segment and not self._is_mostly_text(data):
            return True, "weak", "HLS 未识别二进制切片"
        if len(data) > 100:
            return True, "weak", "仅满足裸流长度阈值"
        return False, "none", f"数据异常过小: {len(data)} bytes"

    def _set_text_resolution(self, channel, height):
        if height >= 2160:
            channel['text_res'] = "4K"
        elif height >= 1080:
            channel['text_res'] = "1080P"
        elif height >= 720:
            channel['text_res'] = "720P"
        else:
            channel['text_res'] = "标清"

    def _update_text_resolution(self, channel, playlist_text):
        heights = [
            int(height)
            for _, height in re.findall(
                r'RESOLUTION\s*=\s*(\d+)x(\d+)', playlist_text, flags=re.IGNORECASE
            )
        ]
        if heights:
            self._set_text_resolution(channel, max(heights))

    def _extract_uri_attribute(self, line):
        match = re.search(r'(?:^|[:,])URI=(?:"([^"]+)"|([^,]+))', line, flags=re.IGNORECASE)
        if not match:
            return None
        return (match.group(1) or match.group(2) or "").strip()

    def _parse_hls_playlist(self, playlist_text):
        variants, segments, parts = [], [], []
        pending_variant = None
        for raw_line in playlist_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            upper_line = line.upper()
            if upper_line.startswith('#EXT-X-STREAM-INF:'):
                pending_variant = line
                continue
            if upper_line.startswith('#EXT-X-PART:'):
                part_uri = self._extract_uri_attribute(line)
                if part_uri:
                    parts.append(part_uri)
                continue
            if upper_line.startswith('#EXT-X-PRELOAD-HINT:') and 'TYPE=PART' in upper_line:
                part_uri = self._extract_uri_attribute(line)
                if part_uri:
                    parts.append(part_uri)
                continue
            if line.startswith('#'):
                continue

            if pending_variant:
                bandwidth_match = re.search(
                    r'(?:^|[:,])BANDWIDTH=(\d+)', pending_variant, flags=re.IGNORECASE
                )
                bandwidth = int(bandwidth_match.group(1)) if bandwidth_match else 0
                resolution_match = re.search(
                    r'RESOLUTION\s*=\s*(\d+)x(\d+)',
                    pending_variant,
                    flags=re.IGNORECASE,
                )
                height = int(resolution_match.group(2)) if resolution_match else 0
                variants.append((bandwidth, height, line))
                pending_variant = None
            else:
                segments.append(line)

        variants.sort(key=lambda item: item[0], reverse=True)
        return variants, segments, parts

    def _select_hls_variants(self, variants):
        if len(variants) <= 3:
            return variants
        selected = [variants[0], variants[len(variants) // 2], variants[-1]]
        return list(dict.fromkeys(selected))

    def _is_stale_live_playlist(self, playlist_text, headers):
        playlist_upper = playlist_text.upper()
        if (
            '#EXT-X-ENDLIST' in playlist_upper
            or '#EXT-X-MEDIA-SEQUENCE' not in playlist_upper
        ):
            return False
        date_value = self._get_header(headers, 'Date')
        modified_value = self._get_header(headers, 'Last-Modified')
        if not date_value or not modified_value:
            return False
        try:
            server_date = parsedate_to_datetime(date_value)
            last_modified = parsedate_to_datetime(modified_value)
            age_seconds = (server_date - last_modified).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return False

        target_match = re.search(
            r'#EXT-X-TARGETDURATION\s*:\s*([0-9.]+)', playlist_text, flags=re.IGNORECASE
        )
        try:
            target_duration = float(target_match.group(1)) if target_match else 0.0
        except ValueError:
            target_duration = 0.0
        stale_after = max(120.0, target_duration * 12)
        return age_seconds > stale_after

    async def _read_limited_content(self, content, limit, initial=b""):
        chunks = [initial] if initial else []
        total_bytes = len(initial)
        while total_bytes < limit and not content.at_eof():
            chunk = await content.read(min(4096, limit - total_bytes))
            if not chunk:
                break
            chunks.append(chunk)
            total_bytes += len(chunk)
        return b''.join(chunks)

    async def _read_to_minimum(self, content, initial, minimum=101, limit=4096):
        chunks = [initial]
        total_bytes = len(initial)
        while total_bytes < minimum and total_bytes < limit and not content.at_eof():
            chunk = await content.read(min(1024, limit - total_bytes))
            if not chunk:
                break
            chunks.append(chunk)
            total_bytes += len(chunk)
        return b''.join(chunks)

    async def _check_hls_playlist(
        self, session, channel, playlist_url, playlist_data, playlist_headers=None,
        depth=0, visited=None
    ):
        if depth > 3:
            return False, "M3U8 层级过深"
        visited = visited or set()
        if playlist_url in visited:
            return False, "M3U8 出现循环引用"
        visited.add(playlist_url)

        try:
            playlist_text = playlist_data.decode('utf-8-sig', errors='ignore')
        except Exception:
            return False, "M3U8 文本解码失败"
        if not self._is_hls_playlist_payload(playlist_data):
            return False, "响应不是有效 M3U8"
        if self._is_stale_live_playlist(playlist_text, playlist_headers):
            channel['_playlist_stale_hint'] = True
            logger.debug(f"M3U8 响应头疑似停更，继续验证实际切片: {playlist_url}")

        variants, segments, parts = self._parse_hls_playlist(playlist_text)
        if not variants:
            self._update_text_resolution(channel, playlist_text)
        request_timeout = aiohttp.ClientTimeout(
            total=max(2.0, float(self.timeout)),
            connect=max(1.0, float(self.timeout)),
        )
        errors = []

        # 主播放列表先递归到实际媒体列表，最多尝试三个码率，防止单个清晰度失效。
        for _, variant_height, variant_uri in self._select_hls_variants(variants):
            if variant_height:
                self._set_text_resolution(channel, variant_height)
            target_url = urljoin(playlist_url, variant_uri)
            request_headers = dict(self.headers)
            request_headers['Referer'] = playlist_url
            try:
                async with session.get(
                    target_url, headers=request_headers, timeout=request_timeout,
                    ssl=False, allow_redirects=True
                ) as variant_resp:
                    if variant_resp.status not in [200, 206]:
                        errors.append(f"子列表HTTP {variant_resp.status}")
                        continue
                    first_chunk = await variant_resp.content.read(4096)
                    if not first_chunk:
                        errors.append("子列表为空")
                        continue
                    if len(first_chunk) <= 100:
                        first_chunk = await self._read_to_minimum(
                            variant_resp.content, first_chunk
                        )
                    final_variant_url = str(variant_resp.url)
                    content_type = self._get_header(variant_resp.headers, 'Content-Type')
                    if self._is_hls_playlist_payload(first_chunk):
                        child_data = await self._read_limited_content(
                            variant_resp.content, 64 * 1024, first_chunk
                        )
                        alive, msg = await self._check_hls_playlist(
                            session, channel, final_variant_url, child_data,
                            variant_resp.headers, depth + 1, visited.copy()
                        )
                        if alive:
                            return True, msg
                        errors.append(msg)
                        continue

                    valid, evidence, reason = self._classify_media_payload(
                        first_chunk, content_type, hls_segment=True
                    )
                    if valid:
                        channel['probe_url'] = final_variant_url
                        channel['_probe_referer'] = playlist_url
                        channel['_liveness_evidence'] = evidence
                        return True, "存活 (M3U8媒体变体)"
                    errors.append(reason)
            except asyncio.TimeoutError:
                errors.append("子列表超时")
            except Exception as e:
                errors.append(f"子列表异常:{type(e).__name__}")

        # 直播窗口里的第一片往往最老、最容易刚好过期，因此从最新切片向前回退。
        media_candidates = list(reversed(segments[-3:]))
        media_candidates.extend(reversed(parts[-3:]))
        for media_uri in media_candidates:
            target_url = urljoin(playlist_url, media_uri)
            request_headers = dict(self.headers)
            request_headers['Referer'] = playlist_url
            try:
                async with session.get(
                    target_url, headers=request_headers, timeout=request_timeout,
                    ssl=False, allow_redirects=True
                ) as seg_resp:
                    if seg_resp.status not in [200, 206]:
                        errors.append(f"切片HTTP {seg_resp.status}")
                        continue
                    segment_data = await seg_resp.content.read(4096)
                    if not segment_data:
                        errors.append("切片为空")
                        continue
                    if len(segment_data) <= 100:
                        segment_data = await self._read_to_minimum(
                            seg_resp.content, segment_data
                        )
                    final_segment_url = str(seg_resp.url)
                    content_type = self._get_header(seg_resp.headers, 'Content-Type')
                    if self._is_hls_playlist_payload(segment_data):
                        child_data = await self._read_limited_content(
                            seg_resp.content, 64 * 1024, segment_data
                        )
                        alive, msg = await self._check_hls_playlist(
                            session, channel, final_segment_url, child_data,
                            seg_resp.headers, depth + 1, visited.copy()
                        )
                        if alive:
                            return True, msg
                        errors.append(msg)
                        continue

                    valid, evidence, reason = self._classify_media_payload(
                        segment_data, content_type, hls_segment=True
                    )
                    if valid:
                        channel['probe_url'] = final_segment_url
                        channel['_probe_referer'] = playlist_url
                        channel['_hls_media_playlist_url'] = playlist_url
                        channel['_liveness_evidence'] = evidence
                        return True, "存活 (M3U8/切片内容已验证)"
                    errors.append(f"切片{reason}")
            except asyncio.TimeoutError:
                errors.append("切片超时")
            except Exception as e:
                errors.append(f"切片异常:{type(e).__name__}")

        if not variants and not media_candidates:
            return False, "M3U8 未找到可探测媒体"
        error_summary = "; ".join(errors[-3:]) if errors else "无可用切片"
        return False, f"切片无法播放 ({error_summary})"

    async def _refresh_hls_probe_url(self, session, channel):
        playlist_url = channel.get('_hls_media_playlist_url')
        if not playlist_url:
            return None
        request_timeout = aiohttp.ClientTimeout(
            total=max(2.0, float(self.timeout)),
            connect=max(1.0, float(self.timeout)),
        )
        try:
            async with session.get(
                playlist_url, headers=self.headers, timeout=request_timeout,
                ssl=False, allow_redirects=True
            ) as response:
                if response.status not in [200, 206]:
                    return None
                first_chunk = await response.content.read(4096)
                if not self._is_hls_playlist_payload(first_chunk):
                    return None
                playlist_data = await self._read_limited_content(
                    response.content, 64 * 1024, first_chunk
                )
                playlist_text = playlist_data.decode('utf-8-sig', errors='ignore')
                _, segments, parts = self._parse_hls_playlist(playlist_text)
                candidates = segments + parts
                if not candidates:
                    return None
                refreshed_url = urljoin(str(response.url), candidates[-1])
                channel['probe_url'] = refreshed_url
                channel['_probe_referer'] = str(response.url)
                return refreshed_url
        except Exception as e:
            logger.debug(f"刷新 HLS 切片失败 [{channel.get('name')}]: {type(e).__name__}: {e}")
            return None

    def _decode_chunked_body(self, body):
        decoded = []
        position = 0
        try:
            while position < len(body):
                line_end = body.find(b'\r\n', position)
                if line_end < 0:
                    break
                size_text = body[position:line_end].split(b';', 1)[0].strip()
                chunk_size = int(size_text, 16)
                position = line_end + 2
                if chunk_size == 0:
                    return b''.join(decoded)
                available = min(chunk_size, len(body) - position)
                decoded.append(body[position:position + available])
                position += available
                if available < chunk_size:
                    break
                if body[position:position + 2] == b'\r\n':
                    position += 2
        except (TypeError, ValueError):
            return body
        return b''.join(decoded) if decoded else body

    async def _check_hls_playlist_via_socket(
        self, channel, playlist_url, playlist_data, playlist_headers=None,
        depth=0, visited=None
    ):
        if depth > 3:
            return False, "socket M3U8 层级过深"
        visited = visited or set()
        if playlist_url in visited:
            return False, "socket M3U8 出现循环引用"
        visited.add(playlist_url)

        if not self._is_hls_playlist_payload(playlist_data):
            return False, "socket 响应不是有效 M3U8"
        playlist_text = playlist_data.decode('utf-8-sig', errors='ignore')
        if self._is_stale_live_playlist(playlist_text, playlist_headers):
            channel['_playlist_stale_hint'] = True
            logger.debug(f"socket M3U8 响应头疑似停更，继续验证实际切片: {playlist_url}")
        variants, segments, parts = self._parse_hls_playlist(playlist_text)
        if not variants:
            self._update_text_resolution(channel, playlist_text)
        errors = []

        for _, variant_height, variant_uri in self._select_hls_variants(variants):
            if variant_height:
                self._set_text_resolution(channel, variant_height)
            target_url = urljoin(playlist_url, variant_uri)
            result = await self._fetch_via_socket(target_url)
            if not result.get('ok'):
                errors.append(result.get('error', 'socket 子列表失败'))
                continue
            status_code = result.get('status_code')
            if status_code is not None and status_code not in [200, 206]:
                errors.append(f"socket 子列表HTTP {status_code}")
                continue
            body = result.get('body', b'')
            final_url = result.get('url', target_url)
            headers = result.get('headers', {})
            if self._is_hls_playlist_payload(body):
                alive, msg = await self._check_hls_playlist_via_socket(
                    channel, final_url, body, headers, depth + 1, visited.copy()
                )
                if alive:
                    return True, msg
                errors.append(msg)
                continue
            valid, evidence, reason = self._classify_media_payload(
                body, self._get_header(headers, 'Content-Type'), hls_segment=True
            )
            if valid:
                channel['probe_url'] = final_url
                channel['_probe_referer'] = playlist_url
                channel['_liveness_evidence'] = evidence
                return True, "存活 (M3U8/socket媒体变体)"
            errors.append(reason)

        media_candidates = list(reversed(segments[-3:]))
        media_candidates.extend(reversed(parts[-3:]))
        for media_uri in media_candidates:
            target_url = urljoin(playlist_url, media_uri)
            result = await self._fetch_via_socket(target_url)
            if not result.get('ok'):
                errors.append(result.get('error', 'socket 切片失败'))
                continue
            status_code = result.get('status_code')
            if status_code is not None and status_code not in [200, 206]:
                errors.append(f"socket 切片HTTP {status_code}")
                continue
            body = result.get('body', b'')
            headers = result.get('headers', {})
            valid, evidence, reason = self._classify_media_payload(
                body, self._get_header(headers, 'Content-Type'), hls_segment=True
            )
            if valid:
                channel['probe_url'] = result.get('url', target_url)
                channel['_probe_referer'] = playlist_url
                channel['_hls_media_playlist_url'] = playlist_url
                channel['_liveness_evidence'] = evidence
                return True, "存活 (M3U8/socket切片内容已验证)"
            errors.append(f"socket 切片{reason}")

        if not variants and not media_candidates:
            return False, "socket M3U8 未找到可探测媒体"
        error_summary = "; ".join(errors[-3:]) if errors else "无可用切片"
        return False, f"socket 切片无法播放 ({error_summary})"

    def _parse_http_header_block(self, header_blob):
        status_code = None
        headers = {}
        if not header_blob:
            return status_code, headers

        lines = header_blob.split(b'\r\n')
        if lines:
            status_line = lines[0].decode('iso-8859-1', errors='ignore')
            status_match = re.match(r'^HTTP/\d(?:\.\d)?\s+(\d+)', status_line)
            if status_match:
                status_code = int(status_match.group(1))

        for raw_line in lines[1:]:
            if b':' not in raw_line:
                continue
            key, value = raw_line.split(b':', 1)
            headers[key.decode('iso-8859-1', errors='ignore').strip().lower()] = value.decode('iso-8859-1', errors='ignore').strip()
        return status_code, headers

    async def _read_socket_response(self, parsed, request_bytes, read_timeout):
        reader = writer = None
        try:
            ssl_context = None
            server_hostname = None
            if parsed.scheme == 'https':
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                server_hostname = parsed.hostname

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    parsed.hostname,
                    parsed.port or (443 if parsed.scheme == 'https' else 80),
                    ssl=ssl_context,
                    server_hostname=server_hostname
                ),
                timeout=read_timeout
            )
            writer.write(request_bytes)
            await asyncio.wait_for(writer.drain(), timeout=read_timeout)

            chunks = []
            total_bytes = 0
            for _ in range(6):
                chunk = await asyncio.wait_for(reader.read(2048), timeout=read_timeout)
                if not chunk:
                    break
                chunks.append(chunk)
                total_bytes += len(chunk)
                if total_bytes >= 8192:
                    break
            return b''.join(chunks)
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(
                        writer.wait_closed(), timeout=max(1.0, min(2.0, read_timeout))
                    )
                except Exception:
                    pass

    async def _fetch_via_socket(self, url, max_redirects=5):
        current_url = url
        redirect_chain = []

        for _ in range(max_redirects + 1):
            parsed = urlparse(current_url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname:
                return {"ok": False, "error": f"socket 不支持的URL: {current_url}", "chain": redirect_chain}

            path = parsed.path or '/'
            if parsed.query:
                path = f"{path}?{parsed.query}"

            request = (
                f"GET {path} HTTP/1.1\r\n"
                f"Host: {parsed.netloc}\r\n"
                f"User-Agent: {self.headers['User-Agent']}\r\n"
                "Accept: */*\r\n"
                "Accept-Encoding: identity\r\n"
                "Connection: close\r\n\r\n"
            ).encode('utf-8', errors='ignore')

            raw = await self._read_socket_response(parsed, request, max(1.0, self.timeout / 2))
            if not raw:
                return {"ok": False, "error": "socket 未返回数据", "chain": redirect_chain}

            header_blob, separator, body = raw.partition(b'\r\n\r\n')
            if separator:
                status_code, headers = self._parse_http_header_block(header_blob)
            else:
                status_code, headers = None, {}
                body = raw

            if 'chunked' in headers.get('transfer-encoding', '').lower():
                body = self._decode_chunked_body(body)

            if status_code in [301, 302, 303, 307, 308]:
                location = headers.get('location')
                if not location:
                    return {"ok": False, "error": f"socket 缺少跳转地址: {status_code}", "chain": redirect_chain}
                redirect_chain.append(str(status_code))
                current_url = urljoin(current_url, location)
                continue

            return {
                "ok": True,
                "url": current_url,
                "status_code": status_code,
                "headers": headers,
                "body": body,
                "chain": redirect_chain
            }

        return {"ok": False, "error": f"socket 跳转过多: {'->'.join(redirect_chain)}", "chain": redirect_chain}

    async def _socket_http_probe(self, channel):
        """
        某些 HTTP 裸流服务端返回并不完全符合 aiohttp 的解析预期，
        但播放器仍可正常消费。这里只对这类 URL 做一次异步 socket 回退，
        避免整批误杀。
        同时接住会被 aiohttp 严格解析拒绝、但播放器仍能读取的 HTTP/HTTPS HLS。
        """
        url = channel.get('url', '')
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False, "不适用 socket 回退"

        host = parsed.hostname
        if not host:
            return False, "URL 缺少主机名"

        try:
            socket_result = await self._fetch_via_socket(url)
            if not socket_result.get("ok"):
                return False, socket_result.get("error", "socket 未知异常")

            final_url = socket_result.get("url", url)
            status_code = socket_result.get("status_code")
            body = socket_result.get("body", b"")
            redirect_chain = socket_result.get("chain", [])
            redirect_suffix = f"/{'->'.join(redirect_chain)}" if redirect_chain else ""

            if status_code is not None and status_code not in [200, 206]:
                return False, f"socket HTTP异常: {status_code}{redirect_suffix}"

            if self._is_hls_playlist_payload(body):
                alive, msg = await self._check_hls_playlist_via_socket(
                    channel, final_url, body, socket_result.get('headers', {})
                )
                if alive:
                    return True, f"{msg}{redirect_suffix}"
                return False, msg

            if self._is_html_payload(body) and not self._looks_like_media_payload(body):
                return False, "socket 命中防盗链网页"

            content_type = self._get_header(socket_result.get('headers', {}), 'Content-Type')
            valid, evidence, reason = self._classify_media_payload(body, content_type)
            if valid and (len(body) > 100 or self._looks_like_ts_payload(body)):
                channel['probe_url'] = final_url
                channel['_liveness_evidence'] = evidence
                return True, f"存活 (裸流/socket回退{redirect_suffix})"

            return False, f"socket {reason}"
        except asyncio.TimeoutError:
            return False, "socket 超时"
        except Exception as e:
            return False, f"socket 异常: {type(e).__name__}:{e}"

    def _parse_cached_time(self, cached_time):
        if isinstance(cached_time, str):
            try:
                dt = datetime.strptime(cached_time, '%Y-%m-%d %H:%M:%S')
                dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                return int(dt.timestamp())
            except (TypeError, ValueError, OverflowError):
                return 0
        try:
            return int(cached_time or 0)
        except (TypeError, ValueError, OverflowError):
            return 0

    def _utc8_timestamp(self):
        tz_utc_8 = timezone(timedelta(hours=8))
        return datetime.now(tz_utc_8).strftime('%Y-%m-%d %H:%M:%S')

    def _apply_cached_probe_info(self, channel, current_time):
        original_url = channel['url']
        cached = self.cache_data.get(original_url)
        if not isinstance(cached, dict):
            return False

        # --- 新增：兼容解析旧版数字时间戳和新版UTC+8时间字符串 ---
        cached_time = cached.get("last_probed", 0)
        cached_time_int = self._parse_cached_time(cached_time)
        # -----------------------------------------------------------

        res = cached.get('resolution', '')
        codec = cached.get('codec', '')
        if res == "Unknown" and codec == "Unknown":
            self.cache_data.pop(original_url, None)
            logger.warning(f"[♻ 负缓存失效] [{channel.get('name')}] 旧版 Unknown 只代表画质探测失败，本次重新探测。")
            return False
        if current_time - cached_time_int >= self.cache_expire_sec:
            return False
        if not res or not codec:
            return False

        channel['probe_info'] = f"[{res}][{codec}]"
        logger.info(f"[⚡ 缓存命中] [{channel.get('name')}] -> {channel['probe_info']}")
        return True

    # ================= 纯内存处理核心 API =================
    async def process_channel_list(self, channels):
        """
        供外部调用的核心 API：接收标准字典列表，返回探测完毕的列表。
        输入格式要求 (每个字典): 
        {
            "name": "频道名", "url": "播放地址", "group": "所属分组", 
            "needs_check": True/False, "is_alive": True/False, "msg": ""
        }
        """
        if not channels: return []

        failed_channels, alive_target_channels = [], []
        sem = asyncio.Semaphore(self.workers)
        
        # --- Step 1: 极速测活 ---
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [self.check_stream_alive(session, ch, sem) for ch in channels if ch.get('needs_check', True)]
            if tasks:
                logger.info(f"[Step 1] 开始极速测活，并发: {self.workers}，超时: {self.timeout}s...")
                for future in asyncio.as_completed(tasks):
                    is_alive, channel, msg = await future
                    channel['is_alive'] = is_alive
                    channel['msg'] = msg
                    if is_alive:
                        logger.info(f"[√ 存活] [{channel.get('name', '未知')}]")
                        alive_target_channels.append(channel)
                    else:
                        logger.warning(f"[x 失效] [{channel.get('name', '未知')}] - {msg}")
                        failed_channels.append(channel)

        # --- Step 2: 终极质量探测 ---
        if self.enable_probe and alive_target_channels:
            logger.info(f"\n[Step 2] 启动终极质量探测引擎！存活频道数: {len(alive_target_channels)}")
            current_time = int(time.time())
            channels_to_probe = [
                channel for channel in alive_target_channels
                if not self._apply_cached_probe_info(channel, current_time)
            ]

            if not channels_to_probe:
                self.save_cache()
                logger.info("[Step 2] 全部命中有效画质缓存，无需启动 ffprobe。\n")
            elif not self.ffprobe_path:
                logger.warning("[⚠ 深度探测跳过] 系统 PATH 中未找到 ffprobe；保留 Step 1 已确认存活的频道。")
                self.save_cache()
            else:
                probe_sem = asyncio.Semaphore(4)
                probe_connector = aiohttp.TCPConnector(ssl=False)

                async with aiohttp.ClientSession(connector=probe_connector) as probe_session:
                    async def probe_worker(channel):
                        original_url = channel['url']

                        async with probe_sem:
                            logger.info(f"[🔍 深度探测中] [{channel.get('name')}]...")
                            refreshed_url = await self._refresh_hls_probe_url(probe_session, channel)
                            target_to_probe = refreshed_url or channel.get('probe_url', original_url)
                            referer = channel.get('_probe_referer')
                            info, probe_status, probe_detail = await self.probe_video_info(
                                target_to_probe, referer=referer, return_status=True,
                                direct_segment=target_to_probe != original_url
                            )

                            if (
                                (not info or info.get('resolution') == "未知")
                                and target_to_probe != original_url
                            ):
                                logger.debug(f"[🔄 自动重试] [{channel.get('name')}] 切片探测失败，回退解析原 M3U8...")
                                info, probe_status, probe_detail = await self.probe_video_info(
                                    original_url, referer=original_url, return_status=True,
                                    direct_segment=False
                                )

                            if info and info['resolution'] != "未知":
                                channel['probe_info'] = f"[{info['resolution']}][{info['codec']}]"
                                self.cache_data[original_url] = info
                                logger.info(f"[🎯 探测成功] [{channel.get('name')}] -> {channel['probe_info']}")
                            else:
                                # --- 新增：生成当前 UTC+8 时间格式字符串 ---
                                fmt_time = self._utc8_timestamp()
                                # ----------------------------------------

                                text_res = channel.get('text_res', '')
                                evidence = channel.get('_liveness_evidence', 'weak')
                                text_fallback_allowed = (
                                    probe_status != 'no_video'
                                    and not (evidence == 'weak' and probe_status == 'error')
                                )
                                if text_res and text_fallback_allowed:
                                    fallback_codec = info.get('codec', 'M3U8') if info else 'M3U8'
                                    channel['probe_info'] = f"[{text_res}][{fallback_codec}]"
                                    self.cache_data[original_url] = {"resolution": text_res, "codec": fallback_codec, "last_probed": fmt_time}
                                    logger.info(f"[📝 文本保底成功] [{channel.get('name')}] -> {channel['probe_info']}")
                                else:
                                    should_remove = (
                                        probe_status == 'no_video'
                                        or (evidence == 'weak' and probe_status == 'error')
                                    )
                                    if should_remove:
                                        channel['is_alive'] = False
                                        channel['msg'] = "深度探测确认无可播放视频流"
                                        failed_channels.append(channel)
                                        logger.warning(f"[💥 终极剔除] [{channel.get('name')}] 假活源/无画质参数，抹除！")
                                    else:
                                        channel['probe_warning'] = probe_detail or probe_status
                                        logger.warning(
                                            f"[⚠ 深探不确定] [{channel.get('name')}] "
                                            f"极速测活已有媒体证据，ffprobe={probe_status}，保留频道并等待下次重试。"
                                        )

                    probe_tasks = [probe_worker(ch) for ch in channels_to_probe]
                    if probe_tasks:
                        await asyncio.gather(*probe_tasks)
                        self.save_cache()
                        logger.info("[Step 2] 质量探测完成，缓存已更新！\n")

        self.write_failed_log(failed_channels)
        return channels # 返回更新了状态的整个列表

    # ================= 底层探测逻辑 (不动) =================
    async def check_stream_alive(self, session, channel, sem):
        url = channel['url']
        async with sem:
            try:
                client_timeout = aiohttp.ClientTimeout(total=self.timeout, connect=self.timeout)
                async with session.get(url, headers=self.headers, timeout=client_timeout, ssl=False, allow_redirects=True) as response:
                    if response.status not in [200, 206]: return False, channel, f"HTTP异常: {response.status}"

                    chunk = await response.content.read(4096)
                    if not chunk: return False, channel, "未返回数据"
                    if len(chunk) <= 100:
                        chunk = await self._read_to_minimum(response.content, chunk)

                    final_url = str(response.url)
                    content_type = self._get_header(response.headers, 'Content-Type')

                    if self._is_hls_playlist_payload(chunk):
                        playlist_data = await self._read_limited_content(
                            response.content, 64 * 1024, chunk
                        )
                        alive, msg = await self._check_hls_playlist(
                            session, channel, final_url, playlist_data, response.headers
                        )
                        return alive, channel, msg

                    if self._is_html_payload(chunk) and not self._looks_like_media_payload(chunk): return False, channel, "防盗链网页"
                    valid, evidence, reason = self._classify_media_payload(chunk, content_type)
                    if valid and len(chunk) > 100:
                        channel['probe_url'] = final_url
                        channel['_liveness_evidence'] = evidence
                        return True, channel, f"存活 (裸流)"
                    else: return False, channel, reason

            except asyncio.TimeoutError:
                socket_alive, socket_msg = await self._socket_http_probe(channel)
                if socket_alive:
                    return True, channel, socket_msg
                return False, channel, "请求/读取超时"
            except Exception as e:
                socket_alive, socket_msg = await self._socket_http_probe(channel)
                if socket_alive:
                    return True, channel, socket_msg
                return False, channel, f"网络异常({type(e).__name__}: {e}; {socket_msg})"

    async def _terminate_probe_process(self, process):
        if process is None or process.returncode is not None:
            return
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except Exception:
            pass

    async def probe_video_info(
        self, probe_url, referer=None, return_status=False, direct_segment=False
    ):
        if not self.ffprobe_path:
            result = (None, 'unavailable', '系统 PATH 中未找到 ffprobe')
            return result if return_status else None

        cmd = [
            self.ffprobe_path, '-v', 'error', '-print_format', 'json',
            '-show_streams', '-select_streams', 'v:0',
            '-probesize', '8000000', '-analyzeduration', '10000000',
            '-user_agent', self.headers["User-Agent"],
            '-rw_timeout', '8000000' if direct_segment else '12000000',
        ]
        if not direct_segment:
            cmd.extend([
                '-reconnect', '1', '-reconnect_streamed', '1',
                '-reconnect_delay_max', '2',
            ])
        if referer and urlparse(probe_url).scheme in ('http', 'https'):
            cmd.extend(['-referer', referer])
        cmd.append(probe_url)

        process = None
        active_probe_timeout = min(6.0, self.probe_timeout) if direct_segment else self.probe_timeout
        try:
            process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=active_probe_timeout
            )
            stderr_text = stderr.decode('utf-8', errors='replace').strip()[-500:]
            if process.returncode not in (0, None):
                result = (None, 'error', stderr_text or f"ffprobe退出码 {process.returncode}")
                return result if return_status else None
            if not stdout:
                result = (None, 'error', stderr_text or "ffprobe未返回JSON")
                return result if return_status else None

            try:
                data = json.loads(stdout.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                result = (None, 'error', f"ffprobe JSON解析失败: {e}")
                return result if return_status else None

            streams = data.get('streams', [])
            if not streams:
                result = (None, 'no_video', stderr_text or "ffprobe确认未发现视频轨")
                return result if return_status else None

            stream = streams[0]
            try:
                width = int(stream.get('width', 0) or 0)
                height = int(stream.get('height', 0) or 0)
            except (TypeError, ValueError):
                width, height = 0, 0
            codec = str(stream.get('codec_name', 'unknown') or 'unknown').upper()

            res_name = f"{width}x{height}"
            if height >= 2160: res_name = "4K"
            elif height >= 1080: res_name = "1080P"
            elif height >= 720: res_name = "720P"
            elif height > 0: res_name = "标清"
            else: res_name = "未知"

            # --- 新增：返回 UTC+8 格式化字符串 ---
            fmt_time = self._utc8_timestamp()
            info = {"resolution": res_name, "codec": codec, "last_probed": fmt_time}
            # ------------------------------------
            status = 'success' if res_name != "未知" else 'video_unknown'
            result = (info, status, stderr_text)
            return result if return_status else info
        except asyncio.CancelledError:
            await self._terminate_probe_process(process)
            raise
        except asyncio.TimeoutError:
            await self._terminate_probe_process(process)
            result = (None, 'timeout', f"ffprobe超过 {active_probe_timeout:.1f}s 已终止")
            return result if return_status else None
        except FileNotFoundError:
            result = (None, 'unavailable', '系统 PATH 中未找到 ffprobe')
            return result if return_status else None
        except NotImplementedError:
            result = (
                None,
                'unavailable',
                '当前 Windows asyncio 事件循环不支持子进程，请使用 Proactor',
            )
            return result if return_status else None
        except Exception as e:
            logger.debug(f"ffprobe异常 [{probe_url}]: {type(e).__name__}: {e}")
            result = (None, 'error', f"{type(e).__name__}: {e}")
            return result if return_status else None

    # ================= 规范化的日志输出 =================
    def write_failed_log(self, failed_channels):
        if not failed_channels: return
        log_path = os.path.join(LOG_DIR, "failed_channels.txt")
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(f"--- 失效检测报告 ---\n总计失效数量: {len(failed_channels)}\n\n")
                for ch in failed_channels:
                    f.write(f"频道: {ch.get('name')}\n原因: {ch.get('msg')}\n链接: {ch.get('url')}\n" + "-"*40 + "\n")
            logger.info(f"失效报告已保存至: {log_path}")
        except Exception: pass


    # ================= CLI 独立运行专属方法 =================
    def parse_m3u_file(self, file_path):
        channels = []
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
        except Exception as e: return channels
        if not lines or not lines[0].startswith('#EXTM3U'): return channels

        current_extinf, current_group = "", ""
        for line in lines[1:]:
            line = line.strip()
            if not line: continue
            if line.startswith('#EXTINF'):
                current_extinf = line
                match = re.search(r'group-title="([^"]+)"', line)
                current_group = match.group(1) if match else ""
            elif line.startswith('http'):
                url = line
                if current_extinf:
                    needs_check = True
                    if self.target_group and self.target_group.lower() not in current_group.lower():
                        needs_check = False
                    name_match = current_extinf.split(',')
                    ch_name = name_match[-1] if len(name_match) > 1 else "未知频道"

                    channels.append({
                        "name": ch_name, "extinf": current_extinf, "url": url, 
                        "group": current_group, "needs_check": needs_check,
                        "is_alive": not needs_check, "msg": "未检测" if not needs_check else "",
                        "probe_info": "", "probe_url": url, "text_res": ""
                    })
                    current_extinf = "" 
        
        total = len(channels)
        to_check = sum(1 for c in channels if c['needs_check'])
        logger.info(f"[CLI 解析] 总计: {total} | 需检测: {to_check} | 免检: {total - to_check}")
        return channels

    def write_m3u_file(self, all_channels, output_path):
        alive_count = sum(1 for ch in all_channels if ch.get('is_alive', False))
        logger.info(f"==================================================")
        logger.info(f"处理完毕！原始总数: {len(all_channels)} | 最终输出存活: {alive_count}")
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write('#EXTM3U\n')
                for ch in all_channels:
                    if ch.get('is_alive', False): 
                        if ch.get('probe_info'):
                            parts = ch['extinf'].rsplit(',', 1)
                            if len(parts) == 2:
                                f.write(f"{parts[0]},{parts[1]} {ch['probe_info']}\n")
                            else: f.write(f"{ch['extinf']} {ch['probe_info']}\n")
                        else: f.write(f"{ch['extinf']}\n")
                        f.write(f"{ch['url']}\n")
            logger.info(f"[成功] 纯净列表已保存至: {output_path}")
        except Exception: pass

    def write_failed_m3u_file(self, all_channels, failed_path):
        failed_channels = [ch for ch in all_channels if not ch.get('is_alive', False) and ch.get('needs_check', False)]
        if not failed_channels: return
        try:
            with open(failed_path, 'w', encoding='utf-8') as f:
                f.write('#EXTM3U\n')
                for ch in failed_channels:
                    parts = ch['extinf'].rsplit(',', 1)
                    if len(parts) == 2: f.write(f"{parts[0]},{parts[1]} [{ch.get('msg')}]\n")
                    else: f.write(f"{ch['extinf']}\n")
                    f.write(f"{ch['url']}\n")
        except Exception: pass

async def cli_main(args):
    checker = IPTVCheckerFinal(
        target_group=args.group, timeout=args.timeout, workers=args.workers, 
        enable_probe=args.enable_probe, cache_expire_hours=args.cache_expire
    )
    channels = checker.parse_m3u_file(args.input)
    if channels:
        processed_channels = await checker.process_channel_list(channels)
        checker.write_m3u_file(processed_channels, args.output)
        if args.failed_output:
            checker.write_failed_m3u_file(processed_channels, args.failed_output)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("-o", "--output", default="output.m3u")
    parser.add_argument("-f", "--failed-output")
    parser.add_argument("-g", "--group")
    parser.add_argument("-t", "--timeout", type=int, default=10)
    parser.add_argument("-w", "--workers", type=int, default=30)
    parser.add_argument("--enable-probe", action="store_true")
    parser.add_argument("--cache-expire", type=int, default=24)
    args = parser.parse_args()
    
    start_time = time.time()
    if sys.platform == 'win32':
        event_loop_policy = (
            asyncio.WindowsProactorEventLoopPolicy()
            if args.enable_probe else asyncio.WindowsSelectorEventLoopPolicy()
        )
        asyncio.set_event_loop_policy(event_loop_policy)
    asyncio.run(cli_main(args))
    logger.info(f"任务结束！总耗时: {time.time() - start_time:.2f} 秒")

if __name__ == "__main__":
    main()
