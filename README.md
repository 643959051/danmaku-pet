# 弹幕宠物（云端版）

抖音直播间 AI 弹幕助播：**云端做决策，开播电脑做表演**。

```text
抖音开放平台
   │  官方评论 / 礼物推送
   ▼
抖音云 Docker 后端（本仓库 danmaku_pet/cloud）
   │  校验 → 去重 → 按 room_id 读外部状态 → DeepSeek / 礼物剧本
   │  PerformCommand
   ▼
抖音云网关（正式） 或 自建 WSS（开发/备用）
   │
   ▼
开播电脑 Windows 客户端（danmaku_pet/client，打包成 DanmakuPet.exe）
   │  EdgeTTS → pygame + dafeiyu 小人 → PerformResult 回执
   ▼
直播伴侣采集小人画面和声音
```

- **云端**：接收官方互动数据、模型决策、礼物剧本、按直播间隔离、下发指令、健康检查与监控。
- **本地**：接收指令、本地合成语音、播放声音、驱动桌面小人、打断与回执、诊断页面。
- **密钥边界**：DeepSeek Key 和平台凭证只存在于抖音云；本地 EXE 里没有任何模型 Key，也没有填 Key 的界面。

> 本仓库按《弹幕宠物项目云端改造计划》完成拆分。平台类目、权限和正式网关参数仍需【人工设置】确认，
> 详见 [平台设置清单](docs/平台设置清单.md) 与 [改造对照表](docs/改造对照表.md)。

> **抖音云规范状态**：8000 端口、根目录 `run.sh`、`GET /v1/ping` 已对齐；房间状态/去重/人设存入
> Redis（prod 强制），请求内完成决策与下发，不再依赖常驻后台 worker。正式环境需要 **Redis +
> 抖音云网关**；自建 WSS 仅作单实例开发/备用。逐条证据见
> [抖音云规范对照](docs/抖音云规范对照.md)。

---

## 1. 目录结构

```text
danmaku_pet/
├─ shared/                 云端/本地共享协议（唯一跨端契约）
│  ├─ events.py            Event：room_id / message_id / user_id / gift_id ...
│  ├─ plans.py             ActionPlan、DecisionResult、枚举
│  ├─ commands.py          PerformCommand、PerformResult、状态机
│  └─ protocol.py          JSON 信封、版本兼容、DedupCache、command_id
├─ cloud/                  抖音云后端（python -m danmaku_pet.cloud）
│  ├─ __main__.py          0.0.0.0:8000（run.sh 启动；每实例 1 Worker，状态在 Redis）
│  ├─ server.py            REST + 管理端 WS + 客户端 WS + 平台推送接收
│  ├─ config.py            云端环境变量（密钥只读，永不回写 .env）
│  ├─ orchestrator.py      CloudOrchestrator：请求内决策/下发（无 worker）
│  ├─ state.py             RoomState + Memory/Redis 状态存储
│  ├─ sessions.py          客户端 ↔ 房间绑定（活连接，WSS 备用）
│  ├─ gateway.py           mock / wss / douyin_cloud 三种下行
│  ├─ ws_gateway.py        自建 WSS（开发/备用，与正式链路隔离）
│  ├─ auth.py              管理端 / 客户端令牌
│  ├─ content_safety.py    评论输入 + 模型输出检查
│  └─ metrics.py           分段耗时与计数
├─ client/                 开播电脑客户端（python -m danmaku_pet.client）
│  ├─ __main__.py          本地页面 + 桌宠 + 云端连接
│  ├─ startup.py           启动参数、平台 launch 参数、校验
│  ├─ connection.py        websocket / static / platform 三种通道，心跳与重连
│  ├─ dispatcher.py        指令去重、过期、排序、礼物抢占、回执
│  ├─ executor.py          EdgeTTS → 动作 → 结果
│  ├─ diagnostics.py       连接/房间/动作/音频/桌宠/错误
│  └─ status_server.py     http://127.0.0.1:8787/ 本地诊断页
├─ douyin/                 官方平台接入
│  ├─ openapi.py           可配置路径的 HTTP 客户端（超时/重试/错误分类）
│  ├─ verifier.py          header / HMAC 校验
│  ├─ tasks.py             评论、礼物任务启动/停止（无后台重试）
│  ├─ receiver.py          推送接收：校验 → 解析 → 派发（去重走 StateStore）
│  ├─ adapter.py           官方字段 → Event
│  ├─ dedup.py             message_id / 受控复合去重
│  └─ content_security.py  getAccessToken + 弹幕/昵称内容安全
├─ decision/               模型决策与礼物剧本（云端）
├─ execution/              本地 TTS、动作映射、dafeiyu、执行后端
├─ web/
│  ├─ cloud/index.html     云端管理端
│  └─ client/index.html    本地诊断页
├─ inputs/                 模拟评论 / 礼物数据（阶段 0/3 联调用）
├─ config.py               本地客户端运行配置
├─ persona.py              角色语气（云端）
└─ paths.py                源码/打包的只读资源与可写目录
Dockerfile                 云端镜像（构建上下文 = 仓库根目录）
.dockerignore              构建上下文排除项（密钥/缓存/日志/产物）
deploy/
├─ docker-compose.yml      仅本地联调
└─ cloud.env.example       变量名与安全占位
docs/                      平台设置、部署、开播、排障、改造对照
packaging/                 Windows 客户端 PyInstaller 配置
scripts/                   安装、启动、打包、冒烟、模拟指令
tests/                     协议、云端、平台适配、客户端、部署测试
```

---

## 2. 快速开始

### 2.1 环境

```powershell
# 首次从零安装（项目内 conda 前缀 + 依赖 + dafeiyu 素材）
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

解释器固定为 `.\.conda\env\python.exe`，不依赖全局 Python。

- `requirements-cloud.txt`：云端镜像依赖
- `requirements-client.txt`：开播电脑依赖（含 PySide6 / pygame / edge-tts）
- `requirements.txt`：本地开发与测试（引用 client + pytest）

### 2.2 本地跑云端（开发模式）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_cloud.ps1
```

默认 `APP_ENV=dev`、`GATEWAY_MODE=mock`、`DEFAULT_ROOM_ID=dev-room`，启动日志会打印本次生成的
`ADMIN_TOKEN` 和 `CLIENT_TOKEN`（临时、每次重启都变；正式环境在抖音云密钥管理里配置固定值）。

- 管理端：`http://127.0.0.1:8000/`（输入 ADMIN_TOKEN 进入）
- 存活性探针：`http://127.0.0.1:8000/v1/ping`
- 详细健康检查：`http://127.0.0.1:8000/api/health`
- 模拟评论/礼物：管理端「模拟互动」或 `scripts/smoke_test.py`
- 状态存储：dev 默认 `STATE_BACKEND=memory`；prod 可直接使用抖音云注入的
  `DB_REDIS_ADDRESS` / `DB_REDIS_ACCOUNT` / `DB_REDIS_PASSWORD`，也可显式设置
  `REDIS_URL`（显式值优先）。详见 [抖音云规范对照](docs/抖音云规范对照.md)。

本地云端配置写在 `.env.cloud`（已 gitignore），不会和客户端 `.env` 混用：

```powershell
Copy-Item .\deploy\cloud.env.example .\.env.cloud
notepad .\.env.cloud   # 本地开发记得改 STATE_BACKEND=memory，并把 DOUYIN_CONTENT_SECURITY_ENABLED 设为 false
# 或者填好 DOUYIN_APP_ID / DOUYIN_APP_SECRET 后保留内容安全检测
```

### 2.3 本地跑客户端

**不连云端，先用模拟指令验收本地语音和动作（阶段 3 验收路径）：**

```powershell
.\.conda\env\python.exe .\scripts\make_mock_commands.py
.\.conda\env\python.exe -m danmaku_pet.client `
  --transport static `
  --static-commands .\temp\mock-commands.jsonl `
  --room-id dev-room
```

**连本地云端（WSS 备用链路）：**

```powershell
# 云端改用 wss 下行
$env:GATEWAY_MODE="wss"; powershell -File .\scripts\run_cloud.ps1

# 客户端：令牌用云端启动日志里的 CLIENT_TOKEN
.\.conda\env\python.exe -m danmaku_pet.client `
  --room-id dev-room `
  --cloud-url ws://127.0.0.1:8000/ws/client `
  --client-token "<CLIENT_TOKEN>"
```

客户端会自动打开本地状态页 `http://127.0.0.1:8787/`；`--no-browser` 可关闭。

### 2.4 Docker（本地验证镜像）

```powershell
Copy-Item .\deploy\cloud.env.example .\deploy\cloud.env
docker build -t danmaku-pet-cloud:dev .
docker compose -f .\deploy\docker-compose.yml up
```

`Dockerfile` 和 `.dockerignore` 都在仓库根目录，构建上下文就是仓库根目录；`deploy/` 只保留
compose 与 cloud env 样例。镜像内**没有** PySide6、pygame、edge-tts、桌宠 helper；`/v1/ping`
是抖音云存活性探针，也是容器健康检查（更详细的 `/api/health` 保留）。

本机没有 Docker 时，可先用 `.\\.conda\\env\\python.exe .\\scripts\\verify_dockerfile.py` 做
COPY/上下文/运行时契约校验（不能替代真实 `docker build`）。

正式部署、密钥配置、回滚见 [部署手册](docs/部署手册.md)。

### 2.5 打包 Windows 客户端

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1 -Clean -Zip
```

产物 `dist/DanmakuPet/`（onedir，约 150 MB）只包含客户端：

```text
DanmakuPet.exe
_internal/           运行时、PySide6、本地状态页、小人素材
.env.example         仅非敏感占位
```

`.env`、`temp/`、`assets/` 不会进 zip；代码里也没有任何模型 Key。开播前配置见
[开播手册](docs/开播手册.md)。

### 2.6 兼容入口

```powershell
python -m danmaku_pet cloud ...     # 等价 python -m danmaku_pet.cloud
python -m danmaku_pet client ...    # 等价 python -m danmaku_pet.client
python -m danmaku_pet --no-desktop  # 旧写法，等同 client
```

---

## 3. 共享协议（阶段 2）

跨端只允许 import `danmaku_pet.shared`：云端不 import client，客户端不 import cloud/decision。

### 3.1 Event

```text
source, user, text, gift_name, gift_count, timestamp, event_id
room_id, message_id, message_type(comment|gift), user_id, gift_id
```

- `message_type` 未指定时按 `source`/`gift_name` 推导，旧的 `Event(source="gift_sim", gift_name="玫瑰")` 仍然可用。
- `display_text` 负责「送出 X × N」的展示文本；`to_llm_dict()` 不把 room/id 传给模型。

### 3.2 ActionPlan

```json
{
  "reply": "不超过 140 个汉字",
  "emotion": "happy | sad | surprised | shy | angry | neutral",
  "motion": "idle | nod | wave | jump | spin | shake_head | shy | sad | surprised",
  "priority": "normal | high",
  "interrupt": false,
  "min_hold_ms": 0
}
```

`min_hold_ms` 是礼物剧本的最短动作保持提示；真实语音时长仍由本地 EdgeTTS 测量后决定。

### 3.3 PerformCommand / PerformResult

```text
PerformCommand: protocol_version, command_id, room_id, event, plan,
                created_at, expires_at, read_aloud
PerformResult:  command_id, room_id, status, speech_ms,
                error_code, error_message, client_id
```

状态机：`accepted → started → completed | interrupted | expired | error`。

- **command_id 确定性生成**：`sha1(room_id + message_type + message_id || event_id)`。
  平台重试会换 `event_id` 但保留 `message_id`，因此同一个弹幕的指令 ID 不变，本地去重后不会重复表演。
- **expires_at 绝对时间**：普通评论默认 8 秒（`COMMENT_EXPIRES_SECONDS`），礼物默认 30 秒
  （`GIFT_EXPIRES_SECONDS`）。客户端重连后收到过期评论只回 `expired`，不会补演旧弹幕。
- **协议版本**：当前 v1；版本不兼容时握手直接报错，不做静默降级。

### 3.4 去重

| 位置 | 键 | 默认 TTL |
| --- | --- | --- |
| 平台推送入口 | `message_id`，缺失时用受控复合键（room/user/text/gift/秒桶） | 600s |
| 云端下发 | `command_id` | 600s |
| 本地执行 | `command_id` | 600s |
| 本地队列 | 单实例、FIFO；礼物抢占 | — |

---

## 4. 云端决策（阶段 3）

- 每个 `room_id` 一份 `RoomState`：上下文、待回复、统计、当前指令、自动回复/暂停，存在 Redis（dev 用内存）。
- 普通评论：请求内做内容检查 →（自动回复开启时）DeepSeek → JSON 校验 → `PerformCommand`。
- 礼物：**绕过模型**，直接走高优先级剧本（`priority=high`、`interrupt=true`），由客户端打断当前表演。
- 自动回复**默认关闭**（`AUTO_REPLY=false`）：新弹幕进入「待回复」，由主播在管理端手动选择；礼物不受此开关影响。
- 单房间单动作由**客户端**强制（云端只负责下发，不等回执）；HTTP 请求内完成决策+下发，不把工作留给后台 worker。
- 状态切换由客户端回执 `/ws/client` 更新 Redis；换实例后下一个请求从 Redis 继续。
- 云端**不碰**音频设备和桌面小人，也不计算真实语音时长。

### 4.1 内容安全

- 评论/昵称：先做本地清洗、长度/敏感词/提示词注入检查，再调用抖音小程序
  `POST /api/v2/tags/text/antidirt`（`X-Token` 为 `getAccessToken` 获取的 access_token）同时检测弹幕和昵称；
- 任一字段 `hit=true`：整个事件在进入模型/TTS **之前丢弃**，所以恶意谐音昵称也不会被念出来；
- 检测接口不可用：默认 fail-closed 丢弃；显式配置 fail-open 时允许生成文本回复，但强制 `read_aloud=false`，仍不转语音；
- access_token 缓存在 Redis，避免多实例重复获取导致旧 token 提前失效；
- 回复：去掉 HTML 标记、敏感词命中替换为安全兜底话术、硬长度上限；
- 管理端可看到 `replies_sanitized`、`comments_flagged`、`content_security_blocked` 计数。

### 4.2 监控

`/api/status` 与管理端「网关与指标」提供：决策耗时、下发耗时、客户端开始执行耗时、表演时长、各状态计数、
各房间状态、丢弃、重复、超时。

---

## 5. 抖音官方接入（阶段 5）

所有官方接口路径都来自配置，默认空：

```dotenv
DOUYIN_OPENAPI_BASE_URL=
DOUYIN_TOKEN_PATH=
DOUYIN_TASK_START_PATH=
DOUYIN_TASK_STOP_PATH=
DOUYIN_TASK_STATUS_PATH=
DOUYIN_COMMAND_PUSH_PATH=
DOUYIN_COMMAND_INTERRUPT_PATH=
```

路径为空时对应调用会抛 `PlatformNotConfigured`，并在管理端/日志里给出明确提示；**不会**去猜接口地址。

- `POST /api/douyin/events/comment`、`/api/douyin/events/gift`：接收平台推送。
- 校验：`DOUYIN_VERIFY_MODE=header|hmac|disabled`（正式环境禁止 disabled）。
- 重复推送返回 200 + `duplicate: true`，避免平台无限重试；格式错误返回 422。
- 评论/礼物任务：管理端按钮或 `POST /api/douyin/tasks/start|stop`，失败按指数退避重试。
- 礼物匹配优先 `gift_id`（`GIFT_ID_MAP=giftId=嘉年华,giftId2=火箭`），名称只用于展示和旧剧本兼容。
- 正式下行是 `GATEWAY_MODE=douyin_cloud`；`wss` 只作为开发/备用，与正式链路完全隔离。

---

## 6. 本地执行（阶段 6）

```text
PerformCommand
  → EdgeTTS 合成（缓存）
  → pygame 测量真实音频时长
  → ActionAdapter 生成 VisualCue（hold = max(动作最短, 语音 + 300ms)）
  → 回 started（带 speech_ms）
  → dafeiyu 小人 + 音频播放
  → 回 completed / error
```

- 高优先级礼物到达时取消当前语音和动作，当前指令回 `interrupted`，只回执一次，再执行礼物。
- TTS 失败：记录 `tts_failed`，按设计执行**静音动作**，不阻塞队列。
- 桌宠 helper 失败：回 `error`（`visual_error`），页面显示最近错误。
- 去重/过期/排序/抢占都在 `client/dispatcher.py`；云端断线期间回执进入有界 outbox，重连后补发。
- 握手：`hello(token, client_id, room_id)` → `welcome(protocol_version, gateway_mode)`；心跳 8 秒，3 个周期无响应重连。
- 本地**不开入站端口**（状态页只监听 127.0.0.1），不存 DeepSeek Key。

### 6.1 三种通道

| `--transport` | 用途 | 说明 |
| --- | --- | --- |
| `websocket` | 开发 / 自建 WSS 备用 | 连 `/ws/client`，需要 CLIENT_TOKEN |
| `static` | 本地验收 | 从 JSONL 回放模拟云端指令，回执写同目录 `.results.jsonl` |
| `platform` | 正式抖音云网关 | 等平台给出客户端 SDK/启动参数；当前显式报错，不假装已连接 |

### 6.2 平台启动参数

平台最终传参格式待确认，因此采用「已知别名映射 + 未知键保留」：

```powershell
DanmakuPet.exe --launch room_id=123 --launch token=abc --launch plugin_version=1.2.3
```

`room_id / client_id / cloud_url / client_token` 会被映射；其他键出现在
`/api/status` 的 `platform_param_keys` 里。确认平台规范后只需扩展
`client/startup.py` 的 `LAUNCH_KEY_MAP`。

---

## 7. 安全与密钥边界

| 内容 | 位置 | 是否会进镜像/EXE/Git |
| --- | --- | --- |
| DeepSeek Key | 抖音云密钥管理 → 容器环境变量 | 否 |
| ADMIN_TOKEN / CLIENT_TOKEN | 抖音云密钥管理 | 否 |
| 平台 App Secret / 验签密钥 | 抖音云密钥管理 | 否 |
| 客户端 `.env` | 开播电脑本地 | 否（gitignore，打包排除） |
| `deploy/cloud.env` | 仅本机 docker compose | 否（gitignore） |

- 云端**没有任何写 Key 的接口**（`/api/settings/llm` 不存在）；人设可以改，密钥不行。
- 日志统一经过 `SecretMaskFilter`，令牌/密钥被替换为 `***`；uvicorn 访问日志默认关闭（URL 可能带令牌）。
- 观众昵称和弹幕文本默认**不写日志**（`LOG_VIEWER_DATA=false`）；排障确需时才显式打开。
- 回答「现在几点」使用 `DISPLAY_TIMEZONE`（默认 `Asia/Shanghai`），不受容器 UTC 影响。
- 管理接口与模拟接口分离：模拟评论/礼物、模拟循环、清空上下文在正式环境默认 404
  （`ENABLE_TEST_ENDPOINTS=false`）。
- 本地页面只显示非敏感信息；`public_status()` 不包含任何令牌。

---

## 8. API

### 云端（`python -m danmaku_pet.cloud`，默认 8000）

| 方法 | 路径 | 鉴权 | 作用 |
| --- | --- | --- | --- |
| GET | `/` | — | 云端管理端页面 |
| GET | `/v1/ping` | — | 抖音云存活性探针，固定 200 |
| GET | `/api/health` | — | 详细存活/版本/房间数/网关模式 |
| GET | `/api/status` | ADMIN | 完整快照（脱敏） |
| GET | `/api/rooms`、`/api/rooms/{room}` | ADMIN | 房间与客户端 |
| POST | `/api/rooms/{room}/events/manual` | ADMIN + 测试接口 | 模拟评论 |
| POST | `/api/rooms/{room}/events/gift` | ADMIN + 测试接口 | 模拟礼物 |
| POST | `/api/rooms/{room}/reply` | ADMIN | 回复待回复弹幕 |
| POST | `/api/rooms/{room}/interrupt` | ADMIN | 打断当前表演 |
| POST | `/api/rooms/{room}/pause` | ADMIN | 暂停/恢复插件 |
| POST | `/api/rooms/{room}/context/clear` | ADMIN + 测试接口 | 清空上下文 |
| POST | `/api/settings/auto-reply` | ADMIN | 自动回复开关（可指定房间） |
| POST | `/api/settings/persona` | ADMIN | 角色语气（写 Redis `dmp:persona`） |
| POST | `/api/simulator/start`、`/stop` | ADMIN + 测试接口 | 模拟循环 |
| GET/POST | `/api/douyin/tasks`、`/start`、`/stop` | ADMIN | 评论/礼物任务 |
| POST | `/api/douyin/events/comment`、`/gift` | 平台验签 | 官方推送入口 |
| WS | `/ws?token=` | ADMIN | 管理端实时事件 |
| WS | `/ws/client` | CLIENT_TOKEN | 客户端握手/指令/回执 |

### 本地（`python -m danmaku_pet.client`，默认 8787，仅 127.0.0.1）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/` | 本地诊断页 |
| GET | `/api/health` | 本地存活 |
| GET | `/api/status` | 连接/绑定/当前动作/音频/桌宠/错误 |
| POST | `/api/interrupt` | 一键停止当前表演 |
| POST | `/api/reconnect` | 重新连接云端 |
| POST | `/api/bind` | 修改 room_id / cloud_url（只写非敏感项） |
| GET | `/api/actions` | 动作映射表 |

---

## 9. 测试

```powershell
$env:TEMP = (Join-Path (Get-Location) "temp\pytest-temp")
.\.conda\env\python.exe -m pytest -q
```

当前：**293 项通过**。覆盖：

- 协议序列化、字段校验、版本不兼容、command_id 稳定性、DedupCache TTL；
- 房间隔离、评论走模型、礼物绕过模型并抢占、单房间单动作、重复消息、过期、暂停、内容安全；
- 云端鉴权、测试接口开关、平台推送验签/去重/422、客户端 WS 端到端（握手→指令→回执）；
- 官方字段适配、复合去重、任务重试、OpenAPI 重试与错误分类；
- 客户端指令去重/过期/排序/抢占/回执、TTS 失败降级、断线重连、静态回放；
- 日志脱敏（令牌/Key）与观众昵称默认不写日志；
- 部署文件与密钥边界（Dockerfile、.dockerignore、env 样例、requirements 拆分、打包入口）。

> 本机若用 pytest 的 `tmp_path` 遇到 `WinError 5`，是本机目录权限问题；测试统一用
> `temp/test-artifacts/`，不依赖 `tmp_path`。

---

## 10. 与改造计划对应

| 阶段 | 代码落点 |
| --- | --- |
| 0 平台能力确认 | 全部官方地址/路径可配置；模拟评论、礼物在 `inputs/` |
| 1 基线 | 原版本 tag `v0.1.0-baseline`；基线 87 项通过；本 README 记录结构 |
| 2 共享协议 | `shared/`、`tests/test_protocol.py` |
| 3 拆分 | `cloud/orchestrator.py`、`client/executor.py`、`execution/backend.py` |
| 4 Docker 后端 | `Dockerfile`、`run.sh`、`.dockerignore`、`deploy/`、`cloud/__main__.py`、`/v1/ping`、`/api/health`、日志脱敏 |
| 5 官方数据 | `douyin/`、`tests/test_douyin_*.py` |
| 6 客户端 | `client/`、`packaging/`、`web/client/` |
| 7 安全治理 | `content_safety.py`、`auth.py`、`metrics.py`、暂停/一键停止 |
| 8 测试 | `tests/` 293 项、`scripts/smoke_test.py`、`scripts/make_mock_commands.py` |

仍未完成/需平台确认：抖音云网关客户端通道（`--transport platform` 显式报错）、真实接口路径与字段、
礼物 ID 表、真实直播间联调。详见 [改造对照表](docs/改造对照表.md)。

---

## 11. 从旧版本迁移

1. **本地 `.env` 里的 `DEEPSEEK_API_KEY` 不再被客户端读取**。请把它移到抖音云密钥管理
   （或本地云端的 `.env.cloud`），然后从开播电脑 `.env` 里删掉。
2. 旧的管理页面（含「设置 Key」）已移除：云端人设/自动回复/模拟互动走云端管理端，本地只剩诊断页。
3. 启动方式变化：
   - 旧：`python -m danmaku_pet` 一个进程全包；
   - 新：`python -m danmaku_pet.cloud` + `python -m danmaku_pet.client`（旧命令仍可作为客户端启动）。
4. 旧文件 `danmaku_pet/server.py`、`orchestrator.py`、`inputs/douyin_webcast.py`、
   `web/app.js|index.html|styles.css` 已删除，替代物见目录结构。
5. 原版本可回滚：tag `v0.1.0-baseline`。

---

## 12. 文档

- [平台设置清单](docs/平台设置清单.md)：开放平台/抖音云需要人工完成的事项
- [抖音云规范对照](docs/抖音云规范对照.md)：逐条检查结果、已修项与仍需改造的无状态/后台任务差距
- [部署手册](docs/部署手册.md)：本地与抖音云部署、密钥、回滚
- [开播手册](docs/开播手册.md)：开播电脑、直播伴侣、音频、客户端参数
- [故障排查](docs/故障排查.md)：从现象到修复
- [改造对照表](docs/改造对照表.md)：计划条目 → 代码/测试，未完成边界

## 13. 许可证

代码以 **MIT** 发布，见 [LICENSE](LICENSE)。第三方依赖与角色素材许可见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

> 本仓库不包含：`vendor/dafeiyu/`（`scripts/fetch_visual.ps1` 下载）、`.conda/`、`.env`、
> `dist/`、`temp/`、`assets/tts_cache/`。
