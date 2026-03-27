# INSTRUCTIONS

本文件用于统一本仓库的开发、维护和提交规范，适用于人工协作和 AI 代码助手。

## 1) 项目目标

- 自动抓取广东移动 IPTV 频道数据并合并外部 M3U 源。
- **内置流质量与存活检测**：在合并完成后，通过 `iptv_checker_v3` 在内存中剔除失效源并附加真实画质标签（1080P/4K）。
- 生成多种绝对纯净、可播的格式：`tv.m3u`、`tv2.m3u`、`ku9.m3u`、`aptv.m3u`。
- 可选下载并合成 EPG：`t.xml`、`t.xml.gz`。

## 2) 关键文件职责

- `tv.py`：主调度程序。负责数据抓取、去重、排序、调用检测引擎、渲染 M3U。
- `iptv_checker_v3.py`：独立/被动双模智能检测引擎。负责极速测活（`aiohttp`）和深度画质探测（`ffprobe`）。
- `config/config.json` / `myconfig.json`：分层覆盖的参数配置文件。
- `config/probe_cache.json`：深度画质探测的持久化缓存数据库，防止重复高耗能探测。
- `log/`：统一日志目录（包含频道合并、EPG统计、探测明细、失效清单）。

## 3) 环境与运行

- Python 3.8+ (必须安装 `requests` 和 `aiohttp`)。
- 执行深度画质探测 (`ENABLE_PROBE=True`) 时，系统环境变量中**必须**可用 `ffprobe` 命令。
  
## 4) 修改原则（必须遵守）
- **内存总线原则**：`tv.py` 调用 `iptv_checker_v2.py` 时，**绝对禁止**使用文件做中转。必须通过 `process_channel_list(channels)` 在内存中传递 `List[Dict]`，实现零磁盘损耗。
- **异步安全**：`iptv_checker_v2` 采用高度敏感的纯异步与子进程逻辑。任何对其发包机制（如 Magic Bytes 截断、并发限制）的修改，都必须防范可能引发的 MPEG-TS 流无限阻塞或系统挂起。
- 保持现有输出文件名和主要输出结构稳定（除非需求明确要求）。
- 修改 `GROUP_DEFINITIONS` / `GROUP_OUTPUT_ORDER` 时，同步确认 `custom_channels.json` 的分组可落位。

## 5) 配置变更注意事项
- `JSON_URL`、`EPG_BASE_URLS`、`CATCHUP_SOURCE_PREFIX` 变更需保证可访问性。
- `ENABLE_STREAM_CHECK` 及 `CHECK_TARGET_GROUPS` 逻辑修改时，需确保正确抽取底层流地址（`zteurl`），不要将附带 `?starttime=` 的回看地址送去探测，避免无谓的重复探测。
- 调整 `EXTERNAL_GROUP_TITLES` 时，需验证外部分组并入后顺序、重命名及画质标签追加结果。  
  
## 6) 代码风格

- 保持与现有代码风格一致（当前以中文注释 + 常量配置为主）。
- 变量名使用清晰语义，避免单字符命名。
- 新增函数应聚焦单一职责，避免把配置硬编码到函数内部。
- 日志输出保持可读，便于定位“过滤数量、下载进度、输出路径”。

## 7) 配置变更注意事项

- `JSON_URL`、`EPG_BASE_URLS`、`CATCHUP_SOURCE_PREFIX` 变更需保证可访问性。
- `NGINX_PROXY_PREFIX` 相关逻辑变更需同时验证 `tv.m3u` 与其余 M3U 的行为差异。
- 调整 `EXTERNAL_GROUP_TITLES` 时，需验证外部分组并入后顺序和重命名结果。
- 修改黑名单规则时，避免误伤常用频道（先小范围验证再提交）。

## 8) 版本号规则

- 发布版本时必须同步更新三处：`CHANGELOG.md`、`README.md` 顶部版本信息。


## 9) AI 助手执行约束

- 优先读 `README.md` 与本文件后再改代码。
- **特别注意**：`iptv_checker_v3.py` 中的 `probe_video_info` 使用的三层回退保底算法（直击切片 -> 原链回退 -> 文本正则）是针对防盗链极端环境调优的结果，不可随意简化或合并步骤。
- 若需要新增文件，优先小而清晰，并说明用途。
- 未经用户明确要求，不要主动运行 `python` 命令进行验证，提供命令由用户确认。