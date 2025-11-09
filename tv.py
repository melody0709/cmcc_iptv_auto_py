# -*- coding: utf-8 -*-
import json
import re
import os
import io
import requests
import sys
import gzip
import time  #  添加时间模块用于重试等待
import threading  # <--- 在这里添加
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from xml.dom import minidom
from concurrent.futures import ThreadPoolExecutor, as_completed

# 设置标准输出编码
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ===================== 自定义配置区域 =====================
#  EPG 下载重试配置
EPG_DOWNLOAD_RETRY_COUNT = 3  # 重试次数
EPG_DOWNLOAD_RETRY_DELAY = 2  # 重试间隔（秒）
EPG_DOWNLOAD_TIMEOUT = 15     # 单个请求超时时间（秒）

# 在这里修改输出文件名（保持默认即可使用原始文件名）
TV_M3U_FILENAME = "tv.m3u"        # 组播地址列表文件
TV2_M3U_FILENAME = "tv2.m3u"      # 转单播地址列表文件
KU9_M3U_FILENAME = "ku9.m3u"      #  KU9回看参数格式文件
XML_FILENAME = "t.xml"            # XML节目单文件
REPLACEMENT_IP = "http://c.cc.top:7088/udp"  # UDPXY地址，
CATCHUP_SOURCE_PREFIX = "http://183.235.162.80:6610/190000002005"  # 回看源前缀，
NGINX_PROXY_PREFIX = "http://c.cc.top:7077"  # 针对外网播放的nginx代理
JSON_URL = "http://183.235.16.92:8082/epg/api/custom/getAllChannel.json" # JSON 文件下载 URL  这个地址有晴彩

#  EPG 地址配置 - 可自定义修改
M3U_EPG_URL = "https://epg.112114.xyz/pp.xml"  # 请修改为你的实际 EPG 地址
# (新增) EPG 下载源地址 (可以配置多个, 任务会自动分配)
EPG_BASE_URLS = [
    "http://183.235.16.92:8082/epg/api/channel/",
    "http://183.235.11.39:8082/epg/api/channel/"
]
#  回看参数配置 - 可自定义修改
CATCHUP_URL_TEMPLATE = "{prefix}/{ztecode}/index.m3u8?starttime=${{utc:yyyyMMddHHmmss}}&endtime=${{utcend:yyyyMMddHHmmss}}"
#  添加KU9回看模板
CATCHUP_URL_KU9 = "{prefix}/{ztecode}/index.m3u8?starttime=${{(b)yyyyMMddHHmmss|UTC}}&endtime=${{(e)yyyyMMddHHmmss|UTC}}"

# 自定义配置文件
CHANNEL_ORDER_FILE = "channel_order.json"        # 频道排序文件
CUSTOM_CHANNELS_FILE = "custom_channels.json"    # 自定义频道文件

#  扩展黑名单配置 - 支持按 title、code 或 zteurl 过滤
BLACKLIST_RULES = {
    "title": ["测试频道", "购物", "导视", "百视通", "指南", "精选频道"],
    "code": [
        # 在这里添加要过滤的频道代码
        # "02000006000000052022060699000003",
    ],
    "zteurl": [
        # 在这里添加要过滤的播放链接
        # "rtp://239.21.0.137:3892",
    ]
}

# 🚀 性能优化：转换为集合
BLACKLIST_TITLE_SET = set(BLACKLIST_RULES["title"])
BLACKLIST_CODE_SET = set(BLACKLIST_RULES["code"])
BLACKLIST_ZTEURL_SET = set(BLACKLIST_RULES["zteurl"])

# 频道名称映射（将高清频道映射到标准名称）
CHANNEL_NAME_MAP = {
    "CCTV-1高清": "CCTV-1综合",
    "CCTV-2高清": "CCTV-2财经",
    "CCTV-3高清": "CCTV-3综艺",
    "CCTV-4高清": "CCTV-4中文国际",
    "CCTV-5高清": "CCTV-5体育",
    "CCTV-6高清": "CCTV-6电影",
    "CCTV-7高清": "CCTV-7国防军事",
    "CCTV-8高清": "CCTV-8电视剧",
    "CCTV-9高清": "CCTV-9纪录",
    "CCTV-10高清": "CCTV-10科教",
    "CCTV-11高清": "CCTV-11戏曲",
    "CCTV-12高清": "CCTV-12社会与法",
    "CCTV-13高清": "CCTV-13新闻",
    "CCTV-14高清": "CCTV-14少儿高清",
    "CCTV-15高清": "CCTV-15音乐",
    "CCTV-16高清": "CCTV-16奥林匹克",
    "CCTV-17高清": "CCTV-17农业高清",
    "广州新闻-测试": "广州新闻高清",
    "广州综合-测试": "广州综合高清"
}

# EPG 下载开关:
ENABLE_EPG_DOWNLOAD = True  # True - 启用EPG下载和生成  False - 跳过EPG下载（方便调试）

# EPG 下载模式:
EPG_DOWNLOAD_MODE = "M3U_ONLY"  # 默认修改为 "M3U_ONLY"  "M3U_ONLY"  - 仅下载和合成 M3U 文件中实际包含的频道 )  "ALL" - 下载和合成所有可用频道（包括被 M3U 过滤掉的)

# EPG 合成模式: (针对 "如果有些频道epg为空..." 的需求)
# True  - (推荐) 仅当频道有节目数据时才将其写入 XML。这可能导致播放器无法映射频道名称。方便iptool整合  False - (不推荐) 即使频道没有节目数据也写入 <channel> 标签 (用于频道名称/图标映射)，只是不包含 <programme> 标签。
XML_SKIP_CHANNELS_WITHOUT_EPG = True # 默认为 True

# 1. 定义所有分组和它们的关键字 (这里的顺序不重要)
GROUP_DEFINITIONS = {
    "央视": ["CCTV"],
    "央视特色": ["兵器科技", "风云", "第一剧场", "世界地理", "央视", "卫生健康", "怀旧", "女性", "高尔夫", "金鹰纪实"],
    "广东": ["广东", "大湾区", "经济科教", "南方", "岭南", "现代教育", "移动频道"],
    "卫视": ["卫视"],
    "少儿": ["少儿", "卡通", "动画", "教育"],
    "CGTN": ["CGTN"],
    "华数咪咕": ["爱", "睛彩", "IPTV", "咪咕", "热播", "经典", "魅力"],
    "超清4k": ["超清", "4k", "4K"],
    "广东地方台": [],  # 自定义频道分组，没有关键字
    "其他": []          # 保底分组，没有关键字
}

# 2. 定义分类逻辑的 *优先级* (e.g., "少儿" 必须在 "央视" 之前) 这里的顺序决定一个频道被分到哪个组
GROUP_CLASSIFICATION_PRIORITY = [
    "少儿",       # 必须在 "央视" 和 "广东" 之前,不然cctv14少儿 会分到央视,确保每个分组只有一个频道,不会有重复频道
    "超清4k",   # 必须在 "央视" 之前，否则CCTV-4K会被分到央视
    "央视",
    "央视特色",
    "广东",
    "CGTN",
    "卫视",
    "华数咪咕",
    # "广东地方台" 和 "其他" 没有关键字，不需要在这里
]

# 3. 定义 M3U 和 XML 文件中的 *输出顺序*  (你可以随意排列这里的顺序，"少儿" 重排序)
GROUP_OUTPUT_ORDER = [
    "央视",
    "央视特色",
    "少儿",  # <--- "少儿" 重排序
    "广东",
    "卫视",
    "CGTN",
    "华数咪咕",
    "超清4k",
    "其他",
    "广东地方台"
]

# 自动生成的压缩文件名（基于XML文件名）
XML_GZ_FILENAME = XML_FILENAME + ".gz"

# 🚀 性能优化：预编译正则表达式
CCTV_PATTERN = re.compile(r'CCTV-(\d+)')  # 匹配CCTV-数字模式
NUMBER_PATTERN = re.compile(r'\d+')  # 匹配数字
QUALITY_PATTERN = re.compile(r'(?:高清|超清|4K|\d+K)')  # 匹配清晰度标识
TVG_ID_CLEAN_PATTERN = re.compile(r'[_\s]*(高清|超清|4K)[_\s]*')  # 清理tvg-id中的清晰度标识
SPACE_DASH_PATTERN = re.compile(r'\s+-\s+')  # 匹配空格-空格模式
MULTI_SPACE_PATTERN = re.compile(r'\s+')  # 匹配多个空格

# 魔法字符串和数字提取为常量
TIMEZONE_OFFSET = "+0800"  # 时区偏移
DATE_FORMAT = "%Y%m%d"  # 日期格式
XML_GENERATOR_NAME = "Custom EPG Generator"  # XML生成器名称
LOG_SEPARATOR = "=" * 50  # 日志分隔符
UNKNOWN_CHANNEL = "Unknown"  # 未知频道名称
UNKNOWN_PROGRAMME = "Unknown Programme"  # 未知节目名称
CHANNEL_PROCESSING_LOG = "channel_processing.log"  # 频道处理日志文件名
EPG_STATISTICS_LOG = "epg_statistics.log"  # EPG统计日志文件名

# 规范化配置（程序内部使用）
def normalize_url(url, trailing_slash='keep'):
    """
    规范化URL，确保斜杠处理正确
    :param url: 要规范化的URL
    :param trailing_slash: 'keep' (默认), 'add' (添加斜杠), or 'remove' (移除斜杠).
    """
    if not url:
        return url
    
    if trailing_slash == 'add':
        if not url.endswith('/'):
            url += '/'
    elif trailing_slash == 'remove':
        if url.endswith('/'):
            url = url.rstrip('/')
            
    return url
# 应用规范化
REPLACEMENT_IP_NORM = normalize_url(REPLACEMENT_IP, trailing_slash='add')
CATCHUP_SOURCE_PREFIX_NORM = normalize_url(CATCHUP_SOURCE_PREFIX, trailing_slash='remove')
NGINX_PROXY_PREFIX_NORM = normalize_url(NGINX_PROXY_PREFIX, trailing_slash='add')

def clean_tvg_id(title):
    """清理频道标题，生成标准的 tvg-id"""
    cleaned = TVG_ID_CLEAN_PATTERN.sub('', title)
    if 'CCTV' in cleaned:
        cleaned = cleaned.replace('-', '')
    return cleaned.strip()

def apply_channel_name_mapping(channel, base_name):
    """应用频道名称映射，返回最终名称"""
    # 如果标题在映射表中，直接返回映射后的名称
    if channel["title"] in CHANNEL_NAME_MAP:
        return CHANNEL_NAME_MAP[channel["title"]]
    
    # 对于CCTV频道，使用标准名称
    cctv_match = CCTV_PATTERN.search(base_name)
    if cctv_match:
        cctv_num = cctv_match.group(1)
        # 从名称映射中查找对应的标准名称
        for key, value in CHANNEL_NAME_MAP.items():
            if f"CCTV-{cctv_num}" in key:
                return value
        return f"CCTV-{cctv_num}"
    
    return channel["title"]

def print_configuration():
    """打印当前使用的配置"""
    print(f"你的组播转单播UDPXY地址是 {REPLACEMENT_IP_NORM}")
    print(f"你的回看源前缀是 {CATCHUP_SOURCE_PREFIX_NORM}")
    print(f"你的nginx代理前缀是 {NGINX_PROXY_PREFIX_NORM}")
    print(f"你的回看URL模板是 {CATCHUP_URL_TEMPLATE}")
    print(f"你的KU9回看URL模板是 {CATCHUP_URL_KU9}")
    print(f"EPG下载开关: {'启用' if ENABLE_EPG_DOWNLOAD else '禁用'}")
    if ENABLE_EPG_DOWNLOAD:
        print(f"EPG下载配置: 重试{EPG_DOWNLOAD_RETRY_COUNT}次, 超时{EPG_DOWNLOAD_TIMEOUT}秒, 间隔{EPG_DOWNLOAD_RETRY_DELAY}秒")

def download_with_retry(url, max_retries=EPG_DOWNLOAD_RETRY_COUNT, timeout=EPG_DOWNLOAD_TIMEOUT):
    """ 带重试机制的下载函数"""
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            error_type = type(e).__name__
            if attempt < max_retries - 1:
                print(f"  下载时发生 '{error_type}' 错误，{EPG_DOWNLOAD_RETRY_DELAY}秒后重试 ({attempt + 1}/{max_retries})...")
                time.sleep(EPG_DOWNLOAD_RETRY_DELAY)
            else:
                print(f"  下载时发生 '{error_type}' 错误，已达最大重试次数 ({max_retries})")
                raise
    return None

def download_json_data(url):
    try:
        response = download_with_retry(url)
        data = response.json()
        print(f"成功获取 JSON 数据从 {url}")
        return data
    except requests.RequestException as e:
        print(f"下载 JSON 数据失败: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"解析 JSON 数据失败: {e}")
        return None

def categorize_channel(title):
    """(重构) 根据 GROUP_CLASSIFICATION_PRIORITY 列表的顺序为频道分类"""
    
    # 按照 "分类优先级" 列表的顺序
    for group_name in GROUP_CLASSIFICATION_PRIORITY:
        # 从 GROUP_DEFINITIONS 获取该组的关键字
        for keyword in GROUP_DEFINITIONS.get(group_name, []):
            if keyword in title:
                # 找到第一个匹配的就返回 这保证了一个频道只在一个分组
                return group_name 
    
    # 如果所有关键字都未匹配，则归类为"其他"
    return "其他"

def extract_number(title):
    match = NUMBER_PATTERN.search(title)
    return int(match.group()) if match else 0

def is_blacklisted(channel):
    """检查频道是否在黑名单中（支持 title、code、zteurl）"""
    # 检查标题黑名单
    title = channel.get("title", "")
    if any(black_word in title for black_word in BLACKLIST_TITLE_SET):
        return True
    
    # 检查代码黑名单
    code = channel.get("code", "")
    if code in BLACKLIST_CODE_SET:
        return True
    
    # 检查播放链接黑名单
    zteurl = channel.get("zteurl", "")
    if not zteurl:
        # 如果没有直接的 zteurl，尝试从 params 中获取
        params = channel.get("params", {})
        zteurl = params.get("zteurl", "") or params.get("hwurl", "")
    
    if zteurl in BLACKLIST_ZTEURL_SET:
        return True
    
    return False

def get_channel_base_name(title):
    """获取频道的基础名称（改进的CCTV频道处理）"""
    # 首先处理CCTV频道的特殊情况
    if "CCTV" in title:
        # 匹配CCTV-数字的模式
        cctv_match = CCTV_PATTERN.search(title)
        if cctv_match:
            cctv_num = cctv_match.group(1)
            # 返回标准化的CCTV基础名称
            return f"CCTV-{cctv_num}"
    
    # 对于非CCTV频道，去除常见的高清标识
    base_name = QUALITY_PATTERN.sub('', title)
    # 去除可能多余的空格和横杠
    base_name = SPACE_DASH_PATTERN.sub('', base_name)
    base_name = MULTI_SPACE_PATTERN.sub(' ', base_name)
    base_name = base_name.strip().strip('-').strip()
    return base_name

def get_channel_quality(title):
    """获取频道的清晰度"""
    if "超清" in title or "4K" in title or "4k" in title:
        return "超清"
    elif "高清" in title:
        return "高清"
    else:
        return "标清"

def is_cctv_channel(title):
    """检查是否是CCTV频道"""
    return "CCTV" in title

def process_channels(channels):
    """处理频道列表，进行去重和名称映射"""
    # 过滤黑名单频道
    filtered_channels = []
    blacklisted_channels = []
    for channel in channels:
        if is_blacklisted(channel):
            blacklisted_channels.append({
                "title": channel["title"],
                "code": channel.get("code", ""),
                "reason": "黑名单规则匹配",
                "source": "主JSON"  #  添加来源标识
            })
            continue
        filtered_channels.append(channel)
    
    print(f"已过滤 {len(blacklisted_channels)} 个黑名单频道（主JSON）")
    
    # 按基础名称分组
    channel_groups = {}
    for channel in filtered_channels:
        base_name = get_channel_base_name(channel["title"])
        if base_name not in channel_groups:
            channel_groups[base_name] = []
        channel_groups[base_name].append(channel)
    
    # 处理每个频道组
    kept_channels = []
    removed_channels = []
    
    for base_name, group in channel_groups.items():
        # 如果只有一个频道，保留它
        if len(group) == 1:
            channel = group[0]
            # 检查是否需要应用名称映射
            if channel["title"] in CHANNEL_NAME_MAP:
                channel["final_name"] = CHANNEL_NAME_MAP[channel["title"]]
            else:
                channel["final_name"] = channel["title"]
            kept_channels.append(channel)
            continue
        
        # 检查是否是CCTV频道组
        is_cctv_group = any(is_cctv_channel(ch["title"]) for ch in group)
        
        if is_cctv_group:
            # 对于CCTV频道，优先保留高清版本
            hd_channels = [ch for ch in group if get_channel_quality(ch["title"]) == "高清"]
            ultra_hd_channels = [ch for ch in group if get_channel_quality(ch["title"]) == "超清"]
            
            # 如果有超清版本，优先保留超清
            if ultra_hd_channels:
                for channel in ultra_hd_channels:
                    # 应用名称映射
                    channel["final_name"] = apply_channel_name_mapping(channel, base_name)
                    kept_channels.append(channel)
                
                # 记录被剔除的其他版本
                for channel in group:
                    if channel not in ultra_hd_channels:
                        removed_channels.append({
                            "name": channel["title"],
                            "reason": f"CCTV频道有超清版本: {[ch['title'] for ch in ultra_hd_channels]}"
                        })
            
            # 如果没有超清但有高清版本，保留高清版本
            elif hd_channels:
                for channel in hd_channels:
                    # 应用名称映射
                    channel["final_name"] = apply_channel_name_mapping(channel, base_name)
                    kept_channels.append(channel)
                
                # 记录被剔除的标清CCTV频道
                for channel in group:
                    if get_channel_quality(channel["title"]) == "标清":
                        removed_channels.append({
                            "name": channel["title"],
                            "reason": f"CCTV频道有高清版本: {[ch['title'] for ch in hd_channels]}"
                        })
            else:
                # 没有高清/超清版本，保留所有标清版本
                for channel in group:
                    channel["final_name"] = channel["title"]
                    kept_channels.append(channel)
        else:
            # 非CCTV频道组，按原来的逻辑处理
            # 找出所有高清/超清版本
            hd_channels = [ch for ch in group if get_channel_quality(ch["title"]) in ["高清", "超清"]]
            
            # 如果没有高清/超清版本，保留所有标清版本
            if not hd_channels:
                for channel in group:
                    channel["final_name"] = channel["title"]
                    kept_channels.append(channel)
                continue
            
            # 有高清/超清版本，只保留这些版本
            for channel in hd_channels:
                channel["final_name"] = channel["title"]
                kept_channels.append(channel)
            
            # 记录被剔除的标清频道
            for channel in group:
                if get_channel_quality(channel["title"]) == "标清":
                    removed_channels.append({
                        "name": channel["title"],
                        "reason": f"有高清/超清版本: {[ch['title'] for ch in hd_channels]}"
                    })
    
    # 不再在这里生成日志文件，改为在 main 函数中统一生成
    return kept_channels, blacklisted_channels, removed_channels

def convert_time_to_xmltv_format(time_str):
    try:
        return f"{time_str} {TIMEZONE_OFFSET}"
    except ValueError as e:
        print(f"时间格式转换失败: {time_str}, 错误: {e}")
        return None

def load_custom_channels(file_path):
    """加载自定义频道"""
    if not os.path.exists(file_path):
        print(f"自定义频道文件不存在: {file_path}")
        return {}
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            custom_channels = json.load(f)
        print(f"成功加载自定义频道文件: {file_path}")
        return custom_channels
    except Exception as e:
        print(f"加载自定义频道文件失败: {e}")
        return {}

def load_channel_order(file_path):
    """加载频道排序配置"""
    if not os.path.exists(file_path):
        print(f"频道排序文件不存在: {file_path}")
        return {}
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            channel_order = json.load(f)
        print(f"成功加载频道排序文件: {file_path}")
        return channel_order
    except Exception as e:
        print(f"加载频道排序文件失败: {e}")
        return {}

def apply_custom_sorting(grouped_channels, channel_order):
    """应用自定义排序"""
    for group_name, channels in grouped_channels.items():
        if group_name in channel_order:
            # 获取该组的排序配置
            order_list = channel_order[group_name]
            
            # 创建频道名称到频道对象的映射
            channel_map = {ch["title"]: ch for ch in channels}
            processed = set()  # 使用集合跟踪已处理的频道
            
            # 按照配置的顺序重新排列
            sorted_channels = []
            for channel_name in order_list:
                if channel_name in channel_map:
                    sorted_channels.append(channel_map[channel_name])
                    processed.add(channel_name)
            
            # 添加未在排序配置中指定的频道（按原顺序）
            for remaining_channel in channels:
                if remaining_channel["title"] not in processed:
                    sorted_channels.append(remaining_channel)
            
            grouped_channels[group_name] = sorted_channels
    
    return grouped_channels

def add_custom_channels(grouped_channels, custom_channels):
    """添加自定义频道到分组，返回（更新后的分组，黑名单频道列表，已添加的自定义频道列表）"""
    blacklisted_custom_channels = []  #  记录被过滤的自定义频道
    added_custom_channels = []  # 记录成功添加的自定义频道
    
    for group_name, channels in custom_channels.items():
        if group_name not in grouped_channels:
            print(f"警告: 自定义分组 '{group_name}' 未在 GROUP_DEFINITIONS 中定义，将自动创建。")
            grouped_channels[group_name] = []
        
        for custom_channel in channels:
            # 检查自定义频道是否在黑名单中
            if is_blacklisted(custom_channel):
                blacklisted_info = {
                    "title": custom_channel.get('title', '未知'),
                    "code": custom_channel.get('code', ''),
                    "reason": "黑名单规则匹配", 
                    "source": "自定义频道"  #  添加来源标识
                }
                blacklisted_custom_channels.append(blacklisted_info)
                print(f"跳过黑名单中的自定义频道: {custom_channel.get('title', '未知')}")
                continue
            
            # --- 修改开始 ---
            # 检查是否需要应用名称映射
            original_title = custom_channel["title"]
            if original_title in CHANNEL_NAME_MAP:
                final_name = CHANNEL_NAME_MAP[original_title]
                print(f"自定义频道名称映射: '{original_title}' -> '{final_name}'")
            else:
                final_name = original_title

            # 为自定义频道添加必要的字段
            custom_channel["title"] = final_name           # 使用最终名称
            custom_channel["original_title"] = original_title  # 保留原始名称
            custom_channel["number"] = extract_number(final_name) # 使用最终名称提取编号
            # --- 修改结束 ---
            
            custom_channel["is_custom"] = True  # 标记为自定义频道
            
            # 添加到分组
            grouped_channels[group_name].append(custom_channel)
            # 记录成功添加的自定义频道
            added_custom_channels.append({
                "title": final_name,
                "original_title": original_title,
                "group": group_name
            })
    
    #  返回黑名单信息和已添加的频道列表
    return grouped_channels, blacklisted_custom_channels, added_custom_channels



def download_epg_for_source(channels, base_url, total_channels, progress_counter, progress_lock):
    """
    (新增) 下载工作函数：从指定的 base_url 下载一组频道的 EPG 数据。
    在线程池中执行。
    """
    schedules_for_source = {}
    # 优化：在函数开始时计算一次日期，避免重复计算
    now = datetime.now()
    current_date = now.strftime(DATE_FORMAT)
    next_date = (now + timedelta(days=1)).strftime(DATE_FORMAT)

    for channel in channels:
        code = channel["code"]
        
        # 为当天和第二天生成下载URL
        urls_for_channel = [
            f"{base_url}{code}.json?begintime={current_date}",
            f"{base_url}{code}.json?begintime={next_date}"
        ]
        
        for url in urls_for_channel:
            try:
                response = download_with_retry(url)
                data = response.json()
                
                if code not in schedules_for_source:
                    schedules_for_source[code] = {
                        "channel": data.get("channel", {}),
                        "schedules": []
                    }
                schedules_for_source[code]["schedules"].extend(data.get("schedules", []))
            except Exception as e:
                # 在线程中打印错误，避免中断其他线程
                # (这个 \n 会另起一行，保留错误，进度条会在下一行继续)
                print(f"\n处理 {url} 失败 (线程内): {e}")
        
        # --- 关键修改：处理完一个频道后，更新进度条 ---
        with progress_lock:
            progress_counter[0] += 1  # 增加共享计数器
            count = progress_counter[0]
            percent = (count / total_channels) * 100
            
            # 打印单行刷新的进度
            print(f"  下载进度: {count}/{total_channels} 个频道 ({percent:.1f}%)", end="\r", flush=True)
            
    return schedules_for_source

def _download_epg_data_parallel(channels_for_xml):
    """(Helper) 并行下载所有频道的EPG数据。"""
    all_channels_to_download = [channel for group in channels_for_xml.values() for channel in group]
    num_channels = len(all_channels_to_download)
    num_sources = len(EPG_BASE_URLS)

    if num_sources == 0:
        print("错误: EPG_BASE_URLS 配置为空，无法下载节目单。")
        return {}

    chunk_size = (num_channels + num_sources - 1) // num_sources
    tasks = []
    for i in range(num_sources):
        start_index = i * chunk_size
        end_index = start_index + chunk_size
        channel_chunk = all_channels_to_download[start_index:end_index]
        if channel_chunk:
            tasks.append({"channels": channel_chunk, "base_url": EPG_BASE_URLS[i]})

    print(f"准备并行下载 {num_channels} 个频道的EPG，使用 {len(tasks)} 个epg地址下载...")

    progress_lock = threading.Lock()
    progress_counter = [0]
    all_schedules = {}

    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_to_task = {
            executor.submit(download_epg_for_source, task["channels"], task["base_url"], num_channels, progress_counter, progress_lock): task
            for task in tasks
        }
        for future in as_completed(future_to_task):
            try:
                result = future.result()
                all_schedules.update(result)
            except Exception as exc:
                print(f'\n一个下载任务生成了异常: {exc}')
    
    print("\n所有下载任务已完成。")
    return all_schedules

def _build_xmltv_tree(channels_for_xml, all_schedules):
    """(Helper) 根据EPG数据构建XMLTV ElementTree。"""
    root = ET.Element("tv")
    root.set("generator-info-name", XML_GENERATOR_NAME)
    
    stats = {
        "channels_in_xml": 0, "channels_with_epg": 0, "total_programmes": 0,
        "skipped_no_epg": 0, "with_epg_list": [], "without_epg_in_xml_list": [],
        "without_epg_skipped_list": []
    }

    for group in GROUP_OUTPUT_ORDER:
        if group in channels_for_xml:
            for channel_entry in channels_for_xml[group]:
                code = channel_entry["code"]
                channel_name = channel_entry["title"]
                
                schedules = all_schedules.get(code, {}).get("schedules", [])
                has_schedules = bool(schedules)

                if XML_SKIP_CHANNELS_WITHOUT_EPG and not has_schedules:
                    stats["skipped_no_epg"] += 1
                    stats["without_epg_skipped_list"].append(f"{channel_name} ({code})")
                    continue

                stats["channels_in_xml"] += 1
                if has_schedules:
                    stats["channels_with_epg"] += 1
                    stats["with_epg_list"].append(f"{channel_name} ({code})")
                else:
                    stats["without_epg_in_xml_list"].append(f"{channel_name} ({code})")

                channel_info = all_schedules.get(code, {}).get("channel", {})
                channel = ET.SubElement(root, "channel")
                channel_id = clean_tvg_id(channel_entry.get("original_title", channel_name))
                channel.set("id", channel_id)
                
                display_name = ET.SubElement(channel, "display-name")
                display_name.text = channel_entry.get("title", channel_info.get("title", UNKNOWN_CHANNEL))

                if has_schedules:
                    for schedule in schedules:
                        stats["total_programmes"] += 1
                        programme = ET.SubElement(root, "programme")
                        programme.set("channel", channel_id)
                        
                        start = convert_time_to_xmltv_format(schedule.get("starttime", ""))
                        end = convert_time_to_xmltv_format(schedule.get("endtime", ""))
                        if start and end:
                            programme.set("start", start)
                            programme.set("stop", end)

                        title = ET.SubElement(programme, "title")
                        title.set("lang", "zh")
                        title.text = schedule.get("title", UNKNOWN_PROGRAMME)
    return root, stats

def _write_epg_files_and_stats(root, stats, output_file=XML_FILENAME):
    """(Helper) 将XML树写入文件，压缩并记录统计信息。"""
    xml_str = minidom.parseString(ET.tostring(root, encoding='utf-8')).toprettyxml(indent="  ")
    
    # 写入XML文件
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(xml_str)
    print(f"已保存节目单XML文件到: {os.path.abspath(output_file)}")

    # 优化：直接压缩内存中的字符串，避免重复读取文件
    xml_bytes = xml_str.encode('utf-8')
    with gzip.open(XML_GZ_FILENAME, 'wb') as f_out:
        f_out.write(xml_bytes)
    print(f"已生成压缩文件: {os.path.abspath(XML_GZ_FILENAME)}")

    # 打印和记录统计信息
    print("\n" + LOG_SEPARATOR)
    print("EPG 合成统计")
    print(LOG_SEPARATOR)
    print(f"\n基本统计:")
    print(f"   - XML 中总共写入 {stats['channels_in_xml']} 个频道")
    print(f"   - 其中 {stats['channels_with_epg']} 个频道成功合成了节目数据")
    print(f"   - 总共合成了 {stats['total_programmes']} 个节目条目")
    if XML_SKIP_CHANNELS_WITHOUT_EPG:
        print(f"   - 已跳过 {stats['skipped_no_epg']} 个没有节目数据的频道")

    with open(EPG_STATISTICS_LOG, "w", encoding="utf-8") as f:
        f.write(f"EPG 合成详细统计\n{LOG_SEPARATOR}\n\n")
        f.write(f"基本统计:\n")
        f.write(f"- XML 中总共写入 {stats['channels_in_xml']} 个频道\n")
        f.write(f"- 其中 {stats['channels_with_epg']} 个频道成功合成了节目数据\n")
        f.write(f"- 总共合成了 {stats['total_programmes']} 个节目条目\n")
        if XML_SKIP_CHANNELS_WITHOUT_EPG:
            f.write(f"- 已跳过 {stats['skipped_no_epg']} 个没有节目数据的频道\n")
        
        f.write(f"\n有 EPG 数据的频道 ({len(stats['with_epg_list'])} 个):\n")
        for channel in sorted(stats['with_epg_list']):
            f.write(f"✓ {channel}\n")
        
        f.write(f"\n没有 EPG 数据但已合成到 XML 的频道 ({len(stats['without_epg_in_xml_list'])} 个):\n")
        for channel in sorted(stats['without_epg_in_xml_list']):
            f.write(f"○ {channel}\n")
        
        if XML_SKIP_CHANNELS_WITHOUT_EPG:
            f.write(f"\n没有 EPG 数据且被跳过的频道 ({len(stats['without_epg_skipped_list'])} 个):\n")
            for channel in sorted(stats['without_epg_skipped_list']):
                f.write(f"✗ {channel}\n")
    
    print(f"\n详细统计已保存到: {os.path.abspath(EPG_STATISTICS_LOG)}")
    print(LOG_SEPARATOR)

def download_and_save_all_schedules(channels_for_xml, output_file=XML_FILENAME):
    """
    (重构) 主协调函数：下载EPG数据，构建XML，并保存文件。
    """
    # 1. 并行下载EPG数据
    all_schedules = _download_epg_data_parallel(channels_for_xml)
    
    # 2. 构建XML树和统计数据
    xml_tree, stats = _build_xmltv_tree(channels_for_xml, all_schedules)
    
    # 3. 写入文件并记录日志
    _write_epg_files_and_stats(xml_tree, stats, output_file)

def run_epg_download(channels, custom_channels_config, grouped_channels):
    """
    执行 EPG 下载和生成的总函数
    (修改版：支持 EPG_DOWNLOAD_MODE 开关)
    """
    print("\n开始下载节目单...")
    
    all_channels_for_epg_download = [] # 用于生成下载URL的列表
    channels_to_write_to_xml = {}      # 用于写入XML的频道字典 (带分组)
    
    # --- (新增) EPG 下载模式切换 (需求 2) ---
    
    if EPG_DOWNLOAD_MODE == "M3U_ONLY":
        print("EPG 模式: M3U_ONLY (仅下载和合成 M3U 中的频道)")
        
        # 1. 决定下载列表：遍历 M3U 频道 (grouped_channels)
        for group_name, channels_in_group in grouped_channels.items():
            for channel in channels_in_group:
                # 只需要 'code' 即可
                if 'code' in channel:
                    all_channels_for_epg_download.append(channel)
        
        # 2. 决定写入XML的列表：就是 M3U 列表
        channels_to_write_to_xml = grouped_channels
        
        m3u_channel_count = len(all_channels_for_epg_download)
        print(f"总共将为 {m3u_channel_count} 个 M3U 频道条目尝试下载EPG。")
        print(f"XML 文件将基于这 {m3u_channel_count} 个频道生成。")

    else: # 默认为 "ALL" 模式 (原脚本的行为)
        print("EPG 模式: ALL (下载所有可用的频道，并全部写入 XML)")
        
        # 1. 决定下载列表：(原始列表 + 自定义列表)
        all_channels_for_epg_download = list(channels) # 从主列表开始 (222个)
        
        custom_channels_for_epg = []
        for group_name, custom_list in custom_channels_config.items():
            for custom_channel in custom_list:
                if 'code' in custom_channel:
                    custom_channels_for_epg.append(custom_channel)
                else:
                    print(f"警告: 自定义频道 {custom_channel.get('title', 'N/A')} 缺少 'code'，无法获取EPG。")

        all_channels_for_epg_download.extend(custom_channels_for_epg) # (32个)
        print(f"总共将为 {len(all_channels_for_epg_download)} 个频道条目尝试下载EPG。 (原始+自定义)")

        # 2. 决定写入XML的列表：(需要重新处理所有频道)
        print(f"正在为 XML (ALL 模式) 重新处理 {len(all_channels_for_epg_download)} 个频道...")
        
        for channel in all_channels_for_epg_download:
            if "title" in channel and "code" in channel:
                original_title = channel["title"]
                # 应用名称映射
                final_name = CHANNEL_NAME_MAP.get(original_title, original_title)
                
                # 使用 original_title 进行分类
                category = categorize_channel(original_title)
                
                # 构建用于XML的精简对象
                channel_obj = {
                    "title": final_name,
                    "original_title": original_title,
                    "code": channel["code"],
                    "icon": channel.get("icon", ""),
                }
                
                if category not in channels_to_write_to_xml:
                    channels_to_write_to_xml[category] = []
                channels_to_write_to_xml[category].append(channel_obj)
            
        total_xml_channels = sum(len(v) for v in channels_to_write_to_xml.values())
        print(f"XML 文件将包含 {total_xml_channels} 个频道 (包括被 M3U 过滤的)。")
    
    # --- 切换结束 ---

    # 使用 'channels_to_write_to_xml' 列表来生成 XML
    download_and_save_all_schedules(channels_to_write_to_xml)
    # --- EPG 函数内容结束 ---

def generate_m3u_content(grouped_channels, replace_url, catchup_template=CATCHUP_URL_TEMPLATE):
    if M3U_EPG_URL:
        content = [f'#EXTM3U x-tvg-url="{M3U_EPG_URL}"']
    else:
        content = ["#EXTM3U"]
    
    catchup_enabled_count = 0
    
    # --- 改进的代理处理逻辑 ---
    # 默认使用原始回看前缀
    final_catchup_prefix = CATCHUP_SOURCE_PREFIX_NORM
    
    # 如果设置了代理前缀，并且回看前缀也存在
    if NGINX_PROXY_PREFIX_NORM and CATCHUP_SOURCE_PREFIX_NORM:
        source_parts = urlparse(CATCHUP_SOURCE_PREFIX_NORM)
        source_path_part = source_parts.netloc + source_parts.path
        final_catchup_prefix = urljoin(NGINX_PROXY_PREFIX_NORM, source_path_part)
        print(f"已将回看源代理至: {final_catchup_prefix}")
    
    # (--- 修改：使用全局输出顺序 ---)
    for group in GROUP_OUTPUT_ORDER:
        for ch in grouped_channels.get(group, []):
            
            # 跳过没有播放链接的频道
            if not ch.get("zteurl"):
                continue
                    
            # 修复的URL替换逻辑 - 使用更健壮的方法
            if replace_url:
                original_url = ch["zteurl"]
                parsed_original = urlparse(original_url)

                # We only want to replace known multicast/streaming protocols
                if parsed_original.scheme in ["rtp", "rtsp", "http", "https"]:
                    # The part to append is the netloc and the path
                    address_part = parsed_original.netloc + parsed_original.path
                    url = urljoin(REPLACEMENT_IP_NORM, address_part)
                elif not parsed_original.scheme:
                    # Handle cases like "239.21.0.88:3692" (no scheme)
                    url = urljoin(REPLACEMENT_IP_NORM, original_url)
                else:
                    # For unknown schemes (e.g., 'foo://...'), keep the original
                    url = original_url
            else:
                url = ch["zteurl"]
            
            # 改进的图标URL处理
            logo_url = ch["icon"]
            if logo_url and NGINX_PROXY_PREFIX_NORM:
                logo_parts = urlparse(logo_url)
                logo_path_part = logo_parts.netloc + logo_parts.path
                logo_url = urljoin(NGINX_PROXY_PREFIX_NORM, logo_path_part)
            
            #  修改：使用清理后的 tvg-id
            cleaned_tvg_id = clean_tvg_id(ch.get("original_title", ch["title"]))
            
            # 构建EXTINF行
            extinf_parts = [
                f'#EXTINF:-1 tvg-id="{cleaned_tvg_id}"',
                f'tvg-name="{ch.get("original_title", ch["title"])}"',
                f'tvg-logo="{logo_url}"'
            ]
            
            # 只有当频道支持回看时才添加catchup属性
            if ch.get("supports_catchup", False):
                ztecode = ch.get("ztecode", "")
                if ztecode:
                    #  使用传入的回看URL模板
                    catchup_source = catchup_template.format(
                        prefix=final_catchup_prefix,
                        ztecode=ztecode
                    )
                    
                    extinf_parts.append(f'catchup="default"')
                    extinf_parts.append(f'catchup-source="{catchup_source}"')
                    catchup_enabled_count += 1
                elif ch.get("is_custom", False):
                    print(f"提示: 自定义频道 '{ch['title']}' 标记为支持回看但缺少 'ztecode'。")
            
            extinf_parts.append(f'group-title="{group}",{ch["title"]}')
            
            content.append(' '.join(extinf_parts))
            content.append(url)
            
    print(f"已为 {catchup_enabled_count} 个支持回看的频道添加catchup属性")
    return '\n'.join(content)

def main():
    """脚本的主入口点。"""
    # 打印当前使用的配置
    print_configuration()
    
    # 加载自定义配置文件
    channel_order = load_channel_order(CHANNEL_ORDER_FILE)
    custom_channels_config = load_custom_channels(CUSTOM_CHANNELS_FILE)
    
    # 添加调试信息
    print(f"自定义频道配置: {list(custom_channels_config.keys())}")
    for group_name, channels in custom_channels_config.items():
        print(f"  分组 '{group_name}' 有 {len(channels)} 个频道")

    data = download_json_data(JSON_URL)
    if data is None:
        print("程序退出")
        sys.exit(1)

    channels = data["channels"]
    
    # 处理频道（去重、名称映射等）
    kept_channels, blacklisted_main_channels, removed_channels = process_channels(channels)
    
    # (--- 修改：动态生成 grouped_channels ---)
    grouped_channels = {group: [] for group in GROUP_DEFINITIONS.keys()}

    # ==================== 代码修改区域开始 ====================
    
    skipped_url_count = 0 # 用于统计跳过的频道

    for channel in kept_channels:
        category = categorize_channel(channel["title"])
        
        # 检查频道是否支持回看功能
        supports_catchup = (channel.get("timeshiftAvailable", "false") == "true" or 
                           channel.get("lookbackAvailable", "false") == "true")
        
        # 使用最终名称
        final_name = channel.get("final_name", channel["title"])
        
        # --- 新增：处理 zteurl 和 hwurl ---
        params = channel.get("params", {})
        zteurl = params.get("zteurl")
        hwurl = params.get("hwurl")
        
        final_url = ""
        if zteurl: 
            final_url = zteurl
        elif hwurl: 
            final_url = hwurl
        
        # 如果两个URL都无效（不存在或为空），则跳过此频道
        if not final_url:
            skipped_url_count += 1
            continue
        
        grouped_channels[category].append({
            "title": final_name,
            "original_title": channel["title"],
            "code": channel["code"],
            "ztecode": params.get("ztecode", ""),
            "icon": channel["icon"],
            "zteurl": final_url,
            "number": extract_number(final_name),
            "supports_catchup": supports_catchup,
            "is_custom": False
        })

    # 添加自定义频道并获取黑名单信息
    grouped_channels, blacklisted_custom_channels, added_custom_channels = add_custom_channels(grouped_channels, custom_channels_config)
    
    #  合并所有黑名单频道
    all_blacklisted_channels = blacklisted_main_channels + blacklisted_custom_channels
    
    # 应用自定义排序
    grouped_channels = apply_custom_sorting(grouped_channels, channel_order)
    
    # 对于没有自定义排序的组，使用默认排序
    for category in grouped_channels:
        if category not in channel_order:
            grouped_channels[category].sort(key=lambda x: (x["number"], x["title"]))

    #  生成M3U文件 - 现在生成三个文件
    for filename, replace_url, catchup_template in [
        (TV_M3U_FILENAME, False, CATCHUP_URL_TEMPLATE),      # 组播地址，标准回看模板
        (TV2_M3U_FILENAME, True, CATCHUP_URL_TEMPLATE),      # 单播地址，标准回看模板
        (KU9_M3U_FILENAME, True, CATCHUP_URL_KU9)           #  单播地址，KU9回看模板
    ]:
        content = generate_m3u_content(grouped_channels, replace_url, catchup_template)
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"已生成M3U文件: {filename}")

    total_channels = sum(len(v) for v in grouped_channels.values())
    
    #  更新统计输出
    print(f"\n已跳过 {skipped_url_count} 个缺少播放链接的频道。")
    print(f"总共过滤 {len(all_blacklisted_channels)} 个黑名单频道（主JSON: {len(blacklisted_main_channels)}, 自定义: {len(blacklisted_custom_channels)}）")
    
    print(f"成功生成 {total_channels} 个频道")
    print(f"单播地址列表: {os.path.abspath(TV2_M3U_FILENAME)}")
    print(f"KU9回看参数列表: {os.path.abspath(KU9_M3U_FILENAME)}")  #  新增输出信息
    
    # 统一生成完整的日志文件，包含主JSON和自定义频道的所有处理结果
    with open(CHANNEL_PROCESSING_LOG, "w", encoding="utf-8") as f:
        f.write("频道处理日志\n")
        f.write(f"{LOG_SEPARATOR}\n\n")
        
        # ========== 主JSON频道处理结果 ==========
        f.write("【主JSON频道处理结果】\n")
        f.write(f"{LOG_SEPARATOR}\n\n")
        
        f.write(f"1. 黑名单过滤 ({len(blacklisted_main_channels)} 个):\n")
        for channel in blacklisted_main_channels:
            f.write(f"  - 标题: {channel['title']}, 代码: {channel['code']}, 原因: {channel['reason']}\n")
        f.write("\n")
        
        f.write(f"2. 去重过滤 ({len(removed_channels)} 个):\n")
        for channel in removed_channels:
            f.write(f"  - {channel['name']} (原因: {channel['reason']})\n")
        f.write("\n")
        
        f.write(f"3. 最终保留 ({len(kept_channels)} 个):\n")
        for channel in kept_channels:
            original_name = channel["title"]
            final_name = channel.get("final_name", original_name)
            if original_name != final_name:
                f.write(f"  - {original_name} -> {final_name}\n")
            else:
                f.write(f"  - {original_name}\n")
        f.write("\n\n")
        
        # ========== 自定义频道处理结果 ==========
        f.write("【自定义频道处理结果】\n")
        f.write(f"{LOG_SEPARATOR}\n\n")
        
        f.write(f"1. 黑名单过滤 ({len(blacklisted_custom_channels)} 个):\n")
        if blacklisted_custom_channels:
            for channel in blacklisted_custom_channels:
                f.write(f"  - 标题: {channel['title']}, 代码: {channel['code']}, 原因: {channel['reason']}\n")
        else:
            f.write("  (无)\n")
        f.write("\n")
        
        f.write(f"2. 成功添加 ({len(added_custom_channels)} 个):\n")
        if added_custom_channels:
            for channel in added_custom_channels:
                original_name = channel['original_title']
                final_name = channel['title']
                group_name = channel['group']
                if original_name != final_name:
                    f.write(f"  - [{group_name}] {original_name} -> {final_name}\n")
                else:
                    f.write(f"  - [{group_name}] {final_name}\n")
        else:
            f.write("  (无)\n")
        f.write("\n\n")
        
        # ========== 汇总信息 ==========
        f.write("【处理汇总】\n")
        f.write(f"{LOG_SEPARATOR}\n\n")
        f.write(f"黑名单过滤汇总:\n")
        f.write(f"  - 主JSON频道: {len(blacklisted_main_channels)} 个\n")
        f.write(f"  - 自定义频道: {len(blacklisted_custom_channels)} 个\n")
        f.write(f"  - 总计: {len(all_blacklisted_channels)} 个\n")
        f.write("\n")
        f.write(f"最终频道统计:\n")
        f.write(f"  - 主JSON保留: {len(kept_channels)} 个\n")
        f.write(f"  - 自定义频道: {len(added_custom_channels)} 个\n")
        f.write(f"  - 总计: {len(kept_channels) + len(added_custom_channels)} 个\n")
    
    print(f"已生成处理日志: {os.path.abspath(CHANNEL_PROCESSING_LOG)}")
    
    # --- EPG 下载控制开关 ---
    # 通过配置区域的 ENABLE_EPG_DOWNLOAD 开关控制是否下载EPG
    if ENABLE_EPG_DOWNLOAD:
        run_epg_download(channels, custom_channels_config, grouped_channels)
    else:
        print("\nEPG下载已禁用，跳过EPG下载和生成。")

if __name__ == "__main__":
    main()