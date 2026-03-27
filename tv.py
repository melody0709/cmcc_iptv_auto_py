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
import random
import asyncio
from urllib.parse import urljoin, urlparse
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

# 将子目录加入 Python 搜索路径,尝试导入同目录下的 checker 模块
checker_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'iptv_checker_v3')
if checker_dir not in sys.path:
    sys.path.append(checker_dir)

try:
    from iptv_checker_v3 import IPTVCheckerFinal
    HAS_CHECKER = True
except ImportError:
    print("警告: 未在 iptv_checker_v3 目录下找到 iptv_checker_v3.py，智能检测功能将被禁用。")
    HAS_CHECKER = False

# 设置标准输出编码
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ===================== 自定义配置区域 =====================

# 是否优先使用 hwurl (Huawei)
# True  = 优先提取 hwurl (如果 hwurl 为空则回退到 zteurl)
# False = 优先提取 zteurl (默认，如果 zteurl 为空则回退到 hwurl)
# 注意：无论此开关如何，回看代码(ztecode)始终使用 params["ztecode"]
IS_HWURL = True  

# === EPG 下载重试与防风控配置（核心提速与稳定配置） ===
EPG_DOWNLOAD_RETRY_COUNT = 3  # 重试次数
EPG_DOWNLOAD_RETRY_DELAY = 2  # 重试间隔（秒）
EPG_DOWNLOAD_TIMEOUT = 15     # 单个请求超时时间（秒）
# 防风控延迟配置 (保护服务器和本地IP，强烈建议保留默认值)
EPG_REQUEST_DELAY = 0.3       # 每次请求后的基础等待时间（秒），设为 0 则不等待
EPG_RANDOM_DELAY = True       # 是否引入随机波动（模拟人类操作，例如 0.2 秒会随机变为 0.1~0.3 秒，避免被识别为爬虫）
MAX_CONCURRENT_DOWNLOADS = 4  # EPG 最大并发下载线程数（配合 delay 可将 QPS 压在安全线内，避免触发 403 拦截）

# 输出文件名配置
TV_M3U_FILENAME = "tv.m3u"        # 组播地址列表文件
TV2_M3U_FILENAME = "tv2.m3u"      # 转单播地址列表文件
KU9_M3U_FILENAME = "ku9.m3u"      # KU9回看参数格式文件
APTV_M3U_FILENAME = "aptv.m3u"    # APTV回看参数格式文件 (新增)
XML_FILENAME = "t.xml"            # XML节目单文件

# 播放代理与替换配置
REPLACEMENT_IP = "http://c.cc.top:7088/udp"  # UDPXY地址
REPLACEMENT_IP_TV = ""            # tv.m3u 专用的 UDPXY 地址（默认为空，使用原始地址）
CATCHUP_SOURCE_PREFIX = "http://183.235.162.80:6610/190000002005"  # 回看源前缀
NGINX_PROXY_PREFIX = ""           # 针对外网播放的nginx代理
ENABLE_NGINX_PROXY_FOR_TV = False # tv.m3u 是否使用 NGINX_PROXY_PREFIX 代理（默认 False）
JSON_URL = "http://183.235.16.92:8082/epg/api/custom/getAllChannel.json" # JSON 文件下载 URL

# EPG 地址配置 - 可自定义修改
M3U_EPG_URL = "https://gitee.com/taksssss/tv/raw/main/epg/51zmte1.xml.gz"  # 请修改为你的实际 EPG 地址
EPG_BASE_URLS = [
    "http://183.235.16.92:8082/epg/api/channel/",
    "http://183.235.11.39:8082/epg/api/channel/"
]
# EPG 下载日期偏移（相对于今天，单位：天）
DEFAULT_EPG_DAY_OFFSETS = [-5, -4, -3, -2, -1, 0, 1]
EPG_DAY_OFFSETS = DEFAULT_EPG_DAY_OFFSETS.copy()

# 回看参数配置 - 可自定义修改
CATCHUP_URL_TEMPLATE = "{prefix}/{ztecode}/index.m3u8?starttime=${{utc:yyyyMMddHHmmss}}&endtime=${{utcend:yyyyMMddHHmmss}}"
CATCHUP_URL_KU9 = "{prefix}/{ztecode}/index.m3u8?starttime=${{(b)yyyyMMddHHmmss|UTC}}&endtime=${{(e)yyyyMMddHHmmss|UTC}}"
CATCHUP_URL_APTV = "{prefix}/{ztecode}/index.m3u8?starttime=${{(b)yyyyMMddHHmmss:utc}}&endtime=${{(e)yyyyMMddHHmmss:utc}}"

# 自定义配置文件
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
COMMON_CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
USER_CONFIG_FILE = os.path.join(CONFIG_DIR, "myconfig.json")
CHANNEL_ORDER_FILE = os.path.join(CONFIG_DIR, "channel_order.json")        # 频道排序文件
CUSTOM_CHANNELS_FILE = os.path.join(CONFIG_DIR, "custom_channels.json")    # 自定义频道文件

# 外部 M3U 合并配置
EXTERNAL_M3U_URL = "https://raw.githubusercontent.com/Jsnzkpg/Jsnzkpg/Jsnzkpg/Jsnzkpg1.m3u"  
# 要提取的 group-title 列表，例如: ["🔮[主用]港澳台直播", "港澳台"],部分粤语频道播放需要科学上网
# 支持列表格式 ["A", "B"]，也支持频道盖帽映射的字典格式 {"A":"新A", "B":"新B"}
EXTERNAL_GROUP_TITLES = {
    "🔮[主用]港澳台直播": "港澳台"
}
ENABLE_EXTERNAL_M3U_MERGE = True  # 是否合并外部 M3U 到所有 M3U 文件 (True/False)
CACHE_M3U_FILENAME = "cache.m3u"  # 外部 M3U 下载缓存文件名

# ===================== 智能测活与质量探测配置 =====================
ENABLE_STREAM_CHECK = True         # 是否启用 iptv_checker_v3 对频道进行存活与质量检测
CHECK_TARGET_GROUPS = ["港澳台"]    # 需要被检测的分组名称列表 (空列表 [] 则检测所有组)
CHECK_TIMEOUT = 5                  # 单个频道探测超时时间(秒)
CHECK_WORKERS = 4                  # 并发检测线程数 (群晖建议 4-8)
ENABLE_PROBE = True                # 是否启用 ffprobe 进行 1080P/4K 画质深度探测
CHECK_CACHE_EXPIRE = 24            # ffprobe 画质探测缓存过期时间(小时)

# 扩展黑名单配置
BLACKLIST_RULES = {
    "title": [
        "测试频道", "CGTN 西班牙语", "CGTN 法语", "CGTN 阿拉伯语", "CGTN 俄语",
        "购物", "导视", "百视通", "指南", "精选频道", "移动咪咕五大联赛4K", "咪咕五大联赛4K"
    ],
    "code": ["02000000000000050000000000000148"],
    # 支持模糊匹配/域名拦截，只要链接包含以下字符串即被拦截
    "zteurl": [
        "https://cdn6.163189.xyz/163189/fct4k",
        "https://cdn.163189.xyz/163189/viu"
        # "cdn6.163189.xyz"  # <--- 在这里添加你需要拦截的域名、IP或关键词
    ]
}

# 🚀 性能优化：预编译集合与正则（运行时根据配置刷新）
BLACKLIST_TITLE_SET = set()
BLACKLIST_CODE_SET = set()
BLACKLIST_ZTEURL_SET = set()
BLACKLIST_TITLE_PATTERN = None  # 编译后的正则对象，极大提升黑名单检索速度
BLACKLIST_URL_PATTERN = None    # 编译后的URL正则对象，支持URL关键词/域名拦截

# 频道名称映射（将高清频道映射到标准名称）
CHANNEL_NAME_MAP = {
    "CCTV-1高清": "CCTV-1综合", "CCTV-2高清": "CCTV-2财经", "CCTV-3高清": "CCTV-3综艺",
    "CCTV-4高清": "CCTV-4中文国际", "CCTV-5高清": "CCTV-5体育", "CCTV-6高清": "CCTV-6电影",
    "CCTV-7高清": "CCTV-7国防军事", "CCTV-8高清": "CCTV-8电视剧", "CCTV-9高清": "CCTV-9纪录",
    "CCTV-10高清": "CCTV-10科教", "CCTV-11高清": "CCTV-11戏曲", "CCTV-12高清": "CCTV-12社会与法",
    "CCTV-13高清": "CCTV-13新闻", "CCTV-14高清": "CCTV-14少儿高清", "CCTV-15高清": "CCTV-15音乐",
    "CCTV-16高清": "CCTV-16奥林匹克", "CCTV-17高清": "CCTV-17农业高清",
    "广州新闻-测试": "广州新闻高清", "广州综合-测试": "广州综合高清"
}

# EPG 下载开关与模式
ENABLE_EPG_DOWNLOAD = True  
EPG_DOWNLOAD_MODE = "M3U_ONLY"  
XML_SKIP_CHANNELS_WITHOUT_EPG = True 

# 分组定义与优先级顺序
GROUP_DEFINITIONS = {
    "央视": ["CCTV"],
    "央视特色": ["兵器科技", "风云", "第一剧场", "世界地理", "央视", "卫生健康", "怀旧", "女性", "高尔夫", "金鹰纪实", "CGTN"],
    "广东": ["广东", "大湾区", "经济科教", "南方", "岭南", "现代教育", "移动频道"],
    "卫视": ["卫视"],
    "少儿": ["少儿", "卡通", "动画", "教育"],
    "华数咪咕": ["爱", "睛彩", "IPTV", "咪咕", "热播", "经典", "魅力"],
    "超高清4k": ["4k", "4K"],
    "广东地方台": [],
    "其他": []
}
# 2. 定义分类逻辑的 *优先级* (e.g., "少儿" 必须在 "央视" 之前) 这里的顺序决定一个频道被分到哪个组
GROUP_CLASSIFICATION_PRIORITY = ["少儿", "超高清4k", "央视", "央视特色", "广东", "卫视", "华数咪咕"]
# 3. 定义 M3U 和 XML 文件中的 *输出顺序* (你可以随意排列这里的顺序，"少儿" 重排序)
GROUP_OUTPUT_ORDER = ["央视", "港澳台", "广东", "央视特色", "少儿", "卫视", "华数咪咕", "超高清4k", "其他", "广东地方台"]

# 自动生成的压缩文件名
XML_GZ_FILENAME = XML_FILENAME + ".gz"

# 🚀 预编译正则表达式常量
CCTV_PATTERN = re.compile(r'CCTV-(\d+)')  
NUMBER_PATTERN = re.compile(r'\d+')  
QUALITY_PATTERN = re.compile(r'(?:高清|超清|4K|\d+K)')  
TVG_ID_CLEAN_PATTERN = re.compile(r'[_\s]*(高清|超清|4K)[_\s]*')  
SPACE_DASH_PATTERN = re.compile(r'\s+-\s+')  
MULTI_SPACE_PATTERN = re.compile(r'\s+')  

# 魔法字符串和常量
TIMEZONE_OFFSET = "+0800"  
DATE_FORMAT = "%Y%m%d"  
XML_GENERATOR_NAME = "Custom EPG Generator"  
LOG_SEPARATOR = "=" * 50  
UNKNOWN_CHANNEL = "Unknown"  
UNKNOWN_PROGRAMME = "Unknown Programme"  
LOG_DIR = os.path.join(BASE_DIR, "log")
CHANNEL_PROCESSING_LOG = os.path.join(LOG_DIR, "channel_processing.log")  
EPG_STATISTICS_LOG = os.path.join(LOG_DIR, "epg_statistics.log")  


# ===================== 核心公共辅助函数 =====================
def apply_nginx_proxy(url, proxy_prefix):
    """统一处理 URL Nginx 代理替换，避免代码到处复制"""
    if not proxy_prefix or not url:
        return url
    if url.startswith('http://'): path = url[7:]
    elif url.startswith('https://'): path = url[8:]
    else: path = url
    return proxy_prefix + path.lstrip('/')

def normalize_url(url, trailing_slash='keep'):
    if not url: return url
    if trailing_slash == 'add' and not url.endswith('/'): url += '/'
    elif trailing_slash == 'remove' and url.endswith('/'): url = url.rstrip('/')
    return url

def ensure_url_scheme(url, default_scheme='http'):
    if not url: return url
    url = str(url).strip()
    if not url or '://' in url: return url
    url = url.lstrip('/')
    return f"{default_scheme}://{url}" if url else url

def load_json_config_file(file_path):
    if not os.path.exists(file_path): return {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"读取配置文件失败: {file_path} -> {e}")
        return {}

def normalize_epg_day_offsets(offsets):
    if not isinstance(offsets, list): return DEFAULT_EPG_DAY_OFFSETS.copy()
    normalized = [int(x) for x in offsets if not isinstance(x, bool) and str(x).lstrip('-').isdigit()]
    if not normalized: return DEFAULT_EPG_DAY_OFFSETS.copy()
    if len(normalized) == 1 and normalized[0] > 0:
        return list(range(-(normalized[0] - 2), 2)) if normalized[0] >= 2 else DEFAULT_EPG_DAY_OFFSETS.copy()
    return sorted(set(normalized))

# ===================== 核心优化 1：环境初始化与配置管理 =====================
def load_runtime_config_overrides():
    """读取并合并 config.json 和 myconfig.json，供全局初始化使用"""
    merged = {}
    if common_config := load_json_config_file(COMMON_CONFIG_FILE):
        merged.update(common_config)
    if user_config := load_json_config_file(USER_CONFIG_FILE):
        merged.update(user_config)
    return merged

def initialize_environment():
    """规范化脚本生命周期：统一执行配置读取、覆盖和正则编译，避免隐患"""
    config = load_runtime_config_overrides()
    if isinstance(config, dict):
        for key, value in config.items():
            if key in globals(): globals()[key] = value

    global EPG_DAY_OFFSETS, EPG_BASE_URLS
    global BLACKLIST_TITLE_SET, BLACKLIST_CODE_SET, BLACKLIST_ZTEURL_SET, BLACKLIST_TITLE_PATTERN, BLACKLIST_URL_PATTERN
    global REPLACEMENT_IP_NORM, REPLACEMENT_IP_TV_NORM, CATCHUP_SOURCE_PREFIX_NORM, NGINX_PROXY_PREFIX_NORM
    global EXTERNAL_M3U_CACHE_FILE

    EPG_DAY_OFFSETS = normalize_epg_day_offsets(EPG_DAY_OFFSETS)
    if not isinstance(EPG_BASE_URLS, list):
        EPG_BASE_URLS = ["http://183.235.16.92:8082/epg/api/channel/", "http://183.235.11.39:8082/epg/api/channel/"]
    EPG_BASE_URLS = [normalize_url(u.strip(), 'add') for u in EPG_BASE_URLS if isinstance(u, str)]

    title_vals = BLACKLIST_RULES.get("title", []) if isinstance(BLACKLIST_RULES, dict) else []
    BLACKLIST_TITLE_SET = set(title_vals if isinstance(title_vals, list) else [])
    BLACKLIST_CODE_SET = set(BLACKLIST_RULES.get("code", []) if isinstance(BLACKLIST_RULES, dict) else [])
    BLACKLIST_ZTEURL_SET = set(BLACKLIST_RULES.get("zteurl", []) if isinstance(BLACKLIST_RULES, dict) else [])

    if BLACKLIST_TITLE_SET:
        pattern_str = '|'.join(map(re.escape, sorted(BLACKLIST_TITLE_SET, key=len, reverse=True)))
        BLACKLIST_TITLE_PATTERN = re.compile(pattern_str)
    else:
        BLACKLIST_TITLE_PATTERN = None

    if BLACKLIST_ZTEURL_SET:
        url_pattern_str = '|'.join(map(re.escape, sorted(BLACKLIST_ZTEURL_SET, key=len, reverse=True)))
        BLACKLIST_URL_PATTERN = re.compile(url_pattern_str)
    else:
        BLACKLIST_URL_PATTERN = None

    REPLACEMENT_IP_NORM = normalize_url(str(REPLACEMENT_IP), 'add') if REPLACEMENT_IP else ""
    REPLACEMENT_IP_TV_NORM = normalize_url(str(REPLACEMENT_IP_TV), 'add') if REPLACEMENT_IP_TV else ""
    CATCHUP_SOURCE_PREFIX_NORM = normalize_url(str(CATCHUP_SOURCE_PREFIX), 'remove') if CATCHUP_SOURCE_PREFIX else ""
    NGINX_PROXY_PREFIX_NORM = normalize_url(str(NGINX_PROXY_PREFIX), 'add') if NGINX_PROXY_PREFIX else ""
    EXTERNAL_M3U_CACHE_FILE = os.path.join(BASE_DIR, CACHE_M3U_FILENAME or "cache.m3u")

# ===================== 其它辅助函数 =====================
def clean_tvg_id(title):
    cleaned = TVG_ID_CLEAN_PATTERN.sub('', title)
    return cleaned.replace('-', '').strip() if 'CCTV' in cleaned else cleaned.strip()

def apply_channel_name_mapping(channel, base_name):
    if channel["title"] in CHANNEL_NAME_MAP: return CHANNEL_NAME_MAP[channel["title"]]
    cctv_match = CCTV_PATTERN.search(base_name)
    if cctv_match:
        for key, value in CHANNEL_NAME_MAP.items():
            if f"CCTV-{cctv_match.group(1)}" in key: return value
        return f"CCTV-{cctv_match.group(1)}"
    return channel["title"]

def print_configuration():
    print(f"你的组播转单播UDPXY地址是 {REPLACEMENT_IP_NORM}")
    print(f"tv.m3u 专用UDPXY地址: {REPLACEMENT_IP_TV_NORM or '未配置(使用原始地址)'}")
    print(f"你的回看源前缀是 {CATCHUP_SOURCE_PREFIX_NORM}")
    print(f"你的nginx代理前缀是 {NGINX_PROXY_PREFIX_NORM}")
    print(f"tv.m3u 使用nginx代理: {'是' if ENABLE_NGINX_PROXY_FOR_TV else '否'}")
    print(f"优先提取地址类型: {'HWURL (Huawei)' if IS_HWURL else 'ZTEURL (ZTE)'}")
    print(f"EPG下载开关: {'启用' if ENABLE_EPG_DOWNLOAD else '禁用'}")
    if ENABLE_EPG_DOWNLOAD:
        print(f"EPG下载配置: 重试{EPG_DOWNLOAD_RETRY_COUNT}次, 超时{EPG_DOWNLOAD_TIMEOUT}秒, 基础间隔{EPG_REQUEST_DELAY}秒")
        print(f"EPG下载日期偏移: {EPG_DAY_OFFSETS}")
    print(f"外部M3U合并开关: {'启用' if ENABLE_EXTERNAL_M3U_MERGE else '禁用'}")
    if ENABLE_EXTERNAL_M3U_MERGE:
        print(f"外部M3U地址: {EXTERNAL_M3U_URL}")
        print(f"提取的分组: {', '.join(EXTERNAL_GROUP_TITLES) if EXTERNAL_GROUP_TITLES else '(未配置)'}")
    if ENABLE_STREAM_CHECK:
        print(f"智能检测开关: 启用 (分组: {CHECK_TARGET_GROUPS}, 探针画质: {ENABLE_PROBE})")


# ===================== 核心优化 2：统一排序引擎 =====================
def sort_channels_by_order(channel_list, order_list):
    """全局统一的频道排序方法，支持保留同名备用源，防止覆盖Bug"""
    if not order_list:
        return channel_list
    
    # 字典中用列表存储同名频道，彻底避免后出现的频道覆盖前面的频道
    ch_dict = {}
    for ch in channel_list:
        ch_dict.setdefault(ch['title'], []).append(ch)
        
    sorted_list = []
    processed = set()
    for name in order_list:
        if name in ch_dict:
            sorted_list.extend(ch_dict[name])
            processed.add(name)
            
    # 追加不在排序列表中的剩余频道
    for ch in channel_list:
        if ch["title"] not in processed:
            sorted_list.append(ch)
            
    return sorted_list

def apply_custom_sorting(grouped_channels, channel_order):
    """为所有的本地频道分组应用统一排序"""
    for group_name, channels in grouped_channels.items():
        if group_name in channel_order:
            grouped_channels[group_name] = sort_channels_by_order(channels, channel_order[group_name])
    return grouped_channels

# ===================== 核心优化 3：极度安全的 URL 重构 =====================
def build_playback_url(original_url, is_tv_m3u, replace_url):
    """彻底告别危险的字符串相加，100% 安全拼装播放地址（修复丢失 / 的问题）"""
    if not original_url: 
        return original_url

    # 1. 确定我们要使用的前缀
    if is_tv_m3u and REPLACEMENT_IP_TV_NORM:
        prefix = REPLACEMENT_IP_TV_NORM
    elif replace_url and REPLACEMENT_IP_NORM:
        prefix = REPLACEMENT_IP_NORM
    else:
        return original_url  # 原样输出

    # 2. 提取原始地址的特征部分
    parsed = urlparse(original_url)
    if parsed.scheme in ["rtp", "rtsp", "http", "https"]:
        address_part = parsed.netloc + parsed.path
    elif not parsed.scheme:
        address_part = original_url
    else:
        return original_url
        
    # 3. 完美兼容用户原始的截断逻辑（去除末尾的 =/）
    if prefix.endswith('=/'):
        prefix = prefix[:-1]
        
    # 4. 纯净拼接返回
    return prefix + address_part


# 支持 Session 长连接注入
def download_with_retry(url, max_retries=EPG_DOWNLOAD_RETRY_COUNT, timeout=EPG_DOWNLOAD_TIMEOUT, headers=None, session=None):
    req_method = session.get if session else requests.get
    for attempt in range(max_retries):
        try:
            response = req_method(url, timeout=timeout, headers=headers)
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                print(f"  下载时发生 '{type(e).__name__}' 错误，{EPG_DOWNLOAD_RETRY_DELAY}秒后重试...")
                time.sleep(EPG_DOWNLOAD_RETRY_DELAY)
            else:
                raise
    return None

def download_json_data(url):
    try:
        data = download_with_retry(url).json()
        print(f"成功获取 JSON 数据从 {url}")
        return data
    except Exception as e:
        print(f"获取 JSON 数据失败: {e}")
        return None

def categorize_channel(title):
    for group_name in GROUP_CLASSIFICATION_PRIORITY:
        for keyword in GROUP_DEFINITIONS.get(group_name, []):
            if keyword in title: return group_name 
    return "其他"

def extract_number(title):
    match = NUMBER_PATTERN.search(title)
    return int(match.group()) if match else 0

# ===================== 核心优化 4：防御性黑名单校验 =====================
def is_blacklisted(channel):
    """极速正则校验，并加入防御性 params.get 容错，绝不抛错退出"""
    title = channel.get("title", "")
    if BLACKLIST_TITLE_PATTERN and BLACKLIST_TITLE_PATTERN.search(title): 
        return True
        
    if channel.get("code", "") in BLACKLIST_CODE_SET: 
        return True
        
    # 安全提取 params (防止源数据中 params 意外为 None)
    params = channel.get("params") or {}
    zteurl = channel.get("zteurl", "") or params.get("zteurl", "") or params.get("hwurl", "")
    
    # 支持模糊匹配和域名的 URL 黑名单检测
    if BLACKLIST_URL_PATTERN and zteurl and BLACKLIST_URL_PATTERN.search(zteurl): 
        return True
        
    return False

def get_channel_base_name(title):
    if "CCTV" in title and (cctv_match := CCTV_PATTERN.search(title)):
        return f"CCTV-{cctv_match.group(1)}"
    base_name = QUALITY_PATTERN.sub('', title)
    return MULTI_SPACE_PATTERN.sub(' ', SPACE_DASH_PATTERN.sub('', base_name)).strip('- ')

def get_channel_quality(title):
    return "超清" if "超清" in title or "4K" in title.upper() else "高清" if "高清" in title else "标清"

def is_cctv_channel(title):
    return "CCTV" in title

def process_channels(channels):
    filtered_channels, blacklisted_channels, removed_channels = [], [], []
    for channel in channels:
        if is_blacklisted(channel):
            blacklisted_channels.append({"title": channel["title"], "code": channel.get("code", ""), "reason": "黑名单匹配", "source": "主JSON"})
        else:
            filtered_channels.append(channel)
    
    print(f"已过滤 {len(blacklisted_channels)} 个黑名单频道（主JSON）")
    
    channel_groups = {}
    for channel in filtered_channels:
        channel_groups.setdefault(get_channel_base_name(channel["title"]), []).append(channel)
    
    kept_channels = []
    for base_name, group in channel_groups.items():
        if len(group) == 1:
            group[0]["final_name"] = CHANNEL_NAME_MAP.get(group[0]["title"], group[0]["title"])
            kept_channels.append(group[0])
            continue
        
        is_cctv = any(is_cctv_channel(ch["title"]) for ch in group)
        hd_channels = [ch for ch in group if get_channel_quality(ch["title"]) == "高清"]
        uhd_channels = [ch for ch in group if get_channel_quality(ch["title"]) == "超清"]
        
        target_group = uhd_channels if uhd_channels else hd_channels if hd_channels else group if not is_cctv else group
        if not is_cctv and not target_group: target_group = group 

        for ch in target_group:
            ch["final_name"] = apply_channel_name_mapping(ch, base_name) if target_group != group or is_cctv else ch["title"]
            kept_channels.append(ch)
            
        for ch in group:
            if ch not in target_group:
                removed_channels.append({"name": ch["title"], "reason": "存在更高清或优选版本"})
                
    return kept_channels, blacklisted_channels, removed_channels

def load_custom_channels(file_path):
    channels = load_json_config_file(file_path)
    if channels:
        print(f"成功加载自定义频道文件: {file_path}")
    return channels

def load_channel_order(file_path):
    order = load_json_config_file(file_path)
    if order:
        print(f"成功加载频道排序文件: {file_path}")
    return order

def add_custom_channels(grouped_channels, custom_channels):
    blacklisted_custom_channels, added_custom_channels = [], []
    print("\n正在处理自定义频道...")
    
    for group_name, channels in custom_channels.items():
        grouped_channels.setdefault(group_name, [])
        for custom_channel in channels:
            # 初始化 zteurl 便于被黑名单检测时能够提取到
            params = custom_channel.get("params") or {}
            raw_zte = params.get("zteurl", "") or custom_channel.get("zteurl", "")
            raw_hw = params.get("hwurl", "") or custom_channel.get("hwurl", "")
            
            if IS_HWURL:
                temp_url = raw_hw or raw_zte
            else:
                temp_url = raw_zte or raw_hw
            if not temp_url: temp_url = custom_channel.get("url", "")
            custom_channel["zteurl"] = temp_url
            
            if is_blacklisted(custom_channel):
                blacklisted_custom_channels.append({"title": custom_channel.get('title', '未知'), "code": custom_channel.get('code', ''), "reason": "黑名单匹配", "source": "自定义频道"})
                print(f"跳过黑名单中的自定义频道: {custom_channel.get('title', '未知')} ({temp_url})")
                continue
            
            orig_title = custom_channel["title"]
            final_name = CHANNEL_NAME_MAP.get(orig_title, orig_title)
            if orig_title != final_name:
                print(f"自定义频道名称映射: '{orig_title}' -> '{final_name}'")
                
            final_ztecode = params.get("ztecode", "") or custom_channel.get("ztecode", "")
            supports_catchup = custom_channel.get("supports_catchup", False) or params.get("supports_catchup", False)
            
            custom_channel.update({"title": final_name, "original_title": orig_title, "number": extract_number(final_name), "ztecode": final_ztecode, "supports_catchup": supports_catchup, "is_custom": True})
            
            src_type = "HWURL" if (raw_hw and IS_HWURL) or (not raw_zte and raw_hw) else "ZTEURL" if raw_zte else "FALLBACK"
            custom_channel["url_source"] = src_type if temp_url else "UNKNOWN"
            
            if temp_url:
                print(f"  [{custom_channel['url_source']}] {final_name} (自定义)")
            else:
                print(f"  [警告] 自定义频道 {final_name} 未找到有效链接")
            
            grouped_channels[group_name].append(custom_channel)
            added_custom_channels.append({"title": final_name, "original_title": orig_title, "group": group_name, "ztecode": final_ztecode, "url_source": custom_channel["url_source"]})
            
    return grouped_channels, blacklisted_custom_channels, added_custom_channels

def build_epg_download_dates(base_time, day_offsets):
    dates, seen = [], set()
    for offset in day_offsets:
        day = (base_time + timedelta(days=offset)).strftime(DATE_FORMAT)
        if day not in seen:
            seen.add(day)
            dates.append(day)
    return dates

def append_schedules_without_duplicates(target_schedules, source_schedules, schedule_keys):
    for schedule in source_schedules:
        start_val = schedule.get("starttime", schedule.get("startTime", schedule.get("start", "")))
        end_val = schedule.get("endtime", schedule.get("endTime", schedule.get("end", "")))
        title_val = schedule.get("title", schedule.get("name", schedule.get("programme", "")))
        key = (start_val, end_val, title_val)
        if not any(key): key = (str(schedule),)
        if key not in schedule_keys:
            schedule_keys.add(key)
            target_schedules.append(schedule)

# ===================== EPG 防风控下载核心 =====================
def download_epg_for_source(channels, base_url, total_channels, progress_counter, progress_lock):
    schedules_for_source = {}
    download_dates = build_epg_download_dates(datetime.now(), EPG_DAY_OFFSETS)

    with requests.Session() as session:
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Connection': 'keep-alive'
        })

        for channel in channels:
            code = channel["code"]
            urls = [f"{base_url}{code}.json?begintime={d}" for d in download_dates]
            
            for url in urls:
                try:
                    response = download_with_retry(url, session=session)
                    data = response.json()
                    
                    if code not in schedules_for_source:
                        schedules_for_source[code] = {"channel": data.get("channel", {}), "schedules": [], "_schedule_keys": set()}
                    append_schedules_without_duplicates(
                        schedules_for_source[code]["schedules"], data.get("schedules", []), schedules_for_source[code]["_schedule_keys"]
                    )
                except Exception as e:
                    print(f"\n处理 {url} 失败 (线程内): {e}")

                if EPG_REQUEST_DELAY > 0:
                    sleep_time = random.uniform(EPG_REQUEST_DELAY * 0.5, EPG_REQUEST_DELAY * 1.5) if EPG_RANDOM_DELAY else EPG_REQUEST_DELAY
                    time.sleep(sleep_time)
            
            with progress_lock:
                progress_counter[0] += 1
                print(f"  下载进度: {progress_counter[0]}/{total_channels} 个频道 ({(progress_counter[0] / total_channels) * 100:.1f}%)", end="\r", flush=True)

    for code in list(schedules_for_source.keys()):
        schedules_for_source[code].pop("_schedule_keys", None)
    return schedules_for_source

def _download_epg_data_parallel(channels_for_xml):
    all_channels = [ch for group in channels_for_xml.values() for ch in group]
    num_channels, num_sources = len(all_channels), len(EPG_BASE_URLS)
    if num_sources == 0: return {}

    chunk_size = 15
    tasks = []
    for i in range(0, num_channels, chunk_size):
        base_url = EPG_BASE_URLS[(i // chunk_size) % num_sources]
        tasks.append({"channels": all_channels[i:i+chunk_size], "base_url": base_url})

    workers = min(MAX_CONCURRENT_DOWNLOADS, len(tasks))
    print(f"准备并行下载 {num_channels} 个频道的EPG，开启 {workers} 个连接池工作...")

    progress_lock, progress_counter, all_schedules = threading.Lock(), [0], {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download_epg_for_source, t["channels"], t["base_url"], num_channels, progress_counter, progress_lock): t for t in tasks}
        for future in as_completed(futures):
            try: all_schedules.update(future.result())
            except Exception as exc: print(f'\n下载任务异常: {exc}')
    
    print("\n所有下载任务已完成。")
    return all_schedules

def convert_time_to_xmltv_format(time_str):
    try:
        return f"{time_str} {TIMEZONE_OFFSET}"
    except ValueError as e:
        print(f"时间格式转换失败: {time_str}, 错误: {e}")
        return None

def _build_xmltv_tree(channels_for_xml, all_schedules):
    root = ET.Element("tv", {"generator-info-name": XML_GENERATOR_NAME})
    stats = {"channels_in_xml": 0, "channels_with_epg": 0, "total_programmes": 0, "skipped_no_epg": 0, "with_epg_list": [], "without_epg_in_xml_list": [], "without_epg_skipped_list": []}

    for group in GROUP_OUTPUT_ORDER:
        for channel_entry in channels_for_xml.get(group, []):
            code, channel_name = channel_entry["code"], channel_entry["title"]
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
            channel = ET.SubElement(root, "channel", {"id": clean_tvg_id(channel_entry.get("original_title", channel_name))})
            ET.SubElement(channel, "display-name").text = channel_entry.get("original_title", channel_entry.get("title", channel_info.get("title", UNKNOWN_CHANNEL)))

            for schedule in schedules:
                stats["total_programmes"] += 1
                programme = ET.SubElement(root, "programme", {"channel": channel.get("id")})
                if start := convert_time_to_xmltv_format(schedule.get("starttime", "")): programme.set("start", start)
                if end := convert_time_to_xmltv_format(schedule.get("endtime", "")): programme.set("stop", end)
                ET.SubElement(programme, "title", {"lang": "zh"}).text = schedule.get("title", UNKNOWN_PROGRAMME)
    return root, stats

def _write_epg_files_and_stats(root, stats, output_file=XML_FILENAME):
    if hasattr(ET, 'indent'):
        ET.indent(root, space="  ", level=0)
    
    xml_bytes = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    
    with open(output_file, 'wb') as f: f.write(xml_bytes)
    print(f"已保存节目单XML文件到: {os.path.abspath(output_file)}")
    
    with gzip.open(XML_GZ_FILENAME, 'wb') as f_out: f_out.write(xml_bytes)
    print(f"已生成压缩文件: {os.path.abspath(XML_GZ_FILENAME)}")

    print("\n" + LOG_SEPARATOR)
    print("EPG 合成统计")
    print(LOG_SEPARATOR)
    print(f"\n基本统计:")
    print(f"   - XML 中总共写入 {stats['channels_in_xml']} 个频道")
    print(f"   - 其中 {stats['channels_with_epg']} 个频道成功合成了节目数据")
    print(f"   - 总共合成了 {stats['total_programmes']} 个节目条目")
    if XML_SKIP_CHANNELS_WITHOUT_EPG:
        print(f"   - 已跳过 {stats['skipped_no_epg']} 个没有节目数据的频道")

    os.makedirs(LOG_DIR, exist_ok=True)
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
    all_schedules = _download_epg_data_parallel(channels_for_xml)
    xml_tree, stats = _build_xmltv_tree(channels_for_xml, all_schedules)
    _write_epg_files_and_stats(xml_tree, stats, output_file)

def run_epg_download(channels, custom_channels_config, grouped_channels):
    print("\n开始下载节目单...")
    channels_to_write_to_xml = {}      
    
    if EPG_DOWNLOAD_MODE == "M3U_ONLY":
        print("EPG 模式: M3U_ONLY (仅下载和合成 M3U 中的频道)")
        channels_to_write_to_xml = grouped_channels
        m3u_channel_count = sum(len(v) for v in channels_to_write_to_xml.values())
        print(f"总共将为 {m3u_channel_count} 个 M3U 频道条目尝试下载EPG。")
    else:
        print("EPG 模式: ALL (下载所有可用的频道，并全部写入 XML)")
        all_channels = list(channels) + [c for g in custom_channels_config.values() for c in g if 'code' in c]
        for ch in all_channels:
            if "title" in ch and "code" in ch:
                orig, final = ch["title"], CHANNEL_NAME_MAP.get(ch["title"], ch["title"])
                channels_to_write_to_xml.setdefault(categorize_channel(orig), []).append({"title": final, "original_title": orig, "code": ch["code"], "icon": ch.get("icon", "")})
        total_xml_channels = sum(len(v) for v in channels_to_write_to_xml.values())
        print(f"XML 文件将包含 {total_xml_channels} 个频道 (包括被 M3U 过滤的)。")
        
    download_and_save_all_schedules(channels_to_write_to_xml)

def is_valid_m3u_content(content): return bool(content and isinstance(content, str) and content.strip() and ('#EXTM3U' in content or '#EXTINF' in content))
def load_external_m3u_cache(cache_file_path):
    if os.path.exists(cache_file_path):
        with open(cache_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if is_valid_m3u_content(content): return content
    return None
def save_external_m3u_cache(content, cache_file_path):
    if is_valid_m3u_content(content):
        with open(cache_file_path, 'w', encoding='utf-8') as f: f.write(content)

def normalize_external_channel_url(url):
    parsed = urlparse(str(url).strip())
    return parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower()).geturl() if parsed.scheme or parsed.netloc else str(url).strip()

def download_external_m3u(url):
    try:
        print(f"正在下载外部 M3U 文件: {url}")
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = download_with_retry(url, headers=headers)
        if resp and is_valid_m3u_content(resp.text):
            print(f"成功下载外部 M3U 文件，大小: {len(resp.text)} 字节")
            save_external_m3u_cache(resp.text, EXTERNAL_M3U_CACHE_FILE)
            return resp.text, "network"
    except Exception as e: print(f"下载外部 M3U 失败: {e}")
    
    cached = load_external_m3u_cache(EXTERNAL_M3U_CACHE_FILE)
    if cached:
        print("外部 M3U 网络更新失败，已回退到本地缓存")
        return cached, "cache"
    return None, "none"

def parse_m3u_content(m3u_content, target_groups):
    if not m3u_content or not target_groups: return [], [], []
    target_groups_set = set(target_groups)
    channels, blacklisted_external_channels, duplicate_external_channels, seen_urls = [], [], [], set()
    
    current_channel = None
    for line in m3u_content.strip().split('\n'):
        line = line.strip()
        if line.startswith('#EXTINF'):
            current_channel = {'extinf_line': line, 'extra_lines': [], 'attributes': {}, 'url': None, 'title': line.split(',')[-1].strip(), 'group_title': ''}
            for k, v in re.findall(r'(\S+?)="([^"]*)"', line):
                current_channel['attributes'][k] = v
                if k == 'group-title': current_channel['group_title'] = v
        elif line.startswith('#') and current_channel:
            current_channel['extra_lines'].append(line)
        elif line and current_channel:
            current_channel['url'] = line
            if current_channel['group_title'] in target_groups_set:
                if is_blacklisted({'title': current_channel['title'], 'zteurl': current_channel['url']}):
                    blacklisted_external_channels.append({'title': current_channel['title'], 'reason': '黑名单规则匹配', 'source': '外部M3U'})
                elif (norm_url := normalize_external_channel_url(line)) in seen_urls:
                    duplicate_external_channels.append({'title': current_channel['title'], 'group_title': current_channel['group_title'], 'url': line, 'reason': '外部M3U存在同URL不同别名，已保留首次出现的频道'})
                else:
                    seen_urls.add(norm_url)
                    channels.append(current_channel.copy())
            current_channel = None
            
    print(f"从外部 M3U 中提取了 {len(channels)} 个频道 (目标分组: {', '.join(target_groups)})")
    if blacklisted_external_channels:
        print(f"已过滤 {len(blacklisted_external_channels)} 个黑名单外部频道")
    if duplicate_external_channels:
        print(f"已按 URL 去重 {len(duplicate_external_channels)} 个外部频道（保留首次出现）")
    return channels, blacklisted_external_channels, duplicate_external_channels

# ===================== 核心优化 5：消除代码冗余 =====================
def format_external_channel_m3u(ext_ch, use_proxy):
    """纯净抽取：专门负责将外部频道的字典结构组装成标准 M3U 字符串块"""
    lines = []
    attrs = ext_ch.get('attributes', {}).copy()
    if use_proxy and attrs.get('tvg-logo'):
        attrs['tvg-logo'] = apply_nginx_proxy(attrs['tvg-logo'], NGINX_PROXY_PREFIX_NORM)
    
    attr_parts = ['#EXTINF:-1']
    for k, v in attrs.items():
        attr_parts.append(f'{k}="{v}"')
        
    title = ext_ch.get("title", "")
    if ext_ch.get("probe_info"):
        title = f"{title} {ext_ch['probe_info']}"
    
    lines.append(' '.join(attr_parts) + f',{title}')
    lines.extend(ext_ch.get('extra_lines', []))
    
    url = ext_ch.get('url', '')
    lines.append(apply_nginx_proxy(url, NGINX_PROXY_PREFIX_NORM) if use_proxy else url)
    return lines

def generate_m3u_content(grouped_channels, replace_url, catchup_template=CATCHUP_URL_TEMPLATE, external_channels=None, is_tv_m3u=False, channel_order=None):
    channel_order = channel_order or {}
    use_proxy = NGINX_PROXY_PREFIX_NORM and (not is_tv_m3u or ENABLE_NGINX_PROXY_FOR_TV)
    final_catchup_prefix = apply_nginx_proxy(CATCHUP_SOURCE_PREFIX_NORM, NGINX_PROXY_PREFIX_NORM) if use_proxy else CATCHUP_SOURCE_PREFIX_NORM
    
    content = [f'#EXTM3U x-tvg-url="{M3U_EPG_URL}"'] if M3U_EPG_URL else ["#EXTM3U"]
    catchup_enabled_count = 0

    ext_by_group = {}
    if external_channels:
        for ch in external_channels:
            ext_by_group.setdefault(ch.get('group_title', ''), []).append(ch)

    for group in GROUP_OUTPUT_ORDER:
        
        # 处理并输出本组的外部频道
        if group in ext_by_group:
            sorted_ext_channels = sort_channels_by_order(ext_by_group[group], channel_order.get(group, []))
            for ext_ch in sorted_ext_channels:
                content.extend(format_external_channel_m3u(ext_ch, use_proxy))
            del ext_by_group[group]

        # 处理并输出本组的本地频道
        for ch in grouped_channels.get(group, []):
            if not ch.get("zteurl"): continue

            url = build_playback_url(ch["zteurl"], is_tv_m3u, replace_url)
            logo = apply_nginx_proxy(ch.get("icon", ""), NGINX_PROXY_PREFIX_NORM) if use_proxy else ensure_url_scheme(ch.get("icon", ""))
            
            extinf = [
                f'#EXTINF:-1 tvg-id="{clean_tvg_id(ch.get("original_title", ch["title"]))}"', 
                f'tvg-name="{ch.get("original_title", ch["title"])}"', 
                f'tvg-logo="{logo}"'
            ]
            
            if is_tv_m3u and ch.get("ztecode"): 
                extinf.append(f'ztecode="{ch["ztecode"]}"')
                
            if ch.get("supports_catchup") and ch.get("ztecode"):
                catch_url = ensure_url_scheme(catchup_template.format(prefix=final_catchup_prefix, ztecode=ch["ztecode"]))
                extinf.extend(['catchup="default"', f'catchup-source="{catch_url}"'])
                catchup_enabled_count += 1
                
            title = ch.get("final_name", ch["title"])
            if ch.get("probe_info"):
                title = f"{title} {ch['probe_info']}"
                
            extinf.append(f'group-title="{group}",{title}')
            content.append(' '.join(extinf))
            content.append(url)

    # 兜底输出剩余未在主排序列表中的外部频道
    for group, ch_list in sorted(ext_by_group.items()):
        sorted_ext_channels = sort_channels_by_order(ch_list, channel_order.get(group, []))
        for ext_ch in sorted_ext_channels:
            content.extend(format_external_channel_m3u(ext_ch, use_proxy))

    if not is_tv_m3u: 
        print(f"已为 {catchup_enabled_count} 个支持回看的频道添加catchup属性")
        
    return '\n'.join(content)

# ===================== 桥接函数：智能检测 =====================
def run_smart_checker(grouped_channels, external_channels):
    """
    将 tv.py 的复杂数据结构降维，送入 Checker 内存检测，再将结果升维合并。
    """
    print("\n" + LOG_SEPARATOR)
    print("启动底层视频流智能检测引擎 (iptv_checker_v3)")
    print(LOG_SEPARATOR)
    
    if not HAS_CHECKER:
        print("未找到检测组件，跳过测试。")
        return grouped_channels, external_channels

    checker = IPTVCheckerFinal(
        target_group=None,  
        timeout=CHECK_TIMEOUT, 
        workers=CHECK_WORKERS, 
        enable_probe=ENABLE_PROBE, 
        cache_expire_hours=CHECK_CACHE_EXPIRE
    )
    
    # 构建待检测的标准列表
    channels_to_check = []
    
    # 1. 抽取本地主JSON/自定义频道
    for group, ch_list in grouped_channels.items():
        for ch in ch_list:
            url = ch.get("zteurl")
            if not url: continue
            needs_check = not CHECK_TARGET_GROUPS or group in CHECK_TARGET_GROUPS
            channels_to_check.append({
                "name": ch.get("final_name", ch["title"]),
                "url": url,
                "group": group,
                "needs_check": needs_check,
                "is_alive": not needs_check,
                "msg": "",
                "_tv_ref": ch,  # 魔法：保存原始字典的引用，检测完直接改它
                "_is_external": False
            })

    # 2. 抽取外部 M3U 频道
    if external_channels:
        for ch in external_channels:
            url = ch.get("url")
            if not url: continue
            group = ch.get("group_title", "")
            needs_check = not CHECK_TARGET_GROUPS or group in CHECK_TARGET_GROUPS
            channels_to_check.append({
                "name": ch.get("title", "Unknown"),
                "url": url,
                "group": group,
                "needs_check": needs_check,
                "is_alive": not needs_check,
                "msg": "",
                "_tv_ref": ch,
                "_is_external": True
            })
            
    if not channels_to_check:
        print("没有提取到需要检测的频道URL。")
        return grouped_channels, external_channels

    # 异步执行内存检测
    if sys.platform == 'win32': asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    processed_channels = asyncio.run(checker.process_channel_list(channels_to_check))
    
    # 升维回写结果
    dead_count = 0
    for res in processed_channels:
        orig_ch = res["_tv_ref"]
        if not res.get("is_alive", False):
            # 打上死亡标记
            orig_ch["_is_dead"] = True
            dead_count += 1
        elif res.get("probe_info"):
            # 将画质标签存入专门的 probe_info 字段中，不要直接修改 title
            orig_ch["probe_info"] = res["probe_info"]
                
    # 从原始字典中彻底剔除死亡频道
    for group in grouped_channels:
        grouped_channels[group] = [ch for ch in grouped_channels[group] if not ch.get("_is_dead")]
    
    if external_channels:
        # filter 掉死亡的外部频道，在原列表上操作
        external_channels[:] = [ch for ch in external_channels if not ch.get("_is_dead")]
        
    print(f"\n智能检测完毕！共剔除了 {dead_count} 个彻底失效的黑屏源。\n")
    return grouped_channels, external_channels

def main():
    import argparse
    parser = argparse.ArgumentParser(description="广东移动IPTV自动抓取与清洗工具")
    parser.add_argument("-se", "--skip-epg", action="store_true", help="临时禁用 EPG 下载")
    parser.add_argument("-sc", "--skip-check", action="store_true", help="临时禁用所有的底层存活与质量检测")
    parser.add_argument("-sp", "--skip-probe", action="store_true", help="临时禁用 FFprobe 深度画质探测 (仅保留极速测活)")
    args = parser.parse_args()

    # 1. 正常加载所有 JSON 配置文件
    initialize_environment()

    # 2. 根据命令行参数，强行覆盖全局开关
    if args.skip_epg:
        global ENABLE_EPG_DOWNLOAD
        ENABLE_EPG_DOWNLOAD = False
        print("\n[CLI 覆写] 已通过命令行参数强制禁用 EPG 下载！")
        
    if args.skip_check:
        global ENABLE_STREAM_CHECK
        ENABLE_STREAM_CHECK = False
        print("\n[CLI 覆写] 已通过命令行参数强制禁用所有底层视频流检测！")
        
    if args.skip_probe:
        global ENABLE_PROBE
        ENABLE_PROBE = False
        print("\n[CLI 覆写] 已通过命令行参数强制禁用 FFprobe 画质探测！")

    print_configuration()
    
    channel_order = load_channel_order(CHANNEL_ORDER_FILE)
    custom_channels_config = load_custom_channels(CUSTOM_CHANNELS_FILE)
    
    print(f"自定义频道配置: {list(custom_channels_config.keys())}")
    for group_name, channels in custom_channels_config.items():
        print(f"  分组 '{group_name}' 有 {len(channels)} 个频道")
        
    data = download_json_data(JSON_URL)
    if not data: 
        print("程序退出")
        sys.exit(1)

    kept_channels, blacklisted_main_channels, removed_channels = process_channels(data["channels"])
    grouped_channels = {g: [] for g in GROUP_DEFINITIONS.keys()}
    
    skipped_url_count, stats_zte_count, stats_hw_count = 0, 0, 0

    print("\n正在处理频道 URL...")
    for ch in kept_channels:
        params = ch.get("params") or {}
        raw_zte, raw_hw = params.get("zteurl", ""), params.get("hwurl", "")
        
        if IS_HWURL:
            final_url = raw_hw or raw_zte
            src_type = "HWURL" if raw_hw else "ZTEURL" if raw_zte else ""
        else:
            final_url = raw_zte or raw_hw
            src_type = "ZTEURL" if raw_zte else "HWURL" if raw_hw else ""

        final_name = ch.get("final_name", ch["title"])

        if not final_url:
            skipped_url_count += 1
            print(f"  [跳过] {final_name} - 无有效播放链接")
            continue
            
        stats_zte_count += 1 if src_type == "ZTEURL" else 0
        stats_hw_count += 1 if src_type == "HWURL" else 0

        print(f"  [{src_type}] {final_name}")

        grouped_channels[categorize_channel(ch["title"])].append({
            "title": final_name, "original_title": ch["title"], "code": ch["code"], 
            "ztecode": params.get("ztecode", ""), "icon": ch["icon"], "zteurl": final_url, 
            "number": extract_number(final_name), 
            "supports_catchup": ch.get("timeshiftAvailable") == "true" or ch.get("lookbackAvailable") == "true",
            "is_custom": False, "url_source": src_type
        })
        
    print(f"URL 处理完成: 使用 ZTEURL {stats_zte_count} 个, 使用 HWURL {stats_hw_count} 个")

    grouped_channels, blacklisted_custom_channels, added_custom_channels = add_custom_channels(grouped_channels, custom_channels_config)
    grouped_channels = apply_custom_sorting(grouped_channels, channel_order)
    
    for c in grouped_channels:
        if c not in channel_order: grouped_channels[c].sort(key=lambda x: (x["number"], x["title"]))

    external_channels, blacklisted_external_channels, duplicate_external_channels, external_m3u_source = None, [], [], "none"
    
    if ENABLE_EXTERNAL_M3U_MERGE and EXTERNAL_M3U_URL:
        print(f"\n开始处理外部 M3U 合并...")
        target_groups_raw = EXTERNAL_GROUP_TITLES if isinstance(EXTERNAL_GROUP_TITLES, list) else list(EXTERNAL_GROUP_TITLES.keys())
        if target_groups_raw:
            ext_content, external_m3u_source = download_external_m3u(EXTERNAL_M3U_URL)
            if ext_content:
                external_channels, blacklisted_external_channels, duplicate_external_channels = parse_m3u_content(ext_content, target_groups_raw)
                if isinstance(EXTERNAL_GROUP_TITLES, dict):
                    mapped_groups = set()
                    for ch in external_channels:
                        original_group = ch['group_title']
                        if original_group in EXTERNAL_GROUP_TITLES:
                            ch['group_title'] = ch['attributes']['group-title'] = EXTERNAL_GROUP_TITLES[original_group]
                            mapped_groups.add(original_group)
                    if mapped_groups:
                        mapping_desc = ', '.join([f"'{old}' -> '{EXTERNAL_GROUP_TITLES[old]}'" for old in mapped_groups])
                        print(f"  映射外部频道分组: {mapping_desc}")
                
                if external_channels:
                    source_desc = "网络" if external_m3u_source == "network" else "缓存"
                    print(f"成功提取 {len(external_channels)} 个外部频道，将合并到所有 M3U 文件（来源: {source_desc}）")
                    for external_group in set(ch['group_title'] for ch in external_channels):
                        if external_group in GROUP_OUTPUT_ORDER:
                            print(f"  外部分组 '{external_group}' 已在输出顺序中，将按顺序输出")
                        else:
                            print(f"  外部分组 '{external_group}' 不在输出顺序中，将添加到 M3U 文件末尾")
            else:
                print(f"警告: 无法下载外部 M3U 文件，跳过外部频道合并")
        else:
            print("警告: EXTERNAL_GROUP_TITLES 配置为空，无法提取外部频道")

    # ================= 接入底层检测清洗逻辑 =================
    if ENABLE_STREAM_CHECK and HAS_CHECKER:
        grouped_channels, external_channels = run_smart_checker(grouped_channels, external_channels)
    # ========================================================

    # M3U 文件生成
    for filename, replace_url, catchup_template, is_tv_m3u in [
        (TV_M3U_FILENAME, False, CATCHUP_URL_KU9, True), 
        (TV2_M3U_FILENAME, True, CATCHUP_URL_TEMPLATE, False),
        (KU9_M3U_FILENAME, True, CATCHUP_URL_KU9, False), 
        (APTV_M3U_FILENAME, True, CATCHUP_URL_APTV, False)
    ]:
        content = generate_m3u_content(grouped_channels, replace_url, catchup_template, external_channels, is_tv_m3u, channel_order)
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(content)
        external_count = len(external_channels) if external_channels else 0
        if external_count > 0:
            print(f"已生成M3U文件: {filename} (包含 {external_count} 个外部频道)")
        else:
            print(f"已生成M3U文件: {filename}")

    # 控制台日志汇总
    all_blacklisted_channels = blacklisted_main_channels + blacklisted_custom_channels + blacklisted_external_channels
    total_channels = sum(len(v) for v in grouped_channels.values())
    external_count = len(external_channels) if external_channels else 0
    total_channels_with_external = total_channels + external_count
    
    print(f"\n已跳过 {skipped_url_count} 个缺少播放链接的频道。")
    blacklist_info_parts = [f"主JSON: {len(blacklisted_main_channels)}", f"自定义: {len(blacklisted_custom_channels)}"]
    if blacklisted_external_channels:
        blacklist_info_parts.append(f"外部: {len(blacklisted_external_channels)}")
    print(f"总共过滤 {len(all_blacklisted_channels)} 个黑名单频道（{', '.join(blacklist_info_parts)}）")
    
    if external_m3u_source != "none":
        source_desc = "网络" if external_m3u_source == "network" else "缓存"
        print(f"外部 M3U 来源: {source_desc}")
    if duplicate_external_channels:
        print(f"外部 M3U 按 URL 去重 {len(duplicate_external_channels)} 个频道（保留首次出现）")
    
    if external_count > 0:
        print(f"成功生成 {total_channels} 个本地频道 + {external_count} 个外部频道 = 总计 {total_channels_with_external} 个频道")
    else:
        print(f"成功生成 {total_channels} 个频道")
    print(f"单播地址列表: {os.path.abspath(TV2_M3U_FILENAME)}")
    print(f"KU9回看参数列表: {os.path.abspath(KU9_M3U_FILENAME)}") 
    print(f"APTV回看参数列表: {os.path.abspath(APTV_M3U_FILENAME)}") 

    # ================= 还原完整的文件日志写入逻辑 =================
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(CHANNEL_PROCESSING_LOG, "w", encoding="utf-8") as f:
        f.write("频道处理日志\n")
        f.write(f"{LOG_SEPARATOR}\n\n")
        
        # 主JSON频道处理结果
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
            
            source_info = ""
            probe_tag = "" 
            for group in grouped_channels.values():
                for c in group:
                    if c["original_title"] == original_name and not c.get("is_custom", False):
                        source_info = f" [{c.get('url_source', 'UNKNOWN')}]"
                        probe_tag = f" {c.get('probe_info', '')}" if c.get('probe_info') else "" 
                        break
                if source_info: break
            
            if original_name != final_name:
                f.write(f"  - {original_name} -> {final_name}{source_info}{probe_tag}\n")
            else:
                f.write(f"  - {original_name}{source_info}{probe_tag}\n")
        f.write("\n\n")
        
        # 自定义频道处理结果
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
                source_info = f" [{channel.get('url_source', 'UNKNOWN')}]"
                
                probe_tag = ""
                for c in grouped_channels.get(group_name, []):
                    if c["original_title"] == original_name and c.get("is_custom", False):
                        probe_tag = f" {c.get('probe_info', '')}" if c.get('probe_info') else ""
                        break
                
                if original_name != final_name:
                    f.write(f"  - [{group_name}] {original_name} -> {final_name}{source_info}{probe_tag}\n")
                else:
                    f.write(f"  - [{group_name}] {final_name}{source_info}{probe_tag}\n")
        else:
            f.write("  (无)\n")
        f.write("\n\n")
        
        # 外部频道处理结果
        if ENABLE_EXTERNAL_M3U_MERGE:
            f.write("【外部 M3U 频道处理结果】\n")
            f.write(f"{LOG_SEPARATOR}\n\n")
            source_desc = {"network": "网络更新", "cache": "本地缓存", "none": "未使用"}.get(external_m3u_source, external_m3u_source)
            f.write(f"数据来源: {source_desc}\n")
            f.write(f"缓存文件: {EXTERNAL_M3U_CACHE_FILE}\n\n")
            
            f.write(f"1. 黑名单过滤 ({len(blacklisted_external_channels)} 个):\n")
            if blacklisted_external_channels:
                for channel in blacklisted_external_channels:
                    f.write(f"  - 标题: {channel['title']}, 原因: {channel['reason']}\n")
            else:
                f.write("  (无)\n")
            f.write("\n")
            
            f.write(f"2. URL 重复过滤 ({len(duplicate_external_channels)} 个):\n")
            if duplicate_external_channels:
                for channel in duplicate_external_channels:
                    f.write(f"  - [{channel.get('group_title', '未知分组')}] {channel['title']} -> {channel['url']} ({channel['reason']})\n")
            else:
                f.write("  (无)\n")
            f.write("\n")

            f.write(f"3. 成功合并 ({external_count} 个):\n")
            if external_channels:
                for channel in external_channels:
                    probe_tag = f" {channel.get('probe_info', '')}" if channel.get('probe_info') else ""
                    f.write(f"  - [{channel.get('group_title', '未知分组')}] {channel['title']}{probe_tag}\n")
            else:
                f.write("  (无)\n")
            f.write("\n\n")
        
        # 汇总信息
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
        if ENABLE_EXTERNAL_M3U_MERGE:
            f.write(f"外部 M3U 汇总:\n")
            f.write(f"  - 数据来源: {external_m3u_source}\n")
            f.write(f"  - URL 去重: {len(duplicate_external_channels)} 个\n")
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

    if ENABLE_EPG_DOWNLOAD: 
        run_epg_download(data["channels"], custom_channels_config, grouped_channels)
    else:
        print("\nEPG下载已禁用，跳过EPG下载和生成。")

if __name__ == "__main__":
    main()