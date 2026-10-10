/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U1：会话列表（#v-lobby）
   存档列表的读取与渲染、进入会话，以及本片自己的事件绑定。
   共用件在 js/core.js；本片后续若要加「开新的一局 / 改名 / 删除」，
   只改本文件（与 css/lobby.css、index.html）。
   ═══════════════════════════════════════════════════════════ */
'use strict';

async function loadSaves() {
  try {
    var d = await api('/api/saves');
    SAVES = Array.isArray(d.saves) ? d.saves : [];
    SAVE_DIR = d.save_dir || SAVE_DIR;
    SAVES_TOTAL = d.total || SAVES.length;
    SAVES_TRUNCATED = d.truncated || 0;
  } catch (e) {
    SAVES = [];
    toast(e.message);
  }
}

function renderSaveList() {
  var count = $('#lobbyCount');
  if (count) count.textContent = SAVES.length ? (SAVES.length + ' 个') : '';

  var more = $('#lobbyTruncated');
  if (more) {
    more.hidden = !SAVES_TRUNCATED;
    if (SAVES_TRUNCATED) {
      // 列表只回最近若干条：如实告知还有多少没显示，而不是假装只有这些。
      more.textContent = '只显示最近 ' + SAVES.length + ' 个存档，另有 ' +
        SAVES_TRUNCATED + ' 个更早的未列出（文件仍在本机）。';
    }
  }

  var box = $('#lobbySaves');
  if (!SAVES.length) {
    box.innerHTML = '<div class="save-empty">还没有存档。<br>' +
      '<small>开一局之后，每一步都会自动写进存档目录。</small></div>';
    return;
  }
  box.innerHTML = SAVES.map(function (s) {
    var tags = '';
    if (!s.named) tags += '<span class="tag">默认名</span>';
    if (s.id === SID) tags += '<span class="tag tag-on">当前</span>';
    return '<div class="save-row' + (s.id === SID ? ' is-current' : '') + '">' +
      '<button class="save-main" data-open="' + esc(s.id) + '">' +
        '<div class="save-name">' + esc(s.name || s.id) + tags + '</div>' +
        '<div class="save-meta">' + esc(s.mtime_text || '—') + ' · ' +
          (s.log_count || 0) + ' 条记录</div>' +
      '</button>' +
      '</div>';
  }).join('');

  $$('[data-open]', box).forEach(function (b) {
    b.addEventListener('click', function () { enterSession(b.dataset.open); });
  });
}

async function enterSession(sid) {
  SID = sid || '';
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

/* ── 本片的事件绑定：大厅底部的「设置 / 关于」──────────────── */
function bindLobby() {
  $('#btnLobbySettings').addEventListener('click', function () {
    renderSettings();
    showView('v-settings');
  });
  $('#btnLobbyAbout').addEventListener('click', function () { openAux('about'); });
}

onInit(bindLobby);
