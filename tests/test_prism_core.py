#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prism_core M2a 数值落地回归测试（Issue #53）。

覆盖：白名单拒绝、助势骰层数与取消、五档分档边界、灾难后果表、
资源夹取与专注过用、伤害标签修正、状态叠加与【疲惫】、张力曲线触发、
属性修正表与熟练量表、判定记法构造、perform_action 集成。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_prism_core.py` 运行。
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import prism_core as pc


class SeqRandom:
    """确定性随机源：按预定序列返回 randint 结果，越界截断到 [low, high]。"""

    def __init__(self, *values):
        self.values = [int(v) for v in values]
        self.calls = 0

    def randint(self, low, high):
        value = self.values[self.calls % len(self.values)]
        self.calls += 1
        return max(low, min(high, value))


def _unit(**kwargs):
    return pc.new_unit("u1", "测试角色", **kwargs)


# ── 一、掷骰器：白名单 ──────────────────────────────────────────────────

def check_roll_whitelist():
    rng = SeqRandom(5, 2, 3)
    result = pc.roll("1d20", rng=rng)
    assert result["natural"] == 5 and result["total"] == 5, result
    result = pc.roll("2d6+1", rng=rng)
    assert result["dice"] == [2, 3] and result["total"] == 6, result

    for bad in ("1d20+1d4", "2*6", "d", "1d20*2", "0d6", "1d0",
                "41d6", "1d1001", "1d6+2000", "", "  "):
        try:
            pc.roll(bad)
        except ValueError:
            continue
        raise AssertionError("白名单应拒绝记法：%r" % bad)


# ── 二、掷骰器：助势骰 ──────────────────────────────────────────────────

def check_roll_edge_dice():
    # 优势与劣势互相抵消：2 − 1 = 净 1 层 → 恰好掷 1 枚 d6。
    result = pc.roll("1d20", rng=SeqRandom(10, 4), advantage=2, disadvantage=1)
    assert result["edge"]["net"] == 1 and result["edge"]["dice"] == [4], result
    assert result["total"] == 14, result

    # 净 0 层：不掷助势骰。
    result = pc.roll("1d20", rng=SeqRandom(10), advantage=1, disadvantage=1)
    assert result["edge"]["net"] == 0
    assert result["edge"]["dice"] == [] and result["edge"]["sum"] == 0
    assert result["total"] == 10, result

    # 超出 3 层：3 枚 d6 + 每多一层 +2 固定加值（4 层 → 3d6 + 2）。
    result = pc.roll("1d20", rng=SeqRandom(5, 2, 3, 4), advantage=4)
    assert result["edge"]["dice"] == [2, 3, 4] and result["edge"]["flat"] == 2
    assert result["edge"]["sum"] == 11 and result["total"] == 16, result

    # 劣势：掷 d6 并减去。
    result = pc.roll("1d20", rng=SeqRandom(10, 3, 4), disadvantage=2)
    assert result["edge"]["net"] == -2 and result["edge"]["sum"] == -7
    assert result["total"] == 3, result

    # 记法白名单不受助势骰参数影响。
    try:
        pc.roll("1d20+1d4", advantage=1)
    except ValueError:
        pass
    else:
        raise AssertionError("助势骰不得绕过记法白名单")


# ── 三、五档结果 ────────────────────────────────────────────────────────

def check_judge_check_bands():
    cases = (
        (16, 10, "triumph"),    # R = +6
        (15, 10, "success"),    # R = +5
        (10, 10, "success"),    # R = 0（平手归属行动方）
        (9, 10, "narrow"),      # R = −1
        (8, 10, "narrow"),      # R = −2
        (7, 10, "failure"),     # R = −3
        (1, 10, "failure"),     # R = −9
        (0, 10, "catastrophe"), # R = −10
        (0, 11, "catastrophe"), # R = −11
    )
    for total, df, expected in cases:
        got = pc.judge_check(total, df)
        assert got == expected, "judge_check(%d, %d) = %s，期望 %s" % (
            total, df, got, expected)
    # 自然 1 → 灾难，即使数值上很高。
    assert pc.judge_check(20, 10, natural=1) == "catastrophe"
    assert pc.judge_check(20, 10, natural=20) == "triumph"
    assert pc.judge_check(0, 20, auto_pass=True) == "success"


# ── 四、属性修正表与熟练量表 ────────────────────────────────────────────

def check_attribute_and_proficiency_tables():
    expect = {1: -2, 2: -1, 3: -1, 4: 0, 5: 0,
              6: 1, 7: 1, 8: 2, 9: 2, 10: 3}
    for value, modifier in expect.items():
        assert pc.attribute_modifier(value) == modifier, value
    for bad in (0, 11, -1):
        try:
            pc.attribute_modifier(bad)
        except ValueError:
            continue
        raise AssertionError("属性值 %d 应越界报错" % bad)

    expect_prof = {1: 2, 4: 2, 5: 3, 8: 3, 9: 4, 12: 4}
    for level, bonus in expect_prof.items():
        assert pc.proficiency_bonus(level) == bonus, level
    for bad in (0, 13):
        try:
            pc.proficiency_bonus(bad)
        except ValueError:
            continue
        raise AssertionError("等级 %d 应越界报错" % bad)


# ── 五、资源池：上下限、见底与过度使用 ──────────────────────────────────

def check_resource_clamps():
    session = pc.RuleSession("s1")
    unit = _unit()

    # 负担上限 6（01 第十二节：负担从 0 到 6 累加）。
    out = pc.change_resource(session, unit, "strain", 10)
    assert out["left"] == pc.STRAIN_MAX == 6, out
    # 决意上限 5（01 第十二节：起始 3 点，上限 5 点）。
    out = pc.change_resource(session, unit, "resolve", 9)
    assert out["left"] == pc.RESOLVE_MAX == 5, out
    # 任何池不见负。
    out = pc.change_resource(session, unit, "strain", -20)
    assert out["left"] == 0 and out["out"], out
    # 调用方提供的上限仍生效（专注上限依赖职途，由外部给出）。
    unit["max_resources"] = {"focus": 7}
    assert pc.change_resource(session, unit, "focus", 9)["left"] == 7

    # 专注过度使用：余量 2 花费 5 → 见底、疲惫、负担 +1。
    unit2 = _unit()
    unit2["resources"]["focus"] = 2
    out = pc.change_resource(session, unit2, "focus", -5)
    assert out["left"] == 0 and out["overused"], out
    assert "疲惫" in unit2["conditions"], unit2["conditions"]
    assert unit2["resources"]["strain"] == 1, unit2["resources"]
    # 余量充足的花费不触发过用。
    unit3 = _unit()
    unit3["resources"]["focus"] = 3
    out = pc.change_resource(session, unit3, "focus", -3)
    assert not out["overused"] and out["left"] == 0, out
    assert "疲惫" not in unit3["conditions"]

    try:
        pc.change_resource(session, unit, "mana", 1)
    except ValueError:
        pass
    else:
        raise AssertionError("未知资源池应报错")


# ── 六、伤害标签 ────────────────────────────────────────────────────────

def check_damage_tags():
    session = pc.RuleSession("s2")

    # 抗性：减半向下取整（01 第十一节）。
    unit = _unit()
    unit["max_vitality"] = 10
    unit["vitality"] = 10
    unit["damage_relations"] = {"resistance": ["火焰"]}
    out = pc.deal_damage(session, unit, 5, tag="火焰")
    assert out["relation"] == "resistance" and out["lost"] == 2, out
    # 非声明标签不受影响。
    out = pc.deal_damage(session, unit, 4, tag="电击")
    assert out["relation"] == "" and out["lost"] == 4 and out["modified"] is False

    # 弱点：+50%（向下取整）且额外 1 层【失衡】。
    unit = _unit()
    unit["max_vitality"] = 20
    unit["vitality"] = 20
    unit["damage_relations"] = {"weakness": ["火焰"]}
    out = pc.deal_damage(session, unit, 5, tag="火焰")
    assert out["relation"] == "weakness" and out["lost"] == 7, out
    assert "失衡" in unit["conditions"], unit["conditions"]

    # 免疫：伤害无效。
    unit = _unit()
    unit["max_vitality"] = 10
    unit["vitality"] = 10
    unit["damage_relations"] = {"immunity": ["精神"]}
    out = pc.deal_damage(session, unit, 10, tag="精神")
    assert out["relation"] == "immunity" and out["lost"] == 0, out

    # 吸收：反而恢复等量活力。
    unit = _unit()
    unit["max_vitality"] = 10
    unit["vitality"] = 4
    unit["damage_relations"] = {"absorb": ["力场"]}
    out = pc.deal_damage(session, unit, 6, tag="力场")
    assert out["relation"] == "absorb" and out["healed"] == 6, out
    assert unit["vitality"] == 10 and not unit["downed"]

    # 濒危受击失败计数：普通 +1，暴击 +2（01 第十一节 damage_escalation）。
    unit = _unit()
    unit["max_vitality"] = 10
    unit["vitality"] = 3
    out = pc.deal_damage(session, unit, 5)
    assert unit["downed"] and unit.get("downed_fails", 0) == 0, (out, unit)
    pc.deal_damage(session, unit, 1)
    assert unit["downed_fails"] == 1
    pc.deal_damage(session, unit, 1, crit=True)
    assert unit["downed_fails"] == 3
    # 吸收不计受击。
    unit["damage_relations"] = {"absorb": ["火焰"]}
    pc.deal_damage(session, unit, 1, tag="火焰")
    assert unit["downed_fails"] == 3


# ── 七、状态叠加 ────────────────────────────────────────────────────────

def check_conditions():
    session = pc.RuleSession("s3")
    unit = _unit()

    assert pc.add_condition(session, unit, "失衡") is True
    assert pc.add_condition(session, unit, "失衡") is False  # 同状态不重复
    assert unit["conditions"] == ["失衡"]

    try:
        pc.add_condition(session, unit, "不存在的状态")
    except ValueError:
        pass
    else:
        raise AssertionError("白名单外的状态应报错")

    # 两个减益：不触发疲惫。
    unit2 = _unit()
    pc.add_condition(session, unit2, "失衡")
    pc.add_condition(session, unit2, "中毒")
    assert "疲惫" not in unit2["conditions"]
    # 第三个减益：自动获得【疲惫】（01 第五节叠加规则 3）。
    pc.add_condition(session, unit2, "恐惧")
    assert "疲惫" in unit2["conditions"], unit2["conditions"]
    # 疲惫本身不再重复。
    assert unit2["conditions"].count("疲惫") == 1
    # 增益不计数。
    unit3 = _unit()
    for name in ("加速", "护持", "隐匿"):
        pc.add_condition(session, unit3, name)
    assert "疲惫" not in unit3["conditions"]
    # 世界模组登记扩展状态。
    try:
        pc.add_condition(session, unit3, "共鸣灼伤")
    except ValueError:
        pass
    else:
        raise AssertionError("未登记的扩展状态应报错")
    pc.register_condition("共鸣灼伤", debuff=True)
    assert pc.add_condition(session, unit3, "共鸣灼伤") is True


# ── 八、失败代价与灾难后果表 ────────────────────────────────────────────

def check_failure_cost():
    # 源文档未写按 DF 折算的常规代价曲线：全零，不臆造。
    cost = pc.failure_cost(12)
    assert cost["focus"] == 0 and cost["vitality"] == 0
    assert cost["strain"] == 0 and cost["conditions"] == []
    assert "catastrophe_row" not in cost

    # 灾难后果表第 3 行：1d6 活力（非信息型）。
    cost = pc.failure_cost(12, catastrophe=True, rng=SeqRandom(3, 4))
    assert cost["catastrophe_row"] == 3
    assert cost["vitality"] == 4 and cost["strain"] == 0, cost
    # 信息型行动不用活力买情报。
    cost = pc.failure_cost(12, info_only=True, catastrophe=True,
                           rng=SeqRandom(3, 4))
    assert cost["vitality"] == 0, cost
    # 第 10 行：负担 +1。
    cost = pc.failure_cost(12, catastrophe=True, rng=SeqRandom(10))
    assert cost["strain"] == 1 and cost["vitality"] == 0, cost
    # 叙事行：只回文本。
    cost = pc.failure_cost(12, catastrophe=True, rng=SeqRandom(2))
    assert cost["vitality"] == 0 and cost["strain"] == 0
    assert cost["catastrophe_text"] == pc.CATASTROPHE_TABLE[1]
    assert len(pc.CATASTROPHE_TABLE) == 20


def check_apply_failure_settlement():
    session = pc.RuleSession("s4")
    unit = _unit()
    unit["max_vitality"] = 20
    unit["vitality"] = 20

    # 常规失败：无数值曲线 → 状态不变。
    out = pc.apply_failure(session, unit, 12)
    assert unit["vitality"] == 20 and out["damage"] is None
    # 灾难第 10 行：负担 +1 走 change_resource。
    out = pc.apply_failure(session, unit, 12, catastrophe=True,
                           rng=SeqRandom(10))
    assert out["cost"]["catastrophe_row"] == 10
    assert unit["resources"]["strain"] == 1, unit["resources"]
    # 灾难第 3 行：活力伤害走 deal_damage。
    out = pc.apply_failure(session, unit, 12, catastrophe=True,
                           rng=SeqRandom(3, 6))
    assert out["damage"]["lost"] == 6, out
    assert unit["vitality"] == 14


# ── 九、压力计与张力曲线 ────────────────────────────────────────────────

def check_pressure_tension():
    assert pc.tension_band(0)["id"] == "relaxed"
    assert pc.tension_band(4)["id"] == "mild"
    assert pc.tension_band(5)["name"] == "紧绷"
    assert pc.tension_band(9)["id"] == "extreme"
    assert pc.tension_band(99)["id"] == "extreme"  # 夹取到 10

    session = pc.RuleSession("s5")
    # 0 → 3：升入「温和」，触发一次。
    out = pc.tick_pressure(session, 3)
    assert out["fired"] and out["event"]["band"] == "mild", out
    # 3 → 4：同档不触发。
    out = pc.tick_pressure(session, 1)
    assert not out["fired"], out
    # 一次跨多档只算一次（幂等）。
    out = pc.tick_pressure(session, 4)   # 4 → 8，跨两档
    assert out["fired"] and out["event"]["band"] == "high", out
    # 8 → 10：升入「顶点」，触发一次。
    out = pc.tick_pressure(session, 10)
    assert out["pressure"] == pc.PRESSURE_MAX == 10
    assert out["fired"] and out["event"]["band"] == "extreme", out
    # 已在顶点：不再变化、不再触发。
    out = pc.tick_pressure(session, 5)
    assert out["pressure"] == 10 and not out["fired"], out

    # 风险池判定（03 第一节）：危险区 d4 掷 4 触发；安全区 d8 掷 7 不触发。
    assert pc.risk_roll("dangerous", rng=SeqRandom(4))["triggered"] is True
    assert pc.risk_roll("safe", rng=SeqRandom(7))["triggered"] is False
    assert pc.risk_roll("standard", rng=SeqRandom(6))["triggered"] is True
    try:
        pc.risk_roll("void")
    except ValueError:
        pass
    else:
        raise AssertionError("未知区域应报错")


# ── 十、判定记法构造 ────────────────────────────────────────────────────

def check_check_notation():
    # 显式记法优先。
    assert pc._check_notation({"notation": "2d6"}, None) == "2d6"
    # 无修正 → 默认 1d20。
    assert pc._check_notation({}, _unit()) == "1d20"
    # 负担 6 → −3；疲惫 → 再 −1。
    unit = _unit()
    unit["resources"]["strain"] = 6
    unit["conditions"] = ["疲惫"]
    assert pc._check_notation({}, unit) == "1d20-4"
    # 属性 8 → +2；1 级熟练 +2 → 合计 +4；专精再 +1。
    unit = _unit(attributes={"MGT": 8})
    action = {"attribute": "MGT", "proficient": True}
    assert pc._check_notation(action, unit) == "1d20+4"
    action["specialty"] = True
    assert pc._check_notation(action, unit) == "1d20+5"
    # 未知属性报错。
    try:
        pc._check_notation({"attribute": "XYZ"}, unit)
    except ValueError:
        pass
    else:
        raise AssertionError("未知属性应报错")
    # 状态劣势层数：失衡 / 中毒 / 恐惧 各 −1，上限 −3。
    unit = _unit()
    unit["conditions"] = ["失衡", "中毒", "恐惧", "流血"]
    assert pc._condition_disadvantage(unit) == 3
    unit["conditions"] = ["加速", "护持"]
    assert pc._condition_disadvantage(unit) == 0


# ── 十一、perform_action 集成 ───────────────────────────────────────────

def _scene_session(*action_extra):
    session = pc.RuleSession("s6")
    action = {"id": "a1", "kind": "check", "label": "撬锁", "df": 12,
              "max_fail": 2}
    for extra in action_extra:
        action.update(extra)
    session.scene = {"id": "hall", "actions": [action]}
    session.party = [_unit()]
    return session, action


def check_perform_action_integration():
    # 险成：R = −1，达成（passed），不结算失败代价。
    session, _ = _scene_session()
    out = pc.perform_action(session, "a1", rng=SeqRandom(11))
    assert out["outcome"] == "narrow" and out["passed"] is True, out
    assert session.action_fails.get("a1", 0) == 0

    # 挫败：R = −4，失败代价计入次数并推压力（同档内不触发事件）。
    session, _ = _scene_session()
    out = pc.perform_action(session, "a1", rng=SeqRandom(8))
    assert out["outcome"] == "failure" and out["passed"] is False, out
    assert session.action_fails["a1"] == 1
    assert session.pressure == 1
    # 灾难：自然 1；后果表掷第 5 行（叙事行，无数值代价）。
    session, _ = _scene_session()
    out = pc.perform_action(session, "a1", rng=SeqRandom(1, 5))
    assert out["outcome"] == "catastrophe", out
    assert out["cost"]["cost"]["catastrophe_row"] == 5, out["cost"]
    # 机会耗尽：不再掷骰，直接结算代价并推压力。
    session, _ = _scene_session()
    session.action_fails["a1"] = 2
    out = pc.perform_action(session, "a1", rng=SeqRandom(10))
    assert out["status"] == "exhausted" and out["rolled"] is False, out
    assert session.pressure == 1
    # 免骰行动按达成处理。
    session, _ = _scene_session({"auto_pass": True})
    out = pc.perform_action(session, "a1")
    assert out["outcome"] == "success" and out["rolled"] is False, out
    # 状态劣势折入掷骰：失衡 −1 层 → d20=9 − 1d6=2 → 7 < DF 12 → 挫败。
    session, _ = _scene_session()
    session.party[0]["conditions"] = ["失衡"]
    out = pc.perform_action(session, "a1", rng=SeqRandom(9, 2))
    assert out["roll"]["edge"]["net"] == -1, out["roll"]
    assert out["outcome"] == "failure", out


def check_snapshot_serializable():
    """新字段（edge / damage_relations / downed_fails）不得破坏落盘契约。"""
    session = pc.RuleSession("s7")
    unit = _unit()
    unit["max_vitality"] = 10
    session.party = [unit]
    pc.deal_damage(session, unit, 3, tag="火焰")
    pc.change_resource(session, unit, "focus", -1)
    snapshot = session.snapshot()
    restored = pc.RuleSession.from_snapshot(json.loads(json.dumps(snapshot)))
    assert restored.party[0]["downed"] is True
    assert restored.party[0]["resources"]["focus"] == 0


def main():
    checks = (
        check_roll_whitelist,
        check_roll_edge_dice,
        check_judge_check_bands,
        check_attribute_and_proficiency_tables,
        check_resource_clamps,
        check_damage_tags,
        check_conditions,
        check_failure_cost,
        check_apply_failure_settlement,
        check_pressure_tension,
        check_check_notation,
        check_perform_action_integration,
        check_snapshot_serializable,
    )
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            print("  断言失败 [%s]: %s" % (check.__name__, error))
            failures += 1
        except Exception as error:  # 意外异常同样计为失败
            print("  异常 [%s]: %r" % (check.__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败"
              % (len(checks) - failures, len(checks), failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
