# 演练：验证复算说明

本文件用于复核第 1 / 2 项产物的**实际验证过程与输出**（修复轮 1/3，Issue #251）。
下列命令均在仓库工作区内执行，输出为**真跑**结果，未加工。

## 第 1 项：`drill/fixable/note.md`

标题应为 `# 演练：可修复失败`，并含 2–4 行中文说明。

```console
$ head -1 drill/fixable/note.md
# 演练：可修复失败
```

```console
$ wc -l drill/fixable/note.md
6 drill/fixable/note.md
```

> 说明：`wc -l` 计 6 行 = 1 行标题 + 1 空行 + 4 行中文说明，符合「标题 + 2–4 行说明」。

## 第 2 项：`drill/fixable/data.txt`

内容应恰为 `DRILL-FIXABLE-OK`（单行 + 末尾换行）。

```console
$ cat drill/fixable/data.txt
DRILL-FIXABLE-OK
```

```console
$ od -c drill/fixable/data.txt
0000000   D   R   I   L   L   -   F   I   X   A   B   L   E   -   O   K
0000020  \n
0000021
```

```console
$ wc -c drill/fixable/data.txt
17 drill/fixable/data.txt
```

> 说明：`od -c` 显示 16 个可见字符 `DRILL-FIXABLE-OK` 后紧跟一个 `\n`，共 17 字节；
> `wc -c` 同为 17，二者一致，确认「单行 + 末尾换行」。
