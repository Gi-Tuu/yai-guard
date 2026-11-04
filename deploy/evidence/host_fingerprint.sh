#!/usr/bin/env bash
# 宿主取证脚本：容器跑攻击前后各执行一次，diff 两次输出即可证明宿主未受影响。
#
# 用法（在跑容器的宿主机上）：
#   bash deploy/evidence/host_fingerprint.sh > /tmp/host-before.txt
#   # ... 跑 M1 / M2 / M3 ...
#   bash deploy/evidence/host_fingerprint.sh > /tmp/host-after.txt
#   diff -u /tmp/host-before.txt /tmp/host-after.txt || true
set -u

echo "=== YAI Guard host fingerprint ==="
echo "captured_at: $(date -u +%FT%TZ)"
echo "hostname:    $(hostname 2>/dev/null || echo unknown)"
echo "kernel:      $(uname -srm 2>/dev/null || echo unknown)"
echo

echo "--- uptime ---"
uptime 2>/dev/null || echo "uptime unavailable"
echo

echo "--- inodes: df -i / ---"
df -i / 2>/dev/null || echo "df unavailable"
echo

echo "--- files under /etc (xdev) ---"
find /etc -xdev -type f 2>/dev/null | wc -l
echo

echo "--- processes: ps -e ---"
ps -e 2>/dev/null | wc -l
echo

echo "--- tcp/udp sockets: ss -tunp ---"
ss -tunp 2>/dev/null | wc -l || echo "ss unavailable"
echo

echo "--- containers ---"
docker ps -a --format '{{.Names}} {{.Status}}' 2>/dev/null || echo "docker unavailable"
