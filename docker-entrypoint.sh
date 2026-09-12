#!/bin/sh
set -eu

# SubConverter-Extended 需要配置目录可写，且模板/规则相对路径按配置目录解析。
# 把镜像内的 /base 复制到 tmpfs 的 /tmp/subconverter，再以那里为配置目录启动，
# 服务仅监听回环 :25500；随后切换到主应用进程，传入的 CMD 会被原样执行。
mkdir -p /tmp/subconverter
cp -a /base/. /tmp/subconverter/

# 由镜像自带的 pref.example.toml 生成 pref.toml：只覆写 clashsub 需要的键（见 pref-patch.awk 注释），
# 其余配置跟随上游默认值；键缺失时 awk 会以非零状态退出，避免静默退回默认行为。
awk -f /usr/local/bin/pref-patch.awk /base/pref.example.toml > /tmp/subconverter/pref.toml
(cd /tmp/subconverter && PREF_PATH=/tmp/subconverter/pref.toml /usr/local/bin/start-subconverter) &

ready=0
attempt=0
while [ "$attempt" -lt 30 ]; do
  if python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:25500/version', timeout=2)" >/dev/null 2>&1; then
    ready=1
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if [ "$ready" -ne 1 ]; then
  echo "subconverter failed to start on 127.0.0.1:25500" >&2
  exit 1
fi

exec "$@"
