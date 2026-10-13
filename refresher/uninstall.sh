#!/usr/bin/env bash
# Removes the Job Buddy Refresher from this computer. It does not touch the Job Buddy website or its jobs.
set -uo pipefail
HOME_DIR="${JBR_HOME:-$HOME/job-buddy-refresher}"
SERVICE="job-buddy-refresher"
echo "This stops the refresher and removes it from this computer."
read -r -p "Continue? (y/N) " answer
[ "${answer:-n}" = "y" ] || { echo "Nothing changed."; exit 0; }
systemctl --user disable --now "$SERVICE" 2>/dev/null
rm -f "$HOME/.config/systemd/user/$SERVICE.service" "$HOME/.local/bin/jbr"
systemctl --user daemon-reload 2>/dev/null
read -r -p "Also delete its folder, $HOME_DIR (its small database and logs)? (y/N) " answer
if [ "${answer:-n}" = "y" ]; then cd "$HOME" && rm -rf "$HOME_DIR" && echo "Folder deleted."; else echo "Folder kept."; fi
echo "Removed. On the Job Buddy website, you can also remove this computer from Admin > Refresher."
