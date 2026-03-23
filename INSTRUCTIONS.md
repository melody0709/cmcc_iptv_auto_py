# INSTRUCTIONS

本文件用于统一本仓库的开发、维护和提交规范，适用于人工协作和 AI 代码助手。

## 1) 项目目标

- 自动抓取广东移动 IPTV 频道数据并生成播放列表。
- 生成多种播放格式：`tv.m3u`、`tv2.m3u`、`ku9.m3u`、`aptv.m3u`。
- 可选下载并合成 EPG：`t.xml`、`t.xml.gz`。

## 2) 关键文件职责

- `config/config.json`：默认配置文件（可提交），集中管理脚本运行参数。
- `config/myconfig.json`：本地覆盖配置（优先级高于 `config/config.json`，不提交）。
- `config/channel_order.json`：分组内频道排序。
- `config/custom_channels.json`：自定义频道补充。
- `log/`：运行时日志目录（`channel_processing.log`、`epg_statistics.log`）。

## 3) 环境与运行


- 配置加载优先级：`config/config.json` -> `config/myconfig.json`（后者覆盖前者同名键）。
- `config/myconfig.json` 仅存放个人差异项，避免复制整份默认配置。

```powershell
python .\tv.py
```

## 4) 修改原则（必须遵守）

- 以“最小改动”解决问题，避免大范围重构。
- 不改动与当前需求无关的配置、排序、黑名单、频道映射。
- 保持现有输出文件名和主要输出结构稳定（除非需求明确要求）。
- 涉及网络请求逻辑时，优先保留现有重试、超时与回退策略。
- 修改 `GROUP_DEFINITIONS` / `GROUP_OUTPUT_ORDER` 时，同步确认 `custom_channels.json` 的分组可落位。

## 5) 代码风格

- 保持与现有代码风格一致（当前以中文注释 + 常量配置为主）。
- 变量名使用清晰语义，避免单字符命名。
- 新增函数应聚焦单一职责，避免把配置硬编码到函数内部。
- 日志输出保持可读，便于定位“过滤数量、下载进度、输出路径”。

## 6) 配置变更注意事项

- `JSON_URL`、`EPG_BASE_URLS`、`CATCHUP_SOURCE_PREFIX` 变更需保证可访问性。
- `NGINX_PROXY_PREFIX` 相关逻辑变更需同时验证 `tv.m3u` 与其余 M3U 的行为差异。
- 调整 `EXTERNAL_GROUP_TITLES` 时，需验证外部分组并入后顺序和重命名结果。
- 修改黑名单规则时，避免误伤常用频道（先小范围验证再提交）。

## 7) 提交前自检清单

- 能成功运行：`python .\tv.py`。
- 至少检查以下输出是否生成且非空：
  - `tv.m3u`
  - `tv2.m3u`
  - `ku9.m3u`
  - `aptv.m3u`
- 若启用 EPG：确认 `t.xml` 与 `t.xml.gz` 正常生成。
- 检查日志是否有明显异常（超时暴增、频道数异常下降、分组丢失）。

## 8) 文档同步规则

当出现以下情况时，必须同步更新 `README.md`：

- 新增或删除配置项。
- 输出文件名称或格式变化。
- 回看参数模板变化。
- EPG 下载模式或行为变化。

## 9) Git 与发布规则

- 远程仓库统一使用 SSH 地址（`git@github.com:<owner>/<repo>.git`），不要使用 HTTPS 地址提交。
- 推送前先确认远程：`git remote -v`。
- 若远程不是 SSH，先执行：`git remote set-url origin git@github.com:melody0709/cmcc_iptv_auto_py.git`。

## 10) 版本号规则

- `VERSION` 为版本号单一事实来源（Single Source of Truth），格式：`YYYY.M.D`。
- 发布版本时必须同步更新三处：`VERSION`、`CHANGELOG.md`、`README.md` 顶部版本信息。
- `CHANGELOG.md` 保留 `Unreleased` 段，并在发布时新增版本段落（如 `v2026.3.23`）。

## 11) AI 助手执行约束

- 优先读 `README.md` 与本文件后再改代码。
- 修改应聚焦用户请求，不主动引入额外功能。
- 若需要新增文件，优先小而清晰，并说明用途。
- 不能确认的行为（网络依赖、地区可达性）要明确标注“需用户环境验证”。
- 未经用户明确要求，不要主动运行 `python` 命令进行验证。
- 完成改动后，优先提供可复制的验证命令，由用户自行执行并确认结果。
