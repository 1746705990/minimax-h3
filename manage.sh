#!/bin/bash
# h3video 用户与任务管理脚本
# 用法：bash manage.sh <命令> [参数]    （不带参数显示帮助）
cd "$(dirname "$0")"

DB="./data/h3video.db"
OUT="./data/outputs"

if [ ! -f "$DB" ]; then
  echo "找不到数据库：$DB （请在 h3video 项目根目录执行）"
  exit 1
fi

# 优先用 sqlite3 CLI，没有则用 python3 兜底
if command -v sqlite3 >/dev/null 2>&1; then
  sql() { sqlite3 -separator '|' "$DB" "$1"; }
else
  sql() {
    python3 -c "
import sqlite3, sys
c = sqlite3.connect('$DB')
q = sys.argv[1].strip().rstrip(';')
if q.upper().startswith('SELECT'):
    for row in c.execute(q):
        print('|'.join('' if v is None else str(v) for v in row))
else:
    c.execute(q); c.commit()
" "$1"
  }
fi

case "$1" in

# ---------- 用户 ----------
users)
  echo "ID | 用户名 | 管理员 | 注册时间 | 任务总数 | 已完成"
  sql "SELECT u.id, u.username, CASE u.is_admin WHEN 1 THEN '是' ELSE '否' END,
       datetime(u.created_at,'unixepoch','localtime'),
       COUNT(j.id),
       COALESCE(SUM(CASE WHEN j.status='completed' THEN 1 ELSE 0 END),0)
       FROM users u LEFT JOIN jobs j ON j.user_id=u.id
       GROUP BY u.id ORDER BY u.id;"
  ;;

passwd)
  [ -z "$2" ] && { echo "用法：bash manage.sh passwd <用户名>"; exit 1; }
  python3 - "$2" <<'EOF'
import getpass, hashlib, os, sqlite3, sys
user = sys.argv[1]
pw = getpass.getpass(f"为 {user} 设置新密码（输入不显示）: ")
if len(pw) < 6:
    sys.exit("密码至少 6 位")
salt = os.urandom(16).hex()
dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 60000)
c = sqlite3.connect("./data/h3video.db")
cur = c.execute("UPDATE users SET password_hash=? WHERE username=?",
                (f"{salt}${dk.hex()}", user))
c.commit()
print("已重置" if cur.rowcount else "用户不存在")
# 重置后踢掉该用户所有会话，强制重新登录
c.execute("DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username=?)", (user,))
c.commit()
EOF
  ;;

admin)
  [ -z "$2" ] && { echo "用法：bash manage.sh admin <用户名> [on|off]"; exit 1; }
  V=1; [ "$3" = "off" ] && V=0
  sql "UPDATE users SET is_admin=$V WHERE username='$2';"
  echo "已设置 $2 管理员=$([ $V = 1 ] && echo 是 || echo 否)"
  ;;

deluser)
  [ -z "$2" ] && { echo "用法：bash manage.sh deluser <用户名>"; exit 1; }
  echo "将删除用户 $2 及其全部任务记录（视频文件保留）。确认？[y/N]"
  read -r Y; [ "$Y" = "y" ] || exit 0
  sql "DELETE FROM jobs WHERE user_id=(SELECT id FROM users WHERE username='$2');"
  sql "DELETE FROM sessions WHERE user_id=(SELECT id FROM users WHERE username='$2');"
  sql "DELETE FROM users WHERE username='$2';"
  echo "已删除"
  ;;

# ---------- 任务 ----------
jobs)
  if [ -n "$2" ]; then
    W="WHERE username='$2'"; echo "用户 $2 的任务："
  fi
  echo "任务ID | 用户 | 引擎 | 状态 | 时长 | 尺寸 | 创建时间 | 视频文件"
  sql "SELECT id, username, engine, status, seconds, size,
       datetime(created_at,'unixepoch','localtime'), video_file
       FROM jobs $W ORDER BY created_at DESC LIMIT 50;"
  ;;

job)
  [ -z "$2" ] && { echo "用法：bash manage.sh job <任务ID>"; exit 1; }
  sql "SELECT '任务ID: '||id, '用户: '||username, '引擎: '||engine, '状态: '||status,
       '时长: '||seconds||'s', '尺寸: '||size,
       '创建: '||datetime(created_at,'unixepoch','localtime'),
       '完成: '||COALESCE(datetime(finished_at,'unixepoch','localtime'),'-'),
       '视频: '||COALESCE(video_file,'-'),
       '错误: '||COALESCE(substr(error,1,300),'-'),
       '提示词: '||substr(prompt,1,500)
       FROM jobs WHERE id='$2';"
  ;;

retry)
  [ -z "$2" ] && { echo "用法：bash manage.sh retry <任务ID>   （失败/卡死任务重新排队）"; exit 1; }
  S=$(sql "SELECT status FROM jobs WHERE id='$2';")
  case "$S" in
    failed|running)
      sql "UPDATE jobs SET status='pending', started_at=0, finished_at=0, error='' WHERE id='$2';"
      echo "已重新排队（worker 会自动领取）";;
    "") echo "任务不存在";;
    *)  echo "当前状态为 $S，仅 failed/running 可重新排队";;
  esac
  ;;

clean-running)
  N=$(sql "SELECT COUNT(*) FROM jobs WHERE status='running';")
  sql "UPDATE jobs SET status='pending', started_at=0 WHERE status='running';"
  echo "已将 $N 个卡死的 running 任务重置为 pending"
  ;;

deljob)
  [ -z "$2" ] && { echo "用法：bash manage.sh deljob <任务ID> [--with-video]"; exit 1; }
  if [ "$3" = "--with-video" ]; then
    F=$(sql "SELECT video_file FROM jobs WHERE id='$2';")
    [ -n "$F" ] && rm -f "$OUT/$F" && echo "已删除视频 $F"
  fi
  sql "DELETE FROM jobs WHERE id='$2';"
  echo "已删除任务记录"
  ;;

stats)
  echo "状态统计："
  sql "SELECT status, COUNT(*) FROM jobs GROUP BY status;"
  echo ""
  echo "今日提交：$(sql "SELECT COUNT(*) FROM jobs WHERE created_at>=strftime('%s','today');")"
  echo "输出目录占用：$(du -sh $OUT 2>/dev/null | cut -f1)"
  ;;

*)
  cat <<'HELP'
h3video 管理脚本

用户管理：
  users                    用户列表（含任务统计）
  passwd <用户名>          重置密码（同时踢掉其登录会话）
  admin <用户名> [on|off]  设置/取消管理员（默认 on）
  deluser <用户名>         删除用户及其任务记录

任务管理：
  jobs [用户名]            最近 50 条任务（可按用户过滤）
  job <任务ID>             任务详情（含提示词和报错）
  retry <任务ID>           失败/卡死任务重新排队
  clean-running            全部卡死 running 重置为 pending
  deljob <任务ID> [--with-video]   删除任务（可选连视频一起删）

概览：
  stats                    状态统计 / 今日提交量 / 存储占用
HELP
  ;;
esac
