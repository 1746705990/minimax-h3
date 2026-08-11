#!/bin/bash
cd "$(dirname "$0")"
for name in web worker; do
  if [ -f "logs/$name.pid" ]; then
    pid=$(cat "logs/$name.pid")
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" && echo "$name (pid $pid) 已停止"
    fi
    rm -f "logs/$name.pid"
  fi
done
