#!/usr/bin/env python3
"""MCP_Forge — scaffold a runnable Model Context Protocol server from the CLI.

MCP_Forge is a zero-dependency Python command-line tool that generates a
working MCP (Model Context Protocol) server — a JSON-RPC-over-stdio process
with tools/list and tools/call handlers, per-tool input-schema validation,
and a built-in `echo` example tool — then lets you extend the scaffold with
new tool definitions and validate tool-schema spec files before wiring them
up to a real agent.

Domains: AI Agents · Developer Tools · MCP Servers.
"""
import argparse
import json
import os
import re
import sys

# A tool name must be a simple identifier so it maps cleanly to a JSON key,
# a handler name in server.py, and a directory name in `init`.
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*$")

# The JSON Schema subset both the scaffolded server and `validate` understand.
SCHEMA_TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}

# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _success(args, message, **extra):
    """Print a success result in the requested format; return exit code 0."""
    if getattr(args, "format", "text") == "json":
        print(json.dumps({"ok": True, **extra}))
    else:
        print(message)
    return 0


def _fail(args, message, **extra):
    """Print an error in the requested format; return exit code 1."""
    if getattr(args, "format", "text") == "json":
        print(json.dumps({"ok": False, "error": message, **extra}))
    else:
        print("Error: " + message, file=sys.stderr)
    return 1


def _write(path, content):
    """Write a file, creating parent directories as needed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _read_json(path):
    """Read and parse a JSON file; return (data, error_message)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except OSError as exc:
        return None, "cannot read %s: %s" % (path, exc)
    except json.JSONDecodeError as exc:
        return None, "invalid JSON in %s: %s" % (path, exc)


# ---------------------------------------------------------------------------
# Schema validation (static, used by the `validate` subcommand)
# ---------------------------------------------------------------------------


def validate_schema_static(schema, label):
    """Check a schema object's structure; return a list of error strings."""
    errors = []
    if not isinstance(schema, dict):
        return ["%s: schema must be an object" % label]
    stype = schema.get("type")
    if stype is None:
        errors.append("%s.type: required" % label)
    elif stype not in SCHEMA_TYPES:
        errors.append(
            "%s.type: invalid type %r (valid: %s)"
            % (label, stype, ", ".join(sorted(SCHEMA_TYPES)))
        )
    props = schema.get("properties")
    if props is not None:
        if not isinstance(props, dict):
            errors.append("%s.properties: must be an object" % label)
        else:
            for pname, pschema in props.items():
                errors.extend(
                    validate_schema_static(pschema, "%s.properties.%s" % (label, pname))
                )
    required = schema.get("required")
    if required is not None and not isinstance(required, list):
        errors.append("%s.required: must be an array" % label)
    items = schema.get("items")
    if items is not None:
        errors.extend(validate_schema_static(items, "%s.items" % label))
    return errors


def validate_tool(tool, index):
    """Validate a single tool definition; return a list of error strings."""
    label = "tools[%d]" % index
    if not isinstance(tool, dict):
        return ["%s: tool must be an object" % label]
    errors = []
    name = tool.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("%s.name: required non-empty string" % label)
    elif not NAME_RE.fullmatch(name):
        errors.append("%s.name: invalid identifier %r" % (label, name))
    desc = tool.get("description")
    if not isinstance(desc, str) or not desc.strip():
        errors.append("%s.description: required non-empty string" % label)
    schema = tool.get("inputSchema")
    if schema is None:
        errors.append("%s.inputSchema: required" % label)
    else:
        errors.extend(validate_schema_static(schema, "%s.inputSchema" % label))
    return errors


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_init(args):
    """Create a new server directory with a runnable MCP server scaffold."""
    name = args.name
    if not NAME_RE.fullmatch(name):
        return _fail(
            args,
            "invalid server name %r (use letters, digits, '_' or '-')" % name,
        )
    target = os.path.abspath(name)
    if os.path.exists(target):
        if not args.force:
            return _fail(args, "%r already exists (use --force to overwrite)" % name)
        if not os.path.isdir(target):
            return _fail(args, "%r exists and is not a directory" % name)
    else:
        os.makedirs(target)

    _write(os.path.join(target, "server.py"), _SERVER_TEMPLATE.replace("__NAME__", name))
    _write(
        os.path.join(target, "tools.json"),
        json.dumps(_default_tools(), indent=2) + "\n",
    )
    _write(os.path.join(target, "README.md"), _SCAFFOLD_README.replace("__NAME__", name))
    os.chmod(os.path.join(target, "server.py"), 0o755)

    return _success(
        args,
        "Created MCP server scaffold %r in ./%s/ "
        "(server.py, tools.json, README.md)" % (name, name),
        name=name,
        path=name,
        files=["server.py", "tools.json", "README.md"],
        tools=[t["name"] for t in _default_tools()["tools"]],
    )


def cmd_add_tool(args):
    """Add a new tool definition to an existing scaffold's tools.json."""
    directory = args.dir or "."
    manifest_path = os.path.join(directory, "tools.json")
    if not os.path.isfile(manifest_path):
        return _fail(
            args, "no tools.json in %s (run `init` first, or use --dir)" % directory
        )
    data, err = _read_json(manifest_path)
    if err:
        return _fail(args, err)
    tools = data.get("tools") if isinstance(data, dict) else None
    if not isinstance(tools, list):
        return _fail(args, "%s has no 'tools' list" % manifest_path)

    name = args.name
    if not NAME_RE.fullmatch(name):
        return _fail(args, "invalid tool name %r" % name)
    description = " ".join(args.description).strip()
    if not description:
        return _fail(args, "description must not be empty")

    # Build the input schema from --param NAME:TYPE and --required NAME flags.
    properties = {}
    for spec in args.param or []:
        pname, sep, ptype = spec.partition(":")
        pname = pname.strip()
        ptype = ptype.strip() if sep else "string"
        if not pname or not NAME_RE.fullmatch(pname):
            return _fail(args, "invalid param name %r" % pname)
        if ptype not in SCHEMA_TYPES:
            return _fail(
                args,
                "invalid type %r for param %s (valid: %s)"
                % (ptype, pname, ", ".join(sorted(SCHEMA_TYPES))),
            )
        properties[pname] = {"type": ptype}
    schema = {"type": "object"}
    if properties:
        schema["properties"] = properties
    required = args.required or []
    for rname in required:
        if rname not in properties:
            return _fail(args, "--required %r is not a defined param" % rname)
    if required:
        schema["required"] = required

    tool = {"name": name, "description": description, "inputSchema": schema}
    existing = [i for i, t in enumerate(tools) if t.get("name") == name]
    if existing:
        if not args.force:
            return _fail(
                args, "tool %r already exists (use --force to replace)" % name
            )
        tools[existing[0]] = tool  # replace in place, keep position
    else:
        tools.append(tool)

    _write(manifest_path, json.dumps(data, indent=2) + "\n")
    return _success(
        args,
        "Added tool %r to %s (%d tool(s) total)" % (name, manifest_path, len(tools)),
        name=name,
        path=manifest_path,
        tool_count=len(tools),
    )


def cmd_validate(args):
    """Validate a tool-schema spec file (single tool or {tools:[...]})."""
    path = args.spec
    if not os.path.isfile(path):
        return _fail(args, "file not found: %s" % path, file=path, valid=False)
    data, err = _read_json(path)
    if err:
        return _fail(args, err, file=path, valid=False)

    if isinstance(data, dict) and "tools" in data:
        if not isinstance(data["tools"], list):
            return _fail(args, "'tools' must be a list", file=path, valid=False)
        tools = data["tools"]
    else:
        tools = [data]

    errors = []
    for i, tool in enumerate(tools):
        errors.extend(validate_tool(tool, i))

    if errors:
        if getattr(args, "format", "text") != "json":
            for e in errors:
                print("  - " + e, file=sys.stderr)
        return _fail(
            args,
            "%s is invalid (%d error(s))" % (path, len(errors)),
            file=path,
            valid=False,
            errors=errors,
        )
    return _success(
        args,
        "OK: %s (%d tool(s), valid)" % (path, len(tools)),
        file=path,
        valid=True,
        tool_count=len(tools),
    )


# ---------------------------------------------------------------------------
# CLI parser
# ---------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(
        prog="MCP_Forge",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version="MCP_Forge 1.0.0")

    # Shared parent parser so --format works after every subcommand. It must
    # NOT be added to the top-level parser (that breaks the default).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    sp_init = sub.add_parser(
        "init", parents=[common], help="Create a new MCP server scaffold"
    )
    sp_init.add_argument("name", help="Server name (directory to create)")
    sp_init.add_argument(
        "--force", action="store_true", help="Overwrite an existing scaffold"
    )
    sp_init.set_defaults(func=cmd_init)

    sp_add = sub.add_parser(
        "add-tool", parents=[common], help="Add a tool to an existing scaffold"
    )
    sp_add.add_argument("name", help="Tool name")
    sp_add.add_argument("description", nargs="+", help="Tool description")
    sp_add.add_argument(
        "--dir", default=".", help="Scaffold directory (default: current dir)"
    )
    sp_add.add_argument(
        "--param",
        action="append",
        metavar="NAME:TYPE",
        help="Input property (repeatable), e.g. --param text:string",
    )
    sp_add.add_argument(
        "--required",
        action="append",
        metavar="NAME",
        help="Mark a param required (repeatable)",
    )
    sp_add.add_argument(
        "--force", action="store_true", help="Replace an existing tool of the same name"
    )
    sp_add.set_defaults(func=cmd_add_tool)

    sp_val = sub.add_parser(
        "validate", parents=[common], help="Validate a tool-schema spec file"
    )
    sp_val.add_argument("spec", help="Path to SPEC.json")
    sp_val.set_defaults(func=cmd_validate)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


# ---------------------------------------------------------------------------
# Scaffold templates (emitted by `init`)
# ---------------------------------------------------------------------------


def _default_tools():
    """Seed tool manifest: the example `echo` tool."""
    return {
        "tools": [
            {
                "name": "echo",
                "description": "Echo the provided text back to the caller.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "Text to echo."}
                    },
                    "required": ["text"],
                },
            }
        ]
    }


# The scaffolded server is a self-contained, stdlib-only JSON-RPC-over-stdio
# MCP server. __NAME__ is replaced with the server name at `init` time.
_SERVER_TEMPLATE = r'''#!/usr/bin/env python3
"""__NAME__ — a zero-dependency JSON-RPC-over-stdio MCP server.

Scaffolded by MCP_Forge. Tool definitions live in tools.json. Real behavior
lives in HANDLERS; any tool without a dedicated handler falls back to the
default passthrough handler, which echoes its arguments back as JSON text.
"""
import json
import os
import sys

NAME = "__NAME__"
VERSION = "0.1.0"

# Resolve tools.json relative to this file, not the caller's working directory.
TOOLS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools.json")


def load_tools():
    """Read tool definitions from tools.json (fall back to an empty list)."""
    try:
        with open(TOOLS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("tools", []) if isinstance(data, dict) else []


# ---- JSON Schema subset validation ---------------------------------------

SCHEMA_TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}


def _type_name(value):
    """Human-readable type name; bool is reported separately from int."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches(value, stype):
    if stype == "string":
        return isinstance(value, str)
    if stype == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if stype == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if stype == "boolean":
        return isinstance(value, bool)
    if stype == "object":
        return isinstance(value, dict)
    if stype == "array":
        return isinstance(value, list)
    if stype == "null":
        return value is None
    return True  # unknown type -> no constraint


def validate_schema(value, schema, path="$"):
    """Validate value against a JSON Schema subset; return a list of errors."""
    errors = []
    if not isinstance(schema, dict):
        return errors
    stype = schema.get("type")
    if stype in SCHEMA_TYPES and not _matches(value, stype):
        errors.append("%s: expected %s, got %s" % (path, stype, _type_name(value)))
        return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append("%s: value not in enum %s" % (path, json.dumps(schema["enum"])))
    if stype == "object" or "properties" in schema:
        if not isinstance(value, dict):
            errors.append("%s: expected object, got %s" % (path, _type_name(value)))
            return errors
        for key, subschema in (schema.get("properties") or {}).items():
            if key in value:
                errors.extend(
                    validate_schema(value[key], subschema, "%s.%s" % (path, key))
                )
        for key in schema.get("required", []):
            if key not in value:
                errors.append("%s.%s: required property is missing" % (path, key))
    elif stype == "array" and isinstance(value, list):
        items = schema.get("items", {})
        for idx, item in enumerate(value):
            errors.extend(validate_schema(item, items, "%s[%d]" % (path, idx)))
    return errors


# ---- Tool handlers --------------------------------------------------------

def echo_handler(args):
    """The example tool: return the provided text unchanged."""
    return args.get("text", "")


HANDLERS = {
    "echo": echo_handler,
}


def default_handler(args):
    """Fallback for tools without a dedicated handler: dump the arguments."""
    return json.dumps(args, indent=2, sort_keys=True)


# ---- JSON-RPC plumbing ----------------------------------------------------

def result_response(rid, result):
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def error_response(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(req, tools):
    """Dispatch one JSON-RPC request to the right handler."""
    if not isinstance(req, dict) or "method" not in req:
        rid = req.get("id") if isinstance(req, dict) else None
        return error_response(rid, -32600, "Invalid Request")
    method = req["method"]
    rid = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return result_response(rid, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": NAME, "version": VERSION},
        })
    if method.startswith("notifications/"):
        return None  # notifications expect no response
    if method == "ping":
        return result_response(rid, {})
    if method == "tools/list":
        return result_response(rid, {"tools": tools})
    if method == "tools/call":
        return call_tool(params, tools, rid)
    return error_response(rid, -32601, "Method not found: %s" % method)


def call_tool(params, tools, rid):
    name = params.get("name")
    arguments = params.get("arguments") or {}
    tool = next((t for t in tools if t.get("name") == name), None)
    if tool is None:
        return error_response(rid, -32602, "Unknown tool: %s" % name)
    if not isinstance(arguments, dict):
        return error_response(rid, -32602, "arguments must be an object")
    errors = validate_schema(arguments, tool.get("inputSchema") or {})
    if errors:
        return result_response(rid, {
            "content": [{
                "type": "text",
                "text": "Invalid arguments:\n" + "\n".join("  - " + e for e in errors),
            }],
            "isError": True,
        })
    handler = HANDLERS.get(name, default_handler)
    text = handler(arguments)
    if not isinstance(text, str):
        text = json.dumps(text)
    return result_response(rid, {
        "content": [{"type": "text", "text": text}],
        "isError": False,
    })


def main():
    tools = load_tools()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue  # skip non-JSON input lines
        resp = handle(req, tools)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
'''


_SCAFFOLD_README = r'''# __NAME__ MCP server

A zero-dependency JSON-RPC-over-stdio [Model Context Protocol](https://modelcontextprotocol.io) server, scaffolded by MCP_Forge.

## Files

- `server.py` — the server. Implements `initialize`, `tools/list`, and `tools/call`.
- `tools.json` — tool definitions (name, description, inputSchema).

## Run

```bash
python3 server.py
```

It reads JSON-RPC requests from stdin and writes responses to stdout, one JSON object per line.

## Try it

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"0"}}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"echo","arguments":{"text":"hello"}}}' \
  | python3 server.py
```

## Add tools

```bash
python3 ../MCP_Forge.py add-tool reverse "Reverse a string" --param text:string --required text --dir .
```

Or edit `tools.json` directly and add a matching handler to `server.py`.
'''


if __name__ == "__main__":
    sys.exit(main())
