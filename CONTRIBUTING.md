# Contributing

Contributions are welcome through issues and pull requests.

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
make check
make test
```

Changes to the Django starter should also be applied to the bundled scaffold under
`src/django_ninja_starter/template/`. Add or update tests for behavior changes. Before
opening a pull request, run `make check`, `make test`, and `make package`.

## MCP servers

`.mcp.json` declares two optional MCP servers. They are developer tooling, not
project dependencies: nothing in `src/` imports them, and the suite does not need
them. Claude Code asks before enabling a project's servers on first use.

| Server | What it is for |
| --- | --- |
| [`codebase-memory-mcp`](https://github.com/CtrlAltDevelop/codebase-memory-mcp) | A queryable graph of this codebase. Answers "who calls this?" and "does every transport go through the service?" structurally, rather than by grepping and hoping the pattern was right |
| [`headroom`](https://github.com/CtrlAltDevelop/headroom) | Compresses large tool output before it reaches the model |

Install them however you like — each project documents several routes; the
`.mcp.json` here names the bare commands, so anything that puts them on `PATH`
works:

```bash
pip install codebase-memory-mcp
pip install 'headroom-ai[all]'
```

`codebase-memory-mcp` indexes into `~/.cache/codebase-memory-mcp` and writes
nothing into the repository. It is worth re-indexing after a large change, since
the graph is a snapshot rather than a live view.

**Headroom's MCP tools are a no-op on their own.** The compression happens in its
proxy, and with none running `headroom_compress` returns the input unchanged at
0% saved rather than failing — so check `headroom doctor` before concluding
anything about how much it saved. Starting the proxy routes your LLM traffic
through a local process (`headroom proxy`, then `headroom wrap claude`), which is
a per-developer decision and deliberately not configured here.
