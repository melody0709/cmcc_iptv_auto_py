# 广东移动IPTV自动抓取脚本

全流程自动化的广东移动IPTV频道列表和EPG节目单抓取工具，支持频道分组、黑名单过滤、自定义频道合并、分组排序、回看功能等。

**版本日期**: 2025.11.01400

## 功能特性

### 核心功能

- **自动下载频道列表**：从广东移动IPTV服务器自动获取频道JSON数据
- **智能频道分组**：自动将频道分类为央视、央视特色、广东、卫视、少儿、CGTN、华数咪咕、超清4k、广东地方台、其他等分组
- **频道去重**：自动去除重复频道，优先保留高清/超清版本
- **黑名单过滤**：支持按标题关键词、频道代码(code)或播放链接(zteurl)过滤频道
- **自定义频道合并**：通过 `custom_channels.json` 添加自定义频道
- **分组排序**：通过 `channel_order.json` 自定义各分组内频道的排序
- **频道名称映射**：支持频道名称映射（如 "CCTV-3高清" → "CCTV-3综艺"），不影响EPG对齐

### M3U文件生成

脚本会生成3个M3U文件：

1. **tv.m3u**：组播地址列表（原始组播地址）
2. **tv2.m3u**：单播地址列表（通过UDPXY转换的组播地址）
3. **ku9.m3u**：单播地址列表（使用KU9回看参数格式）

### EPG节目单

- **自动下载EPG**：根据频道code自动下载EPG数据
- **支持多源下载**：可配置多个EPG下载源，自动分配任务
- **下载模式**：
  - `M3U_ONLY`：仅下载M3U文件中包含的频道EPG（推荐）
  - `ALL`：下载所有可用频道的EPG（包括被过滤的频道）
- **生成文件**：
  - `t.xml`：XML格式的EPG节目单
  - `t.xml.gz`：压缩后的EPG文件
  - `epg_statistics.log`：EPG下载统计日志

### 回看功能

- **自动识别**：根据JSON中的 `timeshiftAvailable` 或 `lookbackAvailable` 字段自动添加回看参数
- **双模板支持**：
  - 标准回看模板：适配OK影视等播放器
  - KU9回看模板：适配酷9最新版本
- **Nginx代理支持**：支持通过Nginx代理回看源，实现外网访问

### 日志文件

- **channel_processing.log**：频道处理日志，记录去重、改名、黑名单过滤等操作
- **epg_statistics.log**：EPG下载和合成统计信息

## 配置说明

### 基本配置

在 `tv.py` 文件顶部修改以下配置：

```python
# UDPXY地址（组播转单播）
REPLACEMENT_IP = "http://c.top:7088/udp"

# 回看源前缀
CATCHUP_SOURCE_PREFIX = "http://183.235.162.80:6610/190000002005"

# Nginx代理前缀（用于外网访问）
NGINX_PROXY_PREFIX = "http://c.top:7077"

# JSON数据源地址
JSON_URL = "http://183.235.16.92:8082/epg/api/custom/getAllChannel.json"

# EPG下载源（可配置多个）
EPG_BASE_URLS = [
    "http://183.235.16.92:8082/epg/api/channel/",
    "http://183.235.11.39:8082/epg/api/channel/"
]
```

### 回看模板配置

```python
# 标准回看模板（OK影视等）
CATCHUP_URL_TEMPLATE = "{prefix}/{ztecode}/index.m3u8?starttime=${{utc:yyyyMMddHHmmss}}&endtime=${{utcend:yyyyMMddHHmmss}}"

# KU9回看模板（酷9最新版）
CATCHUP_URL_KU9 = "{prefix}/{ztecode}/index.m3u8?starttime=${{(b)yyyyMMddHHmmss|UTC}}&endtime=${{(e)yyyyMMddHHmmss|UTC}}"
```

### EPG配置

```python
# EPG下载开关
ENABLE_EPG_DOWNLOAD = True  # True-启用, False-禁用

# EPG下载模式
EPG_DOWNLOAD_MODE = "M3U_ONLY"  # "M3U_ONLY" 或 "ALL"

# EPG合成模式
XML_SKIP_CHANNELS_WITHOUT_EPG = True  # True-跳过无EPG的频道, False-保留频道标签
```

### 黑名单配置

```python
BLACKLIST_RULES = {
    "title": ["测试频道", "购物", "导视", "百视通", "指南", "精选频道"],
    "code": [
        # 添加要过滤的频道代码
    ],
    "zteurl": [
        # 添加要过滤的播放链接
    ]
}
```

## 配置文件

### custom_channels.json

自定义频道配置文件，格式示例：

```json
{
    "广东地方台": [
        {
            "title": "广州新闻高清",
            "code": "02000006000000052022060699000003",
            "ztecode": "ch000000000000104",
            "zteurl": "rtp://239.21.0.137:3892",
            "icon": "http://183.235.16.92:8081/pics/micro-picture/channelNew/xxx.png"
        }
    ]
}
```

**注意**：如果自定义了新的分组，需要在 `tv.py` 的 `GROUP_DEFINITIONS` 和 `GROUP_OUTPUT_ORDER` 中添加对应的分组名称。

### channel_order.json

频道排序配置文件，格式示例：

```json
{
    "央视": [
        "CCTV-1综合",
        "CCTV-2财经",
        "CCTV-3综艺"
    ],
    "广东": [
        "广东卫视",
        "广东珠江"
    ]
}
```

## 使用方法

### 1. 环境要求

- Python 3.x
- 依赖包：`requests`

安装依赖：

```bash
pip install requests
```

### 2. 运行脚本

```bash
python tv.py
```

### 3. 运行效果示例

```
你的组播转单播UDPXY地址是 http://c.cc.top:7088/udp/
你的回看源前缀是 http://183.235.162.80:6610/190000002005
你的nginx代理前缀是 http://c.cc.top:7077/
你的回看URL模板是 {prefix}/{ztecode}/index.m3u8?starttime=${{utc:yyyyMMddHHmmss}}&endtime=${{utcend:yyyyMMddHHmmss}}
你的KU9回看URL模板是 {prefix}/{ztecode}/index.m3u8?starttime=${{(b)yyyyMMddHHmmss|UTC}}&endtime=${{(e)yyyyMMddHHmmss|UTC}}
EPG下载开关: 启用
EPG下载配置: 重试3次, 超时15秒, 间隔2秒
成功加载频道排序文件: channel_order.json
成功加载自定义频道文件: custom_channels.json
自定义频道配置: ['广东', '广东地方台']
  分组 '广东' 有 5 个频道
  分组 '广东地方台' 有 27 个频道
成功获取 JSON 数据从 http://183.235.16.92:8082/epg/api/custom/getAllChannel.json
已过滤 14 个黑名单频道（主JSON）
自定义频道名称映射: '广州新闻-测试' -> '广州新闻高清'
自定义频道名称映射: '广州综合-测试' -> '广州综合高清'
已将回看源代理至: http://c.cc.top:7077/183.235.162.80:6610/190000002005
已为 150 个支持回看的频道添加catchup属性
已生成M3U文件: tv.m3u
已将回看源代理至: http://c.cc.top:7077/183.235.162.80:6610/190000002005
已为 150 个支持回看的频道添加catchup属性
已生成M3U文件: tv2.m3u
已将回看源代理至: http://c.cc.top:7077/183.235.162.80:6610/190000002005
已为 150 个支持回看的频道添加catchup属性
已生成M3U文件: ku9.m3u

已跳过 0 个缺少播放链接的频道。
总共过滤 14 个黑名单频道（主JSON: 14, 自定义: 0）
成功生成 191 个频道
单播地址列表: \\DS920\web\IPTV\cmcc_iptv_auto_py\tv2.m3u
KU9回看参数列表: \\DS920\web\IPTV\cmcc_iptv_auto_py\ku9.m3u
已生成处理日志: \\DS920\web\IPTV\cmcc_iptv_auto_py\channel_processing.log

开始下载节目单...
EPG 模式: M3U_ONLY (仅下载和合成 M3U 中的频道)
总共将为 191 个 M3U 频道条目尝试下载EPG。
XML 文件将基于这 191 个频道生成。
准备并行下载 191 个频道的EPG，使用 2 个epg地址下载...
  下载进度: 191/191 个频道 (100.0%)
所有下载任务已完成。
已保存节目单XML文件到: \\DS920\web\IPTV\cmcc_iptv_auto_py\t.xml
已生成压缩文件: \\DS920\web\IPTV\cmcc_iptv_auto_py\t.xml.gz

==================================================
EPG 合成统计
==================================================

基本统计:
   - XML 中总共写入 176 个频道
   - 其中 176 个频道成功合成了节目数据
   - 总共合成了 11731 个节目条目
   - 已跳过 15 个没有节目数据的频道

详细统计已保存到: \\DS920\web\IPTV\cmcc_iptv_auto_py\epg_statistics.log
```

## 网络环境配置

### 前提条件

- **广东移动宽带**：脚本需要访问 `183.235.0.0/16` 网段的IPTV服务器
- **IPTV接口**：需要配置IPTV网络接口（如 `br-iptv`）

### 环境说明

**楼主环境：广东移动宽带**

- **路由器**：immortalwrt 23.05，单线复用，光猫到客厅只要一条线

![单线复用配置](files/20251107165649301.webp)

- **光猫配置**：光猫改桥接后，只修改internet那边的vlan，就是划分internet vlan到单线复用线接口，用户侧自定义，设为3，iptv不划vlan，单线复用口直出（因为测试过iptv划vlan导致4分钟卡顿）
- **路由器配置**：wan口上网用vlan就是eth1.3，新建iptv口不用vlan，设置就是eth1，iptv口为br-iptv，机顶盒桥接br-iptv网络可以正常观看
- **鉴权设置**：由于实测移动iptv不鉴权，所以没有针对设置hostname、mac地址、Vendor class identifier设置

### 抓包获取频道数据（可选）

如果需要手动获取频道数据，可以通过以下方式：

#### 1. 使用tcpdump抓包

```bash
# immortalwrt 安装tcpdump
tcpdump -i br-iptv -w /tmp/iptv_capture.pcap

# 打开盒子电源，等待启动完成后打开直播随便切几个台。然后结束
```

#### 2. 使用Wireshark分析

将抓包文件提取到Windows，用Wireshark分析：

![Wireshark分析](files/pixpin_2025-10-15_20-54-37.png)

按大小排序得到200k左右的getChannel的json文件：

![按大小排序](files/pixpin_2025-10-15_20-55-19.png)

#### 3. 分析得到的API地址

分析抓包数据可以得到以下可访问地址：

```
http://183.235.11.39:8082/epg/api/custom/getChannelsInCategory.json?code=category_54543193 
http://183.235.11.39:8082/epg/api/custom/getAllChannel.json  没有晴彩  看来服务地址不同返回不同的频道

http://183.235.16.92:8082/epg/api/custom/getAllChannel.json    这个地址也行,而且是标准的json格式  多了晴彩频道
http://183.235.16.92:8082/epg/api/custom/getAllChannel2.json 
```

**注意**：默认网络是无法访问 `183.235.0.0/16` 的流量，必须通过iptv端口。网上有手动添加静态路由的办法，但是实测dhcp获得的ip会变动，有时是100.93.0.X，有时是10.150.0.x，有时是100.125.71.x。

### 自动路由配置

由于默认网络无法访问 `183.235.0.0/16` 流量，需要配置路由。脚本提供了自动路由配置方案：

#### 1. 创建路由更新脚本

```bash
cat > /usr/bin/update-iptv-route << 'EOF'
#!/bin/sh

IP=$(ip addr show br-iptv 2>/dev/null | grep -o 'inet [0-9.]*/' | cut -d' ' -f2 | cut -d'/' -f1)

[ -z "$IP" ] && exit 1

CURRENT_GW=$(echo "$IP" | awk -F. '{print $1"."$2"."$3".1"}')

ip route del 183.235.0.0/16 2>/dev/null
ip route add 183.235.0.0/16 via "$CURRENT_GW" dev br-iptv
EOF

chmod +x /usr/bin/update-iptv-route
```

#### 2. 创建网络接口热插拔脚本

```bash
cat > /etc/hotplug.d/iface/99-iptv-route << 'EOF'
#!/bin/sh

[ "$ACTION" = "ifup" ] && [ "$INTERFACE" = "iptv" ] || exit 0

echo "IPTV 接口已启动，等待网络就绪..."
sleep 10
/usr/bin/update-iptv-route
EOF

chmod +x /etc/hotplug.d/iface/99-iptv-route
```

#### 3. 配置系统启动时自动运行

```bash
# 添加到 rc.local 确保启动时运行
echo "sleep 25 && /usr/bin/update-iptv-route" >> /etc/rc.local
```

#### 4. 验证路由配置

```bash
# 手动测试路由脚本
/usr/bin/update-iptv-route

# 检查路由是否添加
ip route | grep 183.235
```

### 防火墙配置

需要在防火墙中配置LAN到IPTV的转发规则，允许访问IPTV网络。

![防火墙配置](files/pixpin_2025-11-05_22-39-00.webp)

## Nginx代理配置（外网访问）

如果需要通过外网访问回看功能，需要配置Nginx代理。

### 1. 安装Nginx

```bash
opkg update
opkg install nginx
```

### 2. 禁用UCI管理

修改 `/etc/config/nginx`，将 `uci_enable` 设置为 `false`，或删除UCI配置中的server块。

### 3. 配置Nginx代理

创建代理配置文件：
- 解析器设置为自己的openwrt ip

```bash
cat > /etc/nginx/conf.d/nginx-proxy.conf << 'EOF'
server {
    listen 7077;
    listen [::]:7077;
    
    # 解析器设置
    resolver 10.10.10.1;
    
    # 通用代理 - 支持任意目标地址
    location ~* "^/(?<target_host>[^/]+)(?<target_path>.*)$" {
        # 构建代理目标URL
        set $proxy_target "http://$target_host$target_path$is_args$args";
        
        proxy_pass $proxy_target;
        proxy_set_header Host $target_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        proxy_connect_timeout 30s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        proxy_buffering off;
    }
}
EOF
```

### 4. 配置主配置文件

```bash
cat > /etc/nginx/nginx.conf << 'EOF'
user root;
worker_processes 1;

error_log /var/log/nginx/error.log;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    access_log off;

    sendfile on;
    keepalive_timeout 65;

    include /etc/nginx/conf.d/*.conf;
}
EOF
```

### 5. 检查和管理Nginx

```bash
# 检查配置是否有效
nginx -t

# 检查进程和端口
ps | grep nginx
netstat -ln | grep 7077

# 重载Nginx
/etc/init.d/nginx reload
/etc/init.d/nginx restart
```

配置完成后，回看地址和Logo地址会自动通过Nginx代理：

- 回看地址：`http://your-domain:7077/183.235.162.80:6610/190000002005/ch000000000000104/index.m3u8?starttime=...`
- Logo地址：`http://your-domain:7077/183.235.16.92:8081/pics/micro-picture/channelNew/xxx.png`

## 定时任务

建议将脚本添加到定时任务中，定期更新频道列表和EPG。

### 群晖NAS

在群晖的"任务计划器"中添加定时任务。

### Linux/OpenWrt

使用crontab：

```bash
# 编辑crontab
crontab -e

# 添加任务（每天凌晨2点运行）
0 2 * * * cd /path/to/script && /usr/bin/python3 tv.py
```

## 技术细节

### 频道分组逻辑

脚本按照以下优先级对频道进行分类：

1. 少儿（必须在央视之前）
2. 超清4k（必须在央视之前）
3. 央视
4. 央视特色
5. 广东
6. CGTN
7. 卫视
8. 华数咪咕
9. 其他
10. 广东地方台

### EPG下载机制

- 支持多源并行下载，自动分配任务
- 自动下载当天和次日的EPG数据
- 支持重试机制（默认重试3次）
- 显示实时下载进度

### 回看URL格式

- **标准格式**：`{prefix}/{ztecode}/index.m3u8?starttime=${{utc:yyyyMMddHHmmss}}&endtime=${{utcend:yyyyMMddHHmmss}}`
- **KU9格式**：`{prefix}/{ztecode}/index.m3u8?starttime=${{(b)yyyyMMddHHmmss|UTC}}&endtime=${{(e)yyyyMMddHHmmss|UTC}}`

## 参考资源

- [IPTV相关教程](https://www.hyun.tech/archives/iptv)
- [gmcc-iptv项目](https://github.com/pcg562240/gmcc-iptv)
- [iptv-tool工具](https://github.com/taksssss/iptv-tool) - 为没有EPG的频道提供额外支持

## 效果展示

### 使用效果

- **同城移动大局域网访问正常**：多套房子实现共享，鉴于上传带宽40mbps，正常只支持外网4路8m码率的
- **EPG数据完整**：大部分频道有明天的epg，网上的基本都是当天的，回看正常，只针对酷9最新版本
- **本地台支持**：缺失大部分广东本地台的ztecode、code，因为默认json都不带本地台，需要通过 `custom_channels.json` 手动添加

### 播放器效果截图

![效果展示1](files/pixpin_2025-11-03_18-36-13.webp)

![效果展示2](files/pixpin_2025-11-03_18-36-23.webp)

![效果展示3](files/pixpin_2025-11-03_18-36-30.webp)

![效果展示4](files/pixpin_2025-11-03_18-36-43.webp)

![效果展示5](files/pixpin_2025-11-03_18-37-01.webp)

![效果展示6](files/pixpin_2025-11-03_18-39-11.webp)

![效果展示7](files/pixpin_2025-11-03_18-39-40.webp)

## 注意事项

1. **网络访问**：脚本需要能够访问 `183.235.0.0/16` 网段，必须通过IPTV接口
2. **频道数据**：默认JSON可能不包含所有本地台，需要通过 `custom_channels.json` 手动添加
3. **回看时间**：回看使用UTC时间
4. **EPG数据**：大部分频道有次日的EPG数据，比网上常见的EPG源更完整

## 问题反馈

如果脚本运行出现问题，可以：

1. 查看 `channel_processing.log` 了解频道处理详情
2. 查看 `epg_statistics.log` 了解EPG下载情况
3. 检查网络连接和路由配置
4. 使用AI工具分析错误信息

## 许可证

本项目仅供学习交流使用。
