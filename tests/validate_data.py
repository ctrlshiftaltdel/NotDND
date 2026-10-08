#!/usr/bin/env python3
"""零依赖 JSON Schema 子集校验器（NotDND 数据层）。

只实现 `data/FORMAT.md` 第 7 节列出的**文档化关键字子集**；
出现未支持的关键字即报错。零第三方依赖，仅 Python 3 标准库。

引擎接口（供 tests/test_data.py 使用）：
    SUPPORTED_KEYWORDS / ANNOTATION_KEYWORDS
    collect_unsupported(schema) -> [(json-path, keyword)]
    check_schema(instance, schema, base_dir, path="$", cache=None) -> [error]
    load_json(path) -> object
    validate_file(path, schema_dir, root) -> [error]
    validate_tree(data_dir, schema_dir, root) -> (检查文件数, [error])

命令行：
    python3 tests/validate_data.py [--data-dir data] [--schema-dir data/schema] [--root .]
"""
import argparse
import glob
import json
import os
import re
import sys

# 文档化关键字子集（与 data/FORMAT.md 第 7 节逐字一致）。
SUPPORTED_KEYWORDS = frozenset({
    "type", "required", "properties", "additionalProperties", "items",
    "enum", "const", "pattern", "minLength", "minimum", "maximum",
    "uniqueItems", "$ref", "$defs", "anyOf", "oneOf",
})
# 仅允许的注解关键字（不做校验）。
ANNOTATION_KEYWORDS = frozenset({"$schema", "$id", "title", "description"})
_ALLOWED_KEYWORDS = SUPPORTED_KEYWORDS | ANNOTATION_KEYWORDS
_SCHEMA_MAP_KEYWORDS = ("properties", "$defs")
_SCHEMA_ONE_KEYWORDS = ("additionalProperties", "items")
_SCHEMA_LIST_KEYWORDS = ("anyOf", "oneOf")

# `source` 允许的形式：docs/<system|scenario>/<文件>.md:<行号>
_SOURCE_RE = re.compile(r"^docs/(system|scenario)/[^:]+\.md:[0-9]+$")

_LINE_COUNT_CACHE = {}


def load_json(path):
    """读取 UTF-8 JSON 文件。"""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _json_equal(first, second):
    """按 JSON 规范化比较，避免 Python 里 True == 1。"""
    return (json.dumps(first, sort_keys=True, ensure_ascii=False)
            == json.dumps(second, sort_keys=True, ensure_ascii=False))


def collect_unsupported(schema, path="$"):
    """返回 schema 中未支持的关键字：(json-path, 关键字) 列表。

    `properties` / `$defs` 的**名称键**是任意字符串，不算关键字。
    """
    problems = []
    if not isinstance(schema, dict):
        return problems
    for key, value in schema.items():
        if key not in _ALLOWED_KEYWORDS:
            problems.append((path, key))
            continue
        if key in _SCHEMA_MAP_KEYWORDS and isinstance(value, dict):
            for sub in value.values():
                problems.extend(collect_unsupported(sub, "%s.%s" % (path, key)))
        elif key in _SCHEMA_ONE_KEYWORDS and isinstance(value, dict):
            problems.extend(collect_unsupported(value, "%s.%s" % (path, key)))
        elif key in _SCHEMA_LIST_KEYWORDS and isinstance(value, list):
            for index, sub in enumerate(value):
                problems.extend(collect_unsupported(sub, "%s.%s[%d]" % (path, key, index)))
    return problems


def _is_type(instance, name):
    if name == "object":
        return isinstance(instance, dict)
    if name == "array":
        return isinstance(instance, list)
    if name == "string":
        return isinstance(instance, str)
    if name == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if name == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if name == "boolean":
        return isinstance(instance, bool)
    if name == "null":
        return instance is None
    return False


def _type_name(instance):
    if isinstance(instance, bool):
        return "boolean"
    if isinstance(instance, int):
        return "integer"
    if isinstance(instance, float):
        return "number"
    if isinstance(instance, str):
        return "string"
    if isinstance(instance, list):
        return "array"
    if isinstance(instance, dict):
        return "object"
    if instance is None:
        return "null"
    return type(instance).__name__


def _load_doc(path, cache):
    key = os.path.normpath(path)
    if key not in cache:
        cache[key] = load_json(key)
    return cache[key]


def _resolve_ref(ref, root, base_dir, cache):
    """解析 `#/$defs/x` 或 `<file>#/$defs/x`，返回 (子 schema, 其所属根文档)。"""
    file_part, sep, fragment = ref.partition("#")
    if not sep or not fragment.startswith("/"):
        raise ValueError("不支持的 $ref 形式: %s" % ref)
    if file_part:
        target_root = _load_doc(os.path.join(base_dir, file_part), cache)
    else:
        target_root = root
    node = target_root
    for token in fragment.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        node = node[token]
    return node, target_root


def _check(instance, schema, root, base_dir, path, cache):
    errors = []
    if schema is True:
        return errors
    if schema is False:
        return ["%s: 不允许任何值（schema 为 false）" % path]
    if not isinstance(schema, dict):
        return ["%s: 子 schema 不是对象" % path]

    if "$ref" in schema:
        node, target_root = _resolve_ref(schema["$ref"], root, base_dir, cache)
        return _check(instance, node, target_root, base_dir, path, cache)

    if "type" in schema:
        types = schema["type"]
        if isinstance(types, str):
            types = [types]
        if not any(_is_type(instance, name) for name in types):
            errors.append("%s: 类型（type）应为 %s，实为 %s"
                          % (path, "/".join(types), _type_name(instance)))

    if "enum" in schema and not any(_json_equal(instance, v) for v in schema["enum"]):
        errors.append("%s: 取值不在 enum 中: %r" % (path, instance))
    if "const" in schema and not _json_equal(instance, schema["const"]):
        errors.append("%s: 取值不等于 const %r: %r" % (path, schema["const"], instance))

    if isinstance(instance, str):
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append("%s: 不匹配 pattern %s: %r" % (path, schema["pattern"], instance))
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: 长度不足 minLength %d" % (path, schema["minLength"]))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("%s: 小于 minimum %s" % (path, schema["minimum"]))
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("%s: 大于 maximum %s" % (path, schema["maximum"]))

    if isinstance(instance, list):
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(_check(item, schema["items"], root, base_dir,
                                     "%s[%d]" % (path, index), cache))
        if schema.get("uniqueItems"):
            seen = []
            for index, item in enumerate(instance):
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if key in seen:
                    errors.append("%s[%d]: 与前面的元素重复（uniqueItems）" % (path, index))
                else:
                    seen.append(key)

    if isinstance(instance, dict):
        for name in schema.get("required", []):
            if name not in instance:
                errors.append("%s: 缺少必填字段 '%s'（required）" % (path, name))
        properties = schema.get("properties", {})
        for name, sub in properties.items():
            if name in instance:
                errors.extend(_check(instance[name], sub, root, base_dir,
                                     "%s.%s" % (path, name), cache))
        additional = schema.get("additionalProperties", True)
        extras = [name for name in instance if name not in properties]
        if additional is False:
            for name in extras:
                errors.append("%s: 多余的键 '%s'（additionalProperties: false）" % (path, name))
        elif isinstance(additional, dict):
            for name in extras:
                errors.extend(_check(instance[name], additional, root, base_dir,
                                     "%s.%s" % (path, name), cache))

    if "anyOf" in schema:
        if not any(not _check(instance, sub, root, base_dir, path, cache)
                   for sub in schema["anyOf"]):
            errors.append("%s: 不满足 anyOf 的任何一支" % path)
    if "oneOf" in schema:
        matched = sum(1 for sub in schema["oneOf"]
                      if not _check(instance, sub, root, base_dir, path, cache))
        if matched != 1:
            errors.append("%s: oneOf 需要恰好匹配 1 支，实际 %d" % (path, matched))

    return errors


def check_schema(instance, schema, base_dir, path="$", cache=None):
    """用 schema 校验 instance，返回错误列表（空列表 = 通过）。"""
    if cache is None:
        cache = {}
    return _check(instance, schema, schema, base_dir, path, cache)


# ── 树级校验 ────────────────────────────────────────────────────────────

def _load_registry(schema_dir):
    """读取 registry.json，返回 kind → schema 文件名映射。"""
    return load_json(os.path.join(schema_dir, "registry.json"))["kinds"]


def _iter_sources(node, json_path="$"):
    """递归产出 (json-path, source 值)。"""
    if isinstance(node, dict):
        for key, value in node.items():
            child = "%s.%s" % (json_path, key)
            if key == "source" and isinstance(value, str):
                yield child, value
            else:
                yield from _iter_sources(value, child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _iter_sources(item, "%s[%d]" % (json_path, index))


def _file_line_count(path):
    """文件总行数；文件不存在返回 None。带缓存。"""
    if path not in _LINE_COUNT_CACHE:
        try:
            with open(path, encoding="utf-8") as handle:
                _LINE_COUNT_CACHE[path] = sum(1 for _ in handle)
        except OSError:
            _LINE_COUNT_CACHE[path] = None
    return _LINE_COUNT_CACHE[path]


def _check_unique_ids(instance):
    """同一顶层数组内，字符串 `id` 不得重复。"""
    errors = []
    for key, value in instance.items():
        if not isinstance(value, list):
            continue
        seen = set()
        for index, item in enumerate(value):
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                ident = item["id"]
                if ident in seen:
                    errors.append("$.%s[%d]: 重复 id '%s'" % (key, index, ident))
                else:
                    seen.add(ident)
    return errors


def _check_sources(instance, root):
    """`source` 指向的文件必须存在，行号不得超出文件行数。"""
    errors = []
    for json_path, source in _iter_sources(instance):
        if not _SOURCE_RE.match(source):
            continue  # 格式错误由 schema 的 pattern 负责
        file_part, _, line_part = source.rpartition(":")
        line_no = int(line_part)
        total = _file_line_count(os.path.join(root, file_part))
        if total is None:
            errors.append("%s: source 指向的文件不存在: %s" % (json_path, source))
        elif line_no > total:
            errors.append("%s: source 行号超出文件行数（共 %d 行）: %s"
                          % (json_path, total, source))
    return errors


def validate_file(path, schema_dir, root):
    """校验单个数据文件；返回错误列表。"""
    try:
        instance = load_json(path)
    except json.JSONDecodeError as exc:
        return ["%s: JSON 解析失败: %s" % (path, exc)]
    if not isinstance(instance, dict):
        return ["%s: 顶层必须是对象（kind + 载荷）" % path]
    kind = instance.get("kind")
    if not isinstance(kind, str):
        return ["%s: 缺少 kind（required）" % path]
    registry = _load_registry(schema_dir)
    if kind not in registry:
        return ["%s: 未知 kind: %s" % (path, kind)]

    schema_path = os.path.join(schema_dir, registry[kind])
    schema = load_json(schema_path)
    errors = []
    for where, keyword in collect_unsupported(schema):
        errors.append("%s: schema %s %s 使用了未支持的关键字 %s"
                      % (path, os.path.basename(schema_path), where, keyword))
    errors.extend(check_schema(instance, schema, schema_dir))
    errors.extend(_check_unique_ids(instance))
    errors.extend(_check_sources(instance, root))
    return errors


def validate_tree(data_dir, schema_dir, root):
    """扫描 data_dir 下所有数据文件（排除 schema_dir），返回 (文件数, 错误列表)。"""
    schema_abs = os.path.abspath(schema_dir)
    files = []
    for path in glob.glob(os.path.join(data_dir, "**", "*.json"), recursive=True):
        abs_path = os.path.abspath(path)
        if abs_path == schema_abs or abs_path.startswith(schema_abs + os.sep):
            continue
        files.append(path)
    errors = []
    for path in sorted(files):
        errors.extend(validate_file(path, schema_dir, root))
    return len(files), errors


def main(argv=None):
    parser = argparse.ArgumentParser(description="NotDND 数据层校验器（关键字子集）")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--schema-dir", default="data/schema")
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)

    checked, errors = validate_tree(args.data_dir, args.schema_dir, args.root)
    if checked == 0:
        print("未发现数据文件，跳过校验。")
        return 0
    if errors:
        for error in errors:
            print("  ✗ %s" % error)
        print("校验失败：%d 个文件，%d 处错误" % (checked, len(errors)))
        return 1
    print("校验通过：%d 个文件。" % checked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
