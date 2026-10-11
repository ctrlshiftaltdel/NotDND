# 世界 / 故事资产的落点

这里放**世界与故事**的视觉资产（角色肖像、场景图、物品图、地点图），
由 [`static/assets.json`](../assets.json) 这份清单把它们挂到占位符上
（`UI-DESIGN.md` §4「视觉占位框架」）。

今天**没有任何正式资产**：清单是空表，界面用几何占位（`.ph`）画出来。
纯文本照样可玩——占位是增益，不是通路；缺一张图不阻塞任何流程。

## 文件口径

| 项 | 口径 |
|---|---|
| 目录布局 | `<类别>/<实体 id>.<扩展名>`，例：`scene/loc-02.webp` |
| 类别 | `portrait`（角色）/ `scene`（场景）/ `place`（ATLAS 地点）/ `item`（物品） |
| 命名 | 全小写 ASCII 字母 + 数字 + `-`，与 `AGENTS.md` §7 的文件命名口径一致 |
| 扩展名 | 首选 `.webp`；`.jpg` / `.png` 也认（服务端按扩展名给 Content-Type） |
| 尺寸上限 | 肖像 512×512；场景与地点 1280×720（16:9）；物品 512×512。单文件 ≤ 400 KB |
| 外链 | **不允许**：清单只接受同源路径，外链会被加载器丢弃（`UI-DESIGN.md` §0.2 零外链） |

## 放进一张图（不必改代码）

1. 把文件按上面的命名放进本目录，例：
   `static/assets/place/emberfall/cliff-station.webp`；
2. 在 `static/assets.json` 里加一行：
   `"place/emberfall/cliff-station": "assets/place/emberfall/cliff-station.webp"`；
3. 刷新页面即可——加载器按 `data-slot` 查表：表里没有 → 几何占位；
   有 → 挂 `src`，`load` → 可见，`error`（文件不在 / 坏了）→ **退回几何占位
   加角落失败点**，不留破图图标。

不必改组件、不必重新构建、不必改 `data/`。清单形状见下。

## 清单形状

`static/assets.json` 是**单个 JSON 对象**：键是资产位（`<类别>/<实体 id>`），
值是**相对 `static/` 的路径**（以 `/` 开头的同源路径也接受）。

```json
{
  "portrait/npc-01": "assets/portrait/npc-01.webp",
  "scene/loc-02": "assets/scene/loc-02.webp",
  "place/emberfall/cliff-station": "assets/place/emberfall/cliff-station.webp"
}
```

空表 `{}` 是**合法状态**：表示一个位都没登记，界面全走几何占位。
非字符串值、空值与跨源路径会被加载器丢弃（宁可占位，也不引外链）。

## 接入点（哪里会用到这些位）

| 位置 | 资产位 | 未提供资产时 |
|---|---|---|
| 队伍面板 · 角色卡 | `portrait/<角色 id>` | 56px 几何方块（标题行最前） |
| 场景卡 | `scene/<剧本>-<地点>` | **高度塌缩为 0**，不留空白推叙事 |
| 世界面板 · 当前位置 | `place/<world_key>/<place_id>` | 宽幅几何块（内容最上面） |

叙事流、存档列表与行动按钮**保持纯文本**：那里不放占位，也不放画面。

## 与 `demo/` 的区别

本目录下的 `demo/` 是**隔离的测试样张与视觉参考稿**（`GDD-BASELINE.md` §2
「D2 实施边界」的第 3 类）：只供视觉审查与加载状态试验，**不登记进清单**，
运行时页面不引用它们。它们不代表任何已有的剧本、地点或角色。
