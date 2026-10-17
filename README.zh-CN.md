# pi-memory

[English](README.md) | 简体中文

给 [Pi](https://pi.dev) 用的跨会话记忆。记忆以 Markdown 文件保存在本地 git 仓库里，脚本只依赖 Python 3 标准库。

## 运行要求

- Pi 1.1.0 或更新
- Python 3（macOS 和 Linux 用 `python3`，Windows 用 `py -3`）
- git

## 安装

```bash
pi install git:github.com/n0ctx/pi-memory   # 之后更新：pi update git:github.com/n0ctx/pi-memory
pi install /path/to/pi-memory               # 或者从本地目录直接加载
```

不需要其他设置。记忆目录是 `$MEMQ_ROOT`，未设置时为 `~/reports`；第一次打开 Pi 会话时会自动创建，并建成 git 仓库。

## 工作方式

记账的事由插件完成，模型只负责判断哪些内容值得记。

- **每次请求前**：在系统提示里加一张记忆卡片，最多约 2000 字。内容依次是：
  - 当前项目没完成的报告，逾期的会标出。项目名取 git 仓库目录名，不在仓库里时取当前目录名；
  - 每条一行的偏好和纠正；
  - 不属于任何项目的未完成报告；
  - 全局备注；
  - 其他项目各有几项未完成。
- **`memory` 工具**：给模型四个操作：
  - `search`：搜索；
  - `note`：记一条一行的备注，分 `environment`（本机信息）、`preference`（偏好）、`global`（全局提醒）三类；
  - `report`：新建任务报告；
  - `update`：修改报告，并记下改了什么。

  文件名、日期、所属项目和文件头由代码生成，`related` 用来关联其他报告。同一项目里已有同名且没关闭的报告时，新建会被拒绝，模型要改为更新那一份。
- **每轮回答结束时**：对改过的报告补齐能推出来的字段（标题、创建日期、更新日期、项目），检查改过的文件，重新生成 `INDEX.md`，并提交记忆目录里的全部改动。没改过的文件有问题，只给警告。
- **每轮最多多做一轮**：出现下列情况之一时，模型会多得到一轮去修正或补记：
  - 改过的文件没通过检查，比如有疑似密钥；
  - 本轮改了本机配置（装软件、改服务、改主目录下的配置文件），却什么都没记；
  - 你的话像是持久偏好，却什么都没记；
  - 本轮改了项目文件，你的话里有把部分工作留到以后的说法（如"剩下的明天做""for now"），却没有新建或更新任何报告。

  这两类说法都能识别中文和英文。
- **子任务**：子任务进程（设置了 `PI_SUBAGENT_DEPTH`）既没有卡片也没有工具，结果由主会话来记。
- **写入锁**：自动写入共用 `<root>/.task-memory.lock`。持锁进程已退出，或锁超过 10 分钟，会被自动接管。

## 目录结构

| 记忆目录下的路径 | 内容 |
|---|---|
| `<主题>-YYYY-MM-DD.md` | 报告：一个决定及其理由，或未完成工作的交接 |
| `_meta/STATE.md` | 全局备注，每条一行（`[due YYYY-MM-DD]` 表示截止日期） |
| `_meta/LESSONS.md` | 偏好和纠正，每条一行 |
| `_meta/ENVIRONMENT.md` | 本机信息：工具、端口、服务、模型、配置文件路径 |
| `INDEX.md` | 自动生成的报告目录 |

报告文件头使用受限的 YAML：普通值、双引号字符串、`[a, "b"]` 列表、`#` 注释。

```yaml
---
title: "calc.py 交接"
date: 2026-10-18          # 创建日期，和文件名一致
updated: 2026-10-18
status: partial           # open | partial | plan | done | abandoned | superseded
type: handoff             # 选填：fix | feature | audit | design | ops | handoff | research
project: calc             # 选填；没有项目的报告在每个项目里都会显示
tags: [calc, div, ValueError]
summary: "sub 已完成，mul 和 div 待做"
open: ["mul", "div 除零时抛 ValueError"]
due: 2026-10-19           # 选填
related: []               # 选填：其他报告的文件名
superseded_by: ""         # status 为 superseded 时必填
---
```

旧的记忆目录照常可用：`### L-NNNN` 格式的旧教训和 JOURNAL 文件仍能搜到，未退役的旧教训也会出现在卡片里。

## 脚本

都支持 `--root`，未指定时依次用 `$MEMQ_ROOT`、`~/reports`。Python 启动命令可以是 `python3`、`python` 或 `py -3`。

| 脚本 | 用途 |
|---|---|
| `memcard.py [--project P]` | 输出记忆卡片 |
| `memq.py <关键词...> [--project P]` | 搜索报告、教训和 `_meta` 下的笔记 |
| `memwrite.py <note\|report\|update> --json '{...}'` | `memory` 工具背后的结构化写入 |
| `memsave.py [--project P]` | 补字段、检查、更新目录并提交待保存的改动 |
| `memindex.py [--lint\|--check]` | 重新生成 INDEX，检查整个记忆目录 |
| `init-memory.py` | 创建记忆目录及其 git 仓库 |

## 测试

```bash
python3 -m unittest discover -s tests -v
node --test tests/*.test.ts
```
