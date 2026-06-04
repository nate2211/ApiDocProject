# PromptChat APIDoc Direct Routes

This version avoids generic web browsing by default. It resolves queries directly to known documentation URLs first.

## What changed

The `apidoc` block now uses direct resolvers:

- Python official docs:
  - `python: pathlib Path` -> `https://docs.python.org/3/library/pathlib.html`
  - `python: asyncio Task` -> `https://docs.python.org/3/library/asyncio.html`
- Python packages:
  - PyPI JSON metadata direct request
  - known docs direct routes for requests, numpy, pandas, Flask, FastAPI, SQLAlchemy, BeautifulSoup, PyQt5, pytest, pydantic
- C# / .NET:
  - `dotnet: System.Net.Http.HttpClient SendAsync` -> Microsoft Learn type/method URLs
  - C# language topics route to Microsoft Learn language docs
- Monero mining:
  - GetMonero daemon/wallet RPC docs
  - GetMonero mining user guides
  - P2Pool GitHub README/raw docs
  - XMRig official docs/GitHub README
  - RandomX GitHub README/spec

Search fallback is off by default:

```text
apidoc.search_fallback=false
```

This means the app does not call DuckDuckGo unless you explicitly enable fallback.

## Run GUI

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python gui.py
```

## Run direct-first CLI

```powershell
python main.py apidoc --extra apidoc.query_file=queries/mixed_direct_queries.txt --extra apidoc.profile=all --extra apidoc.out_path=out/apidocs_direct.md --extra apidoc.search_fallback=false
```

## Run Monero direct docs

```powershell
python main.py apidoc --extra apidoc.query_file=queries/monero_direct_queries.txt --extra apidoc.profile=monero --extra apidoc.out_path=out/monero_direct_apidocs.md --extra apidoc.search_fallback=false
```

## Enable fallback search only when needed

```powershell
python main.py apidoc --extra apidoc.query_file=queries/mixed_direct_queries.txt --extra apidoc.search_fallback=true
```

## Useful query prefixes

```text
python: pathlib Path methods
python-packages: requests Session timeout API
csharp: C# async await Task documentation
dotnet: System.Net.Http.HttpClient SendAsync API
monero: get_block_template RPC documentation
url: https://docs.python.org/3/library/pathlib.html
```

## Registered blocks

```text
apidoc
apidoc_parse
apidoc_queries
apidoc_discover
apidoc_fetch
apidoc_markdown
apidoc_profiles
pipeline
```
