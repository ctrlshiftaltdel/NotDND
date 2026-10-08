# NotDND

一个**完全原创、可商用**的 AI 跑团网页应用。规则系统为「**棱镜 PRISM**」
（MIT，PRISM Authors），AI 担任**导引者**（Conductor），纯文本交互，无图片资源；
面向单机 / 局域网「多人轮流共用一台设备」的桌边场景。

> ⚠️ **仅限本机 / 局域网使用。** 后端默认无账号系统、可监听 `0.0.0.0`，
> **请勿暴露到公网。**

## 状态

项目正在初始化（M0）。规则与剧本源文档见 [`docs/`](docs/)（只读）。

## 测试

零依赖回归，只用 Python 3 标准库：

```bash
bash tests/run_all.sh          # 串行跑 tests/ 下全部测试，全过返回 0
bash tests/content_firewall.sh # 内容防火墙：禁止路径 / 品牌标识 / 密钥 / 隐私
```

`tests/run_all.sh` 会逐个打印通过 / 失败行并汇总，有失败即返回非零。
CI 另设语法门（`python3 -m py_compile`）与上述内容防火墙门，见 `.github/workflows/`。

## 许可

代码与规则内容均以 **MIT** 发布；许可与署名见 [`LICENSE`](LICENSE) 与 [`NOTICE`](NOTICE)。

## 协作

开工前请先读 [`AGENTS.md`](AGENTS.md)（角色分工、三条铁律、Issue 循环、审查格式）。
