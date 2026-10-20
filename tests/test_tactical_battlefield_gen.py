#!/usr/bin/env python3
"""六角格战场生成器回归（Issue #418）。

覆盖：
- 模板契约冻结：模块 docstring 携带 02C 第十一节字段表、九类 category、
  无 data/** 落盘、模块源码不内嵌规则阈值字面量（阈值只引用规则数据）；
- 三个 fixture（tests/fixtures/battlefield_gen/）：室内房间（metric）、
  户外不规则空地（abstract）、双高度 + 垂直连接（metric）；
- seed 复现：同 seed 同输出；不同 seed 合理改变非锚定细节（自动放置、
  outdoor 剪枝），锚定细节（显式格位）不变；
- 校验：入口 / 出生点可达（复用 plan_move）、占格互斥、入口 / 出生点不被
  对象压住、无效高度（步长超规则数据阈值）、未知字段、断路（custom 双岛）；
- 受控降级：场景要素放不下 / 未映射时记入 diagnostics，不悄悄丢弃；
- 输入不被改写：scene / template 只读；
- 版本显式可见：generator_version 含生成器版本与模板版本。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_tactical_battlefield_gen.py` 运行。
"""
import copy
import json
import os
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import tactical_battlefield as battlefield_model  # noqa: E402
import tactical_battlefield_gen as gen  # noqa: E402

FIXTURE_DIR = os.path.join(TESTS, "fixtures", "battlefield_gen")
MODULE_PATH = os.path.join(ROOT, "tactical_battlefield_gen.py")

RULES = battlefield_model.space_rules()
HEX = RULES["scale"]["hex_size_cm"]
GRADES = RULES["cover"]["grades"]
LINK_KINDS = RULES["vertical_links"]["kinds"]
SLOPE_MAX = RULES["movement"]["slope_max_cm"]


def load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def room_scene():
    return {
        "world_id": "fixture-world",
        "site_id": "site-room-01",
        "atlas_ref": "fixture-world/room-01",
        "place": {
            "id": "room-01",
            "name": "test-room",
            "kind": "room",
            "frame_id": "local",
            "features": [{"tag": "barrel-stack", "text": "stacked crates"}],
        },
        "features": [{"tag": "loose-rubble", "text": "scattered rubble"}],
    }


def outdoor_scene():
    return {
        "world_id": "fixture-world",
        "place": {
            "id": "clearing-07",
            "name": "test-clearing",
            "kind": "site",
            "frame_id": "wilds",
            "features": [
                {"tag": "overgrowth", "text": "dense thicket"},
                {"tag": "shrine", "text": "old shrine"},
                {"tag": "completely-unmapped", "text": "no declaration"},
            ],
        },
    }


def errors_codes(error):
    return sorted({item["code"] for item in error.errors})


def cells_by_surface(result):
    by_surface = {}
    for cell in result["battlefield"]["cells"]:
        by_surface.setdefault(cell["surface_id"], []).append(cell)
    return by_surface


# ── 契约冻结 ────────────────────────────────────────────────────────────

def check_docstring_carries_template_contract():
    """模板字段表以 02C 第十一节为权威，冻结在模块 docstring。"""
    doc = gen.__doc__
    for field in ("template_id", "template_version", "shape", "size", "radius",
                  "surfaces", "entry_cells", "spawn_points", "features",
                  "encounter_meta"):
        assert field in doc, "docstring 缺少模板字段 %s" % field
    for kind in ("room", "outdoor", "custom"):
        assert kind in doc, "docstring 缺少 shape 枚举 %s" % kind
    assert "02c-hex-battlefield.md" in doc, "docstring 未声明 02C 出处"
    assert "不得内嵌" in doc, "docstring 未声明模板不内嵌规则数值"


def check_module_contract_guards():
    """不落 data/**、不写文件、不内嵌规则阈值字面量。"""
    with open(MODULE_PATH, encoding="utf-8") as handle:
        source = handle.read()
    assert "open(" not in source, "生成器不得读写文件"
    assert "import os" not in source, "生成器不需要 os"
    for literal in ("150", "300", "1d6"):
        assert literal not in source, (
            "模块源码内嵌了疑似规则阈值字面量 %r（阈值只允许引用规则数据）" % literal)
    assert gen.MAX_SURFACES == 3
    assert gen.OBJECT_CATEGORIES == (
        "blocker", "cover", "hazard", "mechanism", "boost",
        "mobility", "pickup", "movable", "destructible",
    ), "九类 category 必须与 02C:308 冻结枚举一致"


def check_unknown_template_field_rejected():
    template = load_fixture("room_metric.json")
    template["totally_bogus"] = 1
    try:
        gen.generate_battlefield(room_scene(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "unknown_field" in errors_codes(error), error.errors
    else:
        raise AssertionError("未知模板字段未被拒绝")


def check_unknown_feature_field_rejected():
    template = load_fixture("room_metric.json")
    template["features"][0]["totally_bogus"] = 1
    try:
        gen.generate_battlefield(room_scene(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "unknown_field" in errors_codes(error), error.errors
    else:
        raise AssertionError("feature 未知字段未被拒绝")


# ── 室内房间（metric） ───────────────────────────────────────────────────

def check_room_metric_generation():
    template = load_fixture("room_metric.json")
    result = gen.generate_battlefield(room_scene(), template, 2026)
    battlefield = result["battlefield"]
    battlefield_model.load_battlefield(battlefield)  # 必须通过既有校验器

    assert battlefield["world_id"] == "fixture-world"
    assert battlefield["site_id"] == "site-room-01"
    assert battlefield["atlas_ref"] == "fixture-world/room-01"
    assert battlefield["seed"] == 2026
    assert battlefield["scale"] == {"mode": "metric", "hex_size_cm": HEX}
    assert len(battlefield["cells"]) == 30
    assert battlefield["vertical_links"] == []
    ids = [cell["cell_id"] for cell in battlefield["cells"]]
    assert len(set(ids)) == 30, "cell_id 必须唯一"
    assert all(cell["movement_cost"] == HEX for cell in battlefield["cells"])
    assert all("elevation_cm" in cell for cell in battlefield["cells"])

    by_coord = {(c["q"], c["r"]): c for c in battlefield["cells"]}
    entry = by_coord[(0, 2)]
    assert entry["spawnable"] is True
    cover = by_coord[(3, 2)]
    assert cover["terrain_id"] == "barricade-line"
    assert cover["cover_grade"] in GRADES
    assert cover["obstacle_height"] == 90
    assert "cover-line" in cover["feature_refs"]
    vent = by_coord[(4, 3)]
    assert vent["terrain_id"] == "hazard-vent"
    assert vent["hazard_refs"] == ["hazard-vent-01"]

    # 自动放置的两个对象：都在可进入格、不压入口 / 出生点、来源可追溯。
    assert len(result["objects"]) == 2
    protected = {(0, 2), (5, 2), (4, 1)}
    for obj in result["objects"]:
        assert obj["category"] == "movable"
        assert obj["state"] == "intact"
        cell = next(c for c in battlefield["cells"]
                    if c["cell_id"] == obj["cell_id"])
        assert cell["blocks_movement"] is False
        assert (cell["q"], cell["r"]) not in protected
        assert cell["spawnable"] is False
    assert result["encounter_meta"] == {"enemy_min": 1, "enemy_max": 3, "waves": 1}
    assert result["diagnostics"]["counts"]["cells"] == 30
    assert result["diagnostics"]["counts"]["objects"] == 2
    # 场景要素 barrel-stack / loose-rubble 没有匹配声明 → 受控降级记录。
    unmapped = [item["tag"] for item in result["diagnostics"]["unmapped_features"]]
    assert unmapped == ["barrel-stack", "loose-rubble"]


def check_same_seed_reproducible():
    template = load_fixture("room_metric.json")
    first = gen.generate_battlefield(room_scene(), template, 2026)
    second = gen.generate_battlefield(room_scene(), template, 2026)
    assert first == second, "同 seed 必须复现同一战场"

    str_seed = gen.generate_battlefield(room_scene(), template, "alpha-seed")
    assert str_seed["battlefield"]["seed"] == "alpha-seed"
    again = gen.generate_battlefield(room_scene(), template, "alpha-seed")
    assert str_seed == again, "字符串 seed 同样必须复现"


def check_different_seed_changes_unanchored_details():
    template = load_fixture("room_metric.json")
    base = gen.generate_battlefield(room_scene(), template, 1)
    other = gen.generate_battlefield(room_scene(), template, 2)
    # 锚定细节不变：格位集、显式地形格、入口 / 出生点。
    assert [c["cell_id"] for c in base["battlefield"]["cells"]] == \
           [c["cell_id"] for c in other["battlefield"]["cells"]]
    # 非锚定细节：自动放置对象位置随 seed 合理变化。
    base_at = sorted(obj["cell_id"] for obj in base["objects"])
    other_at = sorted(obj["cell_id"] for obj in other["objects"])
    assert base_at != other_at, "不同 seed 应改变自动放置的非锚定细节"


def check_version_and_identity_recorded():
    template = load_fixture("room_metric.json")
    result = gen.generate_battlefield(room_scene(), template, 7)
    battlefield = result["battlefield"]
    assert gen.GENERATOR_VERSION in battlefield["generator_version"]
    assert "tpl-1" in battlefield["generator_version"], "模板版本必须显式可见"
    assert battlefield["battlefield_id"] == "bf-test-room-basic-7"


def check_inputs_not_mutated():
    template = load_fixture("room_metric.json")
    scene = room_scene()
    template_copy = copy.deepcopy(template)
    scene_copy = copy.deepcopy(scene)
    gen.generate_battlefield(scene, template, 5)
    assert template == template_copy, "模板 dict 不得被改写"
    assert scene == scene_copy, "场景 dict 不得被改写"


# ── 户外不规则（abstract） ───────────────────────────────────────────────

def check_outdoor_irregular_generation():
    template = load_fixture("outdoor_irregular.json")
    result = gen.generate_battlefield(outdoor_scene(), template, 418,
                                      mode="abstract")
    battlefield = result["battlefield"]
    battlefield_model.load_battlefield(battlefield)

    assert battlefield["scale"]["mode"] == "abstract"
    assert battlefield["scale"]["unit"] in RULES["scale"]["abstract_units"]
    assert all("elevation_level" in c for c in battlefield["cells"])
    assert all(c["movement_cost"] == 1 for c in battlefield["cells"])

    # 不规则：完整六角盘（radius=5 → 91 格）被确定性剪枝，但必须保留连通主体。
    full_disk = 1 + 3 * 5 * 6
    cells = battlefield["cells"]
    assert len(cells) < full_disk, "outdoor 必须剪枝，不能是完整六角盘"
    assert len(cells) > full_disk // 2, "剪枝过半说明生成器坏了"
    pruned = result["diagnostics"]["counts"]["cells_pruned"]
    assert pruned == full_disk - len(cells)
    # 连通性：从入口出发的可达集覆盖全场（无 blocks 格时）。
    coords = {(c["q"], c["r"]) for c in cells}

    def on_disk(q, r):
        return max(abs(q), abs(r), abs(q + r)) <= 5

    boundary = [(q, r) for (q, r) in coords if
                on_disk(q, r) and any(n not in coords
                                      for n in [(q + 1, r), (q + 1, q * 0 + r - 1),
                                                (q, r - 1), (q - 1, r),
                                                (q - 1, r + 1), (q, r + 1)])]
    assert boundary, "剪枝后的战场应保留边界环上的格"

    # 场景要素映射：overgrowth → thicket 地形；shrine → boost 对象；
    # completely-unmapped → 受控降级记录。
    thickets = [c for c in cells if c["terrain_id"] == "thicket"]
    assert thickets, "overgrowth 场景要素必须映射为 thicket 地形"
    assert all(c["blocks_los"] is True for c in thickets)
    shrines = [o for o in result["objects"] if o["definition_id"] == "test.shrine"]
    assert len(shrines) == 1
    assert shrines[0]["category"] == "boost"
    assert shrines[0]["feature_refs"], "对象必须保留场景要素来源引用"
    unmapped = [item["tag"] for item in result["diagnostics"]["unmapped_features"]]
    assert unmapped == ["completely-unmapped"]


def check_different_seed_changes_outdoor_shape():
    template = load_fixture("outdoor_irregular.json")
    first = gen.generate_battlefield(outdoor_scene(), template, 11,
                                     mode="abstract")
    second = gen.generate_battlefield(outdoor_scene(), template, 12,
                                      mode="abstract")
    first_coords = {(c["q"], c["r"]) for c in first["battlefield"]["cells"]}
    second_coords = {(c["q"], c["r"]) for c in second["battlefield"]["cells"]}
    assert first_coords != second_coords, "不同 seed 应改变不规则剪枝形状"
    # 锚定格（入口 / 出生点）在两个 seed 下都必须在场。
    for result in (first, second):
        coords = {(c["q"], c["r"]) for c in result["battlefield"]["cells"]}
        assert (0, 0) in coords
        assert (2, 1) in coords and (-2, 2) in coords


# ── 双高度 + 垂直连接（metric） ─────────────────────────────────────────

def check_two_level_generation():
    template = load_fixture("two_level_metric.json")
    result = gen.generate_battlefield(room_scene_empty(), template, 418)
    battlefield = result["battlefield"]
    battlefield_model.load_battlefield(battlefield)

    by_surface = cells_by_surface(result)
    assert sorted(by_surface) == ["gallery", "ground"]
    assert len(by_surface["ground"]) == 20 and len(by_surface["gallery"]) == 20
    assert all(c["elevation_cm"] >= 250 for c in by_surface["gallery"])
    assert all(c["elevation_cm"] == 0 for c in by_surface["ground"])
    # 层内起伏不超过规则数据坡面阈值（引用值，不内嵌）。
    gallery = sorted(by_surface["gallery"], key=lambda c: c["cell_id"])
    assert {c["elevation_cm"] for c in gallery} <= {250, 250 + 40}

    links = battlefield["vertical_links"]
    assert len(links) == 1
    link = links[0]
    assert link["kind"] == "stairs"
    assert link["bidirectional"] is True
    assert link["kind"] in LINK_KINDS
    from_cell = battlefield_model.cell_by_id(battlefield, link["from_cell_id"])
    to_cell = battlefield_model.cell_by_id(battlefield, link["to_cell_id"])
    assert from_cell["surface_id"] == "ground"
    assert to_cell["surface_id"] == "gallery"
    assert (from_cell["q"], from_cell["r"]) == (1, 1)
    assert (to_cell["q"], to_cell["r"]) == (1, 1)

    # 出生点分布两层；生成成功即代表入口 → 两层出生点均可达（生成器已校验）。
    spawns = result["battlefield"]["cells"]
    gallery_spawn = [c for c in spawns if c["surface_id"] == "gallery"
                     and (c["q"], c["r"]) == (3, 3)]
    assert len(gallery_spawn) == 1
    railing = [c for c in by_surface["gallery"]
               if c["terrain_id"] == "railing"]
    # disk(center=(2,0), radius=1) 的 7 格中，r=-1 的两格不在房间内 → 5 格落地。
    assert len(railing) == 5
    assert all(c["cover_grade"] == "light" for c in railing)


def room_scene_empty():
    """无场景要素的最小场景（两高度 fixture 的模板不含 source_feature）。"""
    return {
        "world_id": "fixture-world",
        "place": {"id": "hall-01", "name": "test-hall", "kind": "room",
                  "frame_id": "local"},
    }


# ── 校验与错误路径 ──────────────────────────────────────────────────────

def check_invalid_elevation_step_references_rule_threshold():
    template = load_fixture("two_level_metric.json")
    template["surfaces"][1]["elevation_step"] = SLOPE_MAX + 10
    try:
        gen.generate_battlefield(room_scene_empty(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "invalid_height" in errors_codes(error), error.errors
        assert "slope_max_cm" in str(error), "错误信息必须引用规则数据阈值名"
    else:
        raise AssertionError("超阈值的 metric 步长未被拒绝")

    # abstract 口径：步长以层为单位（引用 tactical_movement 的常量，不换算）。
    abstract_template = load_fixture("outdoor_irregular.json")
    abstract_template["surfaces"] = [
        {"surface_id": "ground"},
        {"surface_id": "upper", "elevation": 1, "elevation_step": 2,
         "link": {"kind": "stairs", "at": {"q": 0, "r": 0}}},
    ]
    try:
        gen.generate_battlefield(outdoor_scene(), abstract_template, 1,
                                 mode="abstract")
    except gen.BattlefieldGenError as error:
        assert "invalid_height" in errors_codes(error), error.errors
    else:
        raise AssertionError("abstract 超步长未被拒绝")


def check_entry_and_spawn_validation():
    template = load_fixture("room_metric.json")
    template["entry_cells"] = [{"q": 99, "r": 99}]
    try:
        gen.generate_battlefield(room_scene(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "cell_not_on_board" in errors_codes(error), error.errors
    else:
        raise AssertionError("不在场内的入口未被拒绝")

    template = load_fixture("room_metric.json")
    template["spawn_points"] = [{"q": 0, "r": 2}]
    try:
        gen.generate_battlefield(room_scene(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "spawn_conflict" in errors_codes(error), error.errors
    else:
        raise AssertionError("出生点压入口未被拒绝")


def check_disconnected_custom_board_rejected():
    template = {
        "template_id": "test-broken-island",
        "template_version": "1",
        "shape": {"kind": "custom",
                  "coords": [{"q": 0, "r": 0}, {"q": 1, "r": 0},
                             {"q": 6, "r": 6}, {"q": 7, "r": 6}]},
        "entry_cells": [{"q": 0, "r": 0}],
        "spawn_points": [{"q": 6, "r": 6}],
    }
    try:
        gen.generate_battlefield(room_scene_empty(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "spawn_unreachable" in errors_codes(error), error.errors
    else:
        raise AssertionError("断路上的出生点未被拒绝")


def check_object_overlap_rejected():
    template = load_fixture("room_metric.json")
    template["features"] = [
        {"feature_id": "blocker-a", "kind": "object", "at": {"q": 2, "r": 2},
         "object": {"definition_id": "test.blocker", "category": "blocker",
                    "blocks_movement": True}},
        {"feature_id": "blocker-b", "kind": "object", "at": {"q": 2, "r": 2},
         "object": {"definition_id": "test.blocker", "category": "blocker",
                    "blocks_movement": True}},
    ]
    try:
        gen.generate_battlefield(room_scene(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "overlap" in errors_codes(error), error.errors
    else:
        raise AssertionError("同格实体重叠未被拒绝")


def check_object_cannot_cover_entry_or_spawn():
    template = load_fixture("room_metric.json")
    template["features"] = [
        {"feature_id": "door-blocker", "kind": "object", "at": {"q": 0, "r": 2},
         "object": {"definition_id": "test.blocker", "category": "blocker",
                    "blocks_movement": True}},
    ]
    try:
        gen.generate_battlefield(room_scene(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "spawn_conflict" in errors_codes(error), error.errors
    else:
        raise AssertionError("对象压住入口未被拒绝")

    template = load_fixture("room_metric.json")
    template["features"] = [
        {"feature_id": "spawn-blocker", "kind": "object",
         "at": {"q": 5, "r": 2},
         "object": {"definition_id": "test.blocker", "category": "blocker"}},
    ]
    try:
        gen.generate_battlefield(room_scene(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "spawn_conflict" in errors_codes(error), error.errors
    else:
        raise AssertionError("对象压住出生点未被拒绝")


def check_scene_mapping_controlled_degradation():
    """场景要素显式区域放不下 → 受控降级，不悄悄丢弃也不报错。"""
    template = {
        "template_id": "test-degrade",
        "template_version": "1",
        "shape": {"kind": "room"},
        "size": {"width": 3, "height": 1},
        "entry_cells": [{"q": 0, "r": 0}],
        "features": [
            {"feature_id": "crates", "kind": "object", "source_feature": "crate",
             "at": {"q": 1, "r": 0},
             "object": {"definition_id": "test.crate", "category": "movable"}},
        ],
    }
    scene = {
        "world_id": "fixture-world",
        "place": {"id": "room-x", "name": "x", "kind": "room",
                  "frame_id": "local",
                  "features": [{"tag": "crate"}, {"tag": "crate"},
                               {"tag": "crate"}]},
    }
    result = gen.generate_battlefield(scene, template, 1)
    # 同一显式格位只放得下一个，其余两个受控降级。
    assert len(result["objects"]) == 1
    unplaced = result["diagnostics"]["unplaced_features"]
    assert len(unplaced) == 2
    assert all(item["tag"] == "crate" for item in unplaced)
    assert result["diagnostics"]["counts"]["features_placed"] == 1
    assert result["diagnostics"]["counts"]["features_unplaced"] == 2


def check_mode_is_caller_argument():
    """mode 是 generate_battlefield 的参数，不是模板字段。"""
    template = load_fixture("room_metric.json")
    assert "mode" not in template, "mode 不得出现在模板字段表里"
    result = gen.generate_battlefield(room_scene(), template, 3, mode="abstract")
    battlefield = result["battlefield"]
    assert battlefield["scale"]["mode"] == "abstract"
    assert "hex_size_cm" not in battlefield["scale"]
    assert all("elevation_level" in c for c in battlefield["cells"])
    assert all("elevation_cm" not in c for c in battlefield["cells"])

    try:
        gen.generate_battlefield(room_scene(), template, 3, mode="cubits")
    except gen.BattlefieldGenError as error:
        assert "invalid_type" in errors_codes(error), error.errors
    else:
        raise AssertionError("非法 mode 未被拒绝")


def check_invalid_height_payload_blocked():
    """对象 category 必须是九类之一；cover_grade 必须是规则数据四档之一。"""
    template = load_fixture("room_metric.json")
    template["features"] = [
        {"feature_id": "bad-cat", "kind": "object", "count": 1,
         "object": {"definition_id": "test.x", "category": "turret"}},
    ]
    try:
        gen.generate_battlefield(room_scene(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "invalid_type" in errors_codes(error), error.errors
    else:
        raise AssertionError("非法 category 未被拒绝")

    template = load_fixture("room_metric.json")
    template["features"] = [
        {"feature_id": "bad-grade", "kind": "terrain", "at": {"q": 1, "r": 1},
         "terrain": {"terrain_id": "t", "cover_grade": "super"}},
    ]
    try:
        gen.generate_battlefield(room_scene(), template, 1)
    except gen.BattlefieldGenError as error:
        assert "invalid_type" in errors_codes(error), error.errors
    else:
        raise AssertionError("非法 cover_grade 未被拒绝")


def check_world_key_has_no_special_branching():
    """不同 world_id 走同一条生成路径：同模板同 seed 同布局。"""
    template = load_fixture("room_metric.json")
    scene_a = room_scene()
    scene_a["world_id"] = "world-a"
    scene_b = room_scene()
    scene_b["world_id"] = "world-b"
    result_a = gen.generate_battlefield(scene_a, template, 42)
    result_b = gen.generate_battlefield(scene_b, template, 42)
    cells_a = [{k: v for k, v in c.items() if k != "cell_id"}
               for c in result_a["battlefield"]["cells"]]
    cells_b = [{k: v for k, v in c.items() if k != "cell_id"}
               for c in result_b["battlefield"]["cells"]]
    assert cells_a == cells_b, "world 不同不得改变生成路径（数据驱动）"


# ── 入口 ────────────────────────────────────────────────────────────────

CHECKS = (
    check_docstring_carries_template_contract,
    check_module_contract_guards,
    check_unknown_template_field_rejected,
    check_unknown_feature_field_rejected,
    check_room_metric_generation,
    check_same_seed_reproducible,
    check_different_seed_changes_unanchored_details,
    check_version_and_identity_recorded,
    check_inputs_not_mutated,
    check_outdoor_irregular_generation,
    check_different_seed_changes_outdoor_shape,
    check_two_level_generation,
    check_invalid_elevation_step_references_rule_threshold,
    check_entry_and_spawn_validation,
    check_disconnected_custom_board_rejected,
    check_object_overlap_rejected,
    check_object_cannot_cover_entry_or_spawn,
    check_scene_mapping_controlled_degradation,
    check_mode_is_caller_argument,
    check_invalid_height_payload_blocked,
    check_world_key_has_no_special_branching,
)


def main():
    failures = 0
    for check in CHECKS:
        try:
            check()
        except AssertionError as error:
            print("  断言失败: %s — %s" % (check.__name__, error))
            failures += 1
        except Exception as error:  # noqa: BLE001 — 回归脚本要看到全部失败
            print("  异常: %s — %r" % (check.__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(CHECKS) - failures, len(CHECKS), failures))
        return 1
    print("%d/%d 项通过" % (len(CHECKS), len(CHECKS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
