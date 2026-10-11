/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U6：视觉占位框架（世界 / 故事资产）
   UI-DESIGN.md §4：三态占位符 + `data-slot` 契约 + 资产清单加载器。

   契约（§4.3）：
     · 标记：`<figure class="ph" data-slot="scene/yunji-loc-02" data-state="empty">`；
     · **CSS 只认 `data-state`**（几何底座在 css/base.css，U6 的增量在
       css/placeholder.css）；**JS 只改 `data-state` 与 `<img>` 的 src**——
       不写行内样式、不加删 class，将来换加载器（`<img>` → `<picture>`）
       不动样式。
     · `data-state` 四值：`empty`（未提供：清单里没有这个位）/ `loading`
       （已挂 src、尚未解码）/ `ready`（解码完成，画面可见）/ `failed`
       （加载失败：**退回 empty 外观** + 角落 --danger 小点，绝不出现破图图标）。
     · 整块 `aria-hidden`：占位与画面都只是装饰，语义由旁边的文字承担
       （与 §5.3 对装饰性图形的口径一致）。

   清单（§4.4）：`static/assets.json`，形状 `{ "<类别>/<实体 id>": "<相对路径>" }`。
   表里没有 → `empty`；表里有 → 设 src，`load` → `ready`，`error` → `failed`。
   仓库**不含**世界 / 故事图片（D2 实施边界）：这份表今天可以是空表，
   将来放进文件 + 登记一行即可用，不必改组件、不必重新构建、不必改 `data/`。

   接入点（§4.3）：本片不碰叙事流 / 存档列表 / 行动按钮（它们保持纯文本），
   只向宿主文件暴露**可选**装饰钩子——宿主渲染完调用一次，钩子不存在就跳过
   （core.js 场景卡 / panels.js 角色卡与世界面板）：
     · `phDecorateSceneCard()`             场景卡   → `scene/<剧本>-<地点>`
     · `phDecoratePortraits(box, party)`   角色卡   → `portrait/<实体 id>`
     · `phDecoratePlace(box, here)`        世界面板 → `place/<world_key>/<place_id>`
   场景卡在未提供资产时**高度塌缩为 0**（不留一块空白把叙事推下去）：
   那是 base.css 的 `.scene-card > .ph[data-state="empty"] { display: none }`，
   本片只负责把位算对、把状态写对。

   跨片共用件（esc / $ / onInit / ST …）见 js/core.js；本片的 `<link>` 由
   static/index.html 声明。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* ── 类别 → 空态中央那 1 个字的位名（§4.2「名称的 1–2 个字 + 一个几何字形」）
   用字与参考稿 static/assets/demo/refs/boards.html 一致：象 / 景 / 地 / 物。 */
var PH_MARK = { portrait: '象', scene: '景', place: '地', item: '物' };

/* ══════════════ 资产清单加载器 ══════════════
   表是同源静态文件，服务端按 .json 发给浏览器。读不到（404 / 断网 / 坏 JSON）
   **不报错**：占位本来就是「没有资产也能用」，全表按空表处理，页面照常。 */
var PH_MANIFEST_URL = '/assets.json';
var PH_MANIFEST = null;        // {slot: 同源路径}；null = 还没读到
var PH_MANIFEST_LOADING = false;

function phManifest() { return PH_MANIFEST || {}; }

async function phLoadManifest() {
  if (PH_MANIFEST_LOADING) return;
  PH_MANIFEST_LOADING = true;
  try {
    var res = await fetch(PH_MANIFEST_URL, { headers: { Accept: 'application/json' } });
    if (!res.ok) throw new Error('HTTP ' + res.status);
    PH_MANIFEST = phCleanManifest(await res.json());
  } catch (e) {
    PH_MANIFEST = {};          // 读不到 = 一个位都没登记
  }
  PH_MANIFEST_LOADING = false;
  phApplyAll();                // 清单迟到：已画出的占位按新表重算一遍
}

/* 清一遍人手写的表：只留「字符串 + 同源路径」，其余（外链、空值、怪类型）
   一律丢掉——零外链是硬约束（§0.2），清单写错时宁可走 empty。 */
function phCleanManifest(raw) {
  var out = {};
  if (!raw || typeof raw !== 'object') return out;
  Object.keys(raw).forEach(function (slot) {
    var path = phSameOriginPath(raw[slot]);
    if (path) out[String(slot)] = path;
  });
  return out;
}

/* 路径口径：相对 static/（清单所在目录）解析，只接受同源；外链返回空串。
   `new URL` 的 base 必须是**绝对** URL，所以先把清单地址对页面地址绝对化
   （否则相对路径全部抛错 → 整表被丢掉，静默变空清单）。 */
function phSameOriginPath(v) {
  if (typeof v !== 'string' || !v.trim()) return '';
  var url;
  try {
    url = new URL(v.trim(), new URL(PH_MANIFEST_URL, location.href));
  } catch (e) {
    return '';
  }
  if (url.origin !== location.origin) return '';
  return url.pathname + url.search;
}

/* ══════════════ 三态占位符 ══════════════ */

/* 位名 / 位 id：中央写类别的 1 个字，角落小字写实体 id（slot 的最后一段）。 */
function phSlotParts(slot) {
  var parts = String(slot || '').split('/');
  return { kind: parts[0] || '', id: parts[parts.length - 1] || '' };
}

/* 组装一张占位符：**只有这里生成 HTML**，之后 JS 只改 data-state 与 src。
   `ratio` 传 'wide'（场景 / 地点）或 'square'（肖像）；不给则用 base.css 的 1:1。 */
function phFigureHTML(slot, ratio) {
  var p = phSlotParts(slot);
  return '<figure class="ph" aria-hidden="true" data-slot="' + esc(slot) + '"' +
    ' data-state="empty"' + (ratio ? ' data-ratio="' + esc(ratio) + '"' : '') + '>' +
    '<span class="ph-mark">' + esc(PH_MARK[p.kind] || '·') + '</span>' +
    '<span class="ph-id">' + esc(p.id) + '</span>' +
    '</figure>';
}

function phState(fig, state) {
  if (fig.getAttribute('data-state') !== state) fig.setAttribute('data-state', state);
}

/* 把一张占位接到清单上。幂等：同一个 figure 重算多次不会重复建 <img>、
   也不会重放骨架动画；状态只经 phState() 落 data-state，画面只经 img.src 挂载。 */
function phMount(fig) {
  var slot = fig.getAttribute('data-slot') || '';
  var path = slot ? phManifest()[slot] : '';
  if (!path) { phState(fig, 'empty'); return; }
  if (fig.__phSrc === path) return;      // 同一个资产已挂过：不重设 src
  fig.__phSrc = path;

  var img = fig.querySelector('img');
  if (!img) {
    img = document.createElement('img');
    img.setAttribute('alt', '');         // 装饰：语义在旁边的文本里
    img.setAttribute('decoding', 'async');
    // load / error **只改 data-state**：可见性与失败点的样式全在 CSS。
    img.addEventListener('load', function () { phState(fig, 'ready'); });
    img.addEventListener('error', function () { phState(fig, 'failed'); });
    fig.appendChild(img);
  }
  phState(fig, 'loading');
  img.src = path;
}

/* 把画面里已有的占位全部重算（清单到位后调用）。 */
function phApplyAll(root) {
  var scope = root || document;
  Array.prototype.slice.call(scope.querySelectorAll('.ph[data-slot]')).forEach(phMount);
}

function phFirstPh(host) {
  if (!host) return null;
  var kids = host.children || [];
  for (var i = 0; i < kids.length; i++) {
    if (kids[i].classList && kids[i].classList.contains('ph')) return kids[i];
  }
  return null;
}

/* 接入点的公共动作：容器里**最前面**那张占位，按位复用 / 换位 / 撤位。
   位为空（数据里没有可用的 id）时不加占位——没有位就没有资产可挂。 */
function phPlace(host, slot, ratio) {
  if (!host) return null;
  var fig = phFirstPh(host);
  if (!slot) {
    if (fig) fig.remove();
    return null;
  }
  if (fig && fig.getAttribute('data-slot') === slot) {
    phMount(fig);
    return fig;
  }
  if (fig) fig.remove();
  host.insertAdjacentHTML('afterbegin', phFigureHTML(slot, ratio));
  fig = phFirstPh(host);
  if (fig) phMount(fig);
  return fig;
}

/* ── 槽位口径（§4.3）──────────────────────────────────────
   槽位命名一律全小写 ASCII + `-`（同 AGENTS.md §7 的文件命名）：算不出
   ASCII 位时返回空串，调用方据此**不加**占位——界面照常纯文本。 */
function phSlug(v) {
  return String(v == null ? '' : v).trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64);
}

/* 场景卡：`scene/<剧本>-<地点>`（例 `scene/yunji-loc-02`），对应剧本地点 id。
   今天整局视图还没有 `scenario.id`（UI-DESIGN §7 的字段缺口），算不出就
   不加占位——场景卡本来就该在没有资产时塌缩为 0。 */
function phSceneSlot() {
  var sc = (ST && ST.scenario) || {};
  var scene = (ST && ST.scene) || {};
  var a = phSlug(sc.id || sc.key || sc.slug);
  var b = phSlug(scene.location_id || scene.id || scene.slug);
  return (a && b) ? ('scene/' + a + '-' + b) : '';
}

/* 角色卡：`portrait/<实体 id>`（§4.3 队伍面板）。没有 id 的单位不加位。 */
function phPortraitSlot(unit) {
  var id = phSlug(unit && unit.id);
  return id ? ('portrait/' + id) : '';
}

/* 世界面板 · 当前位置：`place/<world_key>/<place_id>`（与 ATLAS 的 place id 对齐）。 */
function phPlaceSlot(here) {
  var world = phSlug(here && here.world_key);
  var place = phSlug(here && here.place_id);
  return (world && place) ? ('place/' + world + '/' + place) : '';
}

/* ── 宿主钩子（core.js / panels.js 渲染完各调一次；不存在时宿主跳过）── */

/* 场景卡：一块 16:9 横幅，摆在卡片最上面（crumb 之前）。 */
function phDecorateSceneCard() {
  phPlace($('#sceneCard'), phSceneSlot(), 'wide');
}

/* 角色卡：每张单位卡标题行最前一个方形肖像位。
   单位列表按渲染顺序配对（unitCard 对空值返回空串，所以先滤掉空值）。 */
function phDecoratePortraits(box, party) {
  if (!box) return;
  var units = (party || []).filter(Boolean);
  Array.prototype.slice.call(box.querySelectorAll('.unit-card')).forEach(function (card, i) {
    phPlace(card.querySelector('.unit-head') || card, phPortraitSlot(units[i] || {}), 'square');
  });
}

/* 世界面板：当前位置的宽幅地点位，摆在面板内容最上面。 */
function phDecoratePlace(box, here) {
  phPlace(box, phPlaceSlot(here), 'wide');
}

/* ══════════════ 启动 ══════════════ */
onInit(function () { phLoadManifest(); });
