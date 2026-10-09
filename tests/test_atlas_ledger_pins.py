#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""账本地点钉住回归测试（ATLAS 切片 I6，Issue #68）。

剧本 `ledger_template` 的地点字段（`locations[]` 条目、`npcs[].location`
为非空字符串的条目）必须带 `place_id` / `pinned` 两个字段。钉住规则：

1. 目标地图是 `data/worlds/<meta.world>.json` 编译出的 atlas；
   `meta.world` 没有对应世界模组时，一律未钉。
2. 区域主名 = 区域 `name` 全角竖线（｜）前部分（与 atlas_compile 一致）。
3. 地点字符串与区域主名或关键地点全文**互为包含**（任意方向）即产生候选。
4. 候选恰好一个 → `pinned: true` + 该 place id（`<key>/<region-id>` 或
   `<key>/<region-id>/k<索引>`）；候选 0 个或多个 → `pinned: false` +
   `place_id: null`，显示名保持原字符串，不臆造坐标。

本测试把上述规则作为可执行断言：数据偏离规则即红。钉中的 place_id
还必须能在编译后的 atlas 里解析到。零依赖：仅 Python 3 标准库；
直接 `python3 tests/test_atlas_ledger_pins.py` 运行。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import atlas as al
import atlas_compile as ac


def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return json.load(handle)


LEXICON = _load("data/atlas/lexicon.json")

WORLDS = {}
for _name in sorted(os.listdir(os.path.join(ROOT, "data", "worlds"))):
    if _name.endswith(".json"):
        _doc = _load("data/worlds/" + _name)
        WORLDS[_doc["world"]["key"]] = _doc

SCENARIOS = {}
for _name in sorted(os.listdir(os.path.join(ROOT, "data", "scenarios"))):
    if _name.endswith(".json"):
        _doc = _load("data/scenarios/" + _name)
        SCENARIOS[_name] = _doc

assert SCENARIOS, "data/scenarios/ 下没有剧本"


def main_name(name):
    """区域主名：全角竖线之前的部分（§4.4，与 atlas_compile._main_name 一致）。"""
    return (name or "").split("｜", 1)[0].strip()


def candidates(key, text):
    """地点字符串的候选 place id（互为包含；0/1/多个都可能）。"""
    world = WORLDS.get(key)
    if world is None:
        return []
    hits = set()
    for region in world.get("regions", []):
        main = main_name(region.get("name"))
        if main and (main in text or text in main):
            hits.add("%s/%s" % (key, region.get("id")))
        for index, kp in enumerate(region.get("key_places", [])):
            if kp and (kp in text or text in kp):
                hits.add("%s/%s/k%d" % (key, region.get("id"), index))
    return sorted(hits)


def expected_pin(key, text):
    """返回 (place_id, pinned)。唯一命中才钉；否则未钉。"""
    hits = candidates(key, text)
    if len(hits) == 1:
        return hits[0], True
    return None, False


_ATLAS_PLACES = {}


def atlas_place_ids(key):
    """编译世界并返回其全部 place id（结果按 key 缓存）。"""
    if key not in _ATLAS_PLACES:
        atlas = ac.compile_world(WORLDS[key], LEXICON, seed=0)
        ids = set()
        for frame in atlas["frames"].values():
            ids.update(frame["places"])
        assert ids, "世界 %s 编译后没有任何 place" % key
        _ATLAS_PLACES[key] = ids
    return _ATLAS_PLACES[key]


def _location_entries(doc):
    """产出 (kind, entry, display_text)——账本里带地点字符串的可钉条目。"""
    ledger = doc.get("ledger_template") or {}
    for entry in ledger.get("locations", []):
        name = entry.get("name")
        if isinstance(name, str) and name:
            yield "locations", entry, name
    for entry in ledger.get("npcs", []):
        loc = entry.get("location")
        if isinstance(loc, str) and loc:
            yield "npcs", entry, loc


def _pin_fields(entry, where, errors):
    """校验单条目的钉住字段形状；返回 (place_id, pinned)。"""
    if "pinned" not in entry or "place_id" not in entry:
        errors.append("%s 缺 place_id/pinned 字段" % where)
        return None, None
    pinned = entry["pinned"]
    place_id = entry["place_id"]
    if not isinstance(pinned, bool):
        errors.append("%s pinned 应为布尔: %r" % (where, pinned))
    if pinned and not isinstance(place_id, str):
        errors.append("%s pinned=true 但 place_id 不是字符串: %r" % (where, place_id))
    if not pinned and place_id is not None:
        errors.append("%s pinned=false 但 place_id=%r（应为 null）" % (where, place_id))
    return place_id, pinned


def check_shape_and_rule():
    """全部剧本：可钉条目字段齐全、钉住结果与规则一致、place_id 可解析。"""
    errors = []
    annotated = 0
    pinned_count = 0
    for fname in sorted(SCENARIOS):
        doc = SCENARIOS[fname]
        key = (doc.get("meta") or {}).get("world")
        for kind, entry, text in _location_entries(doc):
            annotated += 1
            where = "%s ledger.%s %r" % (fname, kind, text)
            place_id, pinned = _pin_fields(entry, where, errors)
            want_id, want_pin = expected_pin(key, text)
            if pinned is not None and (place_id, bool(pinned)) != (want_id, want_pin):
                errors.append("%s 钉住结果与规则不符: 期望 %r 实得 %r"
                              % (where, (want_id, want_pin), (place_id, pinned)))
            if pinned and key not in WORLDS:
                errors.append("%s 世界 %s 没有地图却钉了" % (where, key))
            if pinned and place_id not in atlas_place_ids(key):
                errors.append("%s place_id %r 不在编译后的地图里" % (where, place_id))
            if pinned:
                pinned_count += 1
    assert annotated >= 20, "可钉条目数异常（%d）" % annotated
    assert pinned_count >= 1, "规则一条都没钉中——匹配器可能坏了"
    assert not errors, "\n".join(errors)


def check_unmatched_keep_display_name():
    """未钉条目的显示名必须是原字符串（不被改写、不臆造坐标）。

    规则本身不改显示名；这里用「显示名仍能按规则得到未钉结论」来锁：
    若数据被人改了显示名去凑匹配，重算结果会变。
    """
    errors = []
    for fname in sorted(SCENARIOS):
        doc = SCENARIOS[fname]
        key = (doc.get("meta") or {}).get("world")
        for kind, entry, text in _location_entries(doc):
            if not entry.get("pinned"):
                want_id, want_pin = expected_pin(key, text)
                if want_pin:
                    errors.append("%s ledger.%s %r 现在能唯一命中 %r，"
                                  "却标记为未钉——应重新生成钉住字段"
                                  % (fname, kind, text, want_id))
    assert not errors, "\n".join(errors)


def check_xref_backup_forty_one_pins():
    """已知正例：backup_forty_one 的「档案层隔间」钉到档案层区域。

    用文本定位区域（不把 region id 之外的东西写死；region id 本身是
    稳定的数据键，写进断言是锁契约而非臆造坐标）。
    """
    doc = _load("data/scenarios/backup_forty_one.json")
    hits = [entry for _, entry, text in _location_entries(doc)
            if text == "档案层隔间"]
    assert len(hits) == 1, "应恰有一个「档案层隔间」条目"
    entry = hits[0]
    assert entry["pinned"] is True
    assert entry["place_id"] == "entropic-net/region-06"
    assert entry["location"] == "档案层隔间"  # 显示名保持原字符串


def check_worldless_scenario_all_unpinned():
    """meta.world 没有 data/worlds/ 模组的剧本必须全部未钉。"""
    errors = []
    for fname in sorted(SCENARIOS):
        doc = SCENARIOS[fname]
        key = (doc.get("meta") or {}).get("world")
        if key in WORLDS:
            continue
        for kind, entry, text in _location_entries(doc):
            if entry.get("pinned") or entry.get("place_id") is not None:
                errors.append("%s ledger.%s %r：世界 %s 无地图，不得钉"
                              % (fname, kind, text, key))
    assert not errors, "\n".join(errors)


def check_no_coordinates_fabricated():
    """钉住字段只有 place_id/pinned 两个键，不得出现坐标类新键。"""
    banned = {"x", "y", "z", "coords", "coordinates", "footprint", "frame_id"}
    errors = []
    for fname in sorted(SCENARIOS):
        doc = SCENARIOS[fname]
        for kind, entry, _text in _location_entries(doc):
            extra = banned & set(entry)
            if extra:
                errors.append("%s ledger.%s 出现坐标类键 %s"
                              % (fname, kind, sorted(extra)))
    assert not errors, "\n".join(errors)


CHECKS = [
    check_shape_and_rule,
    check_unmatched_keep_display_name,
    check_xref_backup_forty_one_pins,
    check_worldless_scenario_all_unpinned,
    check_no_coordinates_fabricated,
]


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
