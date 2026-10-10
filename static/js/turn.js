/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U2：跑团主循环（叙事流 + 行动区）
   叙事流渲染、行动按钮渲染与选择占位。
   共用件在 js/core.js；本片后续要接输入区与 /api/guide/turn
   时只改本文件（与 css/stream.css、index.html）。
   ═══════════════════════════════════════════════════════════ */
'use strict';

function renderStream() {
  var box = $('#stream');
  var log = (ST && ST.log) || [];
  if (!log.length) {
    box.innerHTML = '<div class="stream-empty">还没有叙事。开局后每一步都会追加在这里。</div>';
    return;
  }
  box.innerHTML = log.map(function (e) {
    var kind = e.kind === 'act' ? 'act' : (e.kind === 'note' ? 'note' : 'narrate');
    return '<div class="msg msg-' + kind + '">' + esc(e.text || '') + '</div>';
  }).join('');
}

/* 「你可以」行动按钮。出口按钮不在这里——那是 U3 的片（js/atlas.js）。 */
function renderActions() {
  var acts = (ST && ST.actions) || [];
  $('#actList').innerHTML = acts.length
    ? acts.map(function (a, i) {
        return '<button class="btn act-btn" data-act="' + esc(a.id) + '">' +
          '<span class="act-key">' + (i + 1) + '</span>' +
          '<span class="act-label">' + esc(a.label) + '</span>' +
          (a.hint ? '<span class="act-hint">' + esc(a.hint) + '</span>' : '') +
          '</button>';
      }).join('')
    : '<div class="stream-empty">本场景没有可选行动。</div>';

  $$('#actList [data-act]').forEach(function (b) {
    b.addEventListener('click', function () { pickAction(b.dataset.act); });
  });
}

function pickAction(id) {
  var a = ((ST && ST.actions) || []).find(function (x) { return x.id === id; });
  buzz(12);
  toast(a ? '示例骨架：你选择了「' + a.label + '」（未接后端）' : '示例骨架：已选择该行动');
}
