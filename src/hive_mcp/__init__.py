"""hive_mcp — local-first personal memory for AI agents, served over MCP.

Three modules, deliberately separate:

* :mod:`hive_mcp.store` — the memory engine itself. A SQLite file on the user's
  disk: ``propose`` queues, ``recall`` ranks, nothing is confirmed without the
  human. Pure standard library.
* :mod:`hive_mcp.stdio` — the MCP server. Serves :mod:`hive_mcp.store`, and
  prefers the Hive app when it is running (see ``HIVE_MODE``).
* :mod:`hive_mcp.cli` — the human's half. Reviewing proposals and confirming
  them is deliberately *not* reachable over MCP.

None of these is imported here. Importing ``hive_mcp.stdio`` at package level
makes ``python -m hive_mcp.stdio`` emit ``RuntimeWarning: 'hive_mcp.stdio'
found in sys.modules after import of package 'hive_mcp'`` on stderr, because
the submodule is already loaded by the time runpy executes it. A stdio server
must keep stderr free of noise: hosts and directory crawlers capture stderr to
report "server errors", and a warning that appears on every run trains them to
ignore the channel that matters.

Use ``memory-hive-mcp`` and ``memory-hive-mcp-cli``, or
``from hive_mcp.stdio import main``.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
