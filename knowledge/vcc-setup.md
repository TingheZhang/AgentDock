---
title: VCC 174 服务器科研环境
tags:
- vcc
- infra
- memory
permalink: vcc-notes/vcc-setup
---

# VCC 174 服务器科研环境

## 服务器角色
- <LAN_IP>：hub，5 个 agent 的接入点
- 本地盘 /data（11T ext4）：git 仓库与索引
- NFS $NFS_MOUNT：大数据（ext_data 15G，priors 493M，results 415M）

## 关键约定
- git 仓库放 $BARE_REPO（NFS 上 git 慢 100~700 倍）
- 工作副本 $PROJECT_DIR/example-project，大目录用软链回 NFS
- Backlog.md 看板：6421 聚合页 / 6422 VCC 项目 / 6420 写作侧
- codebase-memory-mcp 0.11.0 portable 版装在 $CBM_DIR
- Basic Memory 0.23.2 conda 环境 basic-memory（Python 3.12.15）

## 出网实测
- 可用：api.github.com、gitlab.com、registry.npmjs.org、pypi（走 pip）
- 封锁：objects.githubusercontent.com、raw.githubusercontent.com