#!/usr/bin/env bash
# Pre-install every server at a pinned version into phase2/vendor, so the
# "network down" run never needs to download anything.
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p vendor && cd vendor
[ -f package.json ] || npm init -y >/dev/null
npm install --no-audit --no-fund --loglevel=error \
  @notionhq/notion-mcp-server@2.6.1 \
  @sentry/mcp-server@0.49.0 \
  @microsoft/clarity-mcp-server@2.0.1 \
  @upstash/context7-mcp@4.7.0 \
  @azure-devops/mcp@2.11.0 \
  @softeria/ms-365-mcp-server@0.184.2 \
  @merill/lokka@2.1.2
python3 -m venv venv
venv/bin/pip install -q mcp-server-fetch==2026.11.11
echo "node $(node --version)  python $(python3 --version)"
echo "setup done"
