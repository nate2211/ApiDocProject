from __future__ import annotations
import argparse
import json
import sys
from typing import Any, Dict, Optional

import blocks
from registry import BLOCKS
import pipeline  # registers pipeline

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="promptchat-apidoc", description="PromptChat direct-first API documentation builder.")
    available = ", ".join(BLOCKS.names()) or "(none)"
    p.add_argument("block", nargs="?", default="apidoc", help=f"Block to run. Available: {available}")
    p.add_argument("prompt", nargs="?", default=None, help="Input query text. Reads stdin if omitted and no query_file is supplied.")
    p.add_argument("--extra", action="append", default=[], help="key=val, supports name.key=val and all.key=val")
    p.add_argument("--json", action="store_true", help="Print JSON with metadata")
    p.add_argument("--list-blocks", action="store_true", help="List registered blocks and exit")
    return p

def _read_payload(arg: Optional[str], extras: Dict[str, Dict[str, Any]], block_name: str) -> str:
    if arg is not None:
        return arg
    block_params = extras.get(block_name.lower(), {})
    all_params = extras.get("all", {})
    pipe = extras.get("pipeline", {})
    if block_params.get("query_file") or all_params.get("query_file") or any(str(k).endswith(".query_file") for k in pipe.keys()):
        return ""
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ""

def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_blocks:
        print("\n".join(BLOCKS.names()))
        return 0

    try:
        extras = blocks.parse_extras(args.extra)
    except Exception as e:
        parser.error(f"Failed to parse --extra: {e}")
        return 2

    payload = _read_payload(args.prompt, extras, args.block)
    blocks.ensure_app_dirs()

    try:
        blk = BLOCKS.create(args.block)
        params: Dict[str, Any] = {}
        params.update(extras.get("all", {}))
        params.update(extras.get(args.block.lower(), {}))
        if args.block.lower() == "pipeline":
            params["_gui_extras_passthrough"] = extras
        result, meta = blk.execute(payload, params=params)
        if args.json:
            print(json.dumps({"block": args.block, "metadata": meta, "result": result}, indent=2, ensure_ascii=False, default=str))
        else:
            print(result, end="")
        return 0
    except Exception as e:
        print(f"Unexpected error in block '{args.block}': {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
