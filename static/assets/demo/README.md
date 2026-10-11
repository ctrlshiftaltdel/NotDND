# 测试样张与视觉参考稿

这里的文件**只供视觉审查和加载状态试验**。它们不是正式世界资产：

- 不进资产清单，运行时页面（`static/index.html`）不引用它们。
- 不代表任何已有剧本、地点或角色。文件名里的 `demo` 就是这个意思。
- 界面框架（边框、按钮、浮层、图标）不依赖这些位图，断网也能画出来。
- 占位仍是几何图形。样张失败或缺失时，不把破图留给玩家。

## 样张

| 文件 | 类别 | 槽位示例（未注册） |
|---|---|---|
| `portrait-wayfarer.jpg` | 肖像 | `portrait/demo-wayfarer` |
| `scene-mist-bridge.jpg` | 场景 | `scene/demo-mist-bridge` |
| `item-prism-lens.jpg` | 物品 | `item/demo-prism-lens` |
| `place-cliff-station.jpg` | 地点 | `place/demo-cliff-station` |

画面是为本仓库画的原创插图，共用一套低饱和矿物色。不要把它们抄进 `data/` 或当成某个剧本的正式图。

## 参考稿

`refs/boards.html` 用真正的 `tokens.css` / `base.css` / `lobby.css` / `stream.css` 拼出目标画面，再用查询参数切换：

- `?board=lobby&theme=dark` 会话列表
- `?board=play&theme=dark` 主画面（含目标态输入条）
- `?board=aux&theme=dark` L2 队伍辅助层
- `?board=states&theme=light` 控件状态与四张样张

`theme` 取 `dark` 或 `light`。应用本身不写 `data-theme`，仍跟随系统。

导出的 PNG 与本说明放在一起，方便在不能开页面时审查。参考稿不是运行时代替品：输入发送、存档改名、回合、移动、面板数据、语音和占位加载器仍由 U1–U6 实现。

## 组件（本单已接上的部分）

| 组件 | 落点 | 状态 |
|---|---|---|
| 页面底 / 桌面框 / 卡片 / 抽屉 / 辅助层 | `base.css` 的 `body`、`.view`、`.set-group`、`.drawer`、`.sheet` | 暗色默认；明色跟随系统。辅助层顶部有把手和强调色边；宽屏用外边距居中，避免和滑入动画抢 `transform` |
| 按钮 | `.btn` / `.btn-primary` / `.btn-ghost` | 悬停、按下、焦点环、`:disabled`、`aria-disabled`、`aria-busy`（静态三点）、`.is-error`。最小高度 44px |
| 图标按钮 / 分段 / 开关 / 标签页 | `.ic`、`.seg`、`.switch`、`.tab` | 热区至少 44px。分段控件的焦点环画在内侧，避免被裁切。开关的焦点环在滑轨上 |
| 文本框与输入条外壳 | `input` / `textarea`、`.composer` | 错误用 `aria-invalid`。发送逻辑不在本单 |
| 状态点 | `.status-dot` 加 `data-state="partial\|on\|danger"` | 样式已有。顶栏落点要等导引者状态接线，避免一颗不读接口的点误报 |
| 错误卡 / 提示条 | `.alert`、`.toast`、`.toast.is-error` | 危险色只作边，正文仍用 `--text-1` |
| 占位 | `.ph` 加 `data-state="empty\|loading\|failed"` | 几何块，不请求图片。场景卡里的 empty / failed 高度为 0。加载脚本在 U6 |
| 图标 | `#i-prism` 以及原有 sprite | 描边 1.75、圆头。品牌行使用棱镜标记。不用 emoji 当功能图标 |

令牌名与 `UI-DESIGN.md` §3.2 的色值一致。`--text-3` 仍只放在 `--bg-0` / `--bg-1` 上；占位符里的位 id 用 `--text-2`，因为底是 `--bg-2`。
