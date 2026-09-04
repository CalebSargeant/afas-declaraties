"""Credential retrieval via the 1Password CLI.

The service account token is never referenced here. It is read by ``op`` itself
from ``OP_SERVICE_ACCOUNT_TOKEN`` in the process environment, which is what
makes this work headlessly in a container with no desktop app and no
interactive unlock.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30

#: Run ``op`` inside this container image instead of using the local binary.
#:
#: On a workstation the 1Password CLI talks to the desktop app over
#: ``~/.config/op/op-daemon.sock`` and blocks forever when the app is locked --
#: even with a valid service account token and a clean environment. A container
#: has no desktop app, so ``op`` uses the token directly. This is also exactly
#: how the CLI runs in production, which makes it the faithful path rather than
#: a workaround.
_DOCKER_IMAGE_ENV = "OP_DOCKER_IMAGE"


class OnePasswordError(RuntimeError):
    """A call to the 1Password CLI failed."""


def _command(args: list[str]) -> list[str]:
    image = os.environ.get(_DOCKER_IMAGE_ENV)
    if not image:
        return ["op", *args]

    token = os.environ.get("OP_SERVICE_ACCOUNT_TOKEN")
    if not token:
        raise OnePasswordError(
            f"{_DOCKER_IMAGE_ENV} is set but OP_SERVICE_ACCOUNT_TOKEN is not. "
            "The containerised CLI has no desktop app to fall back to."
        )
    # The token is passed by name, so it is never visible in the argv of the
    # docker process (which is world-readable via `ps`).
    return [
        "docker",
        "run",
        "--rm",
        "-i",
        "-e",
        "OP_SERVICE_ACCOUNT_TOKEN",
        image,
        "op",
        *args,
    ]


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(
            _command(args),
            capture_output=True,
            text=True,
            check=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise OnePasswordError("neither 'op' nor 'docker' is available on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        # Locally this means the desktop-app daemon is waiting on a biometric
        # unlock. In the cluster it means the service account token is missing
        # or invalid -- op falls back to an interactive path that never returns.
        raise OnePasswordError(
            f"'op {' '.join(args)}' timed out after {_TIMEOUT_SECONDS}s. "
            "Check OP_SERVICE_ACCOUNT_TOKEN is set, or unlock the desktop app locally."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise OnePasswordError(f"'op {' '.join(args)}' failed: {exc.stderr.strip()}") from exc
    return result.stdout


def get_field(item: str, field: str, vault: str = "Private") -> str:
    """Return one field from a 1Password item.

    Matches on both ``id`` and ``label``: built-in fields have stable ids
    (``username``, ``password``) while custom fields only carry a label.
    """
    logger.debug("fetching field %r from item %r", field, item)
    payload = _run(["item", "get", item, "--vault", vault, "--format", "json"])

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise OnePasswordError(f"could not parse the response for item {item!r}") from exc

    for entry in data.get("fields", []):
        if entry.get("id") == field or entry.get("label") == field:
            value = entry.get("value")
            if not value:
                raise OnePasswordError(f"field {field!r} on item {item!r} is empty")
            return value

    raise OnePasswordError(f"field {field!r} not found on item {item!r}")


def get_totp(item: str, vault: str = "Private") -> str:
    """Return a current TOTP code.

    A separate invocation because ``--otp`` and ``--format json`` are mutually
    exclusive. Call this at the moment the code is needed, never up front: the
    window is 30 seconds and page loads eat most of it.
    """
    logger.debug("fetching TOTP for item %r", item)
    code = _run(["item", "get", item, "--vault", vault, "--otp"]).strip()
    if not code:
        raise OnePasswordError(f"item {item!r} returned an empty TOTP -- is one configured?")
    return code
