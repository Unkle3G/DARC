#!/usr/bin/env bash
# 把每日运行装进这台 Mac 的 launchd。
#
#   LIVER_INTEL_CONTACT="you@example.com" scripts/install-macos.sh
#
# 幂等：重复执行会覆盖并重新加载。卸载见脚本末尾提示。
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.hepadaily.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

# 状态库刻意放在仓库之外：`git clean` 或重新 clone 都不该抹掉「哪些条目
# 发过」。丢了它，下一次运行会把所有已停试验当成首次见到重发一遍。
STATE="${LIVER_INTEL_STATE:-$HOME/Library/Application Support/HepaDaily}"

CONTACT="${LIVER_INTEL_CONTACT:-}"
if [ -z "$CONTACT" ]; then
    echo "需要 LIVER_INTEL_CONTACT：EDGAR 要求 User-Agent 里带联系邮箱。" >&2
    echo "用法：LIVER_INTEL_CONTACT=\"you@example.com\" $0" >&2
    exit 1
fi

if [ "$(/bin/date +%Z)" != "CST" ]; then
    echo "提示：本机时区是 $(/bin/date +%Z)（$(/bin/date +%z)），不是中国标准时间。" >&2
    echo "      plist 里的 06:30 会按本机时区计时，请自行换算后再改 Hour/Minute。" >&2
fi

mkdir -p "$STATE/logs" "$STATE/out" "$HOME/Library/LaunchAgents"

sed -e "s|__PROJECT__|$PROJECT|g" \
    -e "s|__CONTACT__|$CONTACT|g" \
    -e "s|__STATE__|$STATE|g" \
    "$PROJECT/scripts/com.hepadaily.daily.plist" > "$PLIST"

chmod +x "$PROJECT/scripts/run_daily.sh"

# bootout 先卸掉旧的；没装过时它会报错，这不是问题。
launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"

echo "已装载 $LABEL"
echo "  项目    $PROJECT"
echo "  状态库  $STATE/state.sqlite3   ← 备份这个文件，别删"
echo "  产物    $STATE/out"
echo "  日志    $STATE/logs/daily.log"
echo
echo "查看状态： launchctl print gui/$UID/$LABEL | head -20"
echo "立即试跑： launchctl kickstart -p gui/$UID/$LABEL"
echo "卸载：     launchctl bootout gui/$UID/$LABEL && rm \"$PLIST\""
echo
echo "两件 Mac 上必须知道的事："
echo "  1. 06:30 那一刻 Mac 如果是关机的，这一天就不跑；如果只是睡眠，"
echo "     launchd 会在唤醒后补跑一次。想让它准点醒，另外设："
echo "       sudo pmset repeat wakeorpoweron MTWRF 06:25:00"
echo "  2. 运行完成后引擎只写出文章，不翻译。译文要么来自 ANTHROPIC_API_KEY，"
echo "     要么由 Claude 会话填工作单后跑 \`liver-intel render --date <日期>\`。"
echo "     没有译文的稿子顶部会写 MODEL STEP DID NOT RUN，别当成完整成稿发出去。"
echo
echo "发布用这个文件："
echo "  $STATE/out/liver_daily_<日期>_wechat.html"
echo "  浏览器打开 → 全选 → 复制 → 粘进公众号编辑器。样式全是内联的，"
echo "  不需要 MDNice，也不需要装主题。"
