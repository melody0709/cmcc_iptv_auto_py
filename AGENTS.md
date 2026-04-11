# INSTRUCTIONS

本文件用于统一本仓库的开发、维护和提交规范，适用于人工协作和 AI 代码助手。

##  项目目标

- 自动抓取广东移动 IPTV 频道数据并合并外部 M3U 源。
- **内置流质量与存活检测**：通过 `iptv_checker_v3` 在内存中剔除失效源并附加真实画质标签（1080P/4K）。
- 生成多种绝对纯净、可播的格式：`tv.m3u`、`tv2.m3u`、`ku9.m3u`、`aptv.m3u`。
- 可选下载并合成 EPG：`t.xml`、`t.xml.gz`。

##  关键文件职责

- `tv.py`：主调度程序。负责数据抓取、去重、排序、调用检测引擎、渲染 M3U。
- `iptv_checker_v3/iptv_checker_v3.py`：独立/被动双模智能检测引擎。负责极速测活（`aiohttp`）和深度画质探测（`ffprobe`）。该子目录自带 `AGENTS.md`，修改探测器前必读。
- `config/config.json`：仓库默认配置（可提交）。
- `config/myconfig.json`：本地覆盖配置（优先级更高，已在 `.gitignore` 中）。
- `config/channel_order.json`：分组内频道排序。
- `config/custom_channels.json`：自定义频道（新增分组需同步更新 `tv.py` 的 `GROUP_DEFINITIONS` 和 `GROUP_OUTPUT_ORDER`）。
- `config/probe_cache.json`：ffprobe 画质探测缓存（已在 `.gitignore` 中，24h 过期）。
- `log/`：统一日志目录（`channel_processing.log`、`checker_final.log`、`failed_channels.txt`、`epg_statistics.log`）。

##  环境与运行

- Python 3.8+（群晖建议 Python 3.10 + 虚拟环境 `venv310/`）。
- 依赖：`aiohttp`、`requests`（`pip install -r requirements.txt`）。
- 深度画质探测 (`ENABLE_PROBE=True`) 需要系统 PATH 中可用 `ffprobe` 命令。
- 网络前提：需能访问 `183.235.0.0/16` 网段（广东移动 IPTV 内网）。


### 运行命令

```bash
python tv.py                          # 默认运行
python tv.py --skip-epg               # 临时禁用 EPG 下载
python tv.py --skip-check             # 临时禁用流检测引擎
python tv.py --skip-probe             # 允许测活但禁用 ffprobe 画质探测
python tv.py -se -sc -sp              # 可组合使用
```

### 配置加载顺序

1. `tv.py` 内硬编码默认值
2. `config/config.json`（仓库默认）
3. `config/myconfig.json`（本地覆盖，同名键覆盖前者，未出现键继续继承）

##  修改原则（必须遵守）

- **内存总线原则**：`tv.py` 调用 `iptv_checker_v3` 时，**绝对禁止**使用文件做中转。必须通过 `process_channel_list(channels)` 在内存中传递 `List[Dict]`，实现零磁盘损耗。
- **异步安全**：`iptv_checker_v3` 采用高度敏感的纯异步与子进程逻辑。任何对其发包机制（如 Magic Bytes 截断、并发限制）的修改，都必须防范可能引发的 MPEG-TS 流无限阻塞或系统挂起。
- **三层回退算法不可简化**：`probe_video_info` 的三层回退保底算法（直击切片 -> 原链回退 -> 文本正则）是针对防盗链极端环境调优的结果，不可随意简化或合并步骤。
- 保持现有输出文件名和主要输出结构稳定（除非需求明确要求）。
- 修改 `GROUP_DEFINITIONS` / `GROUP_OUTPUT_ORDER` 时，同步确认 `custom_channels.json` 的分组可落位。
- 不能删除现在注解和调试输出

##  配置变更注意事项

- `JSON_URL`、`EPG_BASE_URLS`、`CATCHUP_SOURCE_PREFIX` 变更需保证可访问性。
- `ENABLE_STREAM_CHECK` 及 `CHECK_TARGET_GROUPS` 逻辑修改时，需确保正确抽取底层流地址（`zteurl`），**不要**将附带 `?starttime=` 的回看地址送去探测，避免无谓的重复探测。
- `NGINX_PROXY_PREFIX` 相关逻辑变更需同时验证 `tv.m3u` 与其余 M3U 的行为差异（`tv.m3u` 有独立开关 `ENABLE_NGINX_PROXY_FOR_TV`）。
- 调整 `EXTERNAL_GROUP_TITLES` 时，需验证外部分组并入后顺序、重命名及画质标签追加结果。该配置支持列表 `["A", "B"]` 和字典 `{"A": "新A"}` 两种格式。
- 修改黑名单规则时，避免误伤常用频道（先小范围验证再提交）。
- `IS_HWURL` 仅影响 `hwurl`/`zteurl` 的优先提取，回看代码(ztecode)始终使用 `params["ztecode"]`。

##  代码风格

- 保持与现有代码风格一致
  
## 版本与提交规则

- 发布版本时至少同步更新：`CHANGELOG.md`、`README.md` 的版本信息；若子模块文档说明受影响，也要同步更新对应 README/CHANGELOG。
- 不要提交生成的 M3U 文件、XML 文件、日志、缓存、虚拟环境（已在 `.gitignore` 中）。


## AI 助手执行约束
- 主动运行 `python` 命令进行验证
- 若需要新增文件，优先小而清晰，并说明用途。

> AI 在完成重大修改或解决复杂报错后，可以追加经验记录,更新AGENTS.md。
