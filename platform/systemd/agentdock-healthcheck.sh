#!/bin/bash
# 五个端口 + 关键端点的存活检查。
# oneshot 单元的子进程崩溃时 systemd 察觉不到（脚本已退出，unit 仍 active），
# Optional local scheduler (systemd timer or cron) invokes this check; the package does not include a timer.
LOG=$LOG_DIR/healthcheck.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }
port() { ss -tln 2>/dev/null | grep -q "$1 "; }

fail=0
# 8421 MemoryKnowledge
if ! port ':8421'; then
  echo "$(ts) 8421 DOWN -> restart agentdock-knowledge" >> $LOG
  systemctl --user restart agentdock-knowledge.service; fail=1
fi
# 8765 Basic Memory
if ! port ':8765'; then
  echo "$(ts) 8765 DOWN -> restart agentdock-basic-memory" >> $LOG
  systemctl --user restart agentdock-basic-memory.service; fail=1
fi
# 6420/6421/6422 Backlog
for p in 6420 6421 6422; do
  if ! port ":$p"; then
    echo "$(ts) $p DOWN -> restart agentdock-backlog" >> $LOG
    systemctl --user restart agentdock-backlog.service; fail=1
    break
  fi
done
[ $fail -eq 0 ] && echo "$(ts) all healthy" >> $LOG
exit 0
