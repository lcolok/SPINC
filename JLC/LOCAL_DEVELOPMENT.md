# SPINC 本机开发与验证入口

> 本指南整理于 2026-09-08，并于 2026-09-23 补充 Golden firmware contract 合流。路径与版本是实测快照，不代替下一次任务的 Git 状态检查。

## 第二轮治理后的默认入口

```sh
# 开发机离线检查；允许未提交改动，但记录其身份，不把它们归属于 HEAD。
python3 -B JLC/verify_local.py --checks-only

# 显式运行受治理的 Golden Rev A firmware remote build；这不是离线检查的一部分。
python3 -B Platformio/golden_build.py

# 只读查询 GitHub 当前 PR、精确源工作流、secret 名称及 main 保护。
python3 -B JLC/verify_repository_readiness.py --pr 1
```

远端检查退出码：`0` = 已检查的自动门禁通过，但仍需人工批准；`1` = 已确认阻塞；`2` = 权限/接口/数据不完整导致未知。API 的普通 404/403 不会被当成“保护不存在”；活跃 ruleset 不能直接推断为缺失或充分。此入口不创建 secret、不取出令牌、不修改设置、不启动 CI、不合并。

它核对当前 PR head/base 与本机和 main，读取本 PR 最新工作流的具体 attempt，要求三个指定 job 全部成功，并检查 required check 是否绑定实际观察到的 GitHub Actions App。读取过程中 PR/main/重跑状态变化会拒绝混用证据。结果保存到 `JLC/out/repository-readiness/<唯一运行目录>/`。

本轮实测仍阻塞：reader secret 缺失、main 没有保护或活跃 ruleset、SPINC 自身两个门禁失败、PR 仍 Draft、本机有未发布改动。配套 JLC PR #2 的四项 CI 成功不能代替 SPINC 的 required check。

## 1. 仓库在哪里

| 目录（默认位于 `~/github`，以 Devspace 实际 allowed root 为准） | 角色 | 本次处理 |
| --- | --- | --- |
| `SPINC/` | 自有 `lcolok/SPINC` 的开发检出 | 本次新增，检出 `jlc-rev-a`，HEAD `db8b48f3488f4cfff0af913c96e79570a5bce809` |
| `SPINC-upstream/` | 原作者 `CoretechR/SPINC` 参考源 | 原样保留；`main` / `af7b36e8ca5e99bfb3e99d8b02d9864117091de7` |
| `_worktrees/jlc-spinc-20260831/` | 旧 JLC harness 实验工作树 | detached `31f934efcc653b2135eff1dbb2c3994bd37a5539`，10 项 tracked 改动、6 项 untracked 条目，全部保留 |
| `_worktrees/spinc-pinned-harness-197c8ee/` | 固定 loader 的隔离检出 | 本次新增，只读核验；`197c8eeb18c4f84d49062ca2087a67fe79315c0c` |

脚本内部以文件位置定位仓库，不依赖某个固定物理卷路径，也不要求当前 shell 必须在根目录。

之前的主线治理并非完全没有落码：GitHub 已有 `91a2aee`、`db8b48f` 两笔 9 月 8 日的治理提交和 Draft PR #1。本次补的是本机明确的自有检出、统一运行入口、回归测试和知识交接，不把这些远端既有成果记为本次新实现。

## 2. 每次开发先看状态

```sh
cd ~/github/SPINC
git remote -v
git status --short --branch
git rev-parse HEAD
```

先重建 tlens/skldr 上下文，再读取当前实现。当前 binary 以 `tlens sessions --last 30d` 代替已移除的 `tlens timeline`。精确历史检索无结果时记录缺口，不补写未经证实的历史。

## 3. 一个命令验证当前工作树

```sh
python3 -B JLC/verify_local.py --checks-only
```

此模式执行复刻基线、功率级等价约束、器件绑定清单、整机 reproduction kit、静态 firmware build contract、harness pin 合约、冻结 BOM/CPL 自测、JLC + firmware Python 回归测试及三份 JLCEDA 脚本的语法检查。它不会运行 AID remote firmware build，也不会运行 JavaScript 的导入或导出动作。

返回零且报告为 `checks-passed`，只表示这些工作树检查通过。零测试、跳过测试、审计失败、缺失程序及超时不能称为完整通过。

## 4. 验证可复现的迁移包

```sh
python3 -B JLC/verify_local.py
```

完整模式在上述检查之后，运行两次已有的迁移包生成器，验证 ZIP 字节一致、CRC、manifest 完整性、每个文件的字节数及 SHA-256，并核对实际源 commit。当前冻结包包含 30 个 KiCad 载荷文件，另有一个 manifest。

生成器要求 tracked 源文件相对 HEAD 干净。如果修改了已跟踪文件，使用 `--checks-only` 做开发验证；需要正式打包时由维护者审查并明确授权提交后，再验证新的 SHA。禁止自动 stash/reset/commit 或放宽限制。

本机工具与文档尚未提交；第二轮已修改 tracked 的打包器与测试，因此当前完整模式应拒绝打包，不能沿用第一轮 tracked 干净时的放行结论。开发验证使用 `--checks-only`。新版打包器另在隔离的干净 db8b48f 输入上实测复现了原迁移包，不代表新版代码已进入 db8b48f。

打包器同时检查暂存区和工作区，逐文件比较实际 Git blob，不允许 assume-unchanged 隐藏载荷变化；输出不得覆盖源目录、已跟踪文件、Git 元数据或链接别名。ZIP 先写入同目录临时文件，CRC 与源状态复核后原子替换。写入失败保留旧产物，但旧 ZIP 存在不等于本轮成功，必须检查退出码和对应运行记录。

本机状态摘要新增工作区与暂存区 diff 哈希，以检测 `git status` 字符串没有改变、实际内容却已变化的情况。

## 5. 证据在哪里

每次运行会打印新的目录，例如：

```text
JLC/out/local-verification/20260908T143149Z-foyinzfm/
  summary.json
  SHA256SUMS
  tests.log
  rev-a.log
  power-stage.log
  roundtrip.json
  ...
  bundle-first.zip    # 完整模式
  bundle-repeat.zip   # 完整模式
```

`summary.json` 包含源 commit/tree、运行前后 Git 状态、验证输入 SHA-256（JLC/Platformio Python 验证器以及 reproduction/firmware contracts）、每步退出码、时长、检查/测试数量和验证边界。每次使用新目录；运行中断的记录不能继承上一轮 PASS。日志留在本机被忽略的 `JLC/out/` 下，不自动推送。

可在具体证据目录中校验：

```sh
shasum -a 256 -c SHA256SUMS
```

本机证据及 skldr 交接都不能替代远端永久保管的最终制品，也不能代替 GitHub required check。

## 6. 固定 loader 的独立只读验证

> 2026-09-25 起 pin 为 harness `f6d03bd34c9a6e53b573d069efab0b095ffceb4f`（此前 `5b26ad3`、`38af06e`；新增 `jlc pcb epro2-repair-kicad-import`）。源码 CI 不再读取私有仓，而是按 `harness-pin.json` 的 `binary.sha256` 下载本仓 release `harness-jlc-f6d03bd34c9a` 中的可复现 `linux/amd64` 二进制（构建见 `MAIN_ADMIN_HANDOVER.md` §1）。下文 `197c8ee` 内容为历史记录。

普通本机入口只验证 pin 的数据契约，不自动取私有仓库或构建 loader。以下命令是在已经核实来源、完整 commit 和干净状态的隔离 worktree 中执行的：

```sh
SPINC_ROOT="${SPINC_ROOT:-$HOME/github/SPINC}"
HARNESS_ROOT="${HARNESS_ROOT:-$HOME/github/_worktrees/spinc-pinned-harness-197c8ee}"
cd "$HARNESS_ROOT/jlc"
git rev-parse HEAD
git status --short
# 先确认实际 HEAD 与 "$SPINC_ROOT/JLC/harness-pin.json" 一致。
go build -o "$SPINC_ROOT/JLC/out/jlc-pinned-197c8ee" .
"$SPINC_ROOT/JLC/out/jlc-pinned-197c8ee" flow validate \
  --board "$SPINC_ROOT/JLC/rev-a" --flow migrate
```

2026-09-23 在固定 `197c8ee` loader 上重新实测 `flow validate`。加入多 client 防护前为 3 个 preflight / 11 个 stages；当前 flow 新增 `unique-client-route`，应解析为 3 个 preflight / 12 个 stages。它只解析流程，不执行这些阶段，不写流程状态库。不要把 `flow validate` 换成真实 `flow run`。

该机器的 `go` 是受控执行入口；不要根据入口 `go version` 推断交付二进制实际使用的编译器版本。固定 harness 构建后应同时检查工作树仍干净，并以 `go version -m <binary>` 读取产物的嵌入构建版本。本轮该 pinned loader 实测仍为 Go 1.25.0；入口显示的版本可以随运行环境升级而变化。

## 7. 当前未完成项不是再写一遍工具

SPINC 的 PR #1 仍为 Draft。已核验的工作流运行 `34223213368` 中，复刻源检查通过；私有仓库授权步骤失败，`validate-pinned-flow` 和汇总 `production-source-gate` 保持失败。

需要维护者按 `MAIN_ADMIN_HANDOVER.md` §1 构建并发布固定 harness 的可复现二进制、回填 `binary.sha256`（不再需要任何跨仓 token），以及处理实际 main 保护、最终候选 CI、配套消费者 PR 审查和持久制品归档。此次不创建/修改 secret、不修改保护规则、不重跑远程工作流、不合并。

源治理通过不等于制造放行，更不等于实物 Golden。真实 JLCEDA 导入、库/封装关联、ERC/DRC、Gerber/DFM、实际 BOM/CPL 往返以及实物充电/机构验收仍是独立边界。旧实验工作树已与配套候选 c8e53fc 逐文件对账：10 个文件不同，另外 6 个文件在候选中不存在；均原样保留。内容不同不等于全部缺失的功能，也不等于可安全删除，不应直接挪进生产候选。

## 8. 旧 JLC 工作树的独立审查候选（R3）

2026-09-08 已新增私有隔离工作树：

```text
~/github/_worktrees/jlc-spinc-review-20260908
```

它以配套候选 `c8e53fc457719690e102036dd060802b13efd753` 为基底，承接旧工作树 13 个文件，并修复 DRC 声明预检、Other Spacing 写入／保存回执、Gerber 区域解析及导入工程身份核验。新增 3 个 Go 测试文件。整个 `jlc/cmd` 包实测 878 项顶层测试通过、0 失败、0 跳过，`go vet ./cmd` 和独立构建通过。这些改动仍未提交，不能把基底 SHA 当作包含改动的新提交。

定位点删除／填充替换代码、对应测试和板级声明共 3 个文件没有进入该候选，仍在旧工作树保留，等待更严格的几何／元件身份／恢复机制审查。原工作树 16 个文件收尾 SHA-256 全部不变。

私有实现报告留在对应私有工作树；跨会话恢复应使用 skldr 中的 SPINC 归档，而不是把会话 handoff 提交进本公共仓库。不要把私有工具代码复制到本公共仓库。

候选二进制在私有工作树 `tmp/spinc-review-r3/jlc-review`；它仅被用于只读 `flow validate`，没有替换本项目的固定 `197c8ee` loader，没有修改 harness pin、执行 live flow 或合并 PR。测试覆盖命令包，不等于全仓 `./...` 回归或真实 EDA／制造验收。
