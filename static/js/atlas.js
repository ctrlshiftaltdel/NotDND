/* ═══════════════════════════════════════════════════════════
   棱镜 · 片 U3：出口与移动（ATLAS 接线）
   「前往」出口列表的渲染与选择占位。出口按钮与行动按钮同源
   （都读 ST.exits / ST.actions），但分属两片：移动接的是
   /api/atlas/exits 与 /api/atlas/move，本片后续只改本文件
   （与 css/ 下本片自己的样式文件、index.html）。
   ═══════════════════════════════════════════════════════════ */
'use strict';

/* 「前往」出口按钮。数据来源现状是 ST.exits，接后端后改为 /api/atlas/exits。 */
function renderExits() {
  var exits = (ST && ST.exits) || [];
  $('#exitList').innerHTML = exits.length
    ? exits.map(function (x) {
        return '<button class="btn act-btn" data-exit="' + esc(x.id) + '">' +
          '<span class="act-label">' + esc(x.label) + '</span>' +
          '</button>';
      }).join('')
    : '<div class="stream-empty">没有已知出口。</div>';

  $$('#exitList [data-exit]').forEach(function (b) {
    b.addEventListener('click', function () { pickExit(b.dataset.exit); });
  });
}

function pickExit(id) {
  var x = ((ST && ST.exits) || []).find(function (e) { return e.id === id; });
  buzz(12);
  toast(x ? '示例骨架：前往「' + x.label + '」（未接后端）' : '示例骨架：已选择该出口');
}
