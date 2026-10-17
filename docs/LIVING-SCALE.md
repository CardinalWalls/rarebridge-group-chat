# Raft + Grok + Living-scale stage snapshot

This repository now includes a stage snapshot of the real Python MCP bridge and native Raft card changes. It is a research demo, not a clinical service. No patient records, authentication files, SQLite databases, or local sessions are included.

## Run the native local demo

1. Install Node 24+, pnpm, Docker, tmux, Python 3.11+ and an authenticated Grok CLI.
2. From this repository run npm run bootstrap. This obtains the pinned upstream Raft revision in README. Local seed accounts must never be exposed publicly.
3. Export RAFT_RUNTIME_DIR to that isolated Raft checkout. Apply raft-integration/native-scale-card.patch with git -C "$RAFT_RUNTIME_DIR" apply /absolute/path/to/raft-integration/native-scale-card.patch, then pnpm install in the runtime. The patch includes FSL upstream-derived files; it is not relicensed MIT.
4. python3 -m venv living-scale/.venv
5. living-scale/.venv/bin/pip install -r living-scale/requirements.txt
6. RAFT_RUNTIME_DIR=/absolute/isolated/raft living-scale/.venv/bin/python living-scale/聊天联调/setup.py
7. Run npm start for the role studio; open the private scale-integration channel in Raft. The setup creates a bounded synthetic study role and installs its Skill/MCP config. Re-running setup stops/resets that role: coordinate with the active operator first.
8. Send a clearly synthetic report to @scale-study. Agent processing uses process_scale_message with a real Raft message UUID. Calculations and card payloads are persisted; compare source facts, assessments and posted message IDs. A sent message or an acknowledgement is not proof of an MCP result.

macOS report-image OCR uses Swift/Vision. Linux/cloud OCR is not implemented; text-only inputs can be used while the OCR adapter is replaced. Grok login and network access must be configured on the target host. Grok model access is account-dependent. Python state defaults to ignored .runtime directories. Keep the included directory layout, since modules load sibling code and schemas.

## Stage limitations and evidence

Native cards, fact corrections and source history exist in the development runtime. A recent public test message 0abb... was delivered into Raft, but the observed c6ae... card belonged to another request e460..., whose interpretation timed out. This pair is NOT accepted as a successful public-message-to-MCP result. Continue full correlated-message acceptance before claiming that end-to-end flow passes.

The optional demo-gateway is a restricted view of a fixed synthetic channel. It uses HTTP polling, not native Raft UI or WebSocket. Its credentials must be supplied locally in demo-gateway/.private/demo-identity.json: credentials(email/password), session(accessToken), channelId, serverId, agentId. It requires a separately provisioned guest whose only visible channel is the synthetic channel. An ignored .private/access-code is generated on start. Start with node demo-gateway/gateway.mjs. No guest provisioning credentials or live URL are shipped. This local gateway is not independent cloud hosting.

## Grok Build handoff

Read README, this document, and docs/GROK-CLOUD.md. Provision persistent PostgreSQL, Redis, S3-compatible storage and a long-running Raft server/daemon with real Grok CLI execution. Keep the upstream license. Create production identity and secrets instead of development seeds. Start with synthetic text reports only. Verify a single correlated chain: submitted Raft message UUID -> MCP invocation -> assessment/fact versions -> returned card UUID -> correction -> refresh/restart recovery. Report timeouts and unimplemented OCR honestly. Do not replace the backend with simulated chat. Do not expose local developer accounts, generic admin proxy endpoints, or patient records.

Current stage is source delivery on GitHub. The user will continue cloud hosting in Grok Build; a temporary local tunnel does not establish cloud deployment completion.

Verification: existing configured Python environment imported the packaged service and initialized a fresh empty SQLite database; JS syntax and four app tests pass. Fresh-machine dependency installation, complete clean-checkout Raft startup, cloud OCR and correlated public-message MCP acceptance have not been verified. This is a stage source snapshot, not a certified turnkey cloud release.

Clean-clone verification (2026-10-16): git clone into /tmp/rarebridge-clean-f052eec succeeded; npm test passed 4/4; using the existing Python environment with installed requirements, importing packaged IntegratedService and initializing an empty temporary SQLite database passed; MCP ClientSession initialize + list_tools against the cloned 接口实现/mcp_server.py passed with 15 tools. No patient database was used. These checks do not install dependencies on a new machine or start a new full Raft stack.
