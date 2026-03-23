# -*- coding: utf-8 -*-
import json
import re
import os
import io
import requests
import sys
import gzip
import time 
import threading 
import copy
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from xml.dom import minidom
from concurrent.futures import ThreadPoolExecutor, as_completed

# 设置标准输出编码
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ===================== 配置加载区域 =====================

# 只保留配置文件入口，业务参数统一从 JSON 读取，避免与代码重复维护。
CONFIG_FILE = "config/config.json"
MY_CONFIG_FILE = "config/myconfig.json"

# 运行时配置白名单与默认值，在 initialize_configuration 中根据 config.json 生成。
DEFAULT_CONFIG = {}
CONFIGURABLE_KEYS = set()

# 派生/缓存变量使用安全初值，避免导入阶段出现未定义访问。
REPLACEMENT_IP_NORM = ""
REPLACEMENT_IP_TV_NORM = ""
CATCHUP_SOURCE_PREFIX_NORM = ""
NGINX_PROXY_PREFIX_NORM = ""
XML_GZ_FILENAME = ""
BLACKLIST_TITLE_SET = set()
BLACKLIST_CODE_SET = set()
BLACKLIST_ZTEURL_SET = set()

# 🚀 性能优化：预编译正则表达式
CCTV_PATTERN = re.compile(r'CCTV-(\d+)')  # 匹配CCTV-数字模式
NUMBER_PATTERN = re.compile(r'\d+')  # 匹配数字
QUALITY_PATTERN = re.compile(r'(?:高清|超清|4K|\d+K)')  # 匹配清晰度标识
TVG_ID_CLEAN_PATTERN = re.compile(r'[_\s]*(高清|超清|4K)[_\s]*')  # 清理tvg-id中的清晰度标识
SPACE_DASH_PATTERN = re.compile(r'\s+-\s+')  # 匹配空格-空格模式
MULTI_SPACE_PATTERN = re.compile(r'\s+')  # 匹配多个空格

# 配置驱动变量占位（真实值由 initialize_configuration 写入）
IS_HWURL = False
EPG_DOWNLOAD_RETRY_COUNT = 0
EPG_DOWNLOAD_RETRY_DELAY = 0
EPG_DOWNLOAD_TIMEOUT = 0
TV_M3U_FILENAME = ""
TV2_M3U_FILENAME = ""
KU9_M3U_FILENAME = ""
APTV_M3U_FILENAME = ""
XML_FILENAME = ""
REPLACEMENT_IP = ""
REPLACEMENT_IP_TV = ""
CATCHUP_SOURCE_PREFIX = ""
NGINX_PROXY_PREFIX = ""
ENABLE_NGINX_PROXY_FOR_TV = False
JSON_URL = ""
M3U_EPG_URL = ""
EPG_BASE_URLS = []
CATCHUP_URL_TEMPLATE = ""
CATCHUP_URL_KU9 = ""
CATCHUP_URL_APTV = ""
CHANNEL_ORDER_FILE = ""
CUSTOM_CHANNELS_FILE = ""
EXTERNAL_M3U_URL = ""
EXTERNAL_M3U_CACHE_FILE = ""
EXTERNAL_GROUP_TITLES = {}
ENABLE_EXTERNAL_M3U_MERGE = False
BLACKLIST_RULES = {"title": [], "code": [], "zteurl": []}
CHANNEL_NAME_MAP = {}
ENABLE_EPG_DOWNLOAD = False
EPG_DOWNLOAD_MODE = ""
XML_SKIP_CHANNELS_WITHOUT_EPG = True
GROUP_DEFINITIONS = {}
GROUP_CLASSIFICATION_PRIORITY = []
GROUP_OUTPUT_ORDER = []
TIMEZONE_OFFSET = ""
DATE_FORMAT = ""
XML_GENERATOR_NAME = ""
LOG_SEPARATOR = ""
UNKNOWN_CHANNEL = ""
UNKNOWN_PROGRAMME = ""
CHANNEL_PROCESSING_LOG = ""
EPG_STATISTICS_LOG = ""

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

def ensure_url_scheme(url, default_scheme='http'):
    """
    确保URL包含协议前缀，如果没有则添加默认协议
    兼容不同Python版本的urlparse行为
    使用字符串检查而不是urlparse，更可靠
    :param url: 要处理的URL
    :param default_scheme: 默认协议（默认为 'http'）
    :return: 包含协议前缀的URL
    """
    if not url:
        return url
    
    # 转换为字符串（防止其他类型）
    url = str(url).strip()
    
    if not url:
        return url
    
    # 如果已经有协议前缀（包含 ://），直接返回
    if '://' in url:
        return url
    
    # 如果没有协议前缀，添加默认协议
    # 兼容处理：去除可能的前导斜杠
    url = url.lstrip('/')
    if url:
        return f"{default_scheme}://{url}"
    else:
        return url

def ensure_parent_directory(file_path):
    """确保目标文件的父目录存在。"""
    dir_path = os.path.dirname(file_path)
    if dir_path:
        os.makedirs(dir_path, exist_ok=True)

def deep_merge_dict(base, override):
    """递归合并字典：override 覆盖 base。"""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_dict(result[key], value)
        else:
            result[key] = value
    return result

def load_json_config_file(file_path):
    """加载 JSON 配置文件，失败时返回 None。"""
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            print(f"警告: 配置文件 {file_path} 顶层必须是对象(dict)，已忽略。")
            return None
        return data
    except Exception as e:
        print(f"警告: 读取配置文件失败 {file_path}: {e}")
        return None

def is_valid_config_type(value, default_value):
    """使用默认值类型做基础校验，避免明显错误配置导致运行异常。"""
    if isinstance(default_value, bool):
        return isinstance(value, bool)
    if isinstance(default_value, int):
        return isinstance(value, int) and not isinstance(value, bool)
    if isinstance(default_value, str):
        return isinstance(value, str)
    if isinstance(default_value, list):
        return isinstance(value, list)
    if isinstance(default_value, dict):
        return isinstance(value, dict)
    return True

def apply_config_overrides(config_data):
    """将配置值写回同名全局变量，仅允许白名单键。"""
    for key in CONFIGURABLE_KEYS:
        if key in config_data:
            default_value = DEFAULT_CONFIG[key]
            value = config_data[key]
            if is_valid_config_type(value, default_value):
                globals()[key] = value
            else:
                print(f"警告: 配置项 {key} 类型不正确，已回退默认值。")
                globals()[key] = copy.deepcopy(default_value)

def recompute_derived_config():
    """根据当前配置重算派生变量。"""
    global REPLACEMENT_IP_NORM
    global REPLACEMENT_IP_TV_NORM
    global CATCHUP_SOURCE_PREFIX_NORM
    global NGINX_PROXY_PREFIX_NORM
    global BLACKLIST_TITLE_SET
    global BLACKLIST_CODE_SET
    global BLACKLIST_ZTEURL_SET
    global XML_GZ_FILENAME

    REPLACEMENT_IP_NORM = normalize_url(REPLACEMENT_IP, trailing_slash='add')
    REPLACEMENT_IP_TV_NORM = normalize_url(REPLACEMENT_IP_TV, trailing_slash='add') if REPLACEMENT_IP_TV else ""
    CATCHUP_SOURCE_PREFIX_NORM = normalize_url(CATCHUP_SOURCE_PREFIX, trailing_slash='remove')
    NGINX_PROXY_PREFIX_NORM = normalize_url(NGINX_PROXY_PREFIX, trailing_slash='add')

    blacklist_title = BLACKLIST_RULES.get("title", []) if isinstance(BLACKLIST_RULES, dict) else []
    blacklist_code = BLACKLIST_RULES.get("code", []) if isinstance(BLACKLIST_RULES, dict) else []
    blacklist_zteurl = BLACKLIST_RULES.get("zteurl", []) if isinstance(BLACKLIST_RULES, dict) else []
    BLACKLIST_TITLE_SET = set(blacklist_title)
    BLACKLIST_CODE_SET = set(blacklist_code)
    BLACKLIST_ZTEURL_SET = set(blacklist_zteurl)

    XML_GZ_FILENAME = XML_FILENAME + ".gz"

def initialize_configuration():
    """初始化配置：config.json -> myconfig.json。"""
    global DEFAULT_CONFIG
    global CONFIGURABLE_KEYS

    base_config = load_json_config_file(CONFIG_FILE)
    if base_config is None:
        print(f"错误: 必需配置文件不存在或无效: {CONFIG_FILE}")
        sys.exit(1)

    DEFAULT_CONFIG = copy.deepcopy(base_config)
    CONFIGURABLE_KEYS = set(DEFAULT_CONFIG.keys())
    merged_config = copy.deepcopy(DEFAULT_CONFIG)
    loaded_files = [CONFIG_FILE]

    local_config = load_json_config_file(MY_CONFIG_FILE)
    if local_config is not None:
        merged_config = deep_merge_dict(merged_config, local_config)
        loaded_files.append(MY_CONFIG_FILE)

    apply_config_overrides(merged_config)
    recompute_derived_config()

    print(f"配置加载完成，生效优先级: {' -> '.join(loaded_files)}")

def clean_tvg_id(title):
    """清理频道标题，生成标准的 tvg-id"""
    cleaned = TVG_ID_CLEAN_PATTERN.sub('', title)
    if 'CCTV' in cleaned:
        cleaned = cleaned.replace('-', '')
    return cleaned.strip()

def apply_channel_name_mapping(channel, base_name):
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
    if REPLACEMENT_IP_TV_NORM:
        print(f"tv.m3u 专用的UDPXY地址是 {REPLACEMENT_IP_TV_NORM}")
    else:
        print(f"tv.m3u 使用原始地址（未配置 REPLACEMENT_IP_TV）")
    print(f"你的回看源前缀是 {CATCHUP_SOURCE_PREFIX_NORM}")
    print(f"你的nginx代理前缀是 {NGINX_PROXY_PREFIX_NORM}")
    print(f"tv.m3u 使用nginx代理: {'是' if ENABLE_NGINX_PROXY_FOR_TV else '否'}")
    print(f"你的回看URL模板是 {CATCHUP_URL_TEMPLATE}")
    print(f"你的KU9回看URL模板是 {CATCHUP_URL_KU9}")
    print(f"你的APTV回看URL模板是 {CATCHUP_URL_APTV}")
    print(f"优先提取地址类型: {'HWURL (Huawei)' if IS_HWURL else 'ZTEURL (ZTE)'}")
    print(f"回看参数代码: 始终使用 ztecode")
    print(f"EPG下载开关: {'启用' if ENABLE_EPG_DOWNLOAD else '禁用'}")
    if ENABLE_EPG_DOWNLOAD:
        print(f"EPG下载配置: 重试{EPG_DOWNLOAD_RETRY_COUNT}次, 超时{EPG_DOWNLOAD_TIMEOUT}秒, 间隔{EPG_DOWNLOAD_RETRY_DELAY}秒")
    print(f"外部M3U合并开关: {'启用' if ENABLE_EXTERNAL_M3U_MERGE else '禁用'}")
    if ENABLE_EXTERNAL_M3U_MERGE:
        print(f"外部M3U地址: {EXTERNAL_M3U_URL}")
        print(f"提取的分组: {', '.join(EXTERNAL_GROUP_TITLES) if EXTERNAL_GROUP_TITLES else '(未配置)'}")

def download_with_retry(url, max_retries=None, timeout=None, headers=None):
    """ 带重试机制的下载函数"""
    if max_retries is None:
        max_retries = EPG_DOWNLOAD_RETRY_COUNT
    if timeout is None:
        timeout = EPG_DOWNLOAD_TIMEOUT

    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=timeout, headers=headers)
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
    # 这里的逻辑不需要修改，因为它会检查所有可能的 URL
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
    
    # 打印自定义频道处理开始
    print("\n正在处理自定义频道...")
    
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
            
            # --- 修改开始：修复 ztecode 提取逻辑 ---
            # 检查是否需要应用名称映射
            original_title = custom_channel["title"]
            if original_title in CHANNEL_NAME_MAP:
                final_name = CHANNEL_NAME_MAP[original_title]
                print(f"自定义频道名称映射: '{original_title}' -> '{final_name}'")
            else:
                final_name = original_title

            # 提取 ztecode：先尝试从 params 中获取，如果为空则从根目录获取
            params = custom_channel.get("params", {})
            ztecode_from_params = params.get("ztecode", "")
            ztecode_from_root = custom_channel.get("ztecode", "")
            
            # 优先使用 params 中的 ztecode，如果为空则使用根目录的
            final_ztecode = ztecode_from_params if ztecode_from_params else ztecode_from_root
            
            # 提取 supports_catchup：先尝试从根目录获取，如果不存在则从 params 获取
            supports_catchup = custom_channel.get("supports_catchup", False)
            if not supports_catchup and params.get("supports_catchup", ""):
                supports_catchup = params.get("supports_catchup", False)
            
            # 为自定义频道添加必要的字段
            custom_channel["title"] = final_name           # 使用最终名称
            custom_channel["original_title"] = original_title  # 保留原始名称
            custom_channel["number"] = extract_number(final_name) # 使用最终名称提取编号
            custom_channel["ztecode"] = final_ztecode     # 存储 ztecode
            custom_channel["supports_catchup"] = supports_catchup  # 存储回看支持状态
            # --- 修改结束 ---
            
            custom_channel["is_custom"] = True  # 标记为自定义频道
            
            # 【新增】对自定义频道应用 HWURL/ZTEURL 选择逻辑
            params = custom_channel.get("params", {})
            # 兼容 params 中的 key，也兼容根目录下的 key
            raw_zteurl = params.get("zteurl", "") or custom_channel.get("zteurl", "")
            raw_hwurl = params.get("hwurl", "") or custom_channel.get("hwurl", "")
            
            final_url = ""
            url_source_type = ""

            if IS_HWURL:
                # 优先使用 Huawei URL
                if raw_hwurl:
                    final_url = raw_hwurl
                    url_source_type = "HWURL"
                elif raw_zteurl:
                    final_url = raw_zteurl
                    url_source_type = "ZTEURL"
            else:
                # 优先使用 ZTE URL
                if raw_zteurl:
                    final_url = raw_zteurl
                    url_source_type = "ZTEURL"
                elif raw_hwurl:
                    final_url = raw_hwurl
                    url_source_type = "HWURL"
            
            # 如果找到了有效的 URL，覆盖 custom_channel 中的 zteurl 字段
            # 注意：M3U 生成器读取的是 custom_channel["zteurl"]
            if final_url:
                custom_channel["zteurl"] = final_url
                custom_channel["url_source"] = url_source_type # 记录来源类型
                print(f"  [{url_source_type}] {final_name} (自定义)")
            else:
                # 如果没有找到任何 URL，可能是旧格式或者用户只填了 url 字段
                # 如果用户填了 "url" 字段，我们将其作为 zteurl 使用
                fallback_url = custom_channel.get("url", "")
                if fallback_url:
                     custom_channel["zteurl"] = fallback_url
                     custom_channel["url_source"] = "FALLBACK"
                     print(f"  [FALLBACK] {final_name} (自定义)")
                else:
                     print(f"  [警告] 自定义频道 {final_name} 未找到有效链接")

            # 添加到分组
            grouped_channels[group_name].append(custom_channel)
            # 记录成功添加的自定义频道 (关键修复：加入 url_source 到日志记录列表)
            added_custom_channels.append({
                "title": final_name,
                "original_title": original_title,
                "group": group_name,
                "ztecode": final_ztecode,  # 添加 ztecode 到记录
                "url_source": custom_channel.get("url_source", "UNKNOWN") # <--- 关键修复：添加此行
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
                # 使用与M3U tvg-name相同的逻辑：优先使用original_title
                display_name.text = channel_entry.get("original_title", channel_entry.get("title", channel_info.get("title", UNKNOWN_CHANNEL)))

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

def _write_epg_files_and_stats(root, stats, output_file=None):
    """(Helper) 将XML树写入文件，压缩并记录统计信息。"""
    if output_file is None:
        output_file = XML_FILENAME

    xml_str = minidom.parseString(ET.tostring(root, encoding='utf-8')).toprettyxml(indent="  ")
    
    # 写入XML文件
    ensure_parent_directory(output_file)
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(xml_str)
    print(f"已保存节目单XML文件到: {os.path.abspath(output_file)}")

    # 优化：直接压缩内存中的字符串，避免重复读取文件
    xml_bytes = xml_str.encode('utf-8')
    ensure_parent_directory(XML_GZ_FILENAME)
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

    ensure_parent_directory(EPG_STATISTICS_LOG)
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

def download_and_save_all_schedules(channels_for_xml, output_file=None):
    if output_file is None:
        output_file = XML_FILENAME

    # 1. 并行下载EPG数据
    all_schedules = _download_epg_data_parallel(channels_for_xml)
    
    # 2. 构建XML树和统计数据
    xml_tree, stats = _build_xmltv_tree(channels_for_xml, all_schedules)
    
    # 3. 写入文件并记录日志
    _write_epg_files_and_stats(xml_tree, stats, output_file)

def run_epg_download(channels, custom_channels_config, grouped_channels):
    print("\n开始下载节目单...")
    
    all_channels_for_epg_download = [] # 用于生成下载URL的列表
    channels_to_write_to_xml = {}      # 用于写入XML的频道字典 (带分组)
      
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
    
    # 使用 'channels_to_write_to_xml' 列表来生成 XML
    download_and_save_all_schedules(channels_to_write_to_xml)
    # --- EPG 函数内容结束 ---

def download_external_m3u(url, cache_file=None):
    if cache_file is None:
        cache_file = EXTERNAL_M3U_CACHE_FILE

    # 尝试从网络下载
    try:
        print(f"正在下载外部 M3U 文件: {url}")
        # 模拟浏览器的 HTTP 头部信息，避免 403 Forbidden 错误
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Cache-Control': 'max-age=0'
        }
        response = download_with_retry(url, max_retries=3, timeout=30, headers=headers)
        if response:
            content = response.text
            print(f"成功下载外部 M3U 文件，大小: {len(content)} 字节")
            
            # --- 新增：成功下载后，将其保存到本地缓存 ---
            try:
                ensure_parent_directory(cache_file)
                with open(cache_file, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"已将外部 M3U 更新并缓存至本地: {cache_file}")
            except Exception as e:
                print(f"警告: 写入本地缓存文件失败: {e}")
                
            return content
    except Exception as e:
        print(f"下载外部 M3U 文件失败: {e}")

    # --- 新增：如果下载失败，尝试读取本地缓存 ---
    print(f"尝试使用本地缓存的外部 M3U 文件: {cache_file}")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                content = f.read()
            print(f"成功读取本地缓存文件，大小: {len(content)} 字节")
            return content
        except Exception as e:
            print(f"读取本地缓存文件失败: {e}")
    else:
        print("本地缓存文件不存在，无法执行回退机制。")

    return None


def parse_m3u_content(m3u_content, target_groups):
    """
    解析 M3U 内容，提取指定 group-title 的频道，并应用黑名单过滤
    """
    if not m3u_content or not target_groups:
        return [], []
    
    # 将目标分组转换为集合，提高查找效率
    target_groups_set = set(target_groups)
    
    channels = []
    blacklisted_channels = []
    lines = m3u_content.strip().split('\n')
    i = 0
    
    # 跳过文件头
    if lines and lines[0].startswith('#EXTM3U'):
        i = 1
    
    current_channel = None
    
    while i < len(lines):
        line = lines[i].strip()
        
        if line.startswith('#EXTINF'):
            # 解析 EXTINF 行
            current_channel = {
                'extinf_line': line,
                'extra_lines': [],  # 存储 #EXTVLCOPT, #KODIPROP 等额外行
                'attributes': {},
                'url': None,
                'title': '',
                'group_title': ''
            }
            
            # 提取标题（最后一个逗号后的内容）
            if ',' in line:
                title_part = line.split(',')[-1]
                current_channel['title'] = title_part.strip()
            
            # 解析属性（tvg-id, tvg-name, tvg-logo, group-title, http-referer 等）
            # 使用正则表达式提取属性，支持多种属性格式
            # 匹配 key="value" 格式
            attr_pattern = r'(\S+?)="([^"]*)"'
            matches = re.findall(attr_pattern, line)
            for attr_name, attr_value in matches:
                current_channel['attributes'][attr_name] = attr_value
                if attr_name == 'group-title':
                    current_channel['group_title'] = attr_value
            
        elif line.startswith('#') and not line.startswith('#EXTINF') and current_channel:
            # 这是额外的属性行（如 #EXTVLCOPT, #KODIPROP 等）
            current_channel['extra_lines'].append(line)
            
        elif line and not line.startswith('#') and current_channel:
            # 这是 URL 行
            current_channel['url'] = line.strip()
            
            # 检查 group-title 是否在目标列表中
            if current_channel['group_title'] in target_groups_set:
                # 构建用于黑名单检查的频道对象（兼容 is_blacklisted 函数）
                channel_for_check = {
                    'title': current_channel['title'],
                    'zteurl': current_channel['url']
                }
                
                # 应用黑名单过滤
                if is_blacklisted(channel_for_check):
                    blacklisted_channels.append({
                        'title': current_channel['title'],
                        'code': '',
                        'reason': '黑名单规则匹配',
                        'source': '外部M3U'
                    })
                    current_channel = None
                    i += 1
                    continue
                
                # 创建一个新的字典副本，避免引用问题
                channel_copy = current_channel.copy()
                channel_copy['extra_lines'] = current_channel['extra_lines'].copy()
                channels.append(channel_copy)
            
            current_channel = None
        
        i += 1
    
    print(f"从外部 M3U 中提取了 {len(channels)} 个频道 (目标分组: {', '.join(target_groups)})")
    if blacklisted_channels:
        print(f"已过滤 {len(blacklisted_channels)} 个黑名单外部频道")
    return channels, blacklisted_channels

def build_external_extinf_line(channel, use_proxy=True):
    """
    构建外部频道的 EXTINF 行，应用 NGINX_PROXY_PREFIX 到 tvg-logo
    """
    # 获取原始 EXTINF 行和属性
    original_line = channel.get('extinf_line', '')
    attributes = channel.get('attributes', {})
    title = channel.get('title', '')
    
    # 如果没有 tvg-logo 属性，直接返回原始行
    if 'tvg-logo' not in attributes:
        return original_line
    
    # 处理 tvg-logo，应用 NGINX_PROXY_PREFIX（如果设置且允许使用代理）
    logo_url = attributes['tvg-logo']
    if use_proxy and NGINX_PROXY_PREFIX_NORM and logo_url:
        # 提取图标的路径部分
        if logo_url.startswith('http://'):
            logo_path = logo_url[7:]
        elif logo_url.startswith('https://'):
            logo_path = logo_url[8:]
        else:
            logo_path = logo_url
        
        # 确保路径不以斜杠开头，避免重复斜杠
        if logo_path.startswith('/'):
            logo_path = logo_path[1:]
        
        # 组合代理URL
        new_logo_url = NGINX_PROXY_PREFIX_NORM + logo_path
        
        # 在原始行中替换 tvg-logo 的值
        # 使用正则表达式替换 tvg-logo="原值" 为 tvg-logo="新值"
        logo_pattern = r'tvg-logo="([^"]*)"'
        new_line = re.sub(logo_pattern, f'tvg-logo="{new_logo_url}"', original_line)
        return new_line
    
    # 如果不需要代理，返回原始行
    return original_line

def generate_m3u_content(grouped_channels, replace_url, catchup_template=None, external_channels=None, is_tv_m3u=False, channel_order=None):
    """
    生成 M3U 内容
    :param grouped_channels: 本地频道分组字典
    :param replace_url: 是否替换 URL（组播转单播）
    :param catchup_template: 回看 URL 模板
    :param external_channels: 外部频道列表（可选），如果提供则合并到 M3U
    :param is_tv_m3u: 是否为 tv.m3u 文件（影响 URL 替换、代理使用和 ztecode 参数）
    :param channel_order: 自定义排序字典（从 channel_order.json 加载）
    :return: M3U 文件内容（字符串）
    """
    if channel_order is None:
        channel_order = {}
    if catchup_template is None:
        catchup_template = CATCHUP_URL_TEMPLATE

    # 排序辅助函数：对给定的外部频道列表按 order_list 排序
    def sort_external_channels(channels, order_list):
        if not order_list:
            return channels
        channel_map = {ch['title']: ch for ch in channels}
        sorted_channels = []
        processed = set()
        for name in order_list:
            if name in channel_map:
                sorted_channels.append(channel_map[name])
                processed.add(name)
        # 添加未在排序列表中的频道（保持原有顺序）
        for ch in channels:
            if ch['title'] not in processed:
                sorted_channels.append(ch)
        return sorted_channels

    if M3U_EPG_URL:
        content = [f'#EXTM3U x-tvg-url="{M3U_EPG_URL}"']
    else:
        content = ["#EXTM3U"]
    
    catchup_enabled_count = 0

    # --- 改进的代理处理逻辑（使用简单字符串拼接，兼容性更好）---
    final_catchup_prefix = CATCHUP_SOURCE_PREFIX_NORM
    use_proxy_for_this_file = NGINX_PROXY_PREFIX_NORM and (not is_tv_m3u or ENABLE_NGINX_PROXY_FOR_TV)
    if use_proxy_for_this_file and CATCHUP_SOURCE_PREFIX_NORM:
        if CATCHUP_SOURCE_PREFIX_NORM.startswith('http://'):
            catchup_path = CATCHUP_SOURCE_PREFIX_NORM[7:]
        elif CATCHUP_SOURCE_PREFIX_NORM.startswith('https://'):
            catchup_path = CATCHUP_SOURCE_PREFIX_NORM[8:]
        else:
            catchup_path = CATCHUP_SOURCE_PREFIX_NORM
        if catchup_path.startswith('/'):
            catchup_path = catchup_path[1:]
        final_catchup_prefix = NGINX_PROXY_PREFIX_NORM + catchup_path
        print(f"已将回看源代理至: {final_catchup_prefix}")

    # 如果提供了外部频道，按分组组织它们
    external_channels_by_group = {}
    external_groups_in_order = set()
    external_groups_not_in_order = set()
    if external_channels:
        for ext_ch in external_channels:
            group_title = ext_ch.get('group_title', '')
            if group_title not in external_channels_by_group:
                external_channels_by_group[group_title] = []
            external_channels_by_group[group_title].append(ext_ch)
            if group_title in GROUP_OUTPUT_ORDER:
                external_groups_in_order.add(group_title)
            else:
                external_groups_not_in_order.add(group_title)

    # 按照 GROUP_OUTPUT_ORDER 的顺序输出本地频道，并在对应位置输出外部频道
    for group in GROUP_OUTPUT_ORDER:
        # 先输出该分组下的外部频道（如果存在且该分组在输出顺序中）
        if group in external_groups_in_order and group in external_channels_by_group:
            # 获取该分组的排序列表，并对外部频道列表进行排序
            order_list = channel_order.get(group, [])
            sorted_ext_channels = sort_external_channels(external_channels_by_group[group], order_list)
            for ext_ch in sorted_ext_channels:
                # 动态构建 EXTINF 行（从 attributes 构建，避免依赖原始行）
                attrs = ext_ch.get('attributes', {}).copy()
                title = ext_ch.get('title', '')
                # 对 tvg-logo 应用代理（如果需要）
                if use_proxy_for_this_file and 'tvg-logo' in attrs and attrs['tvg-logo']:
                    logo = attrs['tvg-logo']
                    if logo.startswith('http://'):
                        logo_path = logo[7:]
                    elif logo.startswith('https://'):
                        logo_path = logo[8:]
                    else:
                        logo_path = logo
                    if logo_path.startswith('/'):
                        logo_path = logo_path[1:]
                    attrs['tvg-logo'] = NGINX_PROXY_PREFIX_NORM + logo_path
                # 构建属性字符串
                attr_parts = ['#EXTINF:-1']
                for key, value in attrs.items():
                    attr_parts.append(f'{key}="{value}"')
                extinf_line = ' '.join(attr_parts) + f',{title}'
                content.append(extinf_line)
                # 添加额外的属性行（如 #EXTVLCOPT, #KODIPROP 等）
                for extra_line in ext_ch.get('extra_lines', []):
                    content.append(extra_line)
                # 处理外部频道 URL，应用代理（如果需要）
                external_url = ext_ch['url']
                if use_proxy_for_this_file and external_url:
                    if external_url.startswith('http://'):
                        url_path = external_url[7:]
                    elif external_url.startswith('https://'):
                        url_path = external_url[8:]
                    else:
                        url_path = external_url
                    if url_path.startswith('/'):
                        url_path = url_path[1:]
                    external_url = NGINX_PROXY_PREFIX_NORM + url_path
                content.append(external_url)

        # 输出本地频道
        for ch in grouped_channels.get(group, []):
            # 跳过没有播放链接的频道
            if not ch.get("zteurl"):
                continue

            # URL 替换逻辑
            if is_tv_m3u and REPLACEMENT_IP_TV_NORM:
                # tv.m3u 使用专用的 REPLACEMENT_IP_TV
                current_prefix = REPLACEMENT_IP_TV_NORM
                if current_prefix.endswith('=/'):
                    current_prefix = current_prefix[:-1]
                original_url = ch["zteurl"]
                parsed_original = urlparse(original_url)
                if parsed_original.scheme in ["rtp", "rtsp", "http", "https"]:
                    address_part = parsed_original.netloc + parsed_original.path
                    url = current_prefix + address_part
                elif not parsed_original.scheme:
                    url = current_prefix + original_url
                else:
                    url = original_url
            elif replace_url:
                original_url = ch["zteurl"]
                parsed_original = urlparse(original_url)
                if parsed_original.scheme in ["rtp", "rtsp", "http", "https"]:
                    address_part = parsed_original.netloc + parsed_original.path
                    url = urljoin(REPLACEMENT_IP_NORM, address_part)
                elif not parsed_original.scheme:
                    url = urljoin(REPLACEMENT_IP_NORM, original_url)
                else:
                    url = original_url
            else:
                url = ch["zteurl"]

            # 图标 URL 处理（应用代理）
            logo_url = ch.get("icon", "")
            if logo_url:
                if use_proxy_for_this_file:
                    if logo_url.startswith('http://'):
                        logo_path = logo_url[7:]
                    elif logo_url.startswith('https://'):
                        logo_path = logo_url[8:]
                    else:
                        logo_path = logo_url
                    if logo_path.startswith('/'):
                        logo_path = logo_path[1:]
                    logo_url = NGINX_PROXY_PREFIX_NORM + logo_path
                else:
                    logo_url = ensure_url_scheme(logo_url)

            # 清理 tvg-id
            cleaned_tvg_id = clean_tvg_id(ch.get("original_title", ch["title"]))

            # 构建 EXTINF 行
            extinf_parts = [
                f'#EXTINF:-1 tvg-id="{cleaned_tvg_id}"',
                f'tvg-name="{ch.get("original_title", ch["title"])}"',
                f'tvg-logo="{logo_url}"'
            ]
            if is_tv_m3u:
                ztecode = ch.get("ztecode", "")
                if ztecode:
                    extinf_parts.append(f'ztecode="{ztecode}"')
            if ch.get("supports_catchup", False):
                ztecode = ch.get("ztecode", "")
                if ztecode:
                    catchup_source = catchup_template.format(
                        prefix=final_catchup_prefix,
                        ztecode=ztecode
                    )
                    catchup_source = ensure_url_scheme(catchup_source)
                    extinf_parts.append(f'catchup="default"')
                    extinf_parts.append(f'catchup-source="{catchup_source}"')
                    catchup_enabled_count += 1
                elif ch.get("is_custom", False):
                    print(f"提示: 自定义频道 '{ch['title']}' 标记为支持回看但缺少 'ztecode'。")
            extinf_parts.append(f'group-title="{group}",{ch["title"]}')
            content.append(' '.join(extinf_parts))
            content.append(url)

    # 输出不在 GROUP_OUTPUT_ORDER 中的外部频道（按分组字母序，组内应用排序）
    if external_groups_not_in_order:
        for group_title in sorted(external_groups_not_in_order):
            if group_title in external_channels_by_group:
                order_list = channel_order.get(group_title, [])
                sorted_ext_channels = sort_external_channels(external_channels_by_group[group_title], order_list)
                for ext_ch in sorted_ext_channels:
                    # 动态构建 EXTINF 行（同上）
                    attrs = ext_ch.get('attributes', {}).copy()
                    title = ext_ch.get('title', '')
                    if use_proxy_for_this_file and 'tvg-logo' in attrs and attrs['tvg-logo']:
                        logo = attrs['tvg-logo']
                        if logo.startswith('http://'):
                            logo_path = logo[7:]
                        elif logo.startswith('https://'):
                            logo_path = logo[8:]
                        else:
                            logo_path = logo
                        if logo_path.startswith('/'):
                            logo_path = logo_path[1:]
                        attrs['tvg-logo'] = NGINX_PROXY_PREFIX_NORM + logo_path
                    attr_parts = ['#EXTINF:-1']
                    for key, value in attrs.items():
                        attr_parts.append(f'{key}="{value}"')
                    extinf_line = ' '.join(attr_parts) + f',{title}'
                    content.append(extinf_line)
                    for extra_line in ext_ch.get('extra_lines', []):
                        content.append(extra_line)
                    external_url = ext_ch['url']
                    if use_proxy_for_this_file and external_url:
                        if external_url.startswith('http://'):
                            url_path = external_url[7:]
                        elif external_url.startswith('https://'):
                            url_path = external_url[8:]
                        else:
                            url_path = external_url
                        if url_path.startswith('/'):
                            url_path = url_path[1:]
                        external_url = NGINX_PROXY_PREFIX_NORM + url_path
                    content.append(external_url)

    print(f"已为 {catchup_enabled_count} 个支持回看的频道添加catchup属性")
    return '\n'.join(content)


def main():
    # 先加载外部配置，确保后续流程使用最终配置值
    initialize_configuration()

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

    grouped_channels = {group: [] for group in GROUP_DEFINITIONS.keys()}

    skipped_url_count = 0 # 用于统计跳过的频道
    
    # 统计计数器
    stats_zte_count = 0
    stats_hw_count = 0

    print("\n正在处理频道 URL...")
    for channel in kept_channels:
        category = categorize_channel(channel["title"])
        
        # 检查频道是否支持回看功能
        supports_catchup = (channel.get("timeshiftAvailable", "false") == "true" or 
                           channel.get("lookbackAvailable", "false") == "true")
        
        # 使用最终名称
        final_name = channel.get("final_name", channel["title"])
        
        # --- 修改：根据 IS_HWURL 开关决定提取 hwurl 还是 zteurl ---
        params = channel.get("params", {})
        
        # 提取原始值
        raw_zteurl = params.get("zteurl", "")
        raw_hwurl = params.get("hwurl", "")
        # ztecode 始终提取 ztecode (全局要求)
        final_code = params.get("ztecode", "")
        
        final_url = ""
        url_source_type = "" # 记录来源类型

        if IS_HWURL:
            # 优先使用 Huawei URL，如果不存在则回退到 ZTE
            if raw_hwurl:
                final_url = raw_hwurl
                url_source_type = "HWURL"
            elif raw_zteurl:
                final_url = raw_zteurl
                url_source_type = "ZTEURL"
        else:
            # 默认逻辑：优先使用 ZTE URL
            if raw_zteurl:
                final_url = raw_zteurl
                url_source_type = "ZTEURL"
            elif raw_hwurl:
                final_url = raw_hwurl
                url_source_type = "HWURL"
        
        # 如果最终URL无效，则跳过此频道
        if not final_url:
            skipped_url_count += 1
            print(f"  [跳过] {final_name} - 无有效播放链接")
            continue
        
        # 统计
        if url_source_type == "ZTEURL":
            stats_zte_count += 1
        elif url_source_type == "HWURL":
            stats_hw_count += 1
            
        # 【修改点】打印当前频道使用的 URL 类型
        print(f"  [{url_source_type}] {final_name}")
        
        grouped_channels[category].append({
            "title": final_name,
            "original_title": channel["title"],
            "code": channel["code"],
            "ztecode": final_code,  # 始终使用 ztecode
            "icon": channel["icon"],
            "zteurl": final_url,    # 可能是 zteurl 也可能是 hwurl，取决于开关
            "number": extract_number(final_name),
            "supports_catchup": supports_catchup,
            "is_custom": False,
            "url_source": url_source_type # 存储来源，以便写入日志
        })
        
    print(f"URL 处理完成: 使用 ZTEURL {stats_zte_count} 个, 使用 HWURL {stats_hw_count} 个")

    # 添加自定义频道并获取黑名单信息
    grouped_channels, blacklisted_custom_channels, added_custom_channels = add_custom_channels(grouped_channels, custom_channels_config)
    
    #  合并所有黑名单频道（外部黑名单会在后面添加）
    all_blacklisted_channels = blacklisted_main_channels + blacklisted_custom_channels
    
    # 应用自定义排序
    grouped_channels = apply_custom_sorting(grouped_channels, channel_order)
    
    # 对于没有自定义排序的组，使用默认排序
    for category in grouped_channels:
        if category not in channel_order:
            grouped_channels[category].sort(key=lambda x: (x["number"], x["title"]))

    if isinstance(EXTERNAL_GROUP_TITLES, list):
        target_groups_raw = EXTERNAL_GROUP_TITLES
        group_name_mapping = {}
    elif isinstance(EXTERNAL_GROUP_TITLES, dict):
        target_groups_raw = list(EXTERNAL_GROUP_TITLES.keys())
        group_name_mapping = EXTERNAL_GROUP_TITLES
    else:
        target_groups_raw = []
        group_name_mapping = {}
        print("警告: EXTERNAL_GROUP_TITLES 配置格式错误，应为列表或字典")

    # 下载并解析外部 M3U（如果启用合并）
    external_channels = None
    blacklisted_external_channels = []
    if ENABLE_EXTERNAL_M3U_MERGE and EXTERNAL_M3U_URL and target_groups_raw:
        print(f"\n开始处理外部 M3U 合并...")
        external_m3u_content = download_external_m3u(EXTERNAL_M3U_URL, EXTERNAL_M3U_CACHE_FILE)
        if external_m3u_content:
            external_channels, blacklisted_external_channels = parse_m3u_content(external_m3u_content, target_groups_raw)
            
            # 应用分组名映射
            if external_channels and group_name_mapping:
                mapped_groups = set()
                for ch in external_channels:
                    original_group = ch['group_title']
                    if original_group in group_name_mapping:
                        new_group = group_name_mapping[original_group]
                        ch['group_title'] = new_group
                        ch['attributes']['group-title'] = new_group
                        mapped_groups.add(original_group)
                if mapped_groups:
                    mapping_desc = ', '.join([f"'{old}' -> '{group_name_mapping[old]}'" for old in mapped_groups])
                    print(f"  映射外部频道分组: {mapping_desc}")
            
            if external_channels:
                print(f"成功提取 {len(external_channels)} 个外部频道，将合并到所有 M3U 文件")
                # 检查外部分组是否在 GROUP_OUTPUT_ORDER 中
                for external_group in set(ch['group_title'] for ch in external_channels):
                    if external_group in GROUP_OUTPUT_ORDER:
                        print(f"  外部分组 '{external_group}' 已在输出顺序中，将按顺序输出")
                    else:
                        print(f"  外部分组 '{external_group}' 不在输出顺序中，将添加到 M3U 文件末尾")
            else:
                print(f"警告: 从外部 M3U 中未找到任何匹配的分组频道（目标分组: {target_groups_raw}）")
        else:
            print(f"警告: 无法下载外部 M3U 文件，跳过外部频道合并")
    
    # 合并外部黑名单频道到总黑名单
    all_blacklisted_channels = blacklisted_main_channels + blacklisted_custom_channels + blacklisted_external_channels

    #  生成M3U文件 - 现在生成四个文件（包含新增的 APTV）
    for filename, replace_url, catchup_template, is_tv_m3u in [
        (TV_M3U_FILENAME, False, CATCHUP_URL_KU9, True),      # 组播地址，标准回看模板，tv.m3u 特殊处理
        (TV2_M3U_FILENAME, True, CATCHUP_URL_TEMPLATE, False),     # 单播地址，标准回看模板
        (KU9_M3U_FILENAME, True, CATCHUP_URL_KU9, False),          # 单播地址，KU9回看模板
        (APTV_M3U_FILENAME, True, CATCHUP_URL_APTV, False)         # 单播地址，APTV回看模板 (新增)
    ]:
        content = generate_m3u_content(grouped_channels, replace_url, catchup_template, external_channels, is_tv_m3u, channel_order)
        ensure_parent_directory(filename)
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        external_count = len(external_channels) if external_channels else 0
        if external_count > 0:
            print(f"已生成M3U文件: {filename} (包含 {external_count} 个外部频道)")
        else:
            print(f"已生成M3U文件: {filename}")

    total_channels = sum(len(v) for v in grouped_channels.values())
    external_count = len(external_channels) if external_channels else 0
    total_channels_with_external = total_channels + external_count
    
    #  更新统计输出
    print(f"\n已跳过 {skipped_url_count} 个缺少播放链接的频道。")
    blacklist_info_parts = [f"主JSON: {len(blacklisted_main_channels)}", f"自定义: {len(blacklisted_custom_channels)}"]
    if blacklisted_external_channels:
        blacklist_info_parts.append(f"外部: {len(blacklisted_external_channels)}")
    print(f"总共过滤 {len(all_blacklisted_channels)} 个黑名单频道（{', '.join(blacklist_info_parts)}）")
    
    if external_count > 0:
        print(f"成功生成 {total_channels} 个本地频道 + {external_count} 个外部频道 = 总计 {total_channels_with_external} 个频道")
    else:
        print(f"成功生成 {total_channels} 个频道")
    print(f"单播地址列表: {os.path.abspath(TV2_M3U_FILENAME)}")
    print(f"KU9回看参数列表: {os.path.abspath(KU9_M3U_FILENAME)}") 
    print(f"APTV回看参数列表: {os.path.abspath(APTV_M3U_FILENAME)}") # 新增输出信息
    
    # 统一生成完整的日志文件，包含主JSON和自定义频道的所有处理结果
    ensure_parent_directory(CHANNEL_PROCESSING_LOG)
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
            
            # 【修改点】在日志中也记录使用的 URL 类型
            # 需要从 grouped_channels 中反查该频道的 url_source，稍微麻烦点，这里简化处理
            # 更好的方式是在 kept_channels 阶段就处理，但这里我们遍历 grouped_channels 来找
            source_info = ""
            for group in grouped_channels.values():
                for c in group:
                    if c["original_title"] == original_name:
                         # 检查是否是主列表频道
                        if not c.get("is_custom", False):
                            source_info = f" [{c.get('url_source', 'UNKNOWN')}]"
                            break
                if source_info: break
            
            if original_name != final_name:
                f.write(f"  - {original_name} -> {final_name}{source_info}\n")
            else:
                f.write(f"  - {original_name}{source_info}\n")
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
                # 获取 URL 来源信息
                source_info = f" [{channel.get('url_source', 'UNKNOWN')}]"
                
                if original_name != final_name:
                    f.write(f"  - [{group_name}] {original_name} -> {final_name}{source_info}\n")
                else:
                    f.write(f"  - [{group_name}] {final_name}{source_info}\n")
        else:
            f.write("  (无)\n")
        f.write("\n\n")
        
        # ========== 外部频道处理结果 ==========
        if ENABLE_EXTERNAL_M3U_MERGE:
            f.write("【外部 M3U 频道处理结果】\n")
            f.write(f"{LOG_SEPARATOR}\n\n")
            
            f.write(f"1. 黑名单过滤 ({len(blacklisted_external_channels)} 个):\n")
            if blacklisted_external_channels:
                for channel in blacklisted_external_channels:
                    f.write(f"  - 标题: {channel['title']}, 原因: {channel['reason']}\n")
            else:
                f.write("  (无)\n")
            f.write("\n")
            
            f.write(f"2. 成功合并 ({external_count} 个):\n")
            if external_channels:
                for channel in external_channels:
                    f.write(f"  - [{channel.get('group_title', '未知分组')}] {channel['title']}\n")
            else:
                f.write("  (无)\n")
            f.write("\n\n")
        
        # ========== 汇总信息 ==========
        f.write("【处理汇总】\n")
        f.write(f"{LOG_SEPARATOR}\n\n")
        f.write(f"URL 提取统计:\n")
        f.write(f"  - 使用 ZTEURL: {stats_zte_count} 个\n")
        f.write(f"  - 使用 HWURL:  {stats_hw_count} 个\n")
        f.write("\n")
        f.write(f"黑名单过滤汇总:\n")
        f.write(f"  - 主JSON频道: {len(blacklisted_main_channels)} 个\n")
        f.write(f"  - 自定义频道: {len(blacklisted_custom_channels)} 个\n")
        if blacklisted_external_channels:
            f.write(f"  - 外部频道: {len(blacklisted_external_channels)} 个\n")
        f.write(f"  - 总计: {len(all_blacklisted_channels)} 个\n")
        f.write("\n")
        f.write(f"最终频道统计:\n")
        f.write(f"  - 主JSON保留: {len(kept_channels)} 个\n")
        f.write(f"  - 自定义频道: {len(added_custom_channels)} 个\n")
        if external_count > 0:
            f.write(f"  - 外部频道: {external_count} 个\n")
            f.write(f"  - 总计: {total_channels_with_external} 个\n")
        else:
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