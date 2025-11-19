Optimizer service for BlobeVM

Overview
- Small, isolated Node-based optimizer that implements memory/cpu/swap/health guards and logging.

Files
- `optimizer/OptimizerService.js` - CLI entrypoint (commands: `status`, `run-once`, `set <key> <json>`, `log-tail`)
- `optimizer/guards/*.js` - guard implementations (MemoryGuard, CPUGuard, SwapGuard, HealthGuard)
- `utils/systemStats.js` - helper used by Optimizer to collect system/container stats

Notes
- This service is intentionally isolated and does not modify any domain/routing configuration.
- The Dashboard exposes simple endpoints to interact with the optimizer via the Flask UI.
