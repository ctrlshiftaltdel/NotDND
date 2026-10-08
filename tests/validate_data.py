#!/usr/bin/env python3
"""零依赖 JSON Schema 子集校验器（NotDND 数据层）。

只实现 `data/FORMAT.md` 第 7 节列出的**文档化关键字子集**；
出现未支持的关键字即报错。零第三方依赖，仅 Python 3 标准库。

引擎接口（供 tests/test_data.py 与后续树级逻辑使用）：
    SUPPORTED_KEYWORDS / ANNOTATION_KEYWORDS
    collect_unsupported(schema) -> [(json-path, keyword)]
    check_schema(instance, schema, base_dir, path="$", cache=None) -> [error]
    load_json(path) -> object
"""
import argparse
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

# 其值是「子 schema」的关键字（用于递归扫描）。
_SCHEMA_MAP_KEYWORDS = ("properties", "$defs")
_SCHEMA_ONE_KEYWORDS = ("additionalProperties", "items")
_SCHEMA_LIST_KEYWORDS = ("anyOf", "oneOf")


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
            errors.append("%s: 类型应为 %s，实为 %s"
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
                errors.append("%s: 缺少必填字段 '%s'" % (path, name))
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="NotDND 数据层校验器（关键字子集；树级扫描见后续任务）")
    parser.parse_args(argv)
    # 树级扫描（data/ 分派、唯一 id、source 检查）在后续任务接入。
    return 0


if __name__ == "__main__":
    sys.exit(main())
