# Life Journal Kit · 生活日记与地图

把主动提供的生活片段整理成日记，确认后归档，再回看一个月的生活。

这是 v0.1.0-preview 预览版。包含可供 Codex 使用的日记 Skill、零第三方运行依赖的 Python 工具，以及只读生活地图。所有演示内容均为虚构。

## 先体验

需要 Python 3.9 或以上及现代浏览器。本机已验证 macOS / Python 3.9.6；其他系统尚未验收。CLI 的进程锁使用 Unix `fcntl`，暂不支持 Windows 原生运行。

在本工具根目录执行：

```bash
python3 scripts/journal.py --data ../journal-demo demo
python3 scripts/journal.py --data ../journal-demo serve
```

`journal-demo` 必须是新空目录。浏览器访问终端显示的 [本地地图](http://127.0.0.1:4318)，按 Ctrl+C 停止服务。端口冲突时在 serve 后加 `--port 4319`。

也可以直接用浏览器打开 `../journal-demo/site/index.html`，无需启动服务。页面所有资源都在这一份文件内，没有远程字体、脚本或遥测。它包含所生成的日记和来源内容，不应随意外发。

演示包含两个月、11 篇日记、2 份经过整理的月度回顾，以及未记录的日期、未明确写感受的日记和跨零点补充。演示回顾是随包附带的虚构成稿，不是脚本调用模型生成的结果。

## 用 Codex 开始自己的记录

1. 在 Codex 中打开整个工具根目录。本仓库自带 `.agents/skills/life-journal/SKILL.md`，不需要安装到全局技能目录。
2. 明确告诉 Codex 使用 `$life-journal`，指定一个源码目录之外的新数据目录和你的时区。
3. 提供当天片段，让它先整理初稿；核对后明确说“可以定稿”。
4. 需要回顾时说“使用 life-journal 生成某年某月回顾，并更新生活地图”。

示例请求（请替换为自己的日期和素材）：

> 使用 $life-journal。我的数据目录是工具目录旁的 my-journal，时区 Asia/Shanghai。记录 2026-10-17：今天整理了书架，还没处理桌面上的纸张。先给我看初稿。

Skill 负责指导 AI 忠实整理；Python 工具负责去重、文件保存、来源检查与地图生成。工具本身不调用模型 API、不要求 API key；通过 Codex 整理时需要你自己的可用 Codex 环境，使用额度和费用遵循该服务。

如果 Skill 没有自动出现，可直接让 Codex 读取上面的 SKILL.md 路径再执行。不要只复制 Skill 文件夹；它依赖仓库内的脚本和页面资源。

## 不使用 AI 也能记录原话

以下示例会在独立目录保存虚构文字；用于熟悉流程。实际使用时替换日期和内容。

```bash
python3 scripts/journal.py --data ../my-journal init --timezone Asia/Shanghai
printf '%s' '今天整理了书架。桌面上的纸张还没有处理。' | python3 scripts/journal.py --data ../my-journal add --date 2026-10-17 --kind event --file -
python3 scripts/journal.py --data ../my-journal draft --date 2026-10-17
```

打开命令返回的初稿 JSON，核对原话。脚本初稿按素材类别排列，不自行推断心理或改写成抒情叙事。可以让 Codex 按 Skill 润色；每段仍保留来源引用。

确认准确后再执行：

```bash
python3 scripts/journal.py --data ../my-journal finalize --date 2026-10-17 --confirm
python3 scripts/journal.py --data ../my-journal review --month 2026-10
python3 scripts/journal.py --data ../my-journal build
```

`--confirm` 表示你已经审阅并同意定稿，不应由自动化默认添加。`review` 默认输出按日汇编；让 Codex 使用 Skill 整理后，才会形成有主题、有来源的月度回顾。再次生成回顾会创建新版本，不覆盖旧版。

`add --kind` 支持 event（发生的事）、feeling（明确感受）、plan（未来计划）、thread（未完成事项）。混合材料可分几次添加，`--file` 也支持 UTF-8 文件路径。重复添加相同日期、类别、来源和原文不会重复保存。表达相近但措辞不同的素材仍需人工去重。

## 补充与更新

- 确认前继续 `add`，再 `draft`；已有润色段落保留，新内容追加。
- 过零点补充昨天，仍显式填写昨天的 `--date`。
- 已有终稿时，新增素材仍保存，但拒绝重新定稿覆盖。基础版暂不提供终稿修订命令，需要后续带历史保留的修订流程。
- 地图是静态快照。每次新增或生成回顾后执行 `build`，然后刷新浏览器；`serve` 启动时也会先构建一次，不自动监听。
- 月报与当前终稿不一致时显示“需要更新”，不继续展示旧结论。

## 数据在哪里

```text
my-journal/                        # 必须在源码之外
  config.json                     # 时区与演示标记，无凭证
  inbox/YYYY-MM-DD.json            # 原始素材
  drafts/YYYY-MM-DD.json           # 待确认初稿
  days/YYYY-MM/YYYY-MM-DD/
    journal.md                    # 可读终稿
    record.json                   # 同一终稿的结构与来源快照
  draft-history/YYYY-MM/           # 已定稿的初稿留档
  reviews/YYYY-MM/v001.json        # 月度回顾，可版本化
  site/index.html                 # 可重建的只读地图，含日记数据
```

终稿目录整体写入成功后才成为有效记录，随后初稿归档。终稿的 Markdown 与 JSON 应一致；若手动只改一份，地图会提示不一致并停止构建，而不是静默采用其中一份。当前格式与其他日记软件不直接兼容。

## 隐私与能力边界

- 基础流程仅处理你主动提供的素材。可选飞书采集需要显式配置，限定指定单聊与机器人；不采集 Codex 历史或其他聊天。
- 本地网页不对外发送数据，预览服务仅绑定 `127.0.0.1`，只提供地图文件，不暴露收件箱目录。
- 用云端 AI 整理时，相关素材可能由所选服务处理；同步盘也可能同步本地文件。不要将“本地保存”理解为“完全离线”。
- 来源校验能检查引用是否存在，不能证明 AI 概括没有曲解原意。请核对事实和情绪表达，不放入密码、密钥、证件号码或不必要的他人隐私。
- 可选飞书读取及本机单次睡眠唤醒已试跑；长期每日运行、Codex 历史采集、多人协作和自动修订尚未验收或实现。地图上的事项是当时的记录，不是自动判定的当前任务状态。

## 离线采集与调度模拟

新增[模拟工具与说明](docs/simulation.md)，使用虚构返回值验证采集、去重、失败重试和补跑；不连接账号、不安装定时器。

## 验证与故障处理

```bash
python3 -m unittest discover -s tests -v
```

[数据格式与恢复说明](docs/data-and-recovery.md) · [验证记录](docs/validation.md)

源码使用 Python 标准库、原生 HTML/CSS/JavaScript 和系统字体；无 npm/pip 运行安装步骤。本项目采用 MIT 许可证，见 LICENSE。

## 可选真实采集

已增加限定单聊的飞书真实适配与 macOS 试跑执行器，默认不启用。配置、数据和日志必须位于源码之外；本机单次睡眠唤醒及后台读取已通过，长期运行尚未验证。参见[接入与限制](docs/real-capture.md)。

## 版本与贡献

[首版更新记录](CHANGELOG.md) · [贡献说明](CONTRIBUTING.md) · [MIT 许可证](LICENSE)
