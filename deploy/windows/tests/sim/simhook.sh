#!/bin/bash
# 模拟 Windows 服务的钩子（仅 Linux 模拟测试）：stop|start <服务名> <安装根目录>
#   DB 服务：本模拟中数据库由外层 withpg.sh 管理，始终运行（记录调用）。
#   App 服务：用安装目录的 runtime/python/python.exe 启动 serve.py，并记录 pid。
act=$1; svc=$2; root=$3
log=$root/logs/sim-services.log
mkdir -p "$root/logs"
echo "$(date +%T) $act $svc" >> "$log"
pidf=/dev/shm/ishsim-app.pid   # 与安装目录无关：模拟“同名服务只有一个进程”（目录被整体移动也能找到）
if [[ $svc == *-App ]]; then
  if [[ $act == stop ]]; then
    if [[ -f $pidf ]]; then kill $(cat $pidf) 2>/dev/null; for i in $(seq 1 30); do kill -0 $(cat $pidf) 2>/dev/null || break; sleep 0.3; done; rm -f $pidf; fi
  else
    if [[ -f $pidf ]] && kill -0 $(cat $pidf) 2>/dev/null; then exit 0; fi
    cd "$root/app/backend" || exit 1
    ISH_CONFIG_FILE="$root/config/app.env" nohup "$root/runtime/python/python.exe" -X utf8 -B serve.py >> "$root/logs/sim-app.log" 2>&1 &
    echo $! > $pidf
  fi
fi
exit 0
