"""The optional `TableGeneration/Submission/summary.tex`: the one rule set, shared by the hosted verifier, the intake, the archive and `./forshor`.

A submission may carry a LaTeX summary beside its policy. It is TEXT FOR PEOPLE TO READ and nothing else:

  * it is never compiled, rendered, included or executed by any script or workflow here (TeX can run shell commands: `\\write18`, `\\input{/etc/passwd}` and the like are
    only ever bytes in a file), it is stored byte for byte, and nothing in it can change what is verified: the policy's digest is computed from the policy alone;
  * it is optional; when present it must satisfy the rules below, and a file that breaks one is refused with a plain message naming the rule (a refusal of the file, not
    a verdict on the policy);
  * the same rules run in the verify job, again in the trusted publish job (which also compares the file at the pull request's head commit with what was verified),
    and in `./forshor check`: this module is the single definition, so those places cannot drift.

The rules (`problem` for the bytes, `inspect_file` for the path):

  1. a regular file: not a symbolic link, not a directory, not anything else;
  2. not empty (a blank file says nothing; delete it instead);
  3. at most `MAX_BYTES` = 100 KiB (102,400 bytes): about twenty-five pages of dense LaTeX, ten times the size of any summary worth reading, small enough that it can be
     shown in a comment, kept in the archive and diffed in review, and a tenth of what the policy file itself may be;
  4. valid UTF-8 (strict: no overlong forms, no surrogates);
  5. no control characters except tab, line feed and carriage return: that is, no NUL and no other Unicode `Cc` character (C0, DEL and C1).

Pure functions, standard library only.
"""
from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from pathlib import Path
from typing import Optional, Tuple

SUMMARY_PATH = "TableGeneration/Submission/summary.tex"
MAX_BYTES = 100 * 1024
_ALLOWED_CONTROL = {"\t", "\n", "\r"}
NAME = "summary.tex"


def problem(data: bytes) -> Optional[str]:
    """The rule `data` (the file's bytes) breaks, in plain words, or None when it is a valid summary."""
    if not data.strip():
        return f"{NAME} is empty. Delete the file, or write the summary in it."
    if len(data) > MAX_BYTES:
        return f"{NAME} is {len(data):,} bytes; the limit is {MAX_BYTES:,} bytes (100 KiB)."
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        return f"{NAME} must be UTF-8 text; the byte at offset {error.start} is not valid UTF-8."
    for index, char in enumerate(text):
        if char not in _ALLOWED_CONTROL and unicodedata.category(char) == "Cc":
            return (f"{NAME} contains the control character U+{ord(char):04X} (character {index + 1}); "
                    "only tab, line feed and carriage return are allowed.")
    return None


def inspect_file(path: Path) -> Tuple[Optional[bytes], Optional[str]]:
    """`(bytes, None)` for a valid summary, `(None, None)` when there is no file, `(None, message)` when the path breaks a rule. Never follows a link: the check is on
    the directory entry itself, then the bytes are read from a descriptor opened without following links."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None, None
    if stat.S_ISLNK(info.st_mode):
        return None, f"{NAME} must be a regular file, not a symbolic link."
    if stat.S_ISDIR(info.st_mode):
        return None, f"{NAME} must be a regular file, not a directory."
    if not stat.S_ISREG(info.st_mode):
        return None, f"{NAME} must be a regular file."
    if info.st_size > MAX_BYTES:
        return None, f"{NAME} is {info.st_size:,} bytes; the limit is {MAX_BYTES:,} bytes (100 KiB)."
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None, f"{NAME} must be a regular file, not a symbolic link."
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    message = problem(data)
    return (None, message) if message else (data, None)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
