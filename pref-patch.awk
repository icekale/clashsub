# 在镜像自带的 pref.example.toml 副本上覆写 clashsub 需要的键，其余沿用上游默认值。
# 用法：awk -f pref-patch.awk <pref.example.toml> > pref.toml
# 任一目标键在源文件中缺失时以非零状态退出，避免上游改名后静默退回默认行为。
function fail(message) {
  printf "pref-patch: %s\n", message > "/dev/stderr"
  bad = 1
}

BEGIN {
  override["managed_config|write_managed_config"] = "false"
  override["remote_subscription|surge_policy_path"] = "false"
  override["remote_subscription|surfboard_policy_path"] = "false"
  override["remote_subscription|loon_remote_proxy"] = "false"
  override["statistics|enabled"] = "true"
  override["statistics|data_dir"] = "\"/data/stats\""
  override["security|profile"] = "\"lan\""
  for (key in override) {
    split(key, parts, "|")
    wanted[parts[1] SUBSEP parts[2]] = override[key]
  }
}

/^\[/ {
  section = $0
  sub(/^\[/, "", section)
  sub(/\].*$/, "", section)
}

{
  if (match($0, /^[ \t]*[A-Za-z0-9_-]+[ \t]*=/)) {
    key = substr($0, RSTART, RLENGTH)
    sub(/[ \t]*=[ \t]*$/, "", key)
    gsub(/^[ \t]+|[ \t]+$/, "", key)
    wanted_key = section SUBSEP key
    if (wanted_key in wanted) {
      print key " = " wanted[wanted_key]
      seen[wanted_key] = 1
      next
    }
  }
  print
}

END {
  if (bad) {
    exit 1
  }
  for (key in wanted) {
    if (!(key in seen)) {
      split(key, parts, SUBSEP)
      fail("缺少键 " parts[1] "." parts[2])
    }
  }
  exit bad ? 1 : 0
}
