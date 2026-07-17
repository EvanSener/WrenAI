"""Embedding function abstraction for Wren Memory.

Uses LanceDB's embedding registry with sentence-transformers (local, no API key).
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

_DEFAULT_MODEL = os.getenv(
    "WREN_EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2"
)
_DEFAULT_DIM = 384


def _disable_transformers_progress_bar() -> None:
    # Imported lazily: transformers ships with the optional `memory` extra,
    # so this module must stay importable when that extra is not installed.
    from transformers.utils import logging as transformers_logging  # noqa: PLC0415

    transformers_logging.disable_progress_bar()


def get_embedding_function(model_name: str = _DEFAULT_MODEL):
    """Return a LanceDB sentence-transformers embedding function.

    The returned object implements ``compute_source_embeddings(texts)``
    and ``compute_query_embeddings(query)`` used by :class:`MemoryStore`.
    """
    _disable_transformers_progress_bar()

    import lancedb.embeddings  # noqa: PLC0415

    registry = lancedb.embeddings.get_registry()
    return registry.get("sentence-transformers").create(
        name=_prefer_cached_snapshot(model_name)
    )


def _prefer_cached_snapshot(model_name: str) -> str:
    """Use an already-downloaded HuggingFace snapshot without a network HEAD.

    SentenceTransformers otherwise checks the Hub even when every model file is
    cached.  Resolving the official cache entry to its local snapshot path keeps
    normal online first-use behavior while making subsequent Wren Memory starts
    deterministic and fully offline.
    """

    expanded = Path(model_name).expanduser()
    if expanded.exists():
        return str(expanded.resolve())
    repo_id = model_name if "/" in model_name else f"sentence-transformers/{model_name}"
    try:
        from huggingface_hub import snapshot_download  # noqa: PLC0415
        from huggingface_hub.errors import LocalEntryNotFoundError  # noqa: PLC0415

        return snapshot_download(repo_id, local_files_only=True)
    except (ImportError, LocalEntryNotFoundError, OSError, ValueError):
        return model_name


@contextlib.contextmanager
def suppress_stderr():
    """Temporarily redirect stderr to /dev/null.

    Suppresses noisy native output (progress bars, load reports) from
    sentence-transformers / candle during model loading.
    """
    old_fd = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 2)
    os.close(devnull)
    try:
        yield
    finally:
        os.dup2(old_fd, 2)
        os.close(old_fd)


def warm_up(embed_fn):
    """Trigger model loading silently and return the vector dimension."""
    _disable_transformers_progress_bar()
    with suppress_stderr():
        probe = embed_fn.compute_source_embeddings(["probe"])
    return len(probe[0])


def default_dimension() -> int:
    """Return the vector dimension for the default model."""
    return _DEFAULT_DIM
