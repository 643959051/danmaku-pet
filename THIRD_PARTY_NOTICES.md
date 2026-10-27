# 第三方组件与素材声明

本项目自身代码以 [MIT](LICENSE) 许可发布。运行时依赖和视觉素材来自下面这些项目，分发时请保留各自的许可文本。

## 运行时依赖

| 组件 | 许可证 | 用途 | 本项目如何获得 |
| --- | --- | --- | --- |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT | 本地 HTTP / WebSocket 服务 | `pip install`（`requirements.txt`） |
| [uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | ASGI 服务器 | 同上 |
| [pydantic](https://github.com/pydantic/pydantic) | MIT | 请求 / 响应模型 | 同上 |
| [httpx](https://github.com/encode/httpx) | BSD-3-Clause | DeepSeek HTTP 客户端 | 同上 |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | BSD-3-Clause | 读取 `.env` | 同上 |
| [edge-tts](https://github.com/rany2/edge-tts) | **LGPL-3.0** | 语音合成（在线） | 同上 |
| [pygame](https://github.com/pygame/pygame) | **LGPL-2.1-or-later** | 音频播放 | 同上 |
| [PySide6 / Qt for Python](https://doc.qt.io/qtforpython-6/) | **LGPL-3.0**（亦提供 GPL / 商业许可） | 桌面小人窗口（QtCore / QtGui / QtWidgets） | 同上 |
| [pytest](https://github.com/pytest-dev/pytest) | MIT | 测试（仅开发依赖） | 同上 |

**打包分发时注意**：`scripts/build_exe.ps1` 产出的便携版是 onedir，LGPL 组件（PySide6 / Qt DLL、edge-tts、pygame）以独立文件形式放在 `_internal/` 中，可以被替换。如果你把这个目录分发出去，请一并附上它们的许可证文本，并保留这些动态库文件不被静态合并。

## 桌面小人与角色素材

| 组件 | 许可证 | 说明 |
| --- | --- | --- |
| [dsh-dafeiyu](https://www.npmjs.com/package/dsh-dafeiyu) | MIT（Copyright (c) 2026 QCYTSN） | 桌面小人原生实现。**不随本仓库分发**：`scripts/install.ps1` 用 `npm pack` 下载到 `vendor/`，运行时由本项目以 `--run-dafeiyu-helper` 重入执行其 `runtime/helper.py` |
| [dsh-pet](https://github.com/PC2005-cloud/dsh-pet) | MIT | dafeiyu 当前使用的角色帧素材来源（经 dafeiyu 打包） |

上游 `dafeiyu` 包内还有一份 `ASSET_LICENSE.md`，其中记录了旧版 BigFish 帧的不同条款。本项目只用当前 `assets/pet/` 下的帧；如果你要二次分发出 `vendor/` 里的素材，请先阅读该文件。

## 其他

- 页面使用的字体为系统中文字体，不含第三方字体文件。
- 本项目不包含任何 DeepSeek、抖音或 Gitee 的 API 密钥；`.env` 被 `.gitignore` 排除。
