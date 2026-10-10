# NotDND

一个**完全原创、可商用**的 AI 跑团网页应用。规则系统为「**棱镜 PRISM**」
（MIT，PRISM Authors），AI 担任**导引者**（Conductor），纯文本交互，无图片资源；
面向单机 / 局域网「多人轮流共用一台设备」的桌边场景。

> ⚠️ **仅限本机 / 局域网使用。** 后端默认无账号系统、可监听 `0.0.0.0`，
> **请勿暴露到公网。**

## 怎么跑

前置：Python 3（标准库即可，无第三方运行时依赖）。

```bash
python3 notdnd_web.py          # 默认监听 0.0.0.0:8600
```

浏览器打开 `http://127.0.0.1:8600/` 即开始游玩。手机 / 平板与本机同一局域网，
访问 `http://<本机IP>:<端口>/`。

常用环境变量（都有默认值，不设也能跑）：

| 变量 | 作用 | 默认 |
|---|---|---|
| `NOTDND_PORT` | 监听端口（回退通用 `PORT`） | `8600` |
| `NOTDND_HOST` | 监听地址 | `0.0.0.0` |
| `NOTDND_SAVE` | 存档目录 | `web-saves/`（本地运行物，不入库） |

**离线可玩**：规则会话核心在本地结算，不配 AI 也能完整跑团。

**AI 导引者（可选）**：在仓库根放一个 `.env`（或用环境变量），只认
`BASE_URL` / `MODEL` / `API_KEY` 三个键（环境变量里已有的值优先）。
**`.env` 不入库**——只写上面这三个变量名，值自己填；没有它整个应用照常工作。

## 结构取舍

- **Python 3 标准库后端 + 原生 HTML/CSS/JS 前端**：零第三方运行时依赖、
  无框架、无构建步骤。跑团应用的寿命以年计，少一层依赖就少一类腐化；
  拉下来 `python3 notdnd_web.py` 就能跑。任何要引入运行时依赖的改动
  **单独开 PR** 说明理由（见 [`AGENTS.md`](AGENTS.md) 铁律 2）。
- **`docs/` → `data/`**：[`docs/`](docs/) 是「棱镜 PRISM」的原创规则与剧本源文档
  （**可修订**，走独立 Issue / PR）；[`data/`](data/) 存放由它整理出的 JSON，
  **「加文件即加内容」**：目录布局为 `data/system/`（跨世界规则）、
  `data/worlds/`（世界模组）、`data/scenarios/`（剧本）、
  `data/random_tables/`（随机表）、`data/atlas/`（地图词典）。
  统一格式、canonical 键与别名、`source` 溯源口径见 [`data/FORMAT.md`](data/FORMAT.md)。
- **「规则会话核心」与「AI 导引者」分属独立模块**（`prism_core.py` /
  `prism_guide.py`）：导引者是可选模块，import 失败不影响离线游玩。

## 测试

零第三方**运行时**依赖（测试期仅允许 `websocket-client`）：

```bash
bash tests/run_all.sh            # 全量回归：收集 tests/test_*.py 串行跑（12 个脚本）
python3 tests/validate_data.py   # 数据校验：data/ 下全部 JSON（79 个文件）
bash tests/content_firewall.sh   # 内容防火墙：禁止路径 / 品牌标识 / 密钥 / 隐私
```

CI 设四道门（`.github/workflows/ci.yml`）：语法门（`py_compile`）、回归门
（`run_all.sh`）、内容防火墙门、提交邮箱门（作者邮箱一律 GitHub noreply）。

写测试 / 跑测试的约定（随机断言、服务起停、优雅跳过等）：见
[`tests/README.md`](tests/README.md)。

## 已知边界

- **仅本机 / 局域网**：后端无鉴权、无账号系统，存档落在服务端 `web-saves/`。
  **请勿暴露到公网。**
- **AI 导引者是可选的**：不配置 `.env` / 环境变量时离线游玩完全可用；
  `prism_guide.py` import 失败时网页层照常工作，导引路由返回固定兜底。
- 纯文本交互，无图片资源；前端骨架（M4）已落地，正式 UI 仍在迭代。

## 许可

代码与规则内容均以 **MIT** 发布；许可与署名见 [`LICENSE`](LICENSE) 与 [`NOTICE`](NOTICE)。

## 协作

开工前请先读 [`AGENTS.md`](AGENTS.md)（角色分工、三条铁律、Issue 循环、审查格式）。
