# MCP_Forge 🛠️

**Scaffold a runnable Model Context Protocol server from the command line — zero dependencies, pure Python stdlib.**

MCP_Forge generates a working MCP server (JSON-RPC over stdio with `tools/list` and `tools/call` handlers, per-tool input-schema validation, and a built-in `echo` example tool), then lets you extend it with new tool definitions and validate tool-schema specs before wiring them up to an agent. No pip installs, no SDKs, no lock-in — just a self-contained server you can read in five minutes.

## Why it exists

MCP is the emerging standard for giving AI agents tools, but the official SDKs pull in a dependency tree and hide the protocol. MCP_Forge lowers the floor: it emits a single-file, stdlib-only server that speaks the protocol directly, so you can learn the wire format, prototype tools fast, and ship a server that runs anywhere Python 3 does.

## One tool, many domains

| Domain | What MCP_Forge does |
|---|---|
| 🤖 **AI Agents** | Spins up an MCP server your agent can call — `initialize`, `tools/list`, `tools/call`. |
| 🧰 **Developer Tools** | Generates a complete, runnable scaffold: server, tool manifest, and docs. |
| 🔌 **Integrations** | New tool definitions (`add-tool`) and spec validation (`validate`) before wiring anything up. |
| 🧪 **Testing / QA** | Validates tool-schema spec files as a CI-friendly gate (exit 0 / exit 1). |

## Install

```bash
git clone git@github.com:realMNohgee/MCP_Forge.git
cd MCP_Forge
python3 MCP_Forge.py --help
```

## Quick start

```bash
# 1. Scaffold a new server
python3 MCP_Forge.py init my_server

# 2. Run it — feed it JSON-RPC and watch it answer
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"text":"hello"}}}' \
  | python3 my_server/server.py

# 3. Add a new tool definition
python3 MCP_Forge.py add-tool reverse "Reverse a string" --param text:string --required text --dir my_server

# 4. Validate a tool-schema spec
python3 MCP_Forge.py validate my_server/tools.json
```

Every subcommand supports `--format json` for machine-readable output.

## License

MIT — see [LICENSE](LICENSE).

---

🧰 [Tool on Hermtica Marketplace](https://hermtica.com/marketplace)
