---
title: example-project 项目知识归属说明
tags:
- vcc
- example-project
- memory
permalink: vcc-notes/projects/example-project/README
---

# example-project 项目知识归属说明

## 这个目录的约定

`MyTask/memory/projects/example-project/` 下的所有 `.md` 归 **VCC double-OOD
recombination** 项目所有，会自动出现在该项目的 Backlog.md 看板
（http://<LAN_IP>:6422/ ）页内嵌的「本项目知识」面板里。

## 归属是怎么判定的

判定规则只有一处：`$PLATFORM_ROOT/platform/ports.json`

```json
{
  "name": "vcc-example-project",
  "ui_port": 6422,
  "knowledge": {
    "note_subdirs": ["projects/example-project"],
    "wikis": [],
    "panel": true
  }
}
```

看板内嵌面板和 6424 知识总览页读的是同一份映射，所以两处永远不会
显示不一致的内容。

## 放笔记的规矩

- 路径写成相对 `MyTask/memory` 的形式，面板按前缀匹配
- 面板默认只列前 12 条 wiki 页，笔记则全部列出
- 未被任何项目声明的笔记不会被丢弃，会归入 6424 的「未归属」区

## 与 wiki 的关系

本项目目前**没有**关联 wiki（`wikis: []`），所以面板里的 Wiki 一栏
显示「这个项目还没有 wiki」。平台的 13 页 wiki 归 `platform-core`
所有，出现在 6423 而不是这里——这是有意的隔离，避免知识串台。
