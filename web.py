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

# 港澳台来源（远程）
M3U_SOURCE_URL = "https://iptv.445569.xyz/live.m3u"
# 央视 / 卫视来源（内网，只取数据，不做流检测）
LAN_M3U_URL = "http://10.10.10.187:8767/all.m3u"
LAN_ENABLE = True

OUTPUT_FILENAME = "web.m3u"
LOCAL_RAW_FILENAME = "web_raw.m3u"
LAN_LOCAL_RAW_FILENAME = "lan_raw.m3u"

EXTRACT_GROUPS = [
    # (匹配模式, 目标分组名, 匹配方式)
    # 匹配方式:
    #   "exact"    — 远程 group-title 完全等于匹配模式
    #   "contains" — 远程 group-title 包含匹配模式子串
    #   "fuzzy"    — tv.py 的片段模糊匹配（共享>=3字符片段）
    # ("[三网1]央卫视", "央卫视", "contains"),
    # ⚠️ [翻] 组必须排在前面：它的 group-title 同样包含“港澳台”，
    #    否则会被下一条 contains 规则吞并成同一个组。
    #    这里用最宽松的 "[翻]" 特征词，上游把组名改成“🔮[翻]港澳台直播”之类也能命中。
    ("[翻]", "港澳台[翻]", "contains"),
    ("港澳台", "港澳台", "contains"),
]

# 港澳台两组整体保留，不参与 GROUP_DEFINITIONS 关键词二次分类
PRESERVE_GROUPS = {"港澳台", "港澳台[翻]"}

# 只对这些组做流检测：内网源（央视/卫视）不送检，港澳台直连与[翻]组均送检
CHECK_GROUPS = {"港澳台", "港澳台[翻]"}

# 内网源补充分组关键词：tv.py 的“超高清4k”只认 4k/4K，
# 这里把 8K 频道补进超高清4k（tv.py 本身不改）
LAN_EXTRA_GROUP_KEYWORDS = [("超高清4k", ("8K",))]

# channel_order.json 比对时是否做归一化（去空格 / 去画质后缀）
# 例：内网 "CCTV-2 财经" 与排序表 "CCTV-2财经" 归一化后即可命中
ENABLE_ORDER_KEY_NORMALIZE = True

# 指定分组内，命中这些关键词的频道统一排到组末尾（保留各自原有相对顺序）
# 例：央视特色里 CGTN 系列排在“CCTV 风云/第一/怀旧剧场”之后
GROUP_TAIL_KEYWORDS = {
    "央视特色": ("CGTN",),
}

# 组别名：这些分组不再单开排序表，直接复用指定分组的 channel_order.json 排序表
ORDER_KEY_ALIASES = {
    "港澳台[翻]": "港澳台",
}

# CGTN 独立分组：只在本脚本内部拆分，tv.py 保持原样（那边 CGTN 属“央视特色”）
# 当前关闭：CGTN 系列并入“央视特色”组，与 tv.py 口径完全一致；改回 True 即可单独成组
WEB_SPLIT_CGTN_GROUP = False
WEB_CGTN_KEYWORD = "CGTN"

# 流检测配置
ENABLE_STREAM_CHECK = True
CHECK_TIMEOUT = 5
CHECK_WORKERS = 4
ENABLE_PROBE = True
CHECK_CACHE_EXPIRE = 48
# 测速门限配置（港澳台已走大流量直连通道，剔除限速30K假死源）
# 实测正常可播放源均 >= 250 KB/s（典型 1.2MB/s~6MB/s），限速源锁死在 25~31 KB/s
CHECK_MIN_SPEED_KBS = 80.0
CHECK_SPEED_SAMPLE_KB = 125
CHECK_SPEED_DURATION = 2.0
CHECK_SEGMENT_PROBESIZE = 500000

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
    get_channel_base_name,
    download_with_retry,
    load_json_config_file,
    M3U_EPG_URL,
    initialize_environment,
)

import tv as _tv

# 输出组顺序：以 tv.py 的 GROUP_OUTPUT_ORDER 为准，
# 只把“港澳台[翻]”插到“港澳台”之后，其余完全跟随 tv.py（tv.py 一改这里自动跟随）
OUTPUT_GROUP_ORDER = list(GROUP_OUTPUT_ORDER)
if "港澳台" in OUTPUT_GROUP_ORDER:
    OUTPUT_GROUP_ORDER.insert(OUTPUT_GROUP_ORDER.index("港澳台") + 1, "港澳台[翻]")
else:
    OUTPUT_GROUP_ORDER.append("港澳台[翻]")

# CGTN 独立分组（仅本脚本）：插到“央视特色”之后；tv.py 未改动，其输出仍把 CGTN 归央视特色
if WEB_SPLIT_CGTN_GROUP and "CGTN" not in OUTPUT_GROUP_ORDER:
    if "央视特色" in OUTPUT_GROUP_ORDER:
        OUTPUT_GROUP_ORDER.insert(OUTPUT_GROUP_ORDER.index("央视特色") + 1, "CGTN")
    else:
        OUTPUT_GROUP_ORDER.append("CGTN")

# 归一化比对时剥离的画质后缀（与 tv.py 的 QUALITY_PATTERN 同源，额外补“超高清/8K”）
# (?<!CCTV-) 保护“CCTV-4K / CCTV-8K”这类频道号本身不被剥成 “CCTV-”
ORDER_QUALITY_SUFFIX_PATTERN = re.compile(r'(超高清|高清|超清|(?<!CCTV-)(?:8K|4K|\d+K))', re.IGNORECASE)

# 带频道号的 CCTV（CCTV-1 / CCTV1 / CCTV5+），用于区分“CCTV 第一剧场”这类特色频道
CCTV_NUMBERED_PATTERN = re.compile(r'CCTV-?\d', re.IGNORECASE)


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


def download_m3u(url, cache_filename=LOCAL_RAW_FILENAME, label="远程 M3U", timeout=None, max_retries=None):
    raw_path = os.path.join(BASE_DIR, cache_filename)
    try:
        print(f"正在下载{label}: {url}")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        retry_kwargs = {}
        if timeout is not None:
            retry_kwargs['timeout'] = timeout
        if max_retries is not None:
            retry_kwargs['max_retries'] = max_retries
        resp = download_with_retry(url, headers=headers, **retry_kwargs)
        if resp and resp.text.strip().startswith('#EXTM3U'):
            print(f"成功下载，大小: {len(resp.text)} 字节")
            with open(raw_path, 'w', encoding='utf-8') as f:
                f.write(resp.text)
            print(f"已保存{cache_filename}原始文件到: {raw_path}")
            return resp.text, "network"
    except Exception as e:
        print(f"下载失败: {e}")

    if os.path.exists(raw_path):
        try:
            with open(raw_path, 'r', encoding='utf-8', errors='ignore') as f:
                cached = f.read()
            if cached.strip().startswith('#EXTM3U'):
                print(f"{label} 网络更新失败，已回退到本地缓存: {raw_path}")
                return cached, "cache"
            print(f"本地缓存文件存在，但格式无效: {raw_path}")
        except Exception as e:
            print(f"读取本地缓存失败: {e}")

    return None, "none"


def parse_m3u_channels(m3u_content, extract_rules, source_tag="hgta", apply_blacklist=True, label="远程 M3U"):
    """extract_rules 传 None 表示不按分组提取（内网源全量收入），group-title 原样保留"""
    if not m3u_content:
        return [], [], []

    channels = []
    blacklisted_channels = []
    duplicate_channels = []
    seen_urls = set()
    matched_source_groups = set()

    current_channel = None
    seq = 0
    for line in m3u_content.strip().split('\n'):
        line = line.strip()
        if line.startswith('#EXTINF'):
            # 标题必须以“引号后的最后一个逗号”为界：
            # 属性值本身可能含逗号（如 tvg-logo=".../x.jpg?spformat=png,webp"），
            # 直接用 split(',')[-1] 会把标题截断成 'webp"'
            title_sep = line.rfind('",')
            title_text = line[title_sep + 2:].strip() if title_sep != -1 else line.split(',')[-1].strip()
            current_channel = {
                'extinf_line': line,
                'extra_lines': [],
                'attributes': {},
                'url': None,
                'title': title_text,
                'group_title': '',
                'original_group_title': '',
                '_source': source_tag,
                '_seq': seq
            }
            seq += 1
            for k, v in re.findall(r'(\S+?)="([^"]*)"', line):
                current_channel['attributes'][k] = v
                if k == 'group-title':
                    current_channel['group_title'] = v
                    current_channel['original_group_title'] = v
        elif line.startswith('#') and current_channel:
            current_channel['extra_lines'].append(line)
        elif line and current_channel:
            current_channel['url'] = line
            matched_target = match_extract_group(current_channel['group_title'], extract_rules) if extract_rules else None
            if matched_target or extract_rules is None:
                if matched_target:
                    matched_source_groups.add(current_channel['original_group_title'])
                current_channel['matched_target_group'] = matched_target

                if apply_blacklist and is_blacklisted({'title': current_channel['title'], 'zteurl': current_channel['url']}):
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

    print(f"从{label}提取了 {len(channels)} 个频道")
    if matched_source_groups:
        print(f"匹配到的远程分组: {', '.join(sorted(matched_source_groups))}")
    if blacklisted_channels:
        print(f"已过滤 {len(blacklisted_channels)} 个黑名单频道")
    if duplicate_channels:
        print(f"已按 URL 去重 {len(duplicate_channels)} 个频道")
    return channels, blacklisted_channels, duplicate_channels


def classify_lan_channel(title):
    """内网源的央视/卫视频道：以 tv.py 的 categorize_channel 为准，只补两条内网专属修正：
    1) 8K -> 超高清4k（tv.py 的“超高清4k”关键词只有 4k/4K）
    2) “CCTV 风云剧场 / CCTV 第一剧场 / CCTV 怀旧剧场”这类频道：
       内网标题带 CCTV 前缀，会被 tv.py 的“央视”关键词(CCTV)抢走，
       但它们本质是央视特色（tv.py 自身数据里标题是“第一剧场高清”，不含 CCTV 前缀）
    """
    text = str(title or '')
    upper_title = text.upper()

    # 1) 8K 补充
    for group_name, keywords in LAN_EXTRA_GROUP_KEYWORDS:
        for keyword in keywords:
            if keyword.upper() in upper_title:
                return group_name

    # 2) 无频道号的 CCTV xxx（剧场/风云/怀旧类）按央视特色关键词判定
    if "CCTV" in upper_title and not CCTV_NUMBERED_PATTERN.search(upper_title):
        for keyword in GROUP_DEFINITIONS.get("央视特色", []):
            if keyword != "央视" and keyword in text:
                return "央视特色"

    return categorize_channel(title)


def merge_channels(lan_channels, hgta_channels):
    """合并双源：内网源优先（URL 相同或频道名归一化后相同，保留内网那条）。
    仅在“内网 vs 港澳台”之间做名称判重，港澳台内部（含[翻]组）不互相吞并。"""
    merged = []
    duplicates = []
    seen_urls = set()
    lan_names = set()

    for ch in lan_channels:
        norm_url = normalize_external_channel_url(ch.get('url', ''))
        if norm_url and norm_url in seen_urls:
            duplicates.append({'title': ch.get('title', ''), 'url': ch.get('url', ''),
                               'reason': '内网源 URL 重复，保留首次出现'})
            continue
        seen_urls.add(norm_url)
        lan_names.add(get_channel_base_name(ch.get('title', '')))
        merged.append(ch)

    for ch in hgta_channels:
        norm_url = normalize_external_channel_url(ch.get('url', ''))
        name_key = get_channel_base_name(ch.get('title', ''))
        if norm_url and norm_url in seen_urls:
            duplicates.append({'title': ch.get('title', ''), 'url': ch.get('url', ''),
                               'reason': 'URL 与内网源/港澳台源重复，保留首次出现'})
            continue
        if name_key and name_key in lan_names:
            duplicates.append({'title': ch.get('title', ''), 'url': ch.get('url', ''),
                               'reason': '频道名与内网源重复，保留内网'})
            continue
        seen_urls.add(norm_url)
        merged.append(ch)

    # 统一重编号 _seq：两个源各自的 _seq 都从 0 开始，
    # 一旦某个分组同时含两源频道，直接比较会错乱（重编号保证源顺序稳定）
    for idx, ch in enumerate(merged):
        ch['_seq'] = idx

    print(f"双源合并完成：内网 {len(lan_channels)} + 港澳台 {len(hgta_channels)} → {len(merged)}（去重 {len(duplicates)}）")
    return merged, duplicates


def classify_channels(channels, preserve_groups):
    grouped = {g: [] for g in GROUP_DEFINITIONS.keys()}
    for g in OUTPUT_GROUP_ORDER:
        if g not in grouped:
            grouped[g] = []

    for ch in channels:
        target_group = ch.get('matched_target_group', '')

        if target_group in preserve_groups:
            if target_group not in grouped:
                grouped[target_group] = []
            grouped[target_group].append(ch)
        elif WEB_SPLIT_CGTN_GROUP and WEB_CGTN_KEYWORD in str(ch.get('title', '')).upper():
            # CGTN 独立分组只在本脚本生效，tv.py 那边 CGTN 仍归“央视特色”
            if "CGTN" not in grouped:
                grouped["CGTN"] = []
            grouped["CGTN"].append(ch)
        else:
            # 内网源（央视/卫视）按 tv.py 的 GROUP_DEFINITIONS 关键词归类
            category = classify_lan_channel(ch['title']) if ch.get('_source') == 'lan' else categorize_channel(ch['title'])
            if category not in grouped:
                grouped[category] = []
            grouped[category].append(ch)

    return grouped


def normalize_order_key(title):
    """排序表比对用的归一化键：去画质后缀 + 去所有空白 + 统一大小写。
    CCTV-2 财经 / CCTV-2财经高清 → cctv-2财经
    ViuTV / VIUTV → viutv"""
    cleaned = ORDER_QUALITY_SUFFIX_PATTERN.sub('', str(title or ''))
    return re.sub(r'\s+', '', cleaned).casefold()


def sort_channels_by_order_normalized(channel_list, order_list):
    """与 tv.py 的 sort_channels_by_order 语义一致（同名备用源整组保留、未命中的保持源顺序），
    区别在于比对时使用归一化键，并分两轮：
      第一轮 精确匹配（去画质后缀 / 去空白 / 统一大小写）
      第二轮 前缀匹配（如排序表 "无线新闻" 命中 "无线新闻台"、"明珠台" 命中 "明珠台(字幕)"）"""
    if not order_list:
        return channel_list
    if not ENABLE_ORDER_KEY_NORMALIZE:
        return sort_channels_by_order(channel_list, order_list)

    entries = [(ch, normalize_order_key(ch.get('title', ''))) for ch in channel_list]
    taken = [False] * len(entries)
    sorted_list = []

    # 按排序表逐项处理：每一项先收“精确命中”，再收“前缀命中”，
    # 这样排序表的位置优先级才不会被整表两轮扫打乱
    for name in order_list:
        key = normalize_order_key(name)
        if not key:
            continue

        for idx, (ch, ch_key) in enumerate(entries):
            if not taken[idx] and ch_key == key:
                taken[idx] = True
                sorted_list.append(ch)

        if len(key) >= 2:
            for idx, (ch, ch_key) in enumerate(entries):
                if not taken[idx] and ch_key.startswith(key):
                    taken[idx] = True
                    sorted_list.append(ch)

    # 追加不在排序列表中的剩余频道（保持源文件原始顺序）
    for idx, (ch, _) in enumerate(entries):
        if not taken[idx]:
            sorted_list.append(ch)

    return sorted_list


def move_tail_channels(channels, keywords):
    """把命中关键词的频道挪到组末尾，前后两段各自保持原有相对顺序（稳定分区）"""
    if not keywords:
        return channels
    head, tail = [], []
    for ch in channels:
        title = str(ch.get('title', '')).upper()
        if any(keyword.upper() in title for keyword in keywords):
            tail.append(ch)
        else:
            head.append(ch)
    return head + tail


def apply_sorting(grouped_channels, channel_order):
    for group_name, channels in grouped_channels.items():
        order_list = channel_order.get(group_name, [])
        if not order_list and group_name in ORDER_KEY_ALIASES:
            # 复用其它分组的排序表（如 港澳台[翻] 复用 港澳台）
            order_list = channel_order.get(ORDER_KEY_ALIASES[group_name], [])
        if order_list:
            grouped_channels[group_name] = sort_channels_by_order_normalized(channels, order_list)
        else:
            # 无排序表时按源文件原始顺序（对齐 tv.py apply_custom_sorting 的行为）
            grouped_channels[group_name].sort(key=lambda x: x.get('_seq', 0))

        # 组内尾部关键词（如央视特色里的 CGTN 系列）
        grouped_channels[group_name] = move_tail_channels(
            grouped_channels[group_name], GROUP_TAIL_KEYWORDS.get(group_name, ()))
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
        cache_expire_hours=CHECK_CACHE_EXPIRE,
        min_speed_kbs=CHECK_MIN_SPEED_KBS,
        speed_sample_kb=CHECK_SPEED_SAMPLE_KB,
        speed_sample_duration=CHECK_SPEED_DURATION,
        segment_probesize=CHECK_SEGMENT_PROBESIZE
    )

    channels_to_check = []
    skipped_groups = []
    for group, ch_list in grouped_channels.items():
        # 内网源（央视/卫视）不送检，原样保留
        if CHECK_GROUPS and group not in CHECK_GROUPS:
            if ch_list:
                skipped_groups.append(f"{group}({len(ch_list)})")
            continue
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

    if skipped_groups:
        print(f"以下分组跳过检测（原样输出）: {', '.join(skipped_groups)}")

    if not channels_to_check:
        print("没有需要检测的频道。")
        return grouped_channels

    if sys.platform == 'win32':
        event_loop_policy = (
            asyncio.WindowsProactorEventLoopPolicy()
            if ENABLE_PROBE else asyncio.WindowsSelectorEventLoopPolicy()
        )
        asyncio.set_event_loop_policy(event_loop_policy)
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
    # M3U_EPG_URL 是导入时的快照，配置覆盖只写回 tv 模块，
    # 所以这里优先读 tv 模块的当前值，避免 web.m3u 头部写死旧地址
    epg_url = getattr(_tv, 'M3U_EPG_URL', '') or M3U_EPG_URL
    header = f'#EXTM3U x-tvg-url="{epg_url}"' if epg_url else "#EXTM3U"
    content.append(header)

    for group in OUTPUT_GROUP_ORDER:
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
    parser = argparse.ArgumentParser(description="从远程 M3U 提取港澳台 + 内网 M3U 合并生成 M3U 文件")
    parser.add_argument("-sc", "--skip-check", action="store_true", help="禁用流检测")
    parser.add_argument("-sp", "--skip-probe", action="store_true", help="禁用 ffprobe 画质探测")
    parser.add_argument("--skip-lan", action="store_true", help="本次不合并内网 M3U 源")
    args = parser.parse_args()

    if args.skip_check:
        global ENABLE_STREAM_CHECK
        ENABLE_STREAM_CHECK = False
        print("[CLI] 已禁用流检测")
    if args.skip_probe:
        global ENABLE_PROBE
        ENABLE_PROBE = False
        print("[CLI] 已禁用 ffprobe 画质探测")

    lan_enabled = LAN_ENABLE and not args.skip_lan
    if args.skip_lan:
        print("[CLI] 已跳过内网 M3U 源")

    initialize_environment()

    # 检测性能参数跟随配置（config/config.json + myconfig.json），避免 web.py 硬编码覆盖用户设置
    global CHECK_WORKERS, CHECK_TIMEOUT, CHECK_CACHE_EXPIRE
    CHECK_WORKERS = int(getattr(_tv, 'CHECK_WORKERS', CHECK_WORKERS) or CHECK_WORKERS)
    CHECK_TIMEOUT = int(getattr(_tv, 'CHECK_TIMEOUT', CHECK_TIMEOUT) or CHECK_TIMEOUT)
    CHECK_CACHE_EXPIRE = int(getattr(_tv, 'CHECK_CACHE_EXPIRE', CHECK_CACHE_EXPIRE) or CHECK_CACHE_EXPIRE)

    print("=" * 50)
    print("web.py — 双源 M3U 提取/合并工具")
    print("=" * 50)

    print(f"\n提取规则:")
    for pattern, target, mode in EXTRACT_GROUPS:
        print(f"  [{mode}] '{pattern}' → '{target}'")
    print(f"保留原组（不二次分类）: {', '.join(PRESERVE_GROUPS)}")
    print(f"只检测这些分组: {', '.join(sorted(CHECK_GROUPS)) if CHECK_GROUPS else '全部'}")
    print(f"输出组顺序（跟随 tv.py）: {' → '.join(OUTPUT_GROUP_ORDER)}")
    print(f"内网源: {'启用' if lan_enabled else '禁用'} {LAN_M3U_URL}（不检测、不加画质标签）")
    print(f"CGTN 独立分组（仅本脚本，tv.py 未改）: {'是' if WEB_SPLIT_CGTN_GROUP else '否（按 tv.py 归央视特色）'}")

    # ---------- 源1：内网（央视 / 卫视） ----------
    lan_channels = []
    if lan_enabled:
        # 内网地址一旦不可达要快速失败（15s*3 次太慢），尽早回退到 lan_raw.m3u 缓存
        lan_content, lan_source = download_m3u(LAN_M3U_URL, LAN_LOCAL_RAW_FILENAME, "内网 M3U",
                                              timeout=8, max_retries=2)
        if lan_content:
            print(f"内网数据来源: {'网络更新' if lan_source == 'network' else '本地缓存'}")
            # 内网源：全量收入、不走黑名单（原样输出）
            lan_channels, lan_blacklisted, lan_dup = parse_m3u_channels(
                lan_content, None, source_tag="lan", apply_blacklist=False, label="内网 M3U")
        else:
            print("警告: 内网 M3U 不可用且无本地缓存，本次只输出港澳台")

    # ---------- 源2：港澳台 ----------
    m3u_content, m3u_source = download_m3u(M3U_SOURCE_URL, LOCAL_RAW_FILENAME, "港澳台 M3U")
    if not m3u_content:
        print("错误: 无法下载远程 M3U，程序退出")
        sys.exit(1)
    print(f"港澳台数据来源: {'网络更新' if m3u_source == 'network' else '本地缓存'}")

    channels, blacklisted, duplicates = parse_m3u_channels(
        m3u_content, EXTRACT_GROUPS, source_tag="hgta", label="港澳台 M3U")
    if not channels and not lan_channels:
        print("未提取到任何频道，程序退出")
        sys.exit(0)

    channel_order = load_json_config_file(CHANNEL_ORDER_FILE)
    if channel_order:
        print(f"已加载频道排序配置: {CHANNEL_ORDER_FILE}")

    merged, merge_duplicates = merge_channels(lan_channels, channels)

    grouped_channels = classify_channels(merged, PRESERVE_GROUPS)
    grouped_channels = apply_sorting(grouped_channels, channel_order)

    grouped_channels = run_stream_check(grouped_channels)

    m3u_output = generate_web_m3u(grouped_channels)
    output_path = os.path.join(BASE_DIR, OUTPUT_FILENAME)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(m3u_output)

    total = sum(len(v) for v in grouped_channels.values())
    print(f"\n已生成: {output_path}")
    print(f"总频道数: {total}")
    for group in OUTPUT_GROUP_ORDER:
        ch_list = grouped_channels.get(group, [])
        if ch_list:
            print(f"  {group}: {len(ch_list)} 个频道")


if __name__ == "__main__":
    main()
