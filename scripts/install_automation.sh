#!/bin/zsh
# Install the two launchd agents: keep the server alive at login, and run
# the refresh hourly at :05 from 7am to 8pm (7am pass is --deep).
set -e
DIR="${0:A:h:h}"
AGENTS="$HOME/Library/LaunchAgents"
mkdir -p "$AGENTS" "$DIR/data"

cat > "$AGENTS/com.doza.production-desk.server.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.doza.production-desk.server</string>
    <key>ProgramArguments</key><array><string>$DIR/run.sh</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>15</integer>
    <key>StandardOutPath</key><string>$DIR/data/server.log</string>
    <key>StandardErrorPath</key><string>$DIR/data/server.log</string>
</dict>
</plist>
EOF

HOURS=""
for h in {7..20}; do
  HOURS+="<dict><key>Hour</key><integer>$h</integer><key>Minute</key><integer>5</integer></dict>"
done
cat > "$AGENTS/com.doza.production-desk.refresh.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.doza.production-desk.refresh</string>
    <key>ProgramArguments</key><array><string>$DIR/refresh.sh</string></array>
    <key>StartCalendarInterval</key><array>$HOURS</array>
    <key>StandardOutPath</key><string>$DIR/data/refresh-cron.log</string>
    <key>StandardErrorPath</key><string>$DIR/data/refresh-cron.log</string>
</dict>
</plist>
EOF

UID_N=$(id -u)
launchctl bootout "gui/$UID_N/com.doza.production-desk.server" 2>/dev/null || true
launchctl bootout "gui/$UID_N/com.doza.production-desk.refresh" 2>/dev/null || true
launchctl bootstrap "gui/$UID_N" "$AGENTS/com.doza.production-desk.server.plist"
launchctl bootstrap "gui/$UID_N" "$AGENTS/com.doza.production-desk.refresh.plist"
echo "✓ installed — server on http://localhost:5175, refresh hourly 7am–8pm"
