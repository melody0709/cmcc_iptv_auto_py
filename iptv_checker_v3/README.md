# IPTV Checker V3 — 智能直播源检测与画质探测引擎

IPTV Checker V3 专为 NAS（如 Synology）与高阶用户打造，支持批量 M3U 直播源的极速存活检测与深度画质解析。

当前作为主项目 v1.6 的检测引擎集成使用；外部 M3U 分组的模糊匹配与重命名逻辑位于 `tv.py`，本引擎 API 与探测流程保持不变。

## 核心特性

- 极速测活
  - 基于 `aiohttp` 异步架构，4KB 魔法字节快速判断有效性
  - 自动处理 M3U8 和裸流
  - 检测防盗链网页/空流/响应异常

- 深度画质探测（可选）
  - 调用系统 `ffprobe` 提取视频流 `resolution` 与 `codec`
  - 缩短探测时间并避免高 CPU 负载
  - 自动回退:
    1. 直接 probe 地址
    2. M3U8 切片检测
    3. 解析 M3U8 文本标签

- 本地缓存机制
  - 结果写入 `config/probe_cache.json`
  - 默认缓存有效期 24 小时
  - 已判定死流源自动跳过

- 自包含 API + CLI
  - 可直接调用 `IPTVCheckerFinal` 进行内存列表处理
  - 也支持命令行方式运行，自动输出成活/失效列表

## 环境要求

- Python 3.8+
- `aiohttp` (`pip install aiohttp`)
- 如果启用 `--enable-probe`：需要 `ffprobe` 可用（系统 PATH）

## 目录结构（示例）

- `iptv_checker_v3.py`：主程序与类定义
- `config/probe_cache.json`：画质探测缓存（自动生成）
- `log/checker_final.log`：运行日志
- `log/failed_channels.txt`：失效频道报告

## CLI 使用

### 基本测活

```bash
python iptv_checker_v3.py input.m3u -o alive.m3u
```

### 推荐完整命令

```bash
python iptv_checker_v3.py tv.m3u \
  -g "港澳台" \
  -t 5 \
  -w 30 \
  -o tv_clean.m3u \
  -f tv_failed.m3u \
  --enable-probe \
  --cache-expire 24
```

### 参数说明

- `input`：输入 M3U 文件（必须以 `#EXTM3U` 开头）
- `-o --output`：存活频道输出文件（默认 `output.m3u`）
- `-f --failed-output`：失效频道输出文件（可选）
- `-g --group`：只检测组名包含关键字的频道，其他保留
- `-t --timeout`：单次请求超时（秒，默认 10）
- `-w --workers`：并发数（默认 30）
- `--enable-probe`：启用 `ffprobe` 深度画质探测
- `--cache-expire`：缓存过期时间（小时，默认 24）

## API 调用示例（Python）

```python
import asyncio
from iptv_checker_v3 import IPTVCheckerFinal

channels = [
    {
        "name": "翡翠台",
        "url": "http://example.com/live.m3u8",
        "group": "港澳台",
        "needs_check": True
    }
]

checker = IPTVCheckerFinal(timeout=8, workers=30, enable_probe=True, cache_expire_hours=24)
processed = asyncio.run(checker.process_channel_list(channels))

for ch in processed:
    print(f"{ch['name']} -> 存活: {ch.get('is_alive')}, 画质: {ch.get('probe_info')}")
```

### API 输入/输出字段

- 输入字段：`name`, `url`, `group`, `needs_check`
- 输出字段：`is_alive`, `msg`, `probe_info`, `probe_url`, `text_res`

## 注意

- 开启 `--enable-probe` 但系统未安装 `ffprobe` 时，程序会跳过深度探测并继续测活
- 错误日志请查看 `log/checker_final.log`
- 若需刷新画质信息，删除 `config/probe_cache.json`
- 并发数过高可能导致部分源被限流或返回 403，建议根据网络/设备情况调节

## 许可证

MIT License
