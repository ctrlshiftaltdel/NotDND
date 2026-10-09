#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atlas 内核回归测试（ATLAS 切片 I1，Issue #63）。

用手工造的两帧（一帧 metric、一帧 abstract）覆盖 ATLAS-DESIGN.md §3.3
的内核契约：四向与上下、对角不穿墙、缺坐标的地点只靠连接存在、
range_band、high_ground、切片不含未发现格和其他 z、
lost_destination 不在最短路上、reroll_unstable 不动锚点边、
carry_across 不换算坐标、增量在地点删除后被丢弃。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_atlas_kernel.py` 运行。
内核不读取 data/worlds/，本测试也不读——两帧全是手工数据。
"""

import copy
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import atlas as al

SURFACE = "w/surface"      # metric 帧
ABSTRACT = "w/abstract"    # abstract 帧

# id 常量（id 不由坐标生成，§3.2）
A = SURFACE + "/a"    # 灰市   (0,0,0)  authored region，带 carry 性状
B = SURFACE + "/b"    # 车站   (1,0,0)  authored region
C = SURFACE + "/c"    # 环园   (1,1,0)  authored region——A 的对角
D = SURFACE + "/d"    # 高台   (0,0,1)  authored region
E = SURFACE + "/e"    # 旧港   (-1,0,0) authored region
N = SURFACE + "/n"    # 北滩   (0,1,0)  authored region
K = SURFACE + "/k"    # 矿坑   (20,0,0) authored region——半径之外
U = SURFACE + "/u"    # 暗门   无坐标，只靠连接存在
G1 = SURFACE + "/g1"  # 工棚   (5,0,0)  generated
G2 = SURFACE + "/g2"  # 货场   (6,0,0)  generated
Z = SURFACE + "/z"    # 孤岩   (0,-5,0) 未发现、无连接
J = SURFACE + "/j"    # 深坑   (0,1,-1) z 更低但无连接
J2 = SURFACE + "/j2"  # 远渊   (2,2,-1) z 更低但切比雪夫 2
FEAT = SURFACE + "/g9_9_0"  # 增量 add_feature 造出的格

P1 = ABSTRACT + "/p1"  # 数据入口 (0,0,0)
P2 = ABSTRACT + "/p2"  # 中枢走廊 (1,0,0)
P3 = ABSTRACT + "/p3"  # 熵流浅滩 (2,0,0)
P4 = ABSTRACT + "/p4"  # 无面集市 (3,0,0)
P5 = ABSTRACT + "/p5"  # 深处     (4,0,0)
P6 = ABSTRACT + "/p6"  # 渊底     (5,0,0)
Q = ABSTRACT + "/q"    # 幽影回廊 (0,-1,0)——通往 P5 的岔路
Q2 = ABSTRACT + "/q2"  # 断链深处 (2,-1,0)——另一条岔路
FE = ABSTRACT + "/fe"  # 回响残片 (0,2,0) abstract 帧的要素


def _place(pid, name, kind, frame, *, x=None, y=None, z=None,
           source="authored", discovered="seen", traits=None):
    place = {"id": pid, "name": name, "kind": kind, "frame_id": frame,
             "source": source, "discovered": discovered,
             "traits": list(traits or [])}
    if x is not None:
        place.update({"x": x, "y": y, "z": z})
    return place


def build_fixture():
    """手工两帧：surface（metric）与 abstract。每次调用全新一份。"""
    atlas = al.new_atlas("w", 20261009)
    al.add_frame(atlas, SURFACE, space="metric", z_meaning="层", cell="街区")
    al.add_frame(atlas, ABSTRACT, space="abstract", z_meaning="深度",
                 cell="一次走位")

    # ── surface 帧的地点 ──────────────────────────────────────────
    al.add_place(atlas, _place(A, "灰市", "region", SURFACE,
                               x=0, y=0, z=0, discovered="seen",
                               traits=["carry"]))
    al.add_place(atlas, _place(B, "车站", "region", SURFACE,
                               x=1, y=0, z=0, discovered="entered"))
    al.add_place(atlas, _place(C, "环园", "region", SURFACE, x=1, y=1, z=0))
    al.add_place(atlas, _place(D, "高台", "region", SURFACE,
                               x=0, y=0, z=1, discovered="entered"))
    al.add_place(atlas, _place(E, "旧港", "region", SURFACE, x=-1, y=0, z=0))
    al.add_place(atlas, _place(N, "北滩", "region", SURFACE, x=0, y=1, z=0))
    al.add_place(atlas, _place(K, "矿坑", "region", SURFACE, x=20, y=0, z=0))
    al.add_place(atlas, _place(U, "暗门", "feature", SURFACE))  # 无坐标
    al.add_place(atlas, _place(G1, "工棚", "room", SURFACE, x=5, y=0, z=0,
                               source="generated"))
    al.add_place(atlas, _place(G2, "货场", "room", SURFACE, x=6, y=0, z=0,
                               source="generated"))
    al.add_place(atlas, _place(Z, "孤岩", "region", SURFACE, x=0, y=-5, z=0,
                               discovered="unseen"))
    al.add_place(atlas, _place(J, "深坑", "feature", SURFACE, x=0, y=1, z=-1))
    al.add_place(atlas, _place(J2, "远渊", "feature", SURFACE, x=2, y=2, z=-1))

    # ── surface 帧的连接 ──────────────────────────────────────────
    al.add_link(atlas, SURFACE, A, B, "东", band="short")
    al.add_link(atlas, SURFACE, A, N, "北", band="short")
    al.add_link(atlas, SURFACE, A, D, "上")
    al.add_link(atlas, SURFACE, B, C, "北", band="short")
    al.add_link(atlas, SURFACE, A, U, "门", band="medium")
    al.add_link(atlas, SURFACE, A, G1, "东", band="long", unstable=True)
    al.add_link(atlas, SURFACE, G1, G2, "东", band="short", unstable=True)
    al.add_link(atlas, SURFACE, A, E, "西", band="medium", unstable=True)
    al.add_link(atlas, SURFACE, G2, K, "门", band="short")

    # ── abstract 帧的地点与连接 ───────────────────────────────────
    al.add_place(atlas, _place(P1, "数据入口", "region", ABSTRACT, x=0, y=0, z=0))
    al.add_place(atlas, _place(P2, "中枢走廊", "zone", ABSTRACT, x=1, y=0, z=0))
    al.add_place(atlas, _place(P3, "熵流浅滩", "zone", ABSTRACT, x=2, y=0, z=0))
    al.add_place(atlas, _place(P4, "无面集市", "zone", ABSTRACT, x=3, y=0, z=0))
    al.add_place(atlas, _place(P5, "深处", "region", ABSTRACT, x=4, y=0, z=0))
    al.add_place(atlas, _place(P6, "渊底", "zone", ABSTRACT, x=5, y=0, z=0))
    al.add_place(atlas, _place(Q, "幽影回廊", "zone", ABSTRACT, x=0, y=-1, z=0))
    al.add_place(atlas, _place(Q2, "断链深处", "zone", ABSTRACT, x=2, y=-1, z=0))
    al.add_place(atlas, _place(FE, "回响残片", "feature", ABSTRACT,
                               x=0, y=2, z=0, discovered="entered"))
    for pair in ((P1, P2), (P2, P3), (P3, P4), (P4, P5), (P5, P6)):
        al.add_link(atlas, ABSTRACT, pair[0], pair[1], "东")
    al.add_link(atlas, ABSTRACT, P1, Q, "南")
    al.add_link(atlas, ABSTRACT, P3, Q2, "南")

    # ── 跨帧连接（抽象帧 ↔ 街面，§5.3：两帧之间只有连接）────────
    al.add_link(atlas, SURFACE, A, P1, "门")   # A 带 carry 性状
    al.add_link(atlas, SURFACE, E, Q, "门")    # 不带 carry——反例用

    return atlas


def locus(pid, frame, x, y, z):
    return {"frame_id": frame, "place_id": pid, "x": x, "y": y, "z": z}


def _drop_place_raw(atlas, pid):
    """模拟「世界 JSON 重编译后该区域消失」：直接摘掉地点与指边。"""
    for frame in atlas["frames"].values():
        frame["places"].pop(pid, None)
        for place in frame["places"].values():
            place["links"] = [l for l in place["links"] if l["to"] != pid]


# ════════════════════════════════════════════════════════════════════════
# 检查项
# ════════════════════════════════════════════════════════════════════════

def check_axis_and_vertical():
    """四向与上下：移动只走 link，落点与坐标都正确。"""
    atlas = build_fixture()
    start = locus(A, SURFACE, 0, 0, 0)

    # 东 → 车站；西回 → 灰市（add_link 自动补反向）
    r = al.move(atlas, start, "东")
    assert r["error"] is None and r["locus"]["place_id"] == B
    assert (r["locus"]["x"], r["locus"]["y"], r["locus"]["z"]) == (1, 0, 0)
    r = al.move(atlas, r["locus"], "西")
    assert r["locus"]["place_id"] == A

    # 北 → 北滩；南回
    r = al.move(atlas, start, "北")
    assert r["locus"]["place_id"] == N
    assert al.move(atlas, r["locus"], "南")["locus"]["place_id"] == A

    # 上 → 高台；下回（楼梯是一条 link）
    r = al.move(atlas, start, "上")
    assert r["locus"]["place_id"] == D
    assert (r["locus"]["x"], r["locus"]["y"], r["locus"]["z"]) == (0, 0, 1)
    r = al.move(atlas, r["locus"], "下")
    assert r["locus"]["place_id"] == A

    # 没有出口的方向不动，并给出 error
    r = al.move(atlas, start, "东南")
    assert r["error"] and r["locus"] == start and r["place"] is None

    # 同一个 via 有多条出口时逐条列出（A 的「东」通车站与工棚）
    east = [e for e in al.exits(atlas, start) if e["via"] == "东"]
    assert {e["place_id"] for e in east} == {B, G1}
    # 出口结构带名字、档位与 unstable 标记
    to_g1 = [e for e in east if e["place_id"] == G1][0]
    assert to_g1["name"] == "工棚" and to_g1["band"] == "long"
    assert to_g1["unstable"] is True


def check_diagonal_no_tunnel():
    """对角不穿墙：对角相邻但没有连接就走不过去，图距离是 2。"""
    atlas = build_fixture()
    start = locus(A, SURFACE, 0, 0, 0)
    # C 在 (1,1)——A 的对角。A 的所有出口里没有 C。
    dests = {e["place_id"] for e in al.exits(atlas, start)}
    assert C not in dests
    # 图距离：A→B→C 为 2，是「近」而不是「相邻」
    assert al.range_band(atlas, SURFACE, A, C) == "near"
    assert al.range_band(atlas, SURFACE, A, B) == "adjacent"


def test_coordless_place_exists_only_via_link():
    """缺坐标的地点不进切片、不占格，但连接照常可走、可投影。"""
    atlas = build_fixture()
    start = locus(A, SURFACE, 0, 0, 0)

    r = al.move(atlas, start, "门")
    assert r["error"] is None and r["locus"]["place_id"] == U
    assert "x" not in r["locus"] and "z" not in r["locus"]

    # 图距离按连接算，与坐标无关
    assert al.range_band(atlas, SURFACE, A, U) == "adjacent"
    # 暗门能回望出口
    back = al.exits(atlas, r["locus"])
    assert any(e["place_id"] == A for e in back)
    # 没坐标的地点给不出切片
    assert "无法切片" in al.slice_text(atlas, r["locus"])


def check_range_band():
    """距离投影只用本帧图距离：0 同区，1 相邻，2 近，3–4 中，更远为远。"""
    atlas = build_fixture()
    assert al.range_band(atlas, SURFACE, A, A) == "same"
    assert al.range_band(atlas, SURFACE, A, B) == "adjacent"
    assert al.range_band(atlas, SURFACE, A, C) == "near"           # 2
    assert al.range_band(atlas, SURFACE, A, K) == "mid"            # 3
    assert al.range_band(atlas, SURFACE, A, Z) == "far"            # 不可达
    # abstract 帧同样按图距离投影，与米无关
    assert al.range_band(atlas, ABSTRACT, P1, P2) == "adjacent"
    assert al.range_band(atlas, ABSTRACT, P1, P4) == "mid"         # 3
    assert al.range_band(atlas, ABSTRACT, P1, P5) == "mid"         # 4
    assert al.range_band(atlas, ABSTRACT, P1, P6) == "far"         # 5
    # 跨帧连接不计入本帧图距离：Q 通旧港（另一帧），但本帧距离到不了 Z
    assert al.range_band(atlas, ABSTRACT, P1, Q) == "adjacent"


def check_high_ground():
    """高地：对方 z 更低 + 平面切比雪夫 ≤ 1 + 存在上/下或相邻连接。"""
    atlas = build_fixture()
    # 高台(0,0,1) 对 灰市(0,0,0)：z 更低、距离 0、有「上」连接 → 占高地
    assert al.high_ground(atlas, SURFACE, D, A) is True
    # 同层不算
    assert al.high_ground(atlas, SURFACE, A, B) is False
    assert al.high_ground(atlas, SURFACE, A, D) is False   # 对方更高
    # z 更低、切比雪夫 ≤ 1，但没有连接 → 不算
    assert al.high_ground(atlas, SURFACE, A, J) is False   # (0,1,-1) 无连接
    assert al.high_ground(atlas, SURFACE, D, E) is False   # (-1,0,0) 无连接
    # z 更低但平面切比雪夫距离 2 → 不算
    assert al.high_ground(atlas, SURFACE, A, J2) is False
    # 缺坐标 → 不算
    assert al.high_ground(atlas, SURFACE, A, U) is False


def check_slice_filtering():
    """切片只画当前 z、只画已发现的格；未发现格与其他 z 一律不出现。"""
    atlas = build_fixture()
    text = al.slice_text(atlas, locus(A, SURFACE, 0, 0, 0))
    assert "@" in text                          # 队伍
    assert "车" in text                         # 车站，已发现
    assert "环" in text                         # 环园，已发现
    assert "工" in text                         # 工棚
    assert "暗" not in text                     # 暗门缺坐标，不占格
    assert "孤" not in text                     # 孤岩未发现
    assert "矿" not in text                     # 矿坑在半径 8 之外
    assert "高" not in text                     # 高台在 z=1，不是本层
    assert "深" not in text                     # 深坑在 z=-1
    assert "层=0" in text                       # 表头标明当前层

    # 上到高台再看切片：本层只有高台自己
    up = al.slice_text(atlas, locus(D, SURFACE, 0, 0, 1))
    assert "层=1" in up and "@" in up
    assert "车" not in up                       # z=0 的东西不进这一层

    # radius 收窄后近处仍在、远处消失
    tight = al.slice_text(atlas, locus(A, SURFACE, 0, 0, 0), radius=2)
    assert "车" in tight and "工" not in tight


def check_lost_destination():
    """迷路落点不在通往 intended 的最短路上，且确实连通可达。"""
    atlas = build_fixture()
    start = locus(P1, ABSTRACT, 0, 0, 0)
    rng = random.Random(7)
    picked = al.lost_destination(atlas, start, P5, rng)
    # P1→P5 的最短路经过 中枢走廊 / 熵流浅滩 / 无面集市
    shortest = {P2, P3, P4}
    assert picked is not None
    assert picked not in shortest and picked not in (P1, P5)
    # 落点必须是已连通的地点（岔路或更深处）
    assert picked in {Q, Q2, P6}
    # 同一种子结果可复现
    again = al.lost_destination(atlas, start, P5, random.Random(7))
    assert again == picked


def check_reroll_unstable_keeps_anchor_edges():
    """重摇只动两端都不是 authored 的边；锚点的边保持不动。"""
    atlas = build_fixture()
    before_ae = [l for l in al.find_place(atlas, A)["links"]
                 if l["to"] == E][0]
    before_ag1 = [l for l in al.find_place(atlas, A)["links"]
                  if l["to"] == G1][0]
    before_g1g2 = [l for l in al.find_place(atlas, G1)["links"]
                   if l["to"] == G2][0]

    count = al.reroll_unstable(atlas, SURFACE, random.Random(11))
    # 只有 工棚—货场 这一条（旧港—灰市、灰市—工棚都沾锚点）
    assert count == 1
    # 锚点边原样：band、unstable 都没动
    assert [l for l in al.find_place(atlas, A)["links"] if l["to"] == E][0] == before_ae
    assert [l for l in al.find_place(atlas, A)["links"] if l["to"] == G1][0] == before_ag1
    # 重摇过的边仍是合法档位、仍标 unstable
    after = [l for l in al.find_place(atlas, G1)["links"] if l["to"] == G2][0]
    assert after["band"] in al.BANDS and after["unstable"] is True
    # 重摇同步到双边记录
    back = [l for l in al.find_place(atlas, G2)["links"] if l["to"] == G1][0]
    assert back["band"] == after["band"]
    # abstract 帧没有 unstable 边，重摇数为 0
    assert al.reroll_unstable(atlas, ABSTRACT, random.Random(11)) == 0


def check_carry_across_keeps_coords():
    """携带要素跨帧：只有带 carry 的连接允许；坐标原样复制不换算。"""
    atlas = build_fixture()
    feature = copy.deepcopy(al.find_place(atlas, FE))
    link = {"from": A, "to": P1, "via": "门"}
    new_id = al.carry_across(atlas, link, feature)
    copy_place = al.find_place(atlas, new_id)
    assert copy_place is not None
    assert copy_place["frame_id"] == SURFACE        # 落到连接另一端所在的帧
    # 不换算坐标：abstract 的格和街区的格不同宽，数字仍原样
    assert (copy_place["x"], copy_place["y"], copy_place["z"]) == (0, 2, 0)
    assert copy_place["name"] == "回响残片"
    assert copy_place["source"] == "generated"

    # 不带 carry 的连接拒绝携带
    try:
        al.carry_across(atlas, {"from": E, "to": Q, "via": "门"}, feature)
    except ValueError:
        pass
    else:
        raise AssertionError("不带 carry 的连接应拒绝 carry_across")


def _apply_baseline_deltas(atlas):
    """三条增量：发现灰市、给车站定性状、凭空补一格。"""
    al.apply_delta(atlas, {"op": "discover", "place_id": A,
                           "payload": {"level": "entered"}})
    al.apply_delta(atlas, {"op": "set_trait", "place_id": B,
                           "payload": {"trait": "barrier", "value": True}})
    al.apply_delta(atlas, {"op": "add_feature", "place_id": FEAT,
                           "payload": _place(FEAT, "掩体残垛", "feature",
                                             SURFACE, x=9, y=9, z=0,
                                             discovered="seen")})


def check_deltas_dropped_after_place_removal():
    """增量保存后再读回：地点已删除的增量被丢弃，队伍搬迁到最近锚点。"""
    # 出发图：打增量、把队伍放到车站，导出存档块
    atlas = build_fixture()
    _apply_baseline_deltas(atlas)
    state = al.export_state(atlas, locus(B, SURFACE, 1, 0, 0))
    assert state["version"] == 1 and state["world_key"] == "w"
    assert state["frames"][SURFACE]["seed"] is None
    assert len(state["frames"][SURFACE]["deltas"]) == 3

    # 目标图：同一份手工数据，但车站已从世界 JSON 里消失
    atlas2 = build_fixture()
    _drop_place_raw(atlas2, B)
    result = al.restore_state(atlas2, state)
    notes = result["notes"]

    # 车站的增量被丢弃，且没有复活
    assert any("set_trait" in n and B in n for n in notes)
    assert al.find_place(atlas2, B) is None
    # 其余增量照常重放
    assert al.find_place(atlas2, A)["discovered"] == "entered"
    assert al.find_place(atlas2, FEAT) is not None
    assert (al.find_place(atlas2, FEAT)["x"],
            al.find_place(atlas2, FEAT)["y"]) == (9, 9)
    # 队伍原在车站：搬迁到坐标最近的 authored 区域（灰市，并列时 id 最小）
    assert result["locus"]["place_id"] == A
    assert any("搬迁" in n and B in n for n in notes)

    # 对照：地点都在时重放同一份存档，无丢弃、无搬迁，位置原样
    atlas3 = build_fixture()
    result3 = al.restore_state(atlas3, state)
    assert result3["notes"] == []
    assert result3["locus"]["place_id"] == B
    assert al.find_place(atlas3, A)["discovered"] == "entered"
    assert "barrier" in al.find_place(atlas3, B)["traits"]
    assert al.find_place(atlas3, FEAT) is not None


def check_guide_card():
    """地点卡：导引者只读卡不读图——含地点、帧口径与出口。"""
    atlas = build_fixture()
    card = al.guide_card(atlas, locus(A, SURFACE, 0, 0, 0))
    assert card["place"]["name"] == "灰市"
    assert card["frame"]["space"] == "metric" and card["frame"]["cell"] == "街区"
    assert any(e["place_id"] == D for e in card["exits"])
    assert al.guide_card(atlas, {"frame_id": SURFACE, "place_id": "无"}) is None


CHECKS = (
    check_axis_and_vertical,
    check_diagonal_no_tunnel,
    test_coordless_place_exists_only_via_link,
    check_range_band,
    check_high_ground,
    check_slice_filtering,
    check_lost_destination,
    check_reroll_unstable_keeps_anchor_edges,
    check_carry_across_keeps_coords,
    check_deltas_dropped_after_place_removal,
    check_guide_card,
)


def main():
    failures = 0
    for check in CHECKS:
        try:
            check()
        except AssertionError as error:
            print("  断言失败: %s — %s" % (check.__name__, error))
            failures += 1
        except Exception as error:  # 意外异常也算失败，但留全栈
            import traceback
            print("  异常: %s" % check.__name__)
            traceback.print_exc()
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(CHECKS) - failures, len(CHECKS), failures))
        return 1
    print("%d/%d 项通过" % (len(CHECKS), len(CHECKS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
