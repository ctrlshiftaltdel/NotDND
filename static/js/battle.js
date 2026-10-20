/* ═══════════════════════════════════════════════════════════
   棱镜 · 战棋视图（M13-B12，Issue #427）

   把新式战斗（#426 服务端闭环）呈现为可操作的六角格战棋：

     · 只发送**行动意图**（move / attack / interact / end_turn / flee）；
       位置、命中、伤害、AP、物件状态全部以服务器 snapshot 为准；
     · 画板是 SVG 几何占位（尖顶类轴向六角，#433/#444 冻结口径：
       中心坐标 x = S(q + r/2)、y = S(√3/2)r；hex-line 并列选格按
       docs/system/02c 第五节的有理数采样实现——**只做预览**，
       不替代服务器裁定）；
     · 相同 (q,r) 不同 surface 各画一条「层带」，带首标注 surface 与
       基准层级；层级差用台阶侧壁 + 高度数值表达，不只靠颜色；
     · 复用 core 的视图系统（.view + .is-on）、写动作单一在途守卫、
       esc() 与 toast；无框架、无构建步骤。

   素材槽位：单位 / 物件 / 地形均带 data-asset-* 语义属性
   （data-asset-kind / data-asset-id），后续正式素材按属性替换，
   不需要改结构。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* ── 模块状态（渲染只读，写只经各 action）────────────────── */
var BT = {
  view: null,        // 最近一次服务器 battle view（唯一事实来源）
  loaded: false,     // 已经拿到过至少一次 view
  selUnit: '',       // 选中的单位 id（'' = 未选）
  selCell: '',       // 选中的格子 cell_id（'' = 未选）
  selObject: '',     // 选中的物件 id（'' = 未选）
  zoom: 1,
  hasBattle: false   // 最近已知：是否存在战斗（入口按钮文案用）
};

/* ── 几何常量（#433 / #444 冻结口径的渲染参数）────────────────
   S = 相邻格中心距离（px）；尖顶类：R = S/√3（外接圆半径）。
   E = 每一级层高的抬升像素（纯视觉）。 */
var HEX_S = 46;
var HEX_R = HEX_S / Math.sqrt(3);
var ELEV_STEP = 14;
var BAND_GAP = 26;

var SIX_DIRS = [[1, 0], [1, -1], [0, -1], [-1, 0], [-1, 1], [0, 1]];

function $b(sel) { return document.querySelector(sel); }

/* ── 小工具 ─────────────────────────────────────────────── */

function battleEsc(v) { return esc(v); }

/* 幂等键：ACTION_IDEM_KEY_RE 要求 ^[A-Za-z0-9_-]{8,64}$。 */
function battleIdemKey() {
  var raw = (window.crypto && window.crypto.getRandomValues)
    ? Array.from(window.crypto.getRandomValues(new Uint8Array(16)))
    : Array.from({ length: 16 }, function () { return Math.floor(Math.random() * 256); });
  var key = '';
  for (var i = 0; i < raw.length; i++) key += (raw[i] % 36).toString(36);
  return 'b' + key;   // 17 位，落在 8–64 内
}

/* axial → cube（#433 第 1 条） */
function axialCube(q, r) { return { x: q, y: -q - r, z: r }; }

/* 六角距离（#444 既有口径：max(|dq|,|dr|,|dq+dr|)） */
function hexDist(aq, ar, bq, br) {
  var dq = bq - aq, dr = br - ar;
  return Math.max(Math.abs(dq), Math.abs(dr), Math.abs(dq + dr));
}

/* hex-line 并列选格预览（docs/system/02c 第五节 6 条，有理数采样）。
   与服务端 tactical_hex.hex_line 同口径：采样点乘 N 保持整数，在
   floor/ceil 组合里取「平方误差最小，并列按 (x, z, y) 字典序」的合法
   立方点。返回不含起点的 axial 数组。仅用于 UI 预览。 */
function hexLine(aq, ar, bq, br) {
  var A = axialCube(aq, ar), B = axialCube(bq, br);
  var dx = B.x - A.x, dy = B.y - A.y, dz = B.z - A.z;
  var n = Math.max(Math.abs(dx), Math.abs(dy), Math.abs(dz));
  if (n === 0) return [];
  var out = [];
  for (var i = 1; i <= n; i++) {
    // 采样点 = A + (i/N)·d，三个分量分子为整数（分母 N）。
    var numX = A.x * n + dx * i, numY = A.y * n + dy * i, numZ = A.z * n + dz * i;
    var best = null, bestKey = null;
    var xs = [Math.floor(numX / n), Math.ceil(numX / n)];
    var ys = [Math.floor(numY / n), Math.ceil(numY / n)];
    var zs = [Math.floor(numZ / n), Math.ceil(numZ / n)];
    xs.forEach(function (x) {
      ys.forEach(function (y) {
        zs.forEach(function (z) {
          if (x + y + z !== 0) return;
          var ex = numX - x * n, ey = numY - y * n, ez = numZ - z * n;
          var err = ex * ex + ey * ey + ez * ez;
          var key = [err, x, z, y];
          if (bestKey === null || keyLess(key, bestKey)) {
            bestKey = key; best = { q: x, r: z };
          }
        });
      });
    });
    if (best) out.push(best);
  }
  return out;
}

/* 逐元素数值比较（数组的 < 会退化为字符串比较，不能用）。 */
function keyLess(a, b) {
  for (var k = 0; k < a.length; k++) {
    if (a[k] !== b[k]) return a[k] < b[k];
  }
  return false;
}

/* ── 战场索引（从 view 派生的只读投影）────────────────────── */

function bfIndex() {
  var view = BT.view;
  var idx = {
    cells: {},            // cell_id -> cell
    byQR: {},             // 'q,r' -> [cell…]（跨 surface）
    surfaces: [],         // [{id, baseElev, cells:[…]}]
    units: {},            // unit_id -> participant 行
    unitAt: {},           // cell_id -> unit_id
    objects: {},          // object_id -> instance
    objectsAt: {},        // cell_id -> [object_id…]
    links: []             // vertical_links
  };
  if (!view || !view.battlefield) return idx;
  var battlefield = view.battlefield;
  var bandOrder = {};
  (battlefield.cells || []).forEach(function (cell) {
    if (!cell || !cell.cell_id) return;
    idx.cells[cell.cell_id] = cell;
    var k = cell.q + ',' + cell.r;
    (idx.byQR[k] = idx.byQR[k] || []).push(cell);
    var sid = String(cell.surface_id || '');
    var band = bandOrder[sid];
    if (!band) { band = bandOrder[sid] = { id: sid, baseElev: null, cells: [] }; }
    band.cells.push(cell);
    var elev = (cell.elevation_level === undefined || cell.elevation_level === null)
      ? 0 : Number(cell.elevation_level);
    if (band.baseElev === null || elev < band.baseElev) band.baseElev = elev;
  });
  idx.surfaces = Object.keys(bandOrder).sort().map(function (sid) { return bandOrder[sid]; });
  (view.participants || []).forEach(function (row) {
    if (!row || !row.unit_id) return;
    idx.units[row.unit_id] = row;
    if (row.cell_id) idx.unitAt[row.cell_id] = row.unit_id;
  });
  (view.objects || []).forEach(function (obj) {
    if (!obj || !obj.object_id) return;
    idx.objects[obj.object_id] = obj;
    if (obj.cell_id) (idx.objectsAt[obj.cell_id] = idx.objectsAt[obj.cell_id] || []).push(obj.object_id);
  });
  idx.links = (battlefield.vertical_links || []).filter(function (l) {
    return l && l.from_cell_id && l.to_cell_id;
  });
  return idx;
}

/* bfIndex 每次渲染算一次，避免重复遍历；动作间失效。 */
var bfIndexCache = null;
function indexNow() {
  if (!bfIndexCache) bfIndexCache = bfIndex();
  return bfIndexCache;
}

/* 邻接可达格（abstract 预览口径：1 AP = 1 格，服务端 plan_move 重算）。
   只在本 surface 内取六邻格；blocks_movement / 已占格排除。 */
function reachableCells(fromCellId) {
  var idx = indexNow();
  var cell = idx.cells[fromCellId];
  if (!cell) return [];
  var occupied = {};
  Object.keys(idx.unitAt).forEach(function (cid) { occupied[cid] = true; });
  Object.keys(idx.objectsAt).forEach(function (cid) {
    (idx.objectsAt[cid] || []).forEach(function (oid) {
      var obj = idx.objects[oid];
      if (obj && obj.blocks_movement) occupied[cid] = true;
    });
  });
  return SIX_DIRS.map(function (d) {
    var k = (cell.q + d[0]) + ',' + (cell.r + d[1]);
    var rows = idx.byQR[k] || [];
    for (var i = 0; i < rows.length; i++) {
      var c = rows[i];
      if (String(c.surface_id) !== String(cell.surface_id)) continue;
      if (c.blocks_movement || occupied[c.cell_id]) continue;
      return c;
    }
    return null;
  }).filter(Boolean);
}

/* ── 视图入口（applyState 可选片钩子，与 campaign.js 同款）────── */

function renderBattleEntry() {
  var entry = $b('#battleEntry');
  if (!entry) return;
  var inPlay = (VIEW === 'v-play');
  entry.hidden = !inPlay;
  if (!inPlay) return;
  var btn = $b('#btnBattleEnter');
  if (!btn) return;
  btn.textContent = BT.hasBattle ? '回到战棋' : '进入战棋';
  btn.disabled = BT.busy;
}

/* ── 进入 / 刷新 ─────────────────────────────────────────── */

async function enterBattle() {
  if (BT.busy) return;
  BT.busy = true; renderBattleEntry();
  try {
    var view;
    try {
      var data = await api('/api/tactical/battle');
      view = data && data.battle;
    } catch (e) {
      // 没有进行中的战斗 → 起一场新的（服务端决定敌体 / 战场 / 先攻）。
      if (!/没有进行中的战斗/.test(String(e && e.message))) throw e;
      var started = await api('/api/tactical/start', {
        method: 'POST', body: {}, headers: { 'Idempotency-Key': battleIdemKey() }
      });
      view = started && started.battle;
      if (started && started.narrative) toast(started.narrative);
    }
    applyBattleView(view);
    showView('v-battle');
  } catch (e) {
    toast(String((e && e.message) || '进入战棋失败'));
  } finally {
    BT.busy = false; renderBattleEntry();
  }
}

async function refreshBattle() {
  try {
    var data = await api('/api/tactical/battle');
    applyBattleView(data && data.battle);
    return true;
  } catch (e) {
    var msg = String((e && e.message) || '刷新失败');
    // 只有「确实没有战斗」才退回叙事视图；网络 / 服务错误留在原地就地提示，
    // 不把渲染或网络抖动误判成「战斗已结束」。
    if (!/没有进行中的战斗/.test(msg)) {
      showBattleError(msg);
      if (VIEW === 'v-battle') toast(msg);
      return false;
    }
    BT.hasBattle = false; BT.view = null; BT.loaded = false;
    renderBattleEntry();
    if (VIEW === 'v-battle') {
      showView('v-play');
      toast(msg);
    }
    return false;
  }
}

/* 服务器快照落地：唯一的事实来源。重置派生选择与几何索引。 */
function applyBattleView(view) {
  BT.view = view || null;
  bfIndexCache = null;
  BT.loaded = !!view;
  BT.hasBattle = !!view;
  var alive = view && view.status === 'active';
  // 选中态跟随快照：默认选中当前行动的队伍单位。
  var idx = bfIndex();
  if (view && alive && view.active_unit_id && idx.units[view.active_unit_id]
      && idx.units[view.active_unit_id].party) {
    BT.selUnit = view.active_unit_id;
    BT.selCell = idx.units[view.active_unit_id].cell_id || '';
  }
  if (!alive) { /* 结束态保留用户手上的选择，面板只读 */ }
  renderBattleAll();
  renderBattleEntry();
}

/* ── 行动意图（唯一出口：POST /api/tactical/<op>）────────────── */

async function battleAction(op, body) {
  var view = BT.view;
  if (!view || view.status !== 'active' || BT.busy) return;
  BT.busy = true; renderBattleActions();
  var payload = Object.assign({}, body || {}, { expected_revision: view.state_revision });
  try {
    var data = await api('/api/tactical/' + op, {
      method: 'POST', body: payload, headers: { 'Idempotency-Key': battleIdemKey() }
    });
    // 成功：以服务器返回的 snapshot 更新（含推进后的敌方回合结果）。
    if (data && data.battle) {
      applyBattleView(data.battle);
    } else {
      await refreshBattle();
    }
    showBattleFeedback((data && data.narrative) || '');
  } catch (e) {
    await handleActionError(e, op);
  } finally {
    BT.busy = false;
    if (VIEW === 'v-battle') renderBattleAll();
    renderBattleEntry();
  }
}

async function handleActionError(e, op) {
  var msg = String((e && e.message) || '行动失败');
  // api() 抛出的错误信息是服务端 error 短语；拿不回结构化 reasons
  //（fetch 层只透出 message），但 409 stale_revision 的短语可识别。
  var stale = /战场状态已变|状态已变/.test(msg);
  showBattleError(msg);
  if (stale) {
    // revision 冲突：提示刷新并以服务器 snapshot 恢复，不覆盖。
    toast('战场状态已更新，已为你刷新');
    await refreshBattle();
  }
}

function showBattleFeedback(text) {
  var box = $b('#battleFeedback');
  if (!box) return;
  box.textContent = String(text || '');
  box.hidden = !text;
}

function showBattleError(text) {
  var box = $b('#battleError');
  if (!box) return;
  box.textContent = String(text || '');
  box.hidden = !text;
}

/* ═══════════════ 渲染 ═══════════════ */

function renderBattleAll() {
  if (!BT.loaded) return;
  renderBattleTop();
  renderBoard();
  renderBattleInfo();
  renderBattleTargets();
  renderBattleActions();
}

function renderBattleTop() {
  var view = BT.view;
  var idx = indexNow();
  var round = $b('#battleRound'), turn = $b('#battleTurn'), ap = $b('#battleAp');
  if (!round) return;
  var activeName = '?';
  var activeRowData = idx.units[view.active_unit_id];
  if (activeRowData && activeRowData.unit && activeRowData.unit.name) {
    activeName = activeRowData.unit.name;
  } else if (activeRowData) { activeName = activeRowData.unit_id; }
  var statusText = view.status === 'ended'
    ? ({ victory: '战斗胜利', defeat: '队伍全灭', fled: '队伍撤离' }[view.outcome] || '战斗结束')
    : '战斗进行中';
  round.textContent = '第 ' + (view.round || 1) + ' 回合 · ' + statusText;
  turn.textContent = '当前行动：' + battleEsc(activeName)
    + '（' + (view.turn_order || []).length + ' 名参战）';
  var myAp = view.turn_ap && view.active_unit_id ? view.turn_ap[view.active_unit_id] : null;
  ap.textContent = 'AP ' + (myAp === null || myAp === undefined ? '—' : myAp);

  var outcome = $b('#battleOutcome');
  if (outcome) {
    if (view.status === 'ended') {
      outcome.textContent = ({
        victory: '战斗胜利。可以返回叙事继续旅程。',
        defeat: '队伍全灭，这一程到此为止。',
        fled: '队伍脱离了战斗（没有获得经验）。'
      }[view.outcome] || '战斗结束。');
      outcome.hidden = false;
    } else { outcome.hidden = true; }
  }
}

/* ── SVG 画板 ───────────────────────────────────────────── */

function hexPoints(cx, cy, r) {
  var pts = [];
  for (var k = 0; k < 6; k++) {
    var a = Math.PI / 180 * (60 * k + 30);   // 尖顶类顶点：30°,90°,…,330°
    pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]);
  }
  return pts;
}

function ptsAttr(pts) {
  return pts.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' ');
}

/* 台阶侧壁：给朝向观察者（下半周）的边画「底 → 顶」挤出面。 */
var WALL_EDGES = [[5, 0], [0, 1], [1, 2], [2, 3]];   // 右下 / 下 / 左下 / 左

function wallQuads(basePts, topPts) {
  return WALL_EDGES.map(function (e) {
    return [basePts[e[0]], basePts[e[1]], topPts[e[1]], topPts[e[0]]];
  });
}

function renderBoard() {
  var host = $b('#battleBoard');
  if (!host) return;
  var view = BT.view;
  var idx = indexNow();
  if (!view || !view.battlefield) { host.innerHTML = '<p class="battle-empty">没有战场数据</p>'; return; }

  var zoom = BT.zoom || 1;
  // 每条 surface 一条层带：带内局部 y 不含带间偏移；带首给标签行。
  var bandTop = [];
  var cursorY = 0;
  var labelH = 22;
  idx.surfaces.forEach(function (band) {
    bandTop.push(cursorY);
    var rows = band.cells.map(function (c) { return c.r; });
    var maxY = Math.max.apply(null, rows);
    var minY = Math.min.apply(null, rows);
    var maxElev = Math.max.apply(null, band.cells.map(function (c) {
      return Number(c.elevation_level || 0);
    }));
    cursorY += labelH + (maxY - minY) * (HEX_S * Math.sqrt(3) / 2)
      + HEX_S * Math.sqrt(3) + maxElev * ELEV_STEP + BAND_GAP;
  });
  var totalH = Math.max(cursorY, HEX_S * 2);
  // 水平范围
  var minX = 0, maxX = 0;
  (view.battlefield.cells || []).forEach(function (c) {
    var x = HEX_S * (c.q + c.r / 2);
    if (x < minX) minX = x; if (x > maxX) maxX = x;
  });
  var pad = HEX_R + 8;
  var width = (maxX - minX) + pad * 2;
  var vbX = minX - pad;

  var out = [];
  out.push('<svg id="battleSvg" xmlns="http://www.w3.org/2000/svg" role="img"',
    ' aria-label="六角格战场：', battleEsc(String((idx.surfaces.length) || 0)),
    ' 个表面，', battleEsc(String((view.battlefield.cells || []).length)), ' 格"',
    ' viewBox="', vbX.toFixed(1), ' -', (ELEV_STEP * 3 + 6).toFixed(1), ' ',
    width.toFixed(1), ' ', (totalH + ELEV_STEP * 3).toFixed(1), '"',
    ' width="', (width * zoom).toFixed(0), '" height="', ((totalH + ELEV_STEP * 3) * zoom).toFixed(0), '">');

  // 逐带渲染
  idx.surfaces.forEach(function (band, bi) {
    var y0 = bandTop[bi];
    out.push('<g class="b-surface" data-surface="', battleEsc(band.id), '">');
    out.push('<text class="b-band-label" x="', vbX + 4, '" y="', y0 + 12, '">',
      '层 ', battleEsc(band.id), ' · 基准层级 H', battleEsc(String(band.baseElev || 0)), '</text>');
    band.cells.slice().sort(function (a, b) { return a.r - b.r || a.q - b.q; })
      .forEach(function (cell) { renderCell(out, cell, y0 + labelH, band.baseElev || 0); });
    out.push('</g>');
  });

  // 垂直连接（跨带连接器）：按 #446 形状（from/to cell_id）解析两端格。
  idx.links.forEach(function (link) {
    var from = idx.cells[link.from_cell_id], to = idx.cells[link.to_cell_id];
    if (!from || !to) return;
    var a = withBandY(from, idx), b = withBandY(to, idx);
    out.push('<line class="b-vlink" x1="', a.x.toFixed(1), '" y1="', a.y.toFixed(1),
      '" x2="', b.x.toFixed(1), '" y2="', b.y.toFixed(1), '">',
      '<title>垂直连接：', battleEsc(String(from.surface_id)), ' ↔ ',
      battleEsc(String(to.surface_id)), '</title></line>');
  });

  // 覆盖层：可达格 / 选中 / 路径
  renderOverlays(out, idx);

  // 单位令牌最后画（压在地形上）
  Object.keys(idx.units).forEach(function (uid) {
    renderUnit(out, idx.units[uid], idx);
  });

  out.push('</svg>');
  host.innerHTML = out.join('');
}

/* 由 cell 直接算全图坐标（带 y 偏移 + 层级抬升），垂直连接器用。 */
function withBandY(cell, idx) {
  var tops = computeBandTops(idx);
  for (var i = 0; i < idx.surfaces.length; i++) {
    if (String(idx.surfaces[i].id) === String(cell.surface_id)) {
      return cellCenter(cell, tops[i] + 22, idx.surfaces[i].baseElev || 0);
    }
  }
  return cellCenter(cell, 0, 0);
}

function computeBandTops(idx) {
  var tops = [];
  var cursorY = 0;
  var labelH = 22;
  idx.surfaces.forEach(function (band) {
    tops.push(cursorY);
    var rows = band.cells.map(function (c) { return c.r; });
    var maxY = Math.max.apply(null, rows);
    var minY = Math.min.apply(null, rows);
    var maxElev = Math.max.apply(null, band.cells.map(function (c) {
      return Number(c.elevation_level || 0);
    }));
    cursorY += labelH + (maxY - minY) * (HEX_S * Math.sqrt(3) / 2)
      + HEX_S * Math.sqrt(3) + maxElev * ELEV_STEP + BAND_GAP;
  });
  return tops;
}

/* 单格中心（带内局部坐标 + 层级抬升） */
function cellCenter(cell, bandY, baseElev) {
  var x = HEX_S * (cell.q + cell.r / 2);
  var y = bandY + (HEX_S * Math.sqrt(3) / 2) * cell.r
    - (Number(cell.elevation_level || 0) - (baseElev || 0)) * ELEV_STEP;
  return { x: x, y: y };
}

function renderCell(out, cell, bandY, baseElev) {
  var c = cellCenter(cell, bandY, baseElev);
  var elev = Number(cell.elevation_level || 0);
  var topPts = hexPoints(c.x, c.y, HEX_R);
  var basePts = hexPoints(c.x, c.y + elev * ELEV_STEP, HEX_R);
  var cid = battleEsc(cell.cell_id);
  var terrain = String(cell.terrain_id || 'open');
  var special = terrain !== 'open';
  var hazard = (cell.hazard_refs || []).length > 0;

  out.push('<g class="b-cell', cell.blocks_movement ? ' b-blocked' : '', '" data-cell-id="', cid, '"',
    ' data-asset-kind="terrain" data-asset-id="', battleEsc(terrain), '"',
    ' data-q="', cell.q, '" data-r="', cell.r, '" data-surface="', battleEsc(String(cell.surface_id || '')), '">');

  // 台阶侧壁（先画，压在顶面之下）
  if (elev > 0) {
    wallQuads(basePts, topPts).forEach(function (quad) {
      out.push('<polygon class="b-wall" points="', ptsAttr(quad), '"/>');
    });
  }
  // 底座虚线（可读的「这一格从这里抬起来」）
  out.push('<polygon class="b-base" points="', ptsAttr(basePts), '"/>');
  // 顶面
  out.push('<polygon class="b-top b-elev-', Math.min(elev, 3), special ? ' b-terrain' : '',
    hazard ? ' b-hazard' : '', '" points="', ptsAttr(topPts), '">',
    '<title>', cellTitle(cell), '</title></polygon>');
  // 阻挡 / 遮蔽记号（几何提示，不只靠颜色）
  if (cell.blocks_movement) {
    out.push('<path class="b-glyph-cross" d="M', (c.x - HEX_R * .38).toFixed(1), ' ', (c.y - HEX_R * .38).toFixed(1),
      ' L', (c.x + HEX_R * .38).toFixed(1), ' ', (c.y + HEX_R * .38).toFixed(1),
      ' M', (c.x + HEX_R * .38).toFixed(1), ' ', (c.y - HEX_R * .38).toFixed(1),
      ' L', (c.x - HEX_R * .38).toFixed(1), ' ', (c.y + HEX_R * .38).toFixed(1), '"/>');
  }
  if ((cell.blocks_los)) {
    out.push('<circle class="b-glyph-los" cx="', c.x.toFixed(1), '" cy="', (c.y - HEX_R * .55).toFixed(1),
      '" r="4"/><line class="b-glyph-los" x1="', (c.x - HEX_R * .55 - 3).toFixed(1), '" y1="',
      (c.y - HEX_R * .55 + 3).toFixed(1), '" x2="', (c.x + HEX_R * .55 + 3).toFixed(1), '" y2="',
      (c.y - HEX_R * .55 - 3).toFixed(1), '"/>');
  }
  if (hazard) {
    out.push('<path class="b-glyph-hazard" d="M', c.x.toFixed(1), ' ', (c.y + HEX_R * .62).toFixed(1),
      ' l-', (HEX_R * .3).toFixed(1), ',-', (HEX_R * .5).toFixed(1),
      ' h', (HEX_R * .6).toFixed(1), ' Z"/>');
  }
  // 高度数值（几何提示 2：数字）
  out.push('<text class="b-elev-label" x="', (c.x + HEX_R * .5).toFixed(1), '" y="',
    (c.y - HEX_R * .28).toFixed(1), '">H', elev, '</text>');
  // 出生点标记
  if (cell.spawnable) {
    out.push('<rect class="b-spawn" x="', (c.x - 4).toFixed(1), '" y="', (c.y + HEX_R * .18).toFixed(1),
      '" width="8" height="8" transform="rotate(45 ', c.x.toFixed(1), ' ', (c.y + HEX_R * .18 + 4).toFixed(1), ')">',
      '<title>出生点</title></rect>');
  }
  // 物件徽记（语义槽位：正式素材按 data-asset-id 替换）
  (indexCacheObjectsAt(cell.cell_id) || []).forEach(function (oid, i) {
    var obj = indexNow().objects[oid];
    if (!obj) return;
    var ox = c.x + (i - 1) * HEX_R * .5;
    var oy = c.y - HEX_R * .72;
    out.push('<g class="b-object', obj.blocks_movement ? ' b-obj-solid' : '', '" data-object-id="',
      battleEsc(oid), '" data-asset-kind="object" data-asset-id="', battleEsc(String(obj.definition_id || '')),
      '"><rect class="b-object-badge" x="', (ox - 8).toFixed(1), '" y="', (oy - 8).toFixed(1),
      '" width="16" height="16" rx="3"/><text class="b-object-text" x="', ox.toFixed(1), '" y="',
      (oy + 4).toFixed(1), '">', obj.category === 'hazard' ? '危' : '机', '</text>',
      '<title>', battleEsc(String(obj.definition_id || oid)), ' · ',
      battleEsc(String(obj.state || '')), '</title></g>');
  });
  out.push('</g>');
}

function indexCacheObjectsAt(cellId) {
  return indexNow().objectsAt[cellId] || null;
}

function cellTitle(cell) {
  var parts = ['(' + cell.q + ',' + cell.r + ') ' + String(cell.surface_id || '')];
  parts.push('高度 H' + Number(cell.elevation_level || 0));
  parts.push(String(cell.terrain_id || 'open'));
  if (cell.blocks_movement) parts.push('阻挡移动');
  if (cell.blocks_los) parts.push('遮蔽视线');
  if ((cell.hazard_refs || []).length) parts.push('危险：' + cell.hazard_refs.join('、'));
  if (cell.cover_grade) parts.push('掩体：' + cell.cover_grade);
  return parts.join(' · ');
}

function renderUnit(out, row, idx) {
  var cell = idx.cells[row.cell_id];
  if (!cell) return;
  var bandIdx = -1;
  idx.surfaces.forEach(function (band, i) {
    if (String(band.id) === String(cell.surface_id)) bandIdx = i;
  });
  if (bandIdx < 0) return;
  var tops = computeBandTops(idx);
  var c = cellCenter(cell, tops[bandIdx] + 22, idx.surfaces[bandIdx].baseElev || 0);
  var unit = row.unit || {};
  var isActive = BT.view && BT.view.active_unit_id === row.unit_id;
  var isSel = BT.selUnit === row.unit_id;
  var cls = row.party ? 'b-unit b-unit-party' : 'b-unit b-unit-enemy';
  if (unit.downed || unit.dead) cls += ' b-unit-down';
  var name = String(unit.name || row.unit_id);
  var vitality = Number(unit.vitality || 0);
  var maxV = Number(unit.max_vitality || 0);
  var frac = maxV > 0 ? Math.max(0, Math.min(1, vitality / maxV)) : 0;
  var circ = 2 * Math.PI * HEX_R * .52;
  out.push('<g class="', cls, '" data-unit-id="', battleEsc(row.unit_id),
    '" data-asset-kind="unit" data-asset-id="', battleEsc(String(unit.name || row.unit_id)), '"',
    ' data-cell-id="', battleEsc(row.cell_id), '">');
  if (isActive) {
    out.push('<circle class="b-active-ring" cx="', c.x.toFixed(1), '" cy="', c.y.toFixed(1),
      '" r="', (HEX_R * .62).toFixed(1), '"/>');
  }
  if (isSel) {
    out.push('<circle class="b-sel-ring" cx="', c.x.toFixed(1), '" cy="', c.y.toFixed(1),
      '" r="', (HEX_R * .7).toFixed(1), '"/>');
  }
  out.push('<circle class="b-unit-body" cx="', c.x.toFixed(1), '" cy="', c.y.toFixed(1),
    '" r="', (HEX_R * .52).toFixed(1), '"/>',
    '<circle class="b-vit-ring" cx="', c.x.toFixed(1), '" cy="', c.y.toFixed(1),
    '" r="', (HEX_R * .52).toFixed(1),
    '" stroke-dasharray="', (circ * frac).toFixed(1), ' ', circ.toFixed(1),
    '" transform="rotate(-90 ', c.x.toFixed(1), ' ', c.y.toFixed(1), ')"/>',
    '<text class="b-unit-name" x="', c.x.toFixed(1), '" y="', (c.y + 4).toFixed(1), '">',
    battleEsc(name.slice(0, 3)), '</text>',
    '<text class="b-unit-vit" x="', c.x.toFixed(1), '" y="', (c.y + HEX_R * .82).toFixed(1), '">',
    vitality, '/', maxV, '</text>');
  if (unit.downed || unit.dead) {
    out.push('<text class="b-unit-state" x="', c.x.toFixed(1), '" y="', (c.y - HEX_R * .55).toFixed(1), '">',
      unit.dead ? '倒灭' : '倒地', '</text>');
  }
  out.push('<title>', battleEsc(name), ' · 活力 ', vitality, '/', maxV,
    unit.downed ? ' · 倒地' : '', unit.dead ? ' · 已死亡' : '', '</title>');
  out.push('</g>');
}

function renderOverlays(out, idx) {
  var view = BT.view;
  if (!view || view.status !== 'active') return;
  var active = idx.units[view.active_unit_id];
  if (!active || !active.party) return;
  // 可达格（abstract 预览：相邻格；服务器 plan_move 重算裁定）
  if (active.cell_id && idx.cells[active.cell_id]) {
    reachableCells(active.cell_id).forEach(function (cell) {
      var bandIdx = bandIndexOf(cell, idx);
      var c = cellCenter(cell, computeBandTops(idx)[bandIdx] + 22, idx.surfaces[bandIdx].baseElev || 0);
      out.push('<polygon class="b-reach" points="', ptsAttr(hexPoints(c.x, c.y, HEX_R * .92)),
        '" data-cell-id="', battleEsc(cell.cell_id), '"><title>可移动：',
        battleEsc(cell.cell_id), '</title></polygon>');
    });
  }
  // 选中格描边
  if (BT.selCell && idx.cells[BT.selCell]) {
    var cell = idx.cells[BT.selCell];
    var bandIdx2 = bandIndexOf(cell, idx);
    var c2 = cellCenter(cell, computeBandTops(idx)[bandIdx2] + 22, idx.surfaces[bandIdx2].baseElev || 0);
    out.push('<polygon class="b-cell-sel" points="', ptsAttr(hexPoints(c2.x, c2.y, HEX_R * .98)), '"/>');
  }
  // 选中攻击目标：距离与 LOS 预览（仅预览，服务器裁定）
  var selRow = idx.units[BT.selUnit];
  if (selRow && !selRow.party && active.cell_id && selRow.cell_id
      && idx.cells[active.cell_id] && idx.cells[selRow.cell_id]) {
    var a = idx.cells[active.cell_id], b = idx.cells[selRow.cell_id];
    if (String(a.surface_id) === String(b.surface_id)) {
      var line = hexLine(a.q, a.r, b.q, b.r);
      var blocked = false;
      var tops = computeBandTops(idx);
      var bandI = bandIndexOf(a, idx);
      var prev = { x: cellCenter(a, tops[bandI] + 22, idx.surfaces[bandI].baseElev || 0).x,
                   y: cellCenter(a, tops[bandI] + 22, idx.surfaces[bandI].baseElev || 0).y };
      line.forEach(function (step) {
        var rows = idx.byQR[step.q + ',' + step.r] || [];
        var mid = null;
        rows.forEach(function (c3) { if (String(c3.surface_id) === String(a.surface_id)) mid = c3; });
        if (!mid) return;
        if (mid.blocks_los && mid.cell_id !== b.cell_id) blocked = true;
        var cc = cellCenter(mid, tops[bandI] + 22, idx.surfaces[bandI].baseElev || 0);
        out.push('<line class="', mid.blocks_los ? 'b-los-line b-los-blocked' : 'b-los-line',
          '" x1="', prev.x.toFixed(1), '" y1="', prev.y.toFixed(1),
          '" x2="', cc.x.toFixed(1), '" y2="', cc.y.toFixed(1), '"/>');
        prev = cc;
      });
    }
  }
}

function bandIndexOf(cell, idx) {
  for (var i = 0; i < idx.surfaces.length; i++) {
    if (String(idx.surfaces[i].id) === String(cell.surface_id)) return i;
  }
  return 0;
}

/* ── 信息面板 / 目标列表 / 动作条 ─────────────────────────── */

function renderBattleInfo() {
  var host = $b('#battleInfo');
  if (!host) return;
  var idx = indexNow();
  var view = BT.view;
  var rows = [];
  var selRow = idx.units[BT.selUnit];

  if (selRow) {
    var unit = selRow.unit || {};
    var frac = unit.max_vitality ? Math.round(100 * (unit.vitality || 0) / unit.max_vitality) : 0;
    rows.push('<div class="b-card" data-info="unit">',
      '<b>', battleEsc(String(unit.name || selRow.unit_id)), '</b>',
      '<span class="b-tag ', selRow.party ? 'b-tag-party' : 'b-tag-enemy', '">',
      selRow.party ? '队伍' : '敌体', '</span>',
      unit.dead ? '<span class="b-tag b-tag-down">已死亡</span>'
        : unit.downed ? '<span class="b-tag b-tag-down">倒地</span>' : '',
      '<div class="b-vitbar" role="img" aria-label="活力 ',
      Number(unit.vitality || 0), '/', Number(unit.max_vitality || 0), '">',
      '<i style="width:', frac, '%"></i></div>',
      '<small>活力 ', Number(unit.vitality || 0), '/', Number(unit.max_vitality || 0),
      ' · 格 ', battleEsc(String(selRow.cell_id || '—')), '</small>',
      '</div>');
  }

  if (BT.selCell && idx.cells[BT.selCell]) {
    var cell = idx.cells[BT.selCell];
    rows.push('<div class="b-card" data-info="cell">',
      '<b>', battleEsc(String(cell.cell_id)), '</b>',
      '<small>(', cell.q, ',', cell.r, ') · 层 ', battleEsc(String(cell.surface_id || '')),
      ' · 高度 H', Number(cell.elevation_level || 0),
      ' · 地形 ', battleEsc(String(cell.terrain_id || 'open')), '</small>',
      cell.cover_grade ? '<small>掩体：' + battleEsc(String(cell.cover_grade)) + '</small>' : '',
      (cell.hazard_refs || []).length
        ? '<small class="b-danger-text">危险：' + battleEsc(cell.hazard_refs.join('、')) + '</small>' : '',
      cell.blocks_movement ? '<small>阻挡移动</small>' : '',
      cell.blocks_los ? '<small>遮蔽视线</small>' : '',
      '</div>');
  }

  // 选中物件 + 互动入口（相邻可达时）
  var objId = BT.selObject;
  if (!objId && BT.selCell) {
    var at = idx.objectsAt[BT.selCell] || [];
    if (at.length) objId = at[0];
  }
  if (objId && idx.objects[objId]) {
    var obj = idx.objects[objId];
    var def = (view.object_definitions || {})[obj.definition_id] || {};
    var transitions = (def.transitions || []).map(function (t) {
      return battleEsc(String(t.trigger || ''));
    }).join(' / ');
    rows.push('<div class="b-card" data-info="object">',
      '<b>', battleEsc(String(obj.definition_id || objId)), '</b>',
      '<small>状态：', battleEsc(String(obj.state || '—')),
      obj.blocks_movement ? ' · 阻挡移动' : '', '</small>',
      transitions ? '<small>可用互动：' + transitions + '</small>' : '',
      '</div>');
  }

  // 选中目标相对信息（距离 / 高地关系 / LOS 预览标记）
  if (selRow && !selRow.party && view.active_unit_id && idx.units[view.active_unit_id]) {
    var act = idx.units[view.active_unit_id];
    var ca = idx.cells[act.cell_id], cb = idx.cells[selRow.cell_id];
    if (ca && cb) {
      var dist = hexDist(ca.q, ca.r, cb.q, cb.r);
      var dElev = Number(cb.elevation_level || 0) - Number(ca.elevation_level || 0);
      var losBlocked = losPreviewBlocked(ca, cb, idx);
      rows.push('<div class="b-card" data-info="target">',
        '<small>距离 ', dist, ' 格 · ',
        dElev > 0 ? '目标在高处（+' + dElev + '）'
          : dElev < 0 ? '目标在低处（' + dElev + '）' : '同层',
        losBlocked === null ? '' : (losBlocked ? ' · 预览：视线可能被遮' : ' · 预览：视线通畅'),
        '</small>',
        '<small>以上为预览，命中与伤害以服务器结算为准</small>',
        '</div>');
    }
  }

  host.innerHTML = rows.join('') || '<p class="battle-empty">点选格子、单位或物件查看详情。</p>';
}

/* LOS 预览：true=中途有遮蔽，false=通畅，null=跨 surface 无法预览 */
function losPreviewBlocked(ca, cb, idx) {
  if (String(ca.surface_id) !== String(cb.surface_id)) return null;
  var line = hexLine(ca.q, ca.r, cb.q, cb.r);
  for (var i = 0; i < line.length; i++) {
    var rows = idx.byQR[line[i].q + ',' + line[i].r] || [];
    for (var j = 0; j < rows.length; j++) {
      var c = rows[j];
      if (String(c.surface_id) !== String(ca.surface_id)) continue;
      if (c.cell_id === cb.cell_id) continue;
      if (c.blocks_los) return true;
    }
  }
  return false;
}

function renderBattleTargets() {
  var host = $b('#battleTargets');
  if (!host) return;
  var idx = indexNow();
  var btns = Object.keys(idx.units).sort().map(function (uid) {
    var row = idx.units[uid];
    var unit = row.unit || {};
    var sel = BT.selUnit === uid ? ' is-on' : '';
    return '<button type="button" class="b-unit-btn' + sel + '" data-select-unit="'
      + battleEsc(uid) + '" aria-pressed="' + (sel ? 'true' : 'false') + '">'
      + battleEsc(String(unit.name || uid))
      + '<small>' + (row.party ? '队伍' : '敌体')
      + ' ' + Number(unit.vitality || 0) + '/' + Number(unit.max_vitality || 0)
      + (unit.dead ? ' 已死亡' : unit.downed ? ' 倒地' : '') + '</small></button>';
  });
  host.innerHTML = '<div class="sec-title">参战单位</div><div class="b-unit-list">'
    + btns.join('') + '</div>';
}

function renderBattleActions() {
  var view = BT.view;
  var idx = indexNow();
  var active = view && idx.units[view.active_unit_id];
  var isMyTurn = !!(view && view.status === 'active' && active && active.party);
  var myAp = view && view.turn_ap && view.active_unit_id
    ? Number(view.turn_ap[view.active_unit_id] || 0) : 0;

  var btnMove = $b('#btnBattleMove'), btnAttack = $b('#btnBattleAttack'),
    btnInteract = $b('#btnBattleInteract'), btnEnd = $b('#btnBattleEndTurn'),
    btnFlee = $b('#btnBattleFlee'), btnRefresh = $b('#btnBattleRefresh');
  if (!btnMove) return;

  // 移动：选中格是相邻可达格（服务器最终裁定）
  var moveOk = false;
  if (isMyTurn && BT.selCell && active && active.cell_id && BT.selCell !== active.cell_id
      && idx.cells[BT.selCell]) {
    var reach = reachableCells(active.cell_id);
    moveOk = reach.some(function (c) { return c.cell_id === BT.selCell; });
  }
  btnMove.hidden = !moveOk;
  if (moveOk) {
    btnMove.textContent = '移动 → ' + BT.selCell;
    btnMove.disabled = BT.busy || myAp < 1;
  }

  // 攻击：选中敌体
  var selRow = idx.units[BT.selUnit];
  var attackOk = !!(isMyTurn && selRow && !selRow.party
    && selRow.unit && !selRow.unit.dead && !selRow.unit.downed);
  btnAttack.hidden = !attackOk;
  if (attackOk) btnAttack.disabled = BT.busy || myAp < 2;

  // 互动：选中物件（服务器裁定距离与状态机）
  var interactOk = !!(isMyTurn && BT.selObject && idx.objects[BT.selObject]);
  btnInteract.hidden = !interactOk;
  if (interactOk) btnInteract.disabled = BT.busy || myAp < 1;

  btnEnd.disabled = !isMyTurn || BT.busy;
  btnFlee.disabled = !isMyTurn || BT.busy;
  btnRefresh.disabled = BT.busy;
}

/* ── 事件绑定 ───────────────────────────────────────────── */

function onBoardClick(e) {
  var objEl = e.target.closest('[data-object-id]');
  var unitEl = e.target.closest('[data-unit-id]');
  var cellEl = e.target.closest('[data-cell-id]');
  if (objEl) BT.selObject = objEl.dataset.objectId;
  if (unitEl) {
    BT.selUnit = unitEl.dataset.unitId;
    BT.selCell = unitEl.dataset.cellId || BT.selCell;
    BT.selObject = '';
  } else if (cellEl) {
    BT.selCell = cellEl.dataset.cellId;
    var idx = indexNow();
    var row = idx.unitAt[BT.selCell];
    if (row) { BT.selUnit = row; BT.selObject = ''; }
    // 点空地且当前没有选中队伍单位 → 保留 selUnit（行动单位的意图目标格）
    var at = idx.objectsAt[BT.selCell] || [];
    if (!unitEl && at.length === 1) BT.selObject = at[0];
  }
  renderBattleInfo();
  renderBattleTargets();
  renderBoard();
  renderBattleActions();
}

function initBattle() {
  var enter = $b('#btnBattleEnter');
  if (enter) enter.addEventListener('click', function () { enterBattle(); });

  var back = $b('#btnBattleBack');
  if (back) back.addEventListener('click', function () { showView('v-play'); });

  var board = $b('#battleBoard');
  if (board) board.addEventListener('click', onBoardClick);

  var zi = $b('#btnBattleZoomIn'), zo = $b('#btnBattleZoomOut'), zr = $b('#btnBattleZoomReset');
  if (zi) zi.addEventListener('click', function () { BT.zoom = Math.min(2.5, BT.zoom * 1.25); renderBoard(); });
  if (zo) zo.addEventListener('click', function () { BT.zoom = Math.max(.5, BT.zoom / 1.25); renderBoard(); });
  if (zr) zr.addEventListener('click', function () { BT.zoom = 1; renderBoard(); });

  var mv = $b('#btnBattleMove');
  if (mv) mv.addEventListener('click', function () {
    battleAction('move', { to_cell_id: BT.selCell });
  });
  var atk = $b('#btnBattleAttack');
  if (atk) atk.addEventListener('click', function () {
    battleAction('attack', { target_id: BT.selUnit });
  });
  var it = $b('#btnBattleInteract');
  if (it) it.addEventListener('click', function () {
    battleAction('interact', { object_id: BT.selObject, action: 'interact' });
  });
  var end = $b('#btnBattleEndTurn');
  if (end) end.addEventListener('click', function () {
    battleAction('end_turn', {});
  });
  var flee = $b('#btnBattleFlee');
  if (flee) flee.addEventListener('click', function () {
    // 两步确认（不用 window.confirm：会打断 aria-live，手机不可样式化，
    // 见 UI-DESIGN.md §1.3.7 与 U1/U3 的先例）：第一次点只「上膛」，
    // 3 秒内再点才真的撤退。
    if (!flee.dataset.armed) {
      flee.dataset.armed = '1';
      flee.textContent = '再点一次确认撤退';
      setTimeout(function () {
        if (flee.dataset.armed) {
          delete flee.dataset.armed;
          flee.textContent = '撤退';
        }
      }, 3000);
      return;
    }
    delete flee.dataset.armed;
    flee.textContent = '撤退';
    battleAction('flee', {});
  });
  var rf = $b('#btnBattleRefresh');
  if (rf) rf.addEventListener('click', function () { refreshBattle(); });

  var targets = $b('#battleTargets');
  if (targets) targets.addEventListener('click', function (e) {
    var btn = e.target.closest('[data-select-unit]');
    if (!btn) return;
    var idx = indexNow();
    BT.selUnit = btn.dataset.selectUnit;
    var row = idx.units[BT.selUnit];
    BT.selCell = row ? (row.cell_id || '') : '';
    BT.selObject = '';
    renderBattleInfo();
    renderBattleTargets();
    renderBoard();
    renderBattleActions();
  });

  // 测试 / 调试钩子：快照与 fixture 注入（渲染路径与服务端返回共用）。
  window.__BATTLE = function () {
    return {
      loaded: BT.loaded, hasBattle: BT.hasBattle, zoom: BT.zoom,
      status: BT.view ? BT.view.status : '',
      revision: BT.view ? BT.view.state_revision : null,
      round: BT.view ? BT.view.round : null,
      activeUnit: BT.view ? BT.view.active_unit_id : '',
      selUnit: BT.selUnit, selCell: BT.selCell, selObject: BT.selObject,
      cells: BT.view && BT.view.battlefield ? (BT.view.battlefield.cells || []).length : 0,
      surfaces: bfIndexCache ? bfIndexCache.surfaces.length : 0
    };
  };
  window.__BATTLE.apply = function (view) {
    applyBattleView(view);
    showView('v-battle');
  };
}

onInit(initBattle);
