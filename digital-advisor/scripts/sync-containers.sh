#!/bin/bash
# Sync source code to running Docker containers
# Run this after making code changes to ensure all containers have the latest code.
set -e
cd "$(dirname "$0")/.."

FILES=(
  "app/services/tools/providers/feishu_mcp.py"    # OAuth approval query + token refresh
  "app/services/agent/policies.py"                 # Routing fix
  "app/services/tools/router.py"                   # Tool execution
  "app/services/llm/answer_semantics.py"            # Semantic intent
  "app/services/feishu/commands.py"                # Gateway commands
  "app/services/feishu/approval_card_entrypoint.py" # Card building
  "app/services/feishu/command_dispatcher.py"      # Command dispatch
)

echo "=== Syncing code to containers ==="
for container in feishu-ws worker beat; do
    CID="digital-advisor-${container}-1"
    if docker inspect "$CID" &>/dev/null; then
        echo "  → Copying to ${container}..."
        for f in "${FILES[@]}"; do
            # Strip 'app/' prefix to match container path /app/app/{relpath}
            rel="${f#app/}"
            docker cp "$f" "${CID}:/app/app/${rel}" 2>/dev/null
        done
        docker restart "$CID" > /dev/null
        echo "    ✓ ${container} restarted"
    else
        echo "    ✗ ${container} not running, skipping"
    fi
done
echo "=== Done ==="
