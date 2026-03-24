# Changelog

本项目变更记录。

## [v1.2] - 2026-03-24

- 外部 M3U 缓存回退：新增 `CACHE_M3U_FILENAME` 配置，默认使用 `cache.m3u` 作为外部 M3U 本地缓存文件。
- 下载容错增强：外部 M3U 改为默认优先网络更新，网络请求失败时自动回退到本地缓存，避免合并结果丢失外部频道。
- 外部频道去重：外部 M3U 解析阶段新增按 URL 去重逻辑，遇到“同 URL 不同别名”时仅保留第一次出现的频道。
- 日志增强：`channel_processing.log` 增加外部 M3U 来源、缓存文件路径和 URL 重复过滤统计，便于排查外部合并结果。
- 文档同步：更新 README 外部 M3U 配置说明，并同步项目版本到 v1.2。

## [v1.1] - 2026-03-24

1- EPG 设置简化：新增 `EPG_DAY_OFFSETS: [9]` 简化写法，自动展开为包含明天的 9 天 EPG 下载窗口。
- 配置统一：`config.json` 和 `myconfig.json` 默认同步更新为简化后的 9 天配置。
- 版本更迭：版本号统一更新为 v1.1。

## [v1.0] - 2026-03-23

- 配置加载改进：`tv.py` 启动时按 `config/config.json` -> `config/myconfig.json` 覆盖加载配置。
- EPG 下载改进：新增并启用 `EPG_DAY_OFFSETS` 多天下载窗口（例如 `[-5,-4,-3,-2,-1,0,1]`）。
- EPG 合成修复：修正节目去重字段匹配，恢复多天场景下节目条目数量异常偏低的问题。
- 路径兼容修复：配置文件路径改为基于脚本目录解析，避免 UNC/CWD 变化导致读取失败。
- 日志路径修复：`channel_processing.log` 与 `epg_statistics.log` 固定输出到 `log/` 目录并自动创建目录。
- 文档更新：补充 Windows UNC 下稳定运行命令与配置使用说明。
- 目录精简：自定义 JSON 统一迁移到 `config/`，运行日志统一写入 `log/`。
- 配置外置：支持 `config/config.json` + `config/myconfig.json` 分层覆盖。
- Git 规则更新：远程仓库统一使用 SSH 方式上传到 GitHub。

## [v1.0.0-beta] - 2026-03-23

- 作为版本控制基线版本。

