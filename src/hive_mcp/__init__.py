"""hive_mcp — MCP stdio bridge for Memory Hive (local-first personal memory).

The bridge lives in :mod:`hive_mcp.stdio` and is deliberately **not** imported
here.

Importing it at package level makes ``python -m hive_mcp.stdio`` emit
``RuntimeWarning: 'hive_mcp.stdio' found in sys.modules after import of package
'hive_mcp', but prior to execution of 'hive_mcp.stdio'`` on stderr, because the
submodule is already loaded by the time runpy executes it. A stdio server must
keep stderr free of noise: hosts and directory crawlers capture stderr to report
"server errors", and a warning that appears on every run trains them to ignore
the channel that matters.

Use ``from hive_mcp.stdio import main``, or the ``memory-hive-mcp`` command.
"""

__version__ = "0.1.6"

__all__ = ["__version__"]
