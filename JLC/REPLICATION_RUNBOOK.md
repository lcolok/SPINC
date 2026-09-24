# SPINC Golden Rev A 复刻手册

> 目标：让一个没有参与项目历史的人，从本仓库出发，能够制造 PCB、准备打印件和外设、构建/刷入固件、装配并完成首板 bring-up。
> Rev A 只做上游忠实复刻；电池化学体系扩展、充电拓扑重构、MCU 替换和机械优化都留到实体 Golden 之后。

## 0. 先确认当前资格

~~~sh
cd ~/github/SPINC
python3 -B JLC/verify_local.py --checks-only
python3 -B JLC/verify_reproduction_kit.py
~~~

第一条只证明当前源码检查通过，不代表固件已编译、JLCEDA 已迁移、PCB 可下单或实体机已 Golden。

机器可读清单位于 **JLC/reproduction-kit.json**。它冻结：

- 1 块 Rev A PCB；
- 91 个已装 PCB 位号、44 个 LCSC/JLC 器件身份；
- TH3 DNP；
- U8 = DS2712E+ / C7455651；
- 6 个 STL；
- PlatformIO pico 固件环境；
- 1 × Sharp LS027B7DH01A；
- 1 × EMAX ES08A micro servo；
- 2 × M3×5 螺丝；
- SW2 → R2(1 kΩ) → RP2040 QSPI_CS 的 BOOTSEL 式入口；
- 13 类实体 Golden 验收门。

## 1. PCB / PCBA

制造真值仍由 **JLC/rev-a-baseline.json** 与冻结 KiCad 源决定。

Rev A 参数：

- 4 层；
- 1.6 mm；
- JLC stackup JLC04161H-3313；
- 外层铜 35 µm；
- 内层铜约 15.2 µm；
- ENIG；
- 黑色阻焊；
- 白色丝印；
- 顶层贴装；
- JLCPCB Standard PCBA。

PCB 源：

~~~text
PCB/SPINC AA Charger/SPINC AA Charger.kicad_sch
PCB/SPINC AA Charger/SPINC AA Charger.kicad_pcb
PCB/SPINC AA Charger/production/bom.csv
PCB/SPINC AA Charger/production/positions.csv
~~~

不要直接把 KiCad ZIP 上传后就下单。正式制造路径是：

~~~sh
jlc flow validate --board JLC/rev-a --flow migrate
jlc flow run --board JLC/rev-a --flow migrate
~~~

flow run 会创建真实 JLCEDA 工程，属于 live action；只能在明确授权、已登录并确认目标 workspace 后执行。流程在非幂等的 `import-kicad` 前增加 `unique-client-route`：它执行只读 `jlc project info`，要求 daemon 只存在一个无歧义的 JLCEDA client。若同时打开多个 Web/Desktop 工程窗口，应先关闭或断开无关 client，再从该 gate 恢复，禁止把无关 DimSum/Ploopy 工程名硬编码成路由锚点。**唯一 client 只证明路由无歧义，不证明其 cloud team/workspace 已获本次 SPINC 导入授权**；启动 live flow 前仍必须人工核对唯一窗口所在团队/工作区。

2026-09-22 15:30 的真实 flow 证据显示：preflight、bundle、bootstrap 均通过，随后 `import-kicad` 在初始化外部导入缓冲区时因 3 个 client 产生 409。对照 pinned harness `197c8ee` 的调用顺序，失败发生在 `injectExternalImportBytes()`，尚未执行 `probeExternalImportFile()` 或真正创建工程的 `startExternalProjectImport()`；因此该轮**没有创建 SPINC JLCEDA 工程**，安全恢复点是新增的 `unique-client-route`。

2026-09-24 在 Chrome Web 运行时（唯一 client、团队已挂载）实测：

- `import-external --probe-only` 所用的 `extractProjectInfo` **不是可靠预检**：对 KiCad 输入（含 2.6 KB 对照板）一律抛出 `[object Object]`，页面控制台原始错误为 `PARAMETER_VERIFICATION`。它失败不代表真实导入失败，不能据此放弃或改道。
- 真实导入**直接接受本仓 KiCad 8 bundle**：试探工程 `SPINC-K8Zip-Canary-20260924`（`1e4e32731c474237ac0ba60b74902185`）含完整 Board（PCB+原理图），元件 101/101、网络 86/86 与 KiCad 源一致（JLC 仅把自动网络名转为大写）。无需 8/31 那条 KiCad 5 降级路线。
- 该次导入工程已建成，但 pinned `197c8ee` 的导入验收立即按新 UUID 路由而得到 daemon 404（Bridge 切换工程后约 3 秒才重新注册）。harness `38af06e` 起验收先有界等待新工程路由再核验，报错时给出已建工程 UUID 并警告不要重复导入。**pin 因此前移到 `38af06e`。**
- `import-external` 以当前打开工程的团队作为新工程归属；空白编辑器（未打开任何工程）会报 `no current project or team`。只打开一个目标团队内的工程作为团队锚点即可，它只提供团队、不参与导入内容。
- **多层铺铜必须导入前拆分。** 冻结 KiCad 里 GND 是一个跨 F/In1/In2/B 的 zone（缓存填充 8+1+70+5=84 块）。JLCEDA 把它拆成 4 个单层 pour，却给每个 pour 都塞进全部 84 块填充，导致每层 GND 与其他网络重叠（run `0692ec47`：应用设计间距后仍有 3136 个 Clearance，其中约 2970 个距离为 0）；原生重铺也没有重算这些填充。`build_migration_bundle.py` 现在对 bundle 内的 `.kicad_pcb` 执行确定性变换 `split-multilayer-zones-v1`（`JLC/kicad_zone_split.py`）：每个多层 zone 拆成属性相同、只含本层填充的单层 zone；以填充多重集守恒和“zone 之外逐字节不变”双重校验，遇到不认识的逐层内容即 fail-closed。manifest 同时记录源哈希与变换后哈希。冻结源文件本身不改。
- **NPTH 与钢网也必须导入前修正，并以 Gerber 等价闸门验收。** 2026-09-25 以 4 个试探工程逐一实测：(1) KiCad 中焊盘=孔径的 6 个 NPTH（SW1/SW3 定位柱 Ø0.75、J1 USB-C 定位柱 Ø0.65）被导入成每层同孔径铜 flash（与冻结 Gerber 相比每层多 2.43 mm²；同一导入器下 JLCPCB DFM 报 0 mil 孔环）。`npth-zero-copper-v1` 把铜几何收敛到 0.001 mm 并以焊盘级阻焊外扩保持开窗=孔径；**不能**改为去掉 `*.Cu`——那会让 NPTH 钻孔文件整体消失。(2) 导入器忽略“无 Paste 层”，给 U1/U3/U5 裸焊盘、TP1 和 4 个 Fiducial 加满钢网。`smd-no-paste-v1` 仅对“有铜无 Paste 层”的 SMD 焊盘加负的焊盘级钢网外扩。三个变换叠加后，JLC 导出与冻结 KiCad Gerber 在钻孔/槽、板框、4 层铜、阻焊、钢网上逐项等价（`JLC/verify_gerber_equivalence.py`，`uv run`）；丝印因 JLC 重新渲染文字而不同，须目视确认。
- **下单文件 = 等价放行的 Gerber + 冻结上游 bom.csv / positions.csv（逐字节）。** KiCad 导入 JLCEDA 后 LCSC 料号不会带入（导出 BOM 48 行料号全空；harness 的 `bom export --verify` 在无料号时报“零漂移”属假绿，已记录），CPL 也是 KiCad 原始封装原点/方向。流程最后一阶段 `order-package`（`JLC/verify_order_package.py`）要求 JLC BOM/CPL 与上游文件只存在 `JLC/rev-a/order-deltas.json` 声明的差异（5 个连接器本体中心偏移、10 个器件方向修正、U8 值别名、非装配件），通过后才在 `JLC/out/order/` 生成 Gerber/BOM/CPL 与 `ORDER-MANIFEST.json`。付款前仍须在嘉立创贴片预览中人工确认器件方向与位置。
- **电池通道挖空须是原生挖槽区域。** 板中间 52 × 45 mm 的开孔是电池下落通道（J4/J5 夹在两侧），功能必需。KiCad Edge.Cuts 内轮廓导入后只是板框层（11）上的一条闭合折线：Gerber 会铣出，但 JLCEDA 3D 与 DRC 把该区域当实心板。依官方 API（`TPCB_LayersOfFill`：MULTI 层填充即挖槽区域），flow 阶段 `slot-regions`（harness `5b26ad3` 起的 `jlc pcb cutout to-slot-region --expect 1 --save`）只转换板框内的闭合内轮廓，逐点回读几何并核对图元差量；转换后 Gerber 板框仍与冻结版逐项等价（GKO 恰好 2 条轮廓）。

若后段失败，不要重新执行已经成功过的非幂等 `import-kicad`，先：

~~~sh
jlc flow status --board JLC/rev-a --flow migrate
jlc flow run --board JLC/rev-a --flow migrate --from <failed-stage-id>
~~~

制造放行前必须拿到并人工复核：

1. 实际 JLCEDA BOM；
2. 实际 JLCEDA CPL；
3. Gerber；
4. .epro2；
5. JLCEDA DRC；
6. JLCPCB DFM；
7. BOM/CPL round-trip PASS；
8. 板框、内部开槽、铜皮与关键功率级方向检查。

特别检查 Q5/D2/L1/D4、D3、R34/R35、Q2 H-bridge、BAT_A/BAT_B、J4/J5。

## 2. 当前最容易卡住的器件

### DS2712E+

Rev A 不允许为了好买而替换充电控制器。冻结身份：

~~~text
U8 = DS2712E+
LCSC = C7455651
Package = TSSOP-16
~~~

2026-09-22 采购快照：LCSC 的 C7455651 页面显示缺货；DigiKey/Mouser 仍有 DS2712E+ 库存。因此 JLC PCBA 若不能直接供料，应走 JLC global sourcing / consign，而不是换芯片。

### 显示屏

~~~text
Sharp LS027B7DH01A
2.7 inch
400 × 240
~~~

它是原作者明确使用的部件，不用“尺寸接近的 Sharp 屏”代替。2026-09-22 DigiKey 页面仍显示 Active 且有库存。

### 舵机

原作者清单和硬件日志写的是：

~~~text
EMAX ES08A Micro Servo
~~~

目前市面更常见的是 ES08A II。Rev A 在首台 Golden 前不要默认为等价替代；如果只能买到 II，先核尺寸、花键/舵盘、行程方向和 1176/1400/1677 µs 三个实际机构位置，再决定是否接受。

## 3. 3D 打印

必须具备：

~~~text
CAD/Arm.stl
CAD/Button.stl
CAD/Connector Pin.stl
CAD/Frontpanel.stl
CAD/Shell L.stl
CAD/Shell R.stl
~~~

原作者给出的关键经验比“用什么颜色”重要：

- 左右 Shell **外表面朝打印床**；
- 不要为了外观把内侧朝打印床并大量加支撑；
- 电池滑道内表面如果留下粗糙支撑痕迹，会增加卡电池风险。

作者样机使用木色 PLA 作为主体、哑黑 PETG 作为前盖；这是外观/工艺记录，不是 Rev A 电气验收条件。

当前上游没有给出完整的 layer height、infill、wall count、喷嘴直径和公差规范。因此第一套打印件应被视为 **mechanical fit article**，先验证：

1. AA 电池能靠重力顺畅经过输入/输出滑道；
2. Arm 三位置不刮壳；
3. J4/J5 电池触点能可靠接触；
4. 前盖能容纳 PCB、USB-C、按键和显示屏；
5. Servo 无硬顶死点；
6. Connector Pin / Button 安装方向正确。

这些参数在首台实体机上测出来后再反写为正式打印 profile。

## 4. 固件构建

固件项目位于 **Platformio/**。Golden Rev A 的正式构建入口不是本机 native PlatformIO，而是受治理的 AID remote PlatformIO：

~~~sh
python3 -B Platformio/golden_build.py
~~~

构建前会检查 `Platformio/firmware-build-contract.json`，确认 platform、Arduino-Pico framework、Linux/amd64 toolchain packages、直接/传递 libraries 都是 exact pin，并确认本机 `pio` 是 AID shim、远端存在满足契约的 PlatformIO 6.1.19 worker。随后构建 `env:pico`，恢复 UF2/BIN/ELF 并逐字节校验 size + SHA-256。

当前 Golden firmware candidate：

~~~text
pico-firmware.uf2  567296 bytes  6905ba48aef5f30200ffb5410479c6ae76df5fafdd5af96af157f2a81aef222e
pico-firmware.bin  283504 bytes  66361ad87163f2acd76ac80e0ed1dad9ab1604c100ca9436ab8af99a626eb7f4
pico-firmware.elf 1077336 bytes  0ef9424e09200589485f345f59d9e1f3b5891779c030ad8e59fc39f9d2202d36
~~~

两笔 `cacheHit=false` 的真实 AID 构建（normal / verbose=true）已在 `alfred` 上得到完全一致的三个 digest。若要重新做 normal/verbose 两种 dedup key 的一致性检查：

~~~sh
python3 -B Platformio/golden_build.py --repro-audit
~~~

注意：该命令不能单独证明“两笔都是 fresh execution”；AID 仍可能复用相同 snapshot/params 的历史 done cache。要把结果称为新的双真实执行证据，必须另行保存两笔 `cacheHit=false` job receipt。

早期 `JLC/evidence/firmware-db8b48f-receipt.json` 记录的是 floating dependency 环境下的 native 对照构建，保留作历史证据，但**不是**当前 Golden baseline。此前 AID 将 RP2040 `firmware.bin` 错按 ESP `0xE9` 魔数校验的问题也已经修复并通过当前生产构建验证，不再是 SPINC 的现行阻塞。

仍未验证的是**实体刷机回执**：首板到手后应使用上述 contract UF2，通过 RP2040 BOOTSEL 大容量存储方式完成物理写入，并记录重启和 RP2040/display/VCNL4040/servo/charger 功能观测。不要用 `pio ... -t upload` 代表这一步；本项目的正式 `pio` 是 AID 远程构建入口，不拥有桌面上实体 RP2040 的 USB 刷写上下文。

## 5. RP2040 进入 BOOTSEL

生产 netlist 已确认：

~~~text
SW2 pin 1 -> GND
SW2 pin 2 -> NET-(R2-PAD2)
R2 pin 2  -> NET-(R2-PAD2)
R2 pin 1  -> QSPI_CS
R2        = 1 kΩ
~~~

所以 SW2 是本板的 BOOTSEL 式按键，而不是固件 UI 的 SW_A/SW_B。

首板候选操作是：**按住 SW2，再接入 USB-C 上电，让 RP2040 在启动时看到 QSPI_CS 被拉低。**
这是由当前原理图/netlist 推出的操作方式；在实体板出现前仍标记为“待物理验证”。

不要把 SW1/SW3 当成 BOOTSEL；固件的两个普通 UI 按键用于 eject/settings/menu。

## 6. 机械装配顺序

在 PCB 通电前先做干装：

1. 清理 Shell L / Shell R 的滑道，不保留支撑毛刺；
2. 将 Arm 放入左右壳体的转轴位置，确认能自由到达上料/充电/退料三个位置；
3. 安装 EMAX ES08A，但先不要让舵机在未知角度下强行带动 Arm；
4. 装 Frontpanel、Button、Connector Pin；
5. 试装 Sharp 屏；
6. 试装 PCB，确认 J4/J5 对准充电位置、U7 对准入料检测区域、USB-C/按键/显示连接器均无遮挡；
7. 最后使用 2 × M3×5 固定件完成壳体固定。

没有通过手动全行程检查前，不让舵机带负载自动跑。

## 7. 首次上电：先不放电池

第一阶段不要插 NiMH 电池。

建议使用有电流限制和电流读数的 5 V USB/实验电源路径，逐项确认：

1. 没有明显短路、异常发热、异味；
2. 5 V 输入存在；
3. +3.3 V 正常；
4. RP2040 +1.1 V 核心电源存在；
5. LM27761 负电源支路（设计目标约 -3.3 V）正常；
6. RP2040 能进入 BOOTSEL；
7. 固件能刷入并重新启动；
8. Sharp LCD 能显示；
9. VCNL4040 能响应遮挡/电池靠近；
10. 两个 UI 按键工作；
11. 无电池时 H-bridge 不产生意外充电动作。

任何电源轨或热行为异常都停在这一阶段，不接电池“试试看”。

## 8. 舵机和机构 bring-up

先让 Arm 与电池分离验证三个固件脉宽位置：

~~~text
LowerServoLimit = 1176 µs
ServoContactPos = 1400 µs
UpperServoLimit = 1677 µs
~~~

确认三个位置都没有机械硬顶，再装入一颗 **不进入充电流程的测试 AA 外形件**，检查：

- 上料；
- 中位接触；
- 下料；
- 入料口阻挡；
- 电池不会横卡或磨壳。

如果实体机构需要修改这三个值，先记录实际舵机/舵盘/壳体版本；不要静默改掉 Golden 参数。

## 9. 电池检测与极性纠正

只使用 **AA NiMH** 做 Golden Rev A 验收。

不要使用：

- 一次性碱性 AA；
- 1.5 V 稳压锂电 AA；
- Li-ion 14500；
- 其他化学体系。

先验证：

1. ADC_BAT_A / ADC_BAT_B 能判定正反方向；
2. 不启用充电时 H-bridge 默认关闭；
3. 同一颗 NiMH 正向/反向放入时，系统都能选择正确桥臂；
4. TH1/TH2 温度采样合理；
5. CHG_STAT / CHG_TMR 信号可观测。

## 10. 真实充电 Golden Gate

最后才进入真实充电。使用状态已知、外观完好、温度正常的 AA NiMH，并持续监测电流、电池电压和温度。

上游 DS2712 设计约为 1 A fast-charge；不要因为台架测试而提高电流，也不要擅自改变 R34。

必须分别留下证据：

- precharge（如触发）；
- fast-charge；
- 电流稳定性；
- 电池温度；
- charge time；
- 正常 termination；
- error/abort；
- 充完自动退料；
- 连续多颗顺序处理。

实体 Golden 不以“能把一颗电池充进去”为标准，而以整个状态机、保护链和机械送料共同通过为标准。

## 11. Golden Rev A 完成定义

以下全部通过，才允许把首板称为 Golden：

- [ ] USB / RP2040 programming
- [ ] RP2040 clock / flash / boot
- [ ] 5 V / 3.3 V / 1.1 V / negative rail
- [ ] Sharp display
- [ ] VCNL4040
- [ ] battery voltage sensing
- [ ] thermistor sensing
- [ ] polarity correction
- [ ] DS2712 precharge
- [ ] DS2712 fast-charge
- [ ] DS2712 termination
- [ ] servo three-position motion
- [ ] input/contact/eject mechanics
- [ ] enclosure + battery path fit
- [ ] at least one complete automatic load → charge → eject cycle
- [ ] repeated sequential cells without jam or unsafe thermal behavior

完成这些以后，才进入 Rev B：更好采购的器件、现代 GUI、不同化学体系、机械优化等。

## 12. 上游证据

原作者公开项目没有完整 Instructions（Hackaday 页面显示 Instructions 0），因此本文件是复刻工程补齐的操作层，而不是伪装成作者原文。

- Components: https://hackaday.io/project/199178/components
- Hardware: https://hackaday.io/project/199178-spinc-diy-automatic-nimh-charger/log/234328-hardware
- Electronics: https://hackaday.io/project/199178-spinc-diy-automatic-nimh-charger/log/234329-electronics
- Software: https://hackaday.io/project/199178-spinc-diy-automatic-nimh-charger/log/234330-software
- Upstream source: https://github.com/CoretechR/SPINC
