# -*- coding: utf-8 -*-
"""worldgen：世界生成内核（M12-W1/W2，WORLD-DESIGN.md §4 / §5 / §11）。

纯函数模块：只读输入、零 IO、零第三方依赖（只 import 标准库）。
同 (spec, seed) 连跑两次结果逐字节相等；模型只通过注入的 port 填文本字段，
port=None（离线）是正常路径，整条流水线仍可跑通并过校验。

对外面：
- normalize_spec(raw) -> spec        需求规范化（P1 离线路径：调用方直接给 JSON）
- choose_base(spec, ...) -> base     模板底座选择（P2）
- synthesize(spec, base, *, seed, port=None, budget=None) -> world.module（P3）
- roster(spec, seed, *, factions=None) -> [npc]        （P4 名册）
- estimate(spec) -> {...}                              （P6 预估，只展示不强制）
- validate_world(world, *, spec=None) -> [issue]       （P5 一致性检查）
- validate_atlas(atlas, *, world=None, checks=None) -> [issue]  （§5.1 C1–C7）
- repair(atlas, issues, *, rng) -> {atlas, repaired, provenance}（§5.2 局部修复）
- atlas_provenance(atlas) -> {...}                     （来源标注汇总）

新口径（与设计原文的出入，已在 PR「风险与口径补充」登记）：
1. WORLDGEN_CALL_BUDGET = 24（人拍板 P4，取代设计建议的 12）。
2. goods 按世界特性生成（人拍板 P2，取代固定 8–12 种常量表）：
   以「类目-时代」映射表驱动，衣/食/住/行/军五类保底；每条 goods 带 era 时代档。
3. goods 校验只在 world 带 goods 键时执行——三个官方世界与 fixture 没有 goods，
   这样 validate_world 对它们保持零 error（W2 回归锚）。spec.economy_on=True
   而合成结果缺 goods 时仍是 error。
4. era 联动规则：goods.era 时代档**高于** world.type → error（奇幻世界卖激光枪）；
   低于 → warn（未来世界有老物件可接受）。
5. 预算计数的 est_tokens 按每次调用固定 +128 估算（W1 无真实网关，W3 接线时对齐）。
6. 合成结果在 world.module 顶层附带 goods / worldgen_provenance / lexicon_overrides
   三个附加键；W8 扩 world.schema.json 时转正。
7. C2「每个 frame 的连接图强连通」落地为**逐帧连通 + 零坐标分量豁免**：
   实测 entropic-net 的 unlisted 区域（「地图上没有」）与其关键地点、
   abstract 区域与其关键地点都是**零坐标**分量——帧间只靠连接相通
   （atlas.py 设计约定），它们经跨帧「门」进图，是合法的图外浮岛。
   口径：帧内有坐标的分量必须并入本帧主路网，断连报 error；战术帧
   是无跨帧连接的投影孤岛，逐帧口径下天然通过。有向 oneway 子句逐帧判定。
8. C5 venue 抽查只在校验器拿到 world 且其 random_tables **有**场地行时执行
   （与 atlas_gen._venue_pool 语义对齐：世界没有场地行时生成器落 system
   兜底表，而 worldgen 零 IO 读不到它，无法核对）。
9. C6 白名单抽查表 C6_SPOT_CHECKS 为最小实现（设计只给「荒野不产铁锭」
   一例）：wilderness / abstract / water 三性状 × 少数违和产物名，精确名匹配。
10. C7「zone_steps 折算」落地：metric 地点/战术帧上 cardinal via 的连接两端
   必须正交相邻且方向与 via 一致；相邻一格（6 米 / 一次走位 = 1 跨区）上
   的带档连接只能是 short。世界帧不查几何（编译器连接的是脚印边缘而非
   锚点格，锚点间距与档位无矛盾——防误伤）。
11. repair 的 rng 参数预留（§5.2 的「该帧整体重掷」由 W3 生成流水线编排，
    本函数只做确定性局部修复）；C3 只修「缺门」（多门违反「只新增边」
    原则，不修、如实报）。
"""

import copy
import hashlib
import random

# ── 常量（§4.2 默认表 / §4.5 规模档 / V14 预算） ──────────────────────────

WORLDGEN_VERSION = 1

# 人拍板 P4（2026-10-18）：一次生成会话的模型调用上限。
WORLDGEN_CALL_BUDGET = 24
# 每次调用的 token 估算值（新口径 5）。
WORLDGEN_TOKENS_PER_CALL = 128

WORLD_TYPES = ("fantasy", "modern", "future")
ENGINES = ("tactics", "build")
SCALES = ("sketch", "region", "domain")
ERA_ORDER = {"fantasy": 0, "modern": 1, "future": 2}

# §4.5 规模档默认表：区域数 / 关键地点每区 / 具名 NPC / 经济开档 / 预估展示。
SCALE_DEFAULTS = {
    "sketch": {"regions": 3, "key_places_per_region": 2, "npcs": 12,
               "economy_on": False, "duration": "一晚"},
    "region": {"regions": 5, "key_places_per_region": 4, "npcs": 30,
               "economy_on": True, "duration": "一周–一月（游戏时间）"},
    "domain": {"regions": 8, "key_places_per_region": 5, "npcs": 60,
               "economy_on": True, "duration": "数月–数年（游戏时间）"},
}
REGION_RANGE = (2, 10)
KEY_PLACE_RANGE = (1, 6)
NPC_RANGE = (0, 80)
FACTION_DEFAULT = {"sketch": 2, "region": 4, "domain": 4}
FACTION_RANGE = (0, 12)

# P2 底座目录：world_type → 官方模板 key。
DEFAULT_BASE_CATALOG = {
    "fantasy": "emberfall",
    "modern": "threshold",
    "future": "entropic-net",
}

# goods 保底五类（人拍板 P2）：衣 / 食 / 住 / 行 / 军。
GOODS_REQUIRED_CATEGORIES = ("clothing", "food", "shelter", "travel", "military")
CATEGORY_LABELS = {
    "clothing": "衣", "food": "食", "shelter": "住", "travel": "行",
    "military": "军", "tool": "工具", "medicine": "医药", "energy": "能源",
    "luxury": "奢侈品",
}

# 「类目-时代」goods 映射表（人拍板 P2 的核心数据）：按世界类型给出各类目的
# 天然产物。era 取其所属世界类型的时代档——奇幻世界不会从这张表里长出激光枪。
GOODS_CATALOG = {
    "fantasy": {
        "clothing": ["粗布斗篷", "麻布衬衣"],
        "food": ["黑麦干粮", "腌菜", "蜂蜜酒"],
        "shelter": ["蜂蜡蜡烛", "粗毛毯", "灯油"],
        "travel": ["驮马", "帆布行囊", "绳索"],
        "military": ["铁剑", "长弓与箭", "皮甲"],
        "tool": ["铁锹", "铁匠锤"],
        "medicine": ["草药包", "疗伤药膏"],
        "luxury": ["银器", "宝石"],
    },
    "modern": {
        "clothing": ["帆布工装", "防水外套"],
        "food": ["罐头食品", "瓶装水", "压缩饼干"],
        "shelter": ["应急照明灯", "折叠帐篷", "保温毯"],
        "travel": ["汽油", "自行车轮胎", "公交月票"],
        "military": ["防刺背心", "强光手电", "对讲机"],
        "tool": ["多功能工具钳", "防尘口罩"],
        "medicine": ["急救包", "止痛药"],
        "luxury": ["二手相机", "名贵手表"],
    },
    "future": {
        "clothing": ["相变温控服", "防护外骨骼衬层"],
        "food": ["蛋白合成砖", "营养膏", "合成咖啡"],
        "shelter": ["聚变电池", "恒温睡袋", "气密封隔舱"],
        "travel": ["悬浮滑板", "纳米轮胎", "轨道通行证"],
        "military": ["脉冲步枪", "电磁护盾", "战术目镜"],
        "tool": ["万能修复喷剂", "全息工具组"],
        "medicine": ["纳米医疗舱", "再生敷料"],
        "luxury": ["记忆晶体", "全息艺术品"],
    },
}
# 每条 goods 的档位（tier 1–3）：保底类目 1–2，奢侈品 3。
GOODS_TIER = {
    "clothing": 1, "food": 1, "shelter": 1, "travel": 2, "military": 2,
    "tool": 2, "medicine": 2, "energy": 2, "luxury": 3,
}
GOODS_STOCK_BAND = {1: (20, 200), 2: (10, 80), 3: (2, 12)}
GOODS_PRICE_BAND = {1: (2, 8), 2: (10, 40), 3: (60, 200)}

# 兜底短语表（§4.3 三级文本来源的第三级）：port 与模板都不可用时的内置文本。
BUILTIN_TEXT = {
    "fantasy": {
        "world_names": ["余烬之地", "灰烬平原", "烬火王国"],
        "region_prefix": ["灰烬", "古树", "低语", "白骨", "苔灯", "石脉", "霜风", "余烬", "静林", "巨干"],
        "region_suffix": ["荒原", "市集", "哨站", "渡口", "矿坑", "林地", "要塞", "村落"],
        "region_summary": "{name}是一片被余烬浸润的土地，往来的人靠着炉火与旧约维生。",
        "region_atmosphere": ["炉火、灰烬与旧约定的低语", "潮湿的根须气味与远处的钟声"],
        "key_places": ["老集市（有人在夜里以物易物）", "界碑（碑文比城更老）", "炉房（旅人歇脚的节点）"],
        "events": ["商队迟归", "有人从荒原带回不该带的东西", "炉火一夜熄了三次"],
        "faction_names": ["守烬人会", "灰烬行会", "静林氏族", "边境自由团"],
        "faction_summary": "{name}在这片土地上的影响力来自传统与物资的分配权。",
        "glossary": [("余烬", "世界残留的可燃魔力，一切秘术的燃料"),
                     ("灰潮", "余烬的一次区域性爆发，会改变活物")],
        "history": [("第二纪", "大沉默", "诸神停止回应所有祈祷，持续约九百年。")],
        "npc_roles": ["守烬人", "灰法师", "树语者", "行商", "遗物猎人"],
        "npc_names": ["艾德文", "茉尔嘉", "卡霖", "多萝西", "布伦", "希瑟", "洛恩", "薇拉"],
        "npc_drives": ["为家族赎罪", "寻找失踪的师兄", "攒够钱开一间铺子", "弄清灰潮的真相"],
    },
    "modern": {
        "world_names": ["灰线都市", "临港市", "雾城"],
        "region_prefix": ["旧港", "铁北", "白鹭", "高线", "松岭", "碗底", "镜湖", "灰堤", "七号线", "城南"],
        "region_suffix": ["区", "工业带", "新城", "市场", "沿线"],
        "region_summary": "{name}是这座城市的普通一角，但夜里总有说不通的传闻。",
        "region_atmosphere": ["霓虹、锈与夜班的吆喝", "廉价食物、凌晨的灯光、永远的施工围挡"],
        "key_places": ["二十四小时便利店（店员换得很勤）", "旧钟楼（钟停在一个固定时刻）", "地下通道（墙上有新的涂鸦）"],
        "events": ["失踪的出租车司机", "深夜的停电", "有人在打听一栋楼"],
        "faction_names": ["特别事务办公厅", "雾缆互助会", "港区工会", "本地财团"],
        "faction_summary": "{name}在城里的分量来自信息与人手。",
        "glossary": [("渗溢", "异常现象向现实的一次明显渗透"),
                     ("薄处", "现实特别薄、容易出事的地点")],
        "history": [("1971", "第一次渗溢", "连续四十天出现无法解释的光与低频声，官方记录为地下电缆故障。")],
        "npc_roles": ["外勤调查员", "夜班乘务员", "黑市贩子", "急诊护士", "线人"],
        "npc_names": ["林昭", "陈岁", "周宁", "吴桐", "王衡", "郑迟", "沈舟", "韩禾"],
        "npc_drives": ["还清一笔债", "查清亲友的失踪", "保住这间铺子", "离开这座城市"],
    },
    "future": {
        "world_names": ["熵网边缘", "零号轨道城", "深空节点"],
        "region_prefix": ["轨道", "熵潮", "数据", "零点", "辐射", "同步", "晶格", "深井", "链路", "浮环"],
        "region_suffix": ["环带", "节点", "坞", "塔", "站"],
        "region_summary": "{name}是网络与物质交界的一环，同步率决定这里的一切秩序。",
        "region_atmosphere": ["恒温的白光与低频的电流声", "全息广告与纳米尘的反光"],
        "key_places": ["同步站（排队的人不说话）", "旧服务器井（据说还连着网）", "物资闸口（凭纯度放行）"],
        "events": ["一段数据实体逃逸", "同步率波动", "有人在黑市出售纯度"],
        "faction_names": ["同步局", "纯度教团", "自由链路", "轨道财团"],
        "faction_summary": "{name}的权威来自对网络与物资通道的控制。",
        "glossary": [("同步率", "个体与熵网保持一致的程度"),
                     ("纯度", "未经熵蚀的物质/数据的度量")],
        "history": [("接入纪", "大联网", "人类意识接入熵网，同步率成为新的社会分层。")],
        "npc_roles": ["网潜者", "同步技师", "纯度稽查员", "废土回收员", "数据掮客"],
        "npc_names": ["零一", "临川", "照白", "柏舟", "溯", "筌", "岫", "澜"],
        "npc_drives": ["提升同步率", "找回被格式化的记忆", "逃离网络审查", "赚够离开轨道的钱"],
    },
}

# 词表覆盖表（内置副本，零 IO）：tone_terms → atlas.lexicon trait。
# 数据源：data/atlas/lexicon.json 的既有条目（只读快照，改词表时同步）。
TONE_TRAIT_TABLE = {
    "城区": "settlement", "街区": "settlement", "港口": "settlement", "市场": "settlement",
    "住宅": "settlement", "都市": "settlement", "工业区": "settlement", "老港": "settlement",
    "荒野": "wilderness", "山脉": "wilderness", "极地": "wilderness", "平原": "wilderness",
    "林城": "wilderness", "地下林": "wilderness", "废墟": "wilderness",
    "小国": "span_country",
    "环道": "vertical", "崖壁": "vertical", "楼层": "vertical",
    "地下": "below", "负一层": "below", "负二层": "below", "负三层": "below",
    "地铁": "network", "根系": "network", "桥": "network", "通道": "network",
    "群岛": "water", "水上": "water",
    "封锁": "barrier", "雷区": "barrier",
    "地图上没有": "unlisted", "导航到不了": "unlisted",
    "不存在的物理": "abstract", "无法解析": "abstract", "数据区域": "abstract", "网潜": "abstract",
    "每次测量不同": "unstable", "渗溢": "unstable", "薄处": "unstable", "没有几何": "unstable",
    "带出来": "carry", "成为现实": "carry",
    "而不是数天": "shortcut", "穿行约需": "shortcut",
}


# ── 种子派生（确定性合成的基础） ──────────────────────────────────────────

def derive_seed(base_seed, salt):
    """(seed, salt) → 稳定的 64 位整数子种子（_derive_seed 同款派生）。"""
    if isinstance(base_seed, str):
        raw = "%s|%s" % (base_seed, salt)
    else:
        raw = "%s|%s" % (int(base_seed), salt)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def new_session():
    """会话级预算计数器（V14：不落盘，生成发生在战役创建之前）。"""
    return {"calls": 0, "est_tokens": 0}


# ── P1：需求规范化 ────────────────────────────────────────────────────────

def _clamp(value, low, high, note, label):
    if not isinstance(value, int) or isinstance(value, bool):
        note.append("%s 越界（非整数），按下限 %d 钳制" % (label, low))
        return low
    if value < low or value > high:
        note.append("%s=%s 越界，钳制到 [%d, %d]" % (label, value, low, high))
        return max(low, min(high, value))
    return value


def normalize_spec(raw):
    """把玩家输入（世界类型/剧本/描述/overrides）规范化为 WorldSpec。

    非法 world_type 落 modern（§2.3 口径 1：不推导）、非法 engine 落 tactics、
    非法 scale 落 region；越界数量钳制并记 spec_notes（可解释）。
    返回的 spec 可 JSON 序列化、进存档。
    """
    raw = dict(raw or {})
    notes = []

    world_type = raw.get("world_type")
    if world_type not in WORLD_TYPES:
        if world_type is not None:
            notes.append("world_type=%r 不认识，落 modern（§2.3 口径 1）" % (world_type,))
        world_type = "modern"

    engine = raw.get("engine")
    if engine not in ENGINES:
        if engine is not None:
            notes.append("engine=%r 不认识，落 tactics" % (engine,))
        engine = "tactics"

    scale = raw.get("scale")
    if scale not in SCALES:
        if scale is not None:
            notes.append("scale=%r 不认识，落 region" % (scale,))
        scale = "region"
    defaults = SCALE_DEFAULTS[scale]

    tone_terms = [t for t in (raw.get("tone_terms") or []) if isinstance(t, str) and t]

    regions_req = dict(raw.get("regions_req") or {})
    count = _clamp(regions_req.get("count", defaults["regions"]),
                   REGION_RANGE[0], REGION_RANGE[1], notes, "regions_req.count")
    kp = _clamp(regions_req.get("key_places_per_region", defaults["key_places_per_region"]),
                KEY_PLACE_RANGE[0], KEY_PLACE_RANGE[1], notes, "key_places_per_region")

    factions_req = dict(raw.get("factions_req") or {})
    fac = _clamp(factions_req.get("count", FACTION_DEFAULT[scale]),
                 FACTION_RANGE[0], FACTION_RANGE[1], notes, "factions_req.count")

    npcs_req = dict(raw.get("npcs_req") or {})
    npcs = _clamp(npcs_req.get("named", defaults["npcs"]),
                  NPC_RANGE[0], NPC_RANGE[1], notes, "npcs_req.named")

    laws_req = []
    for entry in (raw.get("laws_req") or []):
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        laws_req.append({
            "name": str(entry["name"]),
            "forbid_tags": [str(t) for t in (entry.get("forbid_tags") or [])],
            "scope": entry.get("scope") if entry.get("scope") in ("all", "regions") else "all",
            "threshold": entry.get("threshold") if entry.get("threshold") in ("forbid", "tolerate", "allow") else "forbid",
        })

    economy_raw = raw.get("economy_on")
    economy_on = bool(economy_raw) if economy_raw is not None else defaults["economy_on"]

    spec = {
        "world_type": world_type,
        "engine": engine,
        "scale": scale,
        "tone_terms": tone_terms,
        "regions_req": {"count": count, "key_places_per_region": kp},
        "factions_req": {"count": fac},
        "laws_req": laws_req,
        "economy_on": bool(economy_on),
        "npcs_req": {"named": npcs},
        "secrets_required": bool(raw.get("secrets_required", True)),
        "seed_hint": raw.get("seed_hint"),
    }
    extra_goods = raw.get("goods_extra")
    if isinstance(extra_goods, list):
        spec["goods_extra"] = [g for g in extra_goods if isinstance(g, dict) and g.get("name")]
    if notes:
        spec["spec_notes"] = notes
    return spec


# ── P2：模板选择 ──────────────────────────────────────────────────────────

def choose_base(spec, *, catalog=None, templates=None):
    """按 world_type 从目录挑最近的官方模板作底（§4.1 P2）。

    catalog：{world_type: template_key}，缺省用官方三世界目录；
    templates：{template_key: world.module dict}，由调用方加载传入
    （worldgen 零 IO，不自己读文件）。预制开局可把 templates[key] 直填 spec
    并跳过 synthesize；描述/混合开局走本函数 + synthesize。
    """
    catalog = catalog or DEFAULT_BASE_CATALOG
    key = catalog.get(spec["world_type"])
    if key is None:
        key = DEFAULT_BASE_CATALOG["modern"]
    template = None
    if templates and key in templates:
        template = templates[key]
    return {"template_key": key, "template": template, "overrides": {}}


# ── P3：合成 ──────────────────────────────────────────────────────────────

def _port_call(port, budget, spec, field):
    """三级文本来源的第一级：调用模型端口。预算耗尽视为不可用（单调降级）。"""
    if port is None or budget is None:
        return None
    if budget.get("calls", 0) >= WORLDGEN_CALL_BUDGET:
        return None
    suggest = port.get("suggest") if isinstance(port, dict) else getattr(port, "suggest", None)
    if not callable(suggest):
        return None
    budget["calls"] = budget.get("calls", 0) + 1
    budget["est_tokens"] = budget.get("est_tokens", 0) + WORLDGEN_TOKENS_PER_CALL
    try:
        return suggest(spec, field)
    except Exception:
        return None


def _pick(rng, items):
    return items[rng.randrange(len(items))]


def _build_text(spec, base, seed, port, budget):
    """三级文本来源：port → 模板同字段 → 内置兜底短语表。返回 (texts, provenance)。"""
    wt = spec["world_type"]
    builtin = BUILTIN_TEXT[wt]
    template = base.get("template") or {}
    t_regions = [r for r in template.get("regions", []) if isinstance(r, dict)]
    t_factions = [f for f in template.get("factions", []) if isinstance(f, dict)]
    provenance = {"texts": {}, "degraded": False}

    def fetch(field, template_value, builtin_value):
        """返回 (text, source)；field 用于端口与溯源记录。"""
        value = _port_call(port, budget, spec, field)
        if value is not None and str(value).strip():
            provenance["texts"][field] = "port"
            return str(value)
        if template_value:
            provenance["texts"][field] = "template"
            return template_value
        provenance["texts"][field] = "builtin"
        if not budget or budget.get("calls", 0) >= WORLDGEN_CALL_BUDGET:
            provenance["degraded"] = True
        return builtin_value

    rng = random.Random(derive_seed(seed, "text"))
    world_name = fetch("world_name",
                       (template.get("world") or {}).get("name"),
                       _pick(rng, builtin["world_names"]))

    region_texts = []
    for i in range(spec["regions_req"]["count"]):
        rid = "region-%02d" % (i + 1)
        t_region = t_regions[i] if i < len(t_regions) else {}
        if t_region.get("name"):
            name = t_region["name"]
        else:
            name = _pick(rng, builtin["region_prefix"]) + _pick(rng, builtin["region_suffix"])
        summary = fetch("region_summary:%s" % rid,
                        t_region.get("summary"),
                        builtin["region_summary"].format(name=name))
        atmosphere = fetch("region_atmosphere:%s" % rid,
                           t_region.get("atmosphere"),
                           _pick(rng, builtin["region_atmosphere"]))
        kps = list(t_region.get("key_places") or [])
        if not kps:
            kps = [_pick(rng, builtin["key_places"])
                   for _ in range(spec["regions_req"]["key_places_per_region"])]
        events = list(t_region.get("events") or [])
        if not events:
            events = [_pick(rng, builtin["events"]) for _ in range(2)]
        region_texts.append({"id": rid, "name": name, "summary": summary,
                             "atmosphere": atmosphere, "key_places": kps,
                             "events": events})

    faction_texts = []
    for i in range(spec["factions_req"]["count"]):
        fid = "faction-%02d" % (i + 1)
        t_faction = t_factions[i] if i < len(t_factions) else {}
        name = t_faction.get("name") or _pick(rng, builtin["faction_names"])
        summary = fetch("faction_summary:%s" % fid,
                        t_faction.get("summary"),
                        builtin["faction_summary"].format(name=name))
        faction_texts.append({"id": fid, "name": name, "summary": summary,
                              "alignment": t_faction.get("alignment") or "未定"})

    glossary = []
    t_glossary = [g for g in template.get("glossary", []) if isinstance(g, dict)]
    glossary_source = list(t_glossary) or [{"term": t, "meaning": m}
                                           for t, m in builtin["glossary"]]
    for i, g in enumerate(glossary_source[:8]):
        meaning = fetch("glossary_meaning:%s" % g.get("term", i), g.get("meaning"), "")
        glossary.append({"term": g["term"], "meaning": meaning})

    history = []
    t_history = [h for h in template.get("history", []) if isinstance(h, dict)]
    history_source = list(t_history) or [{"year": y, "name": n, "detail": d}
                                         for y, n, d in builtin["history"]]
    for i, h in enumerate(history_source[:6]):
        detail = fetch("history_detail:%s" % h.get("id", i), h.get("detail"), "")
        history.append({"id": h.get("id") or "hist-%02d" % (i + 1),
                        "year": h.get("year") or "", "name": h.get("name") or "",
                        "detail": detail})

    texts = {"world_name": world_name, "regions": region_texts,
             "factions": faction_texts, "glossary": glossary, "history": history}
    return texts, provenance


def _lexicon_overrides(spec):
    """tone_terms → atlas.lexicon 覆盖项（§4.3 词表闭环：加词不加地图）。

    词表没有覆盖的词不进 overrides，由调用方经 provenance 落 warn。
    """
    overrides = []
    for i, term in enumerate(spec.get("tone_terms") or []):
        trait = TONE_TRAIT_TABLE.get(term)
        if trait is None:
            # 尝试子串匹配（「老港口」命中「港口」）。
            for known, tr in TONE_TRAIT_TABLE.items():
                if known in term or term in known:
                    trait = tr
                    break
        if trait is not None:
            overrides.append({"id": "%s-cov-%02d" % (trait, i + 1),
                              "trait": trait, "terms": [term]})
    uncovered = [t for t in (spec.get("tone_terms") or [])
                 if not any(t in o["terms"] for o in overrides)]
    return overrides, uncovered


def _build_goods(spec, base, seed, region_ids):
    """按世界特性合成 goods（人拍板 P2）：类目-时代映射表 + 保底覆盖。

    上不封顶：spec.goods_extra 可追加；下有保底：五类每类至少 1 种。
    """
    if not spec["economy_on"]:
        return []
    wt = spec["world_type"]
    catalog = GOODS_CATALOG[wt]
    rng = random.Random(derive_seed(seed, "goods"))
    goods = []
    index = 0
    for category in sorted(catalog):
        era = wt if wt in ERA_ORDER else "modern"
        for name in catalog[category]:
            index += 1
            tier = GOODS_TIER[category]
            stock = rng.randint(*GOODS_STOCK_BAND[tier])
            price = rng.randint(*GOODS_PRICE_BAND[tier])
            produced_by = region_ids[(index * 7) % len(region_ids)] if region_ids else ""
            goods.append({
                "good_id": "good-%02d" % index,
                "name": name,
                "category": category,
                "tier": tier,
                "base_price": price,
                "stock": stock,
                "produced_by": [produced_by] if produced_by else [],
                "era": era,
            })
    for extra in spec.get("goods_extra") or []:
        index += 1
        category = extra.get("category") if extra.get("category") in CATEGORY_LABELS else "tool"
        era = extra.get("era") if extra.get("era") in ERA_ORDER else wt
        tier = int(extra.get("tier", GOODS_TIER[category])) or GOODS_TIER[category]
        stock = int(extra.get("stock", rng.randint(*GOODS_STOCK_BAND[tier])))
        price = int(extra.get("base_price", rng.randint(*GOODS_PRICE_BAND[tier])))
        produced_by = extra.get("produced_by")
        if not isinstance(produced_by, list):
            produced_by = [region_ids[(index * 7) % len(region_ids)]] if region_ids else []
        goods.append({
            "good_id": "good-%02d" % index,
            "name": str(extra["name"]),
            "category": category,
            "tier": tier,
            "base_price": price,
            "stock": stock,
            "produced_by": produced_by,
            "era": era,
        })
    return goods


def synthesize(spec, base, *, seed, port=None, budget=None):
    """合成完整世界模块（§4.1 P3）：结构由规则确定性生成，文本走三级来源。

    返回 world.module 形状的 dict；另附三个附加键（新口径 6）：
    - goods：经济开档时的商品清单（人拍板 P2）
    - worldgen_provenance：文本来源 / 预算计数 / 未覆盖 tone_terms
    - lexicon_overrides：tone_terms 的 atlas.lexicon 覆盖项
    """
    spec = spec if isinstance(spec, dict) and "world_type" in spec else normalize_spec(spec)
    if budget is None:
        budget = new_session()
    rng = random.Random(derive_seed(seed, "structure"))

    texts, provenance = _build_text(spec, base, seed, port, budget)
    overrides, uncovered = _lexicon_overrides(spec)
    provenance["uncovered_terms"] = uncovered
    provenance["budget"] = {"calls": budget.get("calls", 0),
                            "est_tokens": budget.get("est_tokens", 0),
                            "limit": WORLDGEN_CALL_BUDGET}
    provenance["degraded"] = bool(provenance["degraded"] or uncovered or
                                  budget.get("calls", 0) >= WORLDGEN_CALL_BUDGET
                                  and port is not None)

    wt = spec["world_type"]
    regions = []
    for r in texts["regions"]:
        regions.append({
            "id": r["id"], "name": r["name"], "summary": r["summary"],
            "atmosphere": r["atmosphere"], "key_places": list(r["key_places"]),
            "events": list(r["events"]), "source": "worldgen:synthesized",
        })
    region_ids = [r["id"] for r in regions]

    factions = [{
        "id": f["id"], "name": f["name"], "alignment": f["alignment"],
        "summary": f["summary"], "source": "worldgen:synthesized",
    } for f in texts["factions"]]

    laws = []
    for i, req in enumerate(spec.get("laws_req") or []):
        laws.append({
            "id": "law-%02d" % (i + 1),
            "name": req["name"],
            "forbid_tags": list(req.get("forbid_tags") or []),
            "scope_place_ids": [] if req.get("scope", "all") == "all"
                               else rng.sample(region_ids, min(2, len(region_ids))),
            "threshold": req.get("threshold", "forbid"),
            "penalty": "叙事处置（由导引者按世界规则给出）",
            "source": "worldgen:synthesized",
        })

    world = {
        "schema_version": 1,
        "kind": "world.module",
        "source": "worldgen:synthesized",
        "world": {
            "key": base.get("template_key") or "generated",
            "name": texts["world_name"],
            "engine": spec["engine"],
            "world_type": wt,
            "source": "worldgen:synthesized",
        },
        "glossary": [{"term": g["term"], "meaning": g["meaning"],
                      "source": "worldgen:synthesized"} for g in texts["glossary"]],
        "history": [{"id": h["id"], "year": h["year"], "name": h["name"],
                     "detail": h["detail"], "source": "worldgen:synthesized"}
                    for h in texts["history"]],
        "regions": regions,
        "factions": factions,
        "laws": laws,
        "worldgen_provenance": provenance,
        "lexicon_overrides": overrides,
    }
    if spec["economy_on"]:
        world["goods"] = _build_goods(spec, base, seed, region_ids)
    return world


# ── P4：NPC 名册估算 ──────────────────────────────────────────────────────

def roster(spec, seed, *, factions=None):
    """按 spec 的具名 NPC 预算产出名册（§8.3 目标形状：scenario $defs.npc）。

    结构字段由种子确定性合成；模型润色 name/drive 是 W3/W7 的端口职责。
    """
    spec = spec if isinstance(spec, dict) and "world_type" in spec else normalize_spec(spec)
    builtin = BUILTIN_TEXT[spec["world_type"]]
    rng = random.Random(derive_seed(seed, "roster"))
    factions = list(factions or [])
    npcs = []
    for i in range(spec["npcs_req"]["named"]):
        npc = {
            "id": "npc-%02d" % (i + 1),
            "name": _pick(rng, builtin["npc_names"]),
            "role": _pick(rng, builtin["npc_roles"]),
            "drive": _pick(rng, builtin["npc_drives"]),
            "source": "worldgen:roster",
        }
        if factions:
            npc["faction"] = factions[i % len(factions)]
        if spec["secrets_required"] and i == 0:
            npc["secret"] = "掌握一条与主线相关的秘密（由导引者细化）"
        npcs.append(npc)
    return npcs


# ── 预估（§4.5：只展示、不强制） ─────────────────────────────────────────

def estimate(spec):
    """体量 / 时长估算（P6 预览卡展示用；不强制时长）。"""
    spec = spec if isinstance(spec, dict) and "world_type" in spec else normalize_spec(spec)
    defaults = SCALE_DEFAULTS[spec["scale"]]
    regions = spec["regions_req"]["count"]
    return {
        "scale": spec["scale"],
        "regions": regions,
        "key_places": regions * spec["regions_req"]["key_places_per_region"],
        "npcs": spec["npcs_req"]["named"],
        "factions": spec["factions_req"]["count"],
        "laws": len(spec.get("laws_req") or []),
        "economy_on": spec["economy_on"],
        "sim_load": {"sketch": "low", "region": "medium", "domain": "high"}[spec["scale"]],
        "duration": defaults["duration"],
    }


# ── P5：世界校验器 ────────────────────────────────────────────────────────
# C 系列的「世界级」部分；ATLAS 级（C1–C7）在 W2 落地于同模块。
# 回归锚（§5.1）：三个官方世界 + fixture 必须零 error（warn 允许）——所以
# goods 相关检查只在 world 带 goods 键时生效（新口径 3）。

def _issue(code, level, hint, place_id=None, frame_id=None):
    item = {"code": code, "level": level, "hint": hint}
    if place_id:
        item["place_id"] = place_id
    if frame_id:
        item["frame_id"] = frame_id
    return item


def validate_world(world, *, spec=None):
    """世界校验器：产出 {code, level, place_id?, hint} 分级 issue。

    error 阻断、warn 放行（warn 进预览卡）。传 spec 时额外做 C8 规格核对
    （数量偏差为 warn——用户显式改过的字段以 spec 为准）。
    """
    issues = []
    if not isinstance(world, dict):
        return [_issue("E_KIND", "error", "world 不是 dict")]
    if world.get("kind") != "world.module":
        issues.append(_issue("E_KIND", "error",
                             "kind 应为 world.module，实际 %r" % (world.get("kind"),)))

    winfo = world.get("world") or {}
    if not winfo.get("key"):
        issues.append(_issue("E_WORLD_KEY", "error", "world.world.key 缺失"))
    if winfo.get("engine") not in ENGINES:
        issues.append(_issue("E_ENGINE", "error",
                             "world.engine=%r 不在 %s" % (winfo.get("engine"), ENGINES)))
    if winfo.get("world_type") not in WORLD_TYPES:
        issues.append(_issue("W_WORLD_TYPE", "warn",
                             "world.world_type 缺失或不认识，读取层落 modern（§2.3 口径 1）"))

    regions = world.get("regions") or []
    seen = set()
    for r in regions:
        rid = r.get("id") if isinstance(r, dict) else None
        if not rid:
            issues.append(_issue("E_REGION_ID", "error", "region 缺 id"))
        elif rid in seen:
            issues.append(_issue("E_REGION_DUP", "error",
                                 "region id 重复：%s" % rid, rid))
        seen.add(rid)

    factions = world.get("factions") or []
    fseen = set()
    for f in factions:
        fid = f.get("id") if isinstance(f, dict) else None
        if not fid:
            issues.append(_issue("E_FACTION_ID", "error", "faction 缺 id"))
        elif fid in fseen:
            issues.append(_issue("E_FACTION_DUP", "error",
                                 "faction id 重复：%s" % fid, fid))
        fseen.add(fid)

    for law in world.get("laws") or []:
        if not isinstance(law, dict):
            issues.append(_issue("E_LAW_SHAPE", "error", "laws 条目不是 dict"))
            continue
        if law.get("threshold") not in ("forbid", "tolerate", "allow"):
            issues.append(_issue("E_LAW_THRESHOLD", "error",
                                 "law %r 的 threshold 不在 forbid/tolerate/allow（neura 契约）"
                                 % (law.get("id"),), law.get("id")))
        if not isinstance(law.get("forbid_tags") or [], list):
            issues.append(_issue("E_LAW_TAGS", "error",
                                 "law %r 的 forbid_tags 不是数组" % (law.get("id"),),
                                 law.get("id")))

    # ── goods 校验（新口径 3/4） ─────────────────────────────────────────
    goods = world.get("goods")
    world_rank = ERA_ORDER.get(winfo.get("world_type"))
    if goods is not None:
        covered = set()
        gids = set()
        for g in goods:
            if not isinstance(g, dict):
                issues.append(_issue("E_GOODS_SHAPE", "error", "goods 条目不是 dict"))
                continue
            gid = g.get("good_id")
            if not gid:
                issues.append(_issue("E_GOODS_SHAPE", "error", "goods 缺 good_id"))
            elif gid in gids:
                issues.append(_issue("E_GOODS_DUP", "error",
                                     "good_id 重复：%s" % gid, gid))
            gids.add(gid)
            for key in ("name", "tier", "base_price", "stock", "produced_by", "era"):
                if key not in g:
                    issues.append(_issue("E_GOODS_SHAPE", "error",
                                         "good %r 缺字段 %s" % (gid, key), gid))
            category = g.get("category")
            if category in GOODS_REQUIRED_CATEGORIES:
                covered.add(category)
            if not isinstance(g.get("produced_by") or [], list):
                issues.append(_issue("E_GOODS_SHAPE", "error",
                                     "good %r 的 produced_by 不是数组" % gid, gid))
            else:
                for pid in g.get("produced_by") or []:
                    if pid and pid not in seen:
                        issues.append(_issue("W_GOODS_PLACE", "warn",
                                             "good %r 的 produced_by 引用不存在的 region %s"
                                             % (gid, pid), pid))
            era = g.get("era")
            if era not in ERA_ORDER:
                issues.append(_issue("E_GOODS_ERA", "error",
                                     "good %r 的 era=%r 不认识" % (gid, era), gid))
            elif world_rank is not None:
                if ERA_ORDER[era] > world_rank:
                    issues.append(_issue("E_GOODS_ERA", "error",
                                         "时代违和：good %r（era=%s）超出世界类型 %s"
                                         % (gid, era, winfo.get("world_type")), gid))
                elif ERA_ORDER[era] < world_rank:
                    issues.append(_issue("W_GOODS_ERA_LOW", "warn",
                                         "good %r 的 era=%s 低于世界类型 %s（老物件，可接受）"
                                         % (gid, era, winfo.get("world_type")), gid))
        for category in GOODS_REQUIRED_CATEGORIES:
            if category not in covered:
                issues.append(_issue(
                    "E_GOODS_COVER", "error",
                    "goods 缺少保底类目「%s」（衣食住行军保底，人拍板 P2）"
                    % CATEGORY_LABELS[category]))
    elif spec is not None and spec.get("economy_on"):
        issues.append(_issue("E_SPEC_ECONOMY", "error",
                             "spec.economy_on=True 但世界没有 goods"))

    # ── C8：spec 兑现核对（warn 级——用户显式改过的字段以 spec 为准） ────
    if spec is not None:
        if regions and len(regions) != spec["regions_req"]["count"]:
            issues.append(_issue("W_SPEC_REGIONS", "warn",
                                 "区域数 %d 与 spec %d 不一致"
                                 % (len(regions), spec["regions_req"]["count"])))
        if factions and len(factions) != spec["factions_req"]["count"]:
            issues.append(_issue("W_SPEC_FACTIONS", "warn",
                                 "势力数 %d 与 spec %d 不一致"
                                 % (len(factions), spec["factions_req"]["count"])))
        if (world.get("laws") is not None
                and len(world.get("laws") or []) != len(spec.get("laws_req") or [])):
            issues.append(_issue("W_SPEC_LAWS", "warn",
                                 "法律条数与 spec.laws_req 不一致"))

    # tone_terms 未命中词表 → warn（§4.2：词表没覆盖的词进 issues，不 error）
    provenance = world.get("worldgen_provenance") or {}
    for term in provenance.get("uncovered_terms") or []:
        issues.append(_issue("W_TONE_UNCOVERED", "warn",
                             "tone_term %r 未命中 atlas.lexicon，落兜底 trait" % term))

    return issues


def has_errors(issues):
    """便捷判定：error 级 issue 是否为空（error 阻断，§4.1 P5 语义）。"""
    return any(i.get("level") == "error" for i in issues)


# ── ATLAS 校验与修复（M12-W2，WORLD-DESIGN.md §5.1 / §5.2） ───────────────
# 校验器消费 atlas.py / atlas_compile.py / atlas_gen.py 的**输出形状**
# （§3.1 的 place / link dict），不是内部实现（§5.3，V8 三文件零改动）；
# worldgen 不 import atlas（W1 的 import 纯净口径不变），图遍历与
# _ensure_link 手法按设计在本地重实现。

#: §5.1 时点表：compile_world 之后跑 C1–C4 / C8（C8 在 validate_world）。
ATLAS_CHECKS_POST_COMPILE = ("C1", "C2", "C3", "C4")
#: §5.1 时点表：generate_site / generate_tactical 之后跑一次 C1–C7。
ATLAS_CHECKS_POST_GENERATE = ("C1", "C2", "C3", "C4", "C5", "C6", "C7")

#: §3.1 地点种类（atlas.KINDS 的数据形状快照，只读比对）。
ATLAS_KINDS = ("region", "fill", "site", "room", "zone", "feature")
#: §5.2 行程档位（atlas.BANDS 快照）。
ATLAS_BANDS = ("short", "medium", "long", "dangerous")
#: 六种场地要素 tag（atlas_gen.SITE_ELEMENT_TAGS 快照）。
SITE_VENUE_TAGS = ("掩体", "危险", "机关", "增幅", "情绪", "机动")
#: via 常量（atlas.VIA_* 数据形状快照）。
_VIA_CARDINAL_DELTA = {"北": (0, 1), "南": (0, -1), "东": (1, 0), "西": (-1, 0)}
_VIA_OPPOSITE = {"北": "南", "南": "北", "东": "西", "西": "东",
                 "上": "下", "下": "上", "门": "门", "连接": "连接"}

#: C6 白名单抽查表（新口径 9）：性状 → 与之矛盾的产物名（精确匹配）。
C6_SPOT_CHECKS = {
    "wilderness": ("铁锭", "聚变电池", "脉冲步枪", "激光枪"),
    "abstract": ("黑麦干粮", "腌菜", "驮马", "罐头食品"),
    "water": ("铁锭", "聚变电池", "脉冲步枪"),
}

_REPAIR_SOURCE = "worldgen:repair"


def _atlas_places(atlas):
    """{(fid, pid): place} 全图地名录；atlas 形状不合法返回 None。"""
    frames = atlas.get("frames")
    if not isinstance(frames, dict):
        return None
    out = {}
    for fid, frame in frames.items():
        places = frame.get("places") if isinstance(frame, dict) else None
        if not isinstance(places, dict):
            continue
        for pid, place in places.items():
            if isinstance(place, dict):
                out[(fid, pid)] = place
    return out


def _coords(place):
    """坐标三元组；缺坐标返回 None（atlas._place_coords 同口径）。"""
    if all(key in place for key in ("x", "y", "z")):
        return (place["x"], place["y"], place["z"])
    return None


def _footprint(place):
    """脚印格（(x+dx, y+dy, z)，w×h 展开，atlas.slice_text 同口径）。"""
    base = _coords(place)
    if base is None:
        return set()
    w = max(1, int(place.get("footprint", {}).get("w", 1)))
    h = max(1, int(place.get("footprint", {}).get("h", 1)))
    return {(base[0] + dx, base[1] + dy, base[2])
            for dx in range(w) for dy in range(h)}


def _in_frame_undirected(frame, pids):
    """帧内无向邻接（跨帧连接不计入，atlas._graph_distances 同口径）。"""
    adj = {pid: set() for pid in pids}
    for pid in pids:
        for link in frame["places"][pid]["links"]:
            to = link["to"]
            if to in adj:
                adj[pid].add(to)
                adj[to].add(pid)
    return adj


def _components(adj):
    """无向图连通分量（确定性：按 id 序扫描）。"""
    seen, comps = set(), []
    for pid in sorted(adj):
        if pid in seen:
            continue
        stack, comp = [pid], set()
        while stack:
            cur = stack.pop()
            if cur in comp:
                continue
            comp.add(cur)
            stack.extend(adj[cur] - comp)
        seen |= comp
        comps.append(comp)
    return comps


def _directed_reachable(adj):
    """有向邻接 {pid: [to...]} 下从 start 出发可达集。"""
    def bfs(start):
        dist = {start}
        frontier = [start]
        while frontier:
            nxt = []
            for pid in frontier:
                for to in adj.get(pid, ()):
                    if to not in dist:
                        dist.add(to)
                        nxt.append(to)
            frontier = nxt
        return dist
    return bfs


def _check_c1(atlas, places, issues):
    """C1 位置唯一性（error）：place id 无重复；同帧内 (x,y,z) 不被两个
    place 占用（footprint 展开）。"""
    seen_ids = {}
    for (fid, pid), place in sorted(places.items()):
        declared = place.get("id")
        if declared != pid:
            issues.append(_issue(
                "C1_DUP_ID", "error",
                "place 的 id 字段 %r 与登记键 %r 不一致" % (declared, pid),
                place_id=pid, frame_id=fid))
        if pid in seen_ids:
            issues.append(_issue(
                "C1_DUP_ID", "error",
                "place id 重复：%s（已在 %s 登记）" % (pid, seen_ids[pid]),
                place_id=pid, frame_id=fid))
        else:
            seen_ids[pid] = fid
    by_frame = {}
    for (fid, pid), place in places.items():
        by_frame.setdefault(fid, []).append((pid, place))
    for fid in sorted(by_frame):
        occupied = {}
        for pid, place in sorted(by_frame[fid]):
            for cell in sorted(_footprint(place)):
                if cell in occupied:
                    issues.append(_issue(
                        "C1_CELL_DUP", "error",
                        "格 %r 被 %s 与 %s 同时占用" % (cell, occupied[cell], pid),
                        place_id=pid, frame_id=fid))
                else:
                    occupied[cell] = pid


def _check_c2(atlas, places, issues):
    """C2 道路连通性（error，新口径 7）：逐帧连接图无向连通；**零坐标
    分量豁免**（unlisted / abstract 的地点与其关键地点都没有坐标，构成
    图外浮岛）；有坐标的分量必须并入本帧主路网。有向 oneway 边不破坏
    「从入口可达所有格」（逐帧）。"""
    frames = atlas.get("frames") or {}
    for fid in sorted(frames):
        frame = frames[fid]
        pids = sorted(frame.get("places") or {})
        if not pids:
            continue
        adj = _in_frame_undirected(frame, set(pids))
        comps = _components(adj)
        placed = [c for c in comps
                  if any(_coords(frame["places"][pid]) is not None
                         for pid in c)]
        if len(placed) > 1:
            placed.sort(key=lambda c: (-len(c), sorted(c)[0]))
            main, fragments = placed[0], placed[1:]
            for frag in fragments:
                sample = sorted(frag)[:4]
                issues.append(_issue(
                    "C2_DISCONNECTED", "error",
                    "道路断连：分量 %s（%d 格）与帧内主路网（%d 格）不通"
                    % (sample, len(frag), len(main)),
                    place_id=sorted(frag)[0], frame_id=fid))
        # 有向 oneway 子句：含 oneway 边的帧，从入口（地点帧为锚门目标）
        # 须可达全帧；无入口的帧须存在能遍历全帧的起点。
        has_oneway = any(frame["places"][pid].get("links") and
                         any(link.get("oneway")
                             for link in frame["places"][pid]["links"])
                         for pid in pids)
        if not has_oneway:
            continue
        dadj = {pid: [link["to"]
                      for link in frame["places"][pid].get("links") or []
                      if link.get("to") in frame["places"]]
                for pid in pids}
        bfs = _directed_reachable(dadj)
        entrance = None
        if "/site/" in fid:
            anchor_id = fid.split("/site/", 1)[1]
            anchor = next((p for (_f, _p), p in places.items()
                           if _p == anchor_id and p.get("kind") in
                           ("region", "site")), None)
            if anchor is not None:
                entrance = next((link["to"]
                                 for link in anchor.get("links") or []
                                 if link.get("via") == "门"
                                 and link.get("to") in frame["places"]), None)
        if entrance is not None:
            if bfs(entrance) != set(pids):
                issues.append(_issue(
                    "C2_ONEWAY", "error",
                    "有向边使入口 %s 无法遍历全帧" % entrance,
                    place_id=entrance, frame_id=fid))
        elif not any(bfs(pid) == set(pids) for pid in pids):
            issues.append(_issue(
                "C2_ONEWAY", "error",
                "有向边使全帧不存在能遍历所有格的起点", frame_id=fid))


def _check_c3(atlas, places, issues):
    """C3 出入口存在（error）：每个 site 帧恰有一个 via=门 连到锚
    （入口须有回程门）；每个区域 place 至少一条连接记录。"""
    frames = atlas.get("frames") or {}
    for fid in sorted(frames):
        if "/site/" not in fid:
            continue
        frame = frames[fid]
        pids = set(frame.get("places") or {})
        if not pids:
            continue
        anchor_id = fid.split("/site/", 1)[1]
        anchor = next((p for (_f, _p), p in places.items()
                       if _p == anchor_id
                       and p.get("kind") in ("region", "site")), None)
        if anchor is None:
            issues.append(_issue(
                "C3_NO_GATE", "error",
                "site 帧 %s 的锚 %s 不存在" % (fid, anchor_id),
                frame_id=fid))
            continue
        gates = [link for link in anchor.get("links") or []
                 if link.get("via") == "门" and link.get("to") in pids]
        if not gates:
            issues.append(_issue(
                "C3_NO_GATE", "error",
                "site 帧没有锚 %s 的「门」出入口" % anchor_id,
                place_id=anchor_id, frame_id=fid))
        elif len(gates) > 1:
            issues.append(_issue(
                "C3_GATE_DUP", "error",
                "site 帧有 %d 条锚门（应恰有一条）" % len(gates),
                place_id=anchor_id, frame_id=fid))
        else:
            entrance = frame["places"][gates[0]["to"]]
            back = any(link.get("via") == "门" and link.get("to") == anchor_id
                       for link in entrance.get("links") or [])
            if not back:
                issues.append(_issue(
                    "C3_NO_BACK", "error",
                    "入口格 %s 缺少回到锚 %s 的门" % (gates[0]["to"], anchor_id),
                    place_id=gates[0]["to"], frame_id=fid))
    for (fid, pid), place in sorted(places.items()):
        if place.get("kind") == "region" and not (place.get("links") or []):
            issues.append(_issue(
                "C3_REGION_EDGE", "error",
                "区域 %s 没有任何连接（区域帧至少一条边）" % pid,
                place_id=pid, frame_id=fid))


def _check_c4(atlas, places, issues):
    """C4 关键地点可达（error）：每个区域的 kind=site 关键地点从该区域
    锚点图距离可达（帧内有向，与 move 语义一致）；discovered 无关。"""
    frames = atlas.get("frames") or {}
    for fid in sorted(frames):
        frame = frames[fid]
        pids = sorted(frame.get("places") or {})
        if not pids:
            continue
        dadj = {pid: [link["to"]
                      for link in frame["places"][pid].get("links") or []
                      if link.get("to") in frame["places"]]
                for pid in pids}
        bfs = _directed_reachable(dadj)
        for rid in pids:
            place = frame["places"][rid]
            if place.get("kind") != "region":
                continue
            kps = [pid for pid in pids
                   if pid.startswith(rid + "/k")
                   and pid[len(rid) + 2:].isdigit()
                   and frame["places"][pid].get("kind") == "site"]
            if not kps:
                continue
            reach = bfs(rid)
            for kp in kps:
                if kp not in reach:
                    issues.append(_issue(
                        "C4_KP_UNREACHABLE", "error",
                        "关键地点 %s 从区域锚点 %s 图距离不可达" % (kp, rid),
                        place_id=kp, frame_id=fid))


def _check_c5(atlas, places, world, issues):
    """C5 建筑功能（warn）：kind 合法；site 帧 room 的 venue 行（若带
    feature）来自 random_tables 池（新口径 8：世界无场地行时跳过）。"""
    pool = set()
    if world is not None:
        for table in world.get("random_tables") or []:
            if not isinstance(table, dict):
                continue
            for row in table.get("rows") or []:
                if isinstance(row, dict) and row.get("tag") in SITE_VENUE_TAGS:
                    pool.add((row["tag"], row.get("result", "")))
    for (fid, pid), place in sorted(places.items()):
        if place.get("kind") not in ATLAS_KINDS:
            issues.append(_issue(
                "C5_KIND", "warn",
                "place kind=%r 不在合法种类 %s" % (place.get("kind"), ATLAS_KINDS),
                place_id=pid, frame_id=fid))
        if not pool or "/site/" not in fid:
            continue
        if place.get("kind") != "room":
            continue
        for feature in place.get("features") or []:
            pair = (feature.get("tag"), feature.get("text"))
            if pair not in pool:
                issues.append(_issue(
                    "C5_VENUE", "warn",
                    "feature %r 不在当前世界 random_tables 场地行池" % (pair,),
                    place_id=pid, frame_id=fid))


def _check_c6(atlas, places, world, issues):
    """C6 资源合理性（warn）：经济开档（world 带 goods）时，region 的
    产出声明与 traits 不矛盾（白名单抽查，新口径 9）。"""
    if world is None or not world.get("goods"):
        return
    traits_by_rid = {}
    for (fid, pid), place in places.items():
        if place.get("kind") == "region":
            traits_by_rid[pid] = set(place.get("traits") or [])
            _, _, short = pid.rpartition("/")
            traits_by_rid.setdefault(short, set(place.get("traits") or []))

    def report(good_name, rid, traits):
        for trait, names in C6_SPOT_CHECKS.items():
            if trait in traits and good_name in names:
                issues.append(_issue(
                    "C6_GOODS_TRAIT", "warn",
                    "产出 %r 与区域 %s 的性状 %r 矛盾（白名单抽查）"
                    % (good_name, rid, trait), place_id=rid))

    for good in world.get("goods") or []:
        if not isinstance(good, dict):
            continue
        for rid in good.get("produced_by") or []:
            traits = traits_by_rid.get(rid)
            if traits is not None:
                report(good.get("name"), rid, traits)
    # §6.6 regions[].economy 产出声明（W8 转正前的可选字段）
    for region in world.get("regions") or []:
        if not isinstance(region, dict):
            continue
        econ = region.get("economy")
        produces = econ.get("produces") if isinstance(econ, dict) else \
            (econ if isinstance(econ, list) else [])
        traits = traits_by_rid.get(region.get("id"))
        if traits is None:
            continue
        for name in produces or []:
            if isinstance(name, str):
                report(name, region.get("id"), traits)


def _check_c7(atlas, places, issues):
    """C7 移动成本一致（error）：band 四档取值合法；同一无向边两端的
    band 一致；metric 地点/战术帧上 cardinal via 两端正交相邻、方向与
    via 一致且带档只能是 short（新口径 10，zone_steps 反算）。"""
    frames = atlas.get("frames") or {}
    for fid in sorted(frames):
        frame = frames[fid]
        is_cell_frame = ("/site/" in fid or "/tactical/" in fid)
        metric = frame.get("space") == "metric"
        for pid in sorted(frame.get("places") or {}):
            place = frame["places"][pid]
            for link in place.get("links") or []:
                band = link.get("band")
                if band is not None and band not in ATLAS_BANDS:
                    issues.append(_issue(
                        "C7_BAND", "error",
                        "band=%r 不在四档 %s" % (band, ATLAS_BANDS),
                        place_id=pid, frame_id=fid))
                other = frame["places"].get(link.get("to"))
                if other is None:
                    continue
                via = link.get("via")
                opposite = _VIA_OPPOSITE.get(via)
                for rec in other.get("links") or []:
                    if rec.get("to") == pid and rec.get("via") == opposite \
                            and rec.get("band") != band:
                        issues.append(_issue(
                            "C7_BAND_MISMATCH", "error",
                            "连接 %s↔%s 两端 band 不一致（%r vs %r）"
                            % (pid, link["to"], band, rec.get("band")),
                            place_id=pid, frame_id=fid))
                if not (is_cell_frame and metric and via in _VIA_CARDINAL_DELTA):
                    continue
                here, there = _coords(place), _coords(other)
                if here is None or there is None:
                    continue
                expect = _VIA_CARDINAL_DELTA[via]
                delta = (there[0] - here[0], there[1] - here[1])
                if delta != expect:
                    issues.append(_issue(
                        "C7_GEOMETRY", "error",
                        "via=%r 的连接两端不满足方向与相邻（Δ=%r，应 %r）"
                        % (via, delta, expect),
                        place_id=pid, frame_id=fid))
                elif band not in (None, "short"):
                    issues.append(_issue(
                        "C7_GEOMETRY", "error",
                        "相邻一格（1 跨区）上 band=%r 与 zone_steps 折算矛盾"
                        "（应为 short 或无档）" % (band,),
                        place_id=pid, frame_id=fid))


def validate_atlas(atlas, *, world=None, checks=None):
    """ATLAS 校验器（§5.1 C1–C7）：产出 {code, level, place_id?, frame_id?, hint}。

    error 阻断、warn 放行。atlas 是 compile_world / generate_site /
    generate_tactical 的产物（只读，不改不毁）；world 是 world.module
    （只读它的 goods / random_tables / regions，供 C5/C6）。checks 缺省
    跑全量 C1–C7；compile_world 之后调用方按 §5.1 时点表传
    ATLAS_CHECKS_POST_COMPILE（C1–C4，C8 由 validate_world 承担）。
    """
    checks = tuple(checks) if checks else ATLAS_CHECKS_POST_GENERATE
    issues = []
    places = _atlas_places(atlas)
    if places is None:
        return [_issue("E_ATLAS_SHAPE", "error",
                       "atlas 缺少 frames 结构，无法校验")]
    run = set(checks)
    if "C1" in run:
        _check_c1(atlas, places, issues)
    if "C2" in run:
        _check_c2(atlas, places, issues)
    if "C3" in run:
        _check_c3(atlas, places, issues)
    if "C4" in run:
        _check_c4(atlas, places, issues)
    if "C5" in run:
        _check_c5(atlas, places, world, issues)
    if "C6" in run:
        _check_c6(atlas, places, world, issues)
    if "C7" in run:
        _check_c7(atlas, places, issues)
    return issues


# ── §5.2 局部修复（不是全图重掷） ─────────────────────────────────────────

def _find_place(places, pid):
    """按 id 在全图找地点（atlas.find_place 同口径，消费地名录）。"""
    for (fid, key), place in places.items():
        if key == pid:
            return place
    return None


def _repair_link_record(to, via, band=None):
    """修出来的连接记录：形状与 atlas._link_norm 一致 + source 标注
    （provenance：修复过的内容可识别来源）。"""
    return {"to": to, "via": via, "band": band, "oneway": False,
            "unstable": False, "source": _REPAIR_SOURCE}


def _add_repair_link(places, a_id, b_id, via, band=None):
    """补一条双边连接（atlas.add_link 语义 + atlas_gen._ensure_link 手法
    的重实现，不 import 私有函数；不覆盖已有连接，只新增）。"""
    pa = _find_place(places, a_id)
    pb = _find_place(places, b_id)
    if pa is None or pb is None or a_id == b_id:
        return False
    if any(link.get("to") == b_id and link.get("via") == via
           for link in pa.get("links") or []):
        return False
    pa.setdefault("links", []).append(_repair_link_record(b_id, via, band))
    pb.setdefault("links", []).append(
        _repair_link_record(a_id, _VIA_OPPOSITE.get(via, via), band))
    return True


def _repair_via_band(pa, pb, frame):
    """修边的 via / band：两格正交相邻取基本方向（band=short）；否则
    「连接」（band 按曼哈顿距离取档，新口径 7 不查非 cardinal 边）；
    缺坐标 → 「连接」+ 无档。"""
    ca, cb = _coords(pa), _coords(pb)
    if ca is None or cb is None:
        return "连接", None
    dx, dy = cb[0] - ca[0], cb[1] - ca[1]
    for via, (ex, ey) in _VIA_CARDINAL_DELTA.items():
        if (dx, dy) == (ex, ey):
            return via, "short"
    distance = abs(dx) + abs(dy)
    band = "short" if distance <= 2 else ("medium" if distance <= 5 else "long")
    if frame is not None and frame.get("space") == "abstract":
        band = None
    return "连接", band


def _repair_c2(atlas, places, repaired):
    """C2 断链：在帧内断连分量与主路网间补一条边（最近格对，确定性；
    与校验同口径：只处理有坐标分量，零坐标浮岛豁免）。"""
    frames = atlas.get("frames") or {}
    for _ in range(16):
        fixed_one = False
        for fid in sorted(frames):
            frame = frames[fid]
            pids = sorted(frame.get("places") or {})
            if not pids:
                continue
            adj = _in_frame_undirected(frame, set(pids))
            comps = _components(adj)
            placed = [c for c in comps
                      if any(_coords(frame["places"][pid]) is not None
                             for pid in c)]
            if len(placed) <= 1:
                continue
            placed.sort(key=lambda c: (-len(c), sorted(c)[0]))
            main, frag = placed[0], placed[1]
            best = None
            for f_pid in sorted(frag):
                for m_pid in sorted(main):
                    ca = _coords(frame["places"][f_pid])
                    cb = _coords(frame["places"][m_pid])
                    d = (abs(ca[0] - cb[0]) + abs(ca[1] - cb[1])
                         if ca is not None and cb is not None else 10 ** 9)
                    cand = (d, f_pid, m_pid)
                    if best is None or cand < best:
                        best = cand
            _d, f_pid, m_pid = best
            via, band = _repair_via_band(frame["places"][f_pid],
                                         frame["places"][m_pid], frame)
            if _add_repair_link(places, f_pid, m_pid, via, band):
                repaired.append({"code": "C2", "frame_id": fid,
                                 "action": "补边", "endpoints": [f_pid, m_pid],
                                 "via": via, "band": band})
            fixed_one = True
            break
        if not fixed_one:
            return


def _repair_c3(atlas, places, issues, repaired):
    """C3 缺门：从锚到帧内最靠入口位（坐标序 (1,1,0) 最先）的格补
    via=门。只修缺门；多门违反「只新增边」原则，不修。"""
    frames = atlas.get("frames") or {}
    for issue in issues:
        if issue.get("code") != "C3_NO_GATE" or not issue.get("frame_id"):
            continue
        fid = issue["frame_id"]
        frame = frames.get(fid)
        if frame is None or not (frame.get("places") or {}):
            continue
        anchor_id = fid.split("/site/", 1)[1]
        anchor = _find_place(places, anchor_id)
        if anchor is None:
            continue
        candidates = sorted(frame["places"],
                            key=lambda pid: (_coords(frame["places"][pid])
                                             or (10 ** 9,) * 3, pid))
        target_id = candidates[0]
        if _add_repair_link(places, anchor_id, target_id, "门"):
            repaired.append({"code": "C3", "frame_id": fid, "action": "补门",
                             "endpoints": [anchor_id, target_id],
                             "via": "门", "band": None})


def _repair_c4(atlas, places, issues, repaired):
    """C4 不可达关键地点：把它挂到同区域最近已连通格（kp 无坐标时
    优先挂回区域锚点——编译器本来的结构；有坐标按曼哈顿最近）。"""
    frames = atlas.get("frames") or {}
    for issue in issues:
        if issue.get("code") != "C4_KP_UNREACHABLE" or not issue.get("place_id"):
            continue
        kp_id = issue["place_id"]
        region_id = kp_id.rpartition("/k")[0]
        kp = _find_place(places, kp_id)
        region = _find_place(places, region_id)
        if kp is None or region is None or region.get("kind") != "region":
            continue
        fid = kp.get("frame_id")
        frame = frames.get(fid)
        # 区域锚点在本帧内的可达集（有向，与校验同口径）
        pids = sorted(frame.get("places") or {}) if frame else []
        dadj = {pid: [link["to"]
                      for link in frame["places"][pid].get("links") or []
                      if link.get("to") in frame["places"]]
                for pid in pids}
        bfs = _directed_reachable(dadj)
        if region_id not in pids:
            continue
        reach = bfs(region_id)
        ckp = _coords(kp)
        best = None
        for r_id in sorted(reach):
            r_place = frame["places"][r_id]
            cr = _coords(r_place)
            if ckp is not None and cr is not None:
                d = abs(ckp[0] - cr[0]) + abs(ckp[1] - cr[1])
                rank = (d, 0, r_id)
            else:
                # 无坐标：优先区域锚点本身，其余按 id 字典序近似「最近」
                rank = (0 if r_id == region_id else 1, 1, r_id)
            if best is None or rank < best:
                best = rank
        if best is None:
            continue
        target_id = best[2]
        if _add_repair_link(places, kp_id, target_id, "门"):
            repaired.append({"code": "C4", "frame_id": fid, "action": "挂接",
                             "endpoints": [kp_id, target_id],
                             "via": "门", "band": None})


def repair(atlas, issues, *, rng):
    """§5.2 局部修复：只处理三类 error（C2 断链 / C3 缺门 / C4 不可达
    关键地点），只新增边、不删除不移动。返回 {atlas, repaired, provenance}：
    atlas 就地修改；repaired 是修复动作清单；provenance 记录修了什么、
    几次、哪些 error 不可修（超出三类的如实保留给人工改 spec）。
    rng 预留给 W3 流水线的「该帧整体重掷」，本函数确定性执行（新口径 11）。
    """
    del rng  # 预留：重掷编排归生成流水线（§4.1 P5）
    places = _atlas_places(atlas)
    if places is None:
        return {"atlas": atlas, "repaired": [],
                "provenance": {"repairs": [], "count": 0,
                               "unrepaired": ["E_ATLAS_SHAPE"]}}
    repaired = []
    codes = {i.get("code") for i in issues if i.get("level") == "error"}
    if any(code and code.startswith("C2") for code in codes):
        _repair_c2(atlas, places, repaired)
    if "C3_NO_GATE" in codes:
        _repair_c3(atlas, places, issues, repaired)
    if "C4_KP_UNREACHABLE" in codes:
        _repair_c4(atlas, places, issues, repaired)
    fixed = {item["code"] for item in repaired}
    unrepaired = sorted(code for code in codes
                        if not any(code.startswith(f) for f in fixed))
    provenance = {"repairs": repaired, "count": len(repaired),
                  "unrepaired": unrepaired}
    return {"atlas": atlas, "repaired": repaired, "provenance": provenance}


def atlas_provenance(atlas):
    """来源标注汇总（§5.2 / GDD §16.5 可解释性）：places 按来源计数，
    links 统计 repair 来源条数。"""
    places = _atlas_places(atlas) or {}
    by_source = {"authored": 0, "generated": 0, "other": 0}
    repaired_links = 0
    for (_fid, _pid), place in places.items():
        source = place.get("source")
        if source in by_source:
            by_source[source] += 1
        else:
            by_source["other"] += 1
        for link in place.get("links") or []:
            if link.get("source") == _REPAIR_SOURCE:
                repaired_links += 1
    return {"places": by_source, "repaired_links": repaired_links}
