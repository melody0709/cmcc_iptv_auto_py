# -*- coding: utf-8 -*-
import json
import re
import os
import io
import sys
import requests
import asyncio
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
CHANNEL_ORDER_FILE = os.path.join(CONFIG_DIR, "channel_order.json")

M3U_SOURCE_URL = "https://raw.githubusercontent.com/Jsnzkpg/Jsnzkpg/Jsnzkpg/Jsnzkpg1.m3u"
OUTPUT_FILENAME = "web.m3u"
LOCAL_RAW_FILENAME = "web_raw.m3u"

EXTRACT_GROUPS = [
    # (匹配模式, 目标分组名, 匹配方式)
    # 匹配方式:
    #   "exact"    — 远程 group-title 完全等于匹配模式
    #   "contains" — 远程 group-title 包含匹配模式子串
    #   "fuzzy"    — tv.py 的片段模糊匹配（共享>=3字符片段）
    ("[三网1]央卫视", "央卫视", "contains"),
    ("港澳台", "港澳台", "contains"),
]

# 港澳台组整体保留，不参与 GROUP_DEFINITIONS 关键词二次分类
PRESERVE_GROUPS = {"港澳台"}

# 流检测配置
ENABLE_STREAM_CHECK = True
CHECK_TIMEOUT = 5
CHECK_WORKERS = 4
ENABLE_PROBE = True
CHECK_CACHE_EXPIRE = 24

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

checker_dir = os.path.join(BASE_DIR, 'iptv_checker_v3')
if checker_dir not in sys.path:
    sys.path.append(checker_dir)

try:
    from iptv_checker_v3 import IPTVCheckerFinal
    HAS_CHECKER = True
except ImportError:
    print("警告: 未找到 iptv_checker_v3，智能检测功能将被禁用。")
    HAS_CHECKER = False

from tv import (
    GROUP_DEFINITIONS,
    GROUP_OUTPUT_ORDER,
    GROUP_CLASSIFICATION_PRIORITY,
    is_blacklisted,
    categorize_channel,
    sort_channels_by_order,
    has_shared_group_fragment,
    normalize_external_channel_url,
    download_with_retry,
    load_json_config_file,
    M3U_EPG_URL,
    initialize_environment,
)

import tv as _tv


def match_extract_group(group_title, extract_rules):
    source = str(group_title or '').strip()
    if not source:
        return None
    for pattern, target_name, mode in extract_rules:
        pattern_str = str(pattern or '').strip()
        if not pattern_str:
            continue
        if mode == "exact":
            if source == pattern_str:
                return target_name
        elif mode == "contains":
            if pattern_str in source:
                return target_name
        elif mode == "fuzzy":
            if has_shared_group_fragment(source, pattern_str):
                return target_name
    return None


def download_m3u(url):
    raw_path = os.path.join(BASE_DIR, LOCAL_RAW_FILENAME)
    try:
        print(f"正在下载远程 M3U: {url}")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = download_with_retry(url, headers=headers)
        if resp and resp.text.strip().startswith('#EXTM3U'):
            print(f"成功下载，大小: {len(resp.text)} 字节")
            with open(raw_path, 'w', encoding='utf-8') as f:
                f.write(resp.text)
            print(f"已保存远程原始文件到: {raw_path}")
            return resp.text, "network"
    except Exception as e:
        print(f"下载失败: {e}")

    if os.path.exists(raw_path):
        try:
            with open(raw_path, 'r', encoding='utf-8', errors='ignore') as f:
                cached = f.read()
            if cached.strip().startswith('#EXTM3U'):
                print(f"远程 M3U 网络更新失败，已回退到本地缓存: {raw_path}")
                return cached, "cache"
            print(f"本地缓存文件存在，但格式无效: {raw_path}")
        except Exception as e:
            print(f"读取本地缓存失败: {e}")

    return None, "none"


def parse_m3u_channels(m3u_content, extract_rules):
    if not m3u_content or not extract_rules:
        return [], [], []

    channels = []
    blacklisted_channels = []
    duplicate_channels = []
    seen_urls = set()
    matched_source_groups = set()

    current_channel = None
    for line in m3u_content.strip().split('\n'):
        line = line.strip()
        if line.startswith('#EXTINF'):
            current_channel = {
                'extinf_line': line,
                'extra_lines': [],
                'attributes': {},
                'url': None,
                'title': line.split(',')[-1].strip(),
                'group_title': '',
                'original_group_title': ''
            }
            for k, v in re.findall(r'(\S+?)="([^"]*)"', line):
                current_channel['attributes'][k] = v
                if k == 'group-title':
                    current_channel['group_title'] = v
                    current_channel['original_group_title'] = v
        elif line.startswith('#') and current_channel:
            current_channel['extra_lines'].append(line)
        elif line and current_channel:
            current_channel['url'] = line
            matched_target = match_extract_group(current_channel['group_title'], extract_rules)
            if matched_target:
                matched_source_groups.add(current_channel['original_group_title'])
                current_channel['matched_target_group'] = matched_target

                if is_blacklisted({'title': current_channel['title'], 'zteurl': current_channel['url']}):
                    blacklisted_channels.append({
                        'title': current_channel['title'],
                        'group_title': current_channel['group_title'],
                        'reason': '黑名单规则匹配'
                    })
                else:
                    norm_url = normalize_external_channel_url(line)
                    if norm_url in seen_urls:
                        duplicate_channels.append({
                            'title': current_channel['title'],
                            'group_title': current_channel['group_title'],
                            'url': line,
                            'reason': 'URL重复，保留首次出现'
                        })
                    else:
                        seen_urls.add(norm_url)
                        channels.append(current_channel.copy())
            current_channel = None

    print(f"从远程 M3U 提取了 {len(channels)} 个频道")
    if matched_source_groups:
        print(f"匹配到的远程分组: {', '.join(sorted(matched_source_groups))}")
    if blacklisted_channels:
        print(f"已过滤 {len(blacklisted_channels)} 个黑名单频道")
    if duplicate_channels:
        print(f"已按 URL 去重 {len(duplicate_channels)} 个频道")
    return channels, blacklisted_channels, duplicate_channels


def classify_channels(channels, preserve_groups):
    grouped = {g: [] for g in GROUP_DEFINITIONS.keys()}
    for g in GROUP_OUTPUT_ORDER:
        if g not in grouped:
            grouped[g] = []

    for ch in channels:
        target_group = ch.get('matched_target_group', '')

        if target_group in preserve_groups:
            if target_group not in grouped:
                grouped[target_group] = []
            grouped[target_group].append(ch)
        else:
            category = categorize_channel(ch['title'])
            if category not in grouped:
                grouped[category] = []
            grouped[category].append(ch)

    return grouped


def apply_sorting(grouped_channels, channel_order):
    for group_name, channels in grouped_channels.items():
        order_list = channel_order.get(group_name, [])
        if order_list:
            grouped_channels[group_name] = sort_channels_by_order(channels, order_list)
        else:
            grouped_channels[group_name].sort(key=lambda x: x.get('title', ''))
    return grouped_channels


def run_stream_check(grouped_channels):
    if not HAS_CHECKER or not ENABLE_STREAM_CHECK:
        print("流检测已禁用，跳过。")
        return grouped_channels

    print("\n" + "=" * 50)
    print("启动底层视频流智能检测引擎 (iptv_checker_v3)")
    print("=" * 50)

    checker = IPTVCheckerFinal(
        target_group=None,
        timeout=CHECK_TIMEOUT,
        workers=CHECK_WORKERS,
        enable_probe=ENABLE_PROBE,
        cache_expire_hours=CHECK_CACHE_EXPIRE
    )

    channels_to_check = []
    for group, ch_list in grouped_channels.items():
        for ch in ch_list:
            url = ch.get('url')
            if not url:
                continue
            channels_to_check.append({
                "name": ch.get("title", "Unknown"),
                "url": url,
                "group": group,
                "needs_check": True,
                "is_alive": False,
                "msg": "",
                "_ref": ch,
            })

    if not channels_to_check:
        print("没有需要检测的频道。")
        return grouped_channels

    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    processed = asyncio.run(checker.process_channel_list(channels_to_check))

    dead_count = 0
    for res in processed:
        orig_ch = res["_ref"]
        if not res.get("is_alive", False):
            orig_ch["_is_dead"] = True
            dead_count += 1
        elif res.get("probe_info"):
            orig_ch["probe_info"] = res["probe_info"]

    for group in grouped_channels:
        grouped_channels[group] = [ch for ch in grouped_channels[group] if not ch.get("_is_dead")]

    print(f"\n智能检测完毕！共剔除了 {dead_count} 个失效源。\n")
    return grouped_channels


def generate_web_m3u(grouped_channels):
    content = []
    header = f'#EXTM3U x-tvg-url="{M3U_EPG_URL}"' if M3U_EPG_URL else "#EXTM3U"
    content.append(header)

    for group in GROUP_OUTPUT_ORDER:
        ch_list = grouped_channels.get(group, [])
        if not ch_list:
            continue
        for ch in ch_list:
            attrs = ch.get('attributes', {}).copy()
            attrs['group-title'] = group

            title = ch.get('title', '')
            if ch.get('probe_info'):
                title = f"{title} {ch['probe_info']}"

            attr_parts = ['#EXTINF:-1']
            for k, v in attrs.items():
                attr_parts.append(f'{k}="{v}"')
            content.append(' '.join(attr_parts) + f',{title}')

            for extra in ch.get('extra_lines', []):
                content.append(extra)

            content.append(ch.get('url', ''))

    return '\n'.join(content) + '\n'


def main():
    import argparse
    parser = argparse.ArgumentParser(description="从远程 M3U 提取指定分组频道并生成 M3U 文件")
    parser.add_argument("-sc", "--skip-check", action="store_true", help="禁用流检测")
    parser.add_argument("-sp", "--skip-probe", action="store_true", help="禁用 ffprobe 画质探测")
    args = parser.parse_args()

    if args.skip_check:
        global ENABLE_STREAM_CHECK
        ENABLE_STREAM_CHECK = False
        print("[CLI] 已禁用流检测")
    if args.skip_probe:
        global ENABLE_PROBE
        ENABLE_PROBE = False
        print("[CLI] 已禁用 ffprobe 画质探测")

    initialize_environment()

    print("=" * 50)
    print("web.py — 远程 M3U 频道提取工具")
    print("=" * 50)

    print(f"\n提取规则:")
    for pattern, target, mode in EXTRACT_GROUPS:
        print(f"  [{mode}] '{pattern}' → '{target}'")
    print(f"保留原组（不二次分类）: {', '.join(PRESERVE_GROUPS)}")

    m3u_content, m3u_source = download_m3u(M3U_SOURCE_URL)
    if not m3u_content:
        print("错误: 无法下载远程 M3U，程序退出")
        sys.exit(1)
    print(f"当前使用的数据来源: {'网络更新' if m3u_source == 'network' else '本地缓存'}")

    channels, blacklisted, duplicates = parse_m3u_channels(m3u_content, EXTRACT_GROUPS)
    if not channels:
        print("未提取到任何频道，程序退出")
        sys.exit(0)

    channel_order = load_json_config_file(CHANNEL_ORDER_FILE)
    if channel_order:
        print(f"已加载频道排序配置: {CHANNEL_ORDER_FILE}")

    grouped_channels = classify_channels(channels, PRESERVE_GROUPS)
    grouped_channels = apply_sorting(grouped_channels, channel_order)

    grouped_channels = run_stream_check(grouped_channels)

    m3u_output = generate_web_m3u(grouped_channels)
    output_path = os.path.join(BASE_DIR, OUTPUT_FILENAME)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(m3u_output)

    total = sum(len(v) for v in grouped_channels.values())
    print(f"\n已生成: {output_path}")
    print(f"总频道数: {total}")
    for group in GROUP_OUTPUT_ORDER:
        ch_list = grouped_channels.get(group, [])
        if ch_list:
            print(f"  {group}: {len(ch_list)} 个频道")


if __name__ == "__main__":
    main()
