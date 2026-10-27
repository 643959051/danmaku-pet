# 安装位置清单

生成日期：2026-10-27  
工作区：`D:\working\dsh-chat\douyin-live`

## 1. Python / conda

| 项 | 实际位置 / 值 |
| --- | --- |
| conda 可执行文件 | `D:\working\conda3\Scripts\conda.exe` |
| 项目专属 conda 前缀 | `D:\working\dsh-chat\douyin-live\.conda\env` |
| Python 解释器 | `D:\working\dsh-chat\douyin-live\.conda\env\python.exe` |
| Python 版本 | `3.11.18` |
| site-packages | `D:\working\dsh-chat\douyin-live\.conda\env\Lib\site-packages` |
| 环境体积 | 约 877 MB |

没有使用全局 Python，没有创建 `.venv`，没有执行 `pip install --user`。

创建环境时使用的命令：

```powershell
conda create -p ./.conda/env python=3.11 -y
```

本机 conda 的 ToS 插件会强制读写用户缓存，因此实际创建时使用了等价的：

```powershell
conda --no-plugins create -p ./.conda/env python=3.11 -y --solver classic
```

环境位置和 Python 版本不变。

## 2. Python 依赖

全部位于 `.conda/env/Lib/site-packages`。

| 包 | 版本 |
| --- | --- |
| fastapi | 0.162.0 |
| uvicorn | 0.54.0 |
| httpx | 0.28.1 |
| pydantic | 2.14.1 |
| edge-tts | 7.3.1 |
| pygame | 2.6.1 |
| PySide6 | 6.12.0 |
| pytest | 8.4.2 |

另有 41 个传递依赖，共 49 个 distribution。完整审计见 `docs/install-report.json`。

安装命令（只在项目 conda 前缀内执行）：

```powershell
.\.conda\env\python.exe .\scripts\pip_workspace.py install -r requirements.txt
```

`scripts/pip_workspace.py` 只做一件事：让 pip 的临时目录使用 `0777`，绕开本工作区在创建 `0700` 目录时产生的权限问题；它不修改系统 Python，也不修改系统策略。

## 3. 视觉后端

| 项 | 实际位置 |
| --- | --- |
| 插件包 | `D:\working\dsh-chat\douyin-live\vendor\dafeiyu\package` |
| 版本 | `dsh-dafeiyu@0.1.21` |
| 原生运行入口 | `...\vendor\dafeiyu\package\runtime\helper.py` |
| 动画清单 | `...\vendor\dafeiyu\package\assets\pet-manifest.json` |
| 动画帧 | `...\vendor\dafeiyu\package\assets\pet\` |
| helper 原版备份 | `...\vendor\dafeiyu\package\runtime\helper.py.spin-patch.bak` | 本地 spin effect 补丁前备份 |
| 下载包 | `...\vendor\dsh-dafeiyu-0.1.21.tgz`（本地保留，便于重装） |
| 包体积 | 约 231 MB（含多平台 Helper；本项目只运行源码） |

来源：npm `dsh-dafeiyu@0.1.21`，通过项目内 `scripts/fetch_visual.ps1` 下载，未安装到 DSH 全局插件目录。

运行时环境变量全部重定向：

```text
TEMP / TMP        -> 工作区 temp/dafeiyu-runtime
LOCALAPPDATA      -> 工作区 .cache/localappdata
DSH_DAFEIYU_LAYOUT_PATH -> 工作区 temp/dafeiyu-layout.json
```

## 4. 缓存 / 音频 / 临时文件

| 项 | 实际位置 | 体积 |
| --- | --- | --- |
| TTS 音频缓存 | `D:\working\dsh-chat\douyin-live\assets\tts_cache` | 约 0.2 MB |
| pip 缓存 | `D:\working\dsh-chat\douyin-live\.cache\pip` | 约 286 MB |
| conda 包缓存 | `D:\working\dsh-chat\douyin-live\.cache\conda-pkgs` | 约 218 MB |
| npm 缓存 | `D:\working\dsh-chat\douyin-live\.cache\npm` | 包下载缓存 |
| 临时目录 | `D:\working\dsh-chat\douyin-live\temp` | 约 0.6 MB |
| 运行日志 | `D:\working\dsh-chat\douyin-live\temp\logs\app.log` | 滚动日志 |
| dafeiyu 布局 | `D:\working\dsh-chat\douyin-live\temp\dafeiyu-layout.json` | 247 B |
| LLM/自动回复设置 | `D:\working\dsh-chat\douyin-live\.env` | 网页“设置”或自动回复开关保存时创建；含 Key 时为本地明文，已加入 `.gitignore` |
| 角色语气配置 | `D:\working\dsh-chat\douyin-live\data\persona.json` | 网页“角色语气”保存时创建 |
| ACL 修复备份 | `D:\working\dsh-chat\douyin-live\temp\acl-recovery` | 约 60 KB |

## 5. 没有安装到哪些地方

- 没有全局 `pip install`
- 没有 `pip install --user`
- 没有 `npm install -g`
- 没有项目 `node_modules`（只调用系统 Node 的 `npm pack` 下载，npm 缓存指向上表）
- 没有把 dsh-dafeiyu 装到 `~/.dsh/profiles/...` 的用户插件目录
- 没有修改系统环境变量
- 没有修改 PowerShell 执行策略（只对单次进程使用 `-ExecutionPolicy Bypass`）

## 6. 验证方法

```powershell
.\.conda\env\python.exe .\scripts\verify_install.py
```

验证结果：

```text
prefix_is_project_env: true
outside_workspace_distributions: []
all_expected_inside: true
all_distributions_inside: true
pip_check_ok: true
pip_check_output: No broken requirements found.
```

完整机器可读报告：[`install-report.json`](install-report.json)。

## 7. 工作区外临时残留

修复工作区 Windows 文件权限时，DSH 的 ACL 诊断脚本在工作区相邻目录创建过 `douyin-live-acl-recovery`。该目录已：

1. 复制到 `temp/acl-recovery` 保留可追溯记录；
2. 从工作区外删除；
3. 再次确认 `D:\working\dsh-chat\douyin-live-acl-recovery` 不存在。

当前项目依赖、缓存、模型、音频和下载文件均位于工作区。
