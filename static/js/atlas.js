/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U3：出口与移动（ATLAS 接线）
   「前往」出口列表来自 GET /api/atlas/exits，点出口走
   POST /api/atlas/move { via }。移动不经过导引者（atlas.py 不调
   模型），所以没配 AI 也能走；前端**只回传 via**，绝不自己拼
   place_id（UI-DESIGN.md §2.6）。

   本片只改本文件与 css/atlas.css、index.html 的结构挂点；
   跨片共用件（esc / toast / api / onInit …）在 js/core.js。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* ── 本片状态 ────────────────────────────────────────────
   exits[] 永远来自服务端；serverText 是服务端原文（空态照抄，
   不自己编话术）。placeName 是移动成功后的当前地点名。 */
var ATLAS = {
  mode: 'placeholder',   // 'placeholder'（演示态）| 'server'
  exits: [],
  serverText: '',
  placeName: '',
  loading: false,        // 出口列表加载中
  fetching: false,       // 出口列表请求在途（防重复打）
  moveInFlight: false,   // 移动写请求在途（§2.0 单一在途）
  reqId: 0               // 请求序号：只认最后一次响应，防迟到覆盖
};

/* 只读快照，供测试 / 调试核对本片状态与后端视图（等价于 window.__ERRS 的用途）。 */
function atlasSnapshot() {
  return {
    mode: ATLAS.mode,
    exits: ATLAS.exits.map(function (e) { return { via: e.via, name: e.name, band: e.band, minutes: e.minutes }; }),
    serverText: ATLAS.serverText,
    placeName: ATLAS.placeName,
    busy: ATLAS.moveInFlight
  };
}

/* ── 小工具 ───────────────────────────────────────────── */

/* 名义分钟 → 时段数。1 时段 = 360 名义分钟（BAND_MINUTES，§2.3），
   与 UI-DESIGN.md §2.6 的「4 时段」口径一致；前端只显示、不参与判定。 */
function shiftsFor(minutes) {
  return Math.max(1, Math.round((minutes || 0) / 360));
}

/* 行程档文案：点名档位名（BAND_NAMES 口径），危险档单独标记。 */
function bandInfo(exit) {
  var band = exit && exit.band;
  var n = shiftsFor(exit && exit.minutes);
  var names = { short: '短程', medium: '中程', long: '远程', dangerous: '危险穿越' };
  return {
    name: names[band] || '门内',
    shifts: n,
    dangerous: band === 'dangerous'
  };
}

/* ── 渲染：出口列表 ──────────────────────────────────────
   有真实会话（SID）时数据一律来自服务端；没有会话时用占位态演示，
   且**不允许真的移动**（服务端没有这一局，发请求只会 400）。 */
function renderExits() {
  if (SID) {
    ATLAS.mode = 'server';
    renderServerExits();
    return;
  }
  ATLAS.mode = 'placeholder';
  renderPlaceholderExits();
}

function renderPlaceholderExits() {
  var exits = (ST && ST.exits) || [];
  setExitsBusy(false);
  $('#exitList').innerHTML = exits.length
    ? '<div class="exit-list">' + exits.map(function (x) {
        return '<button class="btn act-btn" data-exit="' + esc(x.via || x.id) + '" disabled>' +
          '<span class="act-label">' + esc(x.label || x.name || '') + '</span>' +
          '</button>';
      }).join('') + '</div>' +
      '<div class="exit-empty">演示出口：接入会话后将由服务端给出「前往」列表。</div>'
    : '<div class="exit-empty">演示态：还没有会话，出口列表为空。</div>';
}

function renderServerExits() {
  var exits = ATLAS.exits || [];

  if (ATLAS.loading) {
    setExitsBusy(true);
    $('#exitList').innerHTML = '<div class="exit-note" role="status">正在读取出口…</div>';
    return;
  }

  setExitsBusy(ATLAS.moveInFlight);

  // 空态：照抄服务端原文（§2.6 / Issue 验收），不自己编话术。
  if (!exits.length) {
    $('#exitList').innerHTML = '<div class="exit-empty">' +
      esc(ATLAS.serverText || '这里没有已知出口。') + '</div>';
    return;
  }

  $('#exitList').innerHTML = '<div class="exit-list">' + exits.map(function (x) {
    var b = bandInfo(x);
    return '<button class="btn act-btn act-btn-exit' + (b.dangerous ? ' is-danger' : '') +
      '" data-exit="' + esc(x.via) + '"' +
      ' aria-label="' + esc((x.name || '') + '（' + b.name + ' · ' + b.shifts + ' 时段）') + '">' +
      '<span class="act-label">' + esc(x.name || '') + '</span>' +
      '<span class="act-hint exit-band">' + esc(b.shifts + ' 时段') + '</span>' +
      (b.dangerous ? '<span class="exit-danger">危险</span>' : '') +
      '</button>';
  }).join('') + '</div>';

  $$('#exitList [data-exit]').forEach(function (btn) {
    if (ATLAS.moveInFlight) return;   // 移动在途：不接受新的点击（§2.0）
    btn.addEventListener('click', function () { pickExit(btn.dataset.exit); });
  });
}

/* 出口小节定为写动作的「就地状态区」：加载 / 移动在途时 aria-busy，
   让读屏与验收脚本都能看到「正在做什么」，也满足 §2.0 的三态。 */
function setExitsBusy(busy) {
  var sec = $('#exitsSection');
  if (sec) sec.setAttribute('aria-busy', busy ? 'true' : 'false');
}

/* ── 拉取出口列表（只读接口）───────────────────────────── */
async function loadExits() {
  if (!SID || ATLAS.fetching) return;
  var my = ++ATLAS.reqId;
  ATLAS.fetching = true;
  ATLAS.loading = true;
  clearExitError();
  renderExits();
  try {
    var view = await api('/api/atlas/exits');
    if (my !== ATLAS.reqId) return;        // 迟到的旧响应：丢弃
    ATLAS.exits = Array.isArray(view.exits) ? view.exits : [];
    ATLAS.serverText = view.text || '';
    renderExits();
  } catch (e) {
    if (my !== ATLAS.reqId) return;
    renderExitError(e.message, '重试');
  } finally {
    ATLAS.fetching = false;
    ATLAS.loading = false;
  }
}

/* ── 就地的出口错误行（§1.3.9 / §2.8）：一行原因 + 一个动作按钮 ── */
function clearExitError() {
  var box = $('#exitError');
  if (box) { box.innerHTML = ''; box.hidden = true; }
}

function renderExitError(message, actionLabel) {
  var box = $('#exitError');
  if (!box) return;
  box.hidden = false;
  box.innerHTML = '<p class="alert-msg">' + esc(message || '移动失败，请重试') + '</p>' +
    '<button type="button" class="btn btn-sm" data-exit-retry="1">' + esc(actionLabel || '重试') + '</button>';
  var retry = $('[data-exit-retry]', box);
  if (retry) retry.addEventListener('click', function () { retryMove(); });
}

/* ── 点出口：危险档先确认，其余直接走 ──────────────────── */
function pickExit(via) {
  via = String(via || '');
  if (!via || ATLAS.moveInFlight) return;
  var x = (ATLAS.exits || []).find(function (e) { return e.via === via; });
  if (!x) return;
  buzz(12);
  if (bandInfo(x).dangerous) {
    confirmExitMove(x);
  } else {
    doMove(via, x);
  }
}

/* ── 危险穿越的 L2 确认（点名目的地，UX-DESIGN §1.3.7 / §2.6）── */
function confirmExitMove(x) {
  var b = bandInfo(x);
  var scrim = $('#exitConfirmScrim');
  var dlg = $('#exitConfirm');
  if (!scrim || !dlg) return;

  $('#exitConfirmTitle').textContent = b.name;
  $('#exitConfirmBody').innerHTML =
    '<p class="confirm-line">前往「' + esc(x.name || '未知地点') + '」？</p>' +
    '<p class="confirm-sub">危险穿越 · ' + b.shifts + ' 时段</p>' +
    '<div class="confirm-actions">' +
      '<button type="button" class="btn" data-exit-cancel="1">取消</button>' +
      '<button type="button" class="btn btn-primary" data-exit-go="1">继续穿越</button>' +
    '</div>';

  scrim.hidden = false;
  dlg.hidden = false;
  if (dlg.focus) { try { dlg.focus(); } catch (e) { /* 忽略 */ } }

  var cancel = $('[data-exit-cancel]', dlg);
  var go = $('[data-exit-go]', dlg);
  if (cancel) cancel.addEventListener('click', closeExitConfirm);
  scrim.addEventListener('click', closeExitConfirm);
  if (go) go.addEventListener('click', function () {
    if (go.disabled) return;
    go.disabled = true;             // 防连点：确认只发一次请求
    closeExitConfirm();
    doMove(x.via, x);
  });
}

function closeExitConfirm() {
  var scrim = $('#exitConfirmScrim');
  var dlg = $('#exitConfirm');
  if (scrim) scrim.hidden = true;
  if (dlg) dlg.hidden = true;
}

/* ── 移动：写动作三态（进行中 aria-busy / 成功就地更新 / 失败可重试）── */
async function doMove(via, x) {
  if (ATLAS.moveInFlight) return;
  ATLAS.moveInFlight = true;
  ATLAS_RETRY = { type: 'move', via: via, exit: x };   // 失败后按原方向重试
  clearExitError();
  showMoveBusy();
  try {
    var view = await api('/api/atlas/move', { method: 'POST', body: { via: via } });
    ATLAS_RETRY = null;
    applyMoveResult(view, x);
    await refreshSession();
  } catch (e) {
    // 失败：保持原地（不乐观更新），就地提示并可重试。
    renderExitError(e.message, '重试');
  } finally {
    ATLAS.moveInFlight = false;
    setExitsBusy(false);
    renderExits();
  }
}

function showMoveBusy() {
  setExitsBusy(true);
  $('#exitList').innerHTML = '<div class="exit-note" role="status">正在移动…</div>';
}

function applyMoveResult(view, x) {
  var here = (view && view.here) || {};
  var name = here.name || (x && x.name) || '';
  var b = bandInfo({ band: (view && view.band) || (x && x.band), minutes: (view && view.minutes) || (x && x.minutes) });

  ATLAS.exits = Array.isArray(view && view.exits) ? view.exits : [];
  ATLAS.serverText = (view && view.text) || '';

  // 顶栏地点随移动更新（写动作**不做**乐观更新：地点名的真相在服务端，
  // 这里用的是 move 响应里的 here.name）。
  if (name) {
    ATLAS.placeName = name;
    var loc = $('#playScene');
    if (loc) loc.textContent = name;
  }

  // 「前往」列表整体替换为新 here 的 exits（下一个 renderExits 读 ATLAS.exits）。
  toast('你到达了「' + name + '」（' + b.shifts + ' 时段）');
}

/* 移动后位置变了，规则层快照可能一起变：重取整局视图并整屏重画。
   重复请求用 reqId 守卫，迟到的旧响应不覆盖新状态。 */
async function refreshSession() {
  if (!SID) return;
  var my = ++ATLAS.reqId;
  try {
    var view = await api('/api/session');
    if (my !== ATLAS.reqId) return;
    applyState(view);
  } catch (e) {
    /* 刷新失败不影响已成功的移动：下一次成功操作或重进会话会再拉。 */
  }
}

/* ── 失败重试：move 失败时重试原方向；列表加载失败时重拉列表 ── */
var ATLAS_RETRY = null;
function retryMove() {
  clearExitError();
  if (ATLAS_RETRY && ATLAS_RETRY.type === 'move') {
    doMove(ATLAS_RETRY.via, ATLAS_RETRY.exit);
  } else {
    loadExits();
  }
}

/* ── 本片事件绑定 ──────────────────────────────────────── */
function bindAtlas() {
  // 危险确认框打开时吞掉 Esc：这是本片自成一体的模态，关闭它。
  // （core 的 Esc 只认它自己的 SHEET_STACK，不认识这个框。）
  document.addEventListener('keydown', function (e) {
    var dlg = $('#exitConfirm');
    if (e.key === 'Escape' && dlg && !dlg.hidden) {
      e.preventDefault();
      e.stopPropagation();
      closeExitConfirm();
    }
  });
  window.__ATLAS = atlasSnapshot;
}

onInit(bindAtlas);
