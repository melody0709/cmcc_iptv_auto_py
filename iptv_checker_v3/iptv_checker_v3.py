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
import ssl
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone, timedelta  # <-- 新增：用于处理 UTC+8 时间格式

# ================= 动态路径与日志配置 =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "log")
os.makedirs(LOG_DIR, exist_ok=True)  # 自动创建 log 目录

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
        
        # 缓存文件优先放在 config 目录，没有则放当前目录
        config_dir = os.path.join(BASE_DIR, "config")
        if os.path.exists(config_dir):
            self.cache_file = os.path.join(config_dir, "probe_cache.json")
        else:
            self.cache_file = os.path.join(BASE_DIR, "probe_cache.json")
            
        self.cache_data = self.load_cache()
        
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Connection": "keep-alive"
        }

    def load_cache(self):
        if not os.path.exists(self.cache_file): return {}
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception: return {}

    def save_cache(self):
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache_data, f, ensure_ascii=False, indent=2)
        except Exception: pass

    def _is_playlist_like_url(self, url):
        url_lower = str(url or "").lower()
        return any(token in url_lower for token in (".m3u8", ".m3u", "playlist", "manifest"))

    def _is_html_payload(self, data):
        data_lower = (data or b"").lower()
        return b'<html' in data_lower and b'<body' in data_lower

    def _looks_like_ts_payload(self, data):
        if not data:
            return False
        sample = data[:188 * 4]
        if len(sample) < 188:
            return False
        sync_hits = 0
        for offset in range(min(188, len(sample))):
            if sample[offset:offset + 1] != b'\x47':
                continue
            if len(sample) > offset + 188 and sample[offset + 188:offset + 189] == b'\x47':
                sync_hits += 1
            if len(sample) > offset + 376 and sample[offset + 376:offset + 377] == b'\x47':
                sync_hits += 1
            if sync_hits >= 2:
                return True
        return False

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
            await writer.drain()

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
                    await writer.wait_closed()
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
        """
        url = channel.get('url', '')
        parsed = urlparse(url)
        if parsed.scheme != 'http' or self._is_playlist_like_url(url):
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

            if b'#EXTM3U' in body:
                text_str = body.decode('utf-8', errors='ignore')
                res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', text_str)
                if res_match:
                    h = int(res_match.group(2))
                    if h >= 2160: channel['text_res'] = "4K"
                    elif h >= 1080: channel['text_res'] = "1080P"
                    elif h >= 720: channel['text_res'] = "720P"
                    else: channel['text_res'] = "标清"

                first_link = next((line.strip() for line in text_str.split('\n') if line.strip() and not line.startswith('#')), None)
                channel['probe_url'] = urljoin(final_url, first_link) if first_link else final_url
                return True, f"存活 (M3U8/socket回退{redirect_suffix})"

            if self._is_html_payload(body):
                return False, "socket 命中防盗链网页"

            if len(body) > 100 or self._looks_like_ts_payload(body):
                channel['probe_url'] = final_url
                return True, f"存活 (裸流/socket回退{redirect_suffix})"

            return False, f"socket 数据异常过小: {len(body)} bytes"
        except asyncio.TimeoutError:
            return False, "socket 超时"
        except Exception as e:
            return False, f"socket 异常: {type(e).__name__}:{e}"

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
            probe_sem = asyncio.Semaphore(4) 
            
            async def probe_worker(channel):
                original_url = channel['url']
                
                if original_url in self.cache_data:
                    cached = self.cache_data[original_url]
                    
                    # --- 新增：兼容解析旧版数字时间戳和新版UTC+8时间字符串 ---
                    cached_time = cached.get("last_probed", 0)
                    if isinstance(cached_time, str):
                        try:
                            dt = datetime.strptime(cached_time, '%Y-%m-%d %H:%M:%S')
                            dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
                            cached_time_int = int(dt.timestamp())
                        except ValueError:
                            cached_time_int = 0
                    else:
                        cached_time_int = cached_time
                    # -----------------------------------------------------------
                    
                    if current_time - cached_time_int < self.cache_expire_sec:
                        res, codec = cached.get('resolution', ''), cached.get('codec', '')
                        if res == "Unknown" and codec == "Unknown":
                            channel['is_alive'] = False
                            channel['msg'] = "深度探测无视频流数据 (缓存)"
                            failed_channels.append(channel)
                            logger.warning(f"[💥 缓存剔除] [{channel.get('name')}] 此源已被记录为死流，彻底移除。")
                            return
                        if res and codec:
                            channel['probe_info'] = f"[{res}][{codec}]"
                            logger.info(f"[⚡ 缓存命中] [{channel.get('name')}] -> {channel['probe_info']}")
                        return

                async with probe_sem:
                    logger.info(f"[🔍 深度探测中] [{channel.get('name')}]...")
                    target_to_probe = channel.get('probe_url', original_url)
                    info = await self.probe_video_info(target_to_probe)
                    
                    if not info and target_to_probe != original_url:
                        logger.debug(f"[🔄 自动重试] [{channel.get('name')}] 切片探测失败，回退解析原 M3U8...")
                        info = await self.probe_video_info(original_url)
                    
                    if info and info['resolution'] != "未知":
                        channel['probe_info'] = f"[{info['resolution']}][{info['codec']}]"
                        self.cache_data[original_url] = info
                        logger.info(f"[🎯 探测成功] [{channel.get('name')}] -> {channel['probe_info']}")
                    else:
                        # --- 新增：生成当前 UTC+8 时间格式字符串 ---
                        tz_utc_8 = timezone(timedelta(hours=8))
                        fmt_time = datetime.now(tz_utc_8).strftime('%Y-%m-%d %H:%M:%S')
                        # ----------------------------------------
                        
                        text_res = channel.get('text_res', '')
                        if text_res:
                            channel['probe_info'] = f"[{text_res}][M3U8]"
                            self.cache_data[original_url] = {"resolution": text_res, "codec": "M3U8", "last_probed": fmt_time}
                            logger.info(f"[📝 文本保底成功] [{channel.get('name')}] -> {channel['probe_info']}")
                        else:
                            self.cache_data[original_url] = {"resolution": "Unknown", "codec": "Unknown", "last_probed": fmt_time}
                            channel['is_alive'] = False
                            channel['msg'] = "深度探测彻底失败，无视频流"
                            failed_channels.append(channel)
                            logger.warning(f"[💥 终极剔除] [{channel.get('name')}] 假活源/无画质参数，抹除！")

            probe_tasks = [probe_worker(ch) for ch in alive_target_channels]
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
                client_timeout = aiohttp.ClientTimeout(total=self.timeout, connect=self.timeout / 2)
                async with session.get(url, headers=self.headers, timeout=client_timeout, ssl=False, allow_redirects=True) as response:
                    if response.status not in [200, 206]: return False, channel, f"HTTP异常: {response.status}"

                    chunk = await response.content.read(4096)
                    if not chunk: return False, channel, "未返回数据"

                    final_url = str(response.url)

                    if b'#EXTM3U' in chunk:
                        text_str = chunk.decode('utf-8', errors='ignore')
                        res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', text_str)
                        if res_match:
                            h = int(res_match.group(2))
                            if h >= 2160: channel['text_res'] = "4K"
                            elif h >= 1080: channel['text_res'] = "1080P"
                            elif h >= 720: channel['text_res'] = "720P"
                            else: channel['text_res'] = "标清"

                        first_link = next((line.strip() for line in text_str.split('\n') if line.strip() and not line.startswith('#')), None)
                        if first_link:
                            target_url = urljoin(final_url, first_link)
                            try:
                                seg_timeout = aiohttp.ClientTimeout(total=self.timeout / 2)
                                async with session.get(target_url, headers=self.headers, timeout=seg_timeout, ssl=False) as seg_resp:
                                    if seg_resp.status in [200, 206]:
                                        channel['probe_url'] = target_url
                                        return True, channel, "存活 (M3U8)"
                                    else: return False, channel, f"死切片"
                            except Exception: return False, channel, "切片无法连通"
                        else: return False, channel, "提取切片失败"

                    if b'<html' in chunk.lower() and b'<body' in chunk.lower(): return False, channel, "防盗链网页"
                    if len(chunk) > 100:
                        channel['probe_url'] = final_url
                        return True, channel, f"存活 (裸流)"
                    else: return False, channel, "数据异常过小"

            except asyncio.TimeoutError:
                return False, channel, "请求/读取超时"
            except Exception as e:
                socket_alive, socket_msg = await self._socket_http_probe(channel)
                if socket_alive:
                    return True, channel, socket_msg
                return False, channel, f"网络异常({type(e).__name__}: {e}; {socket_msg})"

    async def probe_video_info(self, probe_url):
        cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_streams', '-select_streams', 'v:0',
            '-probesize', '8000000', '-analyzeduration', '10000000',
            '-user_agent', self.headers["User-Agent"],
            '-reconnect', '1', '-reconnect_streamed', '1', '-reconnect_delay_max', '2',
            probe_url
        ]
        try:
            process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15.0)
            if not stdout: return None
                
            data = json.loads(stdout.decode('utf-8'))
            if 'streams' in data and len(data['streams']) > 0:
                stream = data['streams'][0]
                width = stream.get('width', 0)
                height = stream.get('height', 0)
                codec = stream.get('codec_name', 'unknown').upper()
                
                res_name = f"{width}x{height}"
                if height >= 2160: res_name = "4K"
                elif height >= 1080: res_name = "1080P"
                elif height >= 720: res_name = "720P"
                elif height > 0: res_name = "标清"
                else: res_name = "未知"
                
                # --- 新增：返回 UTC+8 格式化字符串 ---
                tz_utc_8 = timezone(timedelta(hours=8))
                fmt_time = datetime.now(tz_utc_8).strftime('%Y-%m-%d %H:%M:%S')
                return {"resolution": res_name, "codec": codec, "last_probed": fmt_time}
                # ------------------------------------
        except Exception: return None
        return None

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
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(cli_main(args))
    logger.info(f"任务结束！总耗时: {time.time() - start_time:.2f} 秒")

if __name__ == "__main__":
    main()
