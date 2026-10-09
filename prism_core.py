#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prism_core.py —— 「棱镜 PRISM」规则核心的组织骨架（M2 前置）

本模块只提供**组织**：函数切分、控制流、数据流与工程范式。
一切数值、公式与判定语义按 `docs/system/` 在 M2 逐项落地，
文件内以 `TODO(M2)` 标记并注明对应章节；`TODO(M3)` 指向落盘 / 并发 / HTTP 接线。

设计约定
  · 零第三方依赖（仅 Python 3 标准库）；`import prism_core` **无副作用**。
  · 所有状态都是纯数据（dict / list / 基本类型），可直接 JSON 序列化：
    跨请求的回合状态必须能落盘，不能只放在内存里（见 RuleSession）。
  · 规则函数只返回结构化结果；叙事文案归调用方（HTTP 层 / 导引者模块）。
  · 命名已全部 PRISM 化：六维属性 = 力道 MGT / 灵巧 FIN / 体魄 VIG /
    洞察 INS / 心智 MND / 气场 PRE。

章节地图（骨架 ↔ 本模块小节）
  一、术语与显式白名单      二、掷骰器
  三、会话状态契约          四、结算的单一进出口
  五、场景行动单入口        六、战斗状态机
  七、派生值与明细          八、成长
"""

from __future__ import annotations

import copy
import random
import re

# ════════════════════════════════════════════════════════════════════════
# 【权限边界】玩家意图 vs 服务端结算
#
# 这是规则核心的边界契约。新增任何入口之前先过这一关。
#
# ── 玩家侧（可以有 HTTP 入口）只表达「我想做什么」────────────────────
#   · 选择场景行动 / 选择战斗动作 / 指定目标
#   · 输入自由文本（由导引者模块裁定后，仍回到本模块结算）
#   · 在成长时消费**服务端已经发放**的点数（分配，不是发放）
#   玩家侧永不提交结果：骰值、伤害、命中、资源增减、等级与经验的数值
#   一律不接受——传进来也只会被忽略或直接报错。
#
# ── 结算侧（只有内部函数，没有玩家入口）─────────────────────────────
#   · 掷骰与成败判定            perform_action / resolve_check
#   · 伤害与濒危                deal_damage（**唯一伤害出口**）
#   · 活力恢复                  heal_unit（**唯一恢复出口**）
#   · 资源增减                  change_resource（唯一资源出口）
#   · 失败代价                  apply_failure（场景行动与自由输入共用）
#   · 战斗推进与胜负            _advance / _end_combat / combat_abandon
#   · 经验与成长点数发放        award_xp（发放）；assign_attribute（消费）
#   · 战斗内临时增益清理        _clear_combat_buffs（所有战斗出口都走）
#
#   绕过这些函数、直接写 `unit["vitality"]` / `unit["resources"]` 之类的
#   改动都是**旁路**：新增路径若绕开单一进出口，应视为缺陷而不是风格问题。
#
# ── 显式清单 ─────────────────────────────────────────────────────────
#   所有外部输入先过白名单：六维属性、资源池、行动类别、战斗动作、
#   装备槽位。清单之外的输入一律 raise，不做「尽力猜测」。
# ════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════
# 一、术语与显式白名单
# ════════════════════════════════════════════════════════════════════════

# 六维属性：产品层统一使用 PRISM 名（docs/system/01 第一节）。
ATTRIBUTES = {
    "MGT": {"zh": "力道", "en": "Might"},
    "FIN": {"zh": "灵巧", "en": "Finesse"},
    "VIG": {"zh": "体魄", "en": "Vigor"},
    "INS": {"zh": "洞察", "en": "Insight"},
    "MND": {"zh": "心智", "en": "Mind"},
    "PRE": {"zh": "气场", "en": "Presence"},
}
ATTRIBUTE_IDS = tuple(ATTRIBUTES)
ATTRIBUTE_MIN = 1
ATTRIBUTE_MAX = 10

# 防护基准：`防护 = 10 + 护甲 Guard 值 + 态势修正 + 掩体修正`（docs/system/01 第二节）。
GUARD_BASE = 10

# 四大资源池（docs/system/01 第十二节）。
RESOURCE_POOLS = ("focus", "tempo", "strain", "resolve")

# 场景行动的两类：纯叙述 / 需要判定（行动数据用 kind 字段声明）。
ACTION_KINDS = ("narrate", "check")

# 战斗动作白名单：玩家在战斗里能选的动作只有这些（docs/system/01 第六节、
# data/system/action_economy.json 的动作表）。别在这里加「指定伤害」——
# 那是裁定权，不是玩家动作。
COMBAT_ACTIONS = (
    "move", "stance_switch", "assist_action", "basic_attack",
    "full_ability", "quick_ability", "suppress", "recon",
    "assist", "reaction",
)

# 装备槽位（角色卡模板：主武器 / 副手 / 护甲 / 随身）。
EQUIP_SLOTS = (
    ("main", "主武器"),
    ("off", "副手"),
    ("armor", "护甲"),
    ("carry", "随身"),
)

# 判定结果档位（docs/system/02A 第三节 / 02B 第二节 的五档命名）。
OUTCOMES = ("triumph", "success", "narrow", "failure", "catastrophe")
_OUTCOME_ZH = {
    "triumph": "凯旋",
    "success": "成功",
    "narrow": "险成",
    "failure": "挫败",
    "catastrophe": "灾难",
}
# 未达成档位：这些结果需要结算失败代价。
_FAILED_OUTCOMES = ("failure", "catastrophe")

# ════════════════════════════════════════════════════════════════════════
# 二、掷骰器
# ════════════════════════════════════════════════════════════════════════

# 工程护栏：规模上限只防「超大输入把服务打挂」，不是游戏数值。
DICE_MAX_COUNT = 40
DICE_MAX_SIDES = 1000

# 判定记法的占位默认值；真正的记法由行动数据或 M2 的判定层给出。
DEFAULT_CHECK_NOTATION = "1d20"

# 白名单：`NdM` 或 `NdM±K`。拒绝一切自由记法（例如嵌套表达式、多次掷骰）。
_NOTATION_RE = re.compile(r"^(\d{0,3})d(\d{1,3})([+-]\d{1,3})?$", re.IGNORECASE)


def roll(notation: str, *, rng=None) -> dict:
    """按白名单记法掷骰，返回结构化结果；不合法时抛 ValueError。

    返回 {notation, count, sides, dice, modifier, total, detail, natural}：
      · dice     —— 每颗骰的原始面值（按掷出顺序）
      · total    —— 面值之和 + 修正
      · natural  —— 单颗骰时的原始面值（多颗为 None），供判定层使用
      · detail   —— 人类可读明细（叙事流 / 界面用）

    TODO(M2)：记法的 PRISM 扩展按 docs/system/02A 第二节（助势骰）与
    docs/system/02B 第一节（骰池）落地；面值语义（自然最高值、自然 1 等）
    由判定层按 docs/system/02A 第五、六节使用，本层只提供原始面值。
    `rng` 只为测试注入确定性随机（默认 Python 标准库的 random）。
    """
    text = (notation or "").strip().replace(" ", "")
    matched = _NOTATION_RE.match(text)
    if not matched:
        raise ValueError(f"不支持的骰点记法：{notation}")
    count = int(matched.group(1) or 1)
    sides = int(matched.group(2))
    if count < 1 or sides < 1:
        raise ValueError("骰子数量与面值必须为正")
    if count > DICE_MAX_COUNT or sides > DICE_MAX_SIDES:
        raise ValueError("骰子规模过大")
    modifier = int(matched.group(3)) if matched.group(3) else 0

    source = rng or random
    values = [int(source.randint(1, sides)) for _ in range(count)]
    label = f"{count} 颗 d{sides}" if count > 1 else f"1 颗 d{sides}"
    detail = f"{label}：{values} → {sum(values)}"
    if modifier:
        detail += f"，修正 {modifier:+d}"
    return {
        "notation": text,
        "count": count,
        "sides": sides,
        "dice": values,
        "modifier": modifier,
        "total": sum(values) + modifier,
        "detail": detail,
        "natural": values[0] if count == 1 else None,
    }


# ════════════════════════════════════════════════════════════════════════
# 三、会话状态契约
# ════════════════════════════════════════════════════════════════════════


def new_unit(unit_id: str, name: str, *, attributes: dict | None = None) -> dict:
    """创建一个单位（角色 / 敌体）的最小数据卡。

    只放规则核心用得到的字段；职途、技能、能力、装备目录等数据由 M2
    接入（docs/system/02A/02B、data/system/*.json）。属性默认 4 =
    普通成年人水平（docs/system/01 第一节）。
    """
    attrs = {key: 4 for key in ATTRIBUTE_IDS}
    for key, value in (attributes or {}).items():
        if key not in ATTRIBUTES:
            raise ValueError(f"未知属性：{key}")
        value = int(value)
        if not (ATTRIBUTE_MIN <= value <= ATTRIBUTE_MAX):
            raise ValueError(
                f"属性 {key} 必须在 {ATTRIBUTE_MIN}–{ATTRIBUTE_MAX} 之间")
        attrs[key] = value
    return {
        "id": str(unit_id),
        "name": str(name),
        "level": 1,
        "xp": 0,
        "growth_points": 0,
        "attributes": attrs,
        "vitality": 0,
        "max_vitality": 0,
        "guard": 0,
        "downed": False,          # 濒危标记（docs/system/01 第十一节）
        "dead": False,
        "conditions": [],
        "resources": {pool: 0 for pool in RESOURCE_POOLS},
        "equipment": {},
        "stance": "",
        "cover": "",
    }


class RuleSession:
    """规则核心的最小会话状态切片。

    字段刻意保持 JSON 可序列化：战斗状态、行动去重表与失败计数都必须
    能随存档落盘——跨请求的回合状态不能只放内存，刷新页面就丢。

    TODO(M3)：完整会话 / 存档层（M3 的 HTTP 与存档实现）接管落盘（原子写、
    加载迁移）与并发锁；本类的字段名应被复用或无损扩展，不要在别处
    另起一套。会话标识沿用 `sid`，与存档层的键保持一致。
    """

    def __init__(self, session_id: str = ""):
        self.sid = str(session_id)
        self.party: list[dict] = []
        self.scene: dict = {"id": "", "actions": []}
        self.active_unit_id = ""
        self.log: list[dict] = []
        self.done_actions: dict = {}     # 已完成的场景行动（成功一次即锁）
        self.action_fails: dict = {}     # {行动 id: 累计失败次数}
        self.pressure = 0                # 场景压力计（见 tick_pressure）
        self.combat: dict | None = None
        self.dirty = False               # M3 存档层据此判断是否需要写盘

    def add_log(self, kind: str, text: str, *, speaker: str = "",
                extra: dict | None = None) -> dict:
        entry = {"kind": str(kind), "text": str(text), "speaker": str(speaker)}
        if extra:
            entry["extra"] = dict(extra)
        self.log.append(entry)
        self.dirty = True
        return entry

    def touch(self) -> None:
        """标记状态已变化（M3 存档层读它决定是否写盘）。"""
        self.dirty = True

    def snapshot(self) -> dict:
        """返回 JSON 可序列化的整会话快照（深拷贝，改快照不影响现场）。

        TODO(M3)：真正的落盘（写 .tmp 再原子替换、加载时的惰性迁移）
        由存档层实现；本函数只保证状态本身是纯数据。
        """
        return {
            "sid": self.sid,
            "party": copy.deepcopy(self.party),
            "scene": copy.deepcopy(self.scene),
            "active_unit_id": self.active_unit_id,
            "log": copy.deepcopy(self.log),
            "done_actions": copy.deepcopy(self.done_actions),
            "action_fails": copy.deepcopy(self.action_fails),
            "pressure": self.pressure,
            "combat": copy.deepcopy(self.combat),
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> "RuleSession":
        """从 snapshot() 的结构恢复会话（M3 加载路径的骨架）。"""
        data = data or {}
        session = cls(str(data.get("sid") or ""))
        session.party = list(data.get("party") or [])
        session.scene = dict(data.get("scene") or {"id": "", "actions": []})
        session.active_unit_id = str(data.get("active_unit_id") or "")
        session.log = list(data.get("log") or [])
        session.done_actions = dict(data.get("done_actions") or {})
        session.action_fails = dict(data.get("action_fails") or {})
        session.pressure = int(data.get("pressure") or 0)
        session.combat = data.get("combat")
        return session


def _unit_view(unit: dict | None) -> dict:
    """单位的只读视图：白名单字段 + 深拷贝，不把内部引用交给调用方。"""
    if not unit:
        return {}
    fields = ("id", "name", "level", "vitality", "max_vitality", "guard",
              "downed", "dead", "conditions", "resources", "attributes")
    return {key: copy.deepcopy(unit.get(key)) for key in fields}


# ════════════════════════════════════════════════════════════════════════
# 四、结算的单一进出口
# ════════════════════════════════════════════════════════════════════════


def deal_damage(session: RuleSession, unit: dict | None, amount: int,
                *, label: str = "", tag: str = "") -> dict:
    """**唯一的伤害出口**：标签修正 → 扣活力 → 濒危判定。

    检定失败同样能把人打到濒危，所以失败代价也走这里（而不是直接改
    vitality）——绕过它就会出现「活力归零但濒危标记没挂上」的状态。

    返回 {vitality, lost, downed, dead, raw, modified, label}。
    叙事文案归调用方；濒危的挣扎 / 稳定规则留待 M2（见下方 TODO）。
    """
    out = {
        "vitality": int((unit or {}).get("vitality") or 0),
        "lost": 0,
        "downed": bool((unit or {}).get("downed")),
        "dead": bool((unit or {}).get("dead")),
        "raw": max(0, int(amount or 0)),
        "modified": False,
        "label": str(label or ""),
    }
    if unit is None:
        return out

    effective, modified = _apply_damage_tags(unit, amount, tag)
    out["modified"] = bool(modified)
    before = int(unit.get("vitality") or 0)
    unit["vitality"] = max(0, before - max(0, int(effective or 0)))
    out["vitality"] = int(unit["vitality"])
    out["lost"] = before - int(unit["vitality"])

    # 刚跌到 0 及以下：进入濒危。已经不是濒危的再次受击不改标记——
    # 失败计数的规则留待 M2。
    if int(unit["vitality"]) <= 0 and not out["downed"]:
        unit["downed"] = True
    out["downed"] = bool(unit.get("downed"))
    out["dead"] = bool(unit.get("dead"))
    session.touch()
    return out


def _apply_damage_tags(unit: dict, amount: int, tag: str) -> tuple[int, bool]:
    """伤害标签的修正入口（抗性 / 弱点 / 免疫等）。

    TODO(M2)：按 docs/system/01 第十一节与 data/system/damage.json 落地；
    在语义落地前原值返回，不做任何数值猜测。
    """
    return max(0, int(amount or 0)), False


def heal_unit(session: RuleSession, unit: dict | None, amount: int) -> int:
    """**唯一的活力恢复出口**，返回实际恢复量。

    治疗路径（药剂、休息、医疗、能力）都必须走这里，漏一条就会出现
    「活力回满了但濒危标记还挂着」这类不一致。

    TODO(M2)：濒危的解除条件（挣扎 / 医疗 / 恢复至 0 以上是否立即脱离）
    按 docs/system/01 第十一节落地；在语义落地前本函数只做数值夹取，
    不改动濒危标记。
    """
    amount = max(0, int(amount or 0))
    if unit is None or not amount:
        return 0
    before = int(unit.get("vitality") or 0)
    cap = int(unit.get("max_vitality") or 0)
    healed = before + amount
    if cap > 0:
        healed = min(cap, healed)
    unit["vitality"] = max(0, healed)
    got = int(unit["vitality"]) - before
    session.touch()
    return got


def change_resource(session: RuleSession, unit: dict | None, pool: str,
                    delta: int) -> dict:
    """**唯一资源进出口**：正数获得、负数消耗。

    返回 {pool, changed, left, out}；`out` 表示该池已见底。

    TODO(M2)：各池的上下限、透支与替换规则按 docs/system/01 第十二节
    落地（例如专注见底后仍可过度使用、负担的档位效应）。当前只做
    0 下限与已知上限的夹取，不臆造规则。
    """
    pool = str(pool or "")
    if pool not in RESOURCE_POOLS:
        raise ValueError(f"未知资源池：{pool}")
    delta = int(delta or 0)
    out = {"pool": pool, "changed": 0, "left": 0, "out": False}
    if unit is None or delta == 0:
        if unit is not None:
            out["left"] = int((unit.get("resources") or {}).get(pool, 0) or 0)
            out["out"] = out["left"] <= 0
        return out

    resources = unit.setdefault("resources", {})
    current = int(resources.get(pool, 0) or 0)
    new = current + delta
    caps = unit.get("max_resources") or {}
    cap = caps.get(pool)
    if cap is not None:
        new = min(new, int(cap))
    new = max(0, new)
    resources[pool] = new
    out.update(changed=new - current, left=new, out=(new <= 0))
    session.touch()
    return out


def add_condition(session: RuleSession, unit: dict | None, name: str) -> bool:
    """给单位施加一个状态，返回是否新加。

    TODO(M2)：状态的层数、持续与叠加规则按 docs/system/01 第五节落地；
    当前只做「同状态不重复添加」的最小登记。
    """
    name = str(name or "").strip()
    if not name:
        raise ValueError("状态名不能为空")
    if unit is None:
        return False
    conditions = unit.setdefault("conditions", [])
    if name in conditions:
        return False
    conditions.append(name)
    session.touch()
    return True


def failure_cost(df: int, *, info_only: bool = False) -> dict:
    """一次失败按难度值折算出的代价（结构化，不做结算）。

    TODO(M2)：数值全部按 docs/system/01 第五节 / 第十二节与
    docs/system/03 落地（资源、活力、负担、状态的组合曲线）；
    `info_only` 表示信息型行动——不该用活力去买情报。
    当前返回全零占位，不臆造任何数值。
    """
    return {"focus": 0, "vitality": 0, "strain": 0, "conditions": []}


def apply_failure(session: RuleSession, unit: dict | None, df: int,
                  label: str = "", *, info_only: bool = False) -> dict:
    """结算一次失败的代价。**场景行动与自由输入共用这一个出口。**

    两条路径必须付出完全相同的代价：如果自由输入只推压力不掉资源，
    它就成了一条零风险旁路——玩家可以无限重试同一个想法，把场景行动
    的代价体系整个绕过去。
    """
    cost = failure_cost(df, info_only=info_only)
    out = {"cost": dict(cost), "focus": None, "strain": None, "damage": None}
    if unit is None:
        return out
    focus_cost = int(cost.get("focus") or 0)
    if focus_cost:
        out["focus"] = change_resource(session, unit, "focus", -focus_cost)
    strain_cost = int(cost.get("strain") or 0)
    if strain_cost:
        out["strain"] = change_resource(session, unit, "strain", strain_cost)
    for name in cost.get("conditions") or []:
        add_condition(session, unit, name)
    vitality_cost = int(cost.get("vitality") or 0)
    if vitality_cost:
        out["damage"] = deal_damage(session, unit, vitality_cost,
                                    label=label or "失败代价")
    return out


def tick_pressure(session: RuleSession, step: int = 1) -> dict:
    """推进场景压力计，返回 {fired, pressure, event}。

    失败要真的改变局势，就必须有一个所有路径共用的推进器：
    只在某一条路径上推，其他路径就白嫖。事件的触发判据与后果
    留待 M2（对齐 docs/system/03 第一节风险池 / docs/system/04 张力曲线）。

    TODO(M2)：触发判据 `_pressure_event_due` 与事件内容
    `_fire_pressure_event` 按上述章节落地；当前只累计数值、不触发。
    """
    old = int(session.pressure or 0)
    session.pressure = old + max(0, int(step or 0))
    new = session.pressure
    out = {"fired": False, "pressure": new, "event": None}
    if new == old:
        return out
    session.touch()
    if not _pressure_event_due(old, new):
        return out
    out["fired"] = True
    out["event"] = _fire_pressure_event(session)
    return out


def _pressure_event_due(old: int, new: int) -> bool:
    """压力事件触发判据（幂等：跨越多个阈值也只算一次）。

    TODO(M2)：按 docs/system/03 第一节 / docs/system/04 张力曲线落地；
    当前恒定不触发。
    """
    return False


def _fire_pressure_event(session: RuleSession) -> dict | None:
    """压力事件的内容与后果。

    TODO(M2)：事件表与机械后果按 docs/system/03 第一节、
    docs/system/04 落地；导引者文案由导引者模块提供，数值由规则层给。
    """
    return None


# ════════════════════════════════════════════════════════════════════════
# 五、场景行动单入口
# ════════════════════════════════════════════════════════════════════════


def perform_action(session: RuleSession, action_id: str, unit_id: str = "",
                   *, reveal: bool = False) -> dict:
    """执行一个场景行动：**一次调用完成全部结算**。

    控制流：校验（含战斗锁）→ 已完成短路 → 机会耗尽结算 → 掷骰 →
    判定与代奖（resolve_check）→ 结构化返回。玩家只能传「做哪个行动」，
    掷骰、成败、代价、奖励全部在服务端算完，没有「听结果再改参数」的窗口。

    reveal 只控制叙事流里要不要写骰值明细（透传，不是玩家偏好存储）。
    """
    action = _find_action(session, action_id)
    if action is None:
        raise ValueError("行动不存在于当前场景")

    # 战斗进行中不放行普通场景行动：否则玩家能在战斗界面外把场景清空、
    # 把该做的先做完，回合制的约束整个失效。
    if session.combat and not session.combat.get("over"):
        raise ValueError("战斗还没结束，先打完这场")

    kind = str(action.get("kind") or "check")
    if kind not in ACTION_KINDS:
        raise ValueError(f"未知的行动类别：{kind}")

    if kind == "narrate":
        text = str(action.get("text") or "")
        session.add_log("narrative", text,
                        speaker=str(action.get("label") or "旁白"))
        return {"status": "resolved", "kind": kind, "text": text,
                "need_roll": False}

    unit = _find_action_unit(session, unit_id)
    label = str(action.get("label") or "")

    # 已经成功过的行动：不重复掷骰、不重复发奖；失败可以重试。
    done = session.done_actions.get(action_id)
    if done:
        return {
            "status": "already_done", "need_roll": False, "already": True,
            "action": {"id": action.get("id"), "label": label},
            "text": done.get("text", ""),
            "reward": done.get("reward", []),
            "done_by": done.get("unit", ""),
            "message": f"「{label}」已经完成过了（{done.get('unit', '')}）。",
        }

    # 机会耗尽：行动已被彻底关闭，不再允许重试。这里**必须真的结算代价**，
    # 不能只是返回——否则「耗尽」只是一个灰按钮，世界不会有任何反应。
    max_fail = int(action.get("max_fail") or 0)
    fails_done = int(session.action_fails.get(action_id, 0) or 0)
    if max_fail and fails_done >= max_fail:
        cost = apply_failure(session, unit, int(action.get("df") or 0), label,
                             info_only=not action.get("dangerous"))
        event = tick_pressure(session, 1)
        session.add_log(
            "system",
            f"「{label}」的机会已经用尽（连续失败 {max_fail} 次）；"
            f"放弃同样要付代价。", speaker="代价")
        return {
            "status": "exhausted", "need_roll": False, "exhausted": True,
            "action": {"id": action.get("id"), "label": label,
                       "max_fail": max_fail, "fail_count": fails_done},
            "cost": cost, "pressure_event": event, "rolled": False,
            "roll": None, "left_before": 0,
            "message": f"「{label}」的机会已经用尽。局势继续推进。",
        }

    fails = fails_done
    # 掷骰前的剩余机会：结算后的值会被成功 / 耗尽逻辑改写，
    # 单独返回一份给调用方做严谨断言与展示。
    left_before = max(0, max_fail - fails) if max_fail else None

    if action.get("auto_pass"):
        result = {"notation": "", "count": 0, "sides": 0, "dice": [],
                  "modifier": 0, "total": 0, "natural": None,
                  "detail": "该行动无需掷骰"}
        rolled = False
    else:
        result = roll(_check_notation(action, unit))
        rolled = True

    out = resolve_check(session, action, result, unit, reveal=reveal)
    out["left_before"] = left_before
    out["need_roll"] = False
    out["rolled"] = rolled
    out["roll"] = result if rolled else None
    out["action"] = {
        "id": action.get("id"), "label": label,
        "df": int(action.get("df") or 0), "max_fail": max_fail,
        "fail_count": fails, "left": left_before,
    }
    out["unit"] = _unit_view(unit)
    return out


def resolve_check(session: RuleSession, action: dict, result: dict,
                  unit: dict | None, *, reveal: bool = False) -> dict:
    """结算一次判定：成败 → 失败代价 / 成功奖励 → 记录。

    失败代价只在**配置了重试上限**的行动上结算（max_fail > 0）：
    这类行动是有压力的；没有上限的行动属于低风险试探，不计价。
    """
    action_id = str(action.get("id") or "")
    label = str(action.get("label") or "")
    df = int(action.get("df") or 0)
    total = int(result.get("total") or 0)
    auto = bool(action.get("auto_pass"))
    outcome = judge_check(total, df, auto_pass=auto)
    passed = outcome not in _FAILED_OUTCOMES

    max_fail = int(action.get("max_fail") or 0)
    fails = 0
    tier_text = ""
    exhausted = False
    cost: dict = {}
    if not passed and max_fail > 0:
        fails = int(session.action_fails.get(action_id, 0) or 0) + 1
        session.action_fails[action_id] = fails
        tiers = action.get("fail_tiers") or []
        if tiers:
            tier_text = str(tiers[min(fails, len(tiers)) - 1])
        else:
            tier_text = f"机会正在减少（{fails}/{max_fail}）。"
        if fails >= max_fail:
            exhausted = True
        cost = apply_failure(session, unit, df, label,
                             info_only=not action.get("dangerous"))
        cost["pressure_event"] = tick_pressure(session, 1)

    # 检定行：默认只写结论；reveal 时才附骰值明细。
    verdict_zh = _OUTCOME_ZH.get(outcome, outcome)
    if reveal and result.get("detail"):
        check_text = f"{label} · 判定 {result['detail']} = {total} vs DF {df} → {verdict_zh}"
    else:
        check_text = f"{label} · 判定 → {verdict_zh}"
    session.add_log("check", check_text, speaker=verdict_zh,
                    extra={"df": df, "total": total, "passed": passed,
                           "action": label, "detail": result.get("detail", ""),
                           "revealed": bool(reveal)})
    result_text = action.get("on_pass") if passed else action.get("on_fail")
    if result_text:
        session.add_log("result", str(result_text),
                        speaker=str((unit or {}).get("name") or label))
    if tier_text:
        session.add_log("system", tier_text, speaker="局势")

    granted: list = []
    if passed:
        session.done_actions[action_id] = {
            "unit": str((unit or {}).get("name") or ""),
            "text": str(result_text or ""),
            "reward": [],
        }
        granted = _grant_rewards(session, action, unit)
        session.done_actions[action_id]["reward"] = [
            item.get("name", item) if isinstance(item, dict) else item
            for item in granted
        ]

    return {"status": "resolved", "passed": passed, "outcome": outcome,
            "exhausted": exhausted, "fails": fails, "tier_text": tier_text,
            "cost": cost, "granted": granted, "auto": auto}


def judge_check(total: int, df: int, *, auto_pass: bool = False) -> str:
    """把判定结果归入档位（见 OUTCOMES）。

    TODO(M2)：按 docs/system/02A 第三节 / 02B 第二节 的五档结果梯度落地
    （按结果与 DF 的差值分档）。本骨架只做最小的「达成 / 未达成」占位，
    不采纳任何与分档阈值相关的具体数字。
    """
    if auto_pass:
        return "success"
    return "success" if int(total) >= int(df) else "failure"


def _check_notation(action: dict, unit: dict | None) -> str:
    """构造一次判定的掷骰记法。

    TODO(M2)：按 docs/system/02A 第一节（战术引擎：d20 + 属性修正 +
    熟练 + 助势骰）与 02B 第一节（构建引擎：骰池）落地；行动数据自带
    `notation` 时以数据为准（显式优先），否则用占位默认值。
    """
    explicit = str(action.get("notation") or "").strip()
    return explicit or DEFAULT_CHECK_NOTATION


def _grant_rewards(session: RuleSession, action: dict,
                   unit: dict | None) -> list:
    """成功后的奖励发放（物品 / 成长等）。

    TODO(M2)：奖励的唯一发放出口，按 docs/system/01 第十三 / 十四节、
    docs/system/03 第五节与 data/system/economy.json 落地。当前为空占位
    ——玩家没有奖励入口，奖励只能从这里进。
    """
    return []


def _find_action(session: RuleSession, action_id: str) -> dict | None:
    target = str(action_id or "")
    for action in (session.scene or {}).get("actions") or []:
        if str(action.get("id") or "") == target:
            return action
    return None


def _find_action_unit(session: RuleSession, unit_id: str) -> dict | None:
    """定位行动的执行者。显式传入的 id 必须存在；缺省时用当前行动角色。"""
    target = str(unit_id or "") or str(session.active_unit_id or "")
    if target:
        unit = next((p for p in session.party
                     if str(p.get("id") or "") == target), None)
        if unit is None:
            raise ValueError("单位不存在")
        return unit
    return session.party[0] if session.party else None


# ════════════════════════════════════════════════════════════════════════
# 六、战斗状态机
# ════════════════════════════════════════════════════════════════════════

# 仅本场战斗有效的临时增益字段。TODO(M2)：按 docs/system/01 第七 / 八节与
# 02A 场地要素列出（能力与场地赋予的临时修正）；所有战斗出口都会清理，
# 漏掉任何一条都会让上一场的修正常驻。
COMBAT_BUFF_FIELDS: tuple[str, ...] = ()


def start_combat(session: RuleSession, *, difficulty: str = "",
                 enemies: list[dict] | None = None) -> dict:
    """开始一场遭遇。**内容由服务端决定**，玩家只能选择「打不打」。

    敌体列表缺省时走遭遇生成（build_encounter）；生成器落地前，
    调用方（测试 / 剧本）必须显式传入敌体列表。

    开局后必须**立刻推进一次**：先攻最高的可能是敌体，如果只顾好顺序就
    返回，没人去跑它的回合，战斗会从第一秒就静止。_advance 会一路推进
    到第一个需要玩家操作的位置才返回。
    """
    if session.combat and not session.combat.get("over"):
        raise ValueError("战斗还没结束")
    squad = list(enemies) if enemies is not None else build_encounter(
        session, difficulty)
    if not squad:
        raise ValueError("这里没有可交战的东西")
    session.combat = {
        "scene_id": str((session.scene or {}).get("id") or ""),
        "round": 1,
        "difficulty": str(difficulty or ""),
        "enemies": squad,
        "order": [],
        "turn_i": 0,
        "acts": {},      # 本回合的去重表：{单位 key: 动作 / 标记}
        "events": [],    # 本回合结算出的事件（前端逐条播报）
        "over": "",      # "" / victory / defeat / fled
    }
    _reorder_initiative(session)
    _advance(session)
    session.add_log("system", f"战斗开始：{len(squad)} 个敌体。", speaker="战斗")
    session.touch()
    return combat_view(session)


def build_encounter(session: RuleSession, difficulty: str = "") -> list[dict]:
    """按遭遇预算生成敌体列表（缺省遭遇生成器）。

    TODO(M2)：预算与强度档按 docs/system/02A 第九、十节与
    data/system/tactics_encounter.json 落地；当前返回空列表——
    调用方必须显式传入敌体，数据驱动的入口保持不变。
    """
    return []


def _reorder_initiative(session: RuleSession) -> None:
    """重排行动顺序，并清零本回合的去重表。

    TODO(M2)：先攻分按 docs/system/02A 第六节、docs/system/01 第二节
    （洞察修正 + 灵巧修正的一半）落地；平手规则同节。当前所有单位
    同分，按单位 key 稳定排序（确定性优先，方便测试与复现）。
    """
    combat = session.combat
    if not combat:
        return
    units: list[dict] = []
    for unit in session.party:
        if _can_act_unit(unit):
            units.append(_order_entry("party", str(unit.get("id") or ""),
                                      str(unit.get("name") or ""), unit))
    for index, enemy in enumerate(combat.get("enemies") or []):
        if _can_act_unit(enemy):
            units.append(_order_entry("enemy", str(index),
                                      str(enemy.get("name") or ""), enemy))
    units.sort(key=lambda entry: (-int(entry.get("init") or 0), entry["key"]))
    combat["order"] = units
    combat["turn_i"] = 0


def _order_entry(side: str, unit_key: str, name: str, unit: dict) -> dict:
    return {"key": _unit_key(side, unit_key), "side": side, "id": unit_key,
            "name": name, "init": _initiative_score(unit)}


def _unit_key(side: str, unit_id: str) -> str:
    return f"{side}:{unit_id}"


def _initiative_score(unit: dict) -> int:
    """单位的一次先攻分。

    TODO(M2)：按 docs/system/02A 第六节 / docs/system/01 第二节落地；
    当前返回 0（占位），排序退化为按单位 key 的稳定顺序。
    """
    return 0


def _can_act_unit(unit: dict | None) -> bool:
    """单位本回合能否行动：消亡与濒危都不能。

    消亡与濒危都还可能「被救回」，所以它们是两个标记、也是一条
    独立判据——只查一个标记，就会出现「全员倒地但战斗不结束」或
    「倒地角色还能行动」这类状态矛盾。
    """
    return bool(unit) and not unit.get("dead") and not unit.get("downed")


def _order_unit(session: RuleSession, entry: dict) -> dict | None:
    """按行动顺序条目取回单位本体（party 直接查，enemy 按下标查）。"""
    if str(entry.get("side") or "") == "enemy":
        index = int(entry.get("id") or -1)
        enemies = (session.combat or {}).get("enemies") or []
        if 0 <= index < len(enemies):
            return enemies[index]
        return None
    return next((unit for unit in session.party
                 if str(unit.get("id") or "") == str(entry.get("id"))), None)


def _order_key_at(combat: dict, index: int) -> str:
    order = combat.get("order") or []
    if 0 <= index < len(order):
        return str(order[index].get("key") or "")
    return ""


def _unit_can_act(session: RuleSession, entry: dict) -> bool:
    return _can_act_unit(_order_unit(session, entry))


def _current_unit(session: RuleSession) -> dict | None:
    """当前该行动的单位；扫不到就返回 None。

    跳过不能只 continue：必须把 turn_i 一起推进，否则每次调用都从同一个
    下标重扫，_advance 的 i+1 会被这个陈旧值拉回去，战斗会卡在同一个
    已倒地的单位上。循环上限是兜底——上限内找不到就说明没有可行动单位。
    """
    combat = session.combat
    if not combat or combat.get("over"):
        return None
    order = combat.get("order") or []
    index = int(combat.get("turn_i", 0) or 0)
    for _ in range(len(order) * 2 + 4):
        if index >= len(order):
            break
        entry = order[index]
        if _unit_can_act(session, entry):
            if index != int(combat.get("turn_i", 0) or 0):
                combat["turn_i"] = index
            return entry
        index += 1
    combat["turn_i"] = index
    return None


def _alive_party(session: RuleSession) -> list[dict]:
    """还没消亡的角色（濒危的也算活着：可以被救起）。"""
    return [unit for unit in session.party if not unit.get("dead")]


def _conscious_party(session: RuleSession) -> list[dict]:
    """还站着的角色（没消亡也没濒危）。**胜负判定用这个**。"""
    return [unit for unit in session.party if _can_act_unit(unit)]


def _check_combat_end(session: RuleSession) -> bool:
    """胜负检查点：敌体全倒 → 胜利；全队不能行动 → 失败。

    这是一个**共用检查点**：任何单位行动完、以及恢复中的战斗每次推进前
    都先过这里。漏掉任何一条出口，战斗就会停在「胜负已分、状态却没标记」
    的死局里——界面还显示进行中，玩家却做什么都不对。
    """
    combat = session.combat
    if not combat or combat.get("over"):
        return True
    if all(enemy.get("dead") for enemy in combat.get("enemies") or []):
        _end_combat(session, "victory")
        return True
    if not _conscious_party(session):
        _end_combat(session, "defeat")
        return True
    return False


def _advance(session: RuleSession) -> None:
    """推进到**下一个需要玩家操作的位置**。

    敌体的回合在这里一次性跑完，直到轮到玩家（或战斗结束）才返回；
    把「敌体行动」摊成玩家按钮不是回合制，是卡顿。

    起始下标取决于当前单位**是否已经行动过**：已有动作记录 → 从下一个
    开始；还没有 → 就从当前开始。无条件 +1 会在「战斗刚开始、当前单位
    尚未行动」时把它整个跳过，先攻高的敌体将永远不动。
    """
    combat = session.combat
    if not combat or combat.get("over"):
        return
    if not (combat.get("order") or []):
        return
    if _check_combat_end(session):
        return

    current = int(combat.get("turn_i", 0) or 0)
    index = current if not combat["acts"].get(
        _order_key_at(combat, current)) else current + 1

    # 上限按「可能走过的单位数」算：每跨一回合所有单位都要重新过一次，
    # 给足 3 轮；上限内推不动就说明状态不一致（见函数尾部兜底）。
    guard = 0
    limit = len(combat["order"]) * 3 + 12
    while guard < limit:
        guard += 1
        if index >= len(combat["order"]):
            combat["round"] = int(combat.get("round", 1) or 1) + 1
            combat["acts"] = {}
            _reorder_initiative(session)
            # 重掷先攻是重新赋值 order，旧下标作废，必须回到 0。
            index = 0
        entry = combat["order"][index]
        if not _unit_can_act(session, entry):
            index += 1
            continue
        # 去重表：少了它，顺序回绕时同一单位会在同一回合里再动一次。
        if combat["acts"].get(entry["key"]):
            index += 1
            continue
        combat["turn_i"] = index
        if entry.get("side") == "enemy":
            _enemy_turn(session, entry)
            combat["acts"][entry["key"]] = "enemy"
            # 敌体行动后重判胜负：打死最后一个角色就该结束了。
            if _check_combat_end(session):
                return
            index += 1
            continue
        # 轮到玩家：停下，把操作权交回去。
        return

    # 兜底：上限内没轮到玩家，先显式判一次胜负；确属状态不一致
    # （还有能行动的单位却推不动）就清空去重表，绝不留在一个
    # 谁都不能动的死状态。
    if _check_combat_end(session):
        return
    combat["acts"] = {}
    combat["turn_i"] = 0


def _pick_enemy_target(session: RuleSession) -> dict | None:
    """敌体的目标选择：当前行动角色优先（他刚暴露自己），倒下就换人。

    目标偏好数据（战术人格）由 M2 接入 docs/system/04；这里只保证
    服务端自己选目标，玩家不能替敌体决定打谁。
    """
    active = next((unit for unit in session.party
                   if str(unit.get("id") or "") == str(session.active_unit_id)),
                  None)
    if _can_act_unit(active):
        return active
    return next((unit for unit in session.party if _can_act_unit(unit)), None)


def _enemy_turn(session: RuleSession, entry: dict) -> None:
    """一个敌体的一次行动（控制流骨架）。"""
    combat = session.combat
    enemy = _order_unit(session, entry)
    if enemy is None or not _can_act_unit(enemy):
        return
    target = _pick_enemy_target(session)
    if target is None:
        return
    outcome = _resolve_enemy_action(session, enemy, target) or {}
    text = str(outcome.get("text") or "")
    if text:
        session.add_log("system", text, speaker="战斗")
    for event in outcome.get("events") or []:
        combat["events"].append(event)


def _resolve_enemy_action(session: RuleSession, enemy: dict,
                          target: dict) -> dict:
    """敌体行动的结算（占位）。

    TODO(M2)：按 docs/system/02A 第五、九节与 data/system/tactics_enemies.json
    落地「选招 → 掷骰 → 比防护 → 走 deal_damage」的全过程，并接入
    docs/system/04 的敌体战术人格。在语义落地前返回空结果，
    不做任何数值猜测。
    """
    return {"text": "", "events": []}


def _end_combat(session: RuleSession, outcome: str) -> None:
    """战斗结束。经验结算与压力推进都在这里——玩家不能自己领奖。"""
    combat = session.combat
    if not combat:
        return
    combat["over"] = str(outcome)
    if outcome == "victory":
        # 只给**真正被击倒**的敌体经验；没打倒的不给。
        xp_total = sum(int(enemy.get("xp") or 0)
                       for enemy in combat.get("enemies") or []
                       if enemy.get("dead"))
        # TODO(M2)：难度 / 预算的结算系数与成长口径按
        # docs/system/02A 第十节、docs/system/01 第十四节落地。
        alive = _alive_party(session)
        share = xp_total // max(1, len(alive))
        for unit in alive:
            award_xp(session, unit, share)
        per = f"（每人 {share}）" if len(alive) > 1 else ""
        session.add_log("system", f"战斗胜利！全队共获得 {xp_total} 点经验{per}。",
                        speaker="战斗")
        tick_pressure(session, 1)
    else:
        session.add_log("system", "队伍全灭，这一程到此为止。", speaker="战斗")
    # 无论胜败，临时增益都要清掉；撤退走 combat_abandon，也调同一个清理。
    _clear_combat_buffs(session)
    session.touch()


def combat_abandon(session: RuleSession) -> dict:
    """脱离战斗（撤退）。**代价真实**：推进压力，且不结算经验。

    没有撤退出口，玩家明知打不过也只能耗到全灭——那是把决策变成惩罚。
    """
    combat = session.combat
    if not combat or combat.get("over"):
        raise ValueError("当前没有进行中的战斗")
    combat["over"] = "fled"
    session.add_log("system", "队伍脱离了战斗（没有获得经验）。", speaker="战斗")
    tick_pressure(session, 1)
    _clear_combat_buffs(session)
    session.touch()
    return combat_view(session)


def _clear_combat_buffs(session: RuleSession) -> None:
    """清掉「只在本场战斗内有效」的临时增益。

    这是一条独立的收尾路径，必须在战斗的所有出口都调到（胜利、失败、
    撤退）。漏掉任何一条，上一场的修正会悄悄常驻到下一场——这种 bug
    在测试里几乎看不出来，只有老玩家会觉得数值对不上。
    """
    for field in COMBAT_BUFF_FIELDS:
        for unit in session.party:
            if field in unit:
                unit[field] = 0
    session.touch()


def combat_view(session: RuleSession) -> dict:
    """战斗视图（前端独立视图直接吃这份）。"""
    combat = session.combat
    if not combat:
        return {"active": False}
    return {
        "active": not bool(combat.get("over")),
        "over": str(combat.get("over") or ""),
        "round": int(combat.get("round", 1) or 1),
        "scene_id": str(combat.get("scene_id") or ""),
        "current": copy.deepcopy(_current_unit(session)),
        "order": copy.deepcopy(combat.get("order") or []),
        "events": copy.deepcopy(combat.get("events") or []),
        "acts": copy.deepcopy(combat.get("acts") or {}),
        "enemies": [_unit_view(enemy) for enemy in combat.get("enemies") or []],
        "party": [_unit_view(unit) for unit in session.party],
    }


# ════════════════════════════════════════════════════════════════════════
# 七、派生值与明细
# ════════════════════════════════════════════════════════════════════════


def equipped_items(unit: dict | None) -> dict:
    """按装备槽位取当前穿戴（只认白名单槽位）。

    TODO(M2)：与装备目录 / 背包接线（docs/system/02A 装备表、各世界模组
    装备节）；目录数据在 M2 落入 data/，本函数保持槽位结构不变。
    """
    equipment = (unit or {}).get("equipment") or {}
    return {slot: equipment.get(slot)
            for slot, _ in EQUIP_SLOTS if equipment.get(slot)}


def item_guard_bonus(item) -> int:
    """装备条目自带的防护加值（护甲 Guard 值，0–6，见 docs/system/01 第二节）。"""
    if not isinstance(item, dict):
        return 0
    return int(item.get("guard") or 0)


def _stance_guard_bonus(unit: dict) -> int:
    """态势对防护的修正。

    TODO(M2)：按 docs/system/01 第七节与 data/system/stances.json 落地；
    当前返回 0。
    """
    return 0


def _cover_guard_bonus(unit: dict) -> int:
    """掩体对防护的修正。

    TODO(M2)：按 docs/system/02A 第五节与 data/system/tactics_combat.json
    落地；当前返回 0。
    """
    return 0


def guard_breakdown(unit: dict | None, session: RuleSession | None = None) -> dict:
    """把防护拆成可展示的几段：基础 + 装备 + 态势 + 掩体。

    展示与结算必须同源：compute_guard 直接调本函数。只返回一个数字的话，
    界面要另算一份——那必然漂移，玩家会看到角色卡与结算对不上。
    """
    parts = [{"label": "基础防护", "value": GUARD_BASE}]
    items = equipped_items(unit)
    for slot, slot_zh in EQUIP_SLOTS:
        item = items.get(slot)
        value = item_guard_bonus(item)
        if value and isinstance(item, dict):
            parts.append({"label": str(item.get("name") or slot_zh),
                          "value": value})
    for label, value in (("态势修正", _stance_guard_bonus(unit or {})),
                         ("掩体修正", _cover_guard_bonus(unit or {}))):
        if value:
            parts.append({"label": label, "value": int(value)})
    total = max(0, sum(int(part["value"]) for part in parts))
    return {"total": total, "base": GUARD_BASE, "parts": parts}


def compute_guard(unit: dict | None, session: RuleSession | None = None) -> int:
    """结算用的防护值（= breakdown 的 total）。"""
    return guard_breakdown(unit, session)["total"]


def recompute_unit(unit: dict | None, session: RuleSession | None = None) -> None:
    """统一重算派生值；凡是会改变属性 / 等级 / 装备的路径都必须走这里。

    当前只重算防护，并按已知上限夹取活力（没有上限时不硬夹——
    不能把「未落地的数据」当成 0 上限处理）。
    TODO(M2)：活力上限 / 移动 / 先攻等其余派生值按 docs/system/01 第二节
    与 02A/02B 落地（职途与装备数据接入后，仍统一从这里重算）。
    """
    if unit is None:
        return
    unit["guard"] = compute_guard(unit, session)
    cap = int(unit.get("max_vitality") or 0)
    vitality = max(0, int(unit.get("vitality") or 0))
    if cap > 0:
        vitality = min(vitality, cap)
    unit["vitality"] = vitality
    if session is not None:
        session.touch()


# ════════════════════════════════════════════════════════════════════════
# 八、成长
# ════════════════════════════════════════════════════════════════════════

# 等级门槛表：[(等级, 升入该级所需的累计经验), ...]。
# TODO(M2)：按 docs/system/01 第十四节（经验点法：积满 [当前等级 × 10] 点
# 升级；或里程碑法直接给等级）落地；空表期间恒定 1 级。
LEVEL_XP_THRESHOLDS: tuple[tuple[int, int], ...] = ()


def level_for_xp(xp: int) -> int:
    """按累计经验算等级；超过表顶就停在表顶。"""
    xp = max(0, int(xp or 0))
    level = 1
    for target_level, need in LEVEL_XP_THRESHOLDS:
        if xp >= int(need) and int(target_level) > level:
            level = int(target_level)
    return level


def growth_points_for(level: int) -> int:
    """升入某个等级时**由服务端发放**的成长点数。

    TODO(M2)：按 docs/system/01 第十四节「每级固定收益」与
    docs/system/02A/02B 的成长表落地；当前返回 0（占位）。

    发放只发生在这里——玩家只能消费已经发放的点数（见 assign_attribute），
    没有「加等级 / 加点数」的入口。
    """
    return 0


def award_xp(session: RuleSession, unit: dict | None, amount: int) -> dict:
    """给经验；跨级时自动升级并发放成长点数。

    升级必须**把等级写回单位**（只算不写会出现「日志显示升级、角色卡
    还是旧等级」）。升级只做派生值重算与上限夹取；活力是否随升级变化
    由 M2 的成长口径决定（docs/system/01 第十四节只写了活力上限 +2）。
    """
    if unit is None:
        raise ValueError("经验必须发到某个单位上")
    granted_amount = max(0, int(amount or 0))
    before_level = int(unit.get("level") or 1)
    unit["xp"] = max(0, int(unit.get("xp") or 0)) + granted_amount
    want_level = level_for_xp(unit["xp"])
    gained = 0
    if want_level > before_level:
        unit["level"] = want_level
        for level in range(before_level + 1, want_level + 1):
            points = int(growth_points_for(level) or 0)
            unit["growth_points"] = int(unit.get("growth_points") or 0) + points
            gained += points
        recompute_unit(unit, session)
    session.add_log(
        "system",
        f"{unit.get('name', '')} 获得 {granted_amount} 点经验"
        + (f"，等级 {before_level} → {unit['level']}" if want_level > before_level
           else f"（LV{unit['level']}）")
        + (f"，发放 {gained} 点成长点数" if gained else ""))
    session.touch()
    return {"xp": unit["xp"], "level": unit["level"],
            "growth_points": int(unit.get("growth_points") or 0),
            "gained": gained}


def assign_attribute(session: RuleSession, unit_id: str, attribute: str,
                     delta: int = 1) -> dict:
    """消费成长点数调整属性：**服务端发放、玩家分配**。

    校验四件事：属性名在白名单内；结果不越 1–10 的界；
    delta 不为 0；手里确实有成长点数。任何一条不过都直接拒绝。

    TODO(M2)：A 类成长「每项属性通过成长最多 +4」需要按
    docs/system/01 第十四节追踪每项属性的历史加成，与上限校验一并落地。
    """
    unit = next((p for p in session.party
                 if str(p.get("id") or "") == str(unit_id or "")), None)
    if unit is None:
        raise ValueError("单位不存在")
    key = str(attribute or "").strip().upper()
    if key not in ATTRIBUTES:
        raise ValueError("属性名不合法")
    delta = int(delta or 0)
    if delta == 0:
        raise ValueError("点数变化不能为 0")
    current = int((unit.get("attributes") or {}).get(key, 4))
    new = current + delta
    if not (ATTRIBUTE_MIN <= new <= ATTRIBUTE_MAX):
        raise ValueError(
            f"{ATTRIBUTES[key]['zh']}必须保持在 {ATTRIBUTE_MIN}–{ATTRIBUTE_MAX} "
            f"之间（当前 {current}）")
    if int(unit.get("growth_points") or 0) < 1:
        raise ValueError("没有可分配的成长点数（由服务端在升级时发放）")
    unit["growth_points"] = int(unit["growth_points"]) - 1
    before_cap = int(unit.get("max_vitality") or 0)
    unit.setdefault("attributes", {})[key] = new
    recompute_unit(unit, session)
    after_cap = int(unit.get("max_vitality") or 0)
    session.add_log(
        "system",
        f"{unit.get('name', '')} 的{ATTRIBUTES[key]['zh']} {current} → {new}"
        + (f"（活力上限 {before_cap} → {after_cap}）" if after_cap != before_cap
           else ""))
    session.touch()
    return {"unit": _unit_view(unit), "attribute": key, "value": new,
            "growth_points": unit["growth_points"],
            "d_vitality_cap": after_cap - before_cap}


__all__ = [
    # 一、术语与白名单
    "ATTRIBUTES", "ATTRIBUTE_IDS", "ATTRIBUTE_MIN", "ATTRIBUTE_MAX",
    "GUARD_BASE", "RESOURCE_POOLS", "ACTION_KINDS", "COMBAT_ACTIONS",
    "EQUIP_SLOTS", "OUTCOMES",
    # 二、掷骰器
    "DICE_MAX_COUNT", "DICE_MAX_SIDES", "DEFAULT_CHECK_NOTATION", "roll",
    # 三、会话状态契约
    "new_unit", "RuleSession",
    # 四、结算的单一进出口
    "deal_damage", "heal_unit", "change_resource", "add_condition",
    "failure_cost", "apply_failure", "tick_pressure",
    # 五、场景行动单入口
    "perform_action", "resolve_check", "judge_check",
    # 六、战斗状态机
    "COMBAT_BUFF_FIELDS", "start_combat", "build_encounter",
    "combat_abandon", "combat_view",
    # 七、派生值与明细
    "equipped_items", "item_guard_bonus", "guard_breakdown", "compute_guard",
    "recompute_unit",
    # 八、成长
    "LEVEL_XP_THRESHOLDS", "level_for_xp", "growth_points_for", "award_xp",
    "assign_attribute",
]
