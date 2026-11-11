"""The notification sink (NOTIF-1; PRD FR-N1, FR-N2, §7.10 sink flow): the `notify` MCP server.

`notify_send` is the agent's only way to send. It runs here, downstream of the hub, with two channel
adapters: `telegram` (the Telegram mock: verify the orchestrator's grant, resolve the recipient
token in the vault, "send" through MOCK-1's transport stub, keep only a masked copy in
`mock_outbox`) and `in_app` (a `notifications` record for a platform user). The in-app consumer
(`notify.in_app`) also delivers platform events (change-request outcomes, blocked runs).
"""
