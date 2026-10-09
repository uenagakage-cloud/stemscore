#!/bin/bash
# VM 上で実行すると、GitHub の最新コードで StemScore を作り直す: sudo bash /opt/stemscore/deploy/oracle/update.sh
set -eu
cd /opt/stemscore && git pull --ff-only
cd deploy/oracle && docker compose up -d --build
