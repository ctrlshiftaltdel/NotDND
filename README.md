# NotDND

> **让世界记住你的选择。**

NotDND 是一款 AI 驱动的跑团与持久世界模拟器。你用自然语言决定行动，由 **PRISM** 负责规则裁决、AI **导引者**（Conductor）负责叙事；你的选择会改变人物、关系与世界，而不只是生成下一段故事。

**一个系统，三种世界。** 从剑与魔法、现代都市，到星际殖民——选一个世界，开始你的冒险。

## 开始游玩

需要 Python 3，无需构建前端：

```bash
python3 notdnd_web.py
```

浏览器打开 `http://127.0.0.1:8600/`。同一局域网的手机或平板也可以访问运行设备的 IP 地址。

常用环境变量（都有默认值，不设也能跑；变量名与示例见 [`.env.example`](.env.example)）：

| 变量 | 作用 | 默认 |
|---|---|---|
| `NOTDND_HOST` | 监听地址 | `0.0.0.0` |
| `NOTDND_PORT` | 监听端口（回退通用 `PORT`） | `8600` |
| `NOTDND_SAVE` | 存档目录 | `web-saves/`（本地运行物，不入库） |

AI 导引者是可选的：配置 `.env` 中的 `BASE_URL`、`MODEL`、`API_KEY` 即可；没有 AI 配置时，规则核心仍可离线运行。

> ⚠️ **安全提示：** 仅限本机 / 可信局域网使用——当前无账号与鉴权，请勿暴露到公网。

## 数据层

`docs/` 是 PRISM 的原创规则与剧本源文档（可修订）；`data/` 存放由它整理出的 JSON——**加文件即加内容**（`data/system/` 跨世界规则、`data/worlds/` 世界模组、`data/scenarios/` 剧本、`data/random_tables/` 随机表）。统一格式与 `source` 溯源口径见 [`data/FORMAT.md`](data/FORMAT.md)；零依赖校验器：`python3 tests/validate_data.py`。

## 了解项目

- [游戏设计文档（GDD）](GDD.md) · 产品目标与世界模拟原则
- [UI 设计规范](UI-DESIGN.md) · 信息架构与交互约定
- [三种世界主题设计](UI-WORLD-THEMES.md) · 主题视觉规范
- [规则与剧本源文档](docs/) · 可组合的世界内容
- [协作与开发规范](AGENTS.md) · 角色分工、三条铁律、Issue 循环
- [改造者交接（HANDOVER.md）](HANDOVER.md) · 结构取舍与踩坑清单
- [测试约定](tests/README.md) · 怎么跑、怎么写测试

**许可：** MIT。详见 [LICENSE](LICENSE) 与 [NOTICE](NOTICE)。
