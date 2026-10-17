# 飞书采集与定时运行：离线模拟

本阶段验证处理逻辑，不连接飞书，不读取账号配置，不访问网络，不创建系统定时任务。测试输入是我们自定义的归一化格式，**不是飞书原生 API 响应**。以后真实接入还需要验证授权、接口解析、分页、限流及时间字段映射。

## 一次可复现的演示

在源码根目录执行，`../simulation-demo` 应为新的空目录：

```bash
python3 scripts/simulate.py --data ../simulation-demo init
python3 scripts/simulate.py --data ../simulation-demo --fixture examples/simulated-work-summary.json tick --start 2026-10-17 --now 2026-10-17T22:29:00+08:00
python3 scripts/simulate.py --data ../simulation-demo --fixture examples/simulated-work-summary.json tick --start 2026-10-17 --now 2026-10-17T22:30:00+08:00
python3 scripts/simulate.py --data ../simulation-demo --fixture examples/simulated-work-summary.json tick --start 2026-10-17 --now 2026-10-17T23:00:00+08:00
python3 scripts/simulate.py --data ../simulation-demo --fixture examples/simulated-work-summary.json collect --date 2026-10-17
```

预期：第一次 tick 不执行；22:30 保存初稿；23:00 不重复执行；显式 collect 返回 unchanged。初稿在数据目录的 drafts 中，**不会自动确认成终稿**。模拟目录由 init 标为 demo，采集器拒绝向未标记 demo 的正式目录运行。

所有 tick 都是一次性的“假设现在是这个时间”调用，没有真的等待晚上、设置闹钟或让电脑后台常驻。

## 输入契约

`examples/simulated-work-summary.json` 给出完整样例：

- 顶层 simulation_only 必须为 true，pages 是该时刻可见的完整分页快照。
- 每页 status 可为 ok、timeout、unauthorized、rate_limited；任一页失败都不能处理部分结果。
- 消息包括虚构 id、chat、sender、summary_date、type、marker/title、created_at、updated_at 和 items。
- 默认仅接收 fixture-chat / fixture-bot；先匹配明确日期与 daily_summary，再优先专用标记，没有标记才使用“每日回顾”。晨间前瞻不作为日结。
- 时间必须带时区；晚于零点发出的昨日总结，使用明确 summary_date 归属。测试调用者提供该时刻真正可见的快照，模拟器不模拟服务端消息何时出现。
- items 仅接受 event、plan、thread。来自工作总结的内容是二次来源，不从中自动推断本人感受。

原始素材进入日记收件箱；采集进度只保存日期、消息 ID 的哈希、内容版本哈希和数量。不会把消息正文、真实聊天标识或错误响应正文写进状态与回执。

## 写入与变更

顺序为：完整读取 → 过滤 → 去重 → 素材入箱 → 初稿保存与校验 → 提交采集哈希。

写入或提交失败时，不推进状态；重复运行可利用素材 ID 去重。已经提交的总结发生编辑，或 fallback 被另一个专用标记总结替换时，返回 needs_review，保留旧版本，不将矛盾内容自动追加。未提交但已入箱的内容发生修改也会提示核对。

当前模拟器不自动接受编辑版；需未来明确的修订审核流程。真实适配器还需设计编辑详情的呈现方式。已有终稿时不会写入或覆盖终稿。

## 调度规则

- 使用数据配置时区，默认 22:30；`--at` 可模拟其他时间。
- 同一日期成功后，普通 tick 不重复处理。定稿前或需要查看晚到、编辑总结时，显式 collect 重查目标日期。
- 失败或无总结，30 分钟后允许再次尝试；tick 不自行等待，也不自行启动下一次尝试。
- 模拟次日早上恢复运行时，补跑昨天，不提前生成今天。
- 默认窗口为今天及前 3 天，更早日期作为 outside_window 范围提示，需要单独处理。该范围表示未在本轮检查，不代表全部未完成。
- 需要人工核对的状态停止该日自动重试；没有自动跳过审核或替用户确认。
- 调度状态写入失败时，下次 tick 可重试，采集去重防止再次插入相同素材。

这只是可重用的调度决策模块，不能证明系统调度器能唤醒休眠电脑，也不能证明关机时会运行。正式版仍需选择、安装并实测运行宿主；本轮未修改 launchd、cron 或 Codex 自动化。

## 验证结果

2026-10-17：原有 16 项 + 新增 26 项，共 42 项测试通过。新增测试期间禁止创建网络 socket，防止测试意外访问真实服务。另通过模拟 CLI 的初始化、到点执行、重复触发和手动复查，初稿与状态已落盘供核对。

覆盖：来源范围、日期与消息类型、标记优先/兜底、无总结、超时/未授权/限流、分页失败、重复版本、编辑冲突、写入及提交失败、终稿保护、跨零点、时区、到点、重试间隔、有限补跑和调度状态失败。

尚未验证：飞书账号授权与真实 API 返回、服务端分页一致性、系统定时器安装和唤醒、长时间无人值守、新用户机器上的端到端运行。通过模拟不表示这些能力已上线。
