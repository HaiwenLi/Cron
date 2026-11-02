# M2 Backlog(来自 M1 全分支审查裁定)

> 2026-10-26 M1 完成时整理。非阻塞项,按优先级排列。

## 代码项

| # | 项 | 出处 |
|---|---|---|
| 1 | CLI 层对 `TaskValidationError` 的 per-file skip-and-warn(一个坏 YAML 不再打断 list/run/validate;_parse 已收敛为带文件名的类型化异常) | final review B1 |
| 2 | 分钟步进泛化:多触发器 XML 支持 `*/7` 类非整除步进(当前 `60 % N != 0` 直接 UnsupportedSchedule) | final review B2 |
| 3 | kimi / pi 执行器(argv golden 同 opencode 模式;validate 门已就位) | spec §13 M2 |
| 4 | MONTHLY 触发器 + 不可表达 cron 的每小时回退条目(croniter due 判定) | spec §3.2 |
| 5 | `/cron-toggle` `/cron-status` `/cron-log` `/cron-sync` 命令文件 | spec §8 |
| 6 | 失败 toast 通知(Windows 原生) | spec §13 M2 |
| 7 | runs/ 滚动清理已有,补:names() 查询失败不再静默返回空集(防全量重建) | Task 9 minor |
| 8 | 锁的 Windows `PermissionError`-on-unlink 捕获(→continue);preempted-unlink TOCTOU 加快照比对 | Task 4 fix 残余 |
| 9 | test_main.py 残留 unused `Path` import;`_find_model` 空串语义 | cosmetics |
| 10 | G1 实证:真实重启/睡眠 miss 后 StartWhenAvailable 补跑一次的真机观察(M1 已验证机制接受,未经历真实 miss) | final review A |

## Owner 决定项(公开推送前)

| # | 项 | 说明 |
|---|---|---|
| O1 | ~~git 作者元数据~~ **已裁定(2026-10-26)** | 保留现有身份;全历史泄漏扫描(树/历史 diff/commit message 三层:姓名、QQ 邮箱、绝对路径、机器名、API key 模式)仅命中已接受的身份元数据,内容零泄露 |
| O2 | `/cron-list` `/cron-run` TUI 手动验收 | 在仓库根打开 opencode 实测两条命令 |
