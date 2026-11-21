"""
title: Download Code Blocks
author: silentoplayz
author_url: https://github.com/silentoplayz
funding_url: https://github.com/sponsors/silentoplayz
version: 1.1.0
license: MIT
description: Extracts fenced code blocks from any assistant response and downloads them as properly named files. Features a confirmation modal with syntax-highlighted previews, line numbers, and editable filenames. Supports 170+ languages.
required_open_webui_version: 0.4.0
"""

import html as html_lib
import re
from pydantic import BaseModel, Field

try:
    from pygments import highlight as _pygments_highlight
    from pygments.lexers import get_lexer_by_name, ClassNotFound as _LexerNotFound
    from pygments.formatters import HtmlFormatter as _HtmlFormatter

    _HAS_PYGMENTS = True
except ImportError:
    _HAS_PYGMENTS = False


class Action:
    # SVG download icon (Lucide arrow-down-to-line)
    icon_url = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='M12 17V3'/%3E%3Cpath d='m6 11 6 6 6-6'/%3E%3Cpath d='M19 21H5'/%3E%3C/svg%3E"

    class Valves(BaseModel):
        priority: int = Field(
            default=0, description="Priority level for the action button ordering."
        )
        default_filename: str = Field(
            default="code",
            description="Base filename used when a code block has no language specified.",
        )
        auto_download: bool = Field(
            default=False,
            description="When enabled, downloads all code blocks immediately without confirmation prompts.",
        )

    class UserValves(BaseModel):
        default_filename: str = Field(
            default="code",
            description="Base filename used when a code block has no language specified.",
        )
        auto_download: bool = Field(
            default=False,
            description="When enabled, downloads all code blocks immediately without confirmation prompts.",
        )

    def __init__(self):
        self.valves = self.Valves()

    # Mapping of language identifiers to file extensions
    LANG_EXTENSION_MAP = {
        # Web
        "html": "html",
        "htm": "html",
        "css": "css",
        "scss": "scss",
        "sass": "sass",
        "less": "less",
        "javascript": "js",
        "js": "js",
        "jsx": "jsx",
        "typescript": "ts",
        "ts": "ts",
        "tsx": "tsx",
        "svelte": "svelte",
        "vue": "vue",
        "astro": "astro",
        # Python
        "python": "py",
        "py": "py",
        "python3": "py",
        "pyw": "pyw",
        # Systems
        "c": "c",
        "cpp": "cpp",
        "c++": "cpp",
        "cxx": "cpp",
        "h": "h",
        "hpp": "hpp",
        "rust": "rs",
        "rs": "rs",
        "go": "go",
        "golang": "go",
        "zig": "zig",
        # JVM
        "java": "java",
        "kotlin": "kt",
        "kt": "kt",
        "scala": "scala",
        "groovy": "groovy",
        # .NET
        "csharp": "cs",
        "cs": "cs",
        "c#": "cs",
        "fsharp": "fs",
        "fs": "fs",
        "f#": "fs",
        "vb": "vb",
        "vbnet": "vb",
        # Shell
        "bash": "sh",
        "sh": "sh",
        "shell": "sh",
        "zsh": "zsh",
        "fish": "fish",
        "powershell": "ps1",
        "ps1": "ps1",
        "pwsh": "ps1",
        "bat": "bat",
        "batch": "bat",
        "cmd": "cmd",
        # Data / Config
        "json": "json",
        "jsonc": "jsonc",
        "json5": "json5",
        "yaml": "yaml",
        "yml": "yaml",
        "toml": "toml",
        "ini": "ini",
        "cfg": "cfg",
        "xml": "xml",
        "csv": "csv",
        "tsv": "tsv",
        "env": "env",
        "dotenv": "env",
        "properties": "properties",
        # Markup / Docs
        "markdown": "md",
        "md": "md",
        "rst": "rst",
        "restructuredtext": "rst",
        "tex": "tex",
        "latex": "tex",
        "txt": "txt",
        "text": "txt",
        "plaintext": "txt",
        # Ruby
        "ruby": "rb",
        "rb": "rb",
        # PHP
        "php": "php",
        # Swift / Objective-C
        "swift": "swift",
        "objectivec": "m",
        "objc": "m",
        # Functional
        "haskell": "hs",
        "hs": "hs",
        "elixir": "ex",
        "ex": "ex",
        "erlang": "erl",
        "erl": "erl",
        "clojure": "clj",
        "clj": "clj",
        "ocaml": "ml",
        "ml": "ml",
        "lisp": "lisp",
        "scheme": "scm",
        "racket": "rkt",
        # Scripting
        "lua": "lua",
        "perl": "pl",
        "pl": "pl",
        "r": "r",
        "julia": "jl",
        "jl": "jl",
        "dart": "dart",
        "nim": "nim",
        "v": "v",
        "vlang": "v",
        # Database
        "sql": "sql",
        "mysql": "sql",
        "postgresql": "sql",
        "postgres": "sql",
        "sqlite": "sql",
        "plsql": "sql",
        "graphql": "graphql",
        "gql": "graphql",
        # DevOps / IaC
        "dockerfile": "Dockerfile",
        "docker": "Dockerfile",
        "terraform": "tf",
        "tf": "tf",
        "hcl": "tf",
        "nginx": "conf",
        "apache": "conf",
        # Build tools
        "makefile": "Makefile",
        "make": "Makefile",
        "cmake": "cmake",
        "gradle": "gradle",
        # Other
        "diff": "diff",
        "patch": "patch",
        "asm": "asm",
        "assembly": "asm",
        "nasm": "asm",
        "wasm": "wasm",
        "proto": "proto",
        "protobuf": "proto",
        "solidity": "sol",
        "sol": "sol",
        "cuda": "cu",
        "glsl": "glsl",
        "hlsl": "hlsl",
        "wgsl": "wgsl",
        "regex": "txt",
        "mermaid": "mmd",
    }

    # MIME type mapping for common extensions
    MIME_TYPE_MAP = {
        "html": "text/html",
        "css": "text/css",
        "js": "text/javascript",
        "jsx": "text/javascript",
        "ts": "text/typescript",
        "tsx": "text/typescript",
        "json": "application/json",
        "xml": "application/xml",
        "svg": "image/svg+xml",
        "md": "text/markdown",
        "py": "text/x-python",
        "sh": "text/x-shellscript",
        "sql": "application/sql",
        "yaml": "text/yaml",
        "csv": "text/csv",
    }

    # Maximum number of lines to show in the code preview
    MAX_PREVIEW_LINES = 200

    def _get_extension(self, lang: str) -> str:
        """Resolve a language identifier to a file extension."""
        if not lang:
            return "txt"
        return self.LANG_EXTENSION_MAP.get(lang.lower().strip(), lang.lower().strip())

    def _get_mime_type(self, ext: str) -> str:
        """Get the MIME type for a file extension."""
        return self.MIME_TYPE_MAP.get(ext, "text/plain")

    def _extract_code_blocks(self, content: str) -> list[dict]:
        """
        Extract all fenced code blocks from markdown content.
        Returns a list of dicts with 'lang', 'code', and 'filename' keys.
        """
        # Match ```lang ... ``` blocks, including optional filename hints
        pattern = r"```(\w*)\s*\n(.*?)```"
        matches = re.findall(pattern, content, re.DOTALL)

        blocks = []
        lang_counters: dict[str, int] = {}

        for lang, code in matches:
            lang = lang.strip() if lang else ""
            code = code.rstrip("\n")

            if not code.strip():
                continue

            ext = self._get_extension(lang)
            base = self.valves.default_filename

            # Use the language as a more descriptive base if available
            if lang:
                base = lang.lower()

            # Always number files: python_1.py, python_2.py, etc.
            lang_counters[ext] = lang_counters.get(ext, 0) + 1
            count = lang_counters[ext]

            filename = f"{base}_{count}.{ext}"

            # Handle special filenames (Dockerfile, Makefile, etc.)
            if ext in ("Dockerfile", "Makefile"):
                filename = f"{ext}_{count}"

            blocks.append(
                {
                    "lang": lang or "text",
                    "code": code,
                    "filename": filename,
                    "extension": ext,
                }
            )

        return blocks

    def _build_code_preview(self, block: dict, index: int, total: int) -> str:
        """
        Build an HTML-formatted code preview for the confirmation modal.

        Uses raw HTML that visually matches Open WebUI's native CodeBlock
        component: OLED black background, language header bar, and CSS-
        powered line numbers. html.escape() prevents code content from
        being rendered by the marked parser. A scrollable container
        handles long code.
        """
        lang = block["lang"]
        code = block["code"]
        lines = code.split("\n")
        total_lines = len(lines)

        # Truncate extremely long blocks to limit WebSocket payload size
        if total_lines > self.MAX_PREVIEW_LINES:
            preview_lines = lines[: self.MAX_PREVIEW_LINES]
            remaining = total_lines - self.MAX_PREVIEW_LINES
            truncation_html = (
                f'<div style="padding: 6px 14px 10px; background: #000; '
                f"font-size: 0.7rem; color: #6b7280; font-style: italic; "
                f'border-radius: 0 0 16px 16px;">'
                f"\u2026 {remaining} more line{'s' if remaining != 1 else ''} "
                f"(truncated for preview)</div>"
            )
        else:
            preview_lines = lines
            truncation_html = ""

        escaped_lang = html_lib.escape(lang)
        line_label = f"{total_lines} line{'s' if total_lines != 1 else ''}"

        return (
            f'<p style="margin: 0 0 8px; font-size: 0.85rem;">'
            f"<strong>Code block {index} of {total}</strong> &middot; "
            f"{line_label}</p>"
            #
            # Code block container
            f'<div style="border-radius: 16px; overflow: hidden; '
            f'border: 1px solid rgba(127,127,127,0.15);">'
            #
            # ── Header bar (matches CodeBlock.svelte sticky header) ──
            f'<div style="display: flex; align-items: center; '
            f"padding: 6px 14px; "
            f"background: #000; color: #fff; "
            f'font-size: 0.75rem; font-weight: 400;">'
            f'<span style="opacity: 0.8;">{escaped_lang}</span>'
            f"</div>"
            #
            # ── Scrollable code area ──
            f'<div style="max-height: 250px; overflow-y: auto; '
            f'overflow-x: auto; background: #000;">'
            f'<pre style="margin: 0; padding: 0 14px 12px 0; '
            f"background: #000; color: #c9d1d9; "
            f"font-family: ui-monospace, 'Cascadia Code', 'Fira Code', "
            f"Menlo, Consolas, monospace; "
            f"font-size: 0.75rem; line-height: 1.6; "
            f'white-space: pre; tab-size: 4;">'
            f"<code>{self._build_numbered_code(preview_lines, lang)}</code>"
            f"</pre></div>"
            #
            # Truncation notice (if applicable)
            f"{truncation_html}"
            f"</div>"
        )

    def _highlight_code(self, code: str, lang: str) -> str | None:
        """
        Syntax-highlight code using Pygments with github-dark inline styles.

        Returns the highlighted HTML string, or None if Pygments is
        unavailable or the language is not recognized. Uses
        ``noclasses=True`` so all colors are inlined as ``style``
        attributes, which survive DOMPurify sanitization.
        """
        if not _HAS_PYGMENTS:
            return None

        try:
            lexer = get_lexer_by_name(lang)
        except _LexerNotFound:
            return None

        # github-dark matches Open WebUI's highlight.js theme
        for style in ("github-dark", "monokai"):
            try:
                formatter = _HtmlFormatter(
                    noclasses=True,
                    style=style,
                    nowrap=True,
                )
                return _pygments_highlight(code, lexer, formatter)
            except Exception:
                continue

        return None

    def _build_numbered_code(self, lines: list[str], lang: str = "") -> str:
        """
        Build HTML for code lines with inline line numbers and optional
        syntax highlighting.

        If Pygments is available the code is highlighted first, then
        split by newlines so each line can be prefixed with a dimmed,
        right-aligned line number ``<span>``.
        """
        # Attempt syntax highlighting
        preview_code = "\n".join(lines)
        highlighted = self._highlight_code(preview_code, lang)

        if highlighted is not None:
            h_lines = highlighted.rstrip("\n").split("\n")
        else:
            h_lines = None

        gutter_width = len(str(len(lines)))
        parts = []
        for i in range(len(lines)):
            # Use highlighted line if available, otherwise escape
            if h_lines is not None and i < len(h_lines):
                content = h_lines[i] if h_lines[i] else " "
            else:
                content = html_lib.escape(lines[i]) if lines[i] else " "

            num_str = str(i + 1).rjust(gutter_width)
            parts.append(
                f'<span style="display: inline-block; width: {gutter_width + 1}ch; '
                f"text-align: right; color: #484f58; "
                f"user-select: none; padding-right: 1.5ch; "
                f'margin-left: 8px;">{num_str}</span>{content}'
            )
        return "\n".join(parts)

    def _build_single_download_js(self, code: str, filename: str, ext: str) -> str:
        """Build JavaScript to download a single file."""
        mime = self._get_mime_type(ext)
        escaped_code = (
            code.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
        )
        return f"""
        (function() {{
            const content = `{escaped_code}`;
            const blob = new Blob([content], {{ type: '{mime}' }});
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = '{filename}';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }})();
        """

    def _build_batch_download_js(self, blocks: list[dict]) -> str:
        """
        Build JavaScript that downloads multiple files via the browser.
        Uses a staggered approach to avoid popup blockers.
        """
        file_defs = []
        for block in blocks:
            mime = self._get_mime_type(block["extension"])
            escaped_code = (
                block["code"]
                .replace("\\", "\\\\")
                .replace("`", "\\`")
                .replace("${", "\\${")
            )
            file_defs.append(
                f'{{ filename: "{block["filename"]}", content: `{escaped_code}`, mime: "{mime}" }}'
            )

        files_array = ",\n                ".join(file_defs)

        return f"""
        (function() {{
            const files = [
                {files_array}
            ];

            function downloadFile(file) {{
                const blob = new Blob([file.content], {{ type: file.mime }});
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = file.filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            }}

            // Stagger downloads to avoid browser throttling
            files.forEach(function(file, index) {{
                setTimeout(function() {{
                    downloadFile(file);
                }}, index * 350);
            }});
        }})();
        """

    async def _confirm_and_download(
        self,
        blocks: list[dict],
        __event_emitter__,
        __event_call__,
    ) -> None:
        """
        Interactive download flow with navigation between code blocks.

        For **multiple blocks**, shows a custom modal (via ``execute``
        event) with ◀ Prev / Skip ▶ / ✓ Save buttons so the user can
        freely navigate between code blocks, review the syntax-
        highlighted preview, rename each file, and choose which to save.

        For a **single block**, uses the simpler built-in ``input``
        modal since there is nothing to navigate.
        """
        total = len(blocks)
        saved_count = 0
        saved_files = []

        if total == 1:
            # ── Single block — use the simpler built-in input modal ──
            block = blocks[0]
            preview = self._build_code_preview(block, 1, 1)

            response = await __event_call__(
                {
                    "type": "input",
                    "data": {
                        "title": "Save Code Block",
                        "message": preview,
                        "placeholder": "Enter filename...",
                        "value": block["filename"],
                    },
                }
            )

            if response is not False and response is not None:
                filename = str(response).strip() or block["filename"]
                if not filename:
                    filename = block["filename"]
                if "." in filename:
                    ext = filename.rsplit(".", 1)[-1]
                else:
                    ext = block["extension"]
                    filename = f"{filename}.{ext}"

                js_code = self._build_single_download_js(block["code"], filename, ext)
                await __event_emitter__({"type": "execute", "data": {"code": js_code}})
                saved_count = 1
                saved_files.append(filename)
        else:
            # ── Multiple blocks — custom navigator modal ─────────────
            # Pre-render all code previews for instant navigation
            previews = [
                self._build_code_preview(block, i + 1, total)
                for i, block in enumerate(blocks)
            ]

            current = 0
            while 0 <= current < total:
                block = blocks[current]

                js_code = self._build_navigator_js(
                    preview_html=previews[current],
                    filename=block["filename"],
                    current=current + 1,
                    total=total,
                )

                result = await __event_call__(
                    {"type": "execute", "data": {"code": js_code}}
                )

                # Determine the user's action
                if isinstance(result, dict):
                    user_action = result.get("action", "done")
                else:
                    user_action = "done"

                if user_action == "save":
                    filename = (
                        str(result.get("filename", "")).strip() or block["filename"]
                    )
                    if "." in filename:
                        ext = filename.rsplit(".", 1)[-1]
                    else:
                        ext = block["extension"]
                        filename = f"{filename}.{ext}"

                    dl_js = self._build_single_download_js(block["code"], filename, ext)
                    await __event_emitter__(
                        {"type": "execute", "data": {"code": dl_js}}
                    )

                    saved_count += 1
                    saved_files.append(filename)

                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": f"Saved {filename}",
                                "done": False,
                            },
                        }
                    )
                    current += 1

                elif user_action == "skip":
                    current += 1

                elif user_action == "prev":
                    current = max(0, current - 1)

                else:  # "done" or unknown
                    break

        # ── Final status ─────────────────────────────────────────────
        if saved_count == 0:
            description = "No code blocks were saved."
        else:
            noun = "file" if saved_count == 1 else "files"
            file_list = ", ".join(saved_files)
            description = f"Saved {saved_count} {noun}: {file_list}"

        await __event_emitter__(
            {
                "type": "status",
                "data": {"description": description, "done": True},
            }
        )

    def _build_navigator_js(
        self,
        preview_html: str,
        filename: str,
        current: int,
        total: int,
    ) -> str:
        """
        Build JavaScript that creates a custom modal with navigation
        buttons (◀ Prev / Skip ▶ / ✓ Save).

        The JS returns a Promise that resolves when the user clicks a
        button. The resolved value is an object like
        ``{ action: 'save', filename: '...' }``. The ``execute`` event
        awaits this Promise and passes the result back to Python via
        the socket.io acknowledgment callback.
        """
        import json

        preview_json = json.dumps(preview_html)
        filename_json = json.dumps(filename)

        return f"""
return new Promise(function(resolve) {{
    var previewHtml = {preview_json};
    var defaultFilename = {filename_json};
    var current = {current};
    var total = {total};

    /* ── Overlay ─────────────────────────────────────────────── */
    var overlay = document.createElement('div');
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.6);z-index:99999999;display:flex;justify-content:center;align-items:center;opacity:0;transition:opacity 0.1s ease;';

    /* ── Modal ───────────────────────────────────────────────── */
    var modal = document.createElement('div');
    modal.style.cssText = 'background:rgba(3,7,18,0.95);border-radius:2rem;max-width:32rem;width:calc(100% - 2rem);max-height:90dvh;display:flex;flex-direction:column;box-shadow:0 25px 50px rgba(0,0,0,0.5);border:1px solid rgb(17,24,39);transform:scale(0.985);opacity:0;transition:transform 0.1s ease,opacity 0.1s ease;';
    modal.addEventListener('mousedown', function(e) {{ e.stopPropagation(); }});
    overlay.addEventListener('mousedown', function() {{ finish('done'); }});

    /* ── Title ───────────────────────────────────────────────── */
    var titleEl = document.createElement('div');
    titleEl.style.cssText = 'padding:1.5rem 1.75rem 0;font-size:1.125rem;font-weight:500;color:#e5e7eb;';
    titleEl.textContent = 'Save Code Block (' + current + '/' + total + ')';

    /* ── Code preview ────────────────────────────────────────── */
    var content = document.createElement('div');
    content.style.cssText = 'padding:0.75rem 1.75rem 0;overflow-y:auto;flex:1 1 auto;min-height:0;font-size:0.875rem;color:#9ca3af;';
    content.innerHTML = previewHtml;

    /* ── Filename input ──────────────────────────────────────── */
    var inputWrap = document.createElement('div');
    inputWrap.style.cssText = 'padding:0 1.75rem;';
    var input = document.createElement('input');
    input.type = 'text';
    input.value = defaultFilename;
    input.placeholder = 'Enter filename...';
    input.style.cssText = 'width:100%;box-sizing:border-box;margin-top:0.75rem;border-radius:0.75rem;padding:0.625rem 1rem;font-size:0.875rem;color:#d1d5db;background:rgb(17,24,39);border:none;outline:none;';
    inputWrap.appendChild(input);

    /* ── Buttons ─────────────────────────────────────────────── */
    var btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:0.375rem;padding:1.5rem 1.75rem;';

    function makeBtn(text, bg, fg) {{
        var b = document.createElement('button');
        b.textContent = text;
        b.style.cssText = 'padding:0.5rem 0;border-radius:1.5rem;border:none;cursor:pointer;font-size:0.875rem;font-weight:500;transition:opacity 0.15s;flex:1;background:' + bg + ';color:' + fg + ';';
        b.addEventListener('mouseenter', function() {{ b.style.opacity = '0.85'; }});
        b.addEventListener('mouseleave', function() {{ b.style.opacity = '1'; }});
        return b;
    }}

    var prevBtn = makeBtn('\\u25C0  Prev', 'rgb(31,41,55)', '#9ca3af');
    if (current <= 1) {{
        prevBtn.disabled = true;
        prevBtn.style.opacity = '0.3';
        prevBtn.style.cursor = 'default';
    }}
    prevBtn.addEventListener('click', function() {{ if (current > 1) finish('prev'); }});

    var skipBtn = makeBtn('Skip  \\u25B6', 'rgb(31,41,55)', '#9ca3af');
    skipBtn.addEventListener('click', function() {{ finish('skip'); }});

    var saveBtn = makeBtn('\\u2713  Save', 'rgb(243,244,246)', 'rgb(31,41,55)');
    saveBtn.style.fontWeight = '600';
    saveBtn.addEventListener('click', function() {{ finish('save'); }});

    btnRow.append(prevBtn, skipBtn, saveBtn);
    modal.append(titleEl, content, inputWrap, btnRow);
    overlay.appendChild(modal);

    /* ── Lock body scroll ────────────────────────────────────── */
    var prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    document.body.appendChild(overlay);

    /* ── Animate in ──────────────────────────────────────────── */
    requestAnimationFrame(function() {{
        overlay.style.opacity = '1';
        modal.style.transform = 'scale(1)';
        modal.style.opacity = '1';
        input.focus();
        input.select();
    }});

    /* ── Keyboard shortcuts ──────────────────────────────────── */
    function handleKey(e) {{
        if (e.key === 'Escape') {{ finish('done'); }}
        else if (e.key === 'Enter') {{ e.preventDefault(); finish('save'); }}
    }}
    document.addEventListener('keydown', handleKey, true);

    /* ── Cleanup and resolve ─────────────────────────────────── */
    var resolved = false;
    function finish(action) {{
        if (resolved) return;
        resolved = true;
        document.removeEventListener('keydown', handleKey, true);
        document.body.style.overflow = prevOverflow || '';
        overlay.style.opacity = '0';
        modal.style.transform = 'scale(0.985)';
        modal.style.opacity = '0';
        setTimeout(function() {{
            try {{ document.body.removeChild(overlay); }} catch(e) {{}}
            resolve({{ action: action, filename: input.value.trim() || defaultFilename }});
        }}, 120);
    }}
}});
"""

        return indices

    async def _auto_download(
        self,
        blocks: list[dict],
        __event_emitter__,
    ) -> None:
        """Download all code blocks immediately without confirmation."""
        count = len(blocks)
        file_list = ", ".join(b["filename"] for b in blocks)
        noun = "file" if count == 1 else "files"

        await __event_emitter__(
            {
                "type": "status",
                "data": {
                    "description": f"Found {count} code {noun}. Downloading: {file_list}",
                    "done": False,
                },
            }
        )

        js_code = self._build_batch_download_js(blocks)

        await __event_emitter__(
            {
                "type": "execute",
                "data": {"code": js_code},
            }
        )

        await __event_emitter__(
            {
                "type": "status",
                "data": {
                    "description": f"Downloaded {count} {noun}: {file_list}",
                    "done": True,
                },
            }
        )

    async def action(
        self,
        body: dict,
        __user__=None,
        __event_emitter__=None,
        __event_call__=None,
    ) -> None:
        """Extract code blocks from the assistant's response and download them."""

        # Resolve effective settings: user valves override admin valves
        user_valves = (
            __user__.get("valves") if __user__ and isinstance(__user__, dict) else None
        )

        auto_download = (
            user_valves.auto_download if user_valves else self.valves.auto_download
        )
        default_filename = (
            user_valves.default_filename
            if user_valves
            else self.valves.default_filename
        )

        # Temporarily apply the resolved filename for extraction
        original_filename = self.valves.default_filename
        self.valves.default_filename = default_filename

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": "Scanning for code blocks...",
                        "done": False,
                    },
                }
            )

        # Get the messages from the body
        messages = body.get("messages", [])

        if not messages:
            self.valves.default_filename = original_filename
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": "No messages found.",
                            "done": True,
                        },
                    }
                )
            return

        # Find the target assistant message.
        # The action button is attached to a specific message — `body["id"]`
        # corresponds to the message_id. Walk backwards to find the matching
        # assistant message.
        target_message_id = body.get("id")
        target_content = None

        for msg in reversed(messages):
            if msg.get("role") == "assistant":
                # If we can match by id, do so; otherwise take the last assistant msg
                if target_message_id and msg.get("id") == target_message_id:
                    target_content = msg.get("content", "")
                    break
                elif not target_content:
                    target_content = msg.get("content", "")

        if not target_content:
            self.valves.default_filename = original_filename
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": "No assistant response found.",
                            "done": True,
                        },
                    }
                )
            return

        # Extract code blocks
        blocks = self._extract_code_blocks(target_content)

        # Restore the original valve value
        self.valves.default_filename = original_filename

        if not blocks:
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": "No code blocks found in the response.",
                            "done": True,
                        },
                    }
                )
            return

        # Dispatch to the appropriate mode
        if auto_download:
            await self._auto_download(blocks, __event_emitter__)
        else:
            if not __event_call__:
                # Fallback: if __event_call__ isn't available, use auto download
                await self._auto_download(blocks, __event_emitter__)
            else:
                await self._confirm_and_download(
                    blocks, __event_emitter__, __event_call__
                )
