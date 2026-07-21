#!/bin/bash
# Doza Dashboard menu-bar plugin (SwiftBar). Shows how many replies the owner
# owes; dropdown lists them and opens the dashboard. Refreshes every minute.
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
# <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>

D=$(curl -s -m 3 http://localhost:5175/api/dashboard)
if [ -z "$D" ]; then
  echo "D ·"
  echo "---"
  echo "Dashboard unreachable | color=#A9A49B"
  echo "Open Doza Dashboard | href=http://localhost:5175"
  exit 0
fi

OWED=$(echo "$D" | /usr/bin/jq '.waiting_on_me | length')
HOT=$(echo "$D" | /usr/bin/jq '[.waiting_on_me[] | select(.days >= 3)] | length')

if [ "$OWED" = "0" ]; then
  echo "D ✓"
elif [ "$HOT" -gt 0 ]; then
  echo "D $OWED | color=#E26B60"
else
  echo "D $OWED"
fi
echo "---"
echo "$D" | /usr/bin/jq -r '.waiting_on_me[] |
  "\(.counterpart) — \(.days)d · \(.subject[0:44]) | href=http://localhost:5175"'
echo "---"
DUE=$(echo "$D" | /usr/bin/jq '[.top_actions[] | select(.due_days != null and .due_days < 1)] | length')
[ "$DUE" != "0" ] && echo "$DUE action(s) due today | color=#E08856 href=http://localhost:5175"
echo "Open Doza Dashboard | href=http://localhost:5175"
