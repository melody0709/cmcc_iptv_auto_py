# Changelog

本项目变更记录。

## [Unreleased]

- 配置加载改进：`tv.py` 启动时按 `config/config.json` -> `config/myconfig.json` 覆盖加载配置。
- EPG 下载改进：新增并启用 `EPG_DAY_OFFSETS` 多天下载窗口（例如 `[-5,-4,-3,-2,-1,0,1]`）。
- EPG 合成修复：修正节目去重字段匹配，恢复多天场景下节目条目数量异常偏低的问题。
- 路径兼容修复：配置文件路径改为基于脚本目录解析，避免 UNC/CWD 变化导致读取失败。
- 日志路径修复：`channel_processing.log` 与 `epg_statistics.log` 固定输出到 `log/` 目录并自动创建目录。
- 文档更新：补充 Windows UNC 下稳定运行命令与配置使用说明。

## [v2026.3.23] - 2026-03-23

- 目录精简：自定义 JSON 统一迁移到 `config/`，运行日志统一写入 `log/`。
- 配置外置：支持 `config/config.json` + `config/myconfig.json` 分层覆盖。
- Git 规则更新：远程仓库统一使用 SSH 方式上传到 GitHub。

## [v2026.2.8] - 2026-03-23

- 作为版本控制基线版本。
