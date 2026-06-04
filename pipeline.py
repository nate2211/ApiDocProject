from __future__ import annotations
import json
import sys as _sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Tuple, List

from registry import BLOCKS
import blocks

@dataclass
class ChatPipelineBlock(blocks.BaseBlock):
    _meta_chain: List[Dict[str, Any]] = field(default_factory=list)

    def _get_all_extras(self, params: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        if "_gui_extras_passthrough" in params:
            return params["_gui_extras_passthrough"]
        raw: List[str] = []
        argv = _sys.argv[1:]
        i = 0
        while i < len(argv):
            if argv[i] == "--extra" and i + 1 < len(argv):
                raw.append(argv[i + 1]); i += 2
            else:
                i += 1
        return blocks.parse_extras(raw)

    def _pipeline_params(self, all_extras: Dict[str, Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
        merged = {}
        merged.update(all_extras.get("pipeline", {}))
        merged.update(params or {})
        return merged

    def _resolve_stages(self, pipe: Dict[str, Any], all_extras: Dict[str, Dict[str, Any]]) -> List[str]:
        raw = pipe.get("stages") or pipe.get("pipeline") or pipe.get("pipe") or all_extras.get("pipeline", {}).get("stages")
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Missing pipeline.stages. Example: --extra pipeline.stages='apidoc_parse|apidoc_discover|apidoc_fetch|apidoc_markdown'")
        return [s.strip() for s in raw.split("|") if s.strip()]

    def _stage_params(self, stage: str, all_extras: Dict[str, Dict[str, Any]], pipe: Dict[str, Any]) -> Dict[str, Any]:
        merged = {}
        merged.update(all_extras.get("all", {}))
        merged.update(all_extras.get(stage.lower(), {}))
        prefix = f"{stage}."
        for k, v in pipe.items():
            if k.startswith(prefix):
                merged[k[len(prefix):]] = v
        return merged

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        all_extras = self._get_all_extras(params or {})
        pipe = self._pipeline_params(all_extras, params or {})
        stages = self._resolve_stages(pipe, all_extras)
        stop_on_error = bool(pipe.get("stop_on_error", True))
        debug = bool(pipe.get("debug", False))
        current: Any = payload
        self._meta_chain.clear()

        for stage in stages:
            blk = BLOCKS.create(stage)
            stage_params = self._stage_params(stage, all_extras, pipe)
            meta = {"stage": stage}
            started = time.time()
            if debug:
                text = current if isinstance(current, str) else json.dumps(current, ensure_ascii=False, default=str)
                meta["in_len"] = len(text); meta["in_preview"] = text[:240]
            try:
                out, m = blk.execute(current, params=stage_params)
                meta.update(m or {})
                meta["elapsed_sec"] = round(time.time() - started, 6)
                if debug:
                    text = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
                    meta["out_len"] = len(text); meta["out_preview"] = text[:360]
                self._meta_chain.append(meta)
                current = out
                if stop_on_error and meta.get("error"):
                    break
            except Exception as e:
                meta.update({"error": "stage_failed", "exception": repr(e), "elapsed_sec": round(time.time() - started, 6)})
                self._meta_chain.append(meta)
                if stop_on_error:
                    break

        out = current if isinstance(current, str) else json.dumps(current, indent=2, ensure_ascii=False, default=str)
        return out, {"type": "chat-pipeline", "stages": stages, "chain": self._meta_chain, "stop_on_error": stop_on_error, "debug": debug}

    def get_params_info(self) -> Dict[str, Any]:
        return {
            "stages": "apidoc_parse|apidoc_discover|apidoc_fetch|apidoc_markdown",
            "stop_on_error": True,
            "debug": False,
            "apidoc_parse.query_file": "queries/mixed_direct_queries.txt",
            "apidoc_discover.profile": "all",
            "apidoc_discover.search_fallback": False,
            "apidoc_fetch.max_pages_per_query": 5,
            "apidoc_markdown.out_path": "out/apidocs_direct.md",
        }

BLOCKS.register("pipeline", ChatPipelineBlock)
