#!/bin/bash
# 五个端口 + 关键端点的存活检查。
# oneshot 单元的子进程崩溃时 systemd 察觉不到（脚本已退出，unit 仍 active），
# 本脚本由 vcc-healthcheck.timer 每 2 分钟调用，异常则重启对应单元。
LOG=$LOG_DIR/healthcheck.log
ts() { date '+%Y-%m-%d %H:%M:%S'; }
port() { ss -tln 2>/dev/null | grep -q "$1 "; }

fail=0
# 8421 MemoryKnowledge
if ! port ':8421'; then
  echo "$(ts) 8421 DOWN -> restart vcc-knowledge" >> $LOG
  systemctl --user restart vcc-knowledge.service; fail=1
fi
# 8765 Basic Memory
if ! port ':8765'; then
  echo "$(ts) 8765 DOWN -> restart vcc-basic-memory" >> $LOG
  systemctl --user restart vcc-basic-memory.service; fail=1
fi
# 6420/6421/6422 Backlog
for p in 6420 6421 6422; do
  if ! port ":$p"; then
    echo "$(ts) $p DOWN -> restart vcc-backlog" >> $LOG
    systemctl --user restart vcc-backlog.service; fail=1
    break
  fi
done
[ $fail -eq 0 ] && echo "$(ts) all healthy" >> $LOG
exit 0
