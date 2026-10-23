"""hive_mcp.cli — the human's half of the loop.

The MCP tool surface has no ``approve`` action, by design: an agent may propose
but only a person may confirm. That gate is worthless if the person has no way
to open it, so the gate lives here, in a terminal, run by whoever owns the
machine. Nothing in this module is reachable over MCP.

Typical use::

    $ memory-hive-mcp-cli list
      2 entries awaiting approval
        mem_1791972802_qw4y  2 minutes ago  current
          "The deploy key rotates on the first Monday of each month."

    $ memory-hive-mcp-cli approve mem_1791972802_qw4y
      approved 1 entry

    $ memory-hive-mcp-cli search deploy
      1 result
        mem_1791972802_qw4y  0.87  current  "The deploy key rotates on the ..."

Commands: list, show, approve, reject, search, forget, restore, stats, export,
path. Exit code is 0 on success and 1 on a caller error, so it composes with
``&&`` in a shell.
"""

import argparse
import json
import sys

from hive_mcp import __version__
from hive_mcp.store import (
    PENDING,
    Store,
    StoreError,
    default_db_path,
)


def _store(args):
    return Store(getattr(args, "db", None))


def _print(text=""):
    sys.stdout.write(text + "\n")


def cmd_list(args):
    store = _store(args)
    data = store.list_pending(limit=args.limit)
    if not data["total"]:
        _print("nothing awaiting approval")
        return 0
    _print("%d entr%s awaiting approval" % (data["total"], "y" if data["total"] == 1 else "ies"))
    for entry in data["pending"]:
        _print("  %s  %s  %s" % (entry["id"], entry["created"] or "", entry["vault"]))
        _print('    "%s"' % entry["excerpt"])
    if data["total"] > data["returned"]:
        _print("  ... %d more (raise --limit)" % (data["total"] - data["returned"]))
    _print("")
    _print("approve: memory-hive-mcp-cli approve <id>   reject: memory-hive-mcp-cli reject <id>")
    return 0


def cmd_show(args):
    store = _store(args)
    data = store.get_by_id(args.id)
    _print("id      %s" % data["id"])
    _print("state   %s" % data["state"])
    _print("vault   %s" % data["vault"])
    _print("created %s" % (data["created"] or ""))
    if data["confirmed_at"]:
        _print("confirm %s" % data["confirmed_at"])
    if data["tags"]:
        _print("tags    %s" % " ".join(data["tags"]))
    _print("title   %s" % data["title"])
    _print("")
    _print(data["content"])
    return 0


def cmd_approve(args):
    store = _store(args)
    ids = store.approve(ids=None if args.all else args.ids, all_pending=args.all)
    if not ids:
        _print("nothing to approve")
        return 0
    _print("approved %d entr%s" % (len(ids), "y" if len(ids) == 1 else "ies"))
    for mem_id in ids:
        _print("  %s" % mem_id)
    _print("")
    _print("these are now searchable by recall")
    return 0


def cmd_reject(args):
    store = _store(args)
    for mem_id in args.ids:
        store.drop_pending(mem_id)
        _print("dropped %s" % mem_id)
    return 0


def cmd_search(args):
    store = _store(args)
    data = store.recall(args.query, limit=args.limit, vaults=args.vault or None)
    if not data["results"]:
        _print("no confirmed memory matched %r" % args.query)
        if data.get("note"):
            _print(data["note"])
        return 0
    _print("%d result%s" % (data["total"], "" if data["total"] == 1 else "s"))
    for entry in data["results"]:
        _print("  %s  %.2f  %s" % (entry["id"], entry["score"], entry["vault"]))
        _print('    "%s"' % entry["content"][:120].replace("\n", " "))
    return 0


def cmd_forget(args):
    store = _store(args)
    for mem_id in args.ids:
        data = store.forget(mem_id)
        _print("forgot %s (reversible with: memory-hive-mcp-cli restore %s)" % (mem_id, mem_id))
    return 0


def cmd_restore(args):
    store = _store(args)
    for mem_id in args.ids:
        store.restore(mem_id)
        _print("restored %s" % mem_id)
    return 0


def cmd_stats(args):
    store = _store(args)
    data = store.stats()
    _print("database  %s" % data["database"])
    _print("exists    %s" % ("yes" if data["exists"] else "no"))
    _print("encrypted no (the local store is plain SQLite; the Hive app adds encryption)")
    _print("confirmed %d" % data["memories"]["confirmed"])
    _print("pending   %d" % data["memories"]["pending"])
    _print("forgotten %d" % data["memories"]["discarded"])
    _print("board     %d" % data["board_messages"])
    return 0


def cmd_export(args):
    store = _store(args)
    rows = store.conn.execute(
        "SELECT id, state, title, content, vault, tags, created, confirmed_at"
        " FROM memories ORDER BY created ASC"
    ).fetchall()
    payload = {
        "exported_by": "memory-hive-mcp-cli %s" % __version__,
        "mode": "local",
        "memories": [
            {
                "id": r["id"],
                "state": r["state"],
                "title": r["title"],
                "content": r["content"],
                "vault": r["vault"],
                "tags": (r["tags"] or "").split(),
                "created": r["created"],
                "confirmed_at": r["confirmed_at"],
            }
            for r in rows
        ],
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        _print("wrote %d memories to %s" % (len(payload["memories"]), args.out))
    else:
        _print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_path(args):
    _print(str(default_db_path()))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="memory-hive-mcp-cli",
        description=(
            "Review and confirm what your agents proposed. The MCP surface can "
            "only propose; confirming is a human action and lives here."
        ),
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--db", help="path to the memory database (default: %s)" % default_db_path())
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("list", help="show entries awaiting approval")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="print one entry in full")
    p.add_argument("id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("approve", help="confirm entries so recall can find them")
    p.add_argument("ids", nargs="*")
    p.add_argument("--all", action="store_true", help="approve everything pending")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="permanently drop entries that were never confirmed")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("search", help="search confirmed memory")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--vault", action="append")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("forget", help="soft-delete a confirmed entry (reversible)")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_forget)

    p = sub.add_parser("restore", help="undo forget")
    p.add_argument("ids", nargs="+")
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser("stats", help="counts and database location")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("export", help="dump every entry as JSON")
    p.add_argument("--out")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("path", help="print the database path")
    p.set_defaults(func=cmd_path)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except StoreError as exc:
        sys.stderr.write("error: %s\n" % exc)
        return 1
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
