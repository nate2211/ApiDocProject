# apidoc_block.py
# Rewritten full block file.
#
# Default behavior is now the advanced APIDoc report output:
#   - coverage/intelligence summary
#   - profile/topic/source/host health
#   - gap detection and dedupe notes
#   - grouped results and saved asset index
#
# Existing public block names and engine signatures are preserved.
# Crash-safe v4 patch: bounded fetches, no default crawl, streaming HTTP, HTMLParser extraction.
# ---------------------------------------------------------------------------

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

from registry import BLOCKS

APP_DIR = os.path.join(os.path.expanduser("~"), ".promptchat")
MEMORY_PATH = os.path.join(APP_DIR, "memory.json")
HISTORY_PATH = os.path.join(APP_DIR, "history.json")

def ensure_app_dirs() -> None:
    os.makedirs(APP_DIR, exist_ok=True)
    for p in (MEMORY_PATH, HISTORY_PATH):
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8") as f:
                f.write("{}")

def _coerce(v: str) -> Any:
    s = str(v).strip()
    low = s.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("none", "null"):
        return None
    try:
        if re.fullmatch(r"[-+]?\d+", s):
            return int(s)
        if re.fullmatch(r"[-+]?\d+\.\d+", s):
            return float(s)
    except Exception:
        pass
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        return s[1:-1]
    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        try:
            return json.loads(s)
        except Exception:
            return s
    return s

def parse_extras(items: List[str]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        group, key = k.split(".", 1) if "." in k else ("all", k)
        out.setdefault(group.strip().lower(), {})[key.strip()] = _coerce(v)
    return out

@dataclass
class BaseBlock:
    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        raise NotImplementedError
    def get_params_info(self) -> Dict[str, Any]:
        return {}

class Memory:
    @staticmethod
    def load() -> Dict[str, Any]:
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    @staticmethod
    def save(data: Dict[str, Any]) -> None:
        ensure_app_dirs()
        with open(MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

_UA = "PromptChat-APIDoc/4.0 (+direct-or-search docs fetcher; consent based)"

PREFIX_PROFILE = {'python': 'python',
 'py': 'python',
 'stdlib': 'python',
 'pep': 'python',
 'python-packages': 'python-packages',
 'python_packages': 'python-packages',
 'packages': 'python-packages',
 'package': 'python-packages',
 'pip': 'python-packages',
 'pypi': 'python-packages',
 'csharp': 'csharp',
 'c#': 'csharp',
 'cs': 'csharp',
 'dotnet': 'dotnet',
 '.net': 'dotnet',
 'aspnet': 'csharp',
 'asp.net': 'csharp',
 'cpp': 'cpp',
 'c++': 'cpp',
 'cplusplus': 'cpp',
 'cxx': 'cpp',
 'cc': 'cpp',
 'msvc': 'cpp',
 'stl': 'cpp',
 'cmake': 'cpp',
 'win32': 'windows',
 'windows': 'windows',
 'windows-cpp': 'windows',
 'web': 'web',
 'html': 'web',
 'css': 'web',
 'javascript': 'javascript',
 'js': 'javascript',
 'node': 'node',
 'nodejs': 'node',
 'typescript': 'javascript',
 'ts': 'javascript',
 'rust': 'rust',
 'cargo': 'rust',
 'go': 'go',
 'golang': 'go',
 'java': 'java',
 'jvm': 'java',
 'spring': 'java',
 'kotlin': 'kotlin',
 'android': 'kotlin',
 'swift': 'swift',
 'apple': 'swift',
 'game': 'game',
 'unity': 'game',
 'unreal': 'game',
 'ue': 'game',
 'ue4': 'game',
 'ue5': 'game',
 'godot': 'game',
 'bannerlord': 'bannerlord',
 'taleworlds': 'bannerlord',
 'database': 'database',
 'db': 'database',
 'sql': 'database',
 'postgres': 'database',
 'postgresql': 'database',
 'sqlite': 'database',
 'mysql': 'database',
 'mongodb': 'database',
 'redis': 'database',
 'devops': 'devops',
 'docker': 'devops',
 'kubernetes': 'devops',
 'k8s': 'devops',
 'git': 'devops',
 'github': 'devops',
 'terraform': 'devops',
 'cloud': 'cloud',
 'aws': 'cloud',
 'azure': 'cloud',
 'gcp': 'cloud',
 'ai': 'ai',
 'ml': 'ai',
 'openai': 'ai',
 'pytorch': 'ai',
 'torch': 'ai',
 'tensorflow': 'ai',
 'opencv': 'ai',
 'sklearn': 'ai',
 'linux': 'linux',
 'kernel': 'linux',
 'man': 'linux',
 'monero': 'monero',
 'xmr': 'monero',
 'p2pool': 'monero',
 'xmrig': 'monero',
 'randomx': 'monero',
 'all': 'all'}

DOC_HOST_HINTS = ("docs.", "doc.", "learn.", "developer.", "readthedocs", "github.io", "api.", "reference.", "manual.", "dev.", "pkg.go.dev", "doc.rust-lang", "cppreference", "getmonero", "xmrig", "bannerlordapi", "docs.bannerlordmodding")
DOC_PATH_HINTS = ("/docs", "/doc", "/api", "/reference", "/manual", "/guide", "/guides", "/language-reference", "/library", "/tutorial", "/handbook", "/standard-library", "/resources/developer-guides", "/wiki", "/learn", "/book", "/ref", "/classes", "/c-api", "/javadoc", "/reference/")
BLOCKED_EXTS = (".png",".jpg",".jpeg",".gif",".svg",".webp",".ico",".zip",".tar",".gz",".whl",".exe",".css",".js",".json",".xml",".mp4",".mp3",".woff",".woff2")

DEFAULT_CONFIG = {'profiles': {'python': ['python_direct'],
              'python-packages': ['pypi_direct', 'python_package_common_direct', 'python_packaging_direct'],
              'csharp': ['microsoft_dotnet_direct', 'microsoft_csharp_direct', 'microsoft_aspnet_direct', 'microsoft_efcore_direct'],
              'dotnet': ['microsoft_dotnet_direct', 'microsoft_csharp_direct', 'microsoft_aspnet_direct', 'microsoft_efcore_direct'],
              'cpp': ['microsoft_cpp_direct', 'cppreference_direct', 'cmake_direct', 'llvm_direct'],
              'windows': ['microsoft_win32_direct', 'microsoft_cpp_direct', 'microsoft_dotnet_direct'],
              'web': ['mdn_web_direct', 'mdn_javascript_direct', 'nodejs_direct', 'react_direct', 'vue_direct', 'angular_direct'],
              'javascript': ['mdn_javascript_direct', 'nodejs_direct', 'typescript_direct'],
              'node': ['nodejs_direct', 'npm_direct'],
              'rust': ['rust_std_direct', 'rust_book_direct', 'cargo_direct'],
              'go': ['go_std_direct', 'go_dev_direct'],
              'java': ['oracle_java_direct', 'spring_direct'],
              'kotlin': ['kotlin_direct', 'android_direct'],
              'swift': ['swift_direct', 'apple_developer_direct'],
              'game': ['unity_direct', 'unreal_direct', 'godot_direct', 'bannerlord_direct'],
              'bannerlord': ['bannerlord_direct', 'butr_bannerlord_api_direct', 'microsoft_csharp_direct'],
              'database': ['postgres_direct', 'sqlite_direct', 'mysql_direct', 'mongodb_direct', 'redis_direct'],
              'devops': ['docker_direct', 'kubernetes_direct', 'git_direct', 'github_actions_direct', 'terraform_direct'],
              'cloud': ['aws_direct', 'azure_direct', 'gcp_direct'],
              'ai': ['openai_direct', 'pytorch_direct', 'tensorflow_direct', 'sklearn_direct', 'opencv_direct'],
              'linux': ['linux_man_direct', 'linux_kernel_direct'],
              'monero': ['monero_official_direct', 'p2pool_direct', 'xmrig_direct', 'randomx_direct'],
              'all': ['python_direct',
                      'pypi_direct',
                      'python_package_common_direct',
                      'python_packaging_direct',
                      'microsoft_dotnet_direct',
                      'microsoft_csharp_direct',
                      'microsoft_aspnet_direct',
                      'microsoft_efcore_direct',
                      'microsoft_cpp_direct',
                      'cppreference_direct',
                      'microsoft_win32_direct',
                      'cmake_direct',
                      'llvm_direct',
                      'mdn_web_direct',
                      'mdn_javascript_direct',
                      'nodejs_direct',
                      'typescript_direct',
                      'react_direct',
                      'vue_direct',
                      'angular_direct',
                      'rust_std_direct',
                      'rust_book_direct',
                      'cargo_direct',
                      'go_std_direct',
                      'go_dev_direct',
                      'oracle_java_direct',
                      'spring_direct',
                      'kotlin_direct',
                      'android_direct',
                      'swift_direct',
                      'apple_developer_direct',
                      'unity_direct',
                      'unreal_direct',
                      'godot_direct',
                      'bannerlord_direct',
                      'butr_bannerlord_api_direct',
                      'postgres_direct',
                      'sqlite_direct',
                      'mysql_direct',
                      'mongodb_direct',
                      'redis_direct',
                      'docker_direct',
                      'kubernetes_direct',
                      'git_direct',
                      'github_actions_direct',
                      'terraform_direct',
                      'aws_direct',
                      'azure_direct',
                      'gcp_direct',
                      'openai_direct',
                      'pytorch_direct',
                      'tensorflow_direct',
                      'sklearn_direct',
                      'opencv_direct',
                      'linux_man_direct',
                      'linux_kernel_direct',
                      'monero_official_direct',
                      'p2pool_direct',
                      'xmrig_direct',
                      'randomx_direct']},
 'sources': {'python_direct': {'display_name': 'Python Official Docs Direct',
                               'allowed_domains': ['docs.python.org', 'peps.python.org', 'packaging.python.org', 'typing.python.org', 'devguide.python.org'],
                               'resolver': 'python',
                               'profile': 'python',
                               'match_terms': ['python', 'stdlib', 'pep', 'c api', 'extension', 'embedding'],
                               'seed_urls': ['https://docs.python.org/3/',
                                             'https://docs.python.org/3/library/',
                                             'https://docs.python.org/3/reference/',
                                             'https://docs.python.org/3/c-api/',
                                             'https://peps.python.org/',
                                             'https://typing.python.org/en/latest/',
                                             'https://devguide.python.org/'],
                               'topic_urls': {'c api': ['https://docs.python.org/3/c-api/'],
                                              'pep': ['https://peps.python.org/'],
                                              'typing': ['https://typing.python.org/en/latest/', 'https://docs.python.org/3/library/typing.html'],
                                              'packaging': ['https://packaging.python.org/en/latest/']}},
             'pypi_direct': {'display_name': 'PyPI Project Metadata Direct',
                             'allowed_domains': ['pypi.org', 'readthedocs.io', 'readthedocs.org', 'github.io', 'github.com'],
                             'resolver': 'pypi',
                             'profile': 'python-packages',
                             'seed_urls': [],
                             'match_terms': ['pypi', 'pip', 'python package', 'package docs']},
             'python_package_common_direct': {'display_name': 'Known Python Package Docs Direct',
                                              'allowed_domains': ['requests.readthedocs.io',
                                                                  'numpy.org',
                                                                  'pandas.pydata.org',
                                                                  'flask.palletsprojects.com',
                                                                  'fastapi.tiangolo.com',
                                                                  'docs.sqlalchemy.org',
                                                                  'www.crummy.com',
                                                                  'beautiful-soup-4.readthedocs.io',
                                                                  'riverbankcomputing.com',
                                                                  'docs.pytest.org',
                                                                  'docs.pydantic.dev',
                                                                  'docs.djangoproject.com',
                                                                  'python-poetry.org',
                                                                  'pip.pypa.io',
                                                                  'black.readthedocs.io',
                                                                  'mypy.readthedocs.io'],
                                              'resolver': 'python_package_common',
                                              'profile': 'python-packages',
                                              'seed_urls': [],
                                              'match_terms': ['requests',
                                                              'numpy',
                                                              'pandas',
                                                              'flask',
                                                              'fastapi',
                                                              'sqlalchemy',
                                                              'beautifulsoup',
                                                              'bs4',
                                                              'pyqt5',
                                                              'pytest',
                                                              'pydantic',
                                                              'django',
                                                              'poetry',
                                                              'pip',
                                                              'black',
                                                              'mypy']},
             'python_packaging_direct': {'display_name': 'Python Packaging / PyPA Direct',
                                         'allowed_domains': ['packaging.python.org', 'pip.pypa.io', 'setuptools.pypa.io', 'python-poetry.org'],
                                         'resolver': 'topic_registry',
                                         'seed_urls': ['https://packaging.python.org/en/latest/',
                                                       'https://pip.pypa.io/en/stable/',
                                                       'https://setuptools.pypa.io/en/latest/',
                                                       'https://python-poetry.org/docs/'],
                                         'profile': 'python-packages',
                                         'match_terms': ['packaging', 'pip', 'setuptools', 'poetry', 'wheel', 'pyproject'],
                                         'topic_urls': {'pyproject': ['https://packaging.python.org/en/latest/specifications/pyproject-toml/'],
                                                        'wheel': ['https://packaging.python.org/en/latest/specifications/binary-distribution-format/'],
                                                        'pip': ['https://pip.pypa.io/en/stable/cli/'],
                                                        'poetry': ['https://python-poetry.org/docs/']},
                                         'same_host_crawl_only': True},
             'microsoft_dotnet_direct': {'display_name': 'Microsoft Learn .NET API Direct',
                                         'allowed_domains': ['learn.microsoft.com', 'docs.microsoft.com'],
                                         'resolver': 'dotnet',
                                         'profile': 'dotnet',
                                         'match_terms': ['dotnet', '.net', 'system.', 'microsoft.extensions', 'api browser'],
                                         'seed_urls': ['https://learn.microsoft.com/en-us/dotnet/api/',
                                                       'https://learn.microsoft.com/en-us/dotnet/standard/',
                                                       'https://learn.microsoft.com/en-us/dotnet/fundamentals/'],
                                         'topic_urls': {'httpclient': ['https://learn.microsoft.com/en-us/dotnet/api/system.net.http.httpclient?view=net-9.0'],
                                                        'task': ['https://learn.microsoft.com/en-us/dotnet/api/system.threading.tasks.task?view=net-9.0'],
                                                        'linq': ['https://learn.microsoft.com/en-us/dotnet/csharp/linq/']}},
             'microsoft_csharp_direct': {'display_name': 'Microsoft Learn C# Language Direct',
                                         'allowed_domains': ['learn.microsoft.com', 'docs.microsoft.com'],
                                         'resolver': 'csharp',
                                         'profile': 'csharp',
                                         'match_terms': ['c#', 'csharp', 'async', 'await', 'records', 'linq', 'nullable'],
                                         'seed_urls': ['https://learn.microsoft.com/en-us/dotnet/csharp/',
                                                       'https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/',
                                                       'https://learn.microsoft.com/en-us/dotnet/csharp/programming-guide/']},
             'microsoft_aspnet_direct': {'display_name': 'ASP.NET Core Direct',
                                         'allowed_domains': ['learn.microsoft.com'],
                                         'resolver': 'topic_registry',
                                         'seed_urls': ['https://learn.microsoft.com/en-us/aspnet/core/?view=aspnetcore-10.0',
                                                       'https://learn.microsoft.com/en-us/aspnet/core/fundamentals/apis?view=aspnetcore-10.0',
                                                       'https://learn.microsoft.com/en-us/aspnet/core/mvc/controllers/actions?view=aspnetcore-10.0'],
                                         'profile': 'csharp',
                                         'match_terms': ['asp.net', 'aspnet', 'minimal api', 'mvc', 'razor', 'blazor'],
                                         'topic_urls': {'blazor': ['https://learn.microsoft.com/en-us/aspnet/core/blazor/?view=aspnetcore-10.0'],
                                                        'minimal api': ['https://learn.microsoft.com/en-us/aspnet/core/fundamentals/minimal-apis?view=aspnetcore-10.0']},
                                         'same_host_crawl_only': True},
             'microsoft_efcore_direct': {'display_name': 'Entity Framework Core Direct',
                                         'allowed_domains': ['learn.microsoft.com'],
                                         'resolver': 'topic_registry',
                                         'seed_urls': ['https://learn.microsoft.com/en-us/ef/core/',
                                                       'https://learn.microsoft.com/en-us/ef/core/modeling/',
                                                       'https://learn.microsoft.com/en-us/ef/core/querying/'],
                                         'profile': 'csharp',
                                         'match_terms': ['efcore', 'entity framework', 'dbcontext', 'linq'],
                                         'topic_urls': {},
                                         'same_host_crawl_only': True},
             'microsoft_cpp_direct': {'display_name': 'Microsoft Learn C++ Direct',
                                      'allowed_domains': ['learn.microsoft.com', 'docs.microsoft.com'],
                                      'resolver': 'cpp_microsoft',
                                      'profile': 'cpp',
                                      'match_terms': ['c++', 'cpp', 'msvc', 'std::', 'cmake', 'win32'],
                                      'seed_urls': ['https://learn.microsoft.com/en-us/cpp/?view=msvc-170',
                                                    'https://learn.microsoft.com/en-us/cpp/cpp/cpp-language-reference?view=msvc-170',
                                                    'https://learn.microsoft.com/en-us/cpp/standard-library/cpp-standard-library-reference?view=msvc-170',
                                                    'https://learn.microsoft.com/en-us/cpp/build/reference/compiler-options?view=msvc-170',
                                                    'https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist?view=msvc-170']},
             'cppreference_direct': {'display_name': 'cppreference Direct',
                                     'allowed_domains': ['en.cppreference.com', 'cppreference.com'],
                                     'resolver': 'cppreference',
                                     'profile': 'cpp',
                                     'match_terms': ['cppreference', 'std::', 'c++', 'cpp', 'standard library'],
                                     'seed_urls': ['https://en.cppreference.com/w/cpp',
                                                   'https://en.cppreference.com/w/cpp/language',
                                                   'https://en.cppreference.com/w/cpp/header',
                                                   'https://en.cppreference.com/w/cpp/container',
                                                   'https://en.cppreference.com/w/cpp/algorithm',
                                                   'https://en.cppreference.com/w/cpp/thread',
                                                   'https://en.cppreference.com/w/cpp/io',
                                                   'https://en.cppreference.com/w/cpp/filesystem',
                                                   'https://en.cppreference.com/w/cpp/ranges']},
             'microsoft_win32_direct': {'display_name': 'Win32 / Windows API Direct',
                                        'allowed_domains': ['learn.microsoft.com'],
                                        'resolver': 'topic_registry',
                                        'seed_urls': ['https://learn.microsoft.com/en-us/windows/win32/api/',
                                                      'https://learn.microsoft.com/en-us/windows/win32/winsock/windows-sockets-start-page-2',
                                                      'https://learn.microsoft.com/en-us/windows/win32/api/winuser/',
                                                      'https://learn.microsoft.com/en-us/windows/win32/api/fileapi/',
                                                      'https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/',
                                                      'https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/',
                                                      'https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/'],
                                        'profile': 'windows',
                                        'match_terms': ['win32', 'windows api', 'winsock', 'kernel32', 'user32', 'gdi', 'wintun', 'ndis', 'driver', 'dllmain'],
                                        'topic_urls': {'winsock': ['https://learn.microsoft.com/en-us/windows/win32/winsock/windows-sockets-start-page-2'],
                                                       'driver': ['https://learn.microsoft.com/en-us/windows-hardware/drivers/'],
                                                       'ndis': ['https://learn.microsoft.com/en-us/windows-hardware/drivers/network/'],
                                                       'wdf': ['https://learn.microsoft.com/en-us/windows-hardware/drivers/wdf/'],
                                                       'dllmain': ['https://learn.microsoft.com/en-us/windows/win32/dlls/dllmain']},
                                        'same_host_crawl_only': True},
             'cmake_direct': {'display_name': 'CMake Direct',
                              'allowed_domains': ['cmake.org'],
                              'resolver': 'topic_registry',
                              'seed_urls': ['https://cmake.org/cmake/help/latest/',
                                            'https://cmake.org/cmake/help/latest/manual/cmake-commands.7.html',
                                            'https://cmake.org/cmake/help/latest/manual/cmake-generator-expressions.7.html'],
                              'profile': 'cpp',
                              'match_terms': ['cmake', 'cmakelists', 'target_link_libraries', 'find_package'],
                              'topic_urls': {},
                              'same_host_crawl_only': True},
             'llvm_direct': {'display_name': 'LLVM / Clang Direct',
                             'allowed_domains': ['llvm.org', 'clang.llvm.org'],
                             'resolver': 'topic_registry',
                             'seed_urls': ['https://llvm.org/docs/', 'https://clang.llvm.org/docs/', 'https://llvm.org/docs/LangRef.html'],
                             'profile': 'cpp',
                             'match_terms': ['llvm', 'clang', 'ir', 'compiler'],
                             'topic_urls': {},
                             'same_host_crawl_only': False},
             'mdn_javascript_direct': {'display_name': 'MDN JavaScript Reference Direct',
                                       'allowed_domains': ['developer.mozilla.org'],
                                       'resolver': 'topic_registry',
                                       'seed_urls': ['https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference',
                                                     'https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide',
                                                     'https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects'],
                                       'profile': 'javascript',
                                       'match_terms': ['javascript', 'js', 'ecmascript', 'promise', 'async', 'array', 'object'],
                                       'topic_urls': {'promise': ['https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Promise'],
                                                      'async': ['https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Statements/async_function'],
                                                      'fetch': ['https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API']},
                                       'same_host_crawl_only': True},
             'mdn_web_direct': {'display_name': 'MDN Web APIs / HTML / CSS Direct',
                                'allowed_domains': ['developer.mozilla.org'],
                                'resolver': 'topic_registry',
                                'seed_urls': ['https://developer.mozilla.org/en-US/docs/Web/API',
                                              'https://developer.mozilla.org/en-US/docs/Web/HTML',
                                              'https://developer.mozilla.org/en-US/docs/Web/CSS',
                                              'https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API',
                                              'https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API'],
                                'profile': 'web',
                                'match_terms': ['html', 'css', 'web api', 'dom', 'canvas', 'websocket', 'fetch', 'service worker'],
                                'topic_urls': {'canvas': ['https://developer.mozilla.org/en-US/docs/Web/API/Canvas_API'],
                                               'websocket': ['https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API'],
                                               'css grid': ['https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_grid_layout']},
                                'same_host_crawl_only': True},
             'nodejs_direct': {'display_name': 'Node.js API Direct',
                               'allowed_domains': ['nodejs.org'],
                               'resolver': 'topic_registry',
                               'seed_urls': ['https://nodejs.org/api/',
                                             'https://nodejs.org/api/fs.html',
                                             'https://nodejs.org/api/http.html',
                                             'https://nodejs.org/api/net.html',
                                             'https://nodejs.org/api/stream.html',
                                             'https://nodejs.org/api/crypto.html',
                                             'https://nodejs.org/api/worker_threads.html'],
                               'profile': 'node',
                               'match_terms': ['node', 'nodejs', 'fs', 'stream', 'crypto', 'worker_threads', 'http server'],
                               'topic_urls': {'fs': ['https://nodejs.org/api/fs.html'],
                                              'crypto': ['https://nodejs.org/api/crypto.html'],
                                              'worker': ['https://nodejs.org/api/worker_threads.html']},
                               'same_host_crawl_only': True},
             'typescript_direct': {'display_name': 'TypeScript Handbook Direct',
                                   'allowed_domains': ['www.typescriptlang.org'],
                                   'resolver': 'topic_registry',
                                   'seed_urls': ['https://www.typescriptlang.org/docs/',
                                                 'https://www.typescriptlang.org/docs/handbook/intro.html',
                                                 'https://www.typescriptlang.org/docs/handbook/utility-types.html'],
                                   'profile': 'javascript',
                                   'match_terms': ['typescript', 'ts', 'type', 'interface', 'generic'],
                                   'topic_urls': {},
                                   'same_host_crawl_only': True},
             'npm_direct': {'display_name': 'npm CLI Direct',
                            'allowed_domains': ['docs.npmjs.com'],
                            'resolver': 'topic_registry',
                            'seed_urls': ['https://docs.npmjs.com/cli/',
                                          'https://docs.npmjs.com/cli/commands/npm-install',
                                          'https://docs.npmjs.com/cli/configuring-npm/package-json'],
                            'profile': 'node',
                            'match_terms': ['npm', 'package.json', 'npm install'],
                            'topic_urls': {},
                            'same_host_crawl_only': True},
             'react_direct': {'display_name': 'React Docs Direct',
                              'allowed_domains': ['react.dev'],
                              'resolver': 'topic_registry',
                              'seed_urls': ['https://react.dev/reference/react', 'https://react.dev/reference/react-dom', 'https://react.dev/learn'],
                              'profile': 'web',
                              'match_terms': ['react', 'useeffect', 'usestate', 'component', 'jsx'],
                              'topic_urls': {'useeffect': ['https://react.dev/reference/react/useEffect'],
                                             'usestate': ['https://react.dev/reference/react/useState']},
                              'same_host_crawl_only': True},
             'vue_direct': {'display_name': 'Vue Docs Direct',
                            'allowed_domains': ['vuejs.org'],
                            'resolver': 'topic_registry',
                            'seed_urls': ['https://vuejs.org/api/',
                                          'https://vuejs.org/guide/introduction.html',
                                          'https://vuejs.org/api/composition-api-setup.html'],
                            'profile': 'web',
                            'match_terms': ['vue', 'composition api', 'ref', 'reactive'],
                            'topic_urls': {},
                            'same_host_crawl_only': True},
             'angular_direct': {'display_name': 'Angular Docs Direct',
                                'allowed_domains': ['angular.dev'],
                                'resolver': 'topic_registry',
                                'seed_urls': ['https://angular.dev/api', 'https://angular.dev/overview', 'https://angular.dev/guide/components'],
                                'profile': 'web',
                                'match_terms': ['angular', 'component', 'service', 'rxjs'],
                                'topic_urls': {},
                                'same_host_crawl_only': True},
             'rust_std_direct': {'display_name': 'Rust Standard Library Direct',
                                 'allowed_domains': ['doc.rust-lang.org'],
                                 'resolver': 'topic_registry',
                                 'seed_urls': ['https://doc.rust-lang.org/std/', 'https://doc.rust-lang.org/core/', 'https://doc.rust-lang.org/alloc/'],
                                 'profile': 'rust',
                                 'match_terms': ['rust', 'std::', 'vec', 'option', 'result', 'tokio', 'borrow checker'],
                                 'topic_urls': {'vec': ['https://doc.rust-lang.org/std/vec/struct.Vec.html'],
                                                'option': ['https://doc.rust-lang.org/std/option/enum.Option.html'],
                                                'result': ['https://doc.rust-lang.org/std/result/enum.Result.html'],
                                                'thread': ['https://doc.rust-lang.org/std/thread/']},
                                 'same_host_crawl_only': True},
             'rust_book_direct': {'display_name': 'Rust Book / Reference Direct',
                                  'allowed_domains': ['doc.rust-lang.org'],
                                  'resolver': 'topic_registry',
                                  'seed_urls': ['https://doc.rust-lang.org/book/', 'https://doc.rust-lang.org/reference/', 'https://doc.rust-lang.org/cargo/'],
                                  'profile': 'rust',
                                  'match_terms': ['rust book', 'ownership', 'borrow', 'lifetime', 'trait', 'macro'],
                                  'topic_urls': {'ownership': ['https://doc.rust-lang.org/book/ch04-00-understanding-ownership.html'],
                                                 'lifetime': ['https://doc.rust-lang.org/book/ch10-03-lifetime-syntax.html'],
                                                 'unsafe': ['https://doc.rust-lang.org/book/ch20-01-unsafe-rust.html']},
                                  'same_host_crawl_only': True},
             'cargo_direct': {'display_name': 'Cargo Direct',
                              'allowed_domains': ['doc.rust-lang.org'],
                              'resolver': 'topic_registry',
                              'seed_urls': ['https://doc.rust-lang.org/cargo/',
                                            'https://doc.rust-lang.org/cargo/reference/manifest.html',
                                            'https://doc.rust-lang.org/cargo/reference/build-scripts.html'],
                              'profile': 'rust',
                              'match_terms': ['cargo', 'cargo.toml', 'crate', 'feature'],
                              'topic_urls': {},
                              'same_host_crawl_only': True},
             'go_std_direct': {'display_name': 'Go Standard Library Direct',
                               'allowed_domains': ['pkg.go.dev'],
                               'resolver': 'topic_registry',
                               'seed_urls': ['https://pkg.go.dev/std',
                                             'https://pkg.go.dev/net/http',
                                             'https://pkg.go.dev/context',
                                             'https://pkg.go.dev/sync',
                                             'https://pkg.go.dev/os',
                                             'https://pkg.go.dev/io',
                                             'https://pkg.go.dev/encoding/json'],
                               'profile': 'go',
                               'match_terms': ['go', 'golang', 'net/http', 'goroutine', 'channel', 'context'],
                               'topic_urls': {'http': ['https://pkg.go.dev/net/http'],
                                              'context': ['https://pkg.go.dev/context'],
                                              'sync': ['https://pkg.go.dev/sync']},
                               'same_host_crawl_only': True},
             'go_dev_direct': {'display_name': 'Go Docs Direct',
                               'allowed_domains': ['go.dev'],
                               'resolver': 'topic_registry',
                               'seed_urls': ['https://go.dev/doc/',
                                             'https://go.dev/ref/spec',
                                             'https://go.dev/doc/effective_go',
                                             'https://go.dev/doc/modules/gomod-ref'],
                               'profile': 'go',
                               'match_terms': ['go spec', 'go modules', 'go.mod', 'effective go'],
                               'topic_urls': {},
                               'same_host_crawl_only': True},
             'oracle_java_direct': {'display_name': 'Java SE API Direct',
                                    'allowed_domains': ['docs.oracle.com'],
                                    'resolver': 'topic_registry',
                                    'seed_urls': ['https://docs.oracle.com/en/java/javase/25/docs/api/index.html',
                                                  'https://docs.oracle.com/en/java/javase/25/docs/api/java.base/module-summary.html',
                                                  'https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/lang/package-summary.html',
                                                  'https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/util/package-summary.html',
                                                  'https://docs.oracle.com/en/java/javase/25/docs/api/java.net.http/java/net/http/package-summary.html'],
                                    'profile': 'java',
                                    'match_terms': ['java', 'jdk', 'jvm', 'java.util', 'java.lang', 'httpclient', 'stream api'],
                                    'topic_urls': {'arraylist': ['https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/util/ArrayList.html'],
                                                   'hashmap': ['https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/util/HashMap.html'],
                                                   'httpclient': ['https://docs.oracle.com/en/java/javase/25/docs/api/java.net.http/java/net/http/HttpClient.html']},
                                    'same_host_crawl_only': True},
             'spring_direct': {'display_name': 'Spring Docs Direct',
                               'allowed_domains': ['docs.spring.io', 'spring.io'],
                               'resolver': 'topic_registry',
                               'seed_urls': ['https://docs.spring.io/spring-framework/reference/',
                                             'https://docs.spring.io/spring-framework/docs/current/javadoc-api/',
                                             'https://docs.spring.io/spring-boot/docs/current/reference/html/'],
                               'profile': 'java',
                               'match_terms': ['spring', 'spring boot', 'bean', 'controller', 'jpa'],
                               'topic_urls': {},
                               'same_host_crawl_only': False},
             'kotlin_direct': {'display_name': 'Kotlin API Direct',
                               'allowed_domains': ['kotlinlang.org'],
                               'resolver': 'topic_registry',
                               'seed_urls': ['https://kotlinlang.org/api/core/kotlin-stdlib/',
                                             'https://kotlinlang.org/docs/home.html',
                                             'https://kotlinlang.org/docs/coroutines-overview.html'],
                               'profile': 'kotlin',
                               'match_terms': ['kotlin', 'coroutines', 'stdlib', 'flow', 'suspend'],
                               'topic_urls': {'coroutines': ['https://kotlinlang.org/docs/coroutines-overview.html'],
                                              'stdlib': ['https://kotlinlang.org/api/core/kotlin-stdlib/']},
                               'same_host_crawl_only': True},
             'android_direct': {'display_name': 'Android Developers Direct',
                                'allowed_domains': ['developer.android.com'],
                                'resolver': 'topic_registry',
                                'seed_urls': ['https://developer.android.com/reference',
                                              'https://developer.android.com/develop',
                                              'https://developer.android.com/jetpack/androidx/explorer'],
                                'profile': 'kotlin',
                                'match_terms': ['android', 'activity', 'fragment', 'jetpack', 'compose'],
                                'topic_urls': {},
                                'same_host_crawl_only': False},
             'swift_direct': {'display_name': 'Swift Book / API Direct',
                              'allowed_domains': ['docs.swift.org', 'developer.apple.com'],
                              'resolver': 'topic_registry',
                              'seed_urls': ['https://docs.swift.org/swift-book/documentation/the-swift-programming-language/',
                                            'https://developer.apple.com/documentation/swift',
                                            'https://developer.apple.com/documentation/foundation'],
                              'profile': 'swift',
                              'match_terms': ['swift', 'swiftui', 'foundation', 'actor', 'async'],
                              'topic_urls': {},
                              'same_host_crawl_only': False},
             'apple_developer_direct': {'display_name': 'Apple Developer Direct',
                                        'allowed_domains': ['developer.apple.com'],
                                        'resolver': 'topic_registry',
                                        'seed_urls': ['https://developer.apple.com/documentation/',
                                                      'https://developer.apple.com/documentation/swiftui',
                                                      'https://developer.apple.com/documentation/foundation',
                                                      'https://developer.apple.com/documentation/uikit'],
                                        'profile': 'swift',
                                        'match_terms': ['apple', 'swiftui', 'uikit', 'foundation', 'appkit'],
                                        'topic_urls': {},
                                        'same_host_crawl_only': True},
             'unity_direct': {'display_name': 'Unity Scripting API Direct',
                              'allowed_domains': ['docs.unity3d.com', 'unity.com'],
                              'resolver': 'topic_registry',
                              'seed_urls': ['https://docs.unity3d.com/ScriptReference/',
                                            'https://docs.unity3d.com/Manual/',
                                            'https://docs.unity3d.com/ScriptReference/GameObject.html',
                                            'https://docs.unity3d.com/ScriptReference/MonoBehaviour.html'],
                              'profile': 'game',
                              'match_terms': ['unity', 'gameobject', 'monobehaviour', 'rigidbody', 'transform'],
                              'topic_urls': {'rigidbody': ['https://docs.unity3d.com/ScriptReference/Rigidbody.html'],
                                             'transform': ['https://docs.unity3d.com/ScriptReference/Transform.html']},
                              'same_host_crawl_only': True},
             'unreal_direct': {'display_name': 'Unreal Engine Docs Direct',
                               'allowed_domains': ['dev.epicgames.com'],
                               'resolver': 'topic_registry',
                               'seed_urls': ['https://dev.epicgames.com/documentation/en-us/unreal-engine/API',
                                             'https://dev.epicgames.com/documentation/en-us/unreal-engine/programming-with-cplusplus-in-unreal-engine',
                                             'https://dev.epicgames.com/documentation/en-us/unreal-engine/BlueprintAPI'],
                               'profile': 'game',
                               'match_terms': ['unreal', 'ue', 'ue4', 'ue5', 'blueprint', 'uobject', 'actor'],
                               'topic_urls': {},
                               'same_host_crawl_only': True},
             'godot_direct': {'display_name': 'Godot Docs Direct',
                              'allowed_domains': ['docs.godotengine.org'],
                              'resolver': 'topic_registry',
                              'seed_urls': ['https://docs.godotengine.org/en/stable/',
                                            'https://docs.godotengine.org/en/stable/classes/',
                                            'https://docs.godotengine.org/en/stable/tutorials/scripting/gdscript/'],
                              'profile': 'game',
                              'match_terms': ['godot', 'gdscript', 'node', 'scene'],
                              'topic_urls': {},
                              'same_host_crawl_only': True},
             'bannerlord_direct': {'display_name': 'Bannerlord Community Modding Docs Direct',
                                   'allowed_domains': ['docs.bannerlordmodding.com', 'github.com'],
                                   'resolver': 'topic_registry',
                                   'seed_urls': ['https://docs.bannerlordmodding.com/',
                                                 'https://docs.bannerlordmodding.com/_csharp-api/campaignsystem/',
                                                 'https://docs.bannerlordmodding.com/_csharp-api/core/',
                                                 'https://docs.bannerlordmodding.com/_csharp-api/engine/',
                                                 'https://docs.bannerlordmodding.com/_csharp-api/mountandblade/',
                                                 'https://docs.bannerlordmodding.com/_csharp-api/gauntlet/'],
                                   'profile': 'bannerlord',
                                   'match_terms': ['bannerlord',
                                                   'taleworlds',
                                                   'campaignsystem',
                                                   'campaignbehavior',
                                                   'mobileparty',
                                                   'hero',
                                                   'settlement',
                                                   'gauntlet',
                                                   'mountandblade'],
                                   'topic_urls': {'campaignbehavior': ['https://docs.bannerlordmodding.com/_csharp-api/campaignsystem/campaignbehaviorbase.html'],
                                                  'campaignsystem': ['https://docs.bannerlordmodding.com/_csharp-api/campaignsystem/'],
                                                  'mountandblade': ['https://docs.bannerlordmodding.com/_csharp-api/mountandblade/']},
                                   'same_host_crawl_only': False},
             'butr_bannerlord_api_direct': {'display_name': 'BUTR Unofficial Bannerlord API Direct',
                                            'allowed_domains': ['bannerlordapi.butr.link'],
                                            'resolver': 'topic_registry',
                                            'seed_urls': ['https://bannerlordapi.butr.link/',
                                                          'https://bannerlordapi.butr.link/api/core/TaleWorlds.MountAndBlade.html',
                                                          'https://bannerlordapi.butr.link/api/core/TaleWorlds.Core.html',
                                                          'https://bannerlordapi.butr.link/api/native/TaleWorlds.CampaignSystem.html',
                                                          'https://bannerlordapi.butr.link/api/native/TaleWorlds.CampaignSystem.Party.MobileParty.html'],
                                            'profile': 'bannerlord',
                                            'match_terms': ['taleworlds.', 'mobileparty', 'campaignsystem', 'hero', 'partybase', 'agent', 'mission'],
                                            'topic_urls': {'mobileparty': ['https://bannerlordapi.butr.link/api/native/TaleWorlds.CampaignSystem.Party.MobileParty.html'],
                                                           'hero': ['https://bannerlordapi.butr.link/api/native/TaleWorlds.CampaignSystem.Hero.html'],
                                                           'agent': ['https://bannerlordapi.butr.link/api/core/TaleWorlds.MountAndBlade.Agent.html']},
                                            'same_host_crawl_only': True},
             'postgres_direct': {'display_name': 'PostgreSQL Docs Direct',
                                 'allowed_domains': ['www.postgresql.org'],
                                 'resolver': 'topic_registry',
                                 'seed_urls': ['https://www.postgresql.org/docs/current/',
                                               'https://www.postgresql.org/docs/current/sql.html',
                                               'https://www.postgresql.org/docs/current/functions.html'],
                                 'profile': 'database',
                                 'match_terms': ['postgres', 'postgresql', 'sql', 'psql'],
                                 'topic_urls': {},
                                 'same_host_crawl_only': True},
             'sqlite_direct': {'display_name': 'SQLite Docs Direct',
                               'allowed_domains': ['sqlite.org', 'www.sqlite.org'],
                               'resolver': 'topic_registry',
                               'seed_urls': ['https://www.sqlite.org/docs.html', 'https://www.sqlite.org/lang.html', 'https://www.sqlite.org/c3ref/intro.html'],
                               'profile': 'database',
                               'match_terms': ['sqlite', 'sqlite3', 'c api', 'sql'],
                               'topic_urls': {},
                               'same_host_crawl_only': True},
             'mysql_direct': {'display_name': 'MySQL Docs Direct',
                              'allowed_domains': ['dev.mysql.com'],
                              'resolver': 'topic_registry',
                              'seed_urls': ['https://dev.mysql.com/doc/',
                                            'https://dev.mysql.com/doc/refman/8.4/en/',
                                            'https://dev.mysql.com/doc/c-api/8.4/en/'],
                              'profile': 'database',
                              'match_terms': ['mysql', 'mariadb', 'sql'],
                              'topic_urls': {},
                              'same_host_crawl_only': True},
             'mongodb_direct': {'display_name': 'MongoDB Docs Direct',
                                'allowed_domains': ['www.mongodb.com', 'mongodb.com'],
                                'resolver': 'topic_registry',
                                'seed_urls': ['https://www.mongodb.com/docs/', 'https://www.mongodb.com/docs/manual/', 'https://www.mongodb.com/docs/drivers/'],
                                'profile': 'database',
                                'match_terms': ['mongodb', 'mongo', 'bson', 'aggregation'],
                                'topic_urls': {},
                                'same_host_crawl_only': True},
             'redis_direct': {'display_name': 'Redis Docs Direct',
                              'allowed_domains': ['redis.io'],
                              'resolver': 'topic_registry',
                              'seed_urls': ['https://redis.io/docs/latest/', 'https://redis.io/docs/latest/commands/', 'https://redis.io/docs/latest/develop/'],
                              'profile': 'database',
                              'match_terms': ['redis', 'cache', 'pubsub'],
                              'topic_urls': {},
                              'same_host_crawl_only': True},
             'docker_direct': {'display_name': 'Docker Docs Direct',
                               'allowed_domains': ['docs.docker.com'],
                               'resolver': 'topic_registry',
                               'seed_urls': ['https://docs.docker.com/',
                                             'https://docs.docker.com/reference/',
                                             'https://docs.docker.com/reference/dockerfile/',
                                             'https://docs.docker.com/engine/reference/commandline/cli/'],
                               'profile': 'devops',
                               'match_terms': ['docker', 'dockerfile', 'compose', 'container'],
                               'topic_urls': {},
                               'same_host_crawl_only': True},
             'kubernetes_direct': {'display_name': 'Kubernetes Docs Direct',
                                   'allowed_domains': ['kubernetes.io'],
                                   'resolver': 'topic_registry',
                                   'seed_urls': ['https://kubernetes.io/docs/reference/',
                                                 'https://kubernetes.io/docs/concepts/',
                                                 'https://kubernetes.io/docs/reference/generated/kubernetes-api/'],
                                   'profile': 'devops',
                                   'match_terms': ['kubernetes', 'k8s', 'pod', 'deployment', 'service'],
                                   'topic_urls': {},
                                   'same_host_crawl_only': True},
             'git_direct': {'display_name': 'Git Docs Direct',
                            'allowed_domains': ['git-scm.com'],
                            'resolver': 'topic_registry',
                            'seed_urls': ['https://git-scm.com/docs',
                                          'https://git-scm.com/docs/git',
                                          'https://git-scm.com/docs/git-commit',
                                          'https://git-scm.com/docs/git-rebase'],
                            'profile': 'devops',
                            'match_terms': ['git', 'commit', 'rebase', 'merge'],
                            'topic_urls': {},
                            'same_host_crawl_only': True},
             'github_actions_direct': {'display_name': 'GitHub Actions Docs Direct',
                                       'allowed_domains': ['docs.github.com'],
                                       'resolver': 'topic_registry',
                                       'seed_urls': ['https://docs.github.com/en/actions',
                                                     'https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions',
                                                     'https://docs.github.com/en/rest'],
                                       'profile': 'devops',
                                       'match_terms': ['github actions', 'workflow', 'yaml', 'github api', 'rest api'],
                                       'topic_urls': {},
                                       'same_host_crawl_only': True},
             'terraform_direct': {'display_name': 'Terraform Docs Direct',
                                  'allowed_domains': ['developer.hashicorp.com'],
                                  'resolver': 'topic_registry',
                                  'seed_urls': ['https://developer.hashicorp.com/terraform/docs',
                                                'https://developer.hashicorp.com/terraform/language',
                                                'https://developer.hashicorp.com/terraform/cli'],
                                  'profile': 'devops',
                                  'match_terms': ['terraform', 'hcl', 'provider'],
                                  'topic_urls': {},
                                  'same_host_crawl_only': True},
             'aws_direct': {'display_name': 'AWS Docs Direct',
                            'allowed_domains': ['docs.aws.amazon.com'],
                            'resolver': 'topic_registry',
                            'seed_urls': ['https://docs.aws.amazon.com/',
                                          'https://docs.aws.amazon.com/lambda/latest/dg/',
                                          'https://docs.aws.amazon.com/AmazonS3/latest/API/',
                                          'https://docs.aws.amazon.com/AWSEC2/latest/APIReference/'],
                            'profile': 'cloud',
                            'match_terms': ['aws', 's3', 'lambda', 'ec2', 'boto3'],
                            'topic_urls': {},
                            'same_host_crawl_only': True},
             'azure_direct': {'display_name': 'Azure Docs Direct',
                              'allowed_domains': ['learn.microsoft.com'],
                              'resolver': 'topic_registry',
                              'seed_urls': ['https://learn.microsoft.com/en-us/azure/',
                                            'https://learn.microsoft.com/en-us/rest/api/azure/',
                                            'https://learn.microsoft.com/en-us/azure/azure-functions/'],
                              'profile': 'cloud',
                              'match_terms': ['azure', 'azure functions', 'arm', 'bicep'],
                              'topic_urls': {},
                              'same_host_crawl_only': True},
             'gcp_direct': {'display_name': 'Google Cloud Docs Direct',
                            'allowed_domains': ['cloud.google.com'],
                            'resolver': 'topic_registry',
                            'seed_urls': ['https://cloud.google.com/docs',
                                          'https://cloud.google.com/apis/docs/overview',
                                          'https://cloud.google.com/compute/docs/reference/rest/v1'],
                            'profile': 'cloud',
                            'match_terms': ['gcp', 'google cloud', 'compute engine', 'cloud functions'],
                            'topic_urls': {},
                            'same_host_crawl_only': True},
             'openai_direct': {'display_name': 'OpenAI Platform Docs Direct',
                               'allowed_domains': ['platform.openai.com', 'developers.openai.com'],
                               'resolver': 'topic_registry',
                               'seed_urls': ['https://platform.openai.com/docs',
                                             'https://platform.openai.com/docs/api-reference',
                                             'https://platform.openai.com/docs/guides'],
                               'profile': 'ai',
                               'match_terms': ['openai', 'chatgpt', 'responses api', 'api reference'],
                               'topic_urls': {},
                               'same_host_crawl_only': True},
             'pytorch_direct': {'display_name': 'PyTorch Docs Direct',
                                'allowed_domains': ['pytorch.org'],
                                'resolver': 'topic_registry',
                                'seed_urls': ['https://pytorch.org/docs/stable/index.html',
                                              'https://pytorch.org/docs/stable/torch.html',
                                              'https://pytorch.org/docs/stable/nn.html'],
                                'profile': 'ai',
                                'match_terms': ['pytorch', 'torch', 'nn', 'tensor'],
                                'topic_urls': {},
                                'same_host_crawl_only': True},
             'tensorflow_direct': {'display_name': 'TensorFlow API Direct',
                                   'allowed_domains': ['www.tensorflow.org', 'tensorflow.org'],
                                   'resolver': 'topic_registry',
                                   'seed_urls': ['https://www.tensorflow.org/api_docs',
                                                 'https://www.tensorflow.org/api_docs/python/tf',
                                                 'https://www.tensorflow.org/guide'],
                                   'profile': 'ai',
                                   'match_terms': ['tensorflow', 'tf', 'keras'],
                                   'topic_urls': {},
                                   'same_host_crawl_only': True},
             'sklearn_direct': {'display_name': 'scikit-learn API Direct',
                                'allowed_domains': ['scikit-learn.org'],
                                'resolver': 'topic_registry',
                                'seed_urls': ['https://scikit-learn.org/stable/api/index.html', 'https://scikit-learn.org/stable/user_guide.html'],
                                'profile': 'ai',
                                'match_terms': ['sklearn', 'scikit', 'machine learning'],
                                'topic_urls': {},
                                'same_host_crawl_only': True},
             'opencv_direct': {'display_name': 'OpenCV Docs Direct',
                               'allowed_domains': ['docs.opencv.org'],
                               'resolver': 'topic_registry',
                               'seed_urls': ['https://docs.opencv.org/', 'https://docs.opencv.org/4.x/', 'https://docs.opencv.org/4.x/modules.html'],
                               'profile': 'ai',
                               'match_terms': ['opencv', 'cv2', 'image processing'],
                               'topic_urls': {},
                               'same_host_crawl_only': True},
             'linux_man_direct': {'display_name': 'Linux man-pages Direct',
                                  'allowed_domains': ['man7.org', 'manpages.debian.org'],
                                  'resolver': 'topic_registry',
                                  'seed_urls': ['https://man7.org/linux/man-pages/',
                                                'https://man7.org/linux/man-pages/man2/',
                                                'https://man7.org/linux/man-pages/man3/',
                                                'https://man7.org/linux/man-pages/man7/'],
                                  'profile': 'linux',
                                  'match_terms': ['linux', 'syscall', 'man page', 'socket', 'epoll', 'ioctl'],
                                  'topic_urls': {},
                                  'same_host_crawl_only': False},
             'linux_kernel_direct': {'display_name': 'Linux Kernel Docs Direct',
                                     'allowed_domains': ['docs.kernel.org', 'www.kernel.org'],
                                     'resolver': 'topic_registry',
                                     'seed_urls': ['https://docs.kernel.org/',
                                                   'https://docs.kernel.org/networking/index.html',
                                                   'https://docs.kernel.org/driver-api/index.html',
                                                   'https://docs.kernel.org/core-api/index.html'],
                                     'profile': 'linux',
                                     'match_terms': ['kernel', 'driver', 'netdev', 'ebpf'],
                                     'topic_urls': {},
                                     'same_host_crawl_only': True},
             'monero_official_direct': {'display_name': 'GetMonero Official Docs Direct',
                                        'allowed_domains': ['www.getmonero.org', 'getmonero.org', 'docs.getmonero.org'],
                                        'resolver': 'monero',
                                        'seed_urls': ['https://www.getmonero.org/resources/developer-guides/daemon-rpc.html',
                                                      'https://www.getmonero.org/resources/developer-guides/wallet-rpc.html',
                                                      'https://www.getmonero.org/resources/user-guides/mine-to-pool.html',
                                                      'https://www.getmonero.org/resources/user-guides/solo_mine_GUI.html'],
                                        'profile': 'monero',
                                        'match_terms': ['monero', 'xmr', 'daemon', 'wallet', 'rpc', 'get_block_template'],
                                        'topic_urls': {},
                                        'same_host_crawl_only': True},
             'p2pool_direct': {'display_name': 'P2Pool Direct Docs',
                               'allowed_domains': ['github.com', 'raw.githubusercontent.com', 'p2pool.io', 'mini.p2pool.observer', 'p2pool.observer'],
                               'resolver': 'p2pool',
                               'seed_urls': ['https://github.com/SChernykh/p2pool#readme',
                                             'https://raw.githubusercontent.com/SChernykh/p2pool/master/README.md'],
                               'profile': 'monero',
                               'match_terms': ['p2pool', 'sidechain', 'mini', 'shares', 'payout'],
                               'topic_urls': {},
                               'same_host_crawl_only': False},
             'xmrig_direct': {'display_name': 'XMRig Direct Docs',
                              'allowed_domains': ['xmrig.com', 'github.com', 'raw.githubusercontent.com'],
                              'resolver': 'xmrig',
                              'seed_urls': ['https://xmrig.com/docs/miner/config',
                                            'https://xmrig.com/docs/miner/randomx-optimization-guide',
                                            'https://xmrig.com/docs/miner/hugepages',
                                            'https://github.com/xmrig/xmrig#readme',
                                            'https://raw.githubusercontent.com/xmrig/xmrig/master/README.md'],
                              'profile': 'monero',
                              'match_terms': ['xmrig', 'randomx', 'hugepages', 'msr', 'stratum'],
                              'topic_urls': {},
                              'same_host_crawl_only': False},
             'randomx_direct': {'display_name': 'RandomX Direct Docs',
                                'allowed_domains': ['github.com', 'raw.githubusercontent.com'],
                                'resolver': 'randomx',
                                'seed_urls': ['https://github.com/tevador/RandomX#readme',
                                              'https://raw.githubusercontent.com/tevador/RandomX/master/README.md',
                                              'https://raw.githubusercontent.com/tevador/RandomX/master/doc/specs.md'],
                                'profile': 'monero',
                                'match_terms': ['randomx', 'pow', 'proof of work'],
                                'topic_urls': {},
                                'same_host_crawl_only': False},
             'open_web_search_fallback': {'display_name': 'Optional DuckDuckGo Fallback Search',
                                          'allowed_domains': ['*'],
                                          'allow_any_domain': True,
                                          'docs_like_only': True,
                                          'same_host_crawl_only': True,
                                          'resolver': 'search',
                                          'seed_urls': [],
                                          'profile': 'all',
                                          'match_terms': ['search', 'fallback']}},
 'settings': {'duckduckgo_url': 'https://duckduckgo.com/html/?q={query}',
              'timeout': 20,
              'delay': 0.1,
              'max_pages_per_query': 2,
              'max_links_per_page': 20,
              'max_chars_per_page': 8000,
              'max_direct_urls_per_query': 8,
              'max_search_hits': 4,
              'cache_dir': '.apidoc_cache',
              'direct_mode': True,
              'search_fallback': False,
              'crawl_direct_pages': False,
              'catalog_match_seed_threshold': 1}}

PYTHON_MODULE_MAP = {
    "pathlib": "pathlib", "asyncio": "asyncio", "typing": "typing", "dataclasses": "dataclasses", "argparse": "argparse",
    "enum": "enum", "subprocess": "subprocess", "json": "json", "sqlite3": "sqlite3", "socket": "socket", "ssl": "ssl",
    "threading": "threading", "multiprocessing": "multiprocessing", "concurrent": "concurrent.futures", "queue": "queue",
    "logging": "logging", "inspect": "inspect", "ast": "ast", "re": "re", "os": "os", "sys": "sys", "shutil": "shutil",
    "tempfile": "tempfile", "urllib": "urllib", "http": "http", "email": "email", "ctypes": "ctypes", "struct": "struct",
    "hashlib": "hashlib", "hmac": "hmac", "secrets": "secrets", "base64": "base64", "binascii": "binascii", "time": "time",
    "datetime": "datetime", "zoneinfo": "zoneinfo", "functools": "functools", "itertools": "itertools", "collections": "collections",
    "heapq": "heapq", "bisect": "bisect", "copy": "copy", "pickle": "pickle", "csv": "csv", "configparser": "configparser",
    "tomllib": "tomllib", "xml": "xml.etree.elementtree", "html": "html", "io": "io", "mmap": "mmap", "glob": "glob",
    "fnmatch": "fnmatch", "unittest": "unittest", "doctest": "doctest", "pdb": "pdb", "traceback": "traceback",
}
PYTHON_BUILTIN_MAP = {
    "list": "stdtypes.html#lists", "dict": "stdtypes.html#mapping-types-dict", "set": "stdtypes.html#set-types-set-frozenset",
    "str": "stdtypes.html#text-sequence-type-str", "bytes": "stdtypes.html#bytes-objects", "bytearray": "stdtypes.html#bytearray-objects",
    "tuple": "stdtypes.html#tuples", "range": "stdtypes.html#ranges", "open": "functions.html#open", "print": "functions.html#print",
    "len": "functions.html#len", "super": "functions.html#super", "property": "functions.html#property", "enumerate": "functions.html#enumerate",
    "zip": "functions.html#zip", "sorted": "functions.html#sorted", "isinstance": "functions.html#isinstance", "getattr": "functions.html#getattr",
}
CSHARP_TOPIC_MAP = {
    "async": "https://learn.microsoft.com/en-us/dotnet/csharp/asynchronous-programming/",
    "await": "https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/operators/await",
    "records": "https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/builtin-types/record",
    "record": "https://learn.microsoft.com/en-us/dotnet/csharp/language-reference/builtin-types/record",
    "extension": "https://learn.microsoft.com/en-us/dotnet/csharp/programming-guide/classes-and-structs/extension-methods",
    "linq": "https://learn.microsoft.com/en-us/dotnet/csharp/linq/",
    "generics": "https://learn.microsoft.com/en-us/dotnet/csharp/programming-guide/generics/",
    "pattern": "https://learn.microsoft.com/en-us/dotnet/csharp/fundamentals/functional/pattern-matching",
    "nullable": "https://learn.microsoft.com/en-us/dotnet/csharp/nullable-references",
    "span": "https://learn.microsoft.com/en-us/dotnet/standard/memory-and-spans/",
}

CPP_MICROSOFT_TOPIC_MAP = {
    "language": "https://learn.microsoft.com/en-us/cpp/cpp/cpp-language-reference?view=msvc-170",
    "class": "https://learn.microsoft.com/en-us/cpp/cpp/classes-and-structs-cpp?view=msvc-170",
    "struct": "https://learn.microsoft.com/en-us/cpp/cpp/classes-and-structs-cpp?view=msvc-170",
    "template": "https://learn.microsoft.com/en-us/cpp/cpp/templates-cpp?view=msvc-170",
    "templates": "https://learn.microsoft.com/en-us/cpp/cpp/templates-cpp?view=msvc-170",
    "concept": "https://learn.microsoft.com/en-us/cpp/cpp/concepts-cpp?view=msvc-170",
    "concepts": "https://learn.microsoft.com/en-us/cpp/cpp/concepts-cpp?view=msvc-170",
    "module": "https://learn.microsoft.com/en-us/cpp/cpp/modules-cpp?view=msvc-170",
    "modules": "https://learn.microsoft.com/en-us/cpp/cpp/modules-cpp?view=msvc-170",
    "lambda": "https://learn.microsoft.com/en-us/cpp/cpp/lambda-expressions-in-cpp?view=msvc-170",
    "constexpr": "https://learn.microsoft.com/en-us/cpp/cpp/constexpr-cpp?view=msvc-170",
    "coroutine": "https://learn.microsoft.com/en-us/cpp/cpp/coroutines-cpp?view=msvc-170",
    "coroutines": "https://learn.microsoft.com/en-us/cpp/cpp/coroutines-cpp?view=msvc-170",
    "exception": "https://learn.microsoft.com/en-us/cpp/cpp/exception-handling-in-visual-cpp?view=msvc-170",
    "exceptions": "https://learn.microsoft.com/en-us/cpp/cpp/exception-handling-in-visual-cpp?view=msvc-170",
    "thread": "https://learn.microsoft.com/en-us/cpp/standard-library/thread-class?view=msvc-170",
    "mutex": "https://learn.microsoft.com/en-us/cpp/standard-library/mutex-class-stl?view=msvc-170",
    "atomic": "https://learn.microsoft.com/en-us/cpp/standard-library/atomic?view=msvc-170",
    "filesystem": "https://learn.microsoft.com/en-us/cpp/standard-library/filesystem?view=msvc-170",
    "chrono": "https://learn.microsoft.com/en-us/cpp/standard-library/chrono?view=msvc-170",
    "iostream": "https://learn.microsoft.com/en-us/cpp/standard-library/iostream?view=msvc-170",
    "fstream": "https://learn.microsoft.com/en-us/cpp/standard-library/fstream?view=msvc-170",
    "span": "https://learn.microsoft.com/en-us/cpp/standard-library/span-class?view=msvc-170",
    "ranges": "https://learn.microsoft.com/en-us/cpp/standard-library/ranges?view=msvc-170",
    "vector": "https://learn.microsoft.com/en-us/cpp/standard-library/vector-class?view=msvc-170",
    "string": "https://learn.microsoft.com/en-us/cpp/standard-library/basic-string-class?view=msvc-170",
    "string_view": "https://learn.microsoft.com/en-us/cpp/standard-library/basic-string-view-class?view=msvc-170",
    "map": "https://learn.microsoft.com/en-us/cpp/standard-library/map-class?view=msvc-170",
    "unordered_map": "https://learn.microsoft.com/en-us/cpp/standard-library/unordered-map-class?view=msvc-170",
    "set": "https://learn.microsoft.com/en-us/cpp/standard-library/set-class?view=msvc-170",
    "unordered_set": "https://learn.microsoft.com/en-us/cpp/standard-library/unordered-set-class?view=msvc-170",
    "optional": "https://learn.microsoft.com/en-us/cpp/standard-library/optional-class?view=msvc-170",
    "variant": "https://learn.microsoft.com/en-us/cpp/standard-library/variant-class?view=msvc-170",
    "tuple": "https://learn.microsoft.com/en-us/cpp/standard-library/tuple-class?view=msvc-170",
    "unique_ptr": "https://learn.microsoft.com/en-us/cpp/standard-library/unique-ptr-class?view=msvc-170",
    "shared_ptr": "https://learn.microsoft.com/en-us/cpp/standard-library/shared-ptr-class?view=msvc-170",
    "regex": "https://learn.microsoft.com/en-us/cpp/standard-library/regular-expressions-cpp?view=msvc-170",
    "algorithm": "https://learn.microsoft.com/en-us/cpp/standard-library/algorithm?view=msvc-170",
    "memory": "https://learn.microsoft.com/en-us/cpp/standard-library/memory?view=msvc-170",
    "numeric": "https://learn.microsoft.com/en-us/cpp/standard-library/numeric?view=msvc-170",
    "msvc": "https://learn.microsoft.com/en-us/cpp/build/reference/compiler-options?view=msvc-170",
    "compiler": "https://learn.microsoft.com/en-us/cpp/build/reference/compiler-options?view=msvc-170",
    "cmake": "https://learn.microsoft.com/en-us/cpp/build/cmake-projects-in-visual-studio?view=msvc-170",
    "win32": "https://learn.microsoft.com/en-us/windows/win32/api/",
    "windows": "https://learn.microsoft.com/en-us/windows/win32/api/",
}

CPPREFERENCE_TOPIC_MAP = {
    "language": "https://en.cppreference.com/w/cpp/language",
    "standard library": "https://en.cppreference.com/w/cpp/standard_library",
    "stdlib": "https://en.cppreference.com/w/cpp/standard_library",
    "class": "https://en.cppreference.com/w/cpp/language/classes",
    "struct": "https://en.cppreference.com/w/cpp/language/classes",
    "template": "https://en.cppreference.com/w/cpp/language/templates",
    "templates": "https://en.cppreference.com/w/cpp/language/templates",
    "concept": "https://en.cppreference.com/w/cpp/language/constraints",
    "concepts": "https://en.cppreference.com/w/cpp/language/constraints",
    "module": "https://en.cppreference.com/w/cpp/language/modules",
    "modules": "https://en.cppreference.com/w/cpp/language/modules",
    "lambda": "https://en.cppreference.com/w/cpp/language/lambda",
    "constexpr": "https://en.cppreference.com/w/cpp/language/constexpr",
    "coroutine": "https://en.cppreference.com/w/cpp/language/coroutines",
    "coroutines": "https://en.cppreference.com/w/cpp/language/coroutines",
    "exception": "https://en.cppreference.com/w/cpp/error/exception",
    "exceptions": "https://en.cppreference.com/w/cpp/error/exception",
    "thread": "https://en.cppreference.com/w/cpp/thread/thread",
    "mutex": "https://en.cppreference.com/w/cpp/thread/mutex",
    "atomic": "https://en.cppreference.com/w/cpp/atomic/atomic",
    "filesystem": "https://en.cppreference.com/w/cpp/filesystem",
    "chrono": "https://en.cppreference.com/w/cpp/chrono",
    "iostream": "https://en.cppreference.com/w/cpp/io",
    "fstream": "https://en.cppreference.com/w/cpp/io/basic_fstream",
    "span": "https://en.cppreference.com/w/cpp/container/span",
    "ranges": "https://en.cppreference.com/w/cpp/ranges",
    "vector": "https://en.cppreference.com/w/cpp/container/vector",
    "string": "https://en.cppreference.com/w/cpp/string/basic_string",
    "string_view": "https://en.cppreference.com/w/cpp/string/basic_string_view",
    "map": "https://en.cppreference.com/w/cpp/container/map",
    "unordered_map": "https://en.cppreference.com/w/cpp/container/unordered_map",
    "set": "https://en.cppreference.com/w/cpp/container/set",
    "unordered_set": "https://en.cppreference.com/w/cpp/container/unordered_set",
    "optional": "https://en.cppreference.com/w/cpp/utility/optional",
    "variant": "https://en.cppreference.com/w/cpp/utility/variant",
    "tuple": "https://en.cppreference.com/w/cpp/utility/tuple",
    "unique_ptr": "https://en.cppreference.com/w/cpp/memory/unique_ptr",
    "shared_ptr": "https://en.cppreference.com/w/cpp/memory/shared_ptr",
    "regex": "https://en.cppreference.com/w/cpp/regex",
    "algorithm": "https://en.cppreference.com/w/cpp/algorithm",
    "memory": "https://en.cppreference.com/w/cpp/memory",
    "numeric": "https://en.cppreference.com/w/cpp/numeric",
}

CPPREFERENCE_MEMBER_MAP = {
    ("vector", "push_back"): "https://en.cppreference.com/w/cpp/container/vector/push_back",
    ("vector", "emplace_back"): "https://en.cppreference.com/w/cpp/container/vector/emplace_back",
    ("vector", "reserve"): "https://en.cppreference.com/w/cpp/container/vector/reserve",
    ("vector", "resize"): "https://en.cppreference.com/w/cpp/container/vector/resize",
    ("vector", "erase"): "https://en.cppreference.com/w/cpp/container/vector/erase",
    ("string", "find"): "https://en.cppreference.com/w/cpp/string/basic_string/find",
    ("string", "substr"): "https://en.cppreference.com/w/cpp/string/basic_string/substr",
    ("map", "find"): "https://en.cppreference.com/w/cpp/container/map/find",
    ("unordered_map", "find"): "https://en.cppreference.com/w/cpp/container/unordered_map/find",
    ("optional", "value"): "https://en.cppreference.com/w/cpp/utility/optional/value",
    ("thread", "join"): "https://en.cppreference.com/w/cpp/thread/thread/join",
}
PY_PACKAGE_DOCS = {
    "requests": ["https://requests.readthedocs.io/en/latest/", "https://requests.readthedocs.io/en/latest/api/"],
    "numpy": ["https://numpy.org/doc/stable/reference/", "https://numpy.org/doc/stable/reference/generated/numpy.ndarray.html"],
    "pandas": ["https://pandas.pydata.org/docs/reference/", "https://pandas.pydata.org/docs/reference/api/pandas.DataFrame.html"],
    "flask": ["https://flask.palletsprojects.com/en/latest/api/"],
    "fastapi": ["https://fastapi.tiangolo.com/reference/", "https://fastapi.tiangolo.com/tutorial/"],
    "sqlalchemy": ["https://docs.sqlalchemy.org/en/20/"],
    "beautifulsoup": ["https://www.crummy.com/software/BeautifulSoup/bs4/doc/"],
    "bs4": ["https://www.crummy.com/software/BeautifulSoup/bs4/doc/"],
    "pyqt5": ["https://www.riverbankcomputing.com/static/Docs/PyQt5/"],
    "pytest": ["https://docs.pytest.org/en/stable/reference/"],
    "pydantic": ["https://docs.pydantic.dev/latest/api/base_model/"],
}
MONERO_TOPIC_MAP = {
    "daemon": ["https://www.getmonero.org/resources/developer-guides/daemon-rpc.html"],
    "rpc": ["https://www.getmonero.org/resources/developer-guides/daemon-rpc.html", "https://www.getmonero.org/resources/developer-guides/wallet-rpc.html"],
    "wallet": ["https://www.getmonero.org/resources/developer-guides/wallet-rpc.html"],
    "get_block_template": ["https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#get_block_template"],
    "submit_block": ["https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#submit_block"],
    "mining_status": ["https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#mining_status"],
    "start_mining": ["https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#start_mining"],
    "stop_mining": ["https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#stop_mining"],
    "get_info": ["https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#get_info"],
    "randomx": ["https://github.com/tevador/RandomX#readme", "https://raw.githubusercontent.com/tevador/RandomX/master/doc/specs.md"],
    "p2pool": ["https://github.com/SChernykh/p2pool#readme", "https://raw.githubusercontent.com/SChernykh/p2pool/master/README.md"],
    "xmrig": ["https://xmrig.com/docs/miner/config", "https://xmrig.com/docs/miner/randomx-optimization-guide", "https://xmrig.com/docs/miner/hugepages", "https://github.com/xmrig/xmrig#readme"],
    "stratum": ["https://xmrig.com/docs/miner/config", "https://github.com/xmrig/xmrig-proxy#readme"],
    "solo": ["https://www.getmonero.org/resources/user-guides/solo_mine_GUI.html", "https://xmrig.com/docs/miner/config"],
    "pool": ["https://www.getmonero.org/resources/user-guides/mine-to-pool.html", "https://xmrig.com/docs/miner/config"],
}

# Extra direct-link registries used by the advanced generic resolver.
PY_PACKAGE_DOCS.update({'django': ['https://docs.djangoproject.com/en/stable/', 'https://docs.djangoproject.com/en/stable/ref/'],
 'poetry': ['https://python-poetry.org/docs/'],
 'pip': ['https://pip.pypa.io/en/stable/'],
 'setuptools': ['https://setuptools.pypa.io/en/latest/'],
 'mypy': ['https://mypy.readthedocs.io/en/stable/'],
 'black': ['https://black.readthedocs.io/en/stable/'],
 'matplotlib': ['https://matplotlib.org/stable/api/'],
 'scipy': ['https://docs.scipy.org/doc/scipy/reference/'],
 'pillow': ['https://pillow.readthedocs.io/en/stable/reference/'],
 'opencv-python': ['https://docs.opencv.org/4.x/'],
 'aiohttp': ['https://docs.aiohttp.org/en/stable/'],
 'pyinstaller': ['https://pyinstaller.org/en/stable/'],
 'qasync': ['https://github.com/CabbageDevelopment/qasync#readme'],
 'scapy': ['https://scapy.readthedocs.io/en/latest/']})

DIRECT_DOC_REGISTRY_VERSION = "4.0-advanced-direct-links"

GENERIC_PROFILE_NAMES = {
    "topic_registry", "registry", "direct_catalog", "docs_catalog"
}

def _listify_urls(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value if str(x).strip()]
    return []

def _term_matches_query(term: str, query: str) -> bool:
    term_l = str(term or "").lower().strip()
    q_l = str(query or "").lower()
    if not term_l:
        return False
    if term_l in q_l:
        return True
    # Treat dotted API names and snake/camel terms as searchable fragments.
    clean_term = re.sub(r"[^a-z0-9]+", " ", term_l).strip()
    clean_q = re.sub(r"[^a-z0-9]+", " ", q_l).strip()
    return bool(clean_term and re.search(r"\b" + re.escape(clean_term) + r"\b", clean_q))


def as_bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1","true","yes","y","on")

def as_int(v: Any, default: int, lo: Optional[int] = None, hi: Optional[int] = None) -> int:
    try:
        n = int(v)
    except Exception:
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n

def as_float(v: Any, default: float, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    try:
        n = float(v)
    except Exception:
        n = default
    if lo is not None:
        n = max(lo, n)
    if hi is not None:
        n = min(hi, n)
    return n

def clean_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    p = urlparse(url)
    if p.netloc.endswith("duckduckgo.com") and p.path.startswith("/l/"):
        uddg = parse_qs(p.query).get("uddg", [""])[0]
        if uddg:
            return clean_url(unquote(uddg))
    if p.scheme not in ("http", "https"):
        return ""
    return urlunparse(p._replace(fragment=""))

def absolute_url(base: str, href: str) -> str:
    return clean_url(urljoin(base, href))

def hostname(url: str) -> str:
    try:
        return urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    except Exception:
        return ""

def same_host(a: str, b: str) -> bool:
    return hostname(a) == hostname(b)

def docs_like_url(url: str) -> bool:
    p = urlparse(url)
    host = p.netloc.lower()
    path = p.path.lower()
    return any(x in host for x in DOC_HOST_HINTS) or any(x in path for x in DOC_PATH_HINTS)

def html_url(url: str) -> bool:
    return not urlparse(url).path.lower().endswith(BLOCKED_EXTS)

def tokens(text: str) -> List[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_:]*(?:<[A-Za-z0-9_:,\s]+>)?", text or "")

def score(query: str, title: str, url: str, text: str = "") -> float:
    qterms = []
    seen = set()
    for t in tokens(query):
        low = t.lower().strip()
        if low and low not in seen:
            qterms.append(low)
            seen.add(low)
    title_l, url_l, text_l = (title or "").lower(), (url or "").lower(), (text or "").lower()[:250000]
    s = 0.0
    for t in qterms:
        if t in title_l:
            s += 14
        if t in url_l:
            s += 7
        if text_l:
            s += min(text_l.count(t), 25) * 0.8
    exact = query.lower().strip()
    if exact:
        if exact in title_l:
            s += 35
        if exact in url_l:
            s += 15
        if text_l and exact in text_l:
            s += 18
    if any(h in title_l or h in url_l for h in ("api","reference","class","function","method","module","property","parameter","rpc","mining")):
        s += 5
    if docs_like_url(url):
        s += 8
    return s

def safe_name(value: str, max_len: int = 90) -> str:
    out = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_").lower()
    return (out[:max_len] or "doc")

def dedupe_url(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for item in sorted(items, key=lambda x: float(x.get("score", 0)), reverse=True):
        url = str(item.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            out.append(item)
    return out

class APIDocEngine:
    """APIDoc engine. Direct mode uses catalog/exact URL resolution; search mode uses query search first."""
    def __init__(self, params: Optional[Dict[str, Any]] = None, progress: Optional[Callable[[str], None]] = None):
        self.params = params or {}
        self.progress = progress or (lambda msg: None)
        self.config = self.load_config()
        settings = self.config.get("settings", {})
        self.timeout = as_float(self.params.get("timeout", settings.get("timeout", 20)), 20, 2, 120)
        self.delay = as_float(self.params.get("delay", settings.get("delay", 0.1)), 0.1, 0, 10)
        self.max_pages = as_int(self.params.get("max_pages_per_query", settings.get("max_pages_per_query", 8)), 8, 1, 80)
        self.max_links = as_int(self.params.get("max_links_per_page", settings.get("max_links_per_page", 60)), 60, 0, 800)
        self.max_chars = as_int(self.params.get("max_chars_per_page", settings.get("max_chars_per_page", 14000)), 14000, 500, 250000)
        self.max_direct = as_int(self.params.get("max_direct_urls_per_query", settings.get("max_direct_urls_per_query", 10)), 10, 1, 100)
        self.max_hits = as_int(self.params.get("max_search_hits", settings.get("max_search_hits", 6)), 6, 1, 50)
        self.direct_mode = as_bool(self.params.get("direct_mode"), bool(settings.get("direct_mode", True)))
        self.search_mode = str(self.params.get("search_mode") or ("direct_first" if self.direct_mode else "search_first")).strip().lower().replace("-", "_")
        self.search_fallback = as_bool(self.params.get("search_fallback"), bool(settings.get("search_fallback", False)))
        # Important: when the GUI turns Direct mode OFF, APIDoc must resolve by
        # search query instead of waiting for direct discovery to fail.
        if not self.direct_mode or self.search_mode in {"search", "search_first", "search_only"}:
            self.direct_mode = False
            self.search_fallback = True
            if self.search_mode not in {"search", "search_first", "search_only"}:
                self.search_mode = "search_first"
        self.direct_fallback_when_search_fails = as_bool(self.params.get("direct_fallback_when_search_fails"), bool(settings.get("direct_fallback_when_search_fails", False)))
        self.crawl_direct_pages = as_bool(self.params.get("crawl_direct_pages"), bool(settings.get("crawl_direct_pages", True)))
        self.use_cache = as_bool(self.params.get("use_cache"), True)
        self.cache_dir = Path(str(self.params.get("cache_dir") or settings.get("cache_dir") or ".apidoc_cache"))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": str(self.params.get("user_agent") or _UA), "Accept": "text/html,text/markdown,text/plain,*/*;q=0.8"})
        self._last = 0.0

    def load_config(self) -> Dict[str, Any]:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        path = str(self.params.get("config_path") or self.params.get("sources_config") or "").strip()
        if path and Path(path).exists():
            try:
                user_cfg = json.loads(Path(path).read_text(encoding="utf-8"))
                for section in ("profiles","sources","settings"):
                    if isinstance(user_cfg.get(section), dict):
                        cfg.setdefault(section, {}).update(user_cfg[section])
                self.progress(f"[config] loaded {path}")
            except Exception as e:
                self.progress(f"[config] failed {path}: {e}")
        return cfg

    def read_queries(self, payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        profile = str(self.params.get("profile", "all") or "all")
        qfile = str(self.params.get("query_file") or self.params.get("path") or "").strip()
        if isinstance(payload, dict) and isinstance(payload.get("queries"), list):
            return payload["queries"], {"source": "payload_dict", "count": len(payload["queries"])}
        if qfile:
            p = Path(qfile)
            if not p.exists():
                return [], {"error": "query_file_not_found", "path": str(p), "count": 0}
            jobs = self.parse_query_text(p.read_text(encoding="utf-8"), profile)
            return jobs, {"source": str(p), "count": len(jobs)}
        text = "\n".join(str(x) for x in payload) if isinstance(payload, list) else str(payload or "")
        jobs = self.parse_query_text(text, profile)
        return jobs, {"source": "payload_text", "count": len(jobs)}

    def parse_query_text(self, text: str, default_profile: str) -> List[Dict[str, Any]]:
        jobs = []
        for line_no, raw in enumerate(str(text or "").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if " #" in line:
                line = line.split(" #", 1)[0].strip()
            low = line.lower()
            if low.startswith(("http://","https://")):
                jobs.append({"line": line_no, "raw": raw, "query": line, "profile": default_profile, "direct_url": line, "sources": [], "package": ""})
                continue
            if ":" in line:
                prefix, rest = line.split(":", 1)
                prefix_l, rest = prefix.strip().lower(), rest.strip()
                if prefix_l == "url":
                    jobs.append({"line": line_no, "raw": raw, "query": rest, "profile": default_profile, "direct_url": rest, "sources": [], "package": ""})
                    continue
                if prefix_l in PREFIX_PROFILE:
                    prof = PREFIX_PROFILE[prefix_l]
                    jobs.append({"line": line_no, "raw": raw, "query": rest, "profile": prof, "direct_url": "", "sources": [], "package": self.guess_package(rest) if prof == "python-packages" else ""})
                    continue
                if prefix_l.startswith("source="):
                    jobs.append({"line": line_no, "raw": raw, "query": rest, "profile": "", "direct_url": "", "sources": [prefix_l.split("=",1)[1].strip()], "package": ""})
                    continue
                if prefix_l.startswith("sources="):
                    srcs = [s.strip() for s in prefix_l.split("=",1)[1].split(",") if s.strip()]
                    jobs.append({"line": line_no, "raw": raw, "query": rest, "profile": "", "direct_url": "", "sources": srcs, "package": ""})
                    continue
            jobs.append({"line": line_no, "raw": raw, "query": line, "profile": default_profile, "direct_url": "", "sources": [], "package": self.guess_package(line) if default_profile == "python-packages" else ""})
        return jobs

    def guess_package(self, query: str) -> str:
        parts = re.findall(r"[A-Za-z0-9_.-]+", query or "")
        bad = {"python","package","packages","api","docs","documentation","class","method"}
        return parts[0].lower() if parts and parts[0].lower() not in bad else ""

    def source_keys(self, job: Dict[str, Any]) -> List[str]:
        sources = self.config["sources"]
        profiles = self.config["profiles"]
        explicit = self.params.get("sources") or self.params.get("source_keys") or ""
        if isinstance(explicit, list):
            explicit_keys = [str(x).strip() for x in explicit if str(x).strip()]
        elif str(explicit).strip():
            explicit_keys = [x.strip() for x in str(explicit).split(",") if x.strip()]
        else:
            explicit_keys = []
        keys = job.get("sources") or explicit_keys or profiles.get(str(job.get("profile") or self.params.get("profile") or "all"), profiles["all"])
        out, seen = [], set()
        for k in keys:
            if k in sources and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode("utf-8")).hexdigest() + ".html")

    def http_get(self, url: str, use_cache: Optional[bool] = None):
        url = clean_url(url)
        if not url:
            return None
        use_cache = self.use_cache if use_cache is None else use_cache
        cp = self.cache_path(url)
        if use_cache and cp.exists():
            class R:
                def __init__(self, url, text):
                    self.url = url
                    self.text = text
                    self.headers = {"Content-Type": "text/html; cached"}
                    self.status_code = 200
                def raise_for_status(self): pass
            try:
                return R(url, cp.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
        elapsed = time.monotonic() - self._last
        if self.delay and elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            self._last = time.monotonic()
            r.raise_for_status()
            texty = ("html" in (r.headers.get("Content-Type") or "").lower()) or ("text/plain" in (r.headers.get("Content-Type") or "").lower()) or ("markdown" in (r.headers.get("Content-Type") or "").lower()) or r.text.lstrip().startswith("<")
            if texty:
                try:
                    cp.write_text(r.text, encoding="utf-8")
                except Exception:
                    pass
            return r
        except Exception as e:
            self.progress(f"[http] failed: {url} :: {e}")
            return None

    def http_json(self, url: str):
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None

    def host_allowed(self, url: str, source: Dict[str, Any]) -> bool:
        allowed = [str(x).lower() for x in source.get("allowed_domains", [])]
        if source.get("allow_any_domain") or "*" in allowed:
            return True
        host = hostname(url)
        return any(host == d or host.endswith("." + d) for d in allowed)

    def discover(self, jobs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Resolve candidate documentation URLs.

        Behavior is intentionally split by the GUI's Direct mode checkbox:
        - direct_mode=True: use catalog/exact direct resolvers first, then optional search fallback.
        - direct_mode=False: use the search-query resolver first. Direct resolvers are skipped unless
          direct_fallback_when_search_fails=true. Literal `url:` lines are always honored.
        """
        hits, errors = [], []
        mode = "direct-first" if self.direct_mode else "search-first"
        fallback_src = self.config["sources"].get(
            "open_web_search_fallback",
            {"display_name": "Fallback Search", "allowed_domains": ["*"], "allow_any_domain": True, "docs_like_only": True},
        )

        for i, job in enumerate(jobs, 1):
            q = str(job.get("query", ""))
            if job.get("direct_url"):
                self.progress(f"[url] {i}/{len(jobs)} {str(job.get('direct_url'))[:120]}")
                hits.append(self.hit(job, "direct_url", {"display_name": "Direct URL"}, job["direct_url"], job["direct_url"], 100, "direct_url"))
                continue

            job_hit_start = len(hits)

            if not self.direct_mode:
                self.progress(f"[search] {i}/{len(jobs)} {q[:120]}")
                try:
                    hits.extend(self.search(job, "open_web_search_fallback", fallback_src))
                except Exception as e:
                    errors.append({"stage": "search_first", "query": job.get("query"), "source": "open_web_search_fallback", "error": repr(e)})

                if len(hits) == job_hit_start and self.direct_fallback_when_search_fails:
                    self.progress(f"[direct-after-search-miss] {i}/{len(jobs)} {q[:120]}")
                    for key in self.source_keys(job):
                        src = self.config["sources"][key]
                        try:
                            hits.extend(self.resolve_direct(job, key, src))
                        except Exception as e:
                            errors.append({"stage": "direct_fallback_after_search", "query": job.get("query"), "source": key, "error": repr(e)})
                continue

            self.progress(f"[direct] {i}/{len(jobs)} {q[:120]}")
            for key in self.source_keys(job):
                src = self.config["sources"][key]
                try:
                    source_hits = self.resolve_direct(job, key, src)
                    hits.extend(source_hits)
                except Exception as e:
                    errors.append({"stage": "discover", "query": job.get("query"), "source": key, "error": repr(e)})
            if self.search_fallback and len(hits) == job_hit_start:
                try:
                    self.progress(f"[search-fallback] {i}/{len(jobs)} {q[:120]}")
                    hits.extend(self.search(job, "open_web_search_fallback", fallback_src))
                except Exception as e:
                    errors.append({"stage": "search_fallback", "query": job.get("query"), "source": "open_web_search_fallback", "error": repr(e)})
        return {"queries": jobs, "hits": dedupe_url(hits), "docs": [], "errors": errors, "mode": mode}

    def hit(self, job, key, src, url, title, sc, kind):
        return {"query": job.get("query",""), "line": job.get("line"), "profile": job.get("profile"), "source_key": key, "source_name": src.get("display_name", key), "url": clean_url(str(url)), "title": str(title), "score": float(sc), "kind": kind}

    def resolve_direct(self, job, key, src):
        resolver = str(src.get("resolver") or "")
        if resolver == "python":
            return self.resolve_python(job, key, src)
        if resolver == "pypi":
            return self.resolve_pypi(job, key, src)
        if resolver == "python_package_common":
            return self.resolve_python_package_common(job, key, src)
        if resolver == "dotnet":
            return self.resolve_dotnet(job, key, src)
        if resolver == "csharp":
            return self.resolve_csharp(job, key, src)
        if resolver == "cpp_microsoft":
            return self.resolve_cpp_microsoft(job, key, src)
        if resolver == "cppreference":
            return self.resolve_cppreference(job, key, src)
        if resolver == "monero":
            return self.resolve_monero(job, key, src)
        if resolver == "p2pool":
            return self.resolve_p2pool(job, key, src)
        if resolver == "xmrig":
            return self.resolve_xmrig(job, key, src)
        if resolver == "randomx":
            return self.resolve_randomx(job, key, src)
        if resolver == "search":
            return self.search(job, key, src)
        if resolver in GENERIC_PROFILE_NAMES or src.get("topic_urls") or src.get("match_terms"):
            return self.resolve_topic_registry(job, key, src)
        return self.resolve_seed_source(job, key, src)

    def resolve_python(self, job, key, src):
        q = str(job.get("query") or "")
        low = q.lower()
        urls = []
        base = "https://docs.python.org/3/"
        for name, module in PYTHON_MODULE_MAP.items():
            if re.search(rf"\b{re.escape(name)}\b", low):
                urls.append(base + "library/" + module.lower().replace(".", "/") + ".html")
        for name, page in PYTHON_BUILTIN_MAP.items():
            if re.search(rf"\b{re.escape(name)}\b", low):
                urls.append(base + "library/" + page)
        if any(x in low for x in ("data model","special method","descriptor","metaclass","__get__","__new__","__init_subclass__")):
            urls.append(base + "reference/datamodel.html")
        if any(x in low for x in ("import system","importlib","module spec","namespace package")):
            urls.append(base + "reference/import.html")
            urls.append(base + "library/importlib.html")
        if any(x in low for x in ("grammar","expression","statement","match","pattern")):
            urls.append(base + "reference/index.html")
        urls += src.get("seed_urls", [])
        return [self.hit(job, key, src, u, u, score(q, "", u), "direct_python") for u in dict.fromkeys(urls).keys()][:self.max_direct]

    def resolve_pypi(self, job, key, src):
        pkg = str(job.get("package") or self.guess_package(str(job.get("query") or ""))).strip()
        if not pkg:
            return []
        q = str(job.get("query") or "")
        hits = [self.hit(job, key, src, f"https://pypi.org/project/{pkg}/", f"PyPI project {pkg}", score(q, pkg, f"https://pypi.org/project/{pkg}/") + 5, "direct_pypi_project")]
        data = self.http_json(f"https://pypi.org/pypi/{pkg}/json")
        if not data:
            return hits
        info = data.get("info") or {}
        pairs = []
        for field in ("docs_url","project_url","home_page"):
            if info.get(field):
                pairs.append((field, info[field]))
        for label, url in (info.get("project_urls") or {}).items():
            if any(x in label.lower() for x in ("doc","home","source","github","repository")):
                pairs.append((label, url))
        for label, url in pairs:
            u = clean_url(str(url))
            if u:
                hits.append(self.hit(job, key, src, u, f"PyPI {pkg} {label}", score(q, label, u) + 20, "direct_pypi_metadata"))
        return dedupe_url(hits)[:self.max_direct]

    def resolve_python_package_common(self, job, key, src):
        q = str(job.get("query") or "")
        pkg = str(job.get("package") or self.guess_package(q)).lower()
        urls = []
        for name, mapped in PY_PACKAGE_DOCS.items():
            if name in q.lower() or name == pkg:
                urls.extend(mapped)
        return [self.hit(job, key, src, u, u, score(q, "", u), "direct_package_known") for u in dict.fromkeys(urls).keys()][:self.max_direct]

    def dotnet_type_slug(self, typename: str) -> str:
        t = typename.strip().strip(".")
        m = re.match(r"(.+?)<(.+)>", t)
        if m:
            base = m.group(1)
            arity = len([x for x in m.group(2).split(",") if x.strip()])
            return base.lower() + f"-{arity}"
        return t.lower()

    def resolve_dotnet(self, job, key, src):
        q = str(job.get("query") or "")
        urls = []
        candidates = re.findall(r"\bSystem(?:\.[A-Za-z_][A-Za-z0-9_]*)+(?:<[^>]+>)?", q)
        for t in candidates:
            slug = self.dotnet_type_slug(t)
            urls.append(f"https://learn.microsoft.com/en-us/dotnet/api/{slug}?view=net-9.0")
            # append likely method tokens that follow the type in the query
            after = q[q.find(t)+len(t):]
            for method in re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", after):
                if method.lower() not in {"api","documentation","docs"}:
                    urls.append(f"https://learn.microsoft.com/en-us/dotnet/api/{slug}.{method.lower()}?view=net-9.0")
        if not urls:
            urls.extend(src.get("seed_urls", []))
        return [self.hit(job, key, src, u, u, score(q, "", u), "direct_dotnet") for u in dict.fromkeys(urls).keys()][:self.max_direct]

    def resolve_csharp(self, job, key, src):
        q = str(job.get("query") or "")
        low = q.lower()
        urls = []
        for term, url in CSHARP_TOPIC_MAP.items():
            if term in low:
                urls.append(url)
        urls.extend(src.get("seed_urls", []))
        return [self.hit(job, key, src, u, u, score(q, "", u), "direct_csharp") for u in dict.fromkeys(urls).keys()][:self.max_direct]

    def normalize_cpp_terms(self, query: str) -> Set[str]:
        cleaned = str(query or "").replace("std::", " ")
        cleaned = re.sub(r"[^A-Za-z0-9_:+#<>\s-]+", " ", cleaned)
        raw_terms = set()
        for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", cleaned):
            low = t.lower()
            if low not in {"c", "cpp", "cxx", "api", "docs", "documentation", "reference", "class", "method", "function", "standard"}:
                raw_terms.add(low)
        if "unordered" in raw_terms and "map" in raw_terms:
            raw_terms.add("unordered_map")
        if "unordered" in raw_terms and "set" in raw_terms:
            raw_terms.add("unordered_set")
        if "string" in raw_terms and "view" in raw_terms:
            raw_terms.add("string_view")
        if "unique" in raw_terms and "ptr" in raw_terms:
            raw_terms.add("unique_ptr")
        if "shared" in raw_terms and "ptr" in raw_terms:
            raw_terms.add("shared_ptr")
        if "standard" in raw_terms and "library" in raw_terms:
            raw_terms.add("standard library")
        return raw_terms

    def resolve_cpp_microsoft(self, job, key, src):
        q = str(job.get("query") or "")
        low = q.lower()
        urls = []
        terms = self.normalize_cpp_terms(q)
        for term, url in CPP_MICROSOFT_TOPIC_MAP.items():
            if term in terms or term in low:
                urls.append(url)

        # Try Microsoft Learn's predictable C++ Standard Library page pattern for std:: names.
        for std_name in re.findall(r"std::([A-Za-z_][A-Za-z0-9_]*)", q):
            name = std_name.lower()
            if name not in CPP_MICROSOFT_TOPIC_MAP:
                urls.append(f"https://learn.microsoft.com/en-us/cpp/standard-library/{name}?view=msvc-170")

        if not urls:
            urls.extend(src.get("seed_urls", []))
        else:
            # Always add the root pages after specific hits so crawling has a reliable fallback.
            urls.extend(src.get("seed_urls", [])[:2])
        return [self.hit(job, key, src, u, u, score(q, "", u), "direct_cpp_microsoft") for u in dict.fromkeys(urls).keys()][:self.max_direct]

    def resolve_cppreference(self, job, key, src):
        q = str(job.get("query") or "")
        low = q.lower()
        urls = []
        terms = self.normalize_cpp_terms(q)
        for term, url in CPPREFERENCE_TOPIC_MAP.items():
            if term in terms or term in low:
                urls.append(url)

        for container, member in CPPREFERENCE_MEMBER_MAP:
            if container in terms and member in low:
                urls.append(CPPREFERENCE_MEMBER_MAP[(container, member)])

        # Direct header lookup: "cpp: <vector>" or "cpp: header vector".
        for header in re.findall(r"<([A-Za-z0-9_./-]+)>", q):
            h = header.strip().lower()
            if h:
                urls.append(f"https://en.cppreference.com/w/cpp/header/{h}")

        if not urls:
            urls.extend(src.get("seed_urls", []))
        else:
            urls.extend(src.get("seed_urls", [])[:2])
        return [self.hit(job, key, src, u, u, score(q, "", u), "direct_cppreference") for u in dict.fromkeys(urls).keys()][:self.max_direct]

    def resolve_monero(self, job, key, src):
        q = str(job.get("query") or "")
        low = q.lower()
        urls = []
        for term, mapped in MONERO_TOPIC_MAP.items():
            if term in low:
                urls.extend(mapped)
        if not urls:
            urls.extend(src.get("seed_urls", []))
        return [self.hit(job, key, src, u, u, score(q, "", u), "direct_monero") for u in dict.fromkeys(urls).keys()][:self.max_direct]

    def resolve_p2pool(self, job, key, src):
        q = str(job.get("query") or "")
        low = q.lower()
        urls = []
        if any(x in low for x in ("p2pool","sidechain","share","payout","mini","stratum")):
            urls.extend(src.get("seed_urls", []))
        return [self.hit(job, key, src, u, u, score(q, "", u), "direct_p2pool") for u in dict.fromkeys(urls).keys()][:self.max_direct]

    def resolve_xmrig(self, job, key, src):
        q = str(job.get("query") or "")
        low = q.lower()
        urls = []
        if any(x in low for x in ("xmrig","randomx","huge","msr","stratum","pool","hashrate","cpu","mining")):
            urls.extend(src.get("seed_urls", []))
        return [self.hit(job, key, src, u, u, score(q, "", u), "direct_xmrig") for u in dict.fromkeys(urls).keys()][:self.max_direct]

    def resolve_randomx(self, job, key, src):
        q = str(job.get("query") or "")
        low = q.lower()
        urls = []
        if "randomx" in low or "proof of work" in low or "pow" in low:
            urls.extend(src.get("seed_urls", []))
        return [self.hit(job, key, src, u, u, score(q, "", u), "direct_randomx") for u in dict.fromkeys(urls).keys()][:self.max_direct]

    def job_is_explicit_for_source(self, job: Dict[str, Any], src: Dict[str, Any]) -> bool:
        profile = str(job.get("profile") or self.params.get("profile") or "all").strip().lower()
        source_profile = str(src.get("profile") or "").strip().lower()
        if profile and source_profile and profile == source_profile:
            return True
        # Explicit source= or sources= lines should always be honored.
        if job.get("sources"):
            return True
        return False

    def resolve_seed_source(self, job, key, src):
        q = str(job.get("query") or "")
        if self.job_is_explicit_for_source(job, src) or any(_term_matches_query(t, q) for t in src.get("match_terms", [])):
            return [self.hit(job, key, src, u, u, score(q, "", u) + 2, "direct_seed") for u in dict.fromkeys(src.get("seed_urls", [])).keys()][:self.max_direct]
        return []

    def resolve_topic_registry(self, job, key, src):
        q = str(job.get("query") or "")
        urls: List[Tuple[str, str]] = []
        explicit = self.job_is_explicit_for_source(job, src)
        if explicit or any(_term_matches_query(t, q) for t in src.get("match_terms", [])):
            for u in src.get("seed_urls", []):
                urls.append((str(u), "direct_registry_seed"))
        for term, mapped in (src.get("topic_urls") or {}).items():
            if _term_matches_query(str(term), q) or explicit:
                for u in _listify_urls(mapped):
                    urls.append((u, "direct_registry_topic"))
        # Dotted API names are common in Java/C#/.NET/Bannerlord. Try predictable doc paths.
        urls.extend((u, "direct_registry_pattern") for u in self.pattern_urls_for_source(q, src))
        seen = []
        for u, kind in urls:
            cu = clean_url(str(u))
            if cu and cu not in [x[0] for x in seen]:
                seen.append((cu, kind))
        return [self.hit(job, key, src, u, u, score(q, "", u) + (10 if kind.endswith("topic") else 0), kind) for u, kind in seen][:self.max_direct]

    def pattern_urls_for_source(self, query: str, src: Dict[str, Any]) -> List[str]:
        profile = str(src.get("profile") or "").lower()
        q = str(query or "")
        out: List[str] = []
        if profile == "java" or "docs.oracle.com" in ",".join(src.get("allowed_domains", [])):
            for cls in re.findall(r"\b(java(?:x)?(?:\.[A-Za-z_][A-Za-z0-9_]*){2,})\b", q):
                parts = cls.split(".")
                if len(parts) >= 3:
                    package = "/".join(parts[:-1])
                    out.append(f"https://docs.oracle.com/en/java/javase/25/docs/api/java.base/{package}/{parts[-1]}.html")
        if profile == "go" or "pkg.go.dev" in ",".join(src.get("allowed_domains", [])):
            for pkg in re.findall(r"\b([a-z][a-z0-9_/]+/[a-z0-9_/]+)\b", q.lower()):
                out.append(f"https://pkg.go.dev/{pkg}")
        if profile == "rust" or "doc.rust-lang.org" in ",".join(src.get("allowed_domains", [])):
            for path in re.findall(r"\bstd::([A-Za-z0-9_:]+)\b", q):
                base = path.replace("::", "/")
                out.append(f"https://doc.rust-lang.org/std/{base}/")
        if profile == "bannerlord" or "bannerlordapi.butr.link" in ",".join(src.get("allowed_domains", [])):
            for typename in re.findall(r"\bTaleWorlds(?:\.[A-Za-z_][A-Za-z0-9_]*){1,}\b", q):
                out.append(f"https://bannerlordapi.butr.link/api/core/{typename}.html")
                out.append(f"https://bannerlordapi.butr.link/api/native/{typename}.html")
        return out

    def catalog_markdown(self) -> str:
        lines = ["# APIDoc Direct Link Catalog", "", f"Registry version: `{DIRECT_DOC_REGISTRY_VERSION}`", ""]
        for profile, keys in sorted(self.config.get("profiles", {}).items()):
            lines += [f"## Profile: `{profile}`", ""]
            for key in keys:
                src = self.config.get("sources", {}).get(key, {})
                lines.append(f"### `{key}` — {src.get('display_name', key)}")
                lines.append(f"- Resolver: `{src.get('resolver','')}`")
                if src.get("match_terms"):
                    lines.append(f"- Match terms: `{', '.join(str(x) for x in src.get('match_terms', [])[:24])}`")
                if src.get("seed_urls"):
                    lines.append("- Seed URLs:")
                    for u in src.get("seed_urls", [])[:40]:
                        lines.append(f"  - {u}")
                if src.get("topic_urls"):
                    lines.append("- Topic URLs:")
                    for term, urls in list((src.get("topic_urls") or {}).items())[:40]:
                        joined = ", ".join(_listify_urls(urls)[:4])
                        lines.append(f"  - `{term}` → {joined}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def search(self, job, key, src):
        if not self.search_fallback:
            return []
        q = str(job.get("query") or "")
        if not q.strip():
            return []
        ddg = self.config["settings"].get("duckduckgo_url", "https://duckduckgo.com/html/?q={query}")
        r = self.http_get(ddg.format(query=quote_plus(q)), use_cache=False)
        if not r:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        hits = []
        for a in soup.select("a.result__a, h2 a[href], .result a[href], a[href]"):
            u = clean_url(a.get("href") or "")
            title = a.get_text(" ", strip=True) or u
            if not u.startswith(("http://", "https://")):
                continue
            if src.get("docs_like_only") and not docs_like_url(u):
                continue
            hits.append(self.hit(job, key, src, u, title, score(q, title, u), "search_query" if not self.direct_mode else "search_fallback"))
            if len(hits) >= self.max_hits:
                break
        return hits

    def fetch_extract(self, bundle: Dict[str, Any]) -> Dict[str, Any]:
        docs, errors = [], list(bundle.get("errors", []))
        by_query: Dict[str, List[Dict[str, Any]]] = {}
        for hit in bundle.get("hits", []):
            by_query.setdefault(str(hit.get("query") or ""), []).append(hit)
        for i, job in enumerate(bundle.get("queries", []), 1):
            q = str(job.get("query") or "")
            hits = sorted(by_query.get(q, []), key=lambda h: h.get("score", 0), reverse=True)
            self.progress(f"[fetch] {i}/{len(bundle.get('queries', []))} {q[:100]} :: {len(hits)} resolved urls")
            qdocs, qerrs = self.fetch_query(job, hits)
            docs.extend(qdocs); errors.extend(qerrs)
        out = dict(bundle)
        out["docs"] = dedupe_url(docs)
        out["errors"] = errors
        return out

    def fetch_query(self, job, hits):
        docs, errors, seen = [], [], set()
        queue = list(hits)
        scan_limit = self.max_pages if not self.crawl_direct_pages else self.max_pages * 3
        while queue and len(seen) < scan_limit and len(docs) < self.max_pages:
            hit = queue.pop(0)
            url = clean_url(str(hit.get("url") or ""))
            if not url or url in seen:
                continue
            seen.add(url)
            src = self.config["sources"].get(str(hit.get("source_key") or ""), {"allowed_domains": ["*"], "allow_any_domain": True})
            doc = self.extract_page(url, str(job.get("query") or ""), hit, src)
            if not doc:
                continue
            docs.append(doc)
            if not self.crawl_direct_pages:
                continue
            links = sorted([u for u in doc.get("links", []) if u not in seen and self.can_follow(doc["url"], u, src)], key=lambda u: score(job.get("query",""), "", u), reverse=True)
            for link in links[: max(1, self.max_links // 3)]:
                queue.append({**hit, "url": link, "title": link, "kind": "direct_crawl", "score": score(job.get("query",""), "", link)})
        return sorted(docs, key=lambda d: d.get("score",0), reverse=True)[:self.max_pages], errors

    def can_follow(self, parent_url: str, link: str, source: Dict[str, Any]) -> bool:
        if not html_url(link):
            return False
        if not self.host_allowed(link, source):
            return False
        if source.get("same_host_crawl_only", True) and not same_host(parent_url, link):
            return False
        if source.get("docs_like_only") and not docs_like_url(link):
            return False
        return True

    def extract_page(self, url, query, hit, src):
        r = self.http_get(url)
        if not r or not getattr(r, "text", ""):
            return None
        ctype = (r.headers.get("Content-Type") or "").lower()
        is_markdown = "markdown" in ctype or "text/plain" in ctype or "raw.githubusercontent.com" in hostname(r.url)
        if is_markdown:
            text = r.text
            if len(text) > self.max_chars:
                text = text[:self.max_chars].rstrip() + "\n\n...[clipped]..."
            title = str(hit.get("title") or r.url)
            return {"query": query, "source_key": hit.get("source_key"), "source_name": hit.get("source_name"), "url": clean_url(r.url), "title": title, "score": score(query, title, r.url, text) + float(hit.get("score",0))*0.15, "headings": re.findall(r"^#{1,4}\s+(.+)$", text, flags=re.M)[:50], "text": text, "links": [], "hit_kind": hit.get("kind")}
        if "html" not in ctype and not r.text.lstrip().startswith("<"):
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        self.drop_noise(soup); self.preserve_code(soup)
        title = self.title(soup)
        headings = []
        for tag in soup.find_all(["h1","h2","h3","h4"]):
            h = tag.get_text(" ", strip=True)
            if h and h not in headings:
                headings.append(h)
            if len(headings) >= 50:
                break
        main = soup.find("main") or soup.find("article") or soup.find("div", {"role": "main"}) or soup.find("div", id=re.compile(r"(main|content|body|article|learn)", re.I)) or soup.find("div", class_=re.compile(r"(document|content|body|main|article|markdown|learn)", re.I)) or soup.body or soup
        text = main.get_text("\n", strip=True) if main else soup.get_text("\n", strip=True)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) > self.max_chars:
            text = text[:self.max_chars].rstrip() + "\n\n...[clipped]..."
        raw = BeautifulSoup(r.text, "html.parser")
        links = []
        for a in raw.find_all("a", href=True):
            href = a.get("href","").strip()
            if not href or href.startswith(("mailto:","javascript:","tel:")):
                continue
            link = absolute_url(r.url, href)
            if link and html_url(link) and link not in links:
                links.append(link)
            if len(links) >= self.max_links:
                break
        return {"query": query, "source_key": hit.get("source_key"), "source_name": hit.get("source_name"), "url": clean_url(r.url), "title": title, "score": score(query, title, r.url, text) + float(hit.get("score",0))*0.15, "headings": headings, "text": text, "links": links, "hit_kind": hit.get("kind")}

    def drop_noise(self, soup):
        for tag in soup(["script","style","noscript","svg","canvas","form","iframe"]):
            tag.decompose()
        for sel in ["nav","header","footer",".sidebar",".sphinxsidebar",".wy-nav-side",".toc",".breadcrumb",".search",".navbar","[role='navigation']","[role='banner']","[role='contentinfo']"]:
            for node in soup.select(sel):
                node.decompose()

    def preserve_code(self, soup):
        for pre in soup.find_all("pre"):
            t = pre.get_text("\n", strip=False).strip()
            if t:
                pre.replace_with(soup.new_string("\n\n```text\n" + t + "\n```\n\n"))
        for code in soup.find_all("code"):
            t = code.get_text(" ", strip=True)
            if t:
                code.replace_with(soup.new_string(f"`{t}`"))

    def title(self, soup):
        h1 = soup.find("h1")
        if h1 and h1.get_text(" ", strip=True):
            return h1.get_text(" ", strip=True)
        meta = soup.find("meta", attrs={"property": "og:title"})
        if meta and meta.get("content"):
            return str(meta.get("content")).strip()
        if soup.title:
            return soup.title.get_text(" ", strip=True)
        return "Untitled documentation page"

    def rank_bundle(self, bundle):
        by_q: Dict[str, List[Dict[str, Any]]] = {}
        for doc in bundle.get("docs", []):
            by_q.setdefault(str(doc.get("query") or ""), []).append(doc)
        docs = []
        for q, qdocs in by_q.items():
            docs.extend(sorted(dedupe_url(qdocs), key=lambda d: d.get("score",0), reverse=True)[:self.max_pages])
        out = dict(bundle); out["docs"] = dedupe_url(docs); return out

    def markdown(self, bundle):
        return format_apidoc_bundle(bundle, self.params, search_fallback=self.search_fallback)

    def write_outputs(self, bundle, markdown):
        out_path = str(self.params.get("out_path") or self.params.get("out") or "").strip()
        if not out_path:
            return {"wrote": False}
        p = Path(out_path)
        if p.suffix.lower() == ".md":
            md_path = p; assets = p.with_suffix(".assets")
        else:
            p.mkdir(parents=True, exist_ok=True); md_path = p / "apidocs.md"; assets = p / "apidocs.assets"
        pages = assets / "pages"
        pages.mkdir(parents=True, exist_ok=True)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        for i, doc in enumerate(bundle.get("docs", []), 1):
            page = pages / f"{i:04d}_{safe_name(doc.get('source_key','source'))}_{safe_name(doc.get('title','doc'))}.txt"
            page.write_text(str(doc.get("text") or ""), encoding="utf-8")
            doc["saved_text_path"] = str(page)
        markdown = self.markdown(bundle)
        md_path.write_text(markdown, encoding="utf-8")
        jsonl = assets / "apidocs.jsonl"
        with jsonl.open("w", encoding="utf-8") as f:
            for doc in bundle.get("docs", []):
                f.write(json.dumps(doc, ensure_ascii=False) + "\n")
        manifest = assets / "manifest.json"
        manifest.write_text(json.dumps({"query_count": len(bundle.get("queries", [])), "direct_url_count": len(bundle.get("hits", [])), "doc_count": len(bundle.get("docs", [])), "markdown_path": str(md_path), "jsonl_path": str(jsonl)}, indent=2), encoding="utf-8")
        return {"wrote": True, "markdown_path": str(md_path), "jsonl_path": str(jsonl), "assets_dir": str(assets), "manifest_path": str(manifest)}

    def run_all(self, payload):
        started = time.time()
        self.progress("[apidoc] parse queries")
        jobs, parse_meta = self.read_queries(payload)
        if parse_meta.get("error") or not jobs:
            bundle = {"queries": jobs, "hits": [], "docs": [], "errors": [{"stage": "parse", **parse_meta}], "mode": "direct-first" if self.direct_mode else "search-first"}
            return self.markdown(bundle), bundle, {"ok": False, "parse": parse_meta, "error": parse_meta.get("error") or "no_queries"}
        mode = "direct-first" if self.direct_mode else "search-first"
        self.progress(f"[apidoc] {mode} URL resolution")
        discovered = self.discover(jobs)
        self.progress("[apidoc] fetch resolved pages")
        fetched = self.fetch_extract(discovered)
        self.progress("[apidoc] rank/dedupe")
        ranked = self.rank_bundle(fetched)
        ranked["mode"] = mode
        self.progress("[apidoc] render markdown")
        md = self.markdown(ranked)
        self.progress("[apidoc] write output")
        write_meta = self.write_outputs(ranked, md)
        meta = {"ok": True, "mode": mode, "direct_mode": self.direct_mode, "search_mode": self.search_mode, "search_fallback": self.search_fallback, "direct_fallback_when_search_fails": self.direct_fallback_when_search_fails, "parse": parse_meta, "query_count": len(jobs), "resolved_url_count": len(ranked.get("hits", [])), "direct_url_count": len([h for h in ranked.get("hits", []) if str(h.get("kind", "")).startswith("direct")]), "search_url_count": len([h for h in ranked.get("hits", []) if "search" in str(h.get("kind", ""))]), "doc_count": len(ranked.get("docs", [])), "error_count": len(ranked.get("errors", [])), "write": write_meta, "elapsed_sec": round(time.time() - started, 3)}
        return self.markdown(ranked), ranked, meta


# ---------------------------------------------------------------------------
# Readable output formatting blocks
# ---------------------------------------------------------------------------
# This section is intentionally standalone.  It does not change the APIDoc
# discovery/fetch signatures above; it only changes how fetched bundles and raw
# text are rendered.  Old block names still register below, and old parameters
# are still accepted.  New parameters are optional and default to readable output.

FORMAT_STYLE_ALIASES = {
    "doc": "advanced_report",
    "docs": "advanced_report",
    "full": "advanced_report",
    "default": "advanced_report",
    "readable": "advanced_report",
    "clean": "clean_report",  # kept as explicit simple report mode
    "clean_report": "clean_report",
    "report": "advanced_report",
    "compact": "compact",
    "brief": "compact",
    "chat": "chat",
    "answer": "chat",
    "query_pack": "query_pack",
    "queries": "query_pack",
    "links": "query_pack",
    "asset_index": "asset_index",
    "assets": "asset_index",
    "outline": "outline",
    "plain": "plain",
    "text": "plain",
    "raw": "raw",
    "advanced": "advanced_report",
    "advanced_report": "advanced_report",
    "intel": "advanced_report",
    "intelligence": "advanced_report",
    "source_health": "source_health",
    "health": "source_health",
    "coverage": "coverage",
    "profile_matrix": "coverage",
    "batch_plan": "batch_plan",
}

FORMAT_COMMON_PARAMS = {
    "output_style": "advanced_report | clean_report | compact | chat | query_pack | asset_index | coverage | source_health | batch_plan | outline | plain | raw",
    "format_width": 100,
    "include_toc": True,
    "toc_scope": "structural_only | all_headings",
    "max_toc_items": 80,
    "include_summary": True,
    "include_query_list": True,
    "include_candidates": True,
    "include_results": True,
    "include_headings": True,
    "include_errors": True,
    "include_text": False,
    "include_page_excerpt": True,
    "include_full_text": False,
    "excerpt_chars": 1200,
    "compact_text_chars": 1800,
    "max_query_list": 500,
    "max_candidates": 120,
    "max_docs_per_query": 8,
    "max_results_total": 300,
    "max_text_chars": 0,
    "max_code_chars": 0,
    "code_language": "text",
    "heading_offset": 0,
    "front_matter": False,
    "title": "Optional output title.",
}

STRUCTURAL_TOC_TITLES = (
    "Summary",
    "Query List",
    "Direct URL Candidates",
    "Results",
    "Asset Index",
    "Errors",
)

MOJIBAKE_REPLACEMENTS = {
    "â€™": "’",
    "â€˜": "‘",
    "â€œ": "“",
    "â€": "”",
    "â€“": "–",
    "â€”": "—",
    "â€¦": "…",
    "â€¢": "•",
    "Â ": " ",
    "Â": "",
    "ï¸": "",
    "ðŸ’¡": "💡",
    "ðŸš€": "🚀",
}


def _param(params: Dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in params and params.get(name) not in (None, ""):
            return params.get(name)
    return default


def _stringify_payload(payload: Any, *, pretty_json: bool = True) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    if isinstance(payload, (dict, list, tuple)):
        try:
            return json.dumps(payload, indent=2 if pretty_json else None, ensure_ascii=False, default=str)
        except Exception:
            return str(payload)
    return str(payload)


def _clean_mojibake(text: Any) -> str:
    s = _stringify_payload(text)
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        s = s.replace(bad, good)
    return s


def _strip_markdown_inline(text: str) -> str:
    s = re.sub(r"`([^`]+)`", r"\1", str(text or ""))
    s = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", s)
    s = re.sub(r"[*_~]+", "", s)
    return s.strip()


def _slugify_heading(text: str) -> str:
    base = _strip_markdown_inline(text)
    slug = re.sub(r"[^a-z0-9 -]+", "", base.lower())
    slug = re.sub(r"\s+", "-", slug).strip("-")
    return slug or "section"


def _looks_like_noise_heading(title: str) -> bool:
    s = _strip_markdown_inline(title)
    low = s.lower().strip()
    if not s:
        return True
    if len(s) > 160:
        return True
    if re.match(r"^</?[a-z][^>]*>$", s, re.I):
        return True
    if low in {"none", "ok", "body", "html", "head", "title", "p", "a", "b", "c", "section"}:
        return True
    if low.startswith(("u'", "u\"", "<class ", "<type ", "attributeerror", "keyerror")):
        return True
    if low.startswith(("query: https://", "https://", "http://")) and len(s) > 80:
        return True
    return False


def _safe_heading(title: Any, default: str = "Untitled") -> str:
    s = _clean_mojibake(title).replace("\n", " ").strip()
    s = re.sub(r"\s+", " ", s)
    if not s:
        s = default
    if len(s) > 120:
        s = s[:117].rstrip() + "..."
    return s


def _coerce_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        if isinstance(payload.get("rows"), list):
            rows = payload.get("rows") or []
        elif isinstance(payload.get("docs"), list):
            rows = payload.get("docs") or []
        elif isinstance(payload.get("hits"), list):
            rows = payload.get("hits") or []
        else:
            rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = [{"text": _stringify_payload(payload)}]

    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append(row if isinstance(row, dict) else {"value": row})
    return out


def _markdown_cell(value: Any) -> str:
    text = _clean_mojibake(value)
    text = text.replace("\n", " ").replace("|", "\\|").strip()
    return text


def _bool_param(params: Dict[str, Any], name: str, default: bool) -> bool:
    return as_bool(_param(params, name, default=default), default)


def _int_param(params: Dict[str, Any], name: str, default: int, lo: int = 0, hi: int = 10_000_000) -> int:
    return as_int(_param(params, name, default=default), default, lo, hi)


class OutputFormatter:
    """Readable Markdown/text formatter shared by all formatting blocks."""

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self.params = params or {}
        raw_style = str(_param(self.params, "output_style", "format_style", "style", default="clean_report") or "clean_report")
        self.style = FORMAT_STYLE_ALIASES.get(raw_style.strip().lower(), raw_style.strip().lower())
        self.width = as_int(_param(self.params, "format_width", "width", default=100), 100, 40, 240)
        self.heading_offset = as_int(_param(self.params, "heading_offset", default=0), 0, 0, 4)
        self.max_text_chars = as_int(_param(self.params, "max_text_chars", default=0), 0, 0, 10_000_000)
        self.max_code_chars = as_int(_param(self.params, "max_code_chars", default=0), 0, 0, 10_000_000)
        self.code_language = str(_param(self.params, "code_language", "lang", default="text") or "text")

    def clean(self, text: Any) -> str:
        s = _clean_mojibake(text)
        s = s.replace("\ufeff", "")
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        s = re.sub(r"[\t ]+\n", "\n", s)
        s = re.sub(r"\n{4,}", "\n\n\n", s)
        s = re.sub(r"[ \t]{2,}", " ", s)
        return s.strip()

    def clip(self, text: Any, limit: Optional[int] = None) -> str:
        s = _stringify_payload(text)
        limit = self.max_text_chars if limit is None else int(limit or 0)
        if limit > 0 and len(s) > limit:
            return s[:limit].rstrip() + "\n\n...[clipped]..."
        return s

    def normalize_bullets(self, text: Any) -> str:
        s = self.clean(text)
        s = re.sub(r"(?m)^[ \t]*[•◦▪▫‣][ \t]+", "- ", s)
        s = re.sub(r"(?m)^[ \t]*[-*+][ \t]+", "- ", s)
        return s

    def _wrap_words(self, text: str, width: Optional[int] = None) -> str:
        width = width or self.width
        words = text.replace("\n", " ").split()
        if not words:
            return ""
        lines: List[str] = []
        line = ""
        for word in words:
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        return "\n".join(lines)

    def reflow(self, text: Any) -> str:
        s = self.normalize_bullets(text)
        out: List[str] = []
        in_code = False
        for block in re.split(r"\n\s*\n", s):
            raw = block.rstrip()
            if not raw:
                continue
            if raw.strip().startswith("```"):
                in_code = not in_code
                out.append(raw)
                continue
            if in_code or raw.startswith(("#", "- ", ">", "|")) or re.match(r"^\s*\d+[.)]\s+", raw):
                out.append(raw)
                continue
            out.append(self._wrap_words(raw))
        return "\n\n".join(out).strip()

    def code_fence(self, text: Any, language: Optional[str] = None) -> str:
        lang = language if language is not None else self.code_language
        body = self.clip(self.clean(text), self.max_code_chars)
        body = body.replace("```", "`\u200b``")
        return f"```{lang}\n{body}\n```"

    def heading(self, title: Any, level: int = 1) -> str:
        n = max(1, min(6, int(level) + self.heading_offset))
        return "#" * n + " " + _safe_heading(title)

    def callout(self, text: Any, kind: str = "note", title: str = "") -> str:
        title = _safe_heading(title or str(kind or "note").title())
        body = self.clean(text)
        quoted = "\n".join("> " + line if line else ">" for line in body.splitlines())
        return f"> **{title}:**\n{quoted}"

    def table(self, payload: Any, columns: Optional[List[str]] = None) -> str:
        rows = _coerce_rows(payload)
        if not rows:
            return ""
        if columns is None:
            columns = []
            for row in rows:
                for key in row.keys():
                    k = str(key)
                    if k not in columns:
                        columns.append(k)
                    if len(columns) >= 8:
                        break
                if len(columns) >= 8:
                    break
            if not columns:
                columns = ["value"]
        header = "| " + " | ".join(_markdown_cell(c) for c in columns) + " |"
        sep = "| " + " | ".join("---" for _ in columns) + " |"
        body = ["| " + " | ".join(_markdown_cell(row.get(c, "")) for c in columns) + " |" for row in rows]
        return "\n".join([header, sep] + body)

    def _heading_lines(self, markdown: Any, max_depth: int = 3, structural_only: bool = False) -> List[Tuple[int, str]]:
        s = _stringify_payload(markdown)
        headings: List[Tuple[int, str]] = []
        in_code = False
        for line in s.splitlines():
            stripped = line.strip()
            if stripped.startswith("```"):
                in_code = not in_code
                continue
            if in_code or stripped.startswith(">") or stripped.startswith("|"):
                continue
            m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if not m:
                continue
            depth = len(m.group(1))
            if depth > max_depth:
                continue
            title = _strip_markdown_inline(m.group(2))
            if _looks_like_noise_heading(title):
                continue
            if structural_only and depth >= 3 and not title.startswith("Query:"):
                continue
            if structural_only and title.startswith("Query:"):
                continue
            headings.append((depth, title))
        return headings

    def toc(self, markdown: Any, max_depth: int = 3, *, structural_only: bool = False, max_items: int = 80) -> str:
        seen: Dict[str, int] = {}
        lines: List[str] = []
        for depth, title in self._heading_lines(markdown, max_depth=max_depth, structural_only=structural_only):
            slug = _slugify_heading(title)
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            anchor = slug if count == 0 else f"{slug}-{count}"
            indent = "  " * max(0, depth - 1)
            lines.append(f"{indent}- [{title}](#{anchor})")
            if max_items and len(lines) >= max_items:
                lines.append(f"  - ... {max(0, len(self._heading_lines(markdown, max_depth=max_depth)) - max_items)} more headings omitted")
                break
        return "\n".join(lines)

    def outline(self, markdown: Any) -> str:
        items = []
        for depth, title in self._heading_lines(markdown, max_depth=6, structural_only=False):
            items.append("  " * (depth - 1) + f"- {title}")
        return "\n".join(items).strip()

    def front_matter(self, meta: Dict[str, Any]) -> str:
        safe = {k: v for k, v in meta.items() if isinstance(v, (str, int, float, bool, type(None), list, dict))}
        return "---\n" + json.dumps(safe, indent=2, ensure_ascii=False, default=str) + "\n---\n"

    def format_text(self, payload: Any) -> str:
        if self.style == "raw":
            return _stringify_payload(payload)
        if self.style == "outline":
            return self.outline(payload) or self.reflow(payload)
        return self.reflow(payload)

    def apply_template(self, payload: Any, template: str) -> str:
        data = payload if isinstance(payload, dict) else {"payload": _stringify_payload(payload), "text": _stringify_payload(payload)}
        try:
            return str(template).format(**data)
        except Exception as exc:
            return f"{template}\n\n<!-- template_error: {exc} -->"

    def pipeline(self, payload: Any, steps: List[str]) -> str:
        value: Any = payload
        for step in [s.strip().lower() for s in steps if s.strip()]:
            if step in {"clean", "normalize"}:
                value = self.clean(value)
            elif step in {"bullets", "normalize_bullets"}:
                value = self.normalize_bullets(value)
            elif step in {"wrap", "reflow"}:
                value = self.reflow(value)
            elif step in {"code", "code_fence", "fence"}:
                value = self.code_fence(value)
            elif step == "toc":
                value = self.toc(value)
            elif step == "outline":
                value = self.outline(value)
            elif step == "table":
                value = self.table(value)
            else:
                value = self.format_text(value)
        return _stringify_payload(value)


def _query_text(job: Any) -> str:
    if isinstance(job, dict):
        return str(job.get("query") or job.get("raw") or "")
    return str(job or "")


def _group_docs_by_query(bundle: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for doc in bundle.get("docs", []) or []:
        grouped.setdefault(str(doc.get("query") or ""), []).append(doc)
    return grouped


def _doc_meta_table(formatter: OutputFormatter, doc: Dict[str, Any]) -> str:
    rows = [
        {"Field": "Source", "Value": f"`{doc.get('source_key')}` / {doc.get('source_name') or ''}"},
        {"Field": "Kind", "Value": f"`{doc.get('hit_kind') or 'doc'}`"},
        {"Field": "Score", "Value": f"{float(doc.get('score', 0)):.2f}"},
        {"Field": "URL", "Value": doc.get("url") or ""},
    ]
    if doc.get("saved_text_path"):
        rows.append({"Field": "Saved full text", "Value": f"`{doc.get('saved_text_path')}`"})
    return formatter.table(rows, ["Field", "Value"])


def _doc_headings_line(doc: Dict[str, Any], limit: int = 8) -> str:
    headings = []
    for raw in doc.get("headings", []) or []:
        h = _safe_heading(raw)
        if not _looks_like_noise_heading(h) and h not in headings:
            headings.append(h)
        if len(headings) >= limit:
            break
    return "; ".join(headings)


def _doc_excerpt(formatter: OutputFormatter, doc: Dict[str, Any], chars: int) -> str:
    text = formatter.clean(doc.get("text") or "")
    if not text:
        return ""
    text = formatter.clip(text, chars)
    return formatter.code_fence(text, "text")


def _format_doc_card(formatter: OutputFormatter, doc: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    title = _safe_heading(doc.get("title"), "Untitled documentation page")
    if _looks_like_noise_heading(title):
        title = _safe_heading(doc.get("url") or doc.get("source_name") or "Untitled documentation page")
    include_headings = _bool_param(params, "include_headings", True)
    include_text = _bool_param(params, "include_text", False)
    include_excerpt = _bool_param(params, "include_page_excerpt", True)
    include_full = _bool_param(params, "include_full_text", False)
    excerpt_chars = _int_param(params, "excerpt_chars", 1200, 120, 100_000)

    if formatter.style == "compact":
        excerpt_chars = _int_param(params, "compact_text_chars", 1800, 120, 100_000)

    lines: List[str] = [f"**{title}**", ""]
    lines.append(_doc_meta_table(formatter, doc))
    lines.append("")

    headings = _doc_headings_line(doc)
    if include_headings and headings:
        lines.append(f"- **Page headings:** {headings}")
    if doc.get("saved_text_path"):
        lines.append(f"- **Full text file:** `{doc.get('saved_text_path')}`")
    lines.append("")

    if include_full or include_text:
        lines.append("<details>")
        lines.append("<summary>Fetched text excerpt</summary>")
        lines.append("")
        lines.append(_doc_excerpt(formatter, doc, 10_000_000 if include_full else excerpt_chars))
        lines.append("")
        lines.append("</details>")
        lines.append("")
    elif include_excerpt:
        excerpt = _doc_excerpt(formatter, doc, excerpt_chars)
        if excerpt:
            lines.append("<details>")
            lines.append("<summary>Short excerpt</summary>")
            lines.append("")
            lines.append(excerpt)
            lines.append("")
            lines.append("</details>")
            lines.append("")

    return lines


def _format_query_list(formatter: OutputFormatter, bundle: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    lines: List[str] = [formatter.heading("Query List", 2), ""]
    max_items = _int_param(params, "max_query_list", 500, 0, 100_000)
    queries = list(bundle.get("queries", []) or [])
    shown = queries if max_items <= 0 else queries[:max_items]
    for job in shown:
        flags: List[str] = []
        if isinstance(job, dict):
            if job.get("profile"):
                flags.append(f"profile=`{job.get('profile')}`")
            if job.get("direct_url"):
                flags.append("direct-url")
        suffix = f" — {', '.join(flags)}" if flags else ""
        lines.append(f"- `{_query_text(job)}`{suffix}")
    if max_items and len(queries) > max_items:
        lines.append(f"- ... {len(queries) - max_items} more queries omitted")
    lines.append("")
    return lines


def _format_candidates(formatter: OutputFormatter, bundle: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    lines: List[str] = [formatter.heading("Direct URL Candidates", 2), ""]
    hits = list(bundle.get("hits", []) or [])
    max_candidates = _int_param(params, "max_candidates", 120, 0, 100_000)
    shown = hits if max_candidates <= 0 else hits[:max_candidates]
    if not shown:
        lines.append("_No direct candidates were produced._")
    else:
        for hit in shown:
            src = hit.get("source_key") or "source"
            kind = hit.get("kind") or "direct"
            url = hit.get("url") or ""
            title = _safe_heading(hit.get("title") or url, "candidate")
            lines.append(f"- `{src}` `{kind}` — {title}: {url}")
        if max_candidates and len(hits) > max_candidates:
            lines.append(f"- ... {len(hits) - max_candidates} more candidates omitted")
    lines.append("")
    return lines


def _format_results(formatter: OutputFormatter, bundle: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    lines: List[str] = [formatter.heading("Results", 2), ""]
    grouped = _group_docs_by_query(bundle)
    if not grouped:
        return lines + ["_No documentation pages found._", ""]

    max_docs_per_query = _int_param(params, "max_docs_per_query", 8, 1, 1000)
    max_results_total = _int_param(params, "max_results_total", 300, 1, 100_000)
    rendered = 0
    for query, docs in grouped.items():
        if rendered >= max_results_total:
            break
        lines += [formatter.heading(f"Query: `{query}`", 3), ""]
        ranked = sorted(docs, key=lambda d: d.get("score", 0), reverse=True)[:max_docs_per_query]
        for doc in ranked:
            if rendered >= max_results_total:
                break
            lines.extend(_format_doc_card(formatter, doc, params))
            rendered += 1
        if len(docs) > len(ranked):
            lines.append(f"_Omitted {len(docs) - len(ranked)} lower-ranked page(s) for this query._")
            lines.append("")
    if rendered < sum(len(v) for v in grouped.values()):
        remaining = sum(len(v) for v in grouped.values()) - rendered
        lines.append(f"_Omitted {remaining} result(s) because max_results_total={max_results_total}._")
        lines.append("")
    return lines


def _format_asset_index(formatter: OutputFormatter, bundle: Dict[str, Any]) -> List[str]:
    lines: List[str] = [formatter.heading("Asset Index", 2), ""]
    rows: List[Dict[str, Any]] = []
    for i, doc in enumerate(bundle.get("docs", []) or [], 1):
        rows.append({
            "#": i,
            "Query": doc.get("query") or "",
            "Title": _safe_heading(doc.get("title"), "Untitled"),
            "Source": doc.get("source_key") or "",
            "Saved text": doc.get("saved_text_path") or "",
            "URL": doc.get("url") or "",
        })
    if rows:
        lines.append(formatter.table(rows, ["#", "Query", "Title", "Source", "Saved text", "URL"]))
    else:
        lines.append("_No saved page assets were found._")
    lines.append("")
    return lines


def _format_errors(formatter: OutputFormatter, bundle: Dict[str, Any]) -> List[str]:
    errors = list(bundle.get("errors", []) or [])
    if not errors:
        return []
    lines: List[str] = [formatter.heading("Errors", 2), ""]
    for error in errors[:100]:
        if isinstance(error, dict):
            lines.append(f"- `{error.get('stage', 'unknown')}` `{error.get('query', '')}`: {error.get('error')}")
        else:
            lines.append(f"- {error}")
    if len(errors) > 100:
        lines.append(f"- ... {len(errors) - 100} more errors omitted")
    lines.append("")
    return lines


def _summary_rows(bundle: Dict[str, Any], formatter: OutputFormatter, search_fallback: bool) -> List[Dict[str, Any]]:
    mode = str(bundle.get("mode") or "direct-first")
    resolved_label = "Search/direct URLs" if mode.startswith("search") else "Direct URLs"
    return [
        {"Field": "Mode", "Value": f"`{mode}`"},
        {"Field": "Search fallback", "Value": f"`{search_fallback}`"},
        {"Field": "Queries", "Value": len(bundle.get("queries", []) or [])},
        {"Field": resolved_label, "Value": len(bundle.get("hits", []) or [])},
        {"Field": "Documentation pages", "Value": len(bundle.get("docs", []) or [])},
        {"Field": "Errors", "Value": len(bundle.get("errors", []) or [])},
        {"Field": "Output style", "Value": f"`{formatter.style}`"},
    ]


def _render_structural_toc(formatter: OutputFormatter, title: str, params: Dict[str, Any]) -> str:
    structural = [formatter.heading(title, 1)]
    for name in STRUCTURAL_TOC_TITLES:
        structural.append(formatter.heading(name, 2))
    return formatter.heading("Table of Contents", 2) + "\n\n" + formatter.toc(
        "\n".join(structural),
        max_depth=2,
        structural_only=False,
        max_items=_int_param(params, "max_toc_items", 80, 1, 1000),
    )


def format_apidoc_bundle(bundle: Dict[str, Any], params: Optional[Dict[str, Any]] = None, *, search_fallback: bool = False) -> str:
    """Render an APIDoc bundle into readable Markdown.

    Important readability choices:
      - Fetched documentation body text is never scanned for TOC headings.
      - Full fetched text is saved as assets by APIDocEngine.write_outputs().
      - Main Markdown defaults to cards + short excerpts, not huge pasted docs.
      - Old params are accepted; include_full_text=True restores full inline text.
    """

    params = params or {}
    formatter = OutputFormatter(params)
    title = _safe_heading(_param(params, "title", default="API Documentation Direct Request Results"), "API Documentation Direct Request Results")

    if formatter.style == "raw":
        return json.dumps(bundle, indent=2, ensure_ascii=False, default=str) + "\n"

    if formatter.style == "outline":
        lines = [formatter.heading(title, 1)]
        for section in ("Summary", "Query List", "Direct URL Candidates", "Results", "Asset Index", "Errors"):
            lines.append(formatter.heading(section, 2))
        return formatter.outline("\n".join(lines)) + "\n"

    if formatter.style == "plain":
        plain = [title, "", "Summary"]
        for row in _summary_rows(bundle, formatter, search_fallback):
            plain.append(f"- {row['Field']}: {row['Value']}")
        plain.append("")
        plain.append("Queries")
        for job in bundle.get("queries", []) or []:
            plain.append(f"- {_query_text(job)}")
        return formatter.reflow("\n".join(plain)) + "\n"

    lines: List[str] = []
    if _bool_param(params, "front_matter", False):
        lines.append(formatter.front_matter({
            "title": title,
            "mode": str(bundle.get("mode") or "direct-first"),
            "queries": len(bundle.get("queries", []) or []),
            "hits": len(bundle.get("hits", []) or []),
            "docs": len(bundle.get("docs", []) or []),
            "errors": len(bundle.get("errors", []) or []),
            "output_style": formatter.style,
        }).rstrip())
        lines.append("")

    lines += [formatter.heading(title, 1), "", f"Generated by PromptChat `apidoc` in `{str(bundle.get('mode') or 'direct-first')}` mode.", ""]

    include_toc = _bool_param(params, "include_toc", True)
    if include_toc and formatter.style not in {"compact", "chat", "query_pack", "asset_index"}:
        lines.append("[[STRUCTURAL_TOC]]")
        lines.append("")

    if _bool_param(params, "include_summary", True):
        lines += [formatter.heading("Summary", 2), ""]
        rows = _summary_rows(bundle, formatter, search_fallback)
        if formatter.style in {"compact", "chat"}:
            lines.extend([f"- **{row['Field']}:** {row['Value']}" for row in rows])
        else:
            lines.append(formatter.table(rows, ["Field", "Value"]))
        lines.append("")

    if formatter.style == "chat":
        grouped = _group_docs_by_query(bundle)
        lines += [formatter.heading("Top Results", 2), ""]
        count = 0
        for query, docs in grouped.items():
            if count >= 12:
                break
            top = sorted(docs, key=lambda d: d.get("score", 0), reverse=True)[:2]
            for doc in top:
                lines.append(f"- **{_safe_heading(doc.get('title'))}** — `{doc.get('source_key')}` — {doc.get('url')}")
                count += 1
        if not grouped:
            lines.append("_No documentation pages found._")
        lines.append("")
    elif formatter.style == "query_pack":
        if _bool_param(params, "include_query_list", True):
            lines.extend(_format_query_list(formatter, bundle, params))
        if _bool_param(params, "include_candidates", True):
            lines.extend(_format_candidates(formatter, bundle, params))
    elif formatter.style == "asset_index":
        lines.extend(_format_asset_index(formatter, bundle))
    else:
        if _bool_param(params, "include_query_list", True):
            lines.extend(_format_query_list(formatter, bundle, params))
        if _bool_param(params, "include_candidates", True) and formatter.style != "compact":
            lines.extend(_format_candidates(formatter, bundle, params))
        if _bool_param(params, "include_results", True):
            lines.extend(_format_results(formatter, bundle, params))
        if _bool_param(params, "include_asset_index", False):
            lines.extend(_format_asset_index(formatter, bundle))

    if _bool_param(params, "include_errors", True):
        lines.extend(_format_errors(formatter, bundle))

    markdown = "\n".join(lines).rstrip() + "\n"
    if "[[STRUCTURAL_TOC]]" in markdown:
        toc_scope = str(_param(params, "toc_scope", default="structural_only") or "structural_only").strip().lower()
        if toc_scope == "all_headings":
            body = markdown.replace("[[STRUCTURAL_TOC]]", "")
            toc = formatter.heading("Table of Contents", 2) + "\n\n" + formatter.toc(
                body,
                max_depth=3,
                structural_only=False,
                max_items=_int_param(params, "max_toc_items", 80, 1, 1000),
            )
        else:
            toc = _render_structural_toc(formatter, title, params)
        markdown = markdown.replace("[[STRUCTURAL_TOC]]", toc)
    return markdown


def _write_optional_text_output(text: str, params: Dict[str, Any], default_name: str) -> Dict[str, Any]:
    out_path = str(params.get("out_path") or params.get("out") or "").strip()
    if not out_path:
        return {"wrote": False}
    p = Path(out_path)
    target = p if p.suffix else p / default_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return {"wrote": True, "path": str(target)}


COMMON_PARAMS = {
    "query_file": "Path to .txt file with one query per line.",
    "profile": "all | python | python-packages | csharp | dotnet | cpp | windows | web | javascript | node | rust | go | java | kotlin | swift | game | bannerlord | database | devops | cloud | ai | linux | monero",
    "sources": "Optional comma-separated source keys.",
    "config_path": "Optional JSON source registry path.",
    "out_path": "Markdown output path, e.g. out/apidocs.md",
    "direct_mode": True,
    "search_fallback": False,
    "search_mode": "direct_first | search_first | search_only",
    "direct_fallback_when_search_fails": False,
    "crawl_direct_pages": False,
    "use_cache": True,
    "max_direct_urls_per_query": 32,
    "max_pages_per_query": 8,
    "max_links_per_page": 160,
    "max_search_hits": 20,
    "max_chars_per_page": 24000,
    "max_queries": 100000,
    "timeout": 20,
    "delay": 0.10,
    "output_style": "advanced_report",
    "include_text": False,
    "include_page_excerpt": True,
    "include_full_text": False,
    "excerpt_chars": 1200,
    "include_toc": True,
    "toc_scope": "structural_only",
    "max_toc_items": 80,
    "include_summary": True,
    "include_query_list": True,
    "include_candidates": True,
    "include_results": True,
    "include_errors": True,
    "max_query_list": 500,
    "max_candidates": 120,
    "max_docs_per_query": 8,
    "max_results_total": 300,
    "max_text_chars": 0,
    "compact_text_chars": 1800,
    "front_matter": False,
}


def _apidoc_default_output_params(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge caller params with the advanced-report defaults used by the rewritten block.

    The discovery/fetch engine still accepts the old parameters.  This helper only
    controls rendering defaults so an empty params dict produces the richer output
    style requested by the current block design.
    """
    merged: Dict[str, Any] = dict(params or {})
    merged.setdefault("output_style", "advanced_report")
    merged.setdefault("include_toc", True)
    merged.setdefault("toc_scope", "structural_only")
    merged.setdefault("include_summary", True)
    merged.setdefault("include_intelligence", True)
    merged.setdefault("include_profile_coverage", True)
    merged.setdefault("include_source_health", True)
    merged.setdefault("include_host_coverage", True)
    merged.setdefault("include_gaps", True)
    merged.setdefault("include_results", True)
    merged.setdefault("include_asset_index", True)
    merged.setdefault("include_errors", True)
    merged.setdefault("include_page_excerpt", False)
    merged.setdefault("include_full_text", False)
    merged.setdefault("max_profile_rows", 40)
    merged.setdefault("max_topic_rows", 80)
    merged.setdefault("max_source_rows", 80)
    merged.setdefault("max_host_rows", 80)
    merged.setdefault("max_missing_rows", 80)
    merged.setdefault("max_docs_per_query", 3)
    merged.setdefault("max_results_total", 80)
    return merged


@dataclass
class APIDocBlock(BaseBlock):
    """All-in-one API docs request block. Supports direct-first and search-first modes."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        merged = _apidoc_default_output_params(params)
        progress_lines: List[str] = []

        def progress(msg: str) -> None:
            progress_lines.append(msg)
            print(msg, file=sys.stderr)

        engine = APIDocEngine(params=merged, progress=progress)
        markdown, bundle, meta = engine.run_all(payload)
        if as_bool(merged.get("store_bundle"), False):
            key = str(merged.get("bundle_key", "apidoc_last_bundle"))
            mem = Memory.load()
            mem[key] = bundle
            Memory.save(mem)
            meta["stored_bundle_key"] = key
        meta["progress"] = progress_lines[-200:]
        meta["output_style"] = OutputFormatter(merged).style
        return markdown, {"type": "apidoc", **meta}

    def get_params_info(self) -> Dict[str, Any]:
        return dict(
            COMMON_PARAMS,
            output_style="advanced_report",
            include_asset_index=True,
            include_intelligence=True,
            include_profile_coverage=True,
            include_source_health=True,
            include_host_coverage=True,
            include_gaps=True,
            max_profile_rows=40,
            max_topic_rows=80,
            max_source_rows=80,
            max_host_rows=80,
            store_bundle=False,
            bundle_key="apidoc_last_bundle",
        )


BLOCKS.register("apidoc", APIDocBlock)


@dataclass
class APIDocParseBlock(BaseBlock):
    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        engine = APIDocEngine(params=params)
        jobs, meta = engine.read_queries(payload)
        return {"queries": jobs}, {"type": "apidoc-parse", **meta}

    def get_params_info(self) -> Dict[str, Any]:
        return {"query_file": COMMON_PARAMS["query_file"], "profile": COMMON_PARAMS["profile"]}


BLOCKS.register("apidoc_parse", APIDocParseBlock)
BLOCKS.register("apidoc_queries", APIDocParseBlock)


@dataclass
class APIDocDiscoverBlock(BaseBlock):
    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        engine = APIDocEngine(params=params)
        if isinstance(payload, dict) and isinstance(payload.get("queries"), list):
            jobs = payload.get("queries", [])
        else:
            jobs = engine.read_queries(payload)[0]
        bundle = engine.discover(jobs)
        return bundle, {
            "type": "apidoc-direct-discover",
            "query_count": len(jobs),
            "direct_url_count": len(bundle.get("hits", [])),
            "error_count": len(bundle.get("errors", [])),
        }

    def get_params_info(self) -> Dict[str, Any]:
        return dict(COMMON_PARAMS)


BLOCKS.register("apidoc_discover", APIDocDiscoverBlock)


@dataclass
class APIDocFetchBlock(BaseBlock):
    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        engine = APIDocEngine(params=params)
        if isinstance(payload, dict) and "hits" in payload:
            discovered = payload
        else:
            jobs = payload.get("queries", []) if isinstance(payload, dict) else engine.read_queries(payload)[0]
            discovered = engine.discover(jobs)
        ranked = engine.rank_bundle(engine.fetch_extract(discovered))
        return ranked, {
            "type": "apidoc-fetch",
            "doc_count": len(ranked.get("docs", [])),
            "error_count": len(ranked.get("errors", [])),
        }

    def get_params_info(self) -> Dict[str, Any]:
        return dict(COMMON_PARAMS)


BLOCKS.register("apidoc_fetch", APIDocFetchBlock)


@dataclass
class APIDocMarkdownBlock(BaseBlock):
    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        merged = _apidoc_default_output_params(params)
        engine = APIDocEngine(params=merged)
        bundle = payload if isinstance(payload, dict) else {
            "queries": [],
            "hits": [],
            "docs": [],
            "errors": [{"stage": "markdown", "error": "payload_not_bundle"}],
        }
        md = engine.markdown(bundle)
        write_meta = engine.write_outputs(bundle, md)
        return md, {"type": "apidoc-markdown", "doc_count": len(bundle.get("docs", [])), **write_meta}

    def get_params_info(self) -> Dict[str, Any]:
        return dict(COMMON_PARAMS)


BLOCKS.register("apidoc_markdown", APIDocMarkdownBlock)


@dataclass
class APIDocProfilesBlock(BaseBlock):
    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        engine = APIDocEngine(params=params)
        lines = ["# APIDoc Direct Profiles", "", f"Registry version: `{DIRECT_DOC_REGISTRY_VERSION}`", ""]
        for profile, keys in sorted(engine.config.get("profiles", {}).items()):
            lines += [f"## {profile}", ""]
            for key in keys:
                src = engine.config.get("sources", {}).get(key, {})
                lines.append(
                    f"- `{key}` — {src.get('display_name', key)} "
                    f"/ resolver=`{src.get('resolver', '')}` "
                    f"/ seeds=`{len(src.get('seed_urls', []))}` "
                    f"/ topics=`{len(src.get('topic_urls', {}) or {})}`"
                )
            lines.append("")
        text = "\n".join(lines).rstrip() + "\n"
        return text, {
            "type": "apidoc-profiles",
            "profiles": sorted(engine.config.get("profiles", {}).keys()),
            "sources": sorted(engine.config.get("sources", {}).keys()),
        }

    def get_params_info(self) -> Dict[str, Any]:
        return {"config_path": COMMON_PARAMS["config_path"]}


BLOCKS.register("apidoc_profiles", APIDocProfilesBlock)


@dataclass
class APIDocLinksBlock(BaseBlock):
    """Dump every built-in API documentation source, seed URL, and topic URL."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        engine = APIDocEngine(params=params)
        md = engine.catalog_markdown()
        meta = {"type": "apidoc-links", "profiles": len(engine.config.get("profiles", {})), "sources": len(engine.config.get("sources", {}))}
        meta.update(_write_optional_text_output(md, params, "apidoc_link_catalog.md"))
        return md, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"config_path": COMMON_PARAMS["config_path"], "out_path": "Optional .md path to write the full link catalog."}


BLOCKS.register("apidoc_links", APIDocLinksBlock)
BLOCKS.register("apidoc_catalog", APIDocLinksBlock)


@dataclass
class TextFormatBlock(BaseBlock):
    """Clean, normalize, wrap, or lightly style arbitrary text payloads."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        formatter = OutputFormatter(params)
        text = formatter.format_text(payload)
        meta = {"type": "text-format", "style": formatter.style, "chars": len(text)}
        meta.update(_write_optional_text_output(text, params, "formatted.txt"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return dict(FORMAT_COMMON_PARAMS, out_path="Optional file/folder path to write formatted text.")


BLOCKS.register("format_text", TextFormatBlock)
BLOCKS.register("text_format", TextFormatBlock)
BLOCKS.register("format_clean", TextFormatBlock)
BLOCKS.register("format_readable", TextFormatBlock)


@dataclass
class TextNormalizeBlock(BaseBlock):
    """Only normalize whitespace and bullets. Good before sending text to another block."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        formatter = OutputFormatter(params)
        text = formatter.normalize_bullets(payload)
        meta = {"type": "text-normalize", "chars": len(text)}
        meta.update(_write_optional_text_output(text, params, "normalized.txt"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"out_path": "Optional file/folder path.", "format_width": FORMAT_COMMON_PARAMS["format_width"]}


BLOCKS.register("normalize_text", TextNormalizeBlock)
BLOCKS.register("text_normalize", TextNormalizeBlock)


@dataclass
class TextWrapBlock(BaseBlock):
    """Reflow plain paragraphs while preserving headings, bullets, tables, and code fences."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        formatter = OutputFormatter(params)
        text = formatter.reflow(payload)
        meta = {"type": "text-wrap", "width": formatter.width, "chars": len(text)}
        meta.update(_write_optional_text_output(text, params, "wrapped.txt"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"format_width": 100, "out_path": "Optional file/folder path."}


BLOCKS.register("wrap_text", TextWrapBlock)
BLOCKS.register("text_wrap", TextWrapBlock)


@dataclass
class MarkdownCodeFenceBlock(BaseBlock):
    """Wrap text in a Markdown code fence with a configurable language."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        formatter = OutputFormatter(params)
        language = str(params.get("code_language") or params.get("lang") or "text")
        text = formatter.code_fence(payload, language)
        meta = {"type": "markdown-code-fence", "language": language, "chars": len(text)}
        meta.update(_write_optional_text_output(text, params, "code_fence.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"code_language": "text | python | csharp | cpp | json | ...", "max_code_chars": 0, "out_path": "Optional .md path."}


BLOCKS.register("markdown_code", MarkdownCodeFenceBlock)
BLOCKS.register("code_fence", MarkdownCodeFenceBlock)
BLOCKS.register("format_code", MarkdownCodeFenceBlock)


@dataclass
class MarkdownTableBlock(BaseBlock):
    """Convert a dict/list/list-of-dicts payload to a GitHub-flavored Markdown table."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        formatter = OutputFormatter(params)
        raw_cols = params.get("columns") or params.get("cols") or ""
        columns = [c.strip() for c in str(raw_cols).split(",") if c.strip()] if raw_cols else None
        text = formatter.table(payload, columns)
        meta = {"type": "markdown-table", "rows": len(_coerce_rows(payload)), "columns": columns or "auto", "chars": len(text)}
        meta.update(_write_optional_text_output(text, params, "table.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"columns": "Optional comma-separated columns.", "out_path": "Optional .md path."}


BLOCKS.register("markdown_table", MarkdownTableBlock)
BLOCKS.register("format_table", MarkdownTableBlock)
BLOCKS.register("table_format", MarkdownTableBlock)


@dataclass
class MarkdownCalloutBlock(BaseBlock):
    """Render text as a Markdown callout/blockquote note."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        formatter = OutputFormatter(params)
        kind = str(params.get("kind") or "note")
        title = str(params.get("callout_title") or params.get("title") or "")
        text = formatter.callout(payload, kind, title)
        meta = {"type": "markdown-callout", "chars": len(text)}
        meta.update(_write_optional_text_output(text, params, "callout.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"kind": "note | warning | tip | error", "callout_title": "Optional title.", "out_path": "Optional .md path."}


BLOCKS.register("markdown_callout", MarkdownCalloutBlock)
BLOCKS.register("format_callout", MarkdownCalloutBlock)


@dataclass
class MarkdownTocBlock(BaseBlock):
    """Generate a table of contents from real Markdown headings, ignoring code fences/noise."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        formatter = OutputFormatter(params)
        depth = as_int(params.get("max_depth"), 3, 1, 6)
        structural_only = as_bool(params.get("structural_only"), False)
        max_items = as_int(params.get("max_toc_items"), 80, 1, 1000)
        text = formatter.toc(payload, max_depth=depth, structural_only=structural_only, max_items=max_items)
        meta = {"type": "markdown-toc", "max_depth": depth, "items": len([x for x in text.splitlines() if x.strip()])}
        meta.update(_write_optional_text_output(text, params, "toc.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"max_depth": 3, "structural_only": False, "max_toc_items": 80, "out_path": "Optional .md path."}


BLOCKS.register("markdown_toc", MarkdownTocBlock)
BLOCKS.register("format_toc", MarkdownTocBlock)


@dataclass
class MarkdownOutlineBlock(BaseBlock):
    """Extract only the heading outline from Markdown output."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        formatter = OutputFormatter(params)
        text = formatter.outline(payload)
        meta = {"type": "markdown-outline", "items": len([x for x in text.splitlines() if x.strip()])}
        meta.update(_write_optional_text_output(text, params, "outline.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"out_path": "Optional .md path."}


BLOCKS.register("markdown_outline", MarkdownOutlineBlock)
BLOCKS.register("format_outline", MarkdownOutlineBlock)
BLOCKS.register("outline_text", MarkdownOutlineBlock)


@dataclass
class MarkdownTemplateBlock(BaseBlock):
    """Apply a simple .format() template to payload text or dict fields."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        formatter = OutputFormatter(params)
        template = str(params.get("template") or "{payload}")
        text = formatter.apply_template(payload, template)
        meta = {"type": "markdown-template", "chars": len(text)}
        meta.update(_write_optional_text_output(text, params, "templated.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"template": "Use {payload}/{text} or dict keys, e.g. '# {title}\\n\\n{body}'", "out_path": "Optional file path."}


BLOCKS.register("markdown_template", MarkdownTemplateBlock)
BLOCKS.register("format_template", MarkdownTemplateBlock)


@dataclass
class FormatPipelineBlock(BaseBlock):
    """Run formatter steps in order: clean, bullets, wrap, code, table, toc, outline."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        formatter = OutputFormatter(params)
        raw = params.get("steps") or params.get("pipeline") or "clean,bullets,wrap"
        steps = [s.strip() for s in str(raw).split(",") if s.strip()]
        text = formatter.pipeline(payload, steps)
        meta = {"type": "format-pipeline", "steps": steps, "chars": len(text)}
        meta.update(_write_optional_text_output(text, params, "formatted_pipeline.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"steps": "clean,bullets,wrap,code,table,toc,outline", **FORMAT_COMMON_PARAMS, "out_path": "Optional output path."}


BLOCKS.register("format_pipeline", FormatPipelineBlock)
BLOCKS.register("text_pipeline", FormatPipelineBlock)


@dataclass
class APIDocFormatBlock(BaseBlock):
    """Format an APIDoc bundle using the readable renderer used by APIDocEngine.markdown()."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        if isinstance(payload, dict) and ("docs" in payload or "hits" in payload or "queries" in payload):
            bundle = payload
        else:
            raw_text = _stringify_payload(payload)
            bundle = {"queries": [], "hits": [], "docs": [], "errors": [], "raw_text": raw_text}
            if raw_text:
                bundle["docs"].append({
                    "query": "raw text",
                    "title": str(params.get("title") or "Formatted Text"),
                    "source_key": "raw",
                    "source_name": "Raw payload",
                    "hit_kind": "raw",
                    "score": 0.0,
                    "url": "",
                    "headings": [],
                    "text": raw_text,
                })
        merged = _apidoc_default_output_params(params)
        text = format_apidoc_bundle(bundle, merged, search_fallback=as_bool(merged.get("search_fallback"), False))
        meta = {
            "type": "apidoc-format",
            "style": OutputFormatter(merged).style,
            "doc_count": len(bundle.get("docs", [])),
            "chars": len(text),
        }
        meta.update(_write_optional_text_output(text, params, "apidoc_formatted.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return dict(FORMAT_COMMON_PARAMS, out_path="Optional .md path to write formatted APIDoc output.")


BLOCKS.register("apidoc_format", APIDocFormatBlock)
BLOCKS.register("format_apidoc", APIDocFormatBlock)
BLOCKS.register("apidoc_readable", APIDocFormatBlock)


@dataclass
class FormatterProfilesBlock(BaseBlock):
    """Show built-in formatting block names and parameters."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        lines = ["# Formatter Blocks", "", "## Styles", ""]
        for style in sorted(set(FORMAT_STYLE_ALIASES.values())):
            lines.append(f"- `{style}`")
        lines += [
            "",
            "## Blocks",
            "",
            "- `format_text` / `text_format` — clean and reflow raw text",
            "- `normalize_text` — normalize whitespace and bullets",
            "- `wrap_text` — paragraph wrapping with Markdown/code preservation",
            "- `markdown_code` / `code_fence` — fenced code block",
            "- `markdown_table` — dict/list to Markdown table",
            "- `markdown_callout` — quote/callout note",
            "- `markdown_toc` — TOC from real headings, ignoring code/noise",
            "- `markdown_outline` — heading-only outline",
            "- `markdown_template` — template payload into Markdown",
            "- `format_pipeline` — run multiple formatter steps",
            "- `apidoc_format` / `apidoc_readable` — readable APIDoc bundle renderer",
            "",
            "## Rewritten APIDoc defaults",
            "",
            "- `output_style=advanced_report`",
            "- `toc_scope=structural_only`",
            "- `include_intelligence=true`",
            "- `include_profile_coverage=true`",
            "- `include_source_health=true`",
            "- `include_gaps=true`",
            "- `include_asset_index=true`",
            "- `include_page_excerpt=false`",
            "- `include_full_text=false`",
        ]
        text = "\n".join(lines).rstrip() + "\n"
        meta = {"type": "formatter-profiles", "styles": sorted(set(FORMAT_STYLE_ALIASES.values()))}
        meta.update(_write_optional_text_output(text, params, "formatter_blocks.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"out_path": "Optional .md path."}


BLOCKS.register("formatter_profiles", FormatterProfilesBlock)
BLOCKS.register("formatters", FormatterProfilesBlock)

# ===========================================================================
# Advanced APIDoc intelligence layer
# ===========================================================================
# This layer is intentionally additive:
#   - Existing public block names still work.
#   - Existing APIDocEngine method signatures are not changed.
#   - Existing formatter parameters are still accepted.
#   - New behavior is activated by optional params and by richer default rendering.
#
# It is built for large clean_report outputs like:
#   500+ queries, 500+ direct URLs, 1000+ docs, and zero errors.
# Instead of dumping every query/result into the main Markdown, it adds coverage,
# health, clustering, batch planning, query-pack generation, and saved analysis
# artifacts.

ADVANCED_APIDOC_VERSION = "2026.06.03-output-first-advanced-intel"


def _advanced_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _advanced_query_key(job: Any) -> str:
    if isinstance(job, dict):
        return str(job.get("query") or job.get("raw") or job.get("direct_url") or "").strip()
    return str(job or "").strip()


def _advanced_profile(job: Any) -> str:
    if isinstance(job, dict):
        return str(job.get("profile") or "all").strip() or "all"
    return "all"


def _advanced_host(value: Any) -> str:
    return hostname(str(value or "")) or ""


def _advanced_canonical_url(value: Any) -> str:
    u = clean_url(str(value or ""))
    if not u:
        return ""
    try:
        p = urlparse(u)
        path = p.path or "/"
        # Keep query because API docs often use ?view=... as meaningful, but drop fragments.
        return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", p.query, ""))
    except Exception:
        return u


def _advanced_first_words(text: str, limit: int = 5) -> str:
    toks = [t.lower() for t in re.findall(r"[A-Za-z][A-Za-z0-9_.+-]{1,}", text or "")]
    bad = {"python", "api", "docs", "documentation", "direct", "url", "https", "http"}
    toks = [t for t in toks if t not in bad]
    return " ".join(toks[:limit]) or "general"


def _advanced_topic(job: Any) -> str:
    q = _advanced_query_key(job)
    if ":" in q:
        left = q.split(":", 1)[0].strip().lower()
        if 1 <= len(left) <= 32 and not left.startswith("http"):
            return left
    # Common query-pack convention: "security: ...", "forensics: ...".
    if isinstance(job, dict):
        raw = str(job.get("raw") or "")
        if ":" in raw:
            left = raw.split(":", 1)[0].strip().lower()
            if 1 <= len(left) <= 32 and not left.startswith("http"):
                return left
    return _advanced_first_words(q, 3)


def _advanced_score_band(score_value: Any) -> str:
    try:
        s = float(score_value or 0)
    except Exception:
        s = 0.0
    if s >= 90:
        return "excellent"
    if s >= 55:
        return "strong"
    if s >= 25:
        return "usable"
    if s > 0:
        return "weak"
    return "unknown"


def _advanced_percent(n: int, d: int) -> str:
    if not d:
        return "0.0%"
    return f"{(float(n) / float(d)) * 100.0:.1f}%"


def _advanced_counter_rows(counter: Dict[str, int], key_name: str, value_name: str = "Count", limit: int = 50) -> List[Dict[str, Any]]:
    rows = []
    for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]:
        rows.append({key_name: k or "(blank)", value_name: v})
    return rows


def _advanced_bundle_from_markdown(text_value: Any) -> Dict[str, Any]:
    """Best-effort reader for clean_report Markdown when a raw bundle is not available."""
    s = _stringify_payload(text_value)
    queries: List[Dict[str, Any]] = []
    hits: List[Dict[str, Any]] = []
    docs: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    # Query list lines generated by this renderer:
    # - `query` — profile=`python`
    query_re = re.compile(r"(?m)^-\s+`(?P<q>.*?)`\s+—\s+profile=`(?P<p>.*?)`(?:,\s+direct-url)?\s*$")
    for i, m in enumerate(query_re.finditer(s), 1):
        q = m.group("q").strip()
        queries.append({"line": i, "query": q, "profile": m.group("p").strip(), "raw": q})

    # Candidate lines generated by older output:
    cand_re = re.compile(r"(?m)^-\s+`(?P<src>[^`]+)`\s+`(?P<kind>[^`]+)`\s+—\s+(?P<title>.*?):\s+(?P<url>https?://\S+)\s*$")
    for m in cand_re.finditer(s):
        url = m.group("url").strip()
        hits.append({
            "query": "",
            "source_key": m.group("src").strip(),
            "kind": m.group("kind").strip(),
            "title": m.group("title").strip(),
            "url": url,
            "score": 0,
        })

    # Result cards in readable output use a URL table row.
    url_re = re.compile(r"(?m)^\|\s*URL\s*\|\s*(https?://[^|]+?)\s*\|")
    for m in url_re.finditer(s):
        url = m.group(1).strip()
        docs.append({"query": "", "source_key": "", "source_name": "", "url": url, "title": url, "score": 0, "text": "", "headings": []})

    if re.search(r"(?im)^##\s+Errors\b", s):
        for m in re.finditer(r"(?m)^-\s+\*\*(?P<stage>[^*]+)\*\*:\s+(?P<err>.+)$", s):
            errors.append({"stage": m.group("stage").strip(), "error": m.group("err").strip()})

    return {"queries": queries, "hits": hits, "docs": docs, "errors": errors, "mode": "markdown-import"}


def _advanced_ensure_bundle(payload: Any) -> Dict[str, Any]:
    if isinstance(payload, dict) and any(k in payload for k in ("queries", "hits", "docs", "errors")):
        return payload
    if isinstance(payload, (str, Path)):
        p = Path(str(payload))
        if p.exists() and p.is_file():
            try:
                return _advanced_bundle_from_markdown(p.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                pass
    return _advanced_bundle_from_markdown(payload)


def _advanced_bundle_analysis(bundle: Dict[str, Any], *, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = params or {}
    queries = _advanced_list(bundle.get("queries"))
    hits = _advanced_list(bundle.get("hits"))
    docs = _advanced_list(bundle.get("docs"))
    errors = _advanced_list(bundle.get("errors"))

    q_texts = [_advanced_query_key(q) for q in queries]
    q_set = set(q_texts)
    hits_by_q: Dict[str, List[Dict[str, Any]]] = {}
    docs_by_q: Dict[str, List[Dict[str, Any]]] = {}
    for hit in hits:
        if isinstance(hit, dict):
            hits_by_q.setdefault(str(hit.get("query") or ""), []).append(hit)
    for doc in docs:
        if isinstance(doc, dict):
            docs_by_q.setdefault(str(doc.get("query") or ""), []).append(doc)

    profiles: Dict[str, Dict[str, Any]] = {}
    topics: Dict[str, Dict[str, Any]] = {}
    for job in queries:
        q = _advanced_query_key(job)
        profile = _advanced_profile(job)
        topic = _advanced_topic(job)
        pr = profiles.setdefault(profile, {"profile": profile, "queries": 0, "direct_urls": 0, "docs": 0, "missing_docs": 0, "topics": {}})
        pr["queries"] += 1
        pr["direct_urls"] += len(hits_by_q.get(q, []))
        pr["docs"] += len(docs_by_q.get(q, []))
        if not docs_by_q.get(q):
            pr["missing_docs"] += 1
        pr["topics"][topic] = int(pr["topics"].get(topic, 0)) + 1

        tr = topics.setdefault(topic, {"topic": topic, "queries": 0, "direct_urls": 0, "docs": 0, "missing_docs": 0, "profiles": {}})
        tr["queries"] += 1
        tr["direct_urls"] += len(hits_by_q.get(q, []))
        tr["docs"] += len(docs_by_q.get(q, []))
        if not docs_by_q.get(q):
            tr["missing_docs"] += 1
        tr["profiles"][profile] = int(tr["profiles"].get(profile, 0)) + 1

    source_stats: Dict[str, Dict[str, Any]] = {}
    host_stats: Dict[str, Dict[str, Any]] = {}
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        src = str(hit.get("source_key") or "direct_url")
        st = source_stats.setdefault(src, {"source": src, "hits": 0, "docs": 0, "avg_score": 0.0, "scores": [], "hosts": {}, "kinds": {}})
        st["hits"] += 1
        try:
            st["scores"].append(float(hit.get("score", 0) or 0))
        except Exception:
            pass
        h = _advanced_host(hit.get("url"))
        if h:
            st["hosts"][h] = int(st["hosts"].get(h, 0)) + 1
            hs = host_stats.setdefault(h, {"host": h, "hits": 0, "docs": 0, "sources": {}, "avg_score": 0.0, "scores": []})
            hs["hits"] += 1
            hs["sources"][src] = int(hs["sources"].get(src, 0)) + 1
            try:
                hs["scores"].append(float(hit.get("score", 0) or 0))
            except Exception:
                pass
        kind = str(hit.get("kind") or "unknown")
        st["kinds"][kind] = int(st["kinds"].get(kind, 0)) + 1

    for doc in docs:
        if not isinstance(doc, dict):
            continue
        src = str(doc.get("source_key") or "direct_url")
        st = source_stats.setdefault(src, {"source": src, "hits": 0, "docs": 0, "avg_score": 0.0, "scores": [], "hosts": {}, "kinds": {}})
        st["docs"] += 1
        try:
            st["scores"].append(float(doc.get("score", 0) or 0))
        except Exception:
            pass
        h = _advanced_host(doc.get("url"))
        if h:
            st["hosts"][h] = int(st["hosts"].get(h, 0)) + 1
            hs = host_stats.setdefault(h, {"host": h, "hits": 0, "docs": 0, "sources": {}, "avg_score": 0.0, "scores": []})
            hs["docs"] += 1
            hs["sources"][src] = int(hs["sources"].get(src, 0)) + 1
            try:
                hs["scores"].append(float(doc.get("score", 0) or 0))
            except Exception:
                pass

    for st in source_stats.values():
        scores = st.pop("scores", [])
        st["avg_score"] = round(sum(scores) / len(scores), 2) if scores else 0.0
        st["top_hosts"] = ", ".join(k for k, _v in sorted(st.get("hosts", {}).items(), key=lambda kv: (-kv[1], kv[0]))[:5])
        st["top_kinds"] = ", ".join(k for k, _v in sorted(st.get("kinds", {}).items(), key=lambda kv: (-kv[1], kv[0]))[:5])
        st["doc_rate"] = _advanced_percent(int(st.get("docs", 0)), max(1, int(st.get("hits", 0))))

    for hs in host_stats.values():
        scores = hs.pop("scores", [])
        hs["avg_score"] = round(sum(scores) / len(scores), 2) if scores else 0.0
        hs["top_sources"] = ", ".join(k for k, _v in sorted(hs.get("sources", {}).items(), key=lambda kv: (-kv[1], kv[0]))[:5])

    duplicates: Dict[str, int] = {}
    for item in hits + docs:
        if isinstance(item, dict):
            u = _advanced_canonical_url(item.get("url"))
            if u:
                duplicates[u] = duplicates.get(u, 0) + 1
    duplicate_urls = [{"url": u, "count": c} for u, c in sorted(duplicates.items(), key=lambda kv: (-kv[1], kv[0])) if c > 1]

    missing_docs = []
    no_direct_urls = []
    for job in queries:
        q = _advanced_query_key(job)
        if q and not docs_by_q.get(q):
            missing_docs.append({"query": q, "profile": _advanced_profile(job), "topic": _advanced_topic(job), "direct_urls": len(hits_by_q.get(q, []))})
        if q and not hits_by_q.get(q):
            no_direct_urls.append({"query": q, "profile": _advanced_profile(job), "topic": _advanced_topic(job)})

    profile_rows = []
    for pr in profiles.values():
        topics_sorted = sorted(pr.get("topics", {}).items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        profile_rows.append({
            "Profile": pr["profile"],
            "Queries": pr["queries"],
            "Direct URLs": pr["direct_urls"],
            "Docs": pr["docs"],
            "Missing docs": pr["missing_docs"],
            "Coverage": _advanced_percent(int(pr["queries"]) - int(pr["missing_docs"]), max(1, int(pr["queries"]))),
            "Top topics": ", ".join(k for k, _ in topics_sorted),
        })
    profile_rows.sort(key=lambda r: (-int(r["Queries"]), str(r["Profile"])))

    topic_rows = []
    for tr in topics.values():
        profiles_sorted = sorted(tr.get("profiles", {}).items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        topic_rows.append({
            "Topic": tr["topic"],
            "Queries": tr["queries"],
            "Direct URLs": tr["direct_urls"],
            "Docs": tr["docs"],
            "Missing docs": tr["missing_docs"],
            "Coverage": _advanced_percent(int(tr["queries"]) - int(tr["missing_docs"]), max(1, int(tr["queries"]))),
            "Profiles": ", ".join(k for k, _ in profiles_sorted),
        })
    topic_rows.sort(key=lambda r: (-int(r["Queries"]), str(r["Topic"])))

    source_rows = sorted(source_stats.values(), key=lambda r: (-int(r.get("docs", 0)), -int(r.get("hits", 0)), str(r.get("source"))))
    source_rows = [{
        "Source": r.get("source"),
        "Hits": r.get("hits"),
        "Docs": r.get("docs"),
        "Doc rate": r.get("doc_rate"),
        "Avg score": r.get("avg_score"),
        "Top hosts": r.get("top_hosts", ""),
        "Kinds": r.get("top_kinds", ""),
    } for r in source_rows]

    host_rows = sorted(host_stats.values(), key=lambda r: (-int(r.get("docs", 0)), -int(r.get("hits", 0)), str(r.get("host"))))
    host_rows = [{
        "Host": r.get("host"),
        "Hits": r.get("hits"),
        "Docs": r.get("docs"),
        "Avg score": r.get("avg_score"),
        "Sources": r.get("top_sources", ""),
    } for r in host_rows]

    error_rows = []
    for err in errors:
        if isinstance(err, dict):
            error_rows.append({
                "Stage": err.get("stage", ""),
                "Source": err.get("source", "") or err.get("source_key", ""),
                "Query": err.get("query", ""),
                "Error": err.get("error", "") or repr(err),
            })
        else:
            error_rows.append({"Stage": "", "Source": "", "Query": "", "Error": str(err)})

    total_queries = len(queries)
    covered_queries = len([q for q in q_texts if docs_by_q.get(q)])
    total_docs = len(docs)
    total_hits = len(hits)
    total_errors = len(errors)

    return {
        "version": ADVANCED_APIDOC_VERSION,
        "counts": {
            "queries": total_queries,
            "direct_urls": total_hits,
            "docs": total_docs,
            "errors": total_errors,
            "covered_queries": covered_queries,
            "missing_docs": max(0, total_queries - covered_queries),
            "coverage": _advanced_percent(covered_queries, max(1, total_queries)),
            "unique_doc_hosts": len({r["Host"] for r in host_rows if r.get("Docs", 0)}),
            "unique_sources": len(source_rows),
            "duplicate_urls": len(duplicate_urls),
        },
        "profiles": profile_rows,
        "topics": topic_rows,
        "sources": source_rows,
        "hosts": host_rows,
        "missing_docs": missing_docs,
        "no_direct_urls": no_direct_urls,
        "duplicate_urls": duplicate_urls,
        "errors": error_rows,
    }


def _advanced_table(formatter: OutputFormatter, rows: List[Dict[str, Any]], columns: Optional[List[str]] = None, limit: int = 25, empty: str = "_None._") -> str:
    if not rows:
        return empty
    return formatter.table(rows[:max(1, int(limit))], columns)


def _advanced_details(title: str, body: str, *, open_: bool = False) -> str:
    attr = " open" if open_ else ""
    return f"<details{attr}>\n<summary>{_safe_heading(title)}</summary>\n\n{body.strip()}\n\n</details>"


def _advanced_format_query_groups(formatter: OutputFormatter, bundle: Dict[str, Any], analysis: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    max_profiles = _int_param(params, "max_profile_rows", 40, 1, 500)
    max_topics = _int_param(params, "max_topic_rows", 80, 1, 1000)
    max_query_list = _int_param(params, "max_query_list", 120, 1, 10000)
    lines = [formatter.heading("Profile Coverage", 2), ""]
    lines.append(_advanced_table(formatter, analysis.get("profiles", []), ["Profile", "Queries", "Direct URLs", "Docs", "Missing docs", "Coverage", "Top topics"], max_profiles))
    lines.append("")
    lines += [formatter.heading("Topic Matrix", 2), ""]
    lines.append(_advanced_table(formatter, analysis.get("topics", []), ["Topic", "Queries", "Direct URLs", "Docs", "Missing docs", "Coverage", "Profiles"], max_topics))
    lines.append("")

    if _bool_param(params, "include_query_list", True):
        grouped: Dict[str, List[str]] = {}
        for job in bundle.get("queries", []) or []:
            grouped.setdefault(_advanced_profile(job), []).append(_advanced_query_key(job))
        details_lines: List[str] = []
        total = 0
        for profile, qs in sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            details_lines.append(f"### `{profile}`")
            for q in qs[:max_query_list]:
                details_lines.append(f"- `{q}`")
                total += 1
            if len(qs) > max_query_list:
                details_lines.append(f"- ... {len(qs) - max_query_list} more `{profile}` queries omitted")
            details_lines.append("")
        lines.append(_advanced_details("Grouped query list", "\n".join(details_lines), open_=False))
        lines.append("")
    return lines


def _advanced_format_source_health(formatter: OutputFormatter, analysis: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    lines = [formatter.heading("Source Health", 2), ""]
    max_sources = _int_param(params, "max_source_rows", 80, 1, 1000)
    max_hosts = _int_param(params, "max_host_rows", 80, 1, 1000)
    lines.append(_advanced_table(formatter, analysis.get("sources", []), ["Source", "Hits", "Docs", "Doc rate", "Avg score", "Top hosts", "Kinds"], max_sources))
    lines.append("")
    lines += [formatter.heading("Host Coverage", 2), ""]
    lines.append(_advanced_table(formatter, analysis.get("hosts", []), ["Host", "Hits", "Docs", "Avg score", "Sources"], max_hosts))
    lines.append("")
    return lines


def _advanced_format_gaps(formatter: OutputFormatter, analysis: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    max_missing = _int_param(params, "max_missing_rows", 80, 1, 1000)
    max_dupes = _int_param(params, "max_duplicate_rows", 40, 1, 1000)
    lines = [formatter.heading("Gaps and Dedupe", 2), ""]
    missing = analysis.get("missing_docs", [])
    no_hits = analysis.get("no_direct_urls", [])
    dupes = analysis.get("duplicate_urls", [])
    if missing:
        lines.append(_advanced_details(
            f"Queries with no fetched docs ({len(missing)})",
            _advanced_table(formatter, missing, ["profile", "topic", "direct_urls", "query"], max_missing),
            open_=False,
        ))
        lines.append("")
    if no_hits:
        lines.append(_advanced_details(
            f"Queries with no direct URL candidates ({len(no_hits)})",
            _advanced_table(formatter, no_hits, ["profile", "topic", "query"], max_missing),
            open_=False,
        ))
        lines.append("")
    if dupes:
        lines.append(_advanced_details(
            f"Duplicate URL evidence ({len(dupes)})",
            _advanced_table(formatter, dupes, ["count", "url"], max_dupes),
            open_=False,
        ))
        lines.append("")
    if not missing and not no_hits and not dupes:
        lines.append("_No major gaps detected._")
        lines.append("")
    return lines


def _advanced_format_top_results(formatter: OutputFormatter, bundle: Dict[str, Any], params: Dict[str, Any]) -> List[str]:
    max_results = _int_param(params, "max_results_total", 160, 1, 5000)
    max_per_query = _int_param(params, "max_docs_per_query", 3, 1, 50)
    include_excerpt = _bool_param(params, "include_page_excerpt", False)
    excerpt_chars = _int_param(params, "excerpt_chars", 800, 120, 100000)

    grouped = _group_docs_by_query(bundle)
    lines = [formatter.heading("Results", 2), ""]
    if not grouped and bundle.get("docs"):
        grouped = {"Direct documentation pages": bundle.get("docs", [])}
    count = 0
    for query, docs in grouped.items():
        if count >= max_results:
            break
        top = sorted(docs, key=lambda d: d.get("score", 0), reverse=True)[:max_per_query]
        if not top:
            continue
        lines.append(formatter.heading(f"Query: {query or 'direct URLs'}", 3))
        lines.append("")
        for doc in top:
            if count >= max_results:
                break
            title = _safe_heading(doc.get("title") or doc.get("url") or "Documentation page")
            url = str(doc.get("url") or "")
            src = str(doc.get("source_key") or "")
            score_band = _advanced_score_band(doc.get("score"))
            score_val = f"{float(doc.get('score', 0) or 0):.2f}"
            saved = str(doc.get("saved_text_path") or "")
            lines.append(f"- **{title}**")
            lines.append(f"  - Source: `{src}` | Score: `{score_val}` ({score_band})")
            lines.append(f"  - URL: {url}")
            if saved:
                lines.append(f"  - Saved text: `{saved}`")
            headings = _doc_headings_line(doc, limit=5)
            if headings:
                lines.append(f"  - Headings: {headings}")
            if include_excerpt:
                ex = _doc_excerpt(formatter, doc, excerpt_chars)
                if ex:
                    lines.append("")
                    lines.append(_advanced_details("Excerpt", ex, open_=False))
            lines.append("")
            count += 1
    if count == 0:
        lines.append("_No documentation pages found._")
        lines.append("")
    return lines


def _advanced_batch_plan(bundle: Dict[str, Any], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = params or {}
    batch_size = _int_param(params, "batch_size", 80, 5, 1000)
    mode = str(_param(params, "batch_mode", default="profile_topic") or "profile_topic").strip().lower()
    jobs = _advanced_list(bundle.get("queries"))
    records = []
    for job in jobs:
        records.append({
            "query": _advanced_query_key(job),
            "profile": _advanced_profile(job),
            "topic": _advanced_topic(job),
            "direct_url": bool(isinstance(job, dict) and job.get("direct_url")),
        })

    if mode == "profile":
        records.sort(key=lambda r: (r["profile"], r["query"]))
    elif mode == "topic":
        records.sort(key=lambda r: (r["topic"], r["profile"], r["query"]))
    else:
        records.sort(key=lambda r: (r["profile"], r["topic"], r["query"]))

    batches = []
    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        profile_counts: Dict[str, int] = {}
        topic_counts: Dict[str, int] = {}
        for r in chunk:
            profile_counts[r["profile"]] = profile_counts.get(r["profile"], 0) + 1
            topic_counts[r["topic"]] = topic_counts.get(r["topic"], 0) + 1
        batches.append({
            "batch": len(batches) + 1,
            "count": len(chunk),
            "profiles": dict(sorted(profile_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
            "topics": dict(sorted(topic_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12]),
            "queries": [r["query"] for r in chunk],
        })
    return {"batch_size": batch_size, "batch_mode": mode, "batch_count": len(batches), "batches": batches}


ADVANCED_QUERY_PACKS = {
    "forensics": [
        "forensics: URL provenance model source static dom runtime cdp metadata archive sitemap",
        "forensics: Evidence chain of custody hash timestamp source collector confidence score",
        "forensics: SQLite FTS5 evidence full text search ranking snippets",
        "forensics: Content-addressable storage sha256 path layout CAS",
        "forensics: SimHash MinHash near duplicate page detection",
        "forensics: Canonical URL normalization preserving original evidence URL",
        "forensics: Redirect graph modeling HTTP meta refresh JavaScript location canonical link",
        "forensics: Metadata extraction EXIF IPTC XMP OpenGraph JSON-LD RDFa microdata",
        "forensics: Lost content recovery via sitemaps feeds archives canonical alternates backlinks",
        "forensics: Hidden asset discovery via JS bundles source maps manifests performance entries",
    ],
    "browser": [
        "browser: Playwright Python Page goto wait_for_load_state content screenshot locator evaluate",
        "browser: Playwright Python BrowserContext route add_init_script expose_binding storage_state tracing",
        "browser: Playwright Python Request url method headers resource_type post_data frame",
        "browser: Playwright Python Response url status headers body text json finished security_details server_addr",
        "browser: Playwright Python CDPSession send Network.enable Runtime.evaluate DOM.getDocument",
        "forensics: Chrome DevTools Protocol Network getResponseBody responseReceivedExtraInfo loadingFinished",
        "forensics: Chrome DevTools Protocol Fetch requestPaused continueRequest authRequired",
        "forensics: Chrome DevTools Protocol Page captureSnapshot MHTML forensic snapshot",
        "forensics: Chrome DevTools Protocol ServiceWorker registrations versions scopes",
        "forensics: Chrome DevTools Protocol Media playerPropertiesChanged events",
    ],
    "archives": [
        "archives: Internet Archive CDX Server API url timestamp statuscode mimetype digest original",
        "archives: Internet Archive Wayback Availability API closest snapshot",
        "archives: Internet Archive Metadata API files dir item metadata",
        "archives: Common Crawl Index API urlkey timestamp mime status digest filename offset length",
        "archives: Common Crawl WARC retrieval by filename offset length",
        "archives: Memento protocol TimeMap Link format datetime rel original memento",
        "forensics: Archive fallback search current URL historical URL canonical URL URL variants",
        "forensics: Public dataset discovery Common Crawl GDELT Internet Archive URLScan",
    ],
    "media": [
        "media: RFC 8216 HTTP Live Streaming HLS master playlist variant streams media segments",
        "media: DASH IF IOP MPD SegmentTemplate SegmentList Representation AdaptationSet",
        "media: HTML media element source track poster preload currentSrc",
        "media: HLS EXT-X-STREAM-INF EXT-X-MEDIA EXTINF EXT-X-KEY EXT-X-MAP",
        "media: schema.org VideoObject contentUrl embedUrl thumbnailUrl uploadDate duration",
        "media: OpenGraph og:image og:video og:audio secure_url width height type",
        "media: yt-dlp extractor info_dict formats requested_formats thumbnails subtitles automatic_captions",
        "media: oEmbed provider discovery endpoint JSON XML thumbnail_url html",
    ],
    "security-passive": [
        "security: OWASP Web Security Testing Guide information gathering search engine discovery fingerprint web server review",
        "security: Wappalyzer technology detection API fingerprints",
        "security: Nuclei templates syntax matchers extractors safe passive templates only",
        "security: URLScan.io API search result page screenshots requests responses",
        "security: TLS certificate transparency crt.sh query API domain discovery",
        "security: Common Crawl index API URL search WARC retrieval",
        "security: Internet Archive CDX Server API URL snapshots memento",
    ],
}


ADVANCED_DIRECT_SOURCES = {
    "playwright_direct": {
        "display_name": "Playwright Python Direct",
        "allowed_domains": ["playwright.dev"],
        "resolver": "topic_registry",
        "profile": "browser",
        "match_terms": ["playwright", "browsercontext", "page", "route", "response", "request", "cdpsession"],
        "seed_urls": [
            "https://playwright.dev/python/docs/api/class-page",
            "https://playwright.dev/python/docs/api/class-browsercontext",
            "https://playwright.dev/python/docs/network",
            "https://playwright.dev/python/docs/events",
            "https://playwright.dev/python/docs/api/class-cdpsession",
        ],
        "topic_urls": {
            "page": ["https://playwright.dev/python/docs/api/class-page"],
            "browsercontext": ["https://playwright.dev/python/docs/api/class-browsercontext"],
            "request": ["https://playwright.dev/python/docs/api/class-request"],
            "response": ["https://playwright.dev/python/docs/api/class-response"],
            "route": ["https://playwright.dev/python/docs/api/class-route"],
            "websocket": ["https://playwright.dev/python/docs/api/class-websocket"],
            "download": ["https://playwright.dev/python/docs/api/class-download"],
            "har": ["https://playwright.dev/python/docs/network"],
            "cdpsession": ["https://playwright.dev/python/docs/api/class-cdpsession"],
        },
        "same_host_crawl_only": True,
    },
    "cdp_direct": {
        "display_name": "Chrome DevTools Protocol Direct",
        "allowed_domains": ["chromedevtools.github.io", "developer.chrome.com"],
        "resolver": "topic_registry",
        "profile": "browser",
        "match_terms": ["chrome devtools protocol", "cdp", "network", "fetch", "runtime", "dom", "serviceworker", "media"],
        "seed_urls": [
            "https://chromedevtools.github.io/devtools-protocol/tot/Network/",
            "https://chromedevtools.github.io/devtools-protocol/tot/Fetch/",
            "https://chromedevtools.github.io/devtools-protocol/tot/Page/",
            "https://chromedevtools.github.io/devtools-protocol/tot/Runtime/",
            "https://chromedevtools.github.io/devtools-protocol/tot/DOM/",
            "https://chromedevtools.github.io/devtools-protocol/tot/ServiceWorker/",
            "https://developer.chrome.com/docs/devtools/protocol/",
        ],
        "topic_urls": {
            "network": ["https://chromedevtools.github.io/devtools-protocol/tot/Network/"],
            "fetch": ["https://chromedevtools.github.io/devtools-protocol/tot/Fetch/"],
            "runtime": ["https://chromedevtools.github.io/devtools-protocol/tot/Runtime/"],
            "dom": ["https://chromedevtools.github.io/devtools-protocol/tot/DOM/"],
            "storage": ["https://chromedevtools.github.io/devtools-protocol/tot/Storage/"],
            "media": ["https://chromedevtools.github.io/devtools-protocol/tot/Media/"],
        },
        "same_host_crawl_only": True,
    },
    "rfc_direct": {
        "display_name": "RFC / IETF Direct",
        "allowed_domains": ["www.rfc-editor.org", "datatracker.ietf.org"],
        "resolver": "topic_registry",
        "profile": "rfc",
        "match_terms": ["rfc", "http semantics", "uri generic syntax", "robots exclusion", "memento", "hls"],
        "seed_urls": [
            "https://www.rfc-editor.org/rfc/rfc3986",
            "https://www.rfc-editor.org/rfc/rfc9110",
            "https://www.rfc-editor.org/rfc/rfc9111",
            "https://www.rfc-editor.org/rfc/rfc8288",
            "https://www.rfc-editor.org/rfc/rfc9309",
            "https://www.rfc-editor.org/rfc/rfc8216",
        ],
        "topic_urls": {
            "rfc 3986": ["https://www.rfc-editor.org/rfc/rfc3986"],
            "rfc 9110": ["https://www.rfc-editor.org/rfc/rfc9110"],
            "rfc 9111": ["https://www.rfc-editor.org/rfc/rfc9111"],
            "rfc 8288": ["https://www.rfc-editor.org/rfc/rfc8288"],
            "rfc 9309": ["https://www.rfc-editor.org/rfc/rfc9309"],
            "rfc 8216": ["https://www.rfc-editor.org/rfc/rfc8216"],
            "memento": ["https://www.rfc-editor.org/rfc/rfc7089"],
        },
        "same_host_crawl_only": True,
    },
    "archive_direct": {
        "display_name": "Archives / Wayback / Common Crawl Direct",
        "allowed_domains": ["archive.org", "index.commoncrawl.org", "commoncrawl.org", "mementoweb.org", "iipc.github.io", "pywb.readthedocs.io", "github.com"],
        "resolver": "topic_registry",
        "profile": "archives",
        "match_terms": ["internet archive", "wayback", "cdx", "common crawl", "warc", "memento", "pywb", "browsertrix"],
        "seed_urls": [
            "https://archive.org/help/wayback_api.php",
            "https://archive.org/developers/metadata.html",
            "https://archive.org/developers/advancedsearch.html",
            "https://index.commoncrawl.org/",
            "https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.1/",
            "https://pywb.readthedocs.io/en/latest/",
        ],
        "topic_urls": {
            "cdx": ["https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server"],
            "metadata": ["https://archive.org/developers/metadata.html"],
            "advanced search": ["https://archive.org/developers/advancedsearch.html"],
            "common crawl": ["https://index.commoncrawl.org/", "https://commoncrawl.org/cc-index-table"],
            "warc": ["https://iipc.github.io/warc-specifications/specifications/warc-format/warc-1.1/"],
            "pywb": ["https://pywb.readthedocs.io/en/latest/"],
        },
        "same_host_crawl_only": False,
    },
    "osint_direct": {
        "display_name": "Passive OSINT API Docs Direct",
        "allowed_domains": ["urlscan.io", "docs.virustotal.com", "docs.securitytrails.com", "search.censys.io", "developer.shodan.io", "www.greynoise.io", "docs.abuseipdb.com", "otx.alienvault.com", "www.misp-project.org", "www.icann.org", "datatracker.ietf.org"],
        "resolver": "topic_registry",
        "profile": "security",
        "match_terms": ["urlscan", "virustotal", "securitytrails", "censys", "shodan", "greynoise", "abuseipdb", "otx", "misp", "rdap"],
        "seed_urls": [
            "https://urlscan.io/docs/api/",
            "https://docs.virustotal.com/reference/overview",
            "https://docs.securitytrails.com/docs",
            "https://search.censys.io/api",
            "https://developer.shodan.io/api",
            "https://www.icann.org/resources/pages/rdap-2018-08-31-en",
        ],
        "topic_urls": {
            "urlscan": ["https://urlscan.io/docs/api/"],
            "virustotal": ["https://docs.virustotal.com/reference/overview"],
            "securitytrails": ["https://docs.securitytrails.com/docs"],
            "censys": ["https://search.censys.io/api"],
            "shodan": ["https://developer.shodan.io/api"],
            "rdap": ["https://www.icann.org/resources/pages/rdap-2018-08-31-en", "https://datatracker.ietf.org/doc/html/rfc9082", "https://datatracker.ietf.org/doc/html/rfc9083"],
        },
        "same_host_crawl_only": False,
    },
    "structured_data_direct": {
        "display_name": "Structured Data / Media Metadata Direct",
        "allowed_domains": ["schema.org", "ogp.me", "developer.x.com", "json-ld.org", "www.w3.org", "oembed.com", "docs.oembed.com", "developers.google.com"],
        "resolver": "topic_registry",
        "profile": "media",
        "match_terms": ["schema.org", "opengraph", "open graph", "json-ld", "rdfa", "microdata", "oembed", "videoobject", "imageobject"],
        "seed_urls": [
            "https://schema.org/VideoObject",
            "https://schema.org/ImageObject",
            "https://schema.org/AudioObject",
            "https://ogp.me/",
            "https://json-ld.org/spec/latest/json-ld/",
            "https://oembed.com/",
            "https://developers.google.com/search/docs/appearance/structured-data/video",
        ],
        "topic_urls": {
            "videoobject": ["https://schema.org/VideoObject"],
            "imageobject": ["https://schema.org/ImageObject"],
            "audioobject": ["https://schema.org/AudioObject"],
            "opengraph": ["https://ogp.me/"],
            "json-ld": ["https://json-ld.org/spec/latest/json-ld/", "https://www.w3.org/TR/json-ld11/"],
            "oembed": ["https://oembed.com/", "https://docs.oembed.com/"],
        },
        "same_host_crawl_only": False,
    },
}


def _install_advanced_apidoc_sources() -> None:
    DEFAULT_CONFIG.setdefault("sources", {}).update(ADVANCED_DIRECT_SOURCES)
    profiles = DEFAULT_CONFIG.setdefault("profiles", {})
    profiles.setdefault("browser", [])
    for key in ("playwright_direct", "cdp_direct"):
        if key not in profiles["browser"]:
            profiles["browser"].append(key)
    profiles.setdefault("archives", [])
    if "archive_direct" not in profiles["archives"]:
        profiles["archives"].append("archive_direct")
    profiles.setdefault("security", [])
    if "osint_direct" not in profiles["security"]:
        profiles["security"].append("osint_direct")
    profiles.setdefault("media", [])
    if "structured_data_direct" not in profiles["media"]:
        profiles["media"].append("structured_data_direct")
    profiles.setdefault("rfc", [])
    if "rfc_direct" not in profiles["rfc"]:
        profiles["rfc"].append("rfc_direct")

    all_list = profiles.setdefault("all", [])
    for key in ADVANCED_DIRECT_SOURCES:
        if key not in all_list:
            all_list.append(key)

    PREFIX_PROFILE.update({
        "browser": "browser",
        "playwright": "browser",
        "cdp": "browser",
        "chrome": "browser",
        "archives": "archives",
        "archive": "archives",
        "wayback": "archives",
        "warc": "archives",
        "osint": "security",
        "security": "security",
        "passive": "security",
        "media": "media",
        "structured": "media",
        "rfc": "rfc",
        "ietf": "rfc",
    })

    FORMAT_STYLE_ALIASES.update({
        "advanced": "advanced_report",
        "advanced_report": "advanced_report",
        "forensic": "advanced_report",
        "forensic_report": "advanced_report",
        "intelligence": "advanced_report",
        "coverage": "coverage",
        "source_health": "source_health",
        "health": "source_health",
        "batch_plan": "batch_plan",
        "profile_matrix": "coverage",
    })

    FORMAT_COMMON_PARAMS.update({
        "include_intelligence": True,
        "include_profile_coverage": True,
        "include_source_health": True,
        "include_host_coverage": True,
        "include_gaps": True,
        "max_profile_rows": 40,
        "max_topic_rows": 80,
        "max_source_rows": 80,
        "max_host_rows": 80,
        "max_missing_rows": 80,
        "batch_size": 80,
        "batch_mode": "profile_topic",
    })


_install_advanced_apidoc_sources()


_ORIGINAL_FORMAT_APIDOC_BUNDLE_ADVANCED = format_apidoc_bundle
_ORIGINAL_WRITE_OUTPUTS_ADVANCED = APIDocEngine.write_outputs


def _advanced_format_apidoc_bundle(bundle: Dict[str, Any], params: Optional[Dict[str, Any]] = None, *, search_fallback: bool = False) -> str:
    params = params or {}
    formatter = OutputFormatter(params)
    style = formatter.style
    analysis = _advanced_bundle_analysis(bundle, params=params)
    title = _safe_heading(_param(params, "title", default="API Documentation Direct Request Results"), "API Documentation Direct Request Results")

    if style in {"raw", "plain", "outline"}:
        return _ORIGINAL_FORMAT_APIDOC_BUNDLE_ADVANCED(bundle, params, search_fallback=search_fallback)

    if style == "source_health":
        lines = [formatter.heading("APIDoc Source Health", 1), ""]
        lines += _advanced_format_source_health(formatter, analysis, params)
        lines += _advanced_format_gaps(formatter, analysis, params)
        return "\n".join(lines).rstrip() + "\n"

    if style == "coverage":
        lines = [formatter.heading("APIDoc Coverage Matrix", 1), ""]
        lines += _advanced_format_query_groups(formatter, bundle, analysis, params)
        lines += _advanced_format_gaps(formatter, analysis, params)
        return "\n".join(lines).rstrip() + "\n"

    if style == "batch_plan":
        plan = _advanced_batch_plan(bundle, params)
        if _bool_param(params, "json", False):
            return json.dumps(plan, indent=2, ensure_ascii=False, default=str) + "\n"
        lines = [formatter.heading("APIDoc Batch Plan", 1), ""]
        lines.append(formatter.table([{
            "Field": "Batch size",
            "Value": plan["batch_size"],
        }, {
            "Field": "Batch mode",
            "Value": plan["batch_mode"],
        }, {
            "Field": "Batches",
            "Value": plan["batch_count"],
        }], ["Field", "Value"]))
        lines.append("")
        for batch in plan["batches"]:
            body = []
            body.append(f"- Count: `{batch['count']}`")
            body.append(f"- Profiles: `{', '.join(f'{k}:{v}' for k, v in batch['profiles'].items())}`")
            body.append(f"- Topics: `{', '.join(f'{k}:{v}' for k, v in batch['topics'].items())}`")
            body.append("")
            body.extend(f"- `{q}`" for q in batch["queries"])
            lines.append(_advanced_details(f"Batch {batch['batch']} ({batch['count']} queries)", "\n".join(body), open_=batch["batch"] == 1))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    if style in {"query_pack", "asset_index", "chat", "compact"}:
        # Keep the compact specialized modes from the readable renderer.
        return _ORIGINAL_FORMAT_APIDOC_BUNDLE_ADVANCED(bundle, params, search_fallback=search_fallback)

    # Advanced default clean_report.
    lines: List[str] = []
    if _bool_param(params, "front_matter", False):
        lines.append(formatter.front_matter({
            "title": title,
            "mode": str(bundle.get("mode") or "direct-first"),
            "queries": analysis["counts"]["queries"],
            "direct_urls": analysis["counts"]["direct_urls"],
            "docs": analysis["counts"]["docs"],
            "errors": analysis["counts"]["errors"],
            "coverage": analysis["counts"]["coverage"],
            "output_style": style,
            "advanced_apidoc_version": ADVANCED_APIDOC_VERSION,
        }).rstrip())
        lines.append("")

    lines += [formatter.heading(title, 1), "", f"Generated by PromptChat `apidoc` in `{str(bundle.get('mode') or 'direct-first')}` mode with advanced coverage intelligence.", ""]
    if _bool_param(params, "include_toc", True):
        toc_seed = "\n".join([
            formatter.heading(title, 1),
            formatter.heading("Summary", 2),
            formatter.heading("Intelligence Summary", 2),
            formatter.heading("Profile Coverage", 2),
            formatter.heading("Topic Matrix", 2),
            formatter.heading("Source Health", 2),
            formatter.heading("Host Coverage", 2),
            formatter.heading("Gaps and Dedupe", 2),
            formatter.heading("Results", 2),
            formatter.heading("Asset Index", 2),
            formatter.heading("Errors", 2),
        ])
        lines.append(formatter.heading("Table of Contents", 2))
        lines.append("")
        lines.append(formatter.toc(toc_seed, max_depth=2, structural_only=False, max_items=_int_param(params, "max_toc_items", 80, 1, 1000)))
        lines.append("")

    lines += [formatter.heading("Summary", 2), ""]
    summary_rows = _summary_rows(bundle, formatter, search_fallback)
    extra = analysis["counts"]
    summary_rows.extend([
        {"Field": "Covered queries", "Value": f"`{extra['covered_queries']}` / `{extra['queries']}`"},
        {"Field": "Coverage", "Value": f"`{extra['coverage']}`"},
        {"Field": "Unique doc hosts", "Value": f"`{extra['unique_doc_hosts']}`"},
        {"Field": "Unique sources", "Value": f"`{extra['unique_sources']}`"},
        {"Field": "Duplicate URL evidence", "Value": f"`{extra['duplicate_urls']}`"},
        {"Field": "Advanced layer", "Value": f"`{ADVANCED_APIDOC_VERSION}`"},
    ])
    lines.append(formatter.table(summary_rows, ["Field", "Value"]))
    lines.append("")

    lines += [formatter.heading("Intelligence Summary", 2), ""]
    counts = analysis["counts"]
    lines.extend([
        f"- **Coverage:** `{counts['coverage']}` of queries produced at least one fetched documentation page.",
        f"- **Scale:** `{counts['queries']}` queries → `{counts['direct_urls']}` direct URL candidates → `{counts['docs']}` fetched documentation pages.",
        f"- **Health:** `{counts['errors']}` recorded errors; `{counts['missing_docs']}` queries had no fetched docs.",
        f"- **Source spread:** `{counts['unique_sources']}` source keys across `{counts['unique_doc_hosts']}` documentation hosts.",
    ])
    lines.append("")

    if _bool_param(params, "include_profile_coverage", True):
        lines += _advanced_format_query_groups(formatter, bundle, analysis, params)

    if _bool_param(params, "include_source_health", True):
        lines += _advanced_format_source_health(formatter, analysis, params)

    if _bool_param(params, "include_gaps", True):
        lines += _advanced_format_gaps(formatter, analysis, params)

    if _bool_param(params, "include_results", True):
        lines += _advanced_format_top_results(formatter, bundle, params)

    if _bool_param(params, "include_asset_index", True):
        lines.extend(_format_asset_index(formatter, bundle))

    if _bool_param(params, "include_errors", True):
        lines.extend(_format_errors(formatter, bundle))

    return "\n".join(lines).rstrip() + "\n"


# Replace the renderer used by existing APIDoc blocks without changing their signatures.
format_apidoc_bundle = _advanced_format_apidoc_bundle


def _advanced_write_outputs(self: APIDocEngine, bundle: Dict[str, Any], markdown: str) -> Dict[str, Any]:
    meta = _ORIGINAL_WRITE_OUTPUTS_ADVANCED(self, bundle, markdown)
    if not meta.get("wrote"):
        return meta
    try:
        assets = Path(str(meta.get("assets_dir") or ""))
        assets.mkdir(parents=True, exist_ok=True)
        analysis = _advanced_bundle_analysis(bundle, params=self.params)
        (assets / "analysis.json").write_text(json.dumps(analysis, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        queries_path = assets / "queries.txt"
        with queries_path.open("w", encoding="utf-8") as f:
            for job in bundle.get("queries", []) or []:
                f.write(_advanced_query_key(job) + "\n")

        urls_path = assets / "direct_urls.txt"
        with urls_path.open("w", encoding="utf-8") as f:
            seen = set()
            for hit in bundle.get("hits", []) or []:
                if not isinstance(hit, dict):
                    continue
                u = str(hit.get("url") or "")
                if u and u not in seen:
                    seen.add(u)
                    f.write(u + "\n")

        def write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
            if not rows:
                path.write_text("", encoding="utf-8")
                return
            cols: List[str] = []
            for row in rows:
                for key in row:
                    if key not in cols:
                        cols.append(key)
            with path.open("w", encoding="utf-8") as f:
                f.write(",".join(cols) + "\n")
                for row in rows:
                    vals = []
                    for c in cols:
                        v = str(row.get(c, ""))
                        v = '"' + v.replace('"', '""') + '"' if any(x in v for x in [",", "\n", '"']) else v
                        vals.append(v)
                    f.write(",".join(vals) + "\n")

        write_csv(assets / "source_health.csv", analysis.get("sources", []))
        write_csv(assets / "profile_coverage.csv", analysis.get("profiles", []))
        write_csv(assets / "topic_matrix.csv", analysis.get("topics", []))
        write_csv(assets / "host_coverage.csv", analysis.get("hosts", []))
        write_csv(assets / "missing_docs.csv", analysis.get("missing_docs", []))

        meta["analysis_path"] = str(assets / "analysis.json")
        meta["queries_path"] = str(queries_path)
        meta["direct_urls_path"] = str(urls_path)
        meta["source_health_path"] = str(assets / "source_health.csv")
        meta["profile_coverage_path"] = str(assets / "profile_coverage.csv")
        meta["topic_matrix_path"] = str(assets / "topic_matrix.csv")
        meta["host_coverage_path"] = str(assets / "host_coverage.csv")
        meta["missing_docs_path"] = str(assets / "missing_docs.csv")
    except Exception as exc:
        meta["advanced_write_error"] = repr(exc)
    return meta


APIDocEngine.write_outputs = _advanced_write_outputs


@dataclass
class APIDocAnalyzeBlock(BaseBlock):
    """Analyze a fetched APIDoc bundle or readable Markdown report."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        bundle = _advanced_ensure_bundle(payload)
        analysis = _advanced_bundle_analysis(bundle, params=params)
        output_style = str(_param(params, "output_style", "style", default="advanced_report") or "advanced_report")
        if as_bool(params.get("json"), False) or output_style == "raw":
            text = json.dumps(analysis, indent=2, ensure_ascii=False, default=str)
            meta = {"type": "apidoc-analysis", **analysis.get("counts", {})}
            meta.update(_write_optional_text_output(text, params, "apidoc_analysis.json"))
            return text, meta

        formatter = OutputFormatter({**params, "output_style": output_style})
        lines = [formatter.heading("APIDoc Analysis", 1), ""]
        lines.append(formatter.table([{"Field": k, "Value": v} for k, v in analysis.get("counts", {}).items()], ["Field", "Value"]))
        lines.append("")
        lines += _advanced_format_query_groups(formatter, bundle, analysis, params)
        lines += _advanced_format_source_health(formatter, analysis, params)
        lines += _advanced_format_gaps(formatter, analysis, params)
        text = "\n".join(lines).rstrip() + "\n"
        meta = {"type": "apidoc-analysis", **analysis.get("counts", {})}
        meta.update(_write_optional_text_output(text, params, "apidoc_analysis.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {
            "out_path": "Optional .md or .json output path.",
            "json": False,
            "max_profile_rows": 40,
            "max_topic_rows": 80,
            "max_source_rows": 80,
            "max_host_rows": 80,
        }


@dataclass
class APIDocBatchPlanBlock(BaseBlock):
    """Split a large APIDoc query pack into profile/topic batches."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        bundle = _advanced_ensure_bundle(payload)
        if not bundle.get("queries"):
            engine = APIDocEngine(params=params)
            jobs, _meta = engine.read_queries(payload)
            bundle = {"queries": jobs, "hits": [], "docs": [], "errors": []}
        plan = _advanced_batch_plan(bundle, params)
        if as_bool(params.get("json"), False):
            text = json.dumps(plan, indent=2, ensure_ascii=False, default=str)
            meta = {"type": "apidoc-batch-plan", "batch_count": plan["batch_count"], "batch_size": plan["batch_size"]}
            meta.update(_write_optional_text_output(text, params, "apidoc_batches.json"))
            return text, meta
        formatter = OutputFormatter({**params, "output_style": "batch_plan"})
        text = _advanced_format_apidoc_bundle(bundle, {**params, "output_style": "batch_plan"}, search_fallback=False)
        meta = {"type": "apidoc-batch-plan", "batch_count": plan["batch_count"], "batch_size": plan["batch_size"]}
        meta.update(_write_optional_text_output(text, params, "apidoc_batches.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"batch_size": 80, "batch_mode": "profile_topic | profile | topic", "json": False, "out_path": "Optional output path."}


@dataclass
class APIDocQueryPackBlock(BaseBlock):
    """Emit built-in advanced query packs for forensic/browser/archive/media/security documentation."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        names_raw = str(_param(params, "packs", "pack", default="forensics,browser,archives,media,security-passive") or "")
        requested = [x.strip().lower() for x in re.split(r"[,;\s]+", names_raw) if x.strip()]
        if not requested or requested == ["all"]:
            requested = sorted(ADVANCED_QUERY_PACKS)
        lines: List[str] = []
        for name in requested:
            pack = ADVANCED_QUERY_PACKS.get(name)
            if not pack:
                continue
            if as_bool(params.get("with_headers"), False):
                lines.append(f"# {name}")
            lines.extend(pack)
            lines.append("")
        text = "\n".join(lines).strip() + "\n"
        meta = {"type": "apidoc-query-pack", "packs": requested, "queries": len([l for l in text.splitlines() if l and not l.startswith('#')])}
        meta.update(_write_optional_text_output(text, params, "advanced_apidoc_queries.txt"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"packs": "forensics,browser,archives,media,security-passive or all", "with_headers": False, "out_path": "Optional .txt path."}


@dataclass
class APIDocSourceHealthBlock(BaseBlock):
    """Render only source/domain health from an APIDoc bundle or report."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        bundle = _advanced_ensure_bundle(payload)
        text = _advanced_format_apidoc_bundle(bundle, {**params, "output_style": "source_health"}, search_fallback=False)
        analysis = _advanced_bundle_analysis(bundle, params=params)
        meta = {"type": "apidoc-source-health", **analysis.get("counts", {})}
        meta.update(_write_optional_text_output(text, params, "apidoc_source_health.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"max_source_rows": 80, "max_host_rows": 80, "out_path": "Optional .md path."}


@dataclass
class APIDocCoverageBlock(BaseBlock):
    """Render profile/topic coverage from an APIDoc bundle or report."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        bundle = _advanced_ensure_bundle(payload)
        text = _advanced_format_apidoc_bundle(bundle, {**params, "output_style": "coverage"}, search_fallback=False)
        analysis = _advanced_bundle_analysis(bundle, params=params)
        meta = {"type": "apidoc-coverage", **analysis.get("counts", {})}
        meta.update(_write_optional_text_output(text, params, "apidoc_coverage.md"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {"max_profile_rows": 40, "max_topic_rows": 80, "out_path": "Optional .md path."}


@dataclass
class APIDocAdvancedBlock(BaseBlock):
    """All-in-one APIDoc run using the advanced report renderer and analysis artifacts."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        merged = _apidoc_default_output_params(params)
        return APIDocBlock().execute(payload, params=merged)

    def get_params_info(self) -> Dict[str, Any]:
        info = dict(COMMON_PARAMS)
        info.update({
            "output_style": "advanced_report",
            "include_asset_index": True,
            "max_profile_rows": 40,
            "max_topic_rows": 80,
            "max_source_rows": 80,
            "max_host_rows": 80,
        })
        return info


# New names only; existing names keep working through the monkey-patched renderer.
BLOCKS.register("apidoc_analyze", APIDocAnalyzeBlock)
BLOCKS.register("apidoc_intel", APIDocAnalyzeBlock)
BLOCKS.register("apidoc_batch_plan", APIDocBatchPlanBlock)
BLOCKS.register("apidoc_batches", APIDocBatchPlanBlock)
BLOCKS.register("apidoc_query_pack", APIDocQueryPackBlock)
BLOCKS.register("apidoc_advanced_queries", APIDocQueryPackBlock)
BLOCKS.register("apidoc_source_health", APIDocSourceHealthBlock)
BLOCKS.register("apidoc_health", APIDocSourceHealthBlock)
BLOCKS.register("apidoc_coverage", APIDocCoverageBlock)
BLOCKS.register("apidoc_matrix", APIDocCoverageBlock)
BLOCKS.register("apidoc_advanced", APIDocAdvancedBlock)

# ---------------------------------------------------------------------------
# Crash-safe v4 runtime patch
# ---------------------------------------------------------------------------
# Fix target: Windows process exit -1073740791 / 0xC0000409.
# In practice this usually means a hard native abort/stack-buffer fast fail, not
# a normal Python exception.  This block keeps the APIDoc public API the same but
# removes the risky parts that can trigger hard exits on Windows IDE runs:
#   - no recursive/deep page crawling by default
#   - strict query/direct-url/page/link/response-size caps
#   - streaming HTTP reads instead of unbounded Response.text materialization
#   - built-in HTMLParser extraction instead of repeatedly building full BS4 DOMs
#   - cache/session cleanup and best-effort faulthandler logging

try:
    import faulthandler as _apidoc_faulthandler
    if not _apidoc_faulthandler.is_enabled():
        _apidoc_faulthandler.enable()
except Exception:
    _apidoc_faulthandler = None

from html.parser import HTMLParser as _APIDocSafeHTMLParser
from html import unescape as _apidoc_unescape


def _apidoc_safe_bool(value: Any, default: bool = False) -> bool:
    try:
        return as_bool(value, default)
    except Exception:
        return default


def _apidoc_safe_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        return as_int(value, default, min_value, max_value)
    except Exception:
        return default


class _APIDocSafeResponse:
    __slots__ = ("url", "text", "headers", "status_code")

    def __init__(self, url: str, text: str, headers: Optional[Dict[str, str]] = None, status_code: int = 200):
        self.url = url
        self.text = text or ""
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.status_code = int(status_code or 200)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code} for {self.url}")


class _APIDocSafeExtractor(_APIDocSafeHTMLParser):
    _BLOCK_TAGS = {"p", "div", "section", "article", "main", "br", "li", "tr", "td", "th", "pre", "blockquote"}
    _SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "form", "iframe", "nav", "header", "footer"}
    _HEADING_TAGS = {"h1", "h2", "h3", "h4"}

    def __init__(self, base_url: str, max_links: int, max_chars: int):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.max_links = max(0, int(max_links or 0))
        self.max_chars = max(500, int(max_chars or 8000))
        self.links: List[str] = []
        self._seen_links: Set[str] = set()
        self.text_parts: List[str] = []
        self.headings: List[str] = []
        self.title_parts: List[str] = []
        self._skip_depth = 0
        self._capture_title = False
        self._heading_tag = ""
        self._heading_parts: List[str] = []
        self._text_len = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        tag = (tag or "").lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._capture_title = True
        if tag in self._HEADING_TAGS:
            self._heading_tag = tag
            self._heading_parts = []
        if tag == "a" and self.max_links:
            href = ""
            for k, v in attrs or []:
                if (k or "").lower() == "href":
                    href = (v or "").strip()
                    break
            if href and not href.startswith(("mailto:", "javascript:", "tel:", "#")):
                link = absolute_url(self.base_url, href)
                if link and html_url(link) and link not in self._seen_links and len(self.links) < self.max_links:
                    self._seen_links.add(link)
                    self.links.append(link)
        if tag in self._BLOCK_TAGS:
            self._append_text("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = (tag or "").lower()
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._capture_title = False
        if self._heading_tag and tag == self._heading_tag:
            h = " ".join(" ".join(self._heading_parts).split()).strip()
            if h and h not in self.headings and len(self.headings) < 50:
                self.headings.append(h)
            self._heading_tag = ""
            self._heading_parts = []
        if tag in self._BLOCK_TAGS:
            self._append_text("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        data = _apidoc_unescape(data)
        if self._capture_title:
            self.title_parts.append(data.strip())
        if self._heading_tag:
            self._heading_parts.append(data.strip())
        self._append_text(data)

    def _append_text(self, data: str) -> None:
        if not data or self._text_len >= self.max_chars:
            return
        remaining = self.max_chars - self._text_len
        chunk = data[:remaining]
        self.text_parts.append(chunk)
        self._text_len += len(chunk)

    def result(self) -> Dict[str, Any]:
        title = " ".join(" ".join(self.title_parts).split()).strip() or "Untitled documentation page"
        text = "".join(self.text_parts)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if self._text_len >= self.max_chars:
            text = text.rstrip() + "\n\n...[clipped by crash-safe max_chars_per_page]..."
        return {"title": title, "headings": self.headings[:50], "text": text, "links": self.links[:self.max_links]}




def _apidoc_query_limit(params: Optional[Dict[str, Any]], default: int = 100000, hard_max: int = 1000000) -> int:
    """Return the APIDoc query limit.

    0 means unlimited.  This deliberately ignores the old safe_max_queries path;
    query caps are controlled only by max_queries now.
    """
    p = params or {}
    raw = p.get("max_queries", default)
    if raw is None:
        return 0
    if isinstance(raw, str):
        text = raw.strip().lower()
        if text in {"", "0", "none", "null", "false", "off", "no", "unlimited", "inf", "infinite", "all"}:
            return 0
    try:
        value = int(raw)
    except Exception:
        value = int(default)
    if value <= 0:
        return 0
    return min(value, int(hard_max))

_ORIGINAL_APIDOC_ENGINE_INIT_CRASHSAFE = APIDocEngine.__init__
_ORIGINAL_APIDOC_ENGINE_READ_QUERIES_CRASHSAFE = APIDocEngine.read_queries
_ORIGINAL_APIDOC_ENGINE_HTTP_GET_CRASHSAFE = APIDocEngine.http_get
_ORIGINAL_APIDOC_ENGINE_SEARCH_CRASHSAFE = APIDocEngine.search
_ORIGINAL_APIDOC_ENGINE_FETCH_QUERY_CRASHSAFE = APIDocEngine.fetch_query
_ORIGINAL_APIDOC_ENGINE_EXTRACT_PAGE_CRASHSAFE = APIDocEngine.extract_page


def _apidoc_crashsafe_init(self: APIDocEngine, params: Optional[Dict[str, Any]] = None, progress: Optional[Callable[[str], None]] = None):
    params = dict(params or {})

    # Full APIDoc mode: safe_mode no longer caps queries/pages/search.
    # Keep the public parameter name for compatibility, but force it off inside
    # this engine so old safe_* limits can never silently shrink the job list.
    params["safe_mode"] = False
    params.pop("safe_max_queries", None)
    params.pop("safe_max_pages_per_query", None)
    params.pop("safe_max_direct_urls_per_query", None)
    params.pop("safe_max_links_per_page", None)
    params.pop("safe_max_search_hits", None)
    params.pop("safe_max_chars_per_page", None)

    requested_direct_mode = _apidoc_safe_bool(params.get("direct_mode"), True)
    if not requested_direct_mode:
        params.setdefault("search_mode", "search_first")
        params["search_fallback"] = True
    else:
        params.setdefault("search_mode", "direct_first")
        params.setdefault("search_fallback", False)

    # High, explicit defaults.  These are normal max_* settings, not safe-mode caps.
    params.setdefault("crawl_direct_pages", False)
    params.setdefault("max_pages_per_query", 8)
    params.setdefault("max_direct_urls_per_query", 32)
    params.setdefault("max_links_per_page", 160)
    params.setdefault("max_search_hits", 20)
    params.setdefault("max_chars_per_page", 24000)
    params.setdefault("max_response_bytes", 5_000_000)
    params.setdefault("max_queries", 100000)
    params.setdefault("timeout", 20)
    params.setdefault("delay", 0.03)
    params.setdefault("use_safe_html_parser", False)

    _ORIGINAL_APIDOC_ENGINE_INIT_CRASHSAFE(self, params=params, progress=progress)

    # Important: force runtime state off too.  Several methods branch on self.safe_mode.
    self.safe_mode = False
    self.params["safe_mode"] = False

    try:
        import atexit
        atexit.register(lambda: getattr(self, "session", None) and self.session.close())
    except Exception:
        pass


def _apidoc_crashsafe_read_queries(self: APIDocEngine, payload: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    jobs, meta = _ORIGINAL_APIDOC_ENGINE_READ_QUERIES_CRASHSAFE(self, payload)

    # No safe-mode query cap.  Only max_queries applies; max_queries=0/None/"unlimited"
    # means no truncation.
    max_queries = _apidoc_query_limit(getattr(self, "params", {}) or {}, default=100000, hard_max=1000000)
    if max_queries and len(jobs) > max_queries:
        meta = dict(meta or {})
        meta["original_count"] = len(jobs)
        meta["truncated_to"] = max_queries
        meta["query_limit_note"] = "Query list was capped only by max_queries. Set max_queries=0 or 'unlimited' for no cap."
        jobs = jobs[:max_queries]
    return jobs, meta


def _apidoc_crashsafe_http_get(self: APIDocEngine, url: str, use_cache: Optional[bool] = None):
    url = clean_url(url)
    if not url:
        return None
    use_cache = self.use_cache if use_cache is None else use_cache
    cp = self.cache_path(url)
    if use_cache and cp.exists():
        try:
            return _APIDocSafeResponse(url, cp.read_text(encoding="utf-8", errors="replace"), {"Content-Type": "text/html; cached"}, 200)
        except Exception:
            pass

    elapsed = time.monotonic() - getattr(self, "_last", 0.0)
    if self.delay and elapsed < self.delay:
        time.sleep(self.delay - elapsed)

    max_bytes = _apidoc_safe_int(self.params.get("max_response_bytes"), 1_250_000, 65536, 10_000_000)
    chunk_size = _apidoc_safe_int(self.params.get("http_chunk_size"), 65536, 4096, 262144)
    try:
        with self.session.get(url, timeout=(min(float(self.timeout), 10.0), float(self.timeout)), allow_redirects=True, stream=True) as r:
            self._last = time.monotonic()
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            text_like = ("html" in ctype) or ("text/" in ctype) or ("markdown" in ctype) or ("json" in ctype) or not ctype
            if not text_like:
                return None
            raw = bytearray()
            clipped = False
            for chunk in r.iter_content(chunk_size=chunk_size, decode_unicode=False):
                if not chunk:
                    continue
                if len(raw) + len(chunk) > max_bytes:
                    raw.extend(chunk[: max(0, max_bytes - len(raw))])
                    clipped = True
                    break
                raw.extend(chunk)
            enc = r.encoding or "utf-8"
            try:
                text = bytes(raw).decode(enc, errors="replace")
            except Exception:
                text = bytes(raw).decode("utf-8", errors="replace")
            if clipped:
                text += "\n\n...[clipped by crash-safe max_response_bytes]..."
            resp = _APIDocSafeResponse(clean_url(r.url), text, dict(r.headers), int(r.status_code))
            if use_cache:
                try:
                    cp.write_text(text, encoding="utf-8")
                except Exception:
                    pass
            return resp
    except Exception as e:
        self.progress(f"[http-safe] failed: {url} :: {e}")
        return None


def _apidoc_crashsafe_search(self: APIDocEngine, job, key, src):
    if self.safe_mode and self.direct_mode and not _apidoc_safe_bool(self.params.get("allow_search_in_safe_mode"), False):
        return []
    if not self.search_fallback:
        return []
    q = str(job.get("query") or "")
    ddg = self.config["settings"].get("duckduckgo_url", "https://duckduckgo.com/html/?q={query}")
    r = self.http_get(ddg.format(query=quote_plus(q)), use_cache=False)
    if not r:
        return []
    parser = _APIDocSafeExtractor(str(r.url), max_links=self.max_hits * 4, max_chars=min(self.max_chars, 16000))
    try:
        parser.feed(str(r.text or "")[: min(len(str(r.text or "")), 250000)])
    except Exception:
        pass
    hits = []
    for u in parser.links:
        if src.get("docs_like_only") and not docs_like_url(u):
            continue
        title = u
        hits.append(self.hit(job, key, src, u, title, score(q, title, u), "search_query_safe" if not self.direct_mode else "search_fallback_safe"))
        if len(hits) >= self.max_hits:
            break
    return hits


def _apidoc_crashsafe_fetch_query(self: APIDocEngine, job, hits):
    docs, errors, seen = [], [], set()
    queue = list(hits or [])[: self.max_direct]
    scan_limit = self.max_pages
    if not self.safe_mode and self.crawl_direct_pages:
        scan_limit = min(self.max_pages * 3, 240)
    while queue and len(seen) < scan_limit and len(docs) < self.max_pages:
        hit = queue.pop(0)
        url = clean_url(str(hit.get("url") or ""))
        if not url or url in seen:
            continue
        seen.add(url)
        src = self.config["sources"].get(str(hit.get("source_key") or ""), {"allowed_domains": ["*"], "allow_any_domain": True})
        try:
            doc = self.extract_page(url, str(job.get("query") or ""), hit, src)
        except RecursionError as exc:
            errors.append({"stage": "extract", "url": url, "error": f"recursion_guard:{exc}"})
            continue
        except Exception as exc:
            errors.append({"stage": "extract", "url": url, "error": repr(exc)})
            continue
        if not doc:
            continue
        docs.append(doc)
        if self.safe_mode or not self.crawl_direct_pages:
            continue
        links = sorted([u for u in doc.get("links", []) if u not in seen and self.can_follow(doc["url"], u, src)], key=lambda u: score(job.get("query", ""), "", u), reverse=True)
        for link in links[: max(1, min(self.max_links // 3, 24))]:
            if len(queue) >= self.max_links:
                break
            queue.append({**hit, "url": link, "title": link, "kind": "direct_crawl_safe", "score": score(job.get("query", ""), "", link)})
    return sorted(docs, key=lambda d: d.get("score", 0), reverse=True)[:self.max_pages], errors


def _apidoc_crashsafe_extract_page(self: APIDocEngine, url, query, hit, src):
    r = self.http_get(url)
    if not r or not getattr(r, "text", ""):
        return None
    ctype = (r.headers.get("Content-Type") or "").lower()
    host = hostname(str(r.url))
    is_markdown = "markdown" in ctype or "text/plain" in ctype or "raw.githubusercontent.com" in host
    text_raw = str(r.text or "")
    if is_markdown:
        text = text_raw[:self.max_chars].rstrip()
        if len(text_raw) > self.max_chars:
            text += "\n\n...[clipped by crash-safe max_chars_per_page]..."
        title = str(hit.get("title") or r.url)
        return {
            "query": query,
            "source_key": hit.get("source_key"),
            "source_name": hit.get("source_name"),
            "url": clean_url(r.url),
            "title": title,
            "score": score(query, title, r.url, text) + float(hit.get("score", 0)) * 0.15,
            "headings": re.findall(r"^#{1,4}\s+(.+)$", text, flags=re.M)[:50],
            "text": text,
            "links": [],
            "hit_kind": hit.get("kind"),
            "safe_mode": bool(getattr(self, "safe_mode", True)),
        }
    if "html" not in ctype and not text_raw.lstrip().startswith("<"):
        return None
    parser = _APIDocSafeExtractor(clean_url(str(r.url)), max_links=self.max_links, max_chars=self.max_chars)
    try:
        parser.feed(text_raw[: min(len(text_raw), _apidoc_safe_int(self.params.get("max_html_parse_chars"), 300000, 10000, 2_000_000))])
        parser.close()
    except Exception as exc:
        self.progress(f"[html-safe] partial parse: {url} :: {exc}")
    parsed = parser.result()
    title = parsed.get("title") or str(hit.get("title") or r.url)
    text = parsed.get("text") or title
    return {
        "query": query,
        "source_key": hit.get("source_key"),
        "source_name": hit.get("source_name"),
        "url": clean_url(r.url),
        "title": title,
        "score": score(query, title, r.url, text) + float(hit.get("score", 0)) * 0.15,
        "headings": parsed.get("headings", [])[:50],
        "text": text,
        "links": parsed.get("links", [])[:self.max_links],
        "hit_kind": hit.get("kind"),
        "safe_mode": bool(getattr(self, "safe_mode", True)),
    }


APIDocEngine.__init__ = _apidoc_crashsafe_init
APIDocEngine.read_queries = _apidoc_crashsafe_read_queries
APIDocEngine.http_get = _apidoc_crashsafe_http_get
APIDocEngine.search = _apidoc_crashsafe_search
APIDocEngine.fetch_query = _apidoc_crashsafe_fetch_query
APIDocEngine.extract_page = _apidoc_crashsafe_extract_page


# Snapshot the pre-crashsafe output-param builder before replacing it.
# This must be assigned before _apidoc_crashsafe_output_params is defined so
# static checkers/IDEs do not report _ORIGINAL_APIDOC_DEFAULT_OUTPUT_PARAMS as unresolved.
_ORIGINAL_APIDOC_DEFAULT_OUTPUT_PARAMS = _apidoc_default_output_params


def _apidoc_crashsafe_output_params(params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged = _ORIGINAL_APIDOC_DEFAULT_OUTPUT_PARAMS(params)

    # Full APIDoc mode by default.  safe_mode and all safe_* caps are intentionally
    # removed from the effective runtime parameters.
    merged["safe_mode"] = False
    for key in (
        "safe_max_queries",
        "safe_max_pages_per_query",
        "safe_max_direct_urls_per_query",
        "safe_max_links_per_page",
        "safe_max_search_hits",
        "safe_max_chars_per_page",
    ):
        merged.pop(key, None)

    if not _apidoc_safe_bool(merged.get("direct_mode"), True):
        merged.setdefault("search_mode", "search_first")
        merged["search_fallback"] = True
    else:
        merged.setdefault("search_mode", "direct_first")
        merged.setdefault("search_fallback", False)

    merged.setdefault("crawl_direct_pages", False)
    merged.setdefault("max_pages_per_query", 8)
    merged.setdefault("max_direct_urls_per_query", 32)
    merged.setdefault("max_links_per_page", 160)
    merged.setdefault("max_search_hits", 20)
    merged.setdefault("max_chars_per_page", 24000)
    merged.setdefault("max_response_bytes", 5_000_000)
    merged.setdefault("max_queries", 100000)
    return merged


_apidoc_default_output_params = _apidoc_crashsafe_output_params

# ===========================================================================
# Built-in NumPy / SciPy APIDoc provider patch
# ---------------------------------------------------------------------------
# Added by GPT patch.
#
# What this fixes:
# - numpy.array -> official NumPy generated docs
# - numpy.linalg.solve -> official NumPy generated docs
# - scipy.optimize.minimize -> official SciPy generated docs
# - scipy.stats.entropy -> official SciPy generated docs
# - section headers like "NUMPY SHAPE / BROADCAST / INDEX DOCS"
# - blocks fake PyPI pages like pypi.org/project/numpy.array/
#
# Existing public block names and APIDocEngine signatures are preserved.
# This patch is deliberately installed at the end of the file so it composes
# with the existing advanced report renderer and crash-safe runtime patch.
# ===========================================================================

NUMPY_DOC_BASE = "https://numpy.org/doc/stable/reference/"
NUMPY_GENERATED_BASE = "https://numpy.org/doc/stable/reference/generated/"

SCIPY_DOC_BASE = "https://docs.scipy.org/doc/scipy/reference/"
SCIPY_GENERATED_BASE = "https://docs.scipy.org/doc/scipy/reference/generated/"


NUMPY_MODULE_DOCS = {
    "numpy": NUMPY_DOC_BASE,
    "numpy.ndarray": NUMPY_GENERATED_BASE + "numpy.ndarray.html",
    "numpy.linalg": NUMPY_DOC_BASE + "routines.linalg.html",
    "numpy.fft": NUMPY_DOC_BASE + "routines.fft.html",
    "numpy.random": NUMPY_DOC_BASE + "random/index.html",
    "numpy.ma": NUMPY_DOC_BASE + "maskedarray.html",
    "numpy.char": NUMPY_DOC_BASE + "routines.char.html",
    "numpy.polynomial": NUMPY_DOC_BASE + "routines.polynomials.html",
    "numpy.testing": NUMPY_DOC_BASE + "routines.testing.html",
}


NUMPY_TOPIC_DOCS = {
    "NUMPY CORE ARRAY DOCS / CALLS": NUMPY_DOC_BASE + "routines.array-creation.html",
    "NUMPY SHAPE / BROADCAST / INDEX DOCS": NUMPY_DOC_BASE + "routines.array-manipulation.html",
    "NUMPY ELEMENTWISE MATH DOCS": NUMPY_DOC_BASE + "routines.math.html",
    "NUMPY REDUCTION / SUMMARY DOCS": NUMPY_DOC_BASE + "routines.statistics.html",
    "NUMPY SORT / SEARCH DOCS": NUMPY_DOC_BASE + "routines.sort.html",
    "NUMPY LINEAR ALGEBRA DOCS": NUMPY_DOC_BASE + "routines.linalg.html",
    "NUMPY RANDOM / SIMULATION DOCS": NUMPY_DOC_BASE + "random/index.html",
    "NUMPY FFT DOCS": NUMPY_DOC_BASE + "routines.fft.html",
    "NUMPY DATA TYPE DOCS": NUMPY_DOC_BASE + "arrays.dtypes.html",
    "NUMPY BROADCASTING DOCS": NUMPY_DOC_BASE + "basics.broadcasting.html",
    "NUMPY INDEXING DOCS": NUMPY_DOC_BASE + "arrays.indexing.html",
    "NUMPY ARRAY MANIPULATION DOCS": NUMPY_DOC_BASE + "routines.array-manipulation.html",
    "NUMPY ARRAY CREATION DOCS": NUMPY_DOC_BASE + "routines.array-creation.html",
    "NUMPY STATISTICS DOCS": NUMPY_DOC_BASE + "routines.statistics.html",
    "NUMPY MATH DOCS": NUMPY_DOC_BASE + "routines.math.html",
}


SCIPY_MODULE_DOCS = {
    "scipy": SCIPY_DOC_BASE,
    "scipy.cluster": SCIPY_DOC_BASE + "cluster.html",
    "scipy.constants": SCIPY_DOC_BASE + "constants.html",
    "scipy.datasets": SCIPY_DOC_BASE + "datasets.html",
    "scipy.fft": SCIPY_DOC_BASE + "fft.html",
    "scipy.fftpack": SCIPY_DOC_BASE + "fftpack.html",
    "scipy.integrate": SCIPY_DOC_BASE + "integrate.html",
    "scipy.interpolate": SCIPY_DOC_BASE + "interpolate.html",
    "scipy.io": SCIPY_DOC_BASE + "io.html",
    "scipy.linalg": SCIPY_DOC_BASE + "linalg.html",
    "scipy.ndimage": SCIPY_DOC_BASE + "ndimage.html",
    "scipy.odr": SCIPY_DOC_BASE + "odr.html",
    "scipy.optimize": SCIPY_DOC_BASE + "optimize.html",
    "scipy.signal": SCIPY_DOC_BASE + "signal.html",
    "scipy.sparse": SCIPY_DOC_BASE + "sparse.html",
    "scipy.sparse.linalg": SCIPY_DOC_BASE + "sparse.linalg.html",
    "scipy.sparse.csgraph": SCIPY_DOC_BASE + "sparse.csgraph.html",
    "scipy.spatial": SCIPY_DOC_BASE + "spatial.html",
    "scipy.special": SCIPY_DOC_BASE + "special.html",
    "scipy.stats": SCIPY_DOC_BASE + "stats.html",
}


SCIPY_TOPIC_DOCS = {
    "SCIPY OPTIMIZATION DOCS / CALLS": SCIPY_DOC_BASE + "optimize.html",
    "SCIPY OPTIMIZATION DOCS": SCIPY_DOC_BASE + "optimize.html",
    "SCIPY SPARSE MATRIX DOCS": SCIPY_DOC_BASE + "sparse.html",
    "SCIPY SPARSE LINALG DOCS": SCIPY_DOC_BASE + "sparse.linalg.html",
    "SCIPY GRAPH DOCS": SCIPY_DOC_BASE + "sparse.csgraph.html",
    "SCIPY STATS SUMMARY DOCS": SCIPY_DOC_BASE + "stats.html",
    "SCIPY STATS TEST DOCS": SCIPY_DOC_BASE + "stats.html",
    "SCIPY DISTRIBUTION DOCS": SCIPY_DOC_BASE + "stats.html",
    "SCIPY SIGNAL DOCS": SCIPY_DOC_BASE + "signal.html",
    "SCIPY SPATIAL / DISTANCE DOCS": SCIPY_DOC_BASE + "spatial.html",
    "SCIPY SPATIAL DOCS": SCIPY_DOC_BASE + "spatial.html",
    "SCIPY DISTANCE DOCS": SCIPY_DOC_BASE + "spatial.distance.html",
    "SCIPY CLUSTER DOCS": SCIPY_DOC_BASE + "cluster.html",
    "SCIPY INTEGRATE / ODE DOCS": SCIPY_DOC_BASE + "integrate.html",
    "SCIPY INTEGRATE DOCS": SCIPY_DOC_BASE + "integrate.html",
    "SCIPY INTERPOLATE DOCS": SCIPY_DOC_BASE + "interpolate.html",
    "SCIPY LINALG DOCS": SCIPY_DOC_BASE + "linalg.html",
    "SCIPY SPECIAL FUNCTIONS DOCS": SCIPY_DOC_BASE + "special.html",
    "SCIPY SPECIAL DOCS": SCIPY_DOC_BASE + "special.html",
    "SCIPY FFT DOCS": SCIPY_DOC_BASE + "fft.html",
}


NUMPY_KEYWORD_DOCS = {
    "broadcast": [
        NUMPY_DOC_BASE + "basics.broadcasting.html",
        NUMPY_DOC_BASE + "routines.array-manipulation.html",
    ],
    "shape": [NUMPY_DOC_BASE + "routines.array-manipulation.html"],
    "reshape": [NUMPY_DOC_BASE + "routines.array-manipulation.html"],
    "index": [NUMPY_DOC_BASE + "arrays.indexing.html"],
    "slice": [NUMPY_DOC_BASE + "arrays.indexing.html"],
    "dtype": [NUMPY_DOC_BASE + "arrays.dtypes.html"],
    "data type": [NUMPY_DOC_BASE + "arrays.dtypes.html"],
    "array creation": [NUMPY_DOC_BASE + "routines.array-creation.html"],
    "zeros": [NUMPY_DOC_BASE + "routines.array-creation.html"],
    "ones": [NUMPY_DOC_BASE + "routines.array-creation.html"],
    "empty": [NUMPY_DOC_BASE + "routines.array-creation.html"],
    "linear": [NUMPY_DOC_BASE + "routines.linalg.html"],
    "linalg": [NUMPY_DOC_BASE + "routines.linalg.html"],
    "matrix": [NUMPY_DOC_BASE + "routines.linalg.html"],
    "fft": [NUMPY_DOC_BASE + "routines.fft.html"],
    "fourier": [NUMPY_DOC_BASE + "routines.fft.html"],
    "random": [NUMPY_DOC_BASE + "random/index.html"],
    "simulation": [NUMPY_DOC_BASE + "random/index.html"],
    "generator": [NUMPY_DOC_BASE + "random/index.html"],
    "statistics": [NUMPY_DOC_BASE + "routines.statistics.html"],
    "mean": [NUMPY_DOC_BASE + "routines.statistics.html"],
    "std": [NUMPY_DOC_BASE + "routines.statistics.html"],
    "variance": [NUMPY_DOC_BASE + "routines.statistics.html"],
    "math": [NUMPY_DOC_BASE + "routines.math.html"],
    "ufunc": [NUMPY_DOC_BASE + "routines.ufuncs.html"],
    "elementwise": [NUMPY_DOC_BASE + "routines.math.html"],
}


SCIPY_KEYWORD_DOCS = {
    "optimize": [SCIPY_DOC_BASE + "optimize.html"],
    "optimization": [SCIPY_DOC_BASE + "optimize.html"],
    "minimize": [SCIPY_DOC_BASE + "optimize.html"],
    "root": [SCIPY_DOC_BASE + "optimize.html"],
    "least_squares": [SCIPY_DOC_BASE + "optimize.html"],
    "curve_fit": [SCIPY_DOC_BASE + "optimize.html"],
    "stats": [SCIPY_DOC_BASE + "stats.html"],
    "statistics": [SCIPY_DOC_BASE + "stats.html"],
    "probability": [SCIPY_DOC_BASE + "stats.html"],
    "distribution": [SCIPY_DOC_BASE + "stats.html"],
    "bootstrap": [SCIPY_DOC_BASE + "stats.html"],
    "sparse": [SCIPY_DOC_BASE + "sparse.html"],
    "sparse.linalg": [SCIPY_DOC_BASE + "sparse.linalg.html"],
    "sparse linalg": [SCIPY_DOC_BASE + "sparse.linalg.html"],
    "graph": [SCIPY_DOC_BASE + "sparse.csgraph.html"],
    "csgraph": [SCIPY_DOC_BASE + "sparse.csgraph.html"],
    "shortest path": [SCIPY_DOC_BASE + "sparse.csgraph.html"],
    "signal": [SCIPY_DOC_BASE + "signal.html"],
    "filter": [SCIPY_DOC_BASE + "signal.html"],
    "spectrogram": [SCIPY_DOC_BASE + "signal.html"],
    "peak": [SCIPY_DOC_BASE + "signal.html"],
    "spatial": [SCIPY_DOC_BASE + "spatial.html"],
    "distance": [SCIPY_DOC_BASE + "spatial.distance.html"],
    "nearest": [SCIPY_DOC_BASE + "spatial.html"],
    "kdtree": [SCIPY_DOC_BASE + "spatial.html"],
    "cluster": [SCIPY_DOC_BASE + "cluster.html"],
    "hierarchy": [SCIPY_DOC_BASE + "cluster.hierarchy.html"],
    "kmeans": [SCIPY_DOC_BASE + "cluster.vq.html"],
    "integrate": [SCIPY_DOC_BASE + "integrate.html"],
    "ode": [SCIPY_DOC_BASE + "integrate.html"],
    "solve_ivp": [SCIPY_DOC_BASE + "integrate.html"],
    "interpolate": [SCIPY_DOC_BASE + "interpolate.html"],
    "spline": [SCIPY_DOC_BASE + "interpolate.html"],
    "linalg": [SCIPY_DOC_BASE + "linalg.html"],
    "linear algebra": [SCIPY_DOC_BASE + "linalg.html"],
    "special": [SCIPY_DOC_BASE + "special.html"],
    "softmax": [SCIPY_DOC_BASE + "special.html"],
    "logsumexp": [SCIPY_DOC_BASE + "special.html"],
    "fft": [SCIPY_DOC_BASE + "fft.html"],
    "fourier": [SCIPY_DOC_BASE + "fft.html"],
}


def _apidoc_clean_symbol_query(query: str) -> str:
    q = str(query or "").strip().strip("`").strip()
    q = re.sub(r"\s+", " ", q)
    return q


def _apidoc_is_numpy_symbol(query: str) -> bool:
    q = _apidoc_clean_symbol_query(query)
    return bool(re.fullmatch(r"numpy(?:\.[A-Za-z_][A-Za-z0-9_]*)+", q))


def _apidoc_is_scipy_symbol(query: str) -> bool:
    q = _apidoc_clean_symbol_query(query)
    return bool(re.fullmatch(r"scipy(?:\.[A-Za-z_][A-Za-z0-9_]*)+", q))


def _apidoc_is_section_header(query: str) -> bool:
    q = _apidoc_clean_symbol_query(query)
    if not q:
        return False
    if "/" in q:
        return True
    if q.upper() == q and any(ch.isalpha() for ch in q):
        return True
    if q.endswith("DOCS") or q.endswith("DOCS / CALLS"):
        return True
    return False


def _apidoc_should_block_pypi_fake_api(query: str) -> bool:
    q = _apidoc_clean_symbol_query(query)

    # Never use PyPI for API symbols.
    if q.startswith(("numpy.", "scipy.")):
        return True

    # These are your local tool wrapper names, not packages.
    if q.startswith("math_"):
        return True

    # Section headers should resolve through topic maps, not PyPI.
    if _apidoc_is_section_header(q):
        return True

    # Avoid PyPI guesses for long phrases.
    if " " in q and not re.fullmatch(r"[A-Za-z0-9_.-]+", q):
        return True

    return False


def _apidoc_generated_api_url(symbol: str, generated_base: str) -> str:
    return generated_base + symbol.strip().strip("`") + ".html"


def _apidoc_dedupe_urls(urls: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for raw in urls:
        u = str(raw or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _install_numpy_scipy_builtin_apidocs() -> None:
    """
    Install NumPy/SciPy as first-class APIDoc source providers.

    This mutates DEFAULT_CONFIG/PREFIX_PROFILE/PY_PACKAGE_DOCS at import time.
    It is safe to call repeatedly.
    """

    PREFIX_PROFILE["numpy"] = "scientific"
    PREFIX_PROFILE["scipy"] = "scientific"
    PREFIX_PROFILE["scientific"] = "scientific"
    PREFIX_PROFILE["science"] = "scientific"
    PREFIX_PROFILE["math"] = "scientific"

    DEFAULT_CONFIG.setdefault("profiles", {})
    DEFAULT_CONFIG.setdefault("sources", {})

    DEFAULT_CONFIG["profiles"]["scientific"] = [
        "numpy_direct",
        "scipy_direct",
        "python_package_common_direct",
        "python_direct",
    ]

    # Make NumPy/SciPy first in python-packages mode.
    py_pkg = list(DEFAULT_CONFIG["profiles"].get("python-packages", []))
    for src_key in ("numpy_direct", "scipy_direct"):
        if src_key in py_pkg:
            py_pkg.remove(src_key)
    DEFAULT_CONFIG["profiles"]["python-packages"] = [
        "numpy_direct",
        "scipy_direct",
    ] + py_pkg

    # Make NumPy/SciPy first in all mode.
    all_sources = list(DEFAULT_CONFIG["profiles"].get("all", []))
    for src_key in ("numpy_direct", "scipy_direct"):
        if src_key in all_sources:
            all_sources.remove(src_key)
    DEFAULT_CONFIG["profiles"]["all"] = [
        "numpy_direct",
        "scipy_direct",
    ] + all_sources

    DEFAULT_CONFIG["sources"]["numpy_direct"] = {
        "display_name": "NumPy Official API Docs Direct",
        "allowed_domains": ["numpy.org"],
        "resolver": "numpy",
        "profile": "scientific",
        "seed_urls": [
            NUMPY_DOC_BASE,
            NUMPY_DOC_BASE + "routines.html",
            NUMPY_DOC_BASE + "routines.array-creation.html",
            NUMPY_DOC_BASE + "routines.array-manipulation.html",
            NUMPY_DOC_BASE + "routines.math.html",
            NUMPY_DOC_BASE + "routines.statistics.html",
            NUMPY_DOC_BASE + "routines.linalg.html",
            NUMPY_DOC_BASE + "routines.fft.html",
            NUMPY_DOC_BASE + "random/index.html",
            NUMPY_GENERATED_BASE + "numpy.ndarray.html",
        ],
        "match_terms": [
            "numpy",
            "ndarray",
            "array",
            "broadcast",
            "shape",
            "index",
            "linalg",
            "fft",
            "random",
            "ufunc",
            "dtype",
        ],
        "same_host_crawl_only": True,
    }

    DEFAULT_CONFIG["sources"]["scipy_direct"] = {
        "display_name": "SciPy Official API Docs Direct",
        "allowed_domains": ["docs.scipy.org"],
        "resolver": "scipy",
        "profile": "scientific",
        "seed_urls": [
            SCIPY_DOC_BASE,
            SCIPY_DOC_BASE + "api.html",
            SCIPY_DOC_BASE + "optimize.html",
            SCIPY_DOC_BASE + "stats.html",
            SCIPY_DOC_BASE + "sparse.html",
            SCIPY_DOC_BASE + "sparse.linalg.html",
            SCIPY_DOC_BASE + "sparse.csgraph.html",
            SCIPY_DOC_BASE + "signal.html",
            SCIPY_DOC_BASE + "spatial.html",
            SCIPY_DOC_BASE + "cluster.html",
            SCIPY_DOC_BASE + "integrate.html",
            SCIPY_DOC_BASE + "interpolate.html",
            SCIPY_DOC_BASE + "linalg.html",
            SCIPY_DOC_BASE + "special.html",
            SCIPY_DOC_BASE + "fft.html",
        ],
        "match_terms": [
            "scipy",
            "optimize",
            "stats",
            "sparse",
            "signal",
            "spatial",
            "cluster",
            "integrate",
            "interpolate",
            "linalg",
            "special",
            "fft",
        ],
        "same_host_crawl_only": True,
    }

    # Upgrade the older common package source too.
    common = DEFAULT_CONFIG["sources"].get("python_package_common_direct", {})
    allowed = list(common.get("allowed_domains", []))
    for host in ("numpy.org", "docs.scipy.org"):
        if host not in allowed:
            allowed.append(host)
    common["allowed_domains"] = allowed

    terms = list(common.get("match_terms", []))
    for term in ("numpy", "scipy"):
        if term not in terms:
            terms.append(term)
    common["match_terms"] = terms
    DEFAULT_CONFIG["sources"]["python_package_common_direct"] = common

    PY_PACKAGE_DOCS["numpy"] = [
        NUMPY_DOC_BASE,
        NUMPY_DOC_BASE + "routines.html",
        NUMPY_GENERATED_BASE + "numpy.ndarray.html",
    ]

    PY_PACKAGE_DOCS["scipy"] = [
        SCIPY_DOC_BASE,
        SCIPY_DOC_BASE + "api.html",
        SCIPY_DOC_BASE + "optimize.html",
        SCIPY_DOC_BASE + "stats.html",
        SCIPY_DOC_BASE + "sparse.html",
        SCIPY_DOC_BASE + "signal.html",
    ]


def _apidoc_engine_resolve_numpy(self, job, key, src):
    q = _apidoc_clean_symbol_query(str(job.get("query") or ""))
    low = q.lower()

    # Do not let local GPT tool-wrapper names resolve to NumPy docs.
    if low.startswith("math_"):
        return []

    # NumPy direct resolver should only answer NumPy symbols, NumPy package
    # requests, or explicit NumPy topic headers.
    if (
        "numpy" not in low
        and "ndarray" not in low
        and q not in NUMPY_TOPIC_DOCS
        and not any(topic.lower() in low for topic in NUMPY_TOPIC_DOCS)
    ):
        return []

    urls: List[str] = []

    # Exact section/topic headers.
    for topic, url in NUMPY_TOPIC_DOCS.items():
        topic_low = topic.lower()
        if q == topic or topic_low in low:
            urls.append(url)

    # Exact module docs.
    if q in NUMPY_MODULE_DOCS:
        urls.append(NUMPY_MODULE_DOCS[q])

    # Dotted API symbol docs.
    if _apidoc_is_numpy_symbol(q):
        if q in NUMPY_MODULE_DOCS:
            urls.append(NUMPY_MODULE_DOCS[q])
        else:
            urls.append(_apidoc_generated_api_url(q, NUMPY_GENERATED_BASE))

    # Keyword fallback for broader requests.
    for needle, mapped_urls in NUMPY_KEYWORD_DOCS.items():
        if needle in low:
            urls.extend(mapped_urls)

    # General package request.
    if not urls and ("numpy" in low or "ndarray" in low):
        urls.extend(src.get("seed_urls", []))

    return [
        self.hit(job, key, src, u, u, score(q, "", u) + 80, "direct_numpy_official")
        for u in _apidoc_dedupe_urls(urls)
    ][:self.max_direct]


def _apidoc_engine_resolve_scipy(self, job, key, src):
    q = _apidoc_clean_symbol_query(str(job.get("query") or ""))
    low = q.lower()

    # Do not let local GPT tool-wrapper names resolve to SciPy docs.
    if low.startswith("math_"):
        return []

    # SciPy direct resolver should only answer SciPy symbols, SciPy package
    # requests, or explicit SciPy topic headers.
    if (
        "scipy" not in low
        and q not in SCIPY_TOPIC_DOCS
        and not any(topic.lower() in low for topic in SCIPY_TOPIC_DOCS)
    ):
        return []

    urls: List[str] = []

    # Exact section/topic headers.
    for topic, url in SCIPY_TOPIC_DOCS.items():
        topic_low = topic.lower()
        if q == topic or topic_low in low:
            urls.append(url)

    # Exact module docs.
    if q in SCIPY_MODULE_DOCS:
        urls.append(SCIPY_MODULE_DOCS[q])

    # Dotted API symbol docs.
    if _apidoc_is_scipy_symbol(q):
        if q in SCIPY_MODULE_DOCS:
            urls.append(SCIPY_MODULE_DOCS[q])
        else:
            urls.append(_apidoc_generated_api_url(q, SCIPY_GENERATED_BASE))

    # Keyword fallback for broader requests.
    for needle, mapped_urls in SCIPY_KEYWORD_DOCS.items():
        if needle in low:
            urls.extend(mapped_urls)

    # General package request.
    if not urls and "scipy" in low:
        urls.extend(src.get("seed_urls", []))

    return [
        self.hit(job, key, src, u, u, score(q, "", u) + 80, "direct_scipy_official")
        for u in _apidoc_dedupe_urls(urls)
    ][:self.max_direct]


def _apidoc_engine_resolve_python_package_common_scientific(self, job, key, src):
    """
    Replacement for resolve_python_package_common.

    Keeps the old PY_PACKAGE_DOCS behavior but knows that:
    - numpy.* should be official NumPy generated docs
    - scipy.* should be official SciPy generated docs
    """
    q = _apidoc_clean_symbol_query(str(job.get("query") or ""))
    if q.lower().startswith("math_"):
        return []

    pkg = str(job.get("package") or self.guess_package(q)).lower()
    urls: List[str] = []

    if _apidoc_is_numpy_symbol(q):
        if q in NUMPY_MODULE_DOCS:
            urls.append(NUMPY_MODULE_DOCS[q])
        else:
            urls.append(_apidoc_generated_api_url(q, NUMPY_GENERATED_BASE))

    if _apidoc_is_scipy_symbol(q):
        if q in SCIPY_MODULE_DOCS:
            urls.append(SCIPY_MODULE_DOCS[q])
        else:
            urls.append(_apidoc_generated_api_url(q, SCIPY_GENERATED_BASE))

    for name, mapped in PY_PACKAGE_DOCS.items():
        if name in q.lower() or name == pkg:
            urls.extend(mapped)

    return [
        self.hit(job, key, src, u, u, score(q, "", u) + 30, "direct_package_known")
        for u in _apidoc_dedupe_urls(urls)
    ][:self.max_direct]


def _patch_apidoc_engine_numpy_scipy() -> None:
    """
    Monkey-patch APIDocEngine without changing existing public signatures.

    This is safer than rewriting the entire huge file because it preserves every
    block and every resolver already in your file, then inserts NumPy/SciPy first.
    """

    _install_numpy_scipy_builtin_apidocs()

    if getattr(APIDocEngine, "_numpy_scipy_builtin_patch_applied", False):
        return

    old_resolve_direct = APIDocEngine.resolve_direct
    old_resolve_pypi = APIDocEngine.resolve_pypi

    def resolve_direct_with_numpy_scipy(self, job, key, src):
        resolver = str(src.get("resolver") or "")

        if resolver == "numpy":
            return self.resolve_numpy(job, key, src)

        if resolver == "scipy":
            return self.resolve_scipy(job, key, src)

        return old_resolve_direct(self, job, key, src)

    def resolve_pypi_without_fake_api_symbols(self, job, key, src):
        q = str(job.get("query") or "")

        if _apidoc_should_block_pypi_fake_api(q):
            return []

        return old_resolve_pypi(self, job, key, src)

    APIDocEngine.resolve_numpy = _apidoc_engine_resolve_numpy
    APIDocEngine.resolve_scipy = _apidoc_engine_resolve_scipy
    APIDocEngine.resolve_python_package_common = _apidoc_engine_resolve_python_package_common_scientific
    APIDocEngine.resolve_direct = resolve_direct_with_numpy_scipy
    APIDocEngine.resolve_pypi = resolve_pypi_without_fake_api_symbols
    APIDocEngine._numpy_scipy_builtin_patch_applied = True


_patch_apidoc_engine_numpy_scipy()
# ===========================================================================
# Source-code APIDoc need decipher layer
# ===========================================================================
# This layer is additive and safe to drop at the bottom of the existing
# apidoc_block.py file.  It adds blocks that inspect real source files/code and
# turn the actual imports/includes/usings/API calls into direct APIDoc requests.
# Supported source families include C/C++ (.c/.cc/.cpp/.cxx/.h/.hh/.hpp/.hxx),
# Python (.py/.pyi), C# (.cs/.csproj), JavaScript/TypeScript, Java, Rust, Go,
# and generic text snippets.

SOURCE_APIDOC_INTEL_VERSION = "2026.06.03-source-code-apidoc-decipher-v1"

SOURCE_CODE_EXTENSIONS: Set[str] = {
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inl",
    ".py", ".pyi", ".cs", ".csproj", ".vb",
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".kts", ".rs", ".go", ".swift",
    ".xml", ".json", ".toml", ".yaml", ".yml", ".md", ".txt",
}

EXT_LANGUAGE_MAP: Dict[str, str] = {
    ".c": "cpp", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".h": "cpp", ".hh": "cpp", ".hpp": "cpp", ".hxx": "cpp", ".inl": "cpp",
    ".py": "python", ".pyi": "python",
    ".cs": "csharp", ".csproj": "csharp", ".vb": "dotnet",
    ".js": "javascript", ".jsx": "javascript", ".ts": "javascript", ".tsx": "javascript", ".mjs": "node", ".cjs": "node",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".rs": "rust", ".go": "go", ".swift": "swift",
    ".xml": "xml", ".json": "json", ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".md": "text", ".txt": "text",
}

CPP_INCLUDE_DOCS: Dict[str, List[str]] = {
    "vector": ["https://en.cppreference.com/w/cpp/container/vector"],
    "array": ["https://en.cppreference.com/w/cpp/container/array"],
    "deque": ["https://en.cppreference.com/w/cpp/container/deque"],
    "list": ["https://en.cppreference.com/w/cpp/container/list"],
    "map": ["https://en.cppreference.com/w/cpp/container/map"],
    "unordered_map": ["https://en.cppreference.com/w/cpp/container/unordered_map"],
    "set": ["https://en.cppreference.com/w/cpp/container/set"],
    "unordered_set": ["https://en.cppreference.com/w/cpp/container/unordered_set"],
    "string": ["https://en.cppreference.com/w/cpp/string/basic_string"],
    "string_view": ["https://en.cppreference.com/w/cpp/string/basic_string_view"],
    "memory": ["https://en.cppreference.com/w/cpp/memory", "https://learn.microsoft.com/en-us/cpp/standard-library/memory?view=msvc-170"],
    "thread": ["https://en.cppreference.com/w/cpp/thread/thread", "https://learn.microsoft.com/en-us/cpp/standard-library/thread-class?view=msvc-170"],
    "mutex": ["https://en.cppreference.com/w/cpp/thread/mutex"],
    "shared_mutex": ["https://en.cppreference.com/w/cpp/thread/shared_mutex"],
    "atomic": ["https://en.cppreference.com/w/cpp/atomic/atomic", "https://en.cppreference.com/w/cpp/atomic/memory_order"],
    "future": ["https://en.cppreference.com/w/cpp/thread/future"],
    "condition_variable": ["https://en.cppreference.com/w/cpp/thread/condition_variable"],
    "chrono": ["https://en.cppreference.com/w/cpp/chrono"],
    "filesystem": ["https://en.cppreference.com/w/cpp/filesystem"],
    "algorithm": ["https://en.cppreference.com/w/cpp/algorithm"],
    "numeric": ["https://en.cppreference.com/w/cpp/numeric"],
    "optional": ["https://en.cppreference.com/w/cpp/utility/optional"],
    "variant": ["https://en.cppreference.com/w/cpp/utility/variant"],
    "tuple": ["https://en.cppreference.com/w/cpp/utility/tuple"],
    "span": ["https://en.cppreference.com/w/cpp/container/span"],
    "ranges": ["https://en.cppreference.com/w/cpp/ranges"],
    "cstdint": ["https://en.cppreference.com/w/cpp/types/integer"],
    "stdint.h": ["https://en.cppreference.com/w/cpp/types/integer"],
    "bit": ["https://en.cppreference.com/w/cpp/types/endian", "https://en.cppreference.com/w/cpp/utility/bit_cast"],
    "exception": ["https://en.cppreference.com/w/cpp/error/exception"],
    "stdexcept": ["https://en.cppreference.com/w/cpp/error/exception"],
    "iostream": ["https://en.cppreference.com/w/cpp/io"],
    "fstream": ["https://en.cppreference.com/w/cpp/io/basic_fstream"],
    "sstream": ["https://en.cppreference.com/w/cpp/io/basic_stringstream"],
    "windows.h": ["https://learn.microsoft.com/en-us/windows/win32/api/"],
    "libloaderapi.h": ["https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/"],
    "memoryapi.h": ["https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/"],
    "processthreadsapi.h": ["https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/"],
    "profileapi.h": ["https://learn.microsoft.com/en-us/windows/win32/api/profileapi/"],
    "securitybaseapi.h": ["https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/"],
    "winnt.h": ["https://learn.microsoft.com/en-us/windows/win32/api/winnt/"],
    "winsock2.h": ["https://learn.microsoft.com/en-us/windows/win32/winsock/windows-sockets-start-page-2"],
}

WIN32_SYMBOL_DOCS: Dict[str, str] = {
    "LoadLibraryA": "https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibrarya",
    "LoadLibraryW": "https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryw",
    "LoadLibraryExA": "https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryexa",
    "LoadLibraryExW": "https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryexw",
    "GetProcAddress": "https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-getprocaddress",
    "FreeLibrary": "https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-freelibrary",
    "VirtualAlloc": "https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualalloc",
    "VirtualFree": "https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualfree",
    "VirtualProtect": "https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-virtualprotect",
    "AdjustTokenPrivileges": "https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-adjusttokenprivileges",
    "OpenProcessToken": "https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocesstoken",
    "LookupPrivilegeValueA": "https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-lookupprivilegevaluea",
    "LookupPrivilegeValueW": "https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-lookupprivilegevaluew",
    "SetThreadAffinityMask": "https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadaffinitymask",
    "SetThreadIdealProcessor": "https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setthreadidealprocessor",
    "GetCurrentThread": "https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getcurrentthread",
    "QueryPerformanceCounter": "https://learn.microsoft.com/en-us/windows/win32/api/profileapi/nf-profileapi-queryperformancecounter",
    "QueryPerformanceFrequency": "https://learn.microsoft.com/en-us/windows/win32/api/profileapi/nf-profileapi-queryperformancefrequency",
    "InterlockedCompareExchange64": "https://learn.microsoft.com/en-us/windows/win32/api/winnt/nf-winnt-interlockedcompareexchange64",
    "GetNumaHighestNodeNumber": "https://learn.microsoft.com/en-us/windows/win32/api/systemtopologyapi/nf-systemtopologyapi-getnumahighestnodeNumber",
    "GetNumaNodeProcessorMaskEx": "https://learn.microsoft.com/en-us/windows/win32/api/systemtopologyapi/nf-systemtopologyapi-getnumanodeprocessormaskex",
}

PY_STDLIB_DOCS: Dict[str, str] = {
    "asyncio": "https://docs.python.org/3/library/asyncio.html",
    "ctypes": "https://docs.python.org/3/library/ctypes.html",
    "struct": "https://docs.python.org/3/library/struct.html",
    "threading": "https://docs.python.org/3/library/threading.html",
    "multiprocessing": "https://docs.python.org/3/library/multiprocessing.html",
    "concurrent": "https://docs.python.org/3/library/concurrent.futures.html",
    "pathlib": "https://docs.python.org/3/library/pathlib.html",
    "heapq": "https://docs.python.org/3/library/heapq.html",
    "json": "https://docs.python.org/3/library/json.html",
    "logging": "https://docs.python.org/3/library/logging.html",
    "requests": "https://requests.readthedocs.io/en/latest/api/",
    "bs4": "https://www.crummy.com/software/BeautifulSoup/bs4/doc/",
    "BeautifulSoup": "https://www.crummy.com/software/BeautifulSoup/bs4/doc/",
    "re": "https://docs.python.org/3/library/re.html",
    "os": "https://docs.python.org/3/library/os.html",
    "sys": "https://docs.python.org/3/library/sys.html",
    "time": "https://docs.python.org/3/library/time.html",
    "datetime": "https://docs.python.org/3/library/datetime.html",
    "socket": "https://docs.python.org/3/library/socket.html",
    "ssl": "https://docs.python.org/3/library/ssl.html",
    "hashlib": "https://docs.python.org/3/library/hashlib.html",
    "dataclasses": "https://docs.python.org/3/library/dataclasses.html",
    "typing": "https://docs.python.org/3/library/typing.html",
    "urllib": "https://docs.python.org/3/library/urllib.html",
}

CSHARP_DOCS: Dict[str, str] = {
    "System": "https://learn.microsoft.com/en-us/dotnet/api/system?view=net-9.0",
    "System.IO": "https://learn.microsoft.com/en-us/dotnet/api/system.io?view=net-9.0",
    "System.Net": "https://learn.microsoft.com/en-us/dotnet/api/system.net?view=net-9.0",
    "System.Net.Http": "https://learn.microsoft.com/en-us/dotnet/api/system.net.http?view=net-9.0",
    "System.Threading": "https://learn.microsoft.com/en-us/dotnet/api/system.threading?view=net-9.0",
    "System.Threading.Tasks": "https://learn.microsoft.com/en-us/dotnet/api/system.threading.tasks?view=net-9.0",
    "System.Runtime.InteropServices": "https://learn.microsoft.com/en-us/dotnet/api/system.runtime.interopservices?view=net-9.0",
    "System.Collections.Generic": "https://learn.microsoft.com/en-us/dotnet/api/system.collections.generic?view=net-9.0",
    "Microsoft.Win32": "https://learn.microsoft.com/en-us/dotnet/api/microsoft.win32?view=net-9.0",
    "TaleWorlds.Core": "https://bannerlordapi.butr.link/api/core/TaleWorlds.Core.html",
    "TaleWorlds.Engine": "https://bannerlordapi.butr.link/api/core/TaleWorlds.Engine.html",
    "TaleWorlds.MountAndBlade": "https://bannerlordapi.butr.link/api/core/TaleWorlds.MountAndBlade.html",
    "TaleWorlds.CampaignSystem": "https://bannerlordapi.butr.link/api/native/TaleWorlds.CampaignSystem.html",
    "TaleWorlds.Library": "https://bannerlordapi.butr.link/api/core/TaleWorlds.Library.html",
}

RANDOMX_DOC_URLS: List[str] = [
    "https://raw.githubusercontent.com/tevador/RandomX/master/src/randomx.h",
    "https://raw.githubusercontent.com/tevador/RandomX/master/doc/specs.md",
    "https://raw.githubusercontent.com/tevador/RandomX/master/doc/design.md",
]

RANDOMX_SYMBOL_DOCS: Dict[str, List[str]] = {
    "randomx_calculate_hash": RANDOMX_DOC_URLS,
    "randomx_calculate_hash_first": RANDOMX_DOC_URLS,
    "randomx_calculate_hash_next": RANDOMX_DOC_URLS,
    "randomx_alloc_cache": RANDOMX_DOC_URLS,
    "randomx_init_cache": RANDOMX_DOC_URLS,
    "randomx_release_cache": RANDOMX_DOC_URLS,
    "randomx_alloc_dataset": RANDOMX_DOC_URLS,
    "randomx_init_dataset": RANDOMX_DOC_URLS,
    "randomx_dataset_item_count": RANDOMX_DOC_URLS,
    "randomx_release_dataset": RANDOMX_DOC_URLS,
    "randomx_create_vm": RANDOMX_DOC_URLS,
    "randomx_destroy_vm": RANDOMX_DOC_URLS,
    "randomx_vm_set_cache": RANDOMX_DOC_URLS,
    "randomx_vm_set_dataset": RANDOMX_DOC_URLS,
    "RANDOMX_FLAG_LARGE_PAGES": RANDOMX_DOC_URLS,
    "RANDOMX_FLAG_HARD_AES": RANDOMX_DOC_URLS,
    "RANDOMX_FLAG_FULL_MEM": RANDOMX_DOC_URLS,
    "RANDOMX_FLAG_JIT": RANDOMX_DOC_URLS,
    "RANDOMX_FLAG_SECURE": RANDOMX_DOC_URLS,
}

MONERO_DIRECT_DOCS: Dict[str, str] = {
    "get_block_template": "https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#get_block_template",
    "submit_block": "https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#submit_block",
    "get_miner_data": "https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#get_miner_data",
    "calc_pow": "https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#calc_pow",
    "blocktemplate_blob": "https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#get_block_template",
    "blockhashing_blob": "https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#get_block_template",
    "seed_hash": "https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#get_block_template",
    "difficulty": "https://www.getmonero.org/resources/developer-guides/daemon-rpc.html#get_block_template",
}

P2POOL_SOURCE_DOCS: Dict[str, str] = {
    "StratumServer": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/stratum_server.cpp",
    "stratum_server": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/stratum_server.cpp",
    "BlockTemplate": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/block_template.cpp",
    "block_template": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/block_template.cpp",
    "PoolBlock": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/pool_block.cpp",
    "pool_block": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/pool_block.cpp",
    "SideChain": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/side_chain.cpp",
    "side_chain": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/side_chain.cpp",
    "RandomX_Hasher": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/randomx_hasher.cpp",
    "randomx_hasher": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/randomx_hasher.cpp",
    "Miner": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/miner.cpp",
    "miner": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/miner.cpp",
    "difficulty_type": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/difficulty_type.h",
    "pow_hash": "https://raw.githubusercontent.com/SChernykh/p2pool/master/src/pow_hash.cpp",
    "full-validation": "https://raw.githubusercontent.com/SChernykh/p2pool/master/docs/COMMAND_LINE.MD",
    "mini": "https://raw.githubusercontent.com/SChernykh/p2pool/master/README.md",
}

XMRIG_SOURCE_DOCS: Dict[str, str] = {
    "Job": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/base/net/stratum/Job.cpp",
    "Client": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/base/net/stratum/Client.cpp",
    "Pool": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/base/net/stratum/Pool.cpp",
    "SubmitResult": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/base/net/stratum/SubmitResult.h",
    "Nonce": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/crypto/common/Nonce.h",
    "VirtualMemory": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/crypto/common/VirtualMemory.cpp",
    "Rx": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/crypto/rx/Rx.cpp",
    "RxVm": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/crypto/rx/RxVm.cpp",
    "RxDataset": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/crypto/rx/RxDataset.cpp",
    "RxConfig": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/crypto/rx/RxConfig.cpp",
    "CpuWorker": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/backend/cpu/CpuWorker.cpp",
    "CpuBackend": "https://raw.githubusercontent.com/xmrig/xmrig/master/src/backend/cpu/CpuBackend.cpp",
    "hugepages": "https://xmrig.com/docs/miner/hugepages",
    "randomx": "https://xmrig.com/docs/miner/randomx-optimization-guide",
    "config": "https://xmrig.com/docs/miner/config",
}

GENERIC_DOC_HINTS: Dict[str, List[str]] = {
    "std::atomic": ["https://en.cppreference.com/w/cpp/atomic/atomic", "https://en.cppreference.com/w/cpp/atomic/memory_order"],
    "std::memory_order": ["https://en.cppreference.com/w/cpp/atomic/memory_order"],
    "std::vector": ["https://en.cppreference.com/w/cpp/container/vector", "https://en.cppreference.com/w/cpp/container/vector/reserve", "https://en.cppreference.com/w/cpp/container/vector/push_back"],
    "std::thread": ["https://en.cppreference.com/w/cpp/thread/thread", "https://en.cppreference.com/w/cpp/thread/thread/join"],
    "uint32_t": ["https://en.cppreference.com/w/cpp/types/integer"],
    "uint64_t": ["https://en.cppreference.com/w/cpp/types/integer"],
    "uintptr_t": ["https://en.cppreference.com/w/cpp/types/integer"],
    "std::endian": ["https://en.cppreference.com/w/cpp/types/endian"],
}

SOURCE_LOCAL_WORDS: Set[str] = {
    "if", "else", "for", "while", "switch", "case", "return", "break", "continue", "try", "catch", "finally",
    "class", "struct", "enum", "namespace", "using", "include", "define", "public", "private", "protected",
    "static", "const", "constexpr", "volatile", "inline", "virtual", "override", "new", "delete", "this", "self",
    "true", "false", "null", "nullptr", "None", "True", "False", "var", "let", "const", "function", "def", "import", "from",
}


def _source_apidoc_norm_ext(path_or_name: str) -> str:
    try:
        return Path(str(path_or_name)).suffix.lower()
    except Exception:
        return ""


def _source_apidoc_language_for(path_or_name: str, text: str = "") -> str:
    ext = _source_apidoc_norm_ext(path_or_name)
    if ext in EXT_LANGUAGE_MAP:
        return EXT_LANGUAGE_MAP[ext]
    s = text[:4096]
    if "#include" in s or "std::" in s or "namespace " in s:
        return "cpp"
    if "using System" in s or "namespace " in s and ";" in s and "class " in s:
        return "csharp"
    if re.search(r"^\s*(import|from)\s+[A-Za-z0-9_.]+", s, re.M):
        return "python"
    return "text"


def _source_apidoc_dedupe(items: List[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for item in items:
        s = str(item or "").strip().strip("'\"")
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _source_apidoc_add_need(needs: List[Dict[str, Any]], *, value: str, kind: str, reason: str, language: str, source_file: str, confidence: float = 0.80) -> None:
    v = str(value or "").strip()
    if not v:
        return
    needs.append({
        "value": v,
        "kind": kind,
        "reason": reason,
        "language": language,
        "source_file": source_file,
        "confidence": round(float(confidence), 3),
    })


def _source_apidoc_plain_text_from_payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, (list, tuple)):
        return "\n".join(str(x) for x in payload)
    if isinstance(payload, dict):
        for key in ("code", "text", "source", "content", "payload"):
            if isinstance(payload.get(key), str):
                return str(payload.get(key) or "")
        if isinstance(payload.get("queries"), list):
            return "\n".join(str(x) for x in payload.get("queries") or [])
    return str(payload)


@dataclass
class APIDocSourceUnit:
    path: str
    name: str
    ext: str
    language: str
    text: str
    size: int


class APIDocSourceNeedAnalyzer:
    def __init__(self, params: Optional[Dict[str, Any]] = None):
        self.params = dict(params or {})
        self.max_files = as_int(self.params.get("max_files"), 80, 1, 1000)
        self.max_bytes_per_file = as_int(self.params.get("max_bytes_per_file"), 320_000, 4096, 10_000_000)
        self.max_symbols = as_int(self.params.get("max_symbols"), 220, 10, 5000)
        self.max_queries = _apidoc_query_limit(self.params, default=100000, hard_max=1000000)
        self.prefer_direct_urls = as_bool(self.params.get("prefer_direct_urls"), True)
        self.include_stdlib = as_bool(self.params.get("include_stdlib"), True)
        self.include_generic_symbols = as_bool(self.params.get("include_generic_symbols"), True)

    def read_units(self, payload: Any) -> List[APIDocSourceUnit]:
        units: List[APIDocSourceUnit] = []
        paths: List[str] = []
        inline_items: List[Tuple[str, str]] = []

        if isinstance(payload, dict):
            for key in ("path", "file", "source_path", "code_file"):
                if payload.get(key):
                    paths.append(str(payload.get(key)))
            for key in ("paths", "files", "source_paths", "code_files"):
                val = payload.get(key)
                if isinstance(val, (list, tuple)):
                    paths.extend(str(x) for x in val)
            for key in ("code", "text", "source", "content"):
                if isinstance(payload.get(key), str) and str(payload.get(key)).strip():
                    inline_items.append((str(payload.get("name") or "inline_source"), str(payload.get(key))))
        elif isinstance(payload, (list, tuple)):
            for item in payload:
                if isinstance(item, dict):
                    units.extend(self.read_units(item))
                else:
                    s = str(item)
                    if self._looks_like_existing_path(s):
                        paths.append(s)
                    else:
                        inline_items.append(("inline_source", s))
        elif isinstance(payload, str):
            raw = payload.strip()
            if self._looks_like_existing_path(raw):
                paths.append(raw)
            else:
                possible_lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
                existing = [ln for ln in possible_lines if self._looks_like_existing_path(ln)]
                if existing and len(existing) >= max(1, len(possible_lines) // 2):
                    paths.extend(existing)
                else:
                    inline_items.append(("inline_source", payload))

        for p in _source_apidoc_dedupe(paths)[:self.max_files]:
            units.extend(self._read_path_units(p))
            if len(units) >= self.max_files:
                break

        for name, text in inline_items:
            if len(units) >= self.max_files:
                break
            t = text[: self.max_bytes_per_file]
            ext = _source_apidoc_norm_ext(name)
            lang = _source_apidoc_language_for(name, t)
            units.append(APIDocSourceUnit(path="", name=name, ext=ext, language=lang, text=t, size=len(t.encode("utf-8", "ignore"))))

        return units[: self.max_files]

    def _looks_like_existing_path(self, value: str) -> bool:
        try:
            p = Path(str(value).strip().strip("'\""))
            return p.exists()
        except Exception:
            return False

    def _read_path_units(self, value: str) -> List[APIDocSourceUnit]:
        out: List[APIDocSourceUnit] = []
        try:
            p = Path(str(value).strip().strip("'\""))
            if p.is_dir():
                for child in sorted(p.rglob("*")):
                    if len(out) >= self.max_files:
                        break
                    if child.is_file() and child.suffix.lower() in SOURCE_CODE_EXTENSIONS:
                        unit = self._read_file_unit(child)
                        if unit:
                            out.append(unit)
            elif p.is_file():
                unit = self._read_file_unit(p)
                if unit:
                    out.append(unit)
        except Exception:
            pass
        return out

    def _read_file_unit(self, path: Path) -> Optional[APIDocSourceUnit]:
        try:
            if path.stat().st_size > self.max_bytes_per_file:
                data = path.read_bytes()[: self.max_bytes_per_file]
                text = data.decode("utf-8", "replace")
            else:
                text = path.read_text(encoding="utf-8", errors="replace")
            ext = path.suffix.lower()
            return APIDocSourceUnit(path=str(path), name=path.name, ext=ext, language=_source_apidoc_language_for(str(path), text), text=text, size=len(text.encode("utf-8", "ignore")))
        except Exception:
            return None

    def analyze(self, payload: Any) -> Dict[str, Any]:
        units = self.read_units(payload)
        files: List[Dict[str, Any]] = []
        all_needs: List[Dict[str, Any]] = []
        all_symbols: Dict[str, int] = {}

        for unit in units:
            result = self.analyze_unit(unit)
            files.append(result)
            all_needs.extend(result.get("needs", []))
            for sym in result.get("symbols", []):
                all_symbols[sym] = all_symbols.get(sym, 0) + 1

        needs = self._dedupe_needs(all_needs)
        queries = self.needs_to_queries(needs)
        direct_urls = [q for q in queries if q.startswith("http://") or q.startswith("https://")]
        text_queries = [q for q in queries if q not in direct_urls]
        return {
            "version": SOURCE_APIDOC_INTEL_VERSION,
            "files": files,
            "file_count": len(files),
            "needs": needs,
            "need_count": len(needs),
            "queries": queries,
            "query_count": len(queries),
            "direct_urls": direct_urls,
            "text_queries": text_queries,
            "symbols": sorted(all_symbols.items(), key=lambda kv: (-kv[1], kv[0]))[: self.max_symbols],
            "languages": sorted({f.get("language", "") for f in files if f.get("language")}),
        }

    def analyze_unit(self, unit: APIDocSourceUnit) -> Dict[str, Any]:
        text = unit.text or ""
        language = unit.language
        needs: List[Dict[str, Any]] = []
        symbols: Set[str] = set()

        if language == "cpp":
            self._analyze_cpp(unit, needs, symbols)
        elif language == "python":
            self._analyze_python(unit, needs, symbols)
        elif language in ("csharp", "dotnet"):
            self._analyze_csharp(unit, needs, symbols)
        else:
            self._analyze_generic(unit, needs, symbols)

        return {
            "path": unit.path,
            "name": unit.name,
            "ext": unit.ext,
            "language": language,
            "size": unit.size,
            "symbols": sorted(symbols)[: self.max_symbols],
            "needs": self._dedupe_needs(needs),
        }

    def _analyze_cpp(self, unit: APIDocSourceUnit, needs: List[Dict[str, Any]], symbols: Set[str]) -> None:
        text = unit.text
        for raw in re.findall(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", text, flags=re.M):
            inc = raw.strip()
            symbols.add(inc)
            key = inc.split("/")[-1]
            for url in CPP_INCLUDE_DOCS.get(key, []):
                _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"C/C++ include <{inc}>", language=unit.language, source_file=unit.name, confidence=0.92)
            if not CPP_INCLUDE_DOCS.get(key) and self.include_generic_symbols:
                _source_apidoc_add_need(needs, value=f"cppreference_direct: {inc} C++ header API reference", kind="query", reason=f"Unmapped C/C++ include <{inc}>", language=unit.language, source_file=unit.name, confidence=0.55)

        for sym in re.findall(r"\bstd::[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)?", text):
            symbols.add(sym)
            base = "::".join(sym.split("::")[:2])
            for url in GENERIC_DOC_HINTS.get(base, []):
                _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"C++ standard symbol {sym}", language=unit.language, source_file=unit.name, confidence=0.88)

        for sym, url in WIN32_SYMBOL_DOCS.items():
            if re.search(rf"\b{re.escape(sym)}\b", text):
                symbols.add(sym)
                _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"Win32 API symbol {sym}", language=unit.language, source_file=unit.name, confidence=0.94)

        for sym, urls in RANDOMX_SYMBOL_DOCS.items():
            if re.search(rf"\b{re.escape(sym)}\b", text):
                symbols.add(sym)
                for url in urls:
                    _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"RandomX C API symbol {sym}", language=unit.language, source_file=unit.name, confidence=0.96)

        for sym, url in {**P2POOL_SOURCE_DOCS, **XMRIG_SOURCE_DOCS, **MONERO_DIRECT_DOCS}.items():
            if sym and re.search(rf"\b{re.escape(sym)}\b", text, flags=re.I):
                symbols.add(sym)
                _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"Domain symbol/reference {sym}", language=unit.language, source_file=unit.name, confidence=0.86)

        for sym in re.findall(r"\b(?:uint32_t|uint64_t|uintptr_t|size_t|DWORD|HANDLE|HMODULE|FARPROC|BOOL|LARGE_INTEGER)\b", text):
            symbols.add(sym)
            if sym in GENERIC_DOC_HINTS:
                for url in GENERIC_DOC_HINTS[sym]:
                    _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"Native ABI/type symbol {sym}", language=unit.language, source_file=unit.name, confidence=0.80)
            elif sym in ("DWORD", "HANDLE", "HMODULE", "FARPROC", "BOOL", "LARGE_INTEGER"):
                _source_apidoc_add_need(needs, value="https://learn.microsoft.com/en-us/windows/win32/winprog/windows-data-types", kind="direct_url", reason=f"Win32 data type {sym}", language=unit.language, source_file=unit.name, confidence=0.82)

    def _analyze_python(self, unit: APIDocSourceUnit, needs: List[Dict[str, Any]], symbols: Set[str]) -> None:
        text = unit.text
        modules: Set[str] = set()
        for mod in re.findall(r"^\s*import\s+([A-Za-z_][A-Za-z0-9_\.]*)(?:\s+as\s+[A-Za-z_][A-Za-z0-9_]*)?", text, flags=re.M):
            modules.add(mod.split(".")[0])
        for mod in re.findall(r"^\s*from\s+([A-Za-z_][A-Za-z0-9_\.]*|\.)\s+import\s+", text, flags=re.M):
            if not mod.startswith("."):
                modules.add(mod.split(".")[0])
        for mod in sorted(modules):
            symbols.add(mod)
            url = PY_STDLIB_DOCS.get(mod)
            if url:
                _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"Python import {mod}", language=unit.language, source_file=unit.name, confidence=0.90)
            elif self.include_generic_symbols:
                _source_apidoc_add_need(needs, value=f"python-packages: {mod} API reference documentation", kind="query", reason=f"External Python package import {mod}", language=unit.language, source_file=unit.name, confidence=0.62)

        for call in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){1,3})\s*\(", text):
            root = call.split(".")[0]
            if root in PY_STDLIB_DOCS:
                symbols.add(call)
                _source_apidoc_add_need(needs, value=PY_STDLIB_DOCS[root], kind="direct_url", reason=f"Python API call {call}", language=unit.language, source_file=unit.name, confidence=0.78)

        for sym in ("ctypes.CDLL", "ctypes.WinDLL", "ctypes.CFUNCTYPE", "ctypes.WINFUNCTYPE", "ctypes.c_void_p", "ctypes.POINTER", "ctypes.byref", "ctypes.cast", "ctypes.memmove"):
            if sym in text:
                symbols.add(sym)
                _source_apidoc_add_need(needs, value="https://docs.python.org/3/library/ctypes.html", kind="direct_url", reason=f"Python ctypes native interop symbol {sym}", language=unit.language, source_file=unit.name, confidence=0.95)

    def _analyze_csharp(self, unit: APIDocSourceUnit, needs: List[Dict[str, Any]], symbols: Set[str]) -> None:
        text = unit.text
        for ns in re.findall(r"^\s*using\s+([A-Za-z_][A-Za-z0-9_\.]*)(?:\s*=\s*[^;]+)?\s*;", text, flags=re.M):
            symbols.add(ns)
            url = CSHARP_DOCS.get(ns)
            if url:
                _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"C# using {ns}", language=unit.language, source_file=unit.name, confidence=0.90)
            elif ns.startswith("System."):
                api_path = ns.lower()
                _source_apidoc_add_need(needs, value=f"https://learn.microsoft.com/en-us/dotnet/api/{api_path}?view=net-9.0", kind="direct_url", reason=f"C# framework namespace {ns}", language=unit.language, source_file=unit.name, confidence=0.80)
            elif ns.startswith("TaleWorlds."):
                _source_apidoc_add_need(needs, value=f"bannerlord: {ns} API reference", kind="query", reason=f"Bannerlord namespace {ns}", language=unit.language, source_file=unit.name, confidence=0.72)
            elif self.include_generic_symbols:
                _source_apidoc_add_need(needs, value=f"csharp: {ns} API reference", kind="query", reason=f"C# external namespace {ns}", language=unit.language, source_file=unit.name, confidence=0.55)

        for attr in re.findall(r"\[\s*(DllImport|StructLayout|MarshalAs|UnmanagedFunctionPointer)\b", text):
            symbols.add(attr)
            _source_apidoc_add_need(needs, value="https://learn.microsoft.com/en-us/dotnet/api/system.runtime.interopservices?view=net-9.0", kind="direct_url", reason=f"C# interop attribute {attr}", language=unit.language, source_file=unit.name, confidence=0.94)

        for sym in re.findall(r"\b(?:Task|Thread|CancellationToken|Span|Memory|IntPtr|UIntPtr|Marshal|GCHandle|DllImportAttribute)\b", text):
            symbols.add(sym)
            if sym in ("Task",):
                url = "https://learn.microsoft.com/en-us/dotnet/api/system.threading.tasks.task?view=net-9.0"
            elif sym in ("Thread", "CancellationToken"):
                url = "https://learn.microsoft.com/en-us/dotnet/api/system.threading?view=net-9.0"
            elif sym in ("IntPtr", "UIntPtr"):
                url = "https://learn.microsoft.com/en-us/dotnet/api/system?view=net-9.0"
            elif sym in ("Marshal", "GCHandle", "DllImportAttribute"):
                url = "https://learn.microsoft.com/en-us/dotnet/api/system.runtime.interopservices?view=net-9.0"
            else:
                url = "https://learn.microsoft.com/en-us/dotnet/standard/memory-and-spans/"
            _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"C#/.NET API symbol {sym}", language=unit.language, source_file=unit.name, confidence=0.82)

    def _analyze_generic(self, unit: APIDocSourceUnit, needs: List[Dict[str, Any]], symbols: Set[str]) -> None:
        text = unit.text
        for url in re.findall(r"https?://[^\s\]\)\"'<>]+", text):
            cleaned = url.rstrip(".,;:")
            symbols.add(cleaned)
            _source_apidoc_add_need(needs, value=cleaned, kind="direct_url", reason="Direct URL found in source/text", language=unit.language, source_file=unit.name, confidence=0.97)
        for sym, urls in RANDOMX_SYMBOL_DOCS.items():
            if sym in text:
                symbols.add(sym)
                for url in urls:
                    _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"RandomX symbol {sym}", language=unit.language, source_file=unit.name, confidence=0.90)
        for sym, url in {**P2POOL_SOURCE_DOCS, **XMRIG_SOURCE_DOCS, **MONERO_DIRECT_DOCS, **WIN32_SYMBOL_DOCS}.items():
            if sym and re.search(rf"\b{re.escape(sym)}\b", text, flags=re.I):
                symbols.add(sym)
                _source_apidoc_add_need(needs, value=url, kind="direct_url", reason=f"Known API/domain symbol {sym}", language=unit.language, source_file=unit.name, confidence=0.76)

    def _dedupe_needs(self, needs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        for item in needs:
            value = str(item.get("value") or "").strip()
            if not value:
                continue
            key = value.lower()
            if key not in merged:
                merged[key] = dict(item)
                merged[key]["reasons"] = [str(item.get("reason") or "")]
                merged[key]["source_files"] = [str(item.get("source_file") or "")]
                merged[key]["confidence"] = float(item.get("confidence") or 0.0)
            else:
                old = merged[key]
                reason = str(item.get("reason") or "")
                source_file = str(item.get("source_file") or "")
                if reason and reason not in old["reasons"]:
                    old["reasons"].append(reason)
                if source_file and source_file not in old["source_files"]:
                    old["source_files"].append(source_file)
                old["confidence"] = max(float(old.get("confidence") or 0.0), float(item.get("confidence") or 0.0))
        ordered = sorted(merged.values(), key=lambda x: (-float(x.get("confidence") or 0.0), str(x.get("value") or "")))
        return ordered if not self.max_queries else ordered[: self.max_queries]

    def needs_to_queries(self, needs: List[Dict[str, Any]]) -> List[str]:
        out: List[str] = []
        for item in needs:
            value = str(item.get("value") or "").strip()
            if value:
                out.append(value)
        deduped = _source_apidoc_dedupe(out)
        return deduped if not self.max_queries else deduped[: self.max_queries]

    def markdown(self, analysis: Dict[str, Any]) -> str:
        lines: List[str] = []
        lines.append("# Source APIDoc Need Analysis")
        lines.append("")
        lines.append(f"Version: `{analysis.get('version', SOURCE_APIDOC_INTEL_VERSION)}`")
        lines.append("")
        lines.append("## Summary")
        lines.append("")
        lines.append("| Field | Value |")
        lines.append("| --- | --- |")
        lines.append(f"| Files scanned | `{analysis.get('file_count', 0)}` |")
        lines.append(f"| API/doc needs | `{analysis.get('need_count', 0)}` |")
        lines.append(f"| Query lines | `{analysis.get('query_count', 0)}` |")
        lines.append(f"| Direct URLs | `{len(analysis.get('direct_urls', []) or [])}` |")
        lines.append(f"| Languages | `{', '.join(analysis.get('languages', []) or [])}` |")
        lines.append("")
        lines.append("## Files")
        lines.append("")
        lines.append("| File | Language | Size | Needs |")
        lines.append("| --- | --- | ---: | ---: |")
        for f in analysis.get("files", [])[:80]:
            lines.append(f"| `{f.get('name','')}` | `{f.get('language','')}` | `{f.get('size',0)}` | `{len(f.get('needs', []) or [])}` |")
        lines.append("")
        lines.append("## Needed APIDocs")
        lines.append("")
        lines.append("| Confidence | Kind | Value | Why | Files |")
        lines.append("| ---: | --- | --- | --- | --- |")
        for n in analysis.get("needs", [])[:160]:
            reasons = "; ".join((n.get("reasons") or [n.get("reason", "")])[:3])
            source_files = ", ".join([x for x in (n.get("source_files") or [n.get("source_file", "")]) if x][:5])
            value = str(n.get("value") or "").replace("|", "\\|")
            lines.append(f"| `{float(n.get('confidence') or 0.0):.2f}` | `{n.get('kind','')}` | {value} | {reasons.replace('|', '\\|')} | {source_files.replace('|', '\\|')} |")
        lines.append("")
        lines.append("## Plain query lines")
        lines.append("")
        lines.append("```text")
        lines.extend(analysis.get("queries", []) or [])
        lines.append("```")
        return "\n".join(lines).rstrip() + "\n"


@dataclass
class APIDocSourceScanBlock(BaseBlock):
    """Scan source files/code and return structured API-doc needs."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[Any, Dict[str, Any]]:
        analyzer = APIDocSourceNeedAnalyzer(params=params)
        analysis = analyzer.analyze(payload)
        if as_bool(params.get("store_analysis"), False):
            key = str(params.get("analysis_key") or "apidoc_source_last_analysis")
            mem = Memory.load()
            mem[key] = analysis
            Memory.save(mem)
        meta = {
            "type": "apidoc-source-scan",
            "version": SOURCE_APIDOC_INTEL_VERSION,
            "file_count": analysis.get("file_count", 0),
            "need_count": analysis.get("need_count", 0),
            "query_count": analysis.get("query_count", 0),
            "direct_url_count": len(analysis.get("direct_urls", []) or []),
        }
        return analysis, meta

    def get_params_info(self) -> Dict[str, Any]:
        return {
            "path/source_path/code_file/files/paths": "File, folder, list of files, or inline code payload.",
            "max_files": 80,
            "max_bytes_per_file": 320000,
            "max_symbols": 220,
            "max_queries": "100000, or 0/unlimited for no cap",
            "prefer_direct_urls": True,
            "include_stdlib": True,
            "include_generic_symbols": True,
            "store_analysis": False,
            "analysis_key": "apidoc_source_last_analysis",
        }


@dataclass
class APIDocSourceQueriesBlock(BaseBlock):
    """Return only newline-separated APIDoc query/direct-URL lines from source code."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        if isinstance(payload, dict) and isinstance(payload.get("queries"), list) and isinstance(payload.get("needs"), list):
            analysis = payload
        else:
            analysis = APIDocSourceNeedAnalyzer(params=params).analyze(payload)
        queries = _source_apidoc_dedupe([str(x) for x in (analysis.get("queries", []) or [])])
        text = "\n".join(queries).rstrip() + ("\n" if queries else "")
        meta = {
            "type": "apidoc-source-queries",
            "query_count": len(queries),
            "direct_url_count": len([q for q in queries if q.startswith("http://") or q.startswith("https://")]),
        }
        meta.update(_write_optional_text_output(text, params, "apidoc_needed_queries.txt"))
        return text, meta

    def get_params_info(self) -> Dict[str, Any]:
        info = APIDocSourceScanBlock().get_params_info()
        info.update({"out_path": "Optional output .txt file or folder."})
        return info


@dataclass
class APIDocSourceReportBlock(BaseBlock):
    """Return a readable Markdown report explaining which docs are needed and why."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        analyzer = APIDocSourceNeedAnalyzer(params=params)
        if isinstance(payload, dict) and isinstance(payload.get("needs"), list):
            analysis = payload
        else:
            analysis = analyzer.analyze(payload)
        md = analyzer.markdown(analysis)
        meta = {
            "type": "apidoc-source-report",
            "file_count": analysis.get("file_count", 0),
            "need_count": analysis.get("need_count", 0),
            "query_count": analysis.get("query_count", 0),
        }
        meta.update(_write_optional_text_output(md, params, "apidoc_source_need_report.md"))
        return md, meta

    def get_params_info(self) -> Dict[str, Any]:
        info = APIDocSourceScanBlock().get_params_info()
        info.update({"out_path": "Optional output .md file or folder."})
        return info


@dataclass
class APIDocSourceFetchBlock(BaseBlock):
    """Scan code, generate only the needed doc queries, then run the APIDoc fetch/report pipeline."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        analyzer = APIDocSourceNeedAnalyzer(params=params)
        analysis = analyzer.analyze(payload)
        query_text = "\n".join(analysis.get("queries", []) or [])
        merged = dict(params)
        merged["safe_mode"] = False
        merged.pop("safe_max_queries", None)
        direct_mode = as_bool(merged.get("direct_mode"), True)
        merged.setdefault("direct_mode", direct_mode)
        if not direct_mode:
            merged.setdefault("search_mode", "search_first")
            merged["search_fallback"] = True
        else:
            merged.setdefault("search_fallback", False)
        merged.setdefault("crawl_direct_pages", False)
        merged.setdefault("output_style", params.get("output_style", "advanced_report"))
        md, meta = APIDocBlock().execute(query_text, params=merged)
        meta["source_scan"] = {
            "version": SOURCE_APIDOC_INTEL_VERSION,
            "file_count": analysis.get("file_count", 0),
            "need_count": analysis.get("need_count", 0),
            "query_count": analysis.get("query_count", 0),
            "languages": analysis.get("languages", []),
        }
        return md, {"type": "apidoc-source-fetch", **meta}

    def get_params_info(self) -> Dict[str, Any]:
        info = dict(COMMON_PARAMS)
        info.update(APIDocSourceScanBlock().get_params_info())
        info.update({"out_path": "Optional APIDoc markdown output path."})
        return info


@dataclass
class APIDocSourceExplainBlock(BaseBlock):
    """Short human-readable explanation of the source analyzer block names."""

    def execute(self, payload: Any, *, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        text = """# Source APIDoc blocks

- `apidoc_source_scan` / `apidoc_code_scan` / `apidoc_decipher`: returns structured analysis with `needs`, `queries`, `direct_urls`, symbols, and per-file reasons.
- `apidoc_source_queries` / `apidoc_needed_queries`: returns only plain newline-separated query/direct-URL lines, ready to paste into `apidoc`.
- `apidoc_source_report` / `apidoc_needed_report`: returns a Markdown report explaining why each APIDoc is needed.
- `apidoc_source_fetch` / `apidoc_needed_fetch`: scans source, generates needed queries, then runs the normal APIDoc fetch/report pipeline.

Supported source inputs: inline code text, a file path, a folder path, or a dict with `code`, `path`, `paths`, `files`, or `source_path`.
Supported extensions include `.cpp`, `.cxx`, `.cc`, `.c`, `.hpp`, `.h`, `.hxx`, `.py`, `.cs`, `.js`, `.ts`, `.java`, `.rs`, `.go`, `.swift`, `.xml`, `.json`, `.toml`, `.yaml`, `.md`, and `.txt`.
"""
        return text, {"type": "apidoc-source-help", "version": SOURCE_APIDOC_INTEL_VERSION}

    def get_params_info(self) -> Dict[str, Any]:
        return {}


# Source-code APIDoc decipher block registrations.
BLOCKS.register("apidoc_source_scan", APIDocSourceScanBlock)
BLOCKS.register("apidoc_code_scan", APIDocSourceScanBlock)
BLOCKS.register("apidoc_needed_scan", APIDocSourceScanBlock)
BLOCKS.register("apidoc_decipher", APIDocSourceScanBlock)
BLOCKS.register("apidoc_decipher_code", APIDocSourceScanBlock)

BLOCKS.register("apidoc_source_queries", APIDocSourceQueriesBlock)
BLOCKS.register("apidoc_needed_queries", APIDocSourceQueriesBlock)
BLOCKS.register("apidoc_query_from_code", APIDocSourceQueriesBlock)
BLOCKS.register("apidoc_code_queries", APIDocSourceQueriesBlock)

BLOCKS.register("apidoc_source_report", APIDocSourceReportBlock)
BLOCKS.register("apidoc_needed_report", APIDocSourceReportBlock)
BLOCKS.register("apidoc_code_report", APIDocSourceReportBlock)

BLOCKS.register("apidoc_source_fetch", APIDocSourceFetchBlock)
BLOCKS.register("apidoc_needed_fetch", APIDocSourceFetchBlock)
BLOCKS.register("apidoc_code_fetch", APIDocSourceFetchBlock)

BLOCKS.register("apidoc_source_help", APIDocSourceExplainBlock)
BLOCKS.register("apidoc_decipher_help", APIDocSourceExplainBlock)
