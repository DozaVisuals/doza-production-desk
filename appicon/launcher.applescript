-- Doza Dashboard launcher: make sure the local server is up, then open it.
do shell script "
if ! /usr/bin/curl -s -m 1 -o /dev/null http://localhost:5175/api/health; then
  /bin/launchctl kickstart gui/$(/usr/bin/id -u)/com.doza.production-desk.server 2>/dev/null || /bin/launchctl bootstrap gui/$(/usr/bin/id -u) \"$HOME/Library/LaunchAgents/com.doza.production-desk.server.plist\" 2>/dev/null
  i=0
  while [ $i -lt 30 ]; do
    /usr/bin/curl -s -m 1 -o /dev/null http://localhost:5175/api/health && break
    /bin/sleep 0.3
    i=$((i+1))
  done
fi
/usr/bin/open 'http://localhost:5175'
"
