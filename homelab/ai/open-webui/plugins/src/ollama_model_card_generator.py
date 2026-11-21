"""
title: Ollama Model Card Generator
author: @G30
author_url: https://openwebui.com/u/g30
funding_url: https://buymeacoffee.com/iamg30
version: 1.7.0
license: MIT
description: Admin tool that turns Ollama metadata into rich model cards, names, tags, capabilities, inference params, prompts, avatars and workspace models — VRAM-aware context capping, global context override, orphan cleanup, hide base models behind their workspace wrappers, and dry-run + reset valves for every write.
required_open_webui_version: 0.9.0
"""

import json
import logging
import re
import asyncio
import base64
import hashlib
import io
import mimetypes
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
from pydantic import BaseModel, Field

from open_webui.env import (
    AIOHTTP_CLIENT_SESSION_SSL,
    AIOHTTP_CLIENT_TIMEOUT,
)
from open_webui.utils.session_pool import get_session

try:  # Open WebUI >= 0.11 validates avatar data URIs; mirror its rules.
    from open_webui.env import (
        PROFILE_IMAGE_ALLOWED_MIME_TYPES,
        PROFILE_IMAGE_MAX_DATA_URI_SIZE,
    )
except Exception:  # older releases: no validation
    PROFILE_IMAGE_ALLOWED_MIME_TYPES = frozenset(
        {"image/png", "image/jpeg", "image/gif", "image/webp"}
    )
    PROFILE_IMAGE_MAX_DATA_URI_SIZE = None

log = logging.getLogger(__name__)


def _section(title: str) -> str:
    """Valve-list section header.

    Open WebUI renders valve descriptions as markdown, so appending a rule and
    a heading to the LAST valve of a group draws a divider before the next
    group in the Valves dialog.
    """
    return f"\n\n---\n\n#### {title}"


class Tools:
    """
    Ollama Model Card Generator

    Admin-only tool that reads metadata from the Ollama API (``/api/tags``
    and ``/api/show``) and turns it into Open WebUI model entries:
    descriptions (model cards), tags, capability flags, inference
    parameters, system prompts, suggestion prompts, display names and
    profile images.

    Tools (all require the admin role):

    * ``generate_model_card`` / ``generate_all_model_cards`` — build cards
      without writing anything.
    * ``apply_model_card`` / ``apply_all_model_cards`` — write cards and
      metadata onto the models' database entries.
    * ``create_workspace_model`` / ``create_all_workspace_models`` — create
      "-workspace" models that wrap each Ollama model.
    * ``reset_model`` / ``reset_all_models`` — undo, field by field, via
      the ``clear_*`` valves.
    * ``remove_orphaned_workspace_models`` — delete workspace models whose
      base model no longer exists (dry-run by default).
    * ``apply_context_length_to_all_models`` — push the
      ``context_length_override`` valve onto every model entry.
    * ``apply_profile_images_to_all_models`` — (re)assign avatars to every
      model entry, workspace models included; generates a monogram when
      no icon file matches.
    * ``apply_base_model_visibility_to_all_models`` — hide (or unhide) every
      Ollama base model that has a workspace wrapper, so only the wrappers
      show in the selector (``hide_base_models`` valve).

    Every write is controlled by ``auto_*`` / ``overwrite_*`` valves, so
    the default behaviour fills gaps without clobbering manual edits, and
    every tool that applies, creates, resets or removes something has a
    ``*_dry_run`` valve that reports what would change without writing,
    and every apply/create tool has a ``*_reset`` valve that makes the same
    call undo its work instead (base overrides deleted, workspace models
    deleted, avatars/num_ctx cleared) — Ollama models are never touched.
    """

    def __init__(self):
        self.valves = self.Valves()
        self._profile_image_cache = None
        self._profile_image_cache_dir = None

    class Valves(BaseModel):
        """Tool toggles, dry-run switches and auto-configuration settings.

        Every valve that applies, creates, resets or removes something has a
        ``*_dry_run`` sibling directly beneath it: with the sibling on, the
        tool reports exactly what it WOULD change and writes nothing.
        """

        # ── Preview (read-only) ──────────────────────────────────────────
        enable_generate_model_card: bool = Field(
            default=True,
            description="Enable 'generate_model_card' — builds one model's card and metadata without writing anything. Always safe.",
        )
        enable_generate_all_model_cards: bool = Field(
            default=True,
            description="Enable 'generate_all_model_cards' — builds cards for every installed Ollama model without writing anything. Always safe."
            + _section("✍️ Apply Model Cards"),
        )

        # ── Apply Model Cards ────────────────────────────────────────────
        enable_apply_model_card: bool = Field(
            default=True,
            description="Enable 'apply_model_card' — writes one model's card, name, tags, capabilities, params, prompts and avatar to its database entry (creates the entry if missing).",
        )
        apply_model_card_dry_run: bool = Field(
            default=False,
            description="🔍 DRY RUN for 'apply_model_card': report, field by field, what would be written — and write nothing. Turn off to apply for real.",
        )
        apply_model_card_reset: bool = Field(
            default=False,
            description="↩️ RESET for 'apply_model_card': when on, the tool UNDOES instead of applies — it deletes the model's Open WebUI override entry (card, name, tags, params, prompts, avatar, hidden/disabled state) so the Ollama model shows raw again. The Ollama model itself is never deleted. Honors the dry-run valve above.",
        )
        enable_apply_all_model_cards: bool = Field(
            default=True,
            description="Enable 'apply_all_model_cards' — the same as above for every installed Ollama model.",
        )
        apply_all_model_cards_dry_run: bool = Field(
            default=False,
            description="🔍 DRY RUN for 'apply_all_model_cards': list which models would be updated and which fields would change — and write nothing. Turn off to apply for real.",
        )
        apply_all_model_cards_reset: bool = Field(
            default=False,
            description="↩️ RESET for 'apply_all_model_cards': when on, the tool UNDOES instead — it deletes the override entry of every installed Ollama model (plus stale entries that still carry this tool's card or avatar), so all Ollama models show raw again. Ollama models themselves are never deleted. Honors the dry-run valve above.",
        )
        overwrite_existing: bool = Field(
            default=False,
            description="When applying model cards, overwrite models that already have a description. Off skips those models entirely."
            + _section("🗂️ Workspace Models"),
        )

        # ── Workspace Models ─────────────────────────────────────────────
        enable_create_workspace_model: bool = Field(
            default=True,
            description="Enable 'create_workspace_model' — creates a fully enriched '-workspace' model wrapping one Ollama model, and removes the duplicate base-model override entry if one exists.",
        )
        create_workspace_model_dry_run: bool = Field(
            default=False,
            description="🔍 DRY RUN for 'create_workspace_model': report the workspace model that would be created or updated (and whether a base override would be removed) — and write nothing.",
        )
        create_workspace_model_reset: bool = Field(
            default=False,
            description="↩️ RESET for 'create_workspace_model': when on, the tool UNDOES instead of creates — it deletes the model's '-workspace' entry and unhides its base model if hide_base_models had hidden it. Honors the dry-run valve above.",
        )
        enable_create_all_workspace_models: bool = Field(
            default=True,
            description="Enable 'create_all_workspace_models' — the same as above for every installed Ollama model.",
        )
        create_all_workspace_models_dry_run: bool = Field(
            default=False,
            description="🔍 DRY RUN for 'create_all_workspace_models': list which workspace models would be created, updated or skipped — and write nothing.",
        )
        create_all_workspace_models_reset: bool = Field(
            default=False,
            description="↩️ RESET for 'create_all_workspace_models': when on, the tool UNDOES instead — it deletes every '-workspace' model this tool created and unhides every base model that hide_base_models had hidden. Workspace models with other IDs are left alone. Honors the dry-run valve above.",
        )
        overwrite_workspace_models: bool = Field(
            default=False,
            description="Refresh workspace models that already exist when creating. Off skips models that already have a workspace model.",
        )
        enable_apply_base_model_visibility: bool = Field(
            default=True,
            description="Enable 'apply_base_model_visibility_to_all_models' — hides (hide_base_models on) or unhides (off) every Ollama base model that has a workspace model, so only the workspace versions show in the model selector. Base models without a workspace model are never hidden.",
        )
        apply_base_model_visibility_dry_run: bool = Field(
            default=False,
            description="🔍 DRY RUN for 'apply_base_model_visibility_to_all_models': list which base models would be hidden or unhidden — and write nothing.",
        )
        hide_base_models: bool = Field(
            default=False,
            description="Hide Ollama base models behind their workspace models. On: workspace creation hides the base entry (instead of deleting its override), and 'apply_base_model_visibility_to_all_models' hides every base that has a workspace model. Off: that tool unhides the bases this valve hid (off IS its reset). Hidden ≠ disabled — the base stays loaded so the workspace model keeps working."
            + _section("♻️ Reset (Undo)"),
        )

        # ── Reset ────────────────────────────────────────────────────────
        enable_reset_model: bool = Field(
            default=True,
            description="Enable 'reset_model' — clears one model's fields back to defaults. ONLY the fields whose clear_* valves below are on are touched.",
        )
        reset_model_dry_run: bool = Field(
            default=True,
            description="🔍 DRY RUN for 'reset_model': report which fields would be cleared — and write nothing. On by default; turn off to reset for real.",
        )
        enable_reset_all_models: bool = Field(
            default=True,
            description="Enable 'reset_all_models' — the same as above for every model entry in the database (workspace models included).",
        )
        reset_all_models_dry_run: bool = Field(
            default=True,
            description="🔍 DRY RUN for 'reset_all_models': list which models and fields would be cleared — and write nothing. On by default; turn off to reset for real.",
        )
        clear_description: bool = Field(
            default=False,
            description="When resetting: clear the model description (the model card).",
        )
        clear_display_name: bool = Field(
            default=False,
            description="When resetting: revert the display name to the raw model ID.",
        )
        clear_tags: bool = Field(
            default=False,
            description="When resetting: remove all tags.",
        )
        clear_capabilities: bool = Field(
            default=False,
            description="When resetting: remove capability flags (vision, usage, citations…), letting Open WebUI auto-detect.",
        )
        clear_params: bool = Field(
            default=False,
            description="When resetting: remove all custom inference parameters (num_ctx, temperature, stop tokens…). The system prompt is kept unless clear_system_prompt is also on.",
        )
        clear_system_prompt: bool = Field(
            default=False,
            description="When resetting: remove the system prompt.",
        )
        clear_suggestion_prompts: bool = Field(
            default=False,
            description="When resetting: remove all suggestion prompts (chat starters).",
        )
        clear_profile_image: bool = Field(
            default=False,
            description="When resetting: put the avatar back to the default Open WebUI logo.",
        )
        clear_hidden: bool = Field(
            default=False,
            description="When resetting: unhide models hidden by auto_hide_embedding or hide_base_models.",
        )
        clear_inactive: bool = Field(
            default=False,
            description="When resetting: re-enable (toggle back on) models that were deactivated, e.g. by auto_disable_sharded."
            + _section("🧹 Orphan Cleanup"),
        )

        # ── Orphaned Workspace Models ────────────────────────────────────
        enable_remove_orphaned_workspace_models: bool = Field(
            default=False,
            description="Enable 'remove_orphaned_workspace_models' — deletes workspace models whose base model no longer exists (e.g. after removing Ollama models). Off by default because it deletes database entries.",
        )
        remove_orphaned_workspace_models_dry_run: bool = Field(
            default=True,
            description="🔍 DRY RUN for 'remove_orphaned_workspace_models': list the orphans that would be deleted — and delete nothing. On by default; turn off to delete for real."
            + _section("📏 Context Length & VRAM"),
        )

        # ── Context Length & VRAM ────────────────────────────────────────
        enable_apply_context_length: bool = Field(
            default=True,
            description="Enable 'apply_context_length_to_all_models' — sets num_ctx on EVERY model entry in the database to context_length_override.",
        )
        apply_context_length_dry_run: bool = Field(
            default=False,
            description="🔍 DRY RUN for 'apply_context_length_to_all_models': list each model's current num_ctx and the value it would get — and write nothing.",
        )
        apply_context_length_reset: bool = Field(
            default=False,
            description="↩️ RESET for 'apply_context_length_to_all_models': when on, the tool REMOVES num_ctx (and max_tokens, if context_override_sets_max_tokens is on) from every model entry instead of setting it, so models fall back to Ollama's own defaults. Honors the dry-run valve above.",
        )
        context_length_override: int = Field(
            default=0,
            ge=0,
            description="When greater than 0, force num_ctx to this value for every model this tool writes (apply/create), overriding Ollama's reported context AND the VRAM-aware estimate below. Run 'apply_context_length_to_all_models' to push it onto all existing entries. 0 = disabled.",
        )
        context_override_sets_max_tokens: bool = Field(
            default=False,
            description="When applying the context length override, also set max_tokens to min(override, 8192). Off leaves max_tokens untouched.",
        )
        gpu_vram_gib: float = Field(
            default=0,
            description=(
                "Total GPU VRAM in GiB (e.g. 24 for an RX 7900 XTX, 12 for a 4070 Ti). "
                "Used to calculate the maximum affordable num_ctx per model when "
                "context_length_override is 0. Set to 0 to disable VRAM-aware context capping."
            ),
        )
        vram_reserve_gib: float = Field(
            default=2.0,
            description=(
                "VRAM (GiB) to reserve for the OS and desktop. Covers the "
                "Wayland/X11 compositor, browser GPU acceleration, and other "
                "apps. Default 2 GiB is safe for most Linux desktops."
            ),
        )
        weight_overhead_pct: int = Field(
            default=25,
            description=(
                "Percentage overhead to add to model file size to estimate "
                "actual VRAM usage. Accounts for GPU compute graph buffers, "
                "MoE expert routing, embedding dequantization, and memory "
                "allocator fragmentation. Default 25% is based on observed "
                "measurements with MoE/UD-quant models; dense models may "
                "need less (15-20%)."
            ),
        )
        num_parallel: int = Field(
            default=2,
            description=(
                "Number of parallel inference slots (must match your Ollama "
                "OLLAMA_NUM_PARALLEL setting). Each slot allocates its own "
                "KV cache, so this directly multiplies VRAM usage for context."
            ),
        )
        kv_cache_quant: str = Field(
            default="q8_0",
            description=(
                "KV cache quantization type matching your Ollama config "
                "(OLLAMA_KV_CACHE_TYPE). Used to estimate KV cache VRAM per token. "
                "Options: 'f16' (2 bytes), 'q8_0' (1 byte), 'q4_0' (0.5 bytes)."
            )
            + _section("🖼️ Profile Images"),
        )

        # ── Profile Images ───────────────────────────────────────────────
        enable_apply_profile_images: bool = Field(
            default=True,
            description="Enable 'apply_profile_images_to_all_models' — (re)assigns avatars on EVERY model entry, including existing workspace models that apply/create don't revisit.",
        )
        apply_profile_images_dry_run: bool = Field(
            default=False,
            description="🔍 DRY RUN for 'apply_profile_images_to_all_models': list which models would get an avatar and whether it comes from an icon file or a generated monogram — and write nothing.",
        )
        apply_profile_images_reset: bool = Field(
            default=False,
            description="↩️ RESET for 'apply_profile_images_to_all_models': when on, the tool puts EVERY model entry's avatar back to the default Open WebUI logo instead of assigning one. Honors the dry-run valve above.",
        )
        auto_profile_image: bool = Field(
            default=True,
            description="Assign profile images when applying cards or creating workspace models, based on the model's name and vendor.",
        )
        overwrite_profile_image: bool = Field(
            default=False,
            description="Replace existing hand-picked avatars with a matching icon file. A generated monogram never replaces a hand-picked avatar, even with this on.",
        )
        generate_avatar_fallback: bool = Field(
            default=True,
            description=(
                "When no usable icon file matches a model, generate a PNG monogram avatar "
                "(vendor initials on a colour derived from the vendor name) instead of "
                "leaving the default Open WebUI logo. Works without any profile_image_dir."
            ),
        )
        profile_image_dir: str = Field(
            default="",
            description=(
                "Path to a directory of vendor icons (PNG/JPEG/WebP/GIF — Open WebUI rejects SVG avatars), "
                "as seen by the Open WebUI process (inside the container when using Docker). "
                "Name files by vendor tag, lowercase: qwen.png, google.jpg, mistral.webp… "
                "Subdirectories hold model-specific icons (mistral/nemo.png). "
                "Leave empty to rely on generated monograms only."
            )
            + _section("🧩 Auto-Configuration"),
        )

        # ── Auto-Configuration (what apply/create fill in) ───────────────
        auto_display_name: bool = Field(
            default=True,
            description="Generate human-readable display names (e.g. 'Qwen 3 VL 8B 1M (Q8_K_XL)').",
        )
        overwrite_display_name: bool = Field(
            default=False,
            description="Replace existing display names. Off only names models whose name still equals their raw ID.",
        )
        show_quant_in_name: bool = Field(
            default=True,
            description="Include the quantization level in generated display names, e.g. '(Q4_K_M)'. Off gives cleaner names.",
        )
        auto_tag: bool = Field(
            default=True,
            description="Tag models by detected capabilities (vision, tools, thinking, code…), vendor, provider and structure (MoE, long context, quantized…).",
        )
        overwrite_tags: bool = Field(
            default=False,
            description="Replace all existing tags with the detected ones. Off merges new tags into the existing list.",
        )
        auto_capabilities: bool = Field(
            default=True,
            description="Set capability flags (vision on/off, usage reporting, citations) from Ollama metadata.",
        )
        overwrite_capabilities: bool = Field(
            default=False,
            description="Replace existing capability flags. Off only fills in missing flags.",
        )
        auto_params: bool = Field(
            default=True,
            description="Set inference parameters (num_ctx, temperature, stop tokens, function_calling, reasoning_tags…) from the Ollama Modelfile.",
        )
        overwrite_params: bool = Field(
            default=False,
            description="Replace existing params with the detected ones. Off only fills in missing params.",
        )
        auto_system_prompt: bool = Field(
            default=True,
            description="Populate the system prompt from the Ollama Modelfile.",
        )
        overwrite_system_prompt: bool = Field(
            default=False,
            description="Replace existing system prompts with the Modelfile's. Off only sets prompts for models that have none.",
        )
        auto_suggestion_prompts: bool = Field(
            default=True,
            description="Generate capability-aware suggestion prompts (clickable starters on new chats).",
        )
        overwrite_suggestion_prompts: bool = Field(
            default=False,
            description="Replace existing suggestion prompts with generated ones."
            + _section("🙈 Special Model Handling"),
        )

        # ── Special Model Handling ───────────────────────────────────────
        auto_hide_embedding: bool = Field(
            default=True,
            description="Hide embedding-only models from the chat model selector.",
        )
        auto_disable_sharded: bool = Field(
            default=False,
            description=(
                "Deactivate full-precision (F16/BF16/F32) vision models, which older Ollama "
                "releases could not load because their projector files were sharded. Only "
                "applies when Ollama does not report native capabilities (i.e. old Ollama); "
                "current releases load these fine, so this is off by default."
            )
            + _section("📣 Status Output"),
        )

        # ── Status Output ────────────────────────────────────────────────
        show_status_updates: bool = Field(
            default=True,
            description="Show progress in the chat UI while processing. Off runs silently.",
        )

    # ------------------------------------------------------------------
    # Suggestion prompt templates (keyed by capability)
    # ------------------------------------------------------------------

    _SUGGESTION_PROMPTS = {
        "vision": [
            {
                "content": "Describe this image in detail.",
                "title": ["Analyze Image", "Get a detailed visual description"],
            },
            {
                "content": "What text can you read in this image?",
                "title": ["Read Text", "Extract text from an image"],
            },
            {
                "content": "Compare these two images and describe the differences.",
                "title": ["Compare Images", "Spot the differences between images"],
            },
        ],
        "thinking": [
            {
                "content": "Walk me through solving this problem step by step: ",
                "title": ["Solve Problem", "Get a detailed step-by-step solution"],
            },
            {
                "content": "Analyze the pros and cons of ",
                "title": ["Weigh Options", "Get a balanced analysis of tradeoffs"],
            },
            {
                "content": "What are all the possible approaches to ",
                "title": ["Explore Approaches", "Think through every angle"],
            },
        ],
        "code": [
            {
                "content": "Write a Python function that ",
                "title": ["Write Code", "Generate a Python function"],
            },
            {
                "content": "Review this code for bugs and suggest improvements:\n```\n\n```",
                "title": ["Code Review", "Find bugs and get improvements"],
            },
            {
                "content": "Explain this code step by step:\n```\n\n```",
                "title": ["Explain Code", "Understand what code does"],
            },
        ],
        "tools": [
            {
                "content": "Search the web for the latest information about ",
                "title": ["Web Search", "Find up-to-date information online"],
            },
            {
                "content": "Look up and summarize the key facts about ",
                "title": ["Research Topic", "Gather and synthesize information"],
            },
        ],
        "general": [
            {
                "content": "Explain the concept of ",
                "title": ["Learn Something", "Get a clear explanation of any topic"],
            },
            {
                "content": "Summarize the following text:\n\n",
                "title": ["Summarize", "Get a concise summary of any text"],
            },
            {
                "content": "Help me brainstorm ideas for ",
                "title": ["Brainstorm", "Generate creative ideas together"],
            },
            {
                "content": "Compare and contrast ",
                "title": ["Compare", "Analyze similarities and differences"],
            },
        ],
    }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Supported image extensions for profile image directory scanning
    _IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}

    def _scan_profile_image_dir(self) -> tuple[dict[str, Path], dict[str, Path]]:
        """
        Scan ``profile_image_dir`` for image files.

        Scans the root directory **and** any immediate subdirectories.
        Subdirectories hold model-specific icons for a vendor::

            profile_images/
            ├── mistral.jpg              ← generic Mistral icon
            ├── mistral/                 ← model-specific Mistral icons
            │   ├── codestral.png
            │   ├── devstral.png
            │   ├── nemo.png
            │   └── mathstral.png
            └── qwen.png

        Results are cached per directory path so batch operations
        (e.g. ``apply_all_model_cards``) don't re-scan the filesystem
        for every model.

        Returns:
            A tuple ``(main_files, subdir_files)`` where each is a
            ``{stem_lower: Path}`` mapping.
        """
        dir_path = (self.valves.profile_image_dir or "").strip()
        if not dir_path:
            return {}, {}

        # Return cached result if the directory hasn't changed
        if (
            self._profile_image_cache is not None
            and self._profile_image_cache_dir == dir_path
        ):
            return self._profile_image_cache

        p = Path(dir_path)
        if not p.is_dir():
            return {}, {}

        main_files: dict[str, Path] = {}
        subdir_files: dict[str, Path] = {}

        for item in p.iterdir():
            if item.is_file() and item.suffix.lower() in self._IMAGE_EXTENSIONS:
                main_files[item.stem.lower()] = item
            elif item.is_dir():
                for f in item.iterdir():
                    if f.is_file() and f.suffix.lower() in self._IMAGE_EXTENSIONS:
                        subdir_files[f.stem.lower()] = f

        self._profile_image_cache = (main_files, subdir_files)
        self._profile_image_cache_dir = dir_path
        return main_files, subdir_files

    def _resolve_profile_image(
        self,
        model_id: str,
        architecture: str,
        families: list,
        tags: list[str],
    ) -> str:
        """Resolve a model's avatar; see :meth:`_resolve_profile_image_ex`."""
        return self._resolve_profile_image_ex(model_id, architecture, families, tags)[0]

    def _resolve_profile_image_ex(
        self,
        model_id: str,
        architecture: str,
        families: list,
        tags: list[str],
    ) -> tuple[str, bool]:
        """
        Resolve the profile image for a model based on its identity.

        Returns ``(data_uri, generated)`` where *generated* is True when the
        avatar is a monogram rather than a real icon file.

        Candidates are tried in priority order and the first one that
        yields an avatar Open WebUI accepts wins (SVG files and unreadable
        files are skipped, not fatal):

        1. **Vendor subdirectory files** (model-specific icons), exact match
           on any name segment.  ``mistral/nemo.png`` matches segment ``nemo``
           of ``mistral-nemo:12b``.
        2. **Main directory files** matched against name segments (prefix
           match, longest stem wins) and, for multi-word stems such as
           ``all-minilm``, against the whole base name.
        3. **Vendor tags** (exact match), e.g. tag ``NVIDIA`` → ``nvidia.png``.
        4. **Generated monogram** (``generate_avatar_fallback``).

        Architecture / family names are deliberately **not** checked
        because fine-tunes inherit another vendor's architecture.

        Returns a ``data:image/...`` URI, or empty string if nothing usable
        was found and generation is disabled.
        """
        main_files, subdir_files = self._scan_profile_image_dir()
        name_lower = model_id.lower()
        base_lower = name_lower.split("/")[-1].split(":")[0]
        segments = [s for s in re.split(r"[/:._\-]", name_lower) if len(s) >= 3]

        candidates: list[Path] = []

        # 1. model-specific icons in vendor subdirectories (exact segment)
        for seg in segments:
            if seg in subdir_files:
                candidates.append(subdir_files[seg])

        # 2. main directory: segment prefix match (longest stem first), then
        #    multi-word stems against the base name
        if main_files:
            main_stems = sorted(main_files.keys(), key=len, reverse=True)
            for seg in segments:
                for stem in main_stems:
                    if seg == stem or seg.startswith(stem):
                        candidates.append(main_files[stem])
            for stem in main_stems:
                if ("-" in stem or "_" in stem) and stem in base_lower:
                    candidates.append(main_files[stem])

        # 3. vendor tags
        for tag in tags:
            tl = tag.lower()
            if tl in main_files:
                candidates.append(main_files[tl])
            if tl in subdir_files:
                candidates.append(subdir_files[tl])

        seen = set()
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            uri = self._read_image_file(path)
            if uri:
                return uri, False

        # 4. generated monogram
        if self.valves.generate_avatar_fallback:
            return self._generate_avatar(model_id, tags), True
        return "", False

    # Marker stored in meta so a generated monogram is never mistaken for a
    # hand-picked avatar: it may be replaced by a real icon later, and a
    # hand-picked avatar is never replaced by a monogram.
    _GENERATED_FLAG = "profile_image_generated"

    @staticmethod
    def _sniff_image_mime(data: bytes, filename: str) -> str:
        """
        Determine an image MIME type from file *content*, falling back to
        the extension.  Files are frequently saved with the wrong extension
        (an SVG named ``.jpg``), and a mismatched ``data:`` MIME makes the
        browser refuse to render the avatar.
        """
        head = data[:512]
        if head.startswith(b"\u0089PNG\r\n\u001a\n"):
            return "image/png"
        if head.startswith(b"\u00ff\u00d8\u00ff"):
            return "image/jpeg"
        if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
            return "image/gif"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image/webp"
        stripped = head.lstrip(b"\u00ef\u00bb\u00bf \t\r\n")
        if stripped.startswith(b"<") and b"<svg" in head.lower():
            return "image/svg+xml"
        return mimetypes.guess_type(filename)[0] or "image/png"

    def _read_image_file(self, filepath: Path) -> str:
        """
        Read an image file and return it as a ``data:image/...;base64,...``
        URI that Open WebUI will accept, or ``""`` if it cannot be used.

        Open WebUI only stores PNG/JPEG/GIF/WebP data URIs (SVG is rejected
        for security) and may enforce a size cap, so the MIME type is sniffed
        from the bytes and oversized images are downscaled with Pillow.
        """
        try:
            data = filepath.read_bytes()
        except Exception:
            log.warning(f"Failed to read profile image: {filepath}")
            return ""
        mime = self._sniff_image_mime(data, filepath.name)
        if mime not in PROFILE_IMAGE_ALLOWED_MIME_TYPES:
            log.info(
                f"Skipping {filepath.name}: {mime} is not an accepted avatar type "
                "(Open WebUI accepts PNG/JPEG/GIF/WebP)"
            )
            return ""
        uri = f"data:{mime};base64,{base64.b64encode(data).decode()}"
        cap = PROFILE_IMAGE_MAX_DATA_URI_SIZE
        if cap and len(uri) > cap:
            uri = self._shrink_image_to_cap(data, cap)
        return uri

    @staticmethod
    def _shrink_image_to_cap(data: bytes, cap: int) -> str:
        """Downscale an image until its PNG data URI fits under *cap* bytes."""
        try:
            from PIL import Image
        except Exception:
            return ""
        try:
            img = Image.open(io.BytesIO(data)).convert("RGBA")
            size = 256
            while size >= 32:
                buf = io.BytesIO()
                img.resize((size, size)).save(buf, format="PNG", optimize=True)
                uri = (
                    "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
                )
                if len(uri) <= cap:
                    return uri
                size //= 2
        except Exception as e:
            log.debug(f"Could not shrink avatar image: {e}")
        return ""

    # Tags that describe capabilities/sources rather than a vendor — never
    # used as the label of a generated avatar.
    _NON_VENDOR_TAGS = {
        "completion",
        "vision",
        "tools",
        "thinking",
        "embedding",
        "code",
        "insert",
        "moe",
        "instruct",
        "chat",
        "reasoning",
        "long context",
        "abliterated",
        "huggingface",
        "quantized",
        "unsloth",
        "bartowski",
        "mradermacher",
        "lm studio",
        "turboderp",
        "mlx community",
        "huihui ai",
    }

    def _generate_avatar(self, model_id: str, tags: list[str]) -> str:
        """
        Build a deterministic monogram avatar as a PNG data URI.

        The label is the vendor tag's initials when one was detected
        (``DeepReinforce`` → ``DR``), otherwise the first two letters of
        the model's base name.  The background hue is derived from that
        source so every model from one vendor shares a colour.  Returns
        ``""`` if Pillow is unavailable.
        """
        vendor = next((t for t in tags if t.lower() not in self._NON_VENDOR_TAGS), None)
        if vendor:
            words = re.findall(r"[A-Za-z0-9]+", vendor)
            if len(words) >= 2:
                label = (words[0][0] + words[1][0]).upper()
            else:
                w = words[0] if words else vendor
                caps = [c for c in w if c.isupper()]
                label = (caps[0] + caps[1]) if len(caps) >= 2 else w[:2].capitalize()
            seed = vendor.lower()
        else:
            base = model_id.split("/")[-1].split(":")[0]
            letters = re.sub(r"[^A-Za-z0-9]", "", base)
            label = letters[:2].capitalize() if letters else "AI"
            seed = base.lower()

        try:
            from PIL import Image, ImageDraw, ImageFont
        except Exception:
            log.warning("Pillow not available — cannot generate avatar images")
            return ""

        hue = int(hashlib.sha1(seed.encode("utf-8")).hexdigest()[:4], 16) % 360
        # hsl(hue, 48%, 38%) -> rgb
        import colorsys

        r, g, b = colorsys.hls_to_rgb(hue / 360.0, 0.38, 0.48)
        bg = (int(r * 255), int(g * 255), int(b * 255), 255)

        size = 128
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=26, fill=bg)
        try:
            font = ImageFont.load_default(size=56)
        except TypeError:  # Pillow < 10.1 has no size argument
            font = ImageFont.load_default()
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        tw, th = right - left, bottom - top
        draw.text(
            ((size - tw) / 2 - left, (size - th) / 2 - top),
            label,
            font=font,
            fill=(255, 255, 255, 255),
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    # Regex for detecting quantization-level strings
    _QUANT_RE = re.compile(r"^(UD[-_])?(Q\d|IQ\d|F16|F32|BF16|FP16)", re.IGNORECASE)

    def _is_quant_tag(self, tag: str) -> bool:
        """Return True if *tag* looks like a quantization level (Q8_K_XL, BF16, etc.)."""
        return bool(self._QUANT_RE.match(tag))

    def _extract_quant_from_ollama_tag(self, tag: str) -> Optional[str]:
        """
        Extract quantization from a mixed Ollama tag like ``26b-a4b-it-q4_K_M``.

        Returns the quant segment (e.g. ``q4_K_M``) or None.
        """
        segments = tag.split("-")
        for seg in reversed(segments):
            if self._QUANT_RE.match(seg):
                return seg
        return None

    # Matches size tokens: 7b, 14b, 0.6b, 128k, 568m, 3.8b, a3b, a4b, e4b
    _SIZE_RE = re.compile(r"^[a-z\d.]*\d+[bkmBKM]$", re.IGNORECASE)

    # Words that are acronyms and should always be fully uppercased,
    # even when they appear as all-lowercase and are longer than 2 chars.
    _UPPER_WORDS = {"qat", "gguf", "vl", "rl", "dpo", "sft", "gpt", "oss"}

    # Mixed-case acronyms that need specific casing (not all-upper).
    _CASED_WORDS = {"moe": "MoE"}

    def _smart_title(self, name: str) -> str:
        """
        Capitalise a model name intelligently.

        * Already-cased words are preserved (``VL``, ``DeepSeek``).
        * Known acronyms are uppercased (``qat`` → ``QAT``).
        * Mixed-case acronyms are corrected (``moe`` → ``MoE``).
        * All-lowercase words ≤2 chars are uppercased (``it`` → ``IT``).
        * Size tokens are uppercased (``7b`` → ``7B``, ``128k`` → ``128K``).
        * All-lowercase words >2 chars are title-cased (``instruct`` → ``Instruct``).
        """
        words = name.split()
        result = []
        for w in words:
            if w == w.lower():
                if w.lower() in self._CASED_WORDS:
                    result.append(self._CASED_WORDS[w.lower()])
                elif w.lower() in self._UPPER_WORDS:
                    result.append(w.upper())
                elif self._SIZE_RE.match(w):
                    # 7b → 7B, 128k → 128K, a3b → A3B
                    result.append(w.upper())
                elif len(w) <= 2 and w.isalpha():
                    result.append(w.upper())
                else:
                    result.append(w.capitalize())
            else:
                result.append(w)
        return " ".join(result)

    def _generate_display_name(
        self, model_id: str, show_info: Optional[dict], tags_info: dict
    ) -> str:
        """
        Generate a human-readable display name from a model ID.

        Examples:
            hf.co/unsloth/Qwen3-VL-8B-Instruct-1M-GGUF:Q8_K_XL
            → Qwen3 VL 8B Instruct 1M (Q8_K_XL)

            gemma4:26b-a4b-it-q4_K_M
            → Gemma4 26B A4B IT (Q4_K_M)

            nomic-embed-text:latest
            → Nomic Embed Text
        """
        name = model_id
        quant_suffix = ""

        # ---- HuggingFace models: hf.co/user/repo:tag ----
        if name.startswith("hf.co/"):
            parts = name.split("/", 2)  # ['hf.co', 'user', 'repo:tag']
            if len(parts) >= 3:
                name = parts[2]

            # Split off the tag
            if ":" in name:
                repo, tag = name.rsplit(":", 1)
                if self._is_quant_tag(tag):
                    quant_suffix = tag
                name = repo

            # Strip -GGUF / -gguf suffix
            name = re.sub(r"[-_][Gg][Gg][Uu][Ff]$", "", name)

        # ---- user/model format (e.g. huihui_ai/model:tag) ----
        elif "/" in name:
            # Strip the user prefix
            name = name.split("/", 1)[1]
            if ":" in name:
                base, tag = name.rsplit(":", 1)
                quant = self._extract_quant_from_ollama_tag(tag)
                if quant:
                    quant_suffix = quant
                    # Strip quant from tag, keep remaining info
                    remaining = tag[: tag.lower().rfind(quant.lower())].rstrip("-")
                    name = f"{base}-{remaining}" if remaining else base
                elif tag != "latest":
                    name = f"{base}-{tag}"
                else:
                    name = base

        # ---- Ollama library models: model:tag ----
        elif ":" in name:
            base, tag = name.rsplit(":", 1)
            if tag == "latest":
                name = base
            else:
                quant = self._extract_quant_from_ollama_tag(tag)
                if quant:
                    quant_suffix = quant
                    remaining = tag[: tag.lower().rfind(quant.lower())].rstrip("-")
                    name = f"{base} {remaining}" if remaining else base
                else:
                    name = f"{base} {tag}"

        # ---- Clean up the name ----
        # Convert digit_digit patterns to decimals (2_6 → 2.6) but not quant-like
        name = re.sub(r"(\d)_(\d)", r"\1.\2", name)
        # Replace hyphens and remaining underscores with spaces
        name = name.replace("-", " ").replace("_", " ")
        # Collapse multiple spaces
        name = re.sub(r"\s+", " ", name).strip()
        # Split version numbers from model names:
        # gemma4 → gemma 4, phi3.5 → phi 3.5, granite3.1 → granite 3.1
        # Requires 2+ alpha chars so single-letter prefixes (v2) stay intact.
        name = re.sub(r"\b([a-zA-Z]{2,})(\d[\d.]*)\b", r"\1 \2", name)
        # Strip noise words that add no value to the display name
        name = re.sub(r"\binstruct\b", "", name, flags=re.IGNORECASE)
        name = re.sub(r"\s+", " ", name).strip()
        # Smart capitalisation
        name = self._smart_title(name)

        # Fall back to show_info quantization_level when the tag doesn't
        # provide quant info (e.g. ":latest" models)
        if not quant_suffix and show_info:
            show_details = show_info.get("details", {})
            ql = show_details.get("quantization_level", "")
            if ql and ql.lower() != "unknown":
                quant_suffix = ql

        # Normalise quant suffix to uppercase (q4_K_M → Q4_K_M)
        if quant_suffix and self.valves.show_quant_in_name:
            quant_suffix = quant_suffix.upper()
            name = f"{name} ({quant_suffix})"

        return name

    def _generate_suggestion_prompts(self, capabilities: list) -> list:
        """
        Generate suggestion prompts based on detected model capabilities.

        Returns a list of ``{content, title}`` dicts ready for
        ``meta.suggestion_prompts``.
        """
        prompts: list[dict] = []
        # Add capability-specific prompts
        for cap in ("vision", "thinking", "code", "tools"):
            if cap in capabilities:
                prompts.extend(self._SUGGESTION_PROMPTS.get(cap, []))
        # Always add general prompts for non-embedding chat models
        if "completion" in capabilities:
            prompts.extend(self._SUGGESTION_PROMPTS.get("general", []))
        return prompts

    def _is_admin(self, __user__: dict) -> bool:
        """Check if user has admin role."""
        if not __user__:
            return False
        return __user__.get("role") == "admin"

    async def _emit_status(
        self,
        __event_emitter__,
        description: str,
        done: bool = False,
    ):
        """Emit a status update event (gated by ``show_status_updates`` valve)."""
        if __event_emitter__ and self.valves.show_status_updates:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": description,
                        "done": done,
                    },
                }
            )

    # ------------------------------------------------------------------
    # Shared validation / fetch / build helpers (DRY)
    # ------------------------------------------------------------------

    def _validate_tool_access(
        self,
        tool_name: str,
        valve_enabled: bool,
        __user__: dict,
        __request__,
        model: str = None,
    ) -> Optional[str]:
        """
        Common guard checks for all tool methods.

        Returns an error JSON string if validation fails, or ``None`` if OK.
        """
        if not valve_enabled:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"The '{tool_name}' tool is disabled. Enable it in the tool's Valves settings.",
                }
            )
        if not self._is_admin(__user__):
            return json.dumps(
                {"status": "error", "error": "Access denied. Admin role required."}
            )
        if model is not None and not model:
            return json.dumps({"status": "error", "error": "Model name is required."})
        if __request__ is None:
            return json.dumps(
                {"status": "error", "error": "Request context not available."}
            )
        return None

    async def _get_ollama_models(self, __request__) -> dict:
        """Fetch ``OLLAMA_MODELS`` from app state, refreshing if needed."""
        ollama_models = getattr(__request__.app.state, "OLLAMA_MODELS", None)
        if not ollama_models:
            from open_webui.routers.ollama import get_all_models

            await get_all_models(__request__)
            ollama_models = getattr(__request__.app.state, "OLLAMA_MODELS", {})
        return ollama_models or {}

    # ------------------------------------------------------------------
    # Dry-run helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _dry_prefix(dry_run: bool) -> str:
        return "[DRY RUN] " if dry_run else ""

    def _summarize_value(self, key: str, value):
        """Short, LLM-friendly summary of a model field for dry-run diffs."""
        if value is None or value == "" or value == [] or value == {}:
            return None
        if key == "profile_image_url":
            if self._is_default_avatar(value):
                return None
            if isinstance(value, str) and value.startswith("data:"):
                mime = value[5:].split(";", 1)[0]
                return f"{mime} ({len(value) * 3 // 4 // 1024} KB)"
            return str(value)[:80]
        if key == "tags":
            names = [t.get("name") if isinstance(t, dict) else t for t in value]
            return [n for n in names if n]
        if key == "suggestion_prompts":
            return f"{len(value)} prompt(s)"
        if key in ("description", "params.system"):
            text = str(value)
            return text[:80] + ("…" if len(text) > 80 else "")
        return value

    _GENERATED_STAMP_RE = re.compile(r"\*Generated [^*]+\*")

    def _describe_changes(self, existing, form) -> dict:
        """
        Field-by-field diff between an existing DB entry and the form that
        would be written: ``{field: {"from": ..., "to": ...}}``.

        An empty dict means the write would be a no-op.  For a new entry
        (*existing* is ``None``) every non-empty field is listed with
        ``"from": None``.
        """
        old_meta = existing.meta.model_dump() if existing and existing.meta else {}
        new_meta = form.meta.model_dump() if form.meta else {}
        old_params = (
            existing.params.model_dump() if existing and existing.params else {}
        )
        new_params = form.params.model_dump() if form.params else {}
        changes = {}

        def _norm(field, value):
            # Compare RAW values (summaries are lossy); only empties and the
            # default avatar are folded to None.
            if value is None or value == "" or value == [] or value == {}:
                return None
            if field == "profile_image_url" and self._is_default_avatar(value):
                return None
            return value

        def _cmp(field, old, new):
            if _norm(field, old) == _norm(field, new):
                return
            entry = {
                "from": self._summarize_value(field, old),
                "to": self._summarize_value(field, new),
            }
            if field == "description" and old and new:
                strip = lambda t: self._GENERATED_STAMP_RE.sub("", str(t)).strip()
                if strip(old) == strip(new):
                    entry["note"] = "only the 'Generated …' timestamp differs"
            changes[field] = entry

        _cmp("name", existing.name if existing else None, form.name)
        _cmp(
            "base_model_id",
            existing.base_model_id if existing else None,
            form.base_model_id,
        )
        if existing is not None:
            _cmp("is_active", existing.is_active, form.is_active)
        for key in sorted(set(old_meta) | set(new_meta)):
            _cmp(key, old_meta.get(key), new_meta.get(key))
        for key in sorted(set(old_params) | set(new_params)):
            _cmp(f"params.{key}", old_params.get(key), new_params.get(key))
        return changes

    def _build_model_meta(self, result: dict, existing=None) -> dict:
        """
        Build a ``meta`` dict from a generate result.

        When *existing* is provided (a ``ModelModel``), merge behaviour is
        controlled by the overwrite valves.  When *existing* is ``None``
        (new model), values are set directly.
        """
        card = result["model_card"]
        existing_meta = (
            (existing.meta.model_dump() if existing and existing.meta else {})
            if existing
            else {}
        )

        # Start with existing meta (preserves fields we don't touch) or fresh
        meta_dict = {**existing_meta} if existing else {}
        meta_dict["description"] = card

        # --- Tags ---
        auto_tags = result.get("tags", [])
        if self.valves.auto_tag and auto_tags:
            if existing and not self.valves.overwrite_tags:
                existing_tags = meta_dict.get("tags", []) or []
                existing_tag_names = set()
                for t in existing_tags:
                    if isinstance(t, dict):
                        existing_tag_names.add(t.get("name", "").lower())
                    elif isinstance(t, str):
                        existing_tag_names.add(t.lower())
                for tag in auto_tags:
                    if tag.lower() not in existing_tag_names:
                        existing_tags.append({"name": tag})
                meta_dict["tags"] = existing_tags
            else:
                meta_dict["tags"] = [{"name": tag} for tag in auto_tags]

        # --- Capabilities ---
        meta_caps = result.get("meta_capabilities", {})
        if self.valves.auto_capabilities and meta_caps:
            if existing and not self.valves.overwrite_capabilities:
                existing_caps = meta_dict.get("capabilities") or {}
                meta_dict["capabilities"] = {**meta_caps, **existing_caps}
            else:
                meta_dict["capabilities"] = meta_caps

        # --- Hide embedding models ---
        if self.valves.auto_hide_embedding and "embedding" in result.get(
            "capabilities", []
        ):
            meta_dict["hidden"] = True

        # --- Suggestion prompts ---
        suggestion_prompts = result.get("suggestion_prompts", [])
        if self.valves.auto_suggestion_prompts and suggestion_prompts:
            existing_prompts = (
                meta_dict.get("suggestion_prompts", []) if existing else []
            )
            if self.valves.overwrite_suggestion_prompts or not existing_prompts:
                meta_dict["suggestion_prompts"] = suggestion_prompts

        # --- Profile image ---
        profile_img = result.get("profile_image_url", "")
        img_generated = bool(result.get("profile_image_generated", False))
        if self.valves.auto_profile_image and profile_img:
            current_img = meta_dict.get("profile_image_url") if existing else None
            replaceable = (
                not existing
                or self._is_default_avatar(current_img)
                or bool(meta_dict.get(self._GENERATED_FLAG))
            )
            # A real icon may replace a default/generated avatar always, and a
            # custom avatar only when overwrite is on.  A generated monogram
            # never replaces a hand-picked avatar.
            if replaceable or (
                self.valves.overwrite_profile_image and not img_generated
            ):
                meta_dict["profile_image_url"] = profile_img
                if img_generated:
                    meta_dict[self._GENERATED_FLAG] = True
                else:
                    meta_dict.pop(self._GENERATED_FLAG, None)

        return meta_dict

    def _build_model_params(self, result: dict, existing=None) -> dict:
        """
        Build a ``params`` dict from a generate result.

        When *existing* is provided (a ``ModelModel``), merge behaviour is
        controlled by the overwrite valves.  When *existing* is ``None``
        (new model), values are set directly.
        """
        existing_params = (
            (existing.params.model_dump() if existing and existing.params else {})
            if existing
            else {}
        )
        params = {**existing_params}

        # --- Inference params ---
        if self.valves.auto_params:
            extracted = result.get("extracted_params", {})
            if extracted:
                if self.valves.overwrite_params:
                    # Extracted wins — but keep non-overlapping existing
                    params = {**existing_params, **extracted}
                else:
                    # Existing wins — extracted fills gaps
                    params = {**extracted, **params}

        # --- System prompt ---
        if self.valves.auto_system_prompt:
            sys_prompt = result.get("system_prompt", "")
            if sys_prompt:
                if self.valves.overwrite_system_prompt or not params.get("system"):
                    params["system"] = sys_prompt

        return params

    async def _resolve_api_config(
        self, request, idx: int, url: str
    ) -> tuple[dict, list]:
        """Look up the API config for a backend by its index or URL.

        Returns ``(api_config_dict, ollama_base_urls)``.
        """
        from open_webui.models.config import Config

        ollama_config = await Config.get_many("ollama.base_urls", "ollama.api_configs")
        ollama_base_urls = ollama_config.get("ollama.base_urls") or []
        api_configs = ollama_config.get("ollama.api_configs") or {}
        return api_configs.get(str(idx), api_configs.get(url, {})), ollama_base_urls

    def _get_api_key(self, api_config: dict) -> Optional[str]:
        """Extract the API key from an api_config dict."""
        return api_config.get("key", None)

    async def _resolve_ollama_endpoint(
        self, __request__, model_id: str, model_info: dict
    ) -> tuple[Optional[str], dict, str, Optional[str]]:
        """
        Resolve which Ollama instance serves *model_id*.

        Open WebUI records the connection index for each model in
        ``model_info["urls"]``; the base URL and per-connection settings
        (API key, ``prefix_id``) live in the app config.  Connection
        settings may be keyed by index *or* by URL depending on the Open
        WebUI version, so both are tried.

        Returns ``(url, headers, show_name, error)``.  When ``error`` is a
        string the other values are unusable.
        """
        url_indices = model_info.get("urls", [])
        if not url_indices:
            return (
                None,
                {},
                model_id,
                f"No Ollama instance found for model '{model_id}'.",
            )

        url_idx = url_indices[0]
        api_config, ollama_base_urls = await self._resolve_api_config(
            __request__, url_idx, ""
        )
        url = ollama_base_urls[url_idx] if url_idx < len(ollama_base_urls) else None
        if not url:
            return None, {}, model_id, f"Ollama URL index {url_idx} out of range."

        # Retry the lookup keyed by URL in case the config is stored that way.
        if not api_config:
            api_config, _ = await self._resolve_api_config(__request__, url_idx, url)

        headers = {}
        key = self._get_api_key(api_config)
        if key:
            headers["Authorization"] = f"Bearer {key}"

        show_name = self._strip_prefix(model_id, api_config.get("prefix_id"))
        return url, headers, show_name, None

    def _strip_prefix(self, model_name: str, prefix_id: Optional[str]) -> str:
        """Remove the prefix_id from a model name if present."""
        if prefix_id and model_name.startswith(f"{prefix_id}."):
            return model_name[len(f"{prefix_id}.") :]
        return model_name

    def _format_size(self, size_bytes: int) -> str:
        """Format bytes to human-readable size."""
        if size_bytes == 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        i = 0
        size = float(size_bytes)
        while size >= 1024 and i < len(units) - 1:
            size /= 1024
            i += 1
        return f"{size:.1f} {units[i]}"

    def _format_number(self, n) -> str:
        """Format large numbers with commas."""
        if isinstance(n, (list, tuple)):
            n = n[0] if n else 0
        return f"{int(n):,}"

    def _scalar(self, value):
        """Extract a scalar from a model_info value that may be a list."""
        if isinstance(value, (list, tuple)):
            return value[0] if value else None
        return value

    async def _get_ollama_show(
        self, url: str, model_name: str, headers: dict
    ) -> Optional[dict]:
        """Call Ollama /api/show to get detailed model info."""
        try:
            session = await get_session()
            async with session.post(
                f"{url}/api/show",
                headers={**headers, "Content-Type": "application/json"},
                json={"model": model_name, "verbose": True},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    log.debug(f"/api/show for {model_name} returned {response.status}")
                    return None
        except Exception as e:
            log.debug(f"/api/show failed for {model_name}: {e}")
            return None

    _CODE_NAME_KEYWORDS = (
        "coder",
        "codestral",
        "devstral",
        "codegeex",
        "starcoder",
        "-code",
        "code-",
    )

    def _detect_capabilities(
        self,
        architecture: str,
        families: list,
        model_info: dict,
        show_info: Optional[dict],
        model_id: str = "",
    ) -> list[str]:
        """
        Detect model capabilities from metadata.

        Prefers Ollama's native ``capabilities`` field (available in newer
        Ollama versions). Falls back to heuristic detection for older versions.

        Returns a list of capability strings like
        ['vision', 'tools', 'completion', 'thinking'].
        """
        # --- Prefer Ollama's native capabilities field ---
        ollama_caps = (show_info or {}).get("capabilities", [])
        if ollama_caps and isinstance(ollama_caps, list):
            capabilities = list(ollama_caps)
            # Older Ollama releases don't report 'thinking' — fall back to
            # the chat template.
            template = (show_info or {}).get("template", "")
            if template and ("<think>" in template or "thinking" in template.lower()):
                if "thinking" not in capabilities:
                    capabilities.append("thinking")
            # Code capability via family or the model's own name
            families_lower = [
                f.lower()
                for f in (families or [architecture.lower()] if architecture else [])
            ]
            name_lower = (model_id or "").lower()
            is_code = any("code" in f for f in families_lower) or any(
                kw in name_lower for kw in self._CODE_NAME_KEYWORDS
            )
            if is_code and "code" not in capabilities:
                capabilities.append("code")
            return capabilities

        # --- Heuristic fallback for older Ollama versions ---
        capabilities = []

        # Check for vision capability via model_info keys
        has_vision_keys = any(
            "vision" in key.lower() or "image" in key.lower() or "clip" in key.lower()
            for key in model_info
        )
        if has_vision_keys:
            capabilities.append("vision")

        # Check for embedding capability
        is_embedding = (
            any(
                kw in architecture.lower()
                for kw in ["bert", "nomic", "embed", "bge", "e5"]
            )
            if architecture
            else False
        )
        if is_embedding:
            capabilities.append("embedding")

        # Check for tool/function calling capability via template
        template = (show_info or {}).get("template", "")
        if template:
            if (
                ".ToolCalls" in template
                or "tool_calls" in template
                or "<tool_call>" in template
            ):
                capabilities.append("tools")

        # Completion is always a capability unless it's purely an embedding model
        if not is_embedding:
            capabilities.append("completion")

        # Check for thinking/reasoning capability
        if template and ("<think>" in template or "thinking" in template.lower()):
            capabilities.append("thinking")

        # Code capability via family or the model's own name
        families_lower = [
            f.lower()
            for f in (families or [architecture.lower()] if architecture else [])
        ]
        name_lower = (model_id or "").lower()
        if any("code" in f for f in families_lower) or any(
            kw in name_lower for kw in self._CODE_NAME_KEYWORDS
        ):
            capabilities.append("code")

        return capabilities

    def _estimate_max_context(
        self,
        model_info: Optional[dict],
        model_weight_bytes: int,
    ) -> Optional[int]:
        """
        Estimate the maximum affordable context length for a model
        given the user's VRAM budget.

        Uses the model's GGUF architecture metadata (block_count,
        head_count_kv, embedding_length, head_count) to calculate
        KV cache bytes per token, then derives how much context fits
        in the remaining VRAM after model weights, OS reservation,
        parallel slot count, and a safety margin for GPU runtime
        overhead (activation buffers, allocator fragmentation, etc.).

        Returns ``None`` if VRAM-aware capping is disabled or if
        there is insufficient metadata to calculate.
        """
        vram_total = self.valves.gpu_vram_gib
        if vram_total <= 0 or not model_info or model_weight_bytes <= 0:
            return None

        vram_reserve = self.valves.vram_reserve_gib

        # MoE models have higher compute overhead due to expert routing
        # buffers, gating networks, and larger scratch allocations.
        # Auto-detect MoE from GGUF metadata and bump overhead.
        is_moe = any("expert" in k.lower() for k in (model_info or {}))
        base_overhead_pct = self.valves.weight_overhead_pct
        if is_moe:
            overhead_pct = max(base_overhead_pct, base_overhead_pct + 10)
        else:
            overhead_pct = base_overhead_pct

        weight_overhead = 1.0 + (overhead_pct / 100.0)
        effective_weight_bytes = model_weight_bytes * weight_overhead
        vram_budget_bytes = (vram_total - vram_reserve) * (1024**3)
        vram_for_kv = vram_budget_bytes - effective_weight_bytes

        if vram_for_kv <= 0:
            # Model weights + overhead exceed the budget; fall back to a
            # minimal context so the model can still partially load.
            return 8192

        # Extract architecture params from GGUF metadata
        block_count = None
        head_count_kv = None
        head_count = None
        embedding_length = None

        for key in model_info:
            kl = key.lower()
            if "block_count" in kl and block_count is None:
                block_count = self._scalar(model_info[key])
            elif "head_count_kv" in kl and head_count_kv is None:
                head_count_kv = self._scalar(model_info[key])
            elif "head_count" in kl and "kv" not in kl and head_count is None:
                head_count = self._scalar(model_info[key])
            elif "embedding_length" in kl and embedding_length is None:
                embedding_length = self._scalar(model_info[key])

        if not block_count or not embedding_length or not head_count:
            return None

        # head_dim = embedding_length / head_count
        # If head_count_kv is not set, assume GQA ratio of 1 (MHA)
        head_dim = embedding_length / head_count
        kv_heads = head_count_kv if head_count_kv else head_count

        # Bytes per element based on KV cache quantization
        kv_quant = self.valves.kv_cache_quant.lower().strip()
        bytes_per_elem = {"f16": 2.0, "q8_0": 1.0, "q4_0": 0.5}.get(kv_quant, 1.0)

        # KV cache per token per slot = 2 (K+V) * layers * kv_heads * head_dim * bytes
        kv_bytes_per_token = 2 * block_count * kv_heads * head_dim * bytes_per_elem

        if kv_bytes_per_token <= 0:
            return None

        # Each parallel slot allocates its own full KV cache, so the
        # total VRAM for context = num_ctx * kv_bytes_per_token * num_parallel
        num_parallel = max(self.valves.num_parallel, 1)
        kv_bytes_per_token_total = kv_bytes_per_token * num_parallel

        max_ctx = int(vram_for_kv / kv_bytes_per_token_total)

        # Align down to nearest 1024 for clean values
        max_ctx = (max_ctx // 1024) * 1024

        return max(max_ctx, 8192)

    def _extract_params(
        self,
        show_info: Optional[dict],
        model_info: Optional[dict] = None,
        model_weight_bytes: int = 0,
    ) -> dict:
        """
        Extract inference parameters from Ollama /api/show response.

        Parses the ``parameters`` string into a dict matching Open WebUI's
        ModelParams field names.  Falls back to ``model_info`` GGUF metadata
        for ``num_ctx`` when it's not set in the Modelfile.

        When ``gpu_vram_gib`` is configured, caps ``num_ctx`` to the maximum
        context that fits in VRAM after model weights and OS reservation,
        calculated from the model's architecture metadata.
        """
        if not show_info:
            return {}

        params = {}

        # Parse the parameters string (newline-separated key value pairs)
        parameters_str = show_info.get("parameters", "")
        param_map = {
            "temperature": float,
            "top_p": float,
            "top_k": int,
            "num_ctx": int,
            "num_predict": int,
            "repeat_penalty": float,
            "repeat_last_n": int,
            "mirostat": int,
            "mirostat_eta": float,
            "mirostat_tau": float,
            "seed": int,
            "min_p": float,
            "typical_p": float,
            "num_batch": int,
            "num_keep": int,
            "frequency_penalty": float,
            "presence_penalty": float,
            "num_gpu": int,
            "num_thread": int,
        }
        stop_tokens = []

        if parameters_str and parameters_str.strip():
            for line in parameters_str.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                key, val = parts
                if key == "stop":
                    # Strip surrounding quotes
                    stop_tokens.append(val.strip('"').strip("'"))
                elif key in param_map:
                    try:
                        params[key] = param_map[key](val)
                    except (ValueError, TypeError):
                        pass

        if stop_tokens:
            params["stop"] = stop_tokens

        # Fall back to model_info context_length for num_ctx if not
        # explicitly set in the Modelfile parameters
        if "num_ctx" not in params and model_info:
            for key in model_info:
                if "context_length" in key.lower():
                    ctx = self._scalar(model_info[key])
                    if ctx and isinstance(ctx, (int, float)):
                        params["num_ctx"] = int(ctx)
                    break

        # VRAM-aware context capping: calculate the maximum context
        # this specific model can afford based on its weight size,
        # architecture, and the user's VRAM budget.
        if "num_ctx" in params:
            max_ctx = self._estimate_max_context(model_info, model_weight_bytes)
            if max_ctx is not None and params["num_ctx"] > max_ctx:
                log.info(
                    f"Capping num_ctx from {params['num_ctx']} to {max_ctx} "
                    f"(VRAM budget: {self.valves.gpu_vram_gib} GiB, "
                    f"model weights: {model_weight_bytes / (1024**3):.1f} GiB)"
                )
                params["num_ctx"] = max_ctx

        # Global context length override (valve) — wins over both the
        # Ollama-reported context and the VRAM-aware cap.
        if self.valves.context_length_override > 0:
            params["num_ctx"] = int(self.valves.context_length_override)

        # Derive max_tokens from num_predict or num_ctx.
        # Cap at 8192 to avoid reserving the entire context window for
        # generation — output rarely needs to match the full context.
        if "num_predict" in params:
            params["max_tokens"] = params["num_predict"]
        elif "num_ctx" in params:
            params["max_tokens"] = min(params["num_ctx"], 8192)

        return params

    def _build_meta_capabilities(
        self,
        capabilities: list[str],
        model_info: dict,
        show_info: Optional[dict],
    ) -> dict:
        """
        Build the ``meta.capabilities`` dict for Open WebUI.

        This controls actual UI behavior — whether image upload is shown,
        whether usage stats are requested, etc.
        """
        meta_caps = {}

        # Vision: true only if the model actually supports it
        meta_caps["vision"] = "vision" in capabilities

        # Usage reporting works for all Ollama models
        meta_caps["usage"] = True

        # Citations enabled by default for all models
        meta_caps["citations"] = True

        return meta_caps

    def _detect_tags(
        self,
        model_id: str,
        architecture: str,
        families: list,
        model_info: dict,
        show_info: Optional[dict],
        tags_info: dict,
    ) -> list[str]:
        """
        Detect all tags for a model — functional capabilities plus
        metadata-derived classifications.

        Returns a deduplicated list of tag strings.
        """
        tags = []

        # Start with functional capabilities
        caps = self._detect_capabilities(
            architecture, families, model_info, show_info, model_id
        )
        tags.extend(caps)

        name_lower = model_id.lower()

        # --- MoE (Mixture of Experts) ---
        arch_lower = architecture.lower() if architecture else ""
        is_moe = (
            "moe" in arch_lower
            or any("expert" in k.lower() for k in model_info)
            # Common naming patterns: 30B-A3B, 26B-A4B, etc.
            or bool(
                any(
                    p in name_lower
                    for p in ["-a2b", "-a3b", "-a4b", "-a1b", "-a5b", "-a6b", "-a8b"]
                )
            )
        )
        if is_moe:
            tags.append("moe")

        # --- Instruct ---
        if "instruct" in name_lower:
            tags.append("instruct")

        # --- Chat ---
        if "chat" in name_lower:
            tags.append("chat")

        # --- Reasoning ---
        # Check name for reasoning indicators (don't duplicate 'thinking' from caps)
        reasoning_keywords = ["reason", "-r1", "_r1", ":r1", "-r2"]
        if (
            any(kw in name_lower for kw in reasoning_keywords)
            and "thinking" not in caps
        ):
            tags.append("reasoning")

        # --- Code (from model name, broader than family detection) ---
        if ("code" in name_lower or "coder" in name_lower) and "code" not in caps:
            tags.append("code")

        # --- Vision (from model name, broader than model_info detection) ---
        vision_name_patterns = [
            "-vl-",
            "-vl:",
            "vision",
            "minicpm-v",
            "minicpm-o",
            "internvl",
        ]
        if any(p in name_lower for p in vision_name_patterns) and "vision" not in caps:
            tags.append("vision")

        # --- Long Context ---
        context_length = None
        for key in model_info:
            if "context_length" in key.lower():
                context_length = self._scalar(model_info[key])
                break
        if (
            context_length
            and isinstance(context_length, (int, float))
            and context_length >= 100_000
        ):
            tags.append("long context")

        # --- Abliterated / Uncensored ---
        if (
            "abliterat" in name_lower
            or "uncensor" in name_lower
            or "derestrict" in name_lower
        ):
            tags.append("abliterated")

        # --- HuggingFace ---
        if name_lower.startswith("hf.co/") or name_lower.startswith("huggingface.co/"):
            tags.append("huggingface")

        # --- Quantized ---
        details = tags_info.get("details", {})
        show_details = (show_info or {}).get("details", {})
        quant = (
            show_details.get("quantization_level")
            or details.get("quantization_level")
            or ""
        )
        if quant and quant.lower() not in ("", "none", "unknown"):
            tags.append("quantized")

        # --- Model Family / Vendor ---
        # Maps name patterns to the tag to apply.
        # Checked in order; first match wins per group.
        family_rules = [
            # (name_patterns, tag_name)
            (["gemma"], "Google"),
            (["gpt-oss", "gpt_oss"], "OpenAI"),
            (["muse-glimmer", "llama"], "Meta"),
            (["qwen"], "Qwen"),
            (["deepseek"], "DeepSeek"),
            (["phi3", "phi4", "phi-3", "phi-4", "phi:"], "Microsoft"),
            (["mistral", "ministral", "magistral", "devstral", "mathstral"], "Mistral"),
            (["granite"], "IBM"),
            (["nemotron"], "NVIDIA"),
            (["command-r", "command_r", "aya"], "Cohere"),
            (["olmo"], "AI2"),
            (["minicpm"], "OpenBMB"),
            (["internvl", "intern-vl"], "OpenGVLab"),
            (["snowflake", "arctic"], "Snowflake"),
            (["nomic"], "Nomic"),
            (["glm"], "GLM"),
            (["lfm"], "LiquidAI"),
            (["bge-"], "BAAI"),
            (["wizardlm"], "WizardLM"),
            (["starcoder", "starcode"], "BigCode"),
            (["falcon"], "TII"),
            (["yi-", "yi:"], "01.AI"),
            (["jamba"], "AI21"),
            (["vicuna"], "LMSYS"),
            (["solar"], "Upstage"),
            (["exaone"], "LG AI Research"),
            (["smollm"], "HuggingFace"),
            (["hermes"], "Nous Research"),
            (["laguna"], "Poolside"),
            (["ornith"], "DeepReinforce"),
            (["north-mini", "north_mini"], "Cohere"),
            (["apriel"], "ServiceNow"),
            (["seed-oss", "seed_oss"], "ByteDance"),
            (["k2-horizon", "k2_horizon"], "IFM"),
            (
                [
                    "inclusionai/",
                    "ling-1t",
                    "ling-mini",
                    "ling-lite",
                    "ling-plus",
                    "ling-flash",
                    "ling-3",
                    "ring-1t",
                    "ring-mini",
                    "ring-flash",
                ],
                "InclusionAI",
            ),
            (["kimi"], "Moonshot AI"),
            (["sarvam"], "Sarvam"),
            (["g9v3"], "AI9Stars"),
        ]
        for patterns, family_tag in family_rules:
            if any(p in name_lower for p in patterns):
                tags.append(family_tag)
                break  # One family tag per model

        # --- Source / Provider tags ---
        provider_rules = [
            # (prefix_patterns, tag_name)
            (["hf.co/unsloth/", "unsloth/"], "Unsloth"),
            (["hf.co/bartowski/", "bartowski/"], "Bartowski"),
            (["hf.co/openbmb/", "openbmb/"], "OpenBMB"),
            (["hf.co/huihui_ai/", "huihui_ai/"], "Huihui AI"),
            (["hf.co/mradermacher/", "mradermacher/"], "Mradermacher"),
            (["hf.co/lmstudio-community/", "lmstudio-community/"], "LM Studio"),
            (["hf.co/turboderp/", "turboderp/"], "Turboderp"),
            (["hf.co/mlx-community/", "mlx-community/"], "MLX Community"),
        ]
        for prefixes, provider_tag in provider_rules:
            if any(name_lower.startswith(p) for p in prefixes):
                tags.append(provider_tag)
                break  # One provider tag per model

        # --- Deduplicate, capitalize, preserve order ---
        seen = set()
        unique_tags = []
        for t in tags:
            key = t.lower()
            if key not in seen:
                seen.add(key)
                # Capitalize single-word lowercase tags (e.g. "vision" -> "Vision")
                # but preserve already-cased vendor names (e.g. "IBM", "AI2", "DeepSeek")
                if t == t.lower():
                    t = t.capitalize()
                unique_tags.append(t)

        return unique_tags

    def _build_model_card(
        self,
        model_id: str,
        tags_info: dict,
        show_info: Optional[dict],
        capped_ctx: Optional[int] = None,
    ) -> str:
        """
        Build a formatted model card string from Ollama API data.

        Combines data from /api/tags (in OLLAMA_MODELS) and /api/show
        to produce a comprehensive, human-readable model card.

        The output is **markdown** rendered by Open WebUI's frontend via
        ``marked.parse()`` + ``DOMPurify``.  It appears in two places:

        1. **Inline** below the model name (``line-clamp-2`` or ``3``) —
           only the first ~2 lines are visible, so lead with a summary.
        2. **Tooltip** on hover/tap — shows the *full* rendered card with
           no truncation.

        Supported markdown: bold, italic, code, links, headers, lists,
        blockquotes, tables, horizontal rules.  Raw HTML is stripped by
        ``sanitizeResponseContent`` before parsing.
        """
        details = tags_info.get("details", {})
        show_details = (show_info or {}).get("details", {})
        model_info = (show_info or {}).get("model_info", {})

        # --- Collect raw metadata ---
        architecture = show_details.get("family") or details.get("family") or ""
        families = show_details.get("families") or details.get("families")
        param_size = (
            show_details.get("parameter_size") or details.get("parameter_size") or ""
        )
        fmt = show_details.get("format") or details.get("format")

        context_length = None
        embedding_length = None
        block_count = None
        ff_length = None
        head_count = None
        head_count_kv = None
        vocab_size = None
        rope_freq = None

        for key in model_info:
            kl = key.lower()
            if "context_length" in kl and context_length is None:
                context_length = self._scalar(model_info[key])
            elif "embedding_length" in kl and embedding_length is None:
                embedding_length = self._scalar(model_info[key])
            elif "block_count" in kl and block_count is None:
                block_count = self._scalar(model_info[key])
            elif "feed_forward_length" in kl and ff_length is None:
                ff_length = self._scalar(model_info[key])
            elif "head_count_kv" in kl and head_count_kv is None:
                head_count_kv = self._scalar(model_info[key])
            elif "head_count" in kl and "kv" not in kl and head_count is None:
                head_count = self._scalar(model_info[key])
            elif "vocab_size" in kl and vocab_size is None:
                vocab_size = self._scalar(model_info[key])
            elif (
                "rope.freq_base" in kl or "rope_freq_base" in kl
            ) and rope_freq is None:
                rope_freq = self._scalar(model_info[key])

        quantization = (
            show_details.get("quantization_level")
            or details.get("quantization_level")
            or ""
        )
        if (not quantization or quantization.lower() == "unknown") and ":" in model_id:
            tag_part = model_id.rsplit(":", 1)[-1]
            if any(
                tag_part.upper().startswith(p)
                for p in ["Q", "IQ", "F1", "F3", "BF", "UD"]
            ):
                quantization = tag_part

        size = tags_info.get("size", 0)
        capabilities = self._detect_capabilities(
            architecture, families, model_info, show_info, model_id
        )
        template = (show_info or {}).get("template", "")

        # ================================================================
        # Build the card
        #
        # The frontend converts \n → <br> BEFORE marked.parse(), so
        # multi-line markdown (tables, horizontal rules, lists, headers)
        # will NOT render.  Only inline markdown works on each line:
        # **bold**, *italic*, `code`, [links](url), ~~strikethrough~~.
        # ================================================================
        lines = []

        # --- Line 1: Dense summary (visible in line-clamp-2/3) ---
        summary_parts = []
        if param_size:
            summary_parts.append(f"**{param_size}**")
        if architecture:
            summary_parts.append(f"`{architecture}`")
        if quantization and quantization.lower() != "unknown":
            summary_parts.append(f"`{quantization}`")
        if context_length:
            summary_parts.append(f"{self._format_number(context_length)} ctx")
        if size:
            summary_parts.append(self._format_size(size))

        if summary_parts:
            lines.append(" · ".join(summary_parts))
        else:
            lines.append(f"`{model_id}`")

        # --- Line 2: Capabilities ---
        if capabilities:
            lines.append(" · ".join(f"**{c}**" for c in capabilities))

        # --- Specifications ---
        specs = []

        general_name = model_info.get("general.name")
        if general_name:
            specs.append(f"**Name:** `{general_name}`")

        if architecture:
            arch_str = f"`{architecture}`"
            if families and len(families) > 1:
                arch_str += " (" + ", ".join(f"`{f}`" for f in families) + ")"
            specs.append(f"**Arch:** {arch_str}")

        parent_model = show_details.get("parent_model")
        if parent_model:
            specs.append(f"**Parent:** `{parent_model}`")

        if param_size:
            specs.append(f"**Params:** `{param_size}`")

        if fmt:
            specs.append(f"**Format:** `{fmt}`")

        if context_length:
            ctx_str = f"**Context:** `{self._format_number(context_length)}` tokens"
            if capped_ctx and capped_ctx < context_length:
                ctx_str += (
                    f" (⚠️ capped to `{self._format_number(capped_ctx)}` — VRAM limit)"
                )
            specs.append(ctx_str)

        if embedding_length:
            specs.append(
                f"**Embedding:** `{self._format_number(embedding_length)}` dims"
            )

        if block_count:
            specs.append(f"**Layers:** `{block_count}`")

        if head_count:
            hc_str = f"`{head_count}`"
            if head_count_kv:
                hc_str += f" (KV: `{head_count_kv}`)"
            specs.append(f"**Attn Heads:** {hc_str}")

        if ff_length:
            specs.append(f"**FFN Size:** `{self._format_number(ff_length)}`")

        if vocab_size:
            specs.append(f"**Vocab:** `{self._format_number(vocab_size)}`")

        if rope_freq:
            if isinstance(rope_freq, (int, float)):
                specs.append(f"**RoPE Freq:** `{rope_freq:,.0f}`")
            else:
                specs.append(f"**RoPE Freq:** `{rope_freq}`")

        tokenizer_model = self._scalar(model_info.get("tokenizer.ggml.model"))
        if tokenizer_model:
            specs.append(f"**Tokenizer:** `{tokenizer_model}`")

        if quantization and quantization.lower() != "unknown":
            specs.append(f"**Quant:** `{quantization}`")

        if size:
            specs.append(f"**Size:** `{self._format_size(size)}`")

        # --- MoE (Mixture of Experts) info ---
        expert_count = None
        expert_used = None
        for key in model_info:
            kl = key.lower()
            if "expert_used_count" in kl and expert_used is None:
                expert_used = self._scalar(model_info[key])
            elif "expert_count" in kl and expert_count is None:
                expert_count = self._scalar(model_info[key])
        if expert_count and isinstance(expert_count, (int, float)):
            moe_str = f"**Experts:** `{int(expert_count)}`"
            if expert_used and isinstance(expert_used, (int, float)):
                moe_str += f" (active: `{int(expert_used)}`)"
            specs.append(moe_str)

        if specs:
            lines.append(f"📐 **Specifications**")
            lines.extend(specs)

        # --- Vision Encoder Specs ---
        if "vision" in capabilities:
            vision_specs = []
            vision_fields = [
                ("image_size", "Image Size", "px"),
                ("patch_size", "Patch Size", "px"),
                ("block_count", "Layers", ""),
                ("embedding_length", "Embedding", "dims"),
                ("projection_dim", "Projection", "dims"),
                ("head_count", "Attn Heads", ""),
            ]
            for field_key, label, suffix in vision_fields:
                for key in model_info:
                    kl = key.lower()
                    if "vision" in kl and kl.endswith(f".{field_key}"):
                        val = self._scalar(model_info[key])
                        if val and isinstance(val, (int, float)):
                            if suffix:
                                vision_specs.append(
                                    f"**{label}:** `{self._format_number(val)}` {suffix}"
                                )
                            else:
                                vision_specs.append(
                                    f"**{label}:** `{self._format_number(val)}`"
                                )
                        break
            if vision_specs:
                lines.append("👁️ **Vision Encoder**")
                lines.extend(vision_specs)

        # --- Special Token IDs ---
        bos = model_info.get("tokenizer.ggml.bos_token_id")
        eos = model_info.get("tokenizer.ggml.eos_token_id")
        pad = model_info.get("tokenizer.ggml.padding_token_id")
        token_parts = []
        if bos is not None:
            token_parts.append(f"BOS:`{bos}`")
        if eos is not None:
            token_parts.append(f"EOS:`{eos}`")
        if pad is not None:
            token_parts.append(f"PAD:`{pad}`")
        if token_parts:
            lines.append(f"**Token IDs:** {' · '.join(token_parts)}")

        # --- Inference Parameters ---
        parameters_str = (show_info or {}).get("parameters", "")
        if parameters_str and parameters_str.strip():
            param_items = []
            for pline in parameters_str.strip().split("\n"):
                pline = pline.strip()
                if pline:
                    parts = pline.split(None, 1)
                    if len(parts) == 2:
                        param_items.append(f"`{parts[0]}`=`{parts[1]}`")
            if param_items:
                lines.append(f"⚙️ **Inference Defaults**")
                lines.append(" · ".join(param_items))

        # --- System Prompt (truncated) ---
        system_prompt = (show_info or {}).get("system", "")
        if system_prompt and system_prompt.strip():
            truncated = system_prompt.strip()
            if len(truncated) > 200:
                truncated = truncated[:197] + "..."
            truncated = truncated.replace("\n", " ")
            lines.append(f"💬 **System Prompt** — {truncated}")

        # --- Template hint ---
        if template and template.strip():
            truncated_tmpl = template.strip().replace("\n", " ")
            if len(truncated_tmpl) > 200:
                truncated_tmpl = truncated_tmpl[:197] + "..."
            lines.append(f"📝 **Template** — `{truncated_tmpl}`")

        # --- License ---
        license_text = (show_info or {}).get("license", "")
        if license_text and license_text.strip():
            license_lines = [
                l.strip() for l in license_text.strip().split("\n") if l.strip()
            ]
            if license_lines:
                first_line = license_lines[0][:120]
                lines.append(f"📄 **License** — {first_line}")

        # --- Modified date ---
        modified_at = tags_info.get("modified_at", "")
        if modified_at:
            if isinstance(modified_at, str) and modified_at:
                display_date = modified_at[:19].replace("T", " ")
                lines.append(f"**Modified:** `{display_date}`")
            elif isinstance(modified_at, (int, float)) and modified_at > 0:
                dt = datetime.fromtimestamp(modified_at, tz=timezone.utc)
                lines.append(f"**Modified:** `{dt.strftime('%Y-%m-%d %H:%M:%S')} UTC`")

        # --- Digest ---
        digest = tags_info.get("digest", "")
        if digest:
            short_digest = digest[:19] if len(digest) > 19 else digest
            lines.append(f"**Digest:** `{short_digest}…`")

        # --- Footer ---
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"*Generated {now}*")

        # --- Degraded data warning ---
        if show_info is None:
            lines.insert(
                2, "⚠️ *Limited data — `/api/show` unavailable for this model*"
            )

        return "\n\n".join(lines)

    # ------------------------------------------------------------------
    # Public tool methods
    # ------------------------------------------------------------------

    async def generate_model_card(
        self,
        model: str,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Generate a detailed model card for a specific installed Ollama model.

        Pulls metadata from the Ollama API including architecture, parameter
        count, context length, quantization, capabilities, system prompt,
        and inference parameters. Returns a formatted model card string.
        Nothing is written to the database.

        :param model: The model name (e.g. "llama3.1:latest", "hf.co/user/model:Q4_K_M")
        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with the generated model card text and metadata
        """
        err = self._validate_tool_access(
            "generate_model_card",
            self.valves.enable_generate_model_card,
            __user__,
            __request__,
            model=model,
        )
        if err:
            return err

        result = await self._generate_card_data(model, __request__, __event_emitter__)
        if result.get("status") == "success":
            # Keep multi-kilobyte avatar data out of the LLM-facing output.
            result = {
                **{k: v for k, v in result.items() if k != "profile_image_url"},
                "has_profile_image": bool(result.get("profile_image_url")),
            }
        return json.dumps(result, ensure_ascii=False)

    async def _generate_card_data(
        self, model: str, __request__, __event_emitter__=None
    ) -> dict:
        """
        Build everything the write paths need for one model: the card text,
        detected capabilities/tags/params, display name, suggestion prompts
        and the resolved profile image.  Returns a plain dict (``status`` is
        ``"success"`` or ``"error"``).  Access checks are the caller's job.
        """
        try:
            await self._emit_status(
                __event_emitter__, f"Generating model card for {model}..."
            )

            ollama_models = await self._get_ollama_models(__request__)

            # Look up the model
            model_info = ollama_models.get(model)
            if not model_info and ":" not in model:
                model_info = ollama_models.get(f"{model}:latest")
                if model_info:
                    model = f"{model}:latest"

            if not model_info:
                await self._emit_status(
                    __event_emitter__,
                    f"Model '{model}' not found",
                    done=True,
                )
                return {
                    "status": "error",
                    "error": f"Model '{model}' not found in installed models.",
                }

            # Resolve which Ollama instance serves this model
            url, headers, show_name, ep_err = await self._resolve_ollama_endpoint(
                __request__, model, model_info
            )
            if ep_err:
                await self._emit_status(__event_emitter__, ep_err, done=True)
                return {"status": "error", "error": ep_err}

            # Get detailed model info from /api/show
            show_info = await self._get_ollama_show(url, show_name, headers)

            # Detect capabilities, tags, params, and meta capabilities
            details = model_info.get("details", {})
            show_details = (show_info or {}).get("details", {})
            mi = (show_info or {}).get("model_info", {})
            architecture = show_details.get("family") or details.get("family") or ""
            fams = show_details.get("families") or details.get("families")
            capabilities = self._detect_capabilities(
                architecture, fams, mi, show_info, model
            )
            tags = self._detect_tags(
                model, architecture, fams, mi, show_info, model_info
            )
            extracted_params = self._extract_params(
                show_info, mi, model_info.get("size", 0)
            )
            meta_capabilities = self._build_meta_capabilities(
                capabilities, mi, show_info
            )

            # Auto-set function_calling to native for tool-capable models
            if "tools" in capabilities:
                extracted_params["function_calling"] = "native"

            # Auto-enable reasoning tags for thinking models.
            # Scale reasoning_effort by model size: smaller models need
            # more thinking time to reach good answers.
            if "thinking" in capabilities:
                extracted_params["reasoning_tags"] = True
                param_size = (
                    show_details.get("parameter_size")
                    or details.get("parameter_size")
                    or ""
                )
                # Parse param size (e.g. "32B" -> 32, "3.8B" -> 3.8)
                size_num = 0
                if param_size:
                    m = re.match(r"([\d.]+)", param_size)
                    if m:
                        try:
                            size_num = float(m.group(1))
                        except ValueError:
                            pass
                if size_num >= 30:
                    extracted_params["reasoning_effort"] = "low"
                elif size_num >= 14:
                    extracted_params["reasoning_effort"] = "medium"
                else:
                    extracted_params["reasoning_effort"] = "high"

            # Determine if context was capped for the model card display
            native_ctx = None
            for key in mi:
                if "context_length" in key.lower():
                    native_ctx = self._scalar(mi[key])
                    break
            capped_ctx = None
            if (
                native_ctx
                and extracted_params.get("num_ctx")
                and extracted_params["num_ctx"] < native_ctx
            ):
                capped_ctx = extracted_params["num_ctx"]

            # Build the model card (after params so we can show capping info)
            card = self._build_model_card(
                model, model_info, show_info, capped_ctx=capped_ctx
            )

            await self._emit_status(
                __event_emitter__,
                f"Model card generated for {model}",
                done=True,
            )

            # Include system prompt separately (controlled by its own valve)
            system_prompt = (show_info or {}).get("system", "") if show_info else ""

            # Legacy heuristic: old Ollama releases could not load
            # full-precision vision models (sharded projector files).
            # Only evaluated when Ollama does not report native capabilities,
            # which is the signal for one of those older releases.
            # file_type: 0=F32, 1=F16, 32=BF16
            is_sharded = False
            has_native_caps = bool((show_info or {}).get("capabilities"))
            if "vision" in capabilities and mi and not has_native_caps:
                file_type = mi.get("general.file_type")
                if file_type in (0, 1, 32):
                    is_sharded = True

            # Include display name in result
            display_name = self._generate_display_name(model, show_info, model_info)
            profile_img, img_generated = self._resolve_profile_image_ex(
                model, architecture, fams or [], tags
            )

            return {
                "status": "success",
                "model": model,
                "model_card": card,
                "capabilities": capabilities,
                "tags": tags,
                "extracted_params": extracted_params,
                "meta_capabilities": meta_capabilities,
                "system_prompt": system_prompt.strip() if system_prompt else "",
                "display_name": display_name,
                "is_sharded": is_sharded,
                "suggestion_prompts": self._generate_suggestion_prompts(capabilities),
                "profile_image_url": profile_img,
                "profile_image_generated": img_generated,
                "has_show_data": show_info is not None,
            }

        except Exception as e:
            log.exception(f"generate_model_card error for {model}: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return {"status": "error", "error": str(e)}

    async def generate_all_model_cards(
        self,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Generate model cards for all installed Ollama models.

        Iterates through every installed model, pulls metadata from the
        Ollama API, and returns formatted model cards for each.

        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with model cards for all models, including success/error counts
        """
        err = self._validate_tool_access(
            "generate_all_model_cards",
            self.valves.enable_generate_all_model_cards,
            __user__,
            __request__,
        )
        if err:
            return err

        try:
            await self._emit_status(__event_emitter__, "Fetching installed models...")

            ollama_models = await self._get_ollama_models(__request__)

            if not ollama_models:
                await self._emit_status(
                    __event_emitter__, "No models found.", done=True
                )
                return json.dumps(
                    {
                        "status": "success",
                        "cards": [],
                        "total": 0,
                    }
                )

            cards = []
            errors = []
            total = len(ollama_models)

            for idx, (model_id, model_info) in enumerate(ollama_models.items()):
                await self._emit_status(
                    __event_emitter__,
                    f"Generating card ({idx + 1}/{total}): {model_id}",
                )
                try:
                    url, headers, show_name, ep_err = (
                        await self._resolve_ollama_endpoint(
                            __request__, model_id, model_info
                        )
                    )
                    if ep_err:
                        errors.append({"model": model_id, "error": ep_err})
                        continue

                    show_info = await self._get_ollama_show(url, show_name, headers)

                    card = self._build_model_card(model_id, model_info, show_info)
                    cards.append(
                        {
                            "model": model_id,
                            "model_card": card,
                            "has_show_data": show_info is not None,
                        }
                    )

                except Exception as e:
                    log.debug(f"Error generating card for {model_id}: {e}")
                    errors.append({"model": model_id, "error": str(e)})

            summary = f"Generated {len(cards)} model card(s)"
            if errors:
                summary += f", {len(errors)} error(s)"

            await self._emit_status(__event_emitter__, summary, done=True)

            return json.dumps(
                {
                    "status": "success",
                    "cards": cards,
                    "errors": errors,
                    "total": total,
                    "generated": len(cards),
                    "summary": summary,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            log.exception(f"generate_all_model_cards error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    async def apply_model_card(
        self,
        model: str,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Generate and apply a model card as the description for a specific model.

        Generates the model card from Ollama metadata and writes it to the
        model's description field in the Open WebUI database. If the model
        doesn't have a database entry yet, one will be created.

        With the ``apply_model_card_dry_run`` valve on, nothing is written:
        the response lists, field by field, what WOULD change. With
        ``apply_model_card_reset`` on, the call undoes instead: the model's
        override entry is deleted and the Ollama model shows raw again.

        :param model: The model name (e.g. "llama3.1:latest")
        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with success/failure status and the applied model card (or the dry-run diff)
        """
        err = self._validate_tool_access(
            "apply_model_card",
            self.valves.enable_apply_model_card,
            __user__,
            __request__,
            model=model,
        )
        if err:
            return err
        if self.valves.apply_model_card_reset:
            return await self._reset_apply(
                model, self.valves.apply_model_card_dry_run, __event_emitter__
            )
        return await self._apply_model_card(
            model,
            self.valves.apply_model_card_dry_run,
            __request__,
            __user__,
            __event_emitter__,
        )

    async def _apply_model_card(
        self, model: str, dry_run: bool, __request__, __user__, __event_emitter__
    ) -> str:
        """Shared implementation for apply_model_card / apply_all_model_cards."""
        pfx = self._dry_prefix(dry_run)
        try:
            # First generate the card
            result = await self._generate_card_data(
                model, __request__, __event_emitter__
            )
            if result.get("status") != "success":
                return json.dumps(result)

            card = result["model_card"]
            model_id = result["model"]

            from open_webui.models.models import (
                ModelForm,
                ModelMeta,
                ModelParams,
                Models,
            )

            existing = await Models.get_model_by_id(model_id)
            user_id = (__user__ or {}).get("id", "")

            if existing:
                # Check if it already has a description
                existing_desc = existing.meta.description if existing.meta else None
                if (
                    existing_desc
                    and existing_desc.strip()
                    and not self.valves.overwrite_existing
                ):
                    await self._emit_status(
                        __event_emitter__,
                        f"{pfx}{model_id}: already has description (skipped — enable overwrite_existing valve to replace)",
                        done=True,
                    )
                    return json.dumps(
                        {
                            "status": "success",
                            "dry_run": dry_run,
                            "model": model_id,
                            "applied": False,
                            "would_apply": False,
                            "reason": "Already has description. Enable overwrite_existing valve to replace.",
                            "existing_description": (
                                existing_desc[:100] + "..."
                                if len(existing_desc or "") > 100
                                else existing_desc
                            ),
                        }
                    )

                updated_meta = self._build_model_meta(result, existing)
                existing_params = self._build_model_params(result, existing)

                # Auto-set display name
                display_name = result.get("display_name", "")
                if self.valves.auto_display_name and display_name:
                    if (
                        self.valves.overwrite_display_name
                        or existing.name == existing.id
                    ):
                        model_name = display_name
                    else:
                        model_name = existing.name
                else:
                    model_name = existing.name

                # Auto-disable sharded models
                model_active = existing.is_active
                if self.valves.auto_disable_sharded and result.get("is_sharded", False):
                    model_active = False

                form = ModelForm(
                    id=existing.id,
                    base_model_id=existing.base_model_id,
                    name=model_name,
                    meta=ModelMeta(**updated_meta),
                    params=(
                        ModelParams(**existing_params)
                        if existing_params
                        else ModelParams()
                    ),
                    is_active=model_active,
                )

            else:
                # Create a new minimal DB entry
                meta_dict = self._build_model_meta(result)
                new_params = self._build_model_params(result)

                # Display name
                display_name = result.get("display_name", model_id)
                if not self.valves.auto_display_name:
                    display_name = model_id

                # Auto-disable sharded models
                model_active = True
                if self.valves.auto_disable_sharded and result.get("is_sharded", False):
                    model_active = False

                form = ModelForm(
                    id=model_id,
                    name=display_name,
                    meta=ModelMeta(**meta_dict),
                    params=ModelParams(**new_params) if new_params else ModelParams(),
                    is_active=model_active,
                )

            if dry_run:
                changes = self._describe_changes(existing, form)
                would_apply = existing is None or bool(changes)
                if not would_apply:
                    msg = f"{pfx}{model_id}: nothing would change"
                elif existing is None:
                    msg = f"{pfx}{model_id}: would create a new entry ({len(changes)} field(s))"
                else:
                    msg = f"{pfx}{model_id}: would update {len(changes)} field(s): {', '.join(changes)}"
                await self._emit_status(__event_emitter__, msg, done=True)
                return json.dumps(
                    {
                        "status": "success",
                        "dry_run": True,
                        "model": model_id,
                        "applied": False,
                        "would_apply": would_apply,
                        "would_create_entry": existing is None,
                        "changes": changes,
                        "message": msg,
                    },
                    ensure_ascii=False,
                )

            if existing:
                saved = await Models.update_model_by_id(model_id, form)
            else:
                saved = await Models.insert_new_model(form, user_id)
            if saved is None:
                raise RuntimeError(
                    "database write failed (Open WebUI rejected the entry — check the server log)"
                )

            await self._emit_status(
                __event_emitter__,
                f"Model card applied to {model_id}",
                done=True,
            )

            return json.dumps(
                {
                    "status": "success",
                    "dry_run": False,
                    "model": model_id,
                    "applied": True,
                    "model_card": card,
                    "was_new_entry": existing is None,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            log.exception(f"apply_model_card error for {model}: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    async def apply_all_model_cards(
        self,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Generate and apply model cards for ALL installed Ollama models.

        Iterates through every installed model, generates a model card from
        Ollama metadata, and writes it to the model's description in the
        Open WebUI database. By default, skips models that already have
        a description (controlled by the overwrite_existing valve).

        With the ``apply_all_model_cards_dry_run`` valve on, nothing is
        written: the response lists which models WOULD be updated and
        which fields would change on each. With ``apply_all_model_cards_reset``
        on, the call undoes instead: every Ollama model's override entry
        is deleted (Ollama models themselves are never touched).

        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with results for all models including applied, skipped, and error counts
        """
        err = self._validate_tool_access(
            "apply_all_model_cards",
            self.valves.enable_apply_all_model_cards,
            __user__,
            __request__,
        )
        if err:
            return err

        dry_run = self.valves.apply_all_model_cards_dry_run
        pfx = self._dry_prefix(dry_run)
        if self.valves.apply_all_model_cards_reset:
            return await self._reset_apply_all(dry_run, __request__, __event_emitter__)

        try:
            await self._emit_status(
                __event_emitter__, f"{pfx}Fetching installed models..."
            )

            ollama_models = await self._get_ollama_models(__request__)

            if not ollama_models:
                await self._emit_status(
                    __event_emitter__, "No models found.", done=True
                )
                return json.dumps(
                    {
                        "status": "success",
                        "dry_run": dry_run,
                        "applied": [],
                        "skipped": [],
                        "errors": [],
                        "total": 0,
                    }
                )

            applied = []
            skipped = []
            errors = []
            changes = {}
            total = len(ollama_models)

            for idx, model_id in enumerate(ollama_models.keys()):
                await self._emit_status(
                    __event_emitter__,
                    f"{pfx}Processing ({idx + 1}/{total}): {model_id}",
                )

                try:
                    # Suppress child emitter to avoid per-model status spam
                    result_json = await self._apply_model_card(
                        model_id, dry_run, __request__, __user__, None
                    )
                    result = json.loads(result_json)

                    if result.get("status") == "success":
                        if result.get("applied") or result.get("would_apply"):
                            applied.append(model_id)
                            if dry_run:
                                changes[model_id] = sorted(result.get("changes", {}))
                        else:
                            skipped.append(model_id)
                    else:
                        errors.append(
                            {
                                "model": model_id,
                                "error": result.get("error", "Unknown"),
                            }
                        )

                except Exception as e:
                    log.debug(f"Error applying card for {model_id}: {e}")
                    errors.append({"model": model_id, "error": str(e)})

            summary_parts = []
            if applied:
                summary_parts.append(
                    f"{len(applied)} {'would be applied' if dry_run else 'applied'}"
                )
            if skipped:
                summary_parts.append(
                    f"{len(skipped)} skipped (already have descriptions or no changes)"
                )
            if errors:
                summary_parts.append(f"{len(errors)} error(s)")
            summary = ", ".join(summary_parts) or "No models processed"
            if dry_run:
                summary += " — dry run, nothing written (turn off apply_all_model_cards_dry_run to apply)"

            await self._emit_status(
                __event_emitter__, f"{pfx}Done: {summary}", done=True
            )

            out = {
                "status": "success",
                "dry_run": dry_run,
                ("would_apply" if dry_run else "applied"): applied,
                "skipped": skipped,
                "errors": errors,
                "summary": {
                    "total": total,
                    ("would_apply" if dry_run else "applied"): len(applied),
                    "skipped": len(skipped),
                    "errors": len(errors),
                },
                "message": summary,
            }
            if dry_run:
                out["changes"] = changes
            return json.dumps(out, ensure_ascii=False)

        except Exception as e:
            log.exception(f"apply_all_model_cards error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    # ------------------------------------------------------------------
    # Workspace model creation
    # ------------------------------------------------------------------

    def _generate_workspace_id(self, model_id: str) -> str:
        """Generate a workspace model ID by appending '-workspace' to the Ollama model ID.

        Open WebUI rejects model IDs containing whitespace, so the suffix
        carries none.  Entries created by older versions of this tool used
        ' (Workspace)'; see ``_legacy_workspace_id``."""
        return f"{model_id}{self._WORKSPACE_SUFFIX}"

    def _legacy_workspace_id(self, model_id: str) -> str:
        """Workspace model ID as generated before v1.7.0 (contains whitespace)."""
        return f"{model_id}{self._LEGACY_WORKSPACE_SUFFIX}"

    async def _find_existing_workspace(self, model_id: str):
        """Check if a workspace model already exists for the given base model ID.

        Looks for the current ID first, then the pre-v1.7.0 ID."""
        from open_webui.models.models import Models

        existing = await Models.get_model_by_id(self._generate_workspace_id(model_id))
        if existing is None:
            existing = await Models.get_model_by_id(self._legacy_workspace_id(model_id))
        return existing

    async def _remove_base_override(self, model_id: str):
        """Remove the base-model override DB entry for *model_id* if one exists.

        When both a base-model override (``base_model_id=None``, same ID
        as the Ollama model) and a workspace model exist for the same
        Ollama model, the override causes the model to appear **twice**
        in the selector and makes the toggle/hide controls ineffective.

        Deleting (rather than deactivating) the override is important:
        setting ``is_active=False`` on a base override causes
        ``get_all_models`` to *remove* the raw Ollama model from the
        selector entirely, which breaks the workspace model that depends
        on it.  Deleting the override leaves the raw Ollama model
        untouched.

        Calling this after creating a workspace model ensures only the
        workspace entry carries the custom metadata.
        """
        from open_webui.models.models import Models

        existing = await Models.get_model_by_id(model_id)
        if not existing:
            return

        # Only remove genuine base-model overrides (base_model_id is None).
        if existing.base_model_id is not None:
            return

        await Models.delete_model_by_id(model_id)

    _HIDDEN_FLAG = "base_hidden_by_tool"
    _CREATED_FLAG = "base_override_created_by_tool"

    async def _set_base_hidden(
        self, model_id: str, hidden: bool, user_id: str, dry_run: bool = False
    ) -> dict:
        """Hide or unhide the base-model entry for *model_id*.

        Hiding sets ``meta.hidden`` on the base-model override (creating a
        minimal override entry when none exists).  Hidden entries stay
        active, so Open WebUI keeps the base model loaded and workspace
        models built on it keep working — unlike deactivating the
        override, which removes the base model from the list and breaks
        its workspace models.

        Unhiding only touches entries this tool hid (flagged in meta):
        overrides it created are deleted again, others just lose the flag.

        Returns ``{"action": ...}`` where action is one of ``hide``,
        ``create_hidden_override``, ``unhide``, ``delete_hidden_override``,
        ``unchanged`` or ``skip``.
        """
        from open_webui.models.models import ModelForm, ModelMeta, ModelParams, Models

        existing = await Models.get_model_by_id(model_id)
        if existing is not None and existing.base_model_id is not None:
            return {"action": "skip", "reason": "entry is not a base-model override"}
        meta = existing.meta.model_dump() if existing and existing.meta else {}

        if hidden:
            if existing is not None and meta.get("hidden"):
                return {"action": "unchanged", "reason": "already hidden"}
            action = "hide" if existing is not None else "create_hidden_override"
            if dry_run:
                return {"action": action}
            meta["hidden"] = True
            meta[self._HIDDEN_FLAG] = True
            if existing is not None:
                form = ModelForm(
                    id=existing.id,
                    base_model_id=None,
                    name=existing.name,
                    meta=ModelMeta(**meta),
                    params=existing.params if existing.params else ModelParams(),
                    is_active=existing.is_active,
                )
                saved = await Models.update_model_by_id(model_id, form)
            else:
                meta[self._CREATED_FLAG] = True
                form = ModelForm(
                    id=model_id,
                    name=model_id,
                    meta=ModelMeta(**meta),
                    params=ModelParams(),
                    is_active=True,
                )
                saved = await Models.insert_new_model(form, user_id)
            if saved is None:
                raise RuntimeError("database write failed")
            return {"action": action}

        # --- unhide ---
        if existing is None or not meta.get("hidden"):
            return {"action": "unchanged", "reason": "not hidden"}
        if not meta.get(self._HIDDEN_FLAG):
            return {
                "action": "unchanged",
                "reason": "hidden by something else (not this valve)",
            }
        if meta.get(self._CREATED_FLAG):
            if not dry_run:
                await Models.delete_model_by_id(model_id)
            return {"action": "delete_hidden_override"}
        if not dry_run:
            meta.pop("hidden", None)
            meta.pop(self._HIDDEN_FLAG, None)
            form = ModelForm(
                id=existing.id,
                base_model_id=None,
                name=existing.name,
                meta=ModelMeta(**meta),
                params=existing.params if existing.params else ModelParams(),
                is_active=existing.is_active,
            )
            if await Models.update_model_by_id(model_id, form) is None:
                raise RuntimeError("database write failed")
        return {"action": "unhide"}

    async def create_workspace_model(
        self,
        model: str,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Create a workspace model wrapping a specific Ollama model.

        Generates a full model card from Ollama metadata, then creates a
        new workspace model entry in the Open WebUI database with
        ``base_model_id`` pointing to the original Ollama model. The
        workspace model gets auto-populated with description, tags,
        capabilities, inference parameters, system prompt, display name,
        suggestion prompts, and profile image. Any base-model override
        entry for the same Ollama model is removed so the model is not
        listed twice.

        With the ``create_workspace_model_dry_run`` valve on, nothing is
        written: the response describes the workspace model that WOULD be
        created or updated. With ``create_workspace_model_reset`` on, the
        call undoes instead: the "-workspace" entry is deleted and the base
        model is unhidden if this tool had hidden it.

        :param model: The Ollama model name (e.g. "llama3.1:latest")
        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with success/failure status and workspace model details
        """
        err = self._validate_tool_access(
            "create_workspace_model",
            self.valves.enable_create_workspace_model,
            __user__,
            __request__,
            model=model,
        )
        if err:
            return err
        if self.valves.create_workspace_model_reset:
            return await self._reset_create(
                model,
                self.valves.create_workspace_model_dry_run,
                (__user__ or {}).get("id", ""),
                __event_emitter__,
            )
        return await self._create_workspace_model(
            model,
            self.valves.create_workspace_model_dry_run,
            __request__,
            __user__,
            __event_emitter__,
        )

    async def _create_workspace_model(
        self, model: str, dry_run: bool, __request__, __user__, __event_emitter__
    ) -> str:
        """Shared implementation for create_workspace_model / create_all_workspace_models."""
        pfx = self._dry_prefix(dry_run)
        try:
            # Generate the model card data
            result = await self._generate_card_data(
                model, __request__, __event_emitter__
            )
            if result.get("status") != "success":
                return json.dumps(result)

            model_id = result["model"]
            workspace_id = self._generate_workspace_id(model_id)

            from open_webui.models.models import (
                ModelForm,
                ModelMeta,
                ModelParams,
                Models,
            )

            # Check if workspace model already exists, under the current ID
            # or under the whitespace ID older versions of this tool used.
            legacy_ws = None
            existing_ws = await Models.get_model_by_id(workspace_id)
            if existing_ws is None:
                legacy_ws = await Models.get_model_by_id(
                    self._legacy_workspace_id(model_id)
                )
                existing_ws = legacy_ws

            if legacy_ws is not None and not self.valves.overwrite_workspace_models:
                # Open WebUI no longer accepts the old ID through the model
                # form, so move the entry to the new ID as-is.
                form = ModelForm(
                    id=workspace_id,
                    base_model_id=legacy_ws.base_model_id or model_id,
                    name=legacy_ws.name,
                    meta=legacy_ws.meta,
                    params=legacy_ws.params,
                    is_active=legacy_ws.is_active,
                )
                msg = f"{pfx}{'would migrate' if dry_run else 'migrated'} workspace model '{legacy_ws.id}' to '{workspace_id}'"
                if not dry_run:
                    saved = await Models.insert_new_model(
                        form, (__user__ or {}).get("id", "")
                    )
                    if saved is None:
                        raise RuntimeError(
                            "database write failed (Open WebUI rejected the entry — check the server log)"
                        )
                    await Models.delete_model_by_id(legacy_ws.id)
                await self._emit_status(__event_emitter__, msg, done=True)
                return json.dumps(
                    {
                        "status": "success",
                        "dry_run": dry_run,
                        "model": model_id,
                        "workspace_id": workspace_id,
                        "workspace_name": legacy_ws.name,
                        "created": not dry_run,
                        "would_create": dry_run,
                        "migrated_from": legacy_ws.id,
                        "message": msg,
                    },
                    ensure_ascii=False,
                )

            if existing_ws and not self.valves.overwrite_workspace_models:
                await self._emit_status(
                    __event_emitter__,
                    f"{pfx}{model_id}: workspace model already exists (skipped — enable overwrite_workspace_models to replace)",
                    done=True,
                )
                return json.dumps(
                    {
                        "status": "success",
                        "dry_run": dry_run,
                        "model": model_id,
                        "workspace_id": workspace_id,
                        "created": False,
                        "would_create": False,
                        "reason": "Workspace model already exists. Enable overwrite_workspace_models valve to replace.",
                    }
                )

            # Build meta and params via shared helpers
            meta_dict = self._build_model_meta(result, existing_ws)
            params = self._build_model_params(result, existing_ws)

            # Display name
            display_name = result.get("display_name", model_id)
            if not self.valves.auto_display_name:
                display_name = model_id
            if existing_ws and not self.valves.overwrite_display_name:
                if existing_ws.name != existing_ws.id:
                    display_name = existing_ws.name

            # Sharded check
            model_active = True
            if self.valves.auto_disable_sharded and result.get("is_sharded", False):
                model_active = False

            user_id = (__user__ or {}).get("id", "")

            form = ModelForm(
                id=workspace_id,
                base_model_id=model_id,
                name=display_name,
                meta=ModelMeta(**meta_dict),
                params=ModelParams(**params) if params else ModelParams(),
                is_active=model_active,
            )

            if dry_run:
                changes = self._describe_changes(existing_ws, form)
                would_remove_override = False
                would_hide_base = False
                if self.valves.hide_base_models:
                    hide = await self._set_base_hidden(
                        model_id, True, user_id, dry_run=True
                    )
                    would_hide_base = hide["action"] in (
                        "hide",
                        "create_hidden_override",
                    )
                else:
                    base_override = await Models.get_model_by_id(model_id)
                    would_remove_override = (
                        base_override is not None
                        and base_override.base_model_id is None
                    )
                would_create = existing_ws is None
                would_update = existing_ws is not None and bool(changes)
                if would_create:
                    msg = f"{pfx}would create workspace model '{display_name}' ({workspace_id})"
                elif legacy_ws is not None:
                    msg = f"{pfx}would migrate workspace model '{legacy_ws.id}' to '{workspace_id}'"
                    if changes:
                        msg += f" and update: {', '.join(changes)}"
                elif would_update:
                    msg = f"{pfx}would update workspace model '{display_name}': {', '.join(changes)}"
                else:
                    msg = f"{pfx}{workspace_id}: nothing would change"
                if would_remove_override:
                    msg += f"; would remove duplicate base override '{model_id}'"
                if would_hide_base:
                    msg += f"; would hide base model '{model_id}'"
                await self._emit_status(__event_emitter__, msg, done=True)
                return json.dumps(
                    {
                        "status": "success",
                        "dry_run": True,
                        "model": model_id,
                        "workspace_id": workspace_id,
                        "workspace_name": display_name,
                        "created": False,
                        "would_create": would_create,
                        "would_update": would_update,
                        "would_remove_base_override": would_remove_override,
                        "would_hide_base_model": would_hide_base,
                        "changes": changes,
                        "message": msg,
                    },
                    ensure_ascii=False,
                )

            if legacy_ws is not None:
                saved = await Models.insert_new_model(form, user_id)
                if saved is not None:
                    await Models.delete_model_by_id(legacy_ws.id)
            elif existing_ws:
                saved = await Models.update_model_by_id(workspace_id, form)
            else:
                saved = await Models.insert_new_model(form, user_id)
            if saved is None:
                raise RuntimeError(
                    "database write failed (Open WebUI rejected the entry — check the server log)"
                )

            # The base model must not show up next to its workspace model:
            # either hide it (hide_base_models) or drop the duplicate
            # base-model override entry.
            base_hidden = False
            if self.valves.hide_base_models:
                base_hidden = (await self._set_base_hidden(model_id, True, user_id))[
                    "action"
                ] in ("hide", "create_hidden_override")
            else:
                await self._remove_base_override(model_id)

            action = (
                "migrated"
                if legacy_ws is not None
                else "updated" if existing_ws else "created"
            )
            await self._emit_status(
                __event_emitter__,
                f"Workspace model {action}: {display_name}",
                done=True,
            )

            return json.dumps(
                {
                    "status": "success",
                    "dry_run": False,
                    "model": model_id,
                    "workspace_id": workspace_id,
                    "workspace_name": display_name,
                    "created": True,
                    "was_update": existing_ws is not None,
                    "base_model_hidden": base_hidden,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            log.exception(f"create_workspace_model error for {model}: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    async def create_all_workspace_models(
        self,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Create workspace models for ALL installed Ollama models.

        Iterates through every installed Ollama model, generates a model
        card, and creates a workspace model entry in the database. Each
        workspace model wraps the original Ollama model with enriched
        metadata. By default, skips models that already have a workspace
        model (controlled by the overwrite_workspace_models valve).

        With the ``create_all_workspace_models_dry_run`` valve on, nothing
        is written: the response lists which workspace models WOULD be
        created, updated or skipped. With ``create_all_workspace_models_reset``
        on, the call undoes instead: every "-workspace" model is deleted
        and every base this tool hid is unhidden.

        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with results for all models including created, skipped, and error counts
        """
        err = self._validate_tool_access(
            "create_all_workspace_models",
            self.valves.enable_create_all_workspace_models,
            __user__,
            __request__,
        )
        if err:
            return err

        dry_run = self.valves.create_all_workspace_models_dry_run
        pfx = self._dry_prefix(dry_run)
        if self.valves.create_all_workspace_models_reset:
            return await self._reset_create_all(
                dry_run, (__user__ or {}).get("id", ""), __event_emitter__
            )

        try:
            await self._emit_status(
                __event_emitter__, f"{pfx}Fetching installed models..."
            )

            ollama_models = await self._get_ollama_models(__request__)

            if not ollama_models:
                await self._emit_status(
                    __event_emitter__, "No models found.", done=True
                )
                return json.dumps(
                    {
                        "status": "success",
                        "dry_run": dry_run,
                        "created": [],
                        "skipped": [],
                        "errors": [],
                        "total": 0,
                    }
                )

            created = []
            skipped = []
            errors = []
            details = {}
            total = len(ollama_models)

            for idx, model_id in enumerate(ollama_models.keys()):
                await self._emit_status(
                    __event_emitter__,
                    f"{pfx}Creating workspace ({idx + 1}/{total}): {model_id}",
                )

                try:
                    # Suppress child emitter to avoid per-model status spam
                    result_json = await self._create_workspace_model(
                        model_id, dry_run, __request__, __user__, None
                    )
                    result = json.loads(result_json)

                    if result.get("status") == "success":
                        if (
                            result.get("created")
                            or result.get("would_create")
                            or result.get("would_update")
                            or result.get("would_remove_base_override")
                            or result.get("would_hide_base_model")
                        ):
                            created.append(model_id)
                            if dry_run:
                                details[model_id] = {
                                    "action": (
                                        "create"
                                        if result.get("would_create")
                                        else (
                                            "update"
                                            if result.get("would_update")
                                            else (
                                                "remove_base_override"
                                                if result.get(
                                                    "would_remove_base_override"
                                                )
                                                else "hide_base_model"
                                            )
                                        )
                                    ),
                                    "workspace_name": result.get("workspace_name"),
                                    "fields": sorted(result.get("changes", {})),
                                    "would_remove_base_override": result.get(
                                        "would_remove_base_override", False
                                    ),
                                    "would_hide_base_model": result.get(
                                        "would_hide_base_model", False
                                    ),
                                }
                        else:
                            skipped.append(model_id)
                    else:
                        errors.append(
                            {
                                "model": model_id,
                                "error": result.get("error", "Unknown"),
                            }
                        )

                except Exception as e:
                    log.debug(f"Error creating workspace for {model_id}: {e}")
                    errors.append({"model": model_id, "error": str(e)})

            summary_parts = []
            if created:
                summary_parts.append(
                    f"{len(created)} {'would be created/updated' if dry_run else 'created'}"
                )
            if skipped:
                summary_parts.append(
                    f"{len(skipped)} skipped (already exist or no changes)"
                )
            if errors:
                summary_parts.append(f"{len(errors)} error(s)")
            summary = ", ".join(summary_parts) or "No models processed"
            if dry_run:
                summary += " — dry run, nothing written (turn off create_all_workspace_models_dry_run to create)"

            await self._emit_status(
                __event_emitter__, f"{pfx}Done: {summary}", done=True
            )

            out = {
                "status": "success",
                "dry_run": dry_run,
                ("would_create" if dry_run else "created"): created,
                "skipped": skipped,
                "errors": errors,
                "summary": {
                    "total": total,
                    ("would_create" if dry_run else "created"): len(created),
                    "skipped": len(skipped),
                    "errors": len(errors),
                },
                "message": summary,
            }
            if dry_run:
                out["details"] = details
            return json.dumps(out, ensure_ascii=False)

        except Exception as e:
            log.exception(f"create_all_workspace_models error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    # ------------------------------------------------------------------
    # Model reset (undo tool changes)
    # ------------------------------------------------------------------

    async def _reset_model_fields(self, model_id: str, dry_run: bool = False) -> dict:
        """
        Reset a model's fields back to defaults based on the ``clear_*`` valves.

        Reads the model's current DB entry, clears only the fields whose
        corresponding ``clear_*`` valve is enabled, and writes the updated
        entry back — unless *dry_run* is set, in which case nothing is
        written and the result only reports what would be cleared.

        Returns a dict with ``cleared`` (list of field names that were
        reset), ``skipped`` (list of field names left untouched), and
        ``found`` (whether the model had a DB entry).
        """
        from open_webui.models.models import (
            ModelForm,
            ModelMeta,
            ModelParams,
            Models,
        )

        existing = await Models.get_model_by_id(model_id)
        if not existing:
            return {"found": False, "cleared": [], "skipped": []}

        meta_dict = existing.meta.model_dump() if existing.meta else {}
        params_dict = existing.params.model_dump() if existing.params else {}
        model_name = existing.name
        model_active = existing.is_active

        cleared = []
        skipped = []

        # --- Description ---
        if self.valves.clear_description:
            meta_dict["description"] = ""
            cleared.append("description")
        else:
            skipped.append("description")

        # --- Display Name ---
        if self.valves.clear_display_name:
            model_name = existing.id
            cleared.append("display_name")
        else:
            skipped.append("display_name")

        # --- Tags ---
        if self.valves.clear_tags:
            meta_dict["tags"] = []
            cleared.append("tags")
        else:
            skipped.append("tags")

        # --- Capabilities ---
        if self.valves.clear_capabilities:
            meta_dict.pop("capabilities", None)
            cleared.append("capabilities")
        else:
            skipped.append("capabilities")

        # --- Inference Parameters ---
        if self.valves.clear_params:
            # Preserve system prompt if clear_system_prompt is off
            sys_prompt = params_dict.get("system", "")
            params_dict = {}
            if not self.valves.clear_system_prompt and sys_prompt:
                params_dict["system"] = sys_prompt
            cleared.append("params")
        else:
            skipped.append("params")

        # --- System Prompt ---
        if self.valves.clear_system_prompt:
            params_dict.pop("system", None)
            cleared.append("system_prompt")
        else:
            skipped.append("system_prompt")

        # --- Suggestion Prompts ---
        if self.valves.clear_suggestion_prompts:
            meta_dict["suggestion_prompts"] = []
            cleared.append("suggestion_prompts")
        else:
            skipped.append("suggestion_prompts")

        # --- Profile Image ---
        if self.valves.clear_profile_image:
            meta_dict["profile_image_url"] = "/static/favicon.png"
            meta_dict.pop(self._GENERATED_FLAG, None)
            cleared.append("profile_image")
        else:
            skipped.append("profile_image")

        # --- Hidden ---
        if self.valves.clear_hidden:
            meta_dict.pop("hidden", None)
            meta_dict.pop(self._HIDDEN_FLAG, None)
            meta_dict.pop(self._CREATED_FLAG, None)
            cleared.append("hidden")
        else:
            skipped.append("hidden")

        # --- Inactive (toggled off) ---
        if self.valves.clear_inactive:
            if not model_active:
                model_active = True
                cleared.append("inactive")
            else:
                skipped.append("inactive")
        else:
            skipped.append("inactive")

        # Write back only if something was cleared (and this is not a dry run)
        if cleared and not dry_run:
            form = ModelForm(
                id=existing.id,
                base_model_id=existing.base_model_id,
                name=model_name,
                meta=ModelMeta(**meta_dict),
                params=ModelParams(**params_dict) if params_dict else ModelParams(),
                is_active=model_active,
            )
            if await Models.update_model_by_id(model_id, form) is None:
                raise RuntimeError("database update failed")

        return {"found": True, "cleared": cleared, "skipped": skipped}

    async def reset_model(
        self,
        model: str,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Reset a specific model's fields back to defaults.

        Clears fields based on which ``clear_*`` valves are enabled.
        For example, enabling ``clear_profile_image`` resets the model's
        avatar back to the default favicon. Enabling ``clear_tags``
        removes all tags; ``clear_inactive`` toggles a disabled model back
        on. Each field is independent — only fields with their ``clear_*``
        valve enabled are touched.

        With the ``reset_model_dry_run`` valve on (the default), nothing is
        written: the response lists which fields WOULD be cleared.

        :param model: The model name/ID (e.g. "llama3.1:latest")
        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with details of what was cleared
        """
        err = self._validate_tool_access(
            "reset_model",
            self.valves.enable_reset_model,
            __user__,
            __request__,
            model=model,
        )
        if err:
            return err

        dry_run = self.valves.reset_model_dry_run
        pfx = self._dry_prefix(dry_run)

        try:
            await self._emit_status(
                __event_emitter__, f"{pfx}Resetting fields for {model}..."
            )

            result = await self._reset_model_fields(model, dry_run)

            if not result["found"]:
                await self._emit_status(
                    __event_emitter__,
                    f"Model '{model}' has no database entry (nothing to reset)",
                    done=True,
                )
                return json.dumps(
                    {
                        "status": "success",
                        "model": model,
                        "reset": False,
                        "reason": "No database entry found for this model.",
                    }
                )

            cleared = result["cleared"]
            if cleared and dry_run:
                summary = f"Would reset {len(cleared)} field(s): {', '.join(cleared)} — dry run, nothing written (turn off reset_model_dry_run to reset)"
            elif cleared:
                summary = f"Reset {len(cleared)} field(s): {', '.join(cleared)}"
            else:
                summary = "No clear_* valves are enabled — nothing was reset. Enable the clear_* valves for the fields you want to reset."

            await self._emit_status(
                __event_emitter__,
                f"{pfx}{model}: {summary}",
                done=True,
            )

            return json.dumps(
                {
                    "status": "success",
                    "dry_run": dry_run,
                    "model": model,
                    "reset": bool(cleared) and not dry_run,
                    ("would_clear" if dry_run else "cleared"): cleared,
                    "skipped": result["skipped"],
                    "summary": summary,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            log.exception(f"reset_model error for {model}: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    async def reset_all_models(
        self,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Reset fields for ALL models in the Open WebUI database.

        Iterates through every model entry and clears fields based on
        which ``clear_*`` valves are enabled. Useful for undoing bulk
        changes made by ``apply_all_model_cards`` or
        ``create_all_workspace_models``.

        With the ``reset_all_models_dry_run`` valve on (the default),
        nothing is written: the response lists which models and fields
        WOULD be cleared.

        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with results for all models including reset, skipped, and error counts
        """
        err = self._validate_tool_access(
            "reset_all_models",
            self.valves.enable_reset_all_models,
            __user__,
            __request__,
        )
        if err:
            return err

        dry_run = self.valves.reset_all_models_dry_run
        pfx = self._dry_prefix(dry_run)

        try:
            # Quick sanity check: are any clear valves enabled?
            any_clear = any(
                [
                    self.valves.clear_description,
                    self.valves.clear_display_name,
                    self.valves.clear_tags,
                    self.valves.clear_capabilities,
                    self.valves.clear_params,
                    self.valves.clear_system_prompt,
                    self.valves.clear_suggestion_prompts,
                    self.valves.clear_profile_image,
                    self.valves.clear_hidden,
                    self.valves.clear_inactive,
                ]
            )
            if not any_clear:
                await self._emit_status(
                    __event_emitter__,
                    "No clear_* valves are enabled — nothing to reset. Enable the clear_* valves for the fields you want to reset.",
                    done=True,
                )
                return json.dumps(
                    {
                        "status": "success",
                        "dry_run": dry_run,
                        "reset": [],
                        "skipped": [],
                        "errors": [],
                        "summary": {
                            "total": 0,
                            "reset": 0,
                            "skipped": 0,
                            "errors": 0,
                        },
                        "message": "No clear_* valves are enabled — nothing to reset.",
                    }
                )

            from open_webui.models.models import Models

            await self._emit_status(
                __event_emitter__, f"{pfx}Fetching all models from database..."
            )

            all_models = await Models.get_all_models()

            if not all_models:
                await self._emit_status(
                    __event_emitter__, "No models found in database.", done=True
                )
                return json.dumps(
                    {
                        "status": "success",
                        "reset": [],
                        "skipped": [],
                        "errors": [],
                        "total": 0,
                    }
                )

            reset_list = []
            skipped_list = []
            errors = []
            total = len(all_models)

            for idx, model in enumerate(all_models):
                model_id = model.id
                await self._emit_status(
                    __event_emitter__,
                    f"{pfx}Resetting ({idx + 1}/{total}): {model_id}",
                )

                try:
                    result = await self._reset_model_fields(model_id, dry_run)

                    if result["cleared"]:
                        reset_list.append(model_id)
                    else:
                        skipped_list.append(model_id)

                except Exception as e:
                    log.debug(f"Error resetting {model_id}: {e}")
                    errors.append({"model": model_id, "error": str(e)})

            fields = [
                name
                for name, on in (
                    ("description", self.valves.clear_description),
                    ("display_name", self.valves.clear_display_name),
                    ("tags", self.valves.clear_tags),
                    ("capabilities", self.valves.clear_capabilities),
                    ("params", self.valves.clear_params),
                    ("system_prompt", self.valves.clear_system_prompt),
                    ("suggestion_prompts", self.valves.clear_suggestion_prompts),
                    ("profile_image", self.valves.clear_profile_image),
                    ("hidden", self.valves.clear_hidden),
                    ("inactive", self.valves.clear_inactive),
                )
                if on
            ]
            summary_parts = []
            if reset_list:
                summary_parts.append(
                    f"{len(reset_list)} {'would be reset' if dry_run else 'reset'} ({', '.join(fields)})"
                )
            if skipped_list:
                summary_parts.append(
                    f"{len(skipped_list)} skipped (no clear valves matched)"
                )
            if errors:
                summary_parts.append(f"{len(errors)} error(s)")
            summary = ", ".join(summary_parts) or "No models processed"
            if dry_run:
                summary += " — dry run, nothing written (turn off reset_all_models_dry_run to reset)"

            await self._emit_status(
                __event_emitter__, f"{pfx}Done: {summary}", done=True
            )

            return json.dumps(
                {
                    "status": "success",
                    "dry_run": dry_run,
                    "fields": fields,
                    ("would_reset" if dry_run else "reset"): reset_list,
                    "skipped": skipped_list,
                    "errors": errors,
                    "summary": {
                        "total": total,
                        ("would_reset" if dry_run else "reset"): len(reset_list),
                        "skipped": len(skipped_list),
                        "errors": len(errors),
                    },
                    "message": summary,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            log.exception(f"reset_all_models error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    # ------------------------------------------------------------------
    # Orphaned workspace models
    # ------------------------------------------------------------------

    async def _get_known_base_model_ids(self, __request__, __user__) -> set:
        """
        Return the set of base-model IDs Open WebUI currently knows about
        (Ollama models, OpenAI-compatible connections, and pipe functions).

        Uses ``open_webui.utils.models.get_all_base_models`` so a workspace
        model built on an API-connection model is never mistaken for an
        orphan. Falls back to the Ollama model list if that call fails.
        """
        try:
            from open_webui.utils.models import get_all_base_models

            user = None
            try:
                from open_webui.models.users import Users

                if __user__ and __user__.get("id"):
                    user = await Users.get_user_by_id(__user__["id"])
            except Exception as e:
                log.debug(f"Could not resolve user for base model listing: {e}")

            base_models = await get_all_base_models(__request__, user)
            ids = {m.get("id") for m in (base_models or []) if m.get("id")}
            if ids:
                return ids
        except Exception as e:
            log.warning(f"get_all_base_models failed, falling back to Ollama list: {e}")

        ollama_models = await self._get_ollama_models(__request__)
        return set(ollama_models.keys())

    async def remove_orphaned_workspace_models(
        self,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Remove workspace models whose base model no longer exists.

        A workspace model is "orphaned" when its ``base_model_id`` does not
        match any model Open WebUI can currently see (Ollama, OpenAI-style
        connections, or pipe functions) — typically because the Ollama
        model was removed. By default this only REPORTS the orphans
        (``remove_orphaned_workspace_models_dry_run`` valve). Turn that
        valve off to delete.

        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON listing orphaned workspace models and what was done
        """
        err = self._validate_tool_access(
            "remove_orphaned_workspace_models",
            self.valves.enable_remove_orphaned_workspace_models,
            __user__,
            __request__,
        )
        if err:
            return err

        try:
            from open_webui.models.models import Models

            dry_run = self.valves.remove_orphaned_workspace_models_dry_run
            pfx = self._dry_prefix(dry_run)

            await self._emit_status(
                __event_emitter__, f"{pfx}Fetching known base models..."
            )
            known_ids = await self._get_known_base_model_ids(__request__, __user__)

            if not known_ids:
                await self._emit_status(
                    __event_emitter__,
                    "No base models found — refusing to treat every workspace model as orphaned.",
                    done=True,
                )
                return json.dumps(
                    {
                        "status": "error",
                        "error": "No base models could be listed; aborting so nothing is deleted by mistake.",
                    }
                )

            all_models = await Models.get_all_models()
            orphans = [
                m
                for m in (all_models or [])
                if m.base_model_id and m.base_model_id not in known_ids
            ]

            if not orphans:
                await self._emit_status(
                    __event_emitter__, "No orphaned workspace models found.", done=True
                )
                return json.dumps(
                    {
                        "status": "success",
                        "dry_run": dry_run,
                        "orphans": [],
                        "deleted": [],
                        "summary": {"orphans": 0, "deleted": 0, "errors": 0},
                        "message": "No orphaned workspace models found.",
                    }
                )

            orphan_list = [
                {"id": m.id, "name": m.name, "base_model_id": m.base_model_id}
                for m in orphans
            ]

            deleted = []
            errors = []
            if dry_run:
                summary = (
                    f"{len(orphans)} orphaned workspace model(s) would be deleted — dry run, "
                    "nothing deleted (turn off remove_orphaned_workspace_models_dry_run to delete)"
                )
            else:
                total = len(orphans)
                for idx, m in enumerate(orphans):
                    await self._emit_status(
                        __event_emitter__,
                        f"Deleting orphan ({idx + 1}/{total}): {m.id}",
                    )
                    try:
                        await Models.delete_model_by_id(m.id)
                        deleted.append(m.id)
                    except Exception as e:
                        log.debug(f"Error deleting orphaned model {m.id}: {e}")
                        errors.append({"model": m.id, "error": str(e)})
                summary = f"Deleted {len(deleted)} orphaned workspace model(s)"
                if errors:
                    summary += f", {len(errors)} error(s)"

            await self._emit_status(__event_emitter__, f"{pfx}{summary}", done=True)

            return json.dumps(
                {
                    "status": "success",
                    "dry_run": dry_run,
                    "orphans": orphan_list,
                    "deleted": deleted,
                    "errors": errors,
                    "summary": {
                        "orphans": len(orphans),
                        "deleted": len(deleted),
                        "errors": len(errors),
                    },
                    "message": summary,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            log.exception(f"remove_orphaned_workspace_models error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    # ------------------------------------------------------------------
    # Context length override (bulk apply)
    # ------------------------------------------------------------------

    async def apply_context_length_to_all_models(
        self,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Set num_ctx on EVERY model entry in the Open WebUI database to the
        ``context_length_override`` valve value.

        Affects both workspace models and base-model overrides that have a
        database entry. Models with no entry are untouched (they inherit
        Ollama's default). Set ``context_override_sets_max_tokens`` to also
        update max_tokens.

        With the ``apply_context_length_dry_run`` valve on, nothing is
        written: the response lists each model's current num_ctx and the
        value it WOULD get. With ``apply_context_length_reset`` on, the call
        removes num_ctx (and max_tokens if configured) from every entry
        instead, so models fall back to Ollama's defaults.

        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with updated, unchanged, and error counts
        """
        err = self._validate_tool_access(
            "apply_context_length_to_all_models",
            self.valves.enable_apply_context_length,
            __user__,
            __request__,
        )
        if err:
            return err

        dry_run = self.valves.apply_context_length_dry_run
        pfx = self._dry_prefix(dry_run)
        if self.valves.apply_context_length_reset:
            return await self._reset_context_length_all(dry_run, __event_emitter__)

        ctx = int(self.valves.context_length_override)
        if ctx <= 0:
            await self._emit_status(
                __event_emitter__,
                "context_length_override valve is 0 (disabled) — set it to a value first.",
                done=True,
            )
            return json.dumps(
                {
                    "status": "error",
                    "error": "context_length_override valve is 0 (disabled). Set it to the desired num_ctx first.",
                }
            )

        try:
            from open_webui.models.models import (
                ModelForm,
                ModelMeta,
                ModelParams,
                Models,
            )

            await self._emit_status(
                __event_emitter__, f"{pfx}Fetching all models from database..."
            )
            all_models = await Models.get_all_models()

            updated = []
            unchanged = []
            errors = []
            total = len(all_models or [])

            for idx, m in enumerate(all_models or []):
                await self._emit_status(
                    __event_emitter__,
                    f"{pfx}Applying num_ctx={ctx} ({idx + 1}/{total}): {m.id}",
                )
                try:
                    params_dict = m.params.model_dump() if m.params else {}
                    new_params = dict(params_dict)
                    new_params["num_ctx"] = ctx
                    if self.valves.context_override_sets_max_tokens:
                        new_params["max_tokens"] = min(ctx, 8192)

                    if new_params == params_dict:
                        unchanged.append(m.id)
                        continue

                    if dry_run:
                        updated.append(
                            {
                                "model": m.id,
                                "num_ctx_from": params_dict.get("num_ctx"),
                                "num_ctx_to": ctx,
                            }
                        )
                        continue

                    form = ModelForm(
                        id=m.id,
                        base_model_id=m.base_model_id,
                        name=m.name,
                        meta=ModelMeta(**(m.meta.model_dump() if m.meta else {})),
                        params=ModelParams(**new_params),
                        is_active=m.is_active,
                    )
                    if await Models.update_model_by_id(m.id, form) is None:
                        errors.append(
                            {"model": m.id, "error": "database update failed"}
                        )
                        continue
                    updated.append(m.id)
                except Exception as e:
                    log.debug(f"Error applying context length to {m.id}: {e}")
                    errors.append({"model": m.id, "error": str(e)})

            verb = "would be updated" if dry_run else "updated"
            summary_parts = [f"{len(updated)} {verb} to num_ctx={ctx}"]
            if unchanged:
                summary_parts.append(f"{len(unchanged)} already set")
            if errors:
                summary_parts.append(f"{len(errors)} error(s)")
            summary = ", ".join(summary_parts)
            if dry_run:
                summary += " — dry run, nothing written (turn off apply_context_length_dry_run to apply)"

            await self._emit_status(
                __event_emitter__, f"{pfx}Done: {summary}", done=True
            )

            return json.dumps(
                {
                    "status": "success",
                    "dry_run": dry_run,
                    "num_ctx": ctx,
                    ("would_update" if dry_run else "updated"): updated,
                    "unchanged": unchanged,
                    "errors": errors,
                    "summary": {
                        "total": total,
                        ("would_update" if dry_run else "updated"): len(updated),
                        "unchanged": len(unchanged),
                        "errors": len(errors),
                    },
                    "message": summary,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            log.exception(f"apply_context_length_to_all_models error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    # ------------------------------------------------------------------
    # Profile images (bulk refresh, workspace models included)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_default_avatar(url: Optional[str]) -> bool:
        return not url or url.strip() in ("/static/favicon.png", "/favicon.png")

    async def apply_profile_images_to_all_models(
        self,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Assign a profile image (avatar) to EVERY model entry in the database,
        including existing "-workspace" models that other bulk tools skip.

        Images come from ``profile_image_dir`` when a file matches the
        model's name or vendor; otherwise a monogram avatar is generated
        (``generate_avatar_fallback``). Entries that already have a custom
        avatar are left alone unless ``overwrite_profile_image`` is on.

        With the ``apply_profile_images_dry_run`` valve on, nothing is
        written: the response lists which models WOULD get an avatar and
        whether it comes from an icon file or a generated monogram. With
        ``apply_profile_images_reset`` on, every entry's avatar is put back
        to the default logo instead.

        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with updated, skipped, and error counts
        """
        err = self._validate_tool_access(
            "apply_profile_images_to_all_models",
            self.valves.enable_apply_profile_images,
            __user__,
            __request__,
        )
        if err:
            return err

        dry_run = self.valves.apply_profile_images_dry_run
        pfx = self._dry_prefix(dry_run)
        if self.valves.apply_profile_images_reset:
            return await self._reset_profile_images_all(dry_run, __event_emitter__)

        try:
            from open_webui.models.models import (
                ModelForm,
                ModelMeta,
                ModelParams,
                Models,
            )

            await self._emit_status(
                __event_emitter__, f"{pfx}Fetching models from database..."
            )
            all_models = await Models.get_all_models()

            # Ollama /api/tags data enriches tag detection (quantisation etc.)
            # but is not required — vendor rules work from the name alone.
            try:
                ollama_models = await self._get_ollama_models(__request__)
            except Exception as e:
                log.debug(f"Ollama model list unavailable, using names only: {e}")
                ollama_models = {}

            updated = []
            skipped = []
            errors = []
            total = len(all_models or [])

            for idx, m in enumerate(all_models or []):
                await self._emit_status(
                    __event_emitter__,
                    f"{pfx}Assigning avatar ({idx + 1}/{total}): {m.id}",
                )
                try:
                    meta_dict = m.meta.model_dump() if m.meta else {}
                    current = meta_dict.get("profile_image_url")
                    replaceable = self._is_default_avatar(current) or bool(
                        meta_dict.get(self._GENERATED_FLAG)
                    )
                    if not replaceable and not self.valves.overwrite_profile_image:
                        skipped.append(m.id)
                        continue

                    # Workspace models resolve through their base Ollama model
                    ollama_id = m.base_model_id or m.id
                    tags_info = ollama_models.get(ollama_id, {}) or {}
                    tags = self._detect_tags(ollama_id, "", [], {}, None, tags_info)
                    image, generated = self._resolve_profile_image_ex(
                        ollama_id, "", [], tags
                    )
                    # A monogram never replaces a hand-picked avatar, even with
                    # overwrite on; a real icon may.
                    if not image or image == current or (generated and not replaceable):
                        skipped.append(m.id)
                        continue

                    if dry_run:
                        updated.append(
                            {
                                "model": m.id,
                                "source": "monogram" if generated else "icon",
                                "replaces": self._summarize_value(
                                    "profile_image_url", current
                                )
                                or (
                                    "generated monogram"
                                    if meta_dict.get(self._GENERATED_FLAG)
                                    else "default logo"
                                ),
                            }
                        )
                        continue

                    meta_dict["profile_image_url"] = image
                    if generated:
                        meta_dict[self._GENERATED_FLAG] = True
                    else:
                        meta_dict.pop(self._GENERATED_FLAG, None)
                    form = ModelForm(
                        id=m.id,
                        base_model_id=m.base_model_id,
                        name=m.name,
                        meta=ModelMeta(**meta_dict),
                        params=m.params if m.params else ModelParams(),
                        is_active=m.is_active,
                    )
                    if await Models.update_model_by_id(m.id, form) is None:
                        errors.append(
                            {"model": m.id, "error": "database update failed"}
                        )
                        continue
                    updated.append(m.id)
                except Exception as e:
                    log.debug(f"Error assigning avatar to {m.id}: {e}")
                    errors.append({"model": m.id, "error": str(e)})

            verb = "avatar(s) would be assigned" if dry_run else "avatar(s) assigned"
            summary_parts = [f"{len(updated)} {verb}"]
            if dry_run and updated:
                n_icon = sum(1 for u in updated if u["source"] == "icon")
                summary_parts[
                    0
                ] += f" ({n_icon} from icon files, {len(updated) - n_icon} monograms)"
            if skipped:
                summary_parts.append(
                    f"{len(skipped)} skipped (already custom or no image)"
                )
            if errors:
                summary_parts.append(f"{len(errors)} error(s)")
            summary = ", ".join(summary_parts)
            if dry_run:
                summary += " — dry run, nothing written (turn off apply_profile_images_dry_run to apply)"

            await self._emit_status(
                __event_emitter__, f"{pfx}Done: {summary}", done=True
            )

            return json.dumps(
                {
                    "status": "success",
                    "dry_run": dry_run,
                    ("would_update" if dry_run else "updated"): updated,
                    "skipped": skipped,
                    "errors": errors,
                    "summary": {
                        "total": total,
                        ("would_update" if dry_run else "updated"): len(updated),
                        "skipped": len(skipped),
                        "errors": len(errors),
                    },
                    "message": summary,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            log.exception(f"apply_profile_images_to_all_models error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    # ------------------------------------------------------------------
    # Base model visibility (hide bases behind their workspace models)
    # ------------------------------------------------------------------

    async def apply_base_model_visibility_to_all_models(
        self,
        __request__=None,
        __user__: dict = None,
        __event_emitter__=None,
    ) -> str:
        """
        Hide or unhide every Ollama base model that has a workspace model,
        according to the ``hide_base_models`` valve.

        With the valve ON, each such base model is hidden from the model
        selector (its base-model override gets ``meta.hidden``; a minimal
        override is created when none exists) so only the "-workspace"
        version is offered. The base stays active, so the workspace model
        keeps working. Base models WITHOUT a workspace model are never
        hidden. With the valve OFF, the bases this valve hid are unhidden
        again (overrides it created are deleted). Entries hidden by other
        means (e.g. embedding models) are left alone.

        With the ``apply_base_model_visibility_dry_run`` valve on, nothing
        is written: the response lists what WOULD change.

        :param __request__: FastAPI request object (injected)
        :param __user__: User info dictionary (injected)
        :param __event_emitter__: Event emitter (injected)
        :return: JSON with changed, unchanged, no-workspace and error lists
        """
        err = self._validate_tool_access(
            "apply_base_model_visibility_to_all_models",
            self.valves.enable_apply_base_model_visibility,
            __user__,
            __request__,
        )
        if err:
            return err

        dry_run = self.valves.apply_base_model_visibility_dry_run
        hide = self.valves.hide_base_models
        pfx = self._dry_prefix(dry_run)
        verb = "hide" if hide else "unhide"

        try:
            from open_webui.models.models import Models

            await self._emit_status(__event_emitter__, f"{pfx}Fetching models...")
            ollama_models = await self._get_ollama_models(__request__)
            all_models = await Models.get_all_models()
            wrapped = {m.base_model_id for m in (all_models or []) if m.base_model_id}

            targets = [mid for mid in ollama_models if mid in wrapped]
            no_workspace = [mid for mid in ollama_models if mid not in wrapped]
            user_id = (__user__ or {}).get("id", "")

            changed = []
            unchanged = []
            errors = []
            total = len(targets)
            for idx, mid in enumerate(targets):
                await self._emit_status(
                    __event_emitter__, f"{pfx}{verb} ({idx + 1}/{total}): {mid}"
                )
                try:
                    r = await self._set_base_hidden(mid, hide, user_id, dry_run)
                    if r["action"] in ("unchanged", "skip"):
                        unchanged.append({"model": mid, "reason": r.get("reason", "")})
                    else:
                        changed.append({"model": mid, "action": r["action"]})
                except Exception as e:
                    log.debug(f"Error setting visibility for {mid}: {e}")
                    errors.append({"model": mid, "error": str(e)})

            n_created = sum(
                1 for c in changed if c["action"] == "create_hidden_override"
            )
            n_deleted = sum(
                1 for c in changed if c["action"] == "delete_hidden_override"
            )
            if hide:
                head = f"{len(changed)} base model(s) {'would be ' if dry_run else ''}hidden"
                if n_created:
                    head += f" ({n_created} hidden override(s) {'would be ' if dry_run else ''}created)"
            else:
                head = f"{len(changed)} base model(s) {'would be ' if dry_run else ''}unhidden"
                if n_deleted:
                    head += f" ({n_deleted} tool-created override(s) {'would be ' if dry_run else ''}deleted)"
            parts = [head]
            if unchanged:
                parts.append(f"{len(unchanged)} unchanged")
            if no_workspace:
                parts.append(f"{len(no_workspace)} left visible (no workspace model)")
            if errors:
                parts.append(f"{len(errors)} error(s)")
            summary = ", ".join(parts)
            if dry_run:
                summary += " — dry run, nothing written (turn off apply_base_model_visibility_dry_run to apply)"

            await self._emit_status(
                __event_emitter__, f"{pfx}Done: {summary}", done=True
            )

            return json.dumps(
                {
                    "status": "success",
                    "dry_run": dry_run,
                    "hide_base_models": hide,
                    ("would_change" if dry_run else "changed"): changed,
                    "unchanged": unchanged,
                    "no_workspace_model": no_workspace,
                    "errors": errors,
                    "summary": {
                        "total_with_workspace": total,
                        ("would_change" if dry_run else "changed"): len(changed),
                        "unchanged": len(unchanged),
                        "no_workspace_model": len(no_workspace),
                        "errors": len(errors),
                    },
                    "message": summary,
                },
                ensure_ascii=False,
            )

        except Exception as e:
            log.exception(f"apply_base_model_visibility_to_all_models error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    # ------------------------------------------------------------------
    # Reset siblings — the apply/create tools in undo mode
    # ------------------------------------------------------------------

    _WORKSPACE_SUFFIX = "-workspace"
    _LEGACY_WORKSPACE_SUFFIX = " (Workspace)"

    def _looks_tool_made(self, m) -> bool:
        """True when a DB entry carries this tool's fingerprints (card
        footer, generated avatar, hidden-base flags)."""
        meta = m.meta.model_dump() if m.meta else {}
        if any(
            meta.get(k)
            for k in (self._GENERATED_FLAG, self._HIDDEN_FLAG, self._CREATED_FLAG)
        ):
            return True
        desc = meta.get("description") or ""
        return bool(self._GENERATED_STAMP_RE.search(desc))

    def _select_override_entries(self, all_models, ollama_ids: set) -> list:
        """Base-model override entries that belong to Ollama models: any
        whose ID is an installed Ollama model, plus stale ones carrying
        this tool's fingerprints."""
        return [
            m
            for m in (all_models or [])
            if m.base_model_id is None
            and (m.id in ollama_ids or self._looks_tool_made(m))
        ]

    async def _lookup_entry(self, model_id: str):
        from open_webui.models.models import Models

        m = await Models.get_model_by_id(model_id)
        if m is None and ":" not in model_id:
            m = await Models.get_model_by_id(f"{model_id}:latest")
        return m

    async def _delete_entries(self, entries, __event_emitter__, pfx: str, label: str):
        """Delete *entries*; returns (deleted_ids, errors)."""
        from open_webui.models.models import Models

        deleted, errors = [], []
        total = len(entries)
        for idx, m in enumerate(entries):
            await self._emit_status(
                __event_emitter__, f"{pfx}Deleting {label} ({idx + 1}/{total}): {m.id}"
            )
            try:
                if await Models.delete_model_by_id(m.id) is False:
                    raise RuntimeError("database delete failed")
                deleted.append(m.id)
            except Exception as e:
                log.debug(f"Error deleting {m.id}: {e}")
                errors.append({"model": m.id, "error": str(e)})
        return deleted, errors

    async def _rewrite_entries(
        self, entries, mutate, dry_run: bool, __event_emitter__, pfx: str, label: str
    ):
        """Apply *mutate(meta_dict, params_dict) -> info | None* to each
        entry and write back the ones that changed (unless dry_run).
        Returns (changed_infos, unchanged_ids, errors)."""
        from open_webui.models.models import ModelForm, ModelMeta, ModelParams, Models

        changed, unchanged, errors = [], [], []
        total = len(entries)
        for idx, m in enumerate(entries):
            await self._emit_status(
                __event_emitter__, f"{pfx}{label} ({idx + 1}/{total}): {m.id}"
            )
            try:
                meta = m.meta.model_dump() if m.meta else {}
                params = m.params.model_dump() if m.params else {}
                info = mutate(meta, params)
                if info is None:
                    unchanged.append(m.id)
                    continue
                if not dry_run:
                    form = ModelForm(
                        id=m.id,
                        base_model_id=m.base_model_id,
                        name=m.name,
                        meta=ModelMeta(**meta),
                        params=ModelParams(**params),
                        is_active=m.is_active,
                    )
                    if await Models.update_model_by_id(m.id, form) is None:
                        raise RuntimeError("database update failed")
                changed.append({"model": m.id, **info})
            except Exception as e:
                log.debug(f"Error rewriting {m.id}: {e}")
                errors.append({"model": m.id, "error": str(e)})
        return changed, unchanged, errors

    @staticmethod
    def _reset_result(dry_run, key, items, extra=None, errors=None, message=""):
        """key is the verb: 'delete' -> would_delete / deleted, 'reset' -> would_reset / reset."""
        errors = errors or []
        done = {"delete": "deleted", "reset": "reset"}.get(key, key)
        label = f"would_{key}" if dry_run else done
        out = {
            "status": "success",
            "dry_run": dry_run,
            "reset": True,
            label: items,
            "errors": errors,
            "summary": {label: len(items), "errors": len(errors)},
            "message": message,
        }
        for k, v in (extra or {}).items():
            out[k] = v
            if isinstance(v, list):
                out["summary"][k] = len(v)
        return json.dumps(out, ensure_ascii=False)

    async def _reset_apply(self, model: str, dry_run: bool, __event_emitter__) -> str:
        """Undo apply_model_card: delete the model's base-model override entry."""
        pfx = self._dry_prefix(dry_run)
        try:
            entry = await self._lookup_entry(model)
            if entry is None:
                msg = f"{pfx}{model}: no database entry — nothing to reset (the Ollama model is already raw)"
                await self._emit_status(__event_emitter__, msg, done=True)
                return self._reset_result(dry_run, "delete", [], message=msg)
            if entry.base_model_id is not None:
                msg = f"'{entry.id}' is a workspace model, not a base-model entry. Use create_workspace_model with create_workspace_model_reset on."
                await self._emit_status(__event_emitter__, msg, done=True)
                return json.dumps({"status": "error", "error": msg})
            if dry_run:
                msg = f"{pfx}would delete override entry '{entry.id}' ('{entry.name}') — the Ollama model stays installed and shows raw again"
                await self._emit_status(__event_emitter__, msg, done=True)
                return self._reset_result(
                    True, "delete", [{"id": entry.id, "name": entry.name}], message=msg
                )
            deleted, errors = await self._delete_entries(
                [entry], __event_emitter__, pfx, "base override"
            )
            if errors:
                raise RuntimeError(errors[0]["error"])
            msg = f"Reset {entry.id}: override entry deleted — the Ollama model shows raw again"
            await self._emit_status(__event_emitter__, msg, done=True)
            return self._reset_result(False, "delete", deleted, message=msg)
        except Exception as e:
            log.exception(f"apply_model_card reset error for {model}: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    async def _reset_apply_all(
        self, dry_run: bool, __request__, __event_emitter__
    ) -> str:
        """Undo apply_all_model_cards: delete every Ollama base-model override entry."""
        pfx = self._dry_prefix(dry_run)
        try:
            from open_webui.models.models import Models

            await self._emit_status(__event_emitter__, f"{pfx}Fetching models...")
            try:
                ollama_ids = set((await self._get_ollama_models(__request__)).keys())
            except Exception as e:
                log.warning(f"Ollama model list unavailable during reset: {e}")
                ollama_ids = set()
            targets = self._select_override_entries(
                await Models.get_all_models(), ollama_ids
            )
            if dry_run:
                items = [{"id": m.id, "name": m.name} for m in targets]
                errors = []
            else:
                items, errors = await self._delete_entries(
                    targets, __event_emitter__, pfx, "base override"
                )
            msg = f"{len(items)} base-model override entr(y/ies) {'would be ' if dry_run else ''}deleted — the Ollama models stay installed and show raw"
            if errors:
                msg += f", {len(errors)} error(s)"
            if not ollama_ids:
                msg += ". NOTE: Ollama model list was empty, so only entries carrying this tool's fingerprints were selected"
            if dry_run:
                msg += " — dry run, nothing deleted (turn off apply_all_model_cards_dry_run to reset for real)"
            await self._emit_status(__event_emitter__, f"{pfx}Done: {msg}", done=True)
            return self._reset_result(
                dry_run, "delete", items, errors=errors, message=msg
            )
        except Exception as e:
            log.exception(f"apply_all_model_cards reset error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    async def _reset_create(
        self, model: str, dry_run: bool, user_id: str, __event_emitter__
    ) -> str:
        """Undo create_workspace_model: delete the '-workspace' entry and unhide its base."""
        pfx = self._dry_prefix(dry_run)
        try:
            base_id = model
            entry = await self._lookup_entry(self._generate_workspace_id(model))
            if entry is None:
                entry = await self._lookup_entry(self._legacy_workspace_id(model))
            if entry is not None and entry.base_model_id:
                base_id = entry.base_model_id
            unhide = await self._set_base_hidden(base_id, False, user_id, dry_run=True)
            will_unhide = unhide["action"] in ("unhide", "delete_hidden_override")
            if entry is None and not will_unhide:
                msg = f"{pfx}{model}: no '-workspace' entry and base not hidden by this tool — nothing to reset"
                await self._emit_status(__event_emitter__, msg, done=True)
                return self._reset_result(
                    dry_run, "delete", [], extra={"unhidden": []}, message=msg
                )
            parts = []
            if entry is not None:
                parts.append(f"delete workspace model '{entry.id}'")
            if will_unhide:
                parts.append(f"unhide base model '{base_id}' ({unhide['action']})")
            if dry_run:
                msg = f"{pfx}would " + " and ".join(parts)
                await self._emit_status(__event_emitter__, msg, done=True)
                return self._reset_result(
                    True,
                    "delete",
                    [{"id": entry.id, "name": entry.name}] if entry else [],
                    extra={"unhidden": [base_id] if will_unhide else []},
                    message=msg,
                )
            deleted, errors = ([], [])
            if entry is not None:
                deleted, errors = await self._delete_entries(
                    [entry], __event_emitter__, pfx, "workspace model"
                )
                if errors:
                    raise RuntimeError(errors[0]["error"])
            unhidden = []
            if will_unhide:
                r = await self._set_base_hidden(base_id, False, user_id)
                if r["action"] in ("unhide", "delete_hidden_override"):
                    unhidden.append(base_id)
            msg = "Reset: " + " and ".join(parts).replace(
                "delete ", "deleted "
            ).replace("unhide ", "unhid ")
            await self._emit_status(__event_emitter__, msg, done=True)
            return self._reset_result(
                False, "delete", deleted, extra={"unhidden": unhidden}, message=msg
            )
        except Exception as e:
            log.exception(f"create_workspace_model reset error for {model}: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    async def _reset_create_all(
        self, dry_run: bool, user_id: str, __event_emitter__
    ) -> str:
        """Undo create_all_workspace_models: delete every '-workspace' model, unhide tool-hidden bases."""
        pfx = self._dry_prefix(dry_run)
        try:
            from open_webui.models.models import Models

            await self._emit_status(__event_emitter__, f"{pfx}Fetching models...")
            all_models = await Models.get_all_models() or []
            ws_entries = [
                m
                for m in all_models
                if m.base_model_id
                and (
                    m.id.endswith(self._WORKSPACE_SUFFIX)
                    or m.id.endswith(self._LEGACY_WORKSPACE_SUFFIX)
                )
            ]
            hidden_bases = sorted(
                {m.base_model_id for m in ws_entries}
                | {
                    m.id
                    for m in all_models
                    if m.base_model_id is None
                    and (m.meta.model_dump() if m.meta else {}).get(self._HIDDEN_FLAG)
                }
            )
            if dry_run:
                items = [{"id": m.id, "name": m.name} for m in ws_entries]
                errors = []
            else:
                items, errors = await self._delete_entries(
                    ws_entries, __event_emitter__, pfx, "workspace model"
                )
            unhidden = []
            for base_id in hidden_bases:
                try:
                    r = await self._set_base_hidden(base_id, False, user_id, dry_run)
                    if r["action"] in ("unhide", "delete_hidden_override"):
                        unhidden.append(base_id)
                except Exception as e:
                    errors.append({"model": base_id, "error": str(e)})
            w = "would be " if dry_run else ""
            msg = f"{len(items)} workspace model(s) {w}deleted, {len(unhidden)} base model(s) {w}unhidden"
            if errors:
                msg += f", {len(errors)} error(s)"
            if dry_run:
                msg += " — dry run, nothing written (turn off create_all_workspace_models_dry_run to reset for real)"
            await self._emit_status(__event_emitter__, f"{pfx}Done: {msg}", done=True)
            return self._reset_result(
                dry_run,
                "delete",
                items,
                extra={"unhidden": unhidden},
                errors=errors,
                message=msg,
            )
        except Exception as e:
            log.exception(f"create_all_workspace_models reset error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    async def _reset_context_length_all(self, dry_run: bool, __event_emitter__) -> str:
        """Undo apply_context_length_to_all_models: drop num_ctx (and max_tokens) everywhere."""
        pfx = self._dry_prefix(dry_run)
        drop_max = self.valves.context_override_sets_max_tokens

        def mutate(meta, params):
            if "num_ctx" not in params and not (drop_max and "max_tokens" in params):
                return None
            info = {"num_ctx_removed": params.pop("num_ctx", None)}
            if drop_max:
                info["max_tokens_removed"] = params.pop("max_tokens", None)
            return info

        try:
            from open_webui.models.models import Models

            await self._emit_status(__event_emitter__, f"{pfx}Fetching models...")
            changed, unchanged, errors = await self._rewrite_entries(
                await Models.get_all_models() or [],
                mutate,
                dry_run,
                __event_emitter__,
                pfx,
                "Removing num_ctx",
            )
            w = "would be " if dry_run else ""
            msg = f"num_ctx {w}removed from {len(changed)} model(s), {len(unchanged)} had none"
            if errors:
                msg += f", {len(errors)} error(s)"
            if dry_run:
                msg += " — dry run, nothing written (turn off apply_context_length_dry_run to reset for real)"
            await self._emit_status(__event_emitter__, f"{pfx}Done: {msg}", done=True)
            return self._reset_result(
                dry_run,
                "reset",
                changed,
                extra={"unchanged": unchanged},
                errors=errors,
                message=msg,
            )
        except Exception as e:
            log.exception(f"apply_context_length reset error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})

    async def _reset_profile_images_all(self, dry_run: bool, __event_emitter__) -> str:
        """Undo apply_profile_images_to_all_models: default logo everywhere."""
        pfx = self._dry_prefix(dry_run)

        def mutate(meta, params):
            current = meta.get("profile_image_url")
            if self._is_default_avatar(current) and not meta.get(self._GENERATED_FLAG):
                return None
            info = {
                "was": self._summarize_value("profile_image_url", current)
                or (
                    "generated monogram"
                    if meta.get(self._GENERATED_FLAG)
                    else "default logo"
                )
            }
            meta["profile_image_url"] = "/static/favicon.png"
            meta.pop(self._GENERATED_FLAG, None)
            return info

        try:
            from open_webui.models.models import Models

            await self._emit_status(__event_emitter__, f"{pfx}Fetching models...")
            changed, unchanged, errors = await self._rewrite_entries(
                await Models.get_all_models() or [],
                mutate,
                dry_run,
                __event_emitter__,
                pfx,
                "Resetting avatar",
            )
            w = "would be " if dry_run else ""
            msg = f"{len(changed)} avatar(s) {w}reset to the default logo, {len(unchanged)} already default"
            if errors:
                msg += f", {len(errors)} error(s)"
            if dry_run:
                msg += " — dry run, nothing written (turn off apply_profile_images_dry_run to reset for real)"
            await self._emit_status(__event_emitter__, f"{pfx}Done: {msg}", done=True)
            return self._reset_result(
                dry_run,
                "reset",
                changed,
                extra={"unchanged": unchanged},
                errors=errors,
                message=msg,
            )
        except Exception as e:
            log.exception(f"apply_profile_images reset error: {e}")
            await self._emit_status(__event_emitter__, f"Error: {e}", done=True)
            return json.dumps({"status": "error", "error": str(e)})
