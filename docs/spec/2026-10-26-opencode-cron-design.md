# OpenCode Cron — 定时任务插件系统 设计 Spec

- **日期**:2026-10-26
- **状态**:草案,待评审
- **仓库定位**:面向公开的零 PII 仓库(无绝对路径、无个人信息、无机器细节);真实任务定义放本地 overlay,不入库

---

## 1. 背景与目标

### 1.1 要解决的问题

为一组 AI coding agent CLI(opencode / kimi / pi)与普通脚本提供统一的定时任务系统:

- 按天/周/分钟等频次自动执行任务
- 每个任务由作者提供对应的 script / prompt / skill 内容
- 可管理:查看、启停、手动触发、看日志
- 任务定义可跨机器、跨 agent 共享(git 分发)

### 1.2 目标

| # | 目标 | 验收口径 |
|---|---|---|
| G1 | 机器重启/睡眠后调度不丢 | miss 策略生效(coalesce 补跑一次) |
| G2 | 三 agent 执行器 + 脚本执行器可插拔 | 同一任务改 `executor` 字段即切换 |
| G3 | 文件为唯一真源 | 删掉一切进程,任务定义与运行历史仍在 |
| G4 | 零常驻 | 进程列表里没有任何时刻存在"cron 守护进程" |
| G5 | 跨机器可移植 | clone + 本地配置 + sync 三步即用 |
| G6 | 仓库可公开 | 入库内容零绝对路径、零个人信息 |

### 1.3 非目标(明确不做)

- 进程内调度器(APScheduler 类方案——宿主不开就不跑,已被前作证伪)
- 多机编排/远程调度(单机自治,靠 git 同步任务定义)
- 插件内做外发通知(钉钉/邮件等渠道由任务自身调用,插件只做本地 toast)
- MVP 不做多步编排(schema 预留 steps,实现单步)
- 不承诺拦截 LLM 运行期行为(安全模型见 §10,如实分层)

---

## 2. 总体架构

```
┌──────────────────────────────┐
│ Windows 任务计划程序(schtasks) │  每任务一条条目(cron-<id>),
│  按任务自身频次唤醒             │  miss 策略映射原生 StartWhenAvailable
└──────┬───────────────────────┘
       │ 唤醒即执行
       ▼
┌──────────────────────────────┐      ┌─────────────────────────┐
│ Runner(Python,无状态进程)     │      │ OpenCode 插件(薄 TS)    │
│  runner run <id>              │◀────▶│  /cron list|status|run  │
│  runner sync(对账 schtasks)   │ 读文件 │  /cron toggle|log       │
│  runner list / validate       │ 手动触发└─────────────────────────┘
└──────┬───────────────────────┘
       │ 统一适配接口(纯函数:任务定义→命令行)
       ▼
┌─────────┬──────────────┬─────────────┬─────────────┐
│ script  │ opencode     │ kimi        │ pi          │
│ 直跑命令 │ opencode run │ kimi -p     │ pi -p       │
└─────────┴──────────────┴─────────────┴─────────────┘

文件契约(唯一真相):
  tasks.d/*.yaml + tasks.d.local/*.yaml   任务定义(overlay 覆盖)
  config.yaml + config.local.yaml         配置与变量
  runs/<task-id>/<timestamp>/             运行历史
  state/                                  活跃注册表、锁
  skills/<name>/                          任务专用技能
```

**核心原则**:插件与 runner 零进程耦合,只共享文件契约。没有 OpenCode 的机器也能纯靠 runner + schtasks 运行任务。

---

## 3. 触发与调度

### 3.1 注册模型

- **每个 enabled 任务对应一条 schtasks 条目**,命名 `cron-<id>`,按任务自身频次唤醒(没有全局轮询 tick)
- `runner sync` 幂等对账:tasks.d(+local) 中 enabled 任务 ↔ schtasks 条目,差异增/改/删;任务 disable 或删除 → 移除条目
- 分钟级任务建议最小间隔 5 分钟(防风暴)

### 3.2 cron → schtasks 翻译

| schedule | schtasks 触发器 |
|---|---|
| `M H * * *` | DAILY HH:MM |
| `M H * * 0-6`(周界) | WEEKLY 指定星期 HH:MM |
| `M H D * *` | MONTHLY 指定日 HH:MM |
| `M/N * * * *` / `*/N` | 分钟级重复(相位锚定方案在 M1 实现时验证:优先 DAILY + 重复间隔方案,若 /DU 时长上限不符则退化为 MINUTE /MO N 并接受注册时刻相位) |
| 不可表达(如 `1,15` 多日、月末、DOM∧DOW OR 语义) | **回退**:共享一条小时级检查条目 `cron-fallback-hourly`,runner 对该任务启用 croniter due 判定(游标记 state/) |

### 3.3 miss 策略映射

| miss_policy | 行为 | 实现 |
|---|---|---|
| coalesce(默认) | 错过多次只补跑一次 | schtasks `StartWhenAvailable=true`(开机后尽快补一次) |
| skip | 过期即跳过 | `StartWhenAvailable=false` |
| catchup | 错几次补几次 | M3:StartWhenAvailable + runner 侧槽位重放(上限 3) |

---

## 4. 任务 schema

文件:`tasks.d/<id>.yaml`(入库示例)或 `tasks.d.local/<id>.yaml`(真实任务,不入库)。文件名 = id。

```yaml
id: repo-update            # = 文件名,唯一
name: 仓库例行更新
enabled: true
schedule: "0 20 * * *"     # 5 字段 cron,机器本地时区
miss_policy: coalesce      # coalesce | skip | catchup
executor: opencode         # script | opencode | kimi | pi
permission: auto           # default | yolo | auto(仅 agent 执行器)
model: provider/model      # 可选,传给执行器
timeout: 1800              # 秒,默认 1800
weight: 1                  # 并发权重,默认 1
workdir: ${WORKSPACE}      # 执行目录
notify: [toast]            # 失败本地通知,MVP 仅 toast
steps:                     # schema 为列表,MVP 实现单步
  - name: update
    type: prompt           # script | prompt | skill
    run: |
      更新 ${WORKSPACE} 下的仓库,汇总变更写入 ${RUN_DIR}/report.md
    # skill: report-writer # type: skill 时指定,解析到 ${CRON_HOME}/skills/<name>/
    # output: ${RUN_DIR}/report.md   # 可选产物声明
```

规则:

- **overlay 语义**:`tasks.d.local/` 同 id 任务**整体替换** `tasks.d/` 定义
- **变量插值**:`${CRON_HOME}` `${WORKSPACE}` `${PYTHON}` `${RUN_DIR}` + config 自定义变量;prompt 正文与路径字段都插值;引用未定义变量 → 启动即报错(fail fast)
- `type: skill` = prompt + 对应 CLI 的技能装载方式(见 §7)
- schema 校验只在 runner(唯一权威);`runner validate` 全量检查并给出可读错误

---

## 5. Runner 协议

CLI:`run <id>` | `sync` | `list` | `validate`

### 5.1 `run <id>` 生命周期(schtasks 唤醒与手动触发同路径)

1. 加载 config(+local)与任务定义;插值、校验
2. **per-task 锁**:acquire-or-skip(§6);抢到才继续
3. **全局并发闸**:活跃权重和 + 本任务 `weight` > `max_concurrent` → 记 skipped 退出(活跃权重 = 活跃注册表中各登记任务的 weight 之和)
4. 在 `state/active/` 登记(内容:pid、task_id、boot-id、started_at)
5. 适配器构造命令 → subprocess(timeout、cwd=workdir、注入变量 env)
6. stdout/stderr 流式写 `runs/<id>/<timestamp>/stdout.log|stderr.log`
7. 收割写 `meta.json`:exit_code、duration、model、executor、missed、started_at
8. 失败(非零退出/超时)→ 按 `notify` 发本地 toast
9. 注销活跃登记;退出(无任何常驻状态)

### 5.2 `sync` 生命周期

1. 枚举 enabled 任务 → 期望条目集(名、触发器、StartWhenAvailable、工作目录)
2. 查询现存 `cron-*` 条目 → 差异:创建/更新/删除
3. 输出对账报告;幂等(重复执行无副作用)

---

## 6. 锁与异常处理(死锁对策)

**设计立场:全系统不存在"等待锁",runner 永不阻塞等锁——因此不存在经典死锁;需要处理的只有"陈锁回收"。**

### 6.1 无等待锁

所有锁语义为 acquire-or-skip:抢不到立即放弃并记录 skipped。没有持有-等待环,结构上排除死锁。

### 6.2 per-task 锁(`state/locks/<id>.lock`)

内容 JSON:`{pid, boot_id, started_at, task_id}`

- 获取:独占创建;已存在 → 走 6.3 陈锁判定
- 用途:防止同任务重叠(调度触发与手动 `/cron run` 并发时)

### 6.3 陈锁三判据(任一命中即接管)

1. **pid 不存活**(查询进程存活)
2. **boot_id 变化**(系统重启过 → 防 PID 复用误判"存活")
3. **锁龄 > 任务 timeout + 宽限**(如 timeout + 10 min,兜底一切判据失效)

接管方式:**unlink 陈锁 + `O_EXCL` 独占重建(有界重试)**——并发多 stealer 恰有一个重建成功,败者重新分类后看到新主人存活 → 拒绝(acquired=False, reason=alive);独占创建后**读回验证**持锁身份(pid/boot_id/started_at 三者匹配才算持有),防 create→write 间隙被 corrupt-stealer 顶替;尝试耗尽 → 按竞争拒绝。锁分类阶段任何异常/字段类型异常一律归 `corrupt`,API 永不抛异常。

### 6.4 全局并发闸 = 活跃注册表(非信号量)

- `state/active/<uuid>.json`,内容同锁
- 准入 = 统计"pid 存活且 boot_id 一致"的登记数
- 每次启动先清扫死 pid 的陈旧登记 → 注册表自愈,无死锁可能

### 6.5 子进程与超时

- 每个执行器 subprocess 挂 timeout;超时 `taskkill /T /F`(整树强杀)
- runner 自身被外部杀死可能留下孤儿 CLI 进程:活跃登记下次被清扫,孤儿进程治理为 M3 可选观察项(独立轻量清理,只杀"父进程已死"的登记残留)

---

## 7. 执行器适配层

适配器是纯函数:任务定义 + 解析后变量 → 命令行数组 + env。不做进程管理(那是 runner 的事)。

| executor | 命令模板 | permission 映射 | 输出解析 |
|---|---|---|---|
| script | `${PYTHON} script.py args` 或任务声明的原始命令 | — | exit code |
| opencode | `opencode run --format json [--title cron-<id>] [-m <model>] [flag] "<prompt>"` | auto/yolo → `--auto`;default → 无 flag(交互等待将超时失败,仅调试用) | json(`--format json`) |
| kimi | `kimi -p "<prompt>" [--output-format text] [-m <model>] [flag]` | auto → `--auto`;yolo → `--yolo`;default → 无 | text |
| pi | `pi -p --mode json [--model <model>] [--skill <path>] [flag] "<prompt>"` | auto → 默认工具集;default → `--tools read,grep,find,ls`(只读) | json |

- skill 装载:opencode = 约定放 `${CRON_HOME}/skills/`(自动发现)或 prompt 内引用;kimi = `--skills-dir ${CRON_HOME}/skills`;pi = `--skill ${CRON_HOME}/skills/<name>`
- `model` 字段透传给支持选模型的执行器;script 忽略
- 适配器单测用 golden 用例(输入任务定义 → 断言命令行数组),不真调 LLM
- agent CLI 升级可能改 flags:适配器集中一处封装,版本变化只改单文件(开放问题 §14)

---

## 8. OpenCode 集成(薄)

实现分两层:M1 用 opencode **自定义命令文件**(`.opencode/commands/cron-*.md`,官方文档化特性:支持 `$ARGUMENTS` 与 `!`shell`` 输出注入,零构建链、随仓库分发);若后续需要更丰富交互(TUI 面板、同步按钮)再评估 TS 插件。

| 命令 | 行为 | 写路径 |
|---|---|---|
| `/cron-list` | 注入 runner `list` 表格输出:id、schedule、enabled、last_run、last_status、next_run | 只读 |
| `/cron-run <id>` | 执行 `runner run <id>`,报告退出码与日志路径 | 经 runner |
| `/cron-status <id>` | 最近 N 次运行摘要(读 runs/) | 只读 |
| `/cron-toggle <id>` | 改 YAML `enabled` 字段;提示执行 sync | 写任务文件 |
| `/cron-log <id>` | 最近一次运行 stdout/stderr 尾部 | 只读 |

M1 实现 `/cron-list`、`/cron-run`;其余 M2。命令只读解析、尽力渲染、不做 schema 校验(runner 是唯一权威)。执行类写操作全部经 runner 子进程。

---

## 9. 配置与可移植性

```yaml
# config.yaml(入库,通用默认)
max_concurrent: 2          # 全局并发上限(×weight)
retention_days: 14         # runs/ 滚动清理
variables:
  WORKSPACE: ""            # 占位:必须由 config.local.yaml 覆盖
  PYTHON: "python"
# config.local.yaml(不入库)覆盖任意键值,可增私有变量
```

- `config.example.yaml` 入库作模板(`*.local.*` 已在 .gitignore)
- **新机器三步**:clone 仓库 → 拷 example 为 local 并填 `WORKSPACE` 等变量 → `runner sync` 注册 schtasks
- 所有入库文档与示例任务零绝对路径(一律 `${CRON_HOME}`/`${WORKSPACE}` 等变量)、零个人信息

---

## 10. 安全模型(如实分层)

1. **主防线 = 各执行器自带权限模型**(§7 映射)。本系统默认 `permission: auto`(无人值守场景不等待交互审批),依据:定时任务内容是任务作者预先审阅的
2. **runner 兜底**:timeout 硬杀、workdir 限定、stdout/stderr 全量留痕(runs/ 可审计)
3. **不做的**:拦截 agent 运行期行为(做不到,不装能做到);外发渠道(钉钉/邮件/短信)不进插件——任务自身调用,责任在任务作者
4. **建议**(非强制):高破坏性任务用 script 执行器包装(命令显式、可审查);agent 任务给最小 workdir

---

## 11. 仓库目录结构

```
Cron/(仓库根)
├── runner/
│   ├── cron_runner/          # Python 包
│   │   ├── main.py           # CLI 入口(run/sync/list/validate)
│   │   ├── config.py         # 配置加载、合并、变量插值
│   │   ├── tasks.py          # 任务加载、overlay、schema 校验
│   │   ├── scheduler_win.py  # schtasks 翻译与对账(sync)
│   │   ├── due.py            # croniter due 判定(回退模式用)
│   │   ├── locks.py          # 无等待锁、陈锁回收、活跃注册表
│   │   ├── history.py        # runs/ 写入与滚动清理
│   │   └── executors/
│   │       ├── base.py       # 适配器接口(纯函数)
│   │       ├── script.py
│   │       ├── opencode.py
│   │       ├── kimi.py
│   │       └── pi.py
│   └── tests/
├── plugin/opencode-cron/     # 薄 TS 插件(opencode 插件布局)
├── tasks.d/                  # 示例任务(入库,零 PII)
├── tasks.d.local/            # 真实任务(不入库)
├── skills/                   # 任务专用技能(入库)
├── config.yaml / config.example.yaml
├── install/                  # 说明与脚本(sync 首跑引导等)
├── docs/spec/                # 本文档与后续 plan
└── runs/ state/ logs/        # 全部不入库
```

---

## 12. 测试策略

| 层 | 内容 | 方式 |
|---|---|---|
| 单测 | due 判定与 miss 语义(注入时钟)、变量插值(未定义报错)、overlay 替换、锁竞争(fake pid / 伪 boot_id / 陈龄接管)、适配器命令构造(golden) | pytest,不依赖真 schtasks/LLM |
| 集成 | script 执行器真跑 e2e(echo 任务 → runs/ 产物断言);sync 对账幂等(假 schtasks 层注入) | pytest + 注入桩 |
| 本机验收 | 注册真实 schtasks + 分钟级示例任务,实跑一轮验证 runs/、补跑、toast | 一次性 checklist |
| 插件 | /cron 五命令手动过 | checklist |

---

## 13. 分期

- **M1**:runner 核心(run/list/validate)+ script/opencode 执行器 + sync(常用频次:DAILY/WEEKLY/MINUTE)+ `/cron list,run` + 两个起步用例(周报采集汇总、仓库例行更新)
- **M2**:kimi/pi 执行器 + MONTHLY/回退调度 + `/cron toggle,status,log,sync` + toast + 滚动清理
- **M3**:多步 steps 编排(前步输出注入后步)+ catchup 槽位重放 + 孤儿进程治理(可选)

## 14. 开放问题与风险

| # | 风险/问题 | 对策 |
|---|---|---|
| R1 | schtasks 分钟级相位锚定(/RI + /DU 上限)未实测 | M1 首个验证任务;不满足则退化 MINUTE /MO N 并文档化相位偏移 |
| R2 | agent CLI 升级改 flags | 适配器单文件封装;升级只改一处 + golden 更新 |
| R3 | PID 复用误判"存活" | 锁内含 boot_id,重启即判陈 |
| R4 | runner 被杀留孤儿 CLI 进程 | M3 可选治理;短期靠 timeout 树杀 + 活跃登记清扫 |
| R5 | 时区/DST 边界 | 统一机器本地时区;不做跨时区协调(单机自治) |
