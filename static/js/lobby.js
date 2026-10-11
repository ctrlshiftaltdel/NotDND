/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U1：会话列表（#v-lobby）
   存档列表的读取与渲染、进入会话、改名 / 删除 / 开新的一局，
   以及本片自己的事件绑定。
   共用件在 js/core.js；本片只改 js/lobby.js、css/lobby.css 与
   index.html 的必要结构挂点。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* ── 本片状态 ────────────────────────────────────────────
   SAVES / SAVE_DIR / SAVES_TOTAL / SAVES_TRUNCATED 是 core 里的共用状态，
   这里只放本片自己的交互态。 */
var LOBBY = {
  current: '',        // GET /api/saves 回传的当前局 sid（与本地 SID 同源）
  loaded: false,      // 是否成功拿到过列表（决定空态 / 错误态）
  error: '',          // 列表加载失败（连接 / 服务端错误）→ 就地错误卡
  createError: '',    // 建局失败 → 大厅就地错误卡（不换视图）
  busy: null,         // 本片单一在途：null | 'create' | 'rename' | 'delete'（§2.0）
  manage: '',         // 展开了操作菜单的存档 id（'' = 无）
  editing: '',        // 正在内联改名的存档 id（'' = 无）
  editValue: '',      // 内联输入框的当前文本（重画不丢字）
  renameError: '',    // 内联改名失败提示
  confirm: null,      // 待删除的存档 { id, name }
  deleteError: ''     // 删除确认框里的失败提示
};

function lobbyBusy() {
  // 本片在途标志 + core 统一守卫（§2.0）：任一片的写请求在途时，本片写入口
  // 一并忽略。LOBBY.busy 保留为幂等双保险（本片自己的三态渲染仍靠它）。
  return !!LOBBY.busy || writeInFlight();
}

/* 当前局：服务端回传的 current 优先，退回本地 SID（两者同源）。 */
function lobbyCurrent() { return LOBBY.current || SID; }
function isCurrentSave(s) { return !!s && s.id === lobbyCurrent(); }

function findSave(sid) {
  for (var i = 0; i < SAVES.length; i++) {
    if (SAVES[i] && SAVES[i].id === sid) return SAVES[i];
  }
  return null;
}

/* 只读快照，供测试 / 调试核对本片状态（等价 window.__ERRS 的用途）。 */
function lobbySnapshot() {
  return {
    loaded: LOBBY.loaded,
    error: LOBBY.error,
    createError: LOBBY.createError,
    busy: LOBBY.busy,
    editing: LOBBY.editing,
    manage: LOBBY.manage,
    confirm: LOBBY.confirm ? LOBBY.confirm.id : null,
    deleteError: LOBBY.deleteError,
    renameError: LOBBY.renameError,
    count: SAVES.length,
    total: SAVES_TOTAL,
    truncated: SAVES_TRUNCATED,
    current: lobbyCurrent()
  };
}

/* ── 读取存档列表（只读接口）──────────────────────────────
   失败**不**静默（§1.3.1 错误态）：落到就地错误卡，而不是 toast 一闪而过。
   `silent` 用于删除成功后的刷新——那时列表已经就地更新，刷新失败不该
   把成功的结果盖成错误卡。 */
async function loadSaves(opts) {
  var silent = !!(opts && opts.silent);
  try {
    var d = await api('/api/saves');
    SAVES = Array.isArray(d.saves) ? d.saves : [];
    SAVE_DIR = d.save_dir || SAVE_DIR;
    SAVES_TOTAL = d.total || SAVES.length;
    SAVES_TRUNCATED = d.truncated || 0;
    LOBBY.current = d.current || '';
    LOBBY.error = '';
    LOBBY.loaded = true;
  } catch (e) {
    if (silent) return;
    SAVES = [];
    SAVES_TOTAL = 0;
    SAVES_TRUNCATED = 0;
    LOBBY.error = e.message;
  }
}

/* ── 渲染 ────────────────────────────────────────────────
   纯渲染，不发请求。列表区块 `#lobbySaves` 同时是写动作的
   「就地状态区」：任一写请求在途时 aria-busy（§2.0 三态可断言）。 */
function renderSaveList() {
  var box = $('#lobbySaves');
  if (!box) return;
  var count = $('#lobbyCount');
  var more = $('#lobbyTruncated');
  var newBox = $('#lobbyNew');
  box.setAttribute('aria-busy', LOBBY.busy ? 'true' : 'false');
  if (newBox) newBox.setAttribute('aria-busy', LOBBY.busy ? 'true' : 'false');

  // 连接 / 服务端错误：整块换成错误卡 + 重试（设置 / 关于不依赖网络，仍可用）。
  if (LOBBY.error) {
    if (count) count.textContent = '';
    if (more) more.hidden = true;
    if (newBox) { newBox.hidden = true; newBox.innerHTML = ''; }
    box.innerHTML = errorCard(LOBBY.error);
    bindList();
    return;
  }

  if (count) count.textContent = SAVES.length ? (SAVES.length + ' 个') : '';

  // 截断态：如实说明还有多少更早的没列出（不假装只有这些）。
  if (more) {
    more.hidden = !SAVES_TRUNCATED;
    if (SAVES_TRUNCATED) {
      more.textContent = '只显示最近 ' + SAVES.length + ' 个存档，另有 ' +
        SAVES_TRUNCATED + ' 个更早的未列出（文件仍在本机）。';
    }
  }

  // 空态：空态卡 +「开始新的一局」（§1.3.1）。
  if (!SAVES.length) {
    if (newBox) { newBox.hidden = true; newBox.innerHTML = ''; }
    box.innerHTML = emptyCard();
    bindList();
    return;
  }

  box.innerHTML = SAVES.map(saveRowHtml).join('');
  if (newBox) {
    newBox.hidden = false;
    newBox.innerHTML = newGameButton('btn');
  }
  bindList();
}

function errorCard(message) {
  return '<div class="alert lobby-error" role="alert">' +
    '<p>' + esc(message || '无法连接服务，请确认后端仍在运行') + '</p>' +
    '<div><button type="button" class="btn" data-lobby-retry>重试</button></div>' +
  '</div>';
}

function emptyCard() {
  return '<div class="save-empty">' +
    '<p>还没有存档。</p>' +
    '<p class="save-empty-sub">开一局之后，每一步都会自动写进存档目录。</p>' +
    (LOBBY.createError
      ? '<p class="save-empty-error" role="alert">' + esc(LOBBY.createError) + '</p>'
      : '') +
    newGameButton('btn btn-primary btn-block') +
  '</div>';
}

/* 建局按钮。在途时 aria-busy + disabled（base.css 的 .btn[aria-busy] 会显示 ···）。 */
function newGameButton(classes) {
  return '<button type="button" class="' + classes + ' lobby-new-btn" data-new-game' +
    (LOBBY.busy === 'create' ? ' aria-busy="true" disabled' : '') + '>' +
    '<svg class="i i-sm" aria-hidden="true"><use href="#i-plus"/></svg>开始新的一局</button>';
}

function saveRowHtml(s) {
  if (LOBBY.editing === s.id) return editRowHtml(s);

  var open = LOBBY.manage === s.id;
  return '<div class="save-row' + (isCurrentSave(s) ? ' is-current' : '') +
      (open ? ' has-menu' : '') + '" data-sid="' + esc(s.id) + '">' +
    '<button class="save-main" data-open="' + esc(s.id) + '">' +
      '<div class="save-name">' + esc(s.name || s.id) + saveTags(s) + '</div>' +
      '<div class="save-meta">' + esc(s.mtime_text || '—') + ' · ' +
        (s.log_count || 0) + ' 条记录</div>' +
    '</button>' +
    '<div class="save-actions">' +
      '<button type="button" class="save-menu-btn" data-manage="' + esc(s.id) + '"' +
        ' aria-label="存档管理" aria-expanded="' + (open ? 'true' : 'false') + '"' +
        (LOBBY.busy ? ' disabled' : '') + '>⋯</button>' +
    '</div>' +
    (open ? saveMenuHtml(s) : '') +
  '</div>';
}

function saveTags(s) {
  var tags = '';
  if (!s.named) tags += '<span class="tag">默认名</span>';
  if (isCurrentSave(s)) tags += '<span class="tag tag-on">当前</span>';
  return tags;
}

/* 存档行菜单（§1.3.1 / §1.3.9）：改名 / 删除。当前局**不显示删除入口**。 */
function saveMenuHtml(s) {
  return '<div class="save-menu" role="group" aria-label="存档操作">' +
    '<button type="button" class="btn btn-sm" data-rename="' + esc(s.id) + '"' +
      (LOBBY.busy ? ' disabled' : '') + '>改名</button>' +
    (isCurrentSave(s) ? '' :
      '<button type="button" class="btn btn-sm btn-danger" data-del="' + esc(s.id) + '"' +
        (LOBBY.busy ? ' disabled' : '') + '>删除</button>') +
  '</div>';
}

/* 内联改名行（§2.7）：名字变成 input，Enter 确认 / Esc 取消；本地校验 1–40 字。 */
function editRowHtml(s) {
  var busy = LOBBY.busy === 'rename';
  return '<div class="save-row is-editing' + (isCurrentSave(s) ? ' is-current' : '') +
      '" data-sid="' + esc(s.id) + '" aria-busy="' + (busy ? 'true' : 'false') + '">' +
    '<div class="save-main save-edit">' +
      '<input id="saveNameInput" class="save-name-input" type="text" maxlength="40"' +
        ' aria-label="存档名" value="' + esc(LOBBY.editValue) + '"' +
        (busy ? ' disabled' : '') + '>' +
      '<div id="saveNameHint" class="save-edit-hint">' +
        (busy ? '保存中…' : '回车确认 · Esc 取消') + '</div>' +
      (LOBBY.renameError
        ? '<div class="save-edit-error" role="alert">' + esc(LOBBY.renameError) + '</div>'
        : '') +
    '</div>' +
    '<div class="save-edit-actions">' +
      '<button type="button" class="btn btn-sm btn-primary" data-rename-ok' +
        (busy ? ' disabled' : '') + '>确认</button>' +
      '<button type="button" class="btn btn-sm" data-rename-cancel' +
        (busy ? ' disabled' : '') + '>取消</button>' +
    '</div>' +
  '</div>';
}

/* ── 事件绑定：每次重画后重挂（元素是新建的，不会叠加监听）── */
function bindList() {
  var box = $('#lobbySaves');
  if (!box) return;

  $$('[data-lobby-retry]').forEach(function (b) {
    b.addEventListener('click', retryLoad);
  });
  $$('[data-new-game]').forEach(function (b) {
    b.addEventListener('click', function () { createSession(); });
  });
  Array.prototype.slice.call(box.querySelectorAll('[data-open]')).forEach(function (b) {
    b.addEventListener('click', function () {
      if (lobbyBusy()) return;               // 写动作在途：不并发进入
      enterSession(b.dataset.open);
    });
  });
  Array.prototype.slice.call(box.querySelectorAll('[data-manage]')).forEach(function (b) {
    b.addEventListener('click', function () { toggleManage(b.dataset.manage); });
  });
  Array.prototype.slice.call(box.querySelectorAll('[data-rename]')).forEach(function (b) {
    b.addEventListener('click', function () { startRename(b.dataset.rename); });
  });
  Array.prototype.slice.call(box.querySelectorAll('[data-del]')).forEach(function (b) {
    b.addEventListener('click', function () { askDelete(b.dataset.del); });
  });
  Array.prototype.slice.call(box.querySelectorAll('[data-rename-ok]')).forEach(function (b) {
    b.addEventListener('click', function () { submitRename(); });
  });
  Array.prototype.slice.call(box.querySelectorAll('[data-rename-cancel]')).forEach(function (b) {
    b.addEventListener('click', function () { cancelRename(); });
  });

  var inp = document.getElementById('saveNameInput');
  if (inp) {
    inp.addEventListener('input', function () { LOBBY.editValue = inp.value; });
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); submitRename(); }
      else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancelRename(); }
    });
    focusRenameInput();
  }
}

/* ── 进入会话（读，不是写动作）─────────────────────────── */
async function enterSession(sid) {
  SID = sid || '';
  LOBBY.current = SID;
  try {
    if (SID) localStorage.setItem(SID_KEY, SID);
  } catch (e) { /* 隐私模式 */ }
  showView('v-play');
  try {
    var view = await api('/api/session');
    applyState(view);
  } catch (e) {
    // 服务端没有这一局时退回占位态：骨架在没有真实会话时也要能用。
    toast('该会话暂不可用，已进入占位画面');
    applyState(placeholderState());
  }
}

/* ── 开新的一局（写接口，B1）──────────────────────────────
   失败停在大厅 + 就地错误卡，**不**进 #v-play（避免进到一个空壳，§2.1）。 */
async function createSession() {
  if (lobbyBusy()) return;
  LOBBY.busy = 'create';
  LOBBY.createError = '';
  renderSaveList();
  try {
    var d = await api('/api/session/create', { method: 'POST', body: {} });
    var sid = (d && d.sid) || '';
    if (!sid) throw new Error('建局失败，请重试');
    LOBBY.busy = null;
    enterSession(sid);
  } catch (e) {
    LOBBY.busy = null;
    LOBBY.createError = e.message;
    renderSaveList();
  }
}

/* ── 存档管理菜单：展开 / 收起（同一时刻只开一个）────────── */
function toggleManage(sid) {
  if (lobbyBusy()) return;
  LOBBY.manage = (LOBBY.manage === sid) ? '' : sid;
  LOBBY.editing = '';
  LOBBY.renameError = '';
  renderSaveList();
}

/* ── 改名：内联编辑（§2.7）─────────────────────────────── */
function startRename(sid) {
  var s = findSave(sid);
  if (!s || lobbyBusy()) return;
  LOBBY.manage = '';
  LOBBY.editing = sid;
  LOBBY.editValue = s.name || s.id;
  LOBBY.renameError = '';
  renderSaveList();
}

function cancelRename() {
  if (LOBBY.busy === 'rename') return;
  LOBBY.editing = '';
  LOBBY.editValue = '';
  LOBBY.renameError = '';
  renderSaveList();
}

function focusRenameInput() {
  var inp = document.getElementById('saveNameInput');
  if (!inp) return;
  try {
    inp.focus();
    inp.setSelectionRange(inp.value.length, inp.value.length);
  } catch (e) { /* 老浏览器 / 不可聚焦：忽略 */ }
}

async function submitRename() {
  var sid = LOBBY.editing;
  if (!sid || lobbyBusy()) return;
  var name = (LOBBY.editValue || '').trim();
  if (name.length < 1 || name.length > 40) {
    // 本地校验（服务端 len>40 是 400）：不发请求，就地提示。
    LOBBY.renameError = '存档名需为 1–40 个字符';
    renderSaveList();
    return;
  }
  LOBBY.busy = 'rename';
  LOBBY.renameError = '';
  setRenameBusy(true);          // 三态之「进行中」：就地禁用 + aria-busy
  try {
    var d = await api('/api/save/rename', { method: 'POST', body: { sid: sid, name: name } });
    var s = findSave(sid);
    var shown = (d && d.name) || name;
    if (s) { s.name = shown; s.named = true; }
    // 顶栏：改的正是当前打开的这局时同步（**不改本地 SID、不刷新页面**，§2.7）。
    if (sid === SID && ST && ST.save) {
      ST.save.name = shown;
      ST.save.named = true;
      renderTop();
    }
    LOBBY.editing = '';
    LOBBY.editValue = '';
    toast('已重命名');
  } catch (e) {
    LOBBY.renameError = e.message;   // 失败：就地提示，输入保留可重试
  } finally {
    LOBBY.busy = null;
    renderSaveList();
    if (LOBBY.editing) focusRenameInput();
  }
}

/* 不改状态、只切 DOM 的「进行中」态：重画会丢焦点，所以就地改。 */
function setRenameBusy(busy) {
  var inp = document.getElementById('saveNameInput');
  if (inp) inp.disabled = busy;
  var row = document.querySelector('.save-row.is-editing');
  if (row) row.setAttribute('aria-busy', busy ? 'true' : 'false');
  var hint = document.getElementById('saveNameHint');
  if (hint) hint.textContent = busy ? '保存中…' : '回车确认 · Esc 取消';
  $$('[data-rename-ok], [data-rename-cancel]').forEach(function (b) { b.disabled = busy; });
}

/* ── 删除：L2 确认框（§1.3.7 / §2.7）────────────────────── */
function askDelete(sid) {
  if (lobbyBusy()) return;
  var s = findSave(sid);
  if (!s || isCurrentSave(s)) return;   // 当前局不显示删除入口（服务端也会拒 400）
  LOBBY.manage = '';
  LOBBY.confirm = { id: sid, name: s.name || sid };
  LOBBY.deleteError = '';
  renderSaveConfirm();
  var del = document.querySelector('#saveConfirm [data-save-del]');
  if (del) { try { del.focus(); } catch (e) { /* 忽略 */ } }
}

function renderSaveConfirm() {
  var scrim = $('#saveConfirmScrim');
  var dlg = $('#saveConfirm');
  if (!scrim || !dlg) return;
  if (!LOBBY.confirm) { scrim.hidden = true; dlg.hidden = true; return; }

  var c = LOBBY.confirm;
  var busy = LOBBY.busy === 'delete';
  $('#saveConfirmBody').innerHTML =
    '<p class="confirm-line">删除「' + esc(c.name) + '」？</p>' +
    '<p class="confirm-sub">该局记录会从本机删掉，无法恢复。</p>' +
    (LOBBY.deleteError
      ? '<p class="confirm-error" role="alert">' + esc(LOBBY.deleteError) + '</p>'
      : '') +
    '<div class="confirm-actions">' +
      '<button type="button" class="btn" data-save-cancel' + (busy ? ' disabled' : '') +
        '>取消</button>' +
      '<button type="button" class="btn btn-danger" data-save-del' +
        (busy ? ' aria-busy="true" disabled' : '') + '>删除</button>' +
    '</div>';

  scrim.hidden = false;
  dlg.hidden = false;
  var cancel = document.querySelector('#saveConfirm [data-save-cancel]');
  var del = document.querySelector('#saveConfirm [data-save-del]');
  if (cancel) cancel.addEventListener('click', closeSaveConfirm);
  if (del) del.addEventListener('click', doDelete);
}

function closeSaveConfirm() {
  if (LOBBY.busy === 'delete') return;   // 删除在途：不允许半途关掉
  LOBBY.confirm = null;
  LOBBY.deleteError = '';
  renderSaveConfirm();
}

async function doDelete() {
  if (lobbyBusy() || !LOBBY.confirm) return;
  var sid = LOBBY.confirm.id;
  LOBBY.busy = 'delete';
  LOBBY.deleteError = '';
  renderSaveConfirm();                    // 三态之「进行中」：删除按钮 aria-busy
  try {
    await api('/api/save/delete', { method: 'POST', body: { sid: sid } });
    // 从列表移除；随后向服务端重取，如实刷新 total / truncated
    //（被截断的更早一条可能补位，服务端不保证，§2.7）。
    SAVES = SAVES.filter(function (s) { return s.id !== sid; });
    SAVES_TOTAL = Math.max(0, SAVES_TOTAL - 1);
    LOBBY.busy = null;
    LOBBY.confirm = null;
    LOBBY.deleteError = '';
    renderSaveConfirm();
    renderSaveList();
    await loadSaves({ silent: true });
    renderSaveList();
  } catch (e) {
    LOBBY.busy = null;
    LOBBY.deleteError = e.message;        // 失败：确认框里就地提示，可重试
    renderSaveConfirm();
  }
}

/* 从主画面返回列表时重取一次：否则刚开的局 / 改过的名不会出现在列表里
   （core 的「返回会话列表」只切视图，不知道列表需要刷新）。 */
async function refreshSaves() {
  await loadSaves();
  renderSaveList();
}

/* ── 列表加载失败后的「重试」────────────────────────────── */
async function retryLoad() {
  var box = $('#lobbySaves');
  if (box) box.innerHTML = '<div class="boot">正在读取存档…</div>';
  LOBBY.error = '';
  await loadSaves();
  renderSaveList();
}

/* ── 本片的事件绑定：大厅底部的「设置 / 关于」+ 确认框的静态部分 ── */
function bindLobby() {
  $('#btnLobbySettings').addEventListener('click', function () {
    renderSettings();
    showView('v-settings');
  });
  $('#btnLobbyAbout').addEventListener('click', function () { openAux('about'); });

  // 返回列表时刷新存档（core 只切视图；新建的局要能出现）。
  var back = $('#btnBackToLobby');
  if (back) back.addEventListener('click', refreshSaves);

  // 删除确认框的遮罩：静态元素只绑一次，避免每次重画叠加监听。
  var scrim = $('#saveConfirmScrim');
  if (scrim) scrim.addEventListener('click', closeSaveConfirm);

  // Esc：本片的确认框 / 内联改名自成一体，不在 core 的 SHEET_STACK 里，
  // core 的 Esc 不认识它们——在 capture 阶段先处理，逐层语义由本片负责。
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    if (LOBBY.confirm) { e.preventDefault(); e.stopPropagation(); closeSaveConfirm(); return; }
    if (LOBBY.editing) { e.preventDefault(); e.stopPropagation(); cancelRename(); }
  }, true);

  window.__LOBBY = lobbySnapshot;
}

onInit(bindLobby);
