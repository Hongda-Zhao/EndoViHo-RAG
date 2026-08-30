#!/usr/bin/env python3
"""Compute a closed, RECORD-verified identity for a V0 provider environment.

The identity covers installed wheel distributions in ``site-packages`` and every
distribution-owned file, including scripts installed into ``bin``.  The Python
executable, standard library, activation helpers, and ``pyvenv.cfg`` are not
distribution artifacts and must be bound separately by the caller's policy.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import hashlib
import hmac
import io
import json
import os
import posixpath
import re
import stat
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.parser import BytesHeaderParser
from pathlib import Path, PurePosixPath
from typing import Final

IDENTITY_SCHEMA_VERSION: Final = "v0-provider-environment-identity-v1"
MANIFEST_SCHEMA_VERSION: Final = "v0-provider-environment-manifest-v1"

_DIST_INFO_SUFFIX: Final = ".dist-info"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_PROJECT_NAME_RE: Final = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")
_VERSION_RE: Final = re.compile(r"^[!-~]+$")
_PYTHON_LIBRARY_RE: Final = re.compile(r"^python\d+\.\d+$")
_PYTHON_EXECUTABLE_RE: Final = re.compile(r"^python(?:3(?:\.\d+)?)?(?:\.exe)?$")
_CACHE_FILE_RE: Final = re.compile(
    r"^.+\.(?:cpython|pypy)-\d+(?:\.opt-\d+)?\.py[co]$"
)
_DECIMAL_SIZE_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)$")
_ACTIVATION_HELPERS: Final = frozenset(
    {"activate", "activate.csh", "activate.fish", "Activate.ps1"}
)
_READ_CHUNK_SIZE: Final = 1024 * 1024
_MAX_RECORD_BYTES: Final = 32 * 1024 * 1024
_MAX_METADATA_BYTES: Final = 4 * 1024 * 1024
_MAX_SMALL_POLICY_FILE_BYTES: Final = 4 * 1024 * 1024


class ProviderEnvironmentError(RuntimeError):
    """The provider environment is incomplete, mutable, or outside policy."""


@dataclass(frozen=True, slots=True)
class ProviderFileIdentity:
    """Content identity for one distribution-owned regular file."""

    relative_path: str
    byte_size: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "byte_size": self.byte_size,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class ProviderDistributionIdentity:
    """Canonical installed-distribution identity derived from METADATA and RECORD."""

    canonical_name: str
    version: str
    record: ProviderFileIdentity
    files: tuple[ProviderFileIdentity, ...]

    @property
    def file_count(self) -> int:
        """Return identity files, including the physical RECORD file."""

        return len(self.files) + 1

    def as_dict(self) -> dict[str, object]:
        return {
            "canonical_name": self.canonical_name,
            "file_count": self.file_count,
            "files": [file.as_dict() for file in self.files],
            "record": self.record.as_dict(),
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ProviderEnvironmentIdentity:
    """Complete canonical identity of the provider's installed distributions."""

    distributions: tuple[ProviderDistributionIdentity, ...]
    semantic_sha256: str

    @property
    def distribution_count(self) -> int:
        return len(self.distributions)

    @property
    def file_count(self) -> int:
        return sum(distribution.file_count for distribution in self.distributions)

    @property
    def distribution_versions(self) -> dict[str, str]:
        return {
            distribution.canonical_name: distribution.version
            for distribution in self.distributions
        }

    def semantic_payload(self) -> dict[str, object]:
        """Return the payload whose canonical bytes define ``semantic_sha256``."""

        return {
            "distribution_count": self.distribution_count,
            "distributions": [
                distribution.as_dict() for distribution in self.distributions
            ],
            "file_count": self.file_count,
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        }

    def manifest(self) -> dict[str, object]:
        """Return the self-hashed compact manifest emitted by the CLI."""

        payload: dict[str, object] = {
            "distributions": [
                {
                    "canonical_name": distribution.canonical_name,
                    "file_count": distribution.file_count,
                    "record_sha256": distribution.record.sha256,
                    "version": distribution.version,
                }
                for distribution in self.distributions
            ],
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "manifest_sha256": "0" * 64,
            "provider_environment_distribution_count": self.distribution_count,
            "provider_environment_file_count": self.file_count,
            "provider_environment_sha256": self.semantic_sha256,
        }
        manifest_payload = dict(payload)
        del manifest_payload["manifest_sha256"]
        payload["manifest_sha256"] = hashlib.sha256(
            _canonical_json_bytes(manifest_payload)
        ).hexdigest()
        return payload

    def summary(self) -> dict[str, object]:
        """Compatibility alias for the compact manifest."""

        return self.manifest()


@dataclass(frozen=True, slots=True)
class _StatFingerprint:
    device: int
    inode: int
    mode: int
    byte_size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _ObservedFile:
    identity: ProviderFileIdentity
    fingerprint: _StatFingerprint
    content: bytes | None = None


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _write_new(path: Path, payload: bytes) -> None:
    try:
        parent_stat = path.parent.lstat()
    except OSError as exc:
        raise ProviderEnvironmentError("manifest output directory is unavailable") from exc
    if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
        raise ProviderEnvironmentError("manifest output directory is unavailable")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o644)
    except OSError as exc:
        raise ProviderEnvironmentError("manifest output must be one new regular file") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink(missing_ok=True)
        finally:
            raise


def _canonical_name(raw_name: str) -> str:
    if _PROJECT_NAME_RE.fullmatch(raw_name) is None:
        raise ProviderEnvironmentError("distribution METADATA contains an invalid Name")
    return re.sub(r"[-_.]+", "-", raw_name).lower()


def _positive_manifest_count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ProviderEnvironmentError("provider environment manifest count is invalid")
    return value


def _fingerprint(file_stat: os.stat_result) -> _StatFingerprint:
    return _StatFingerprint(
        device=file_stat.st_dev,
        inode=file_stat.st_ino,
        mode=file_stat.st_mode,
        byte_size=file_stat.st_size,
        modified_ns=file_stat.st_mtime_ns,
        changed_ns=file_stat.st_ctime_ns,
    )


def _validated_root(provider_root: Path) -> Path:
    supplied = provider_root.expanduser()
    try:
        supplied_stat = supplied.lstat()
    except OSError as exc:
        raise ProviderEnvironmentError("provider environment root is unavailable") from exc
    if stat.S_ISLNK(supplied_stat.st_mode) or not stat.S_ISDIR(supplied_stat.st_mode):
        raise ProviderEnvironmentError("provider environment root must be a real directory")
    try:
        return supplied.resolve(strict=True)
    except OSError as exc:
        raise ProviderEnvironmentError("provider environment root is unavailable") from exc


def _safe_relative_path(relative_path: str) -> tuple[str, ...]:
    pure = PurePosixPath(relative_path)
    if (
        not relative_path
        or pure.is_absolute()
        or "\\" in relative_path
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in relative_path)
    ):
        raise ProviderEnvironmentError("provider file path is invalid")
    parts = pure.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ProviderEnvironmentError("provider file path is not canonical")
    return parts


def _open_directory_fd(root_fd: int, relative_path: str) -> int:
    parts = _safe_relative_path(relative_path)
    current_fd = os.dup(root_fd)
    try:
        for part in parts:
            next_fd = os.open(
                part,
                os.O_RDONLY
                | os.O_CLOEXEC
                | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
    except OSError as exc:
        os.close(current_fd)
        raise ProviderEnvironmentError("provider directory is unavailable or symbolic") from exc
    return current_fd


def _try_open_directory_fd(root_fd: int, relative_path: str) -> int | None:
    path_parts = _safe_relative_path(relative_path)
    current_fd = os.dup(root_fd)
    for part in path_parts:
        try:
            next_fd = os.open(
                part,
                os.O_RDONLY
                | os.O_CLOEXEC
                | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=current_fd,
            )
        except FileNotFoundError:
            os.close(current_fd)
            return None
        except OSError as exc:
            os.close(current_fd)
            raise ProviderEnvironmentError(
                "provider directory is unavailable or symbolic"
            ) from exc
        os.close(current_fd)
        current_fd = next_fd
    return current_fd


def _open_regular_fd(root_fd: int, relative_path: str) -> int:
    parts = _safe_relative_path(relative_path)
    directory_fd = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY
                | os.O_CLOEXEC
                | os.O_DIRECTORY
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(
            parts[-1],
            os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise ProviderEnvironmentError("provider file is missing or symbolic") from exc
    finally:
        os.close(directory_fd)


def _observe_regular_file(
    root_fd: int,
    relative_path: str,
    *,
    collect_content: bool = False,
    maximum_bytes: int | None = None,
) -> _ObservedFile:
    file_fd = _open_regular_fd(root_fd, relative_path)
    try:
        initial_stat = os.fstat(file_fd)
        if not stat.S_ISREG(initial_stat.st_mode):
            raise ProviderEnvironmentError("provider artifact is not a regular file")
        if maximum_bytes is not None and initial_stat.st_size > maximum_bytes:
            raise ProviderEnvironmentError("provider metadata exceeds its size bound")
        digest = hashlib.sha256()
        collected = bytearray() if collect_content else None
        observed_size = 0
        while chunk := os.read(file_fd, _READ_CHUNK_SIZE):
            observed_size += len(chunk)
            digest.update(chunk)
            if collected is not None:
                collected.extend(chunk)
        final_stat = os.fstat(file_fd)
    finally:
        os.close(file_fd)
    initial_fingerprint = _fingerprint(initial_stat)
    if initial_fingerprint != _fingerprint(final_stat) or observed_size != final_stat.st_size:
        raise ProviderEnvironmentError("provider file changed while it was being read")
    return _ObservedFile(
        identity=ProviderFileIdentity(
            relative_path=relative_path,
            byte_size=observed_size,
            sha256=digest.hexdigest(),
        ),
        fingerprint=initial_fingerprint,
        content=bytes(collected) if collected is not None else None,
    )


def _stat_regular_file(root_fd: int, relative_path: str) -> _StatFingerprint:
    file_fd = _open_regular_fd(root_fd, relative_path)
    try:
        file_stat = os.fstat(file_fd)
    finally:
        os.close(file_fd)
    if not stat.S_ISREG(file_stat.st_mode):
        raise ProviderEnvironmentError("provider artifact is not a regular file")
    return _fingerprint(file_stat)


def _discover_site_packages(root_fd: int, root: Path) -> tuple[str, ...]:
    candidates: set[str] = set()
    for library_name in ("lib", "lib64"):
        library_path = root / library_name
        try:
            library_stat = library_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ProviderEnvironmentError("provider library root is unavailable") from exc
        if stat.S_ISLNK(library_stat.st_mode):
            if library_name == "lib64":
                continue
            raise ProviderEnvironmentError("provider library root may not be symbolic")
        if not stat.S_ISDIR(library_stat.st_mode):
            continue
        library_fd = _open_directory_fd(root_fd, library_name)
        try:
            with os.scandir(library_fd) as entries:
                names = sorted(entry.name for entry in entries)
        finally:
            os.close(library_fd)
        for name in names:
            if _PYTHON_LIBRARY_RE.fullmatch(name) is None:
                continue
            candidate = f"{library_name}/{name}/site-packages"
            candidate_fd = _try_open_directory_fd(root_fd, candidate)
            if candidate_fd is not None:
                os.close(candidate_fd)
                candidates.add(candidate)

    for candidate in ("Lib/site-packages", "site-packages"):
        candidate_fd = _try_open_directory_fd(root_fd, candidate)
        if candidate_fd is not None:
            os.close(candidate_fd)
            candidates.add(candidate)

    if not candidates:
        raise ProviderEnvironmentError("provider environment has no site-packages directory")
    return tuple(sorted(candidates))


def _discover_dist_info_directories(
    root_fd: int, site_package_paths: Sequence[str]
) -> tuple[tuple[str, str], ...]:
    discovered: list[tuple[str, str]] = []
    for site_packages in site_package_paths:
        site_fd = _open_directory_fd(root_fd, site_packages)
        try:
            with os.scandir(site_fd) as entries:
                for entry in sorted(entries, key=lambda item: item.name):
                    if not entry.name.endswith(_DIST_INFO_SUFFIX):
                        continue
                    entry_stat = entry.stat(follow_symlinks=False)
                    if not stat.S_ISDIR(entry_stat.st_mode):
                        raise ProviderEnvironmentError(
                            "dist-info entry must be a real directory"
                        )
                    discovered.append(
                        (site_packages, posixpath.join(site_packages, entry.name))
                    )
        finally:
            os.close(site_fd)
    if not discovered:
        raise ProviderEnvironmentError("provider environment has no installed distributions")
    return tuple(discovered)


def _resolve_record_target(site_packages: str, record_path: str) -> str:
    if (
        not record_path
        or PurePosixPath(record_path).is_absolute()
        or "\\" in record_path
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in record_path)
    ):
        raise ProviderEnvironmentError("RECORD contains an invalid path")
    record_parts = record_path.split("/")
    if any(part in {"", "."} for part in record_parts):
        raise ProviderEnvironmentError("RECORD path is not canonical")
    combined = list(PurePosixPath(site_packages).parts)
    for part in record_parts:
        if part == "..":
            if not combined:
                raise ProviderEnvironmentError("RECORD path escapes the provider root")
            combined.pop()
        else:
            combined.append(part)
    if not combined:
        raise ProviderEnvironmentError("RECORD path resolves to the provider root")
    normalized = "/".join(combined)
    _safe_relative_path(normalized)
    return normalized


def _decode_record_sha256(encoded_hash: str) -> str:
    if not encoded_hash.startswith("sha256="):
        raise ProviderEnvironmentError("RECORD requires a sha256 digest for every file")
    encoded_digest = encoded_hash.removeprefix("sha256=")
    if not encoded_digest:
        raise ProviderEnvironmentError("RECORD contains an empty sha256 digest")
    try:
        raw_digest = base64.b64decode(
            encoded_digest + "=" * (-len(encoded_digest) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise ProviderEnvironmentError("RECORD contains an invalid sha256 digest") from exc
    canonical = base64.urlsafe_b64encode(raw_digest).decode("ascii").rstrip("=")
    if len(raw_digest) != hashlib.sha256().digest_size or canonical != encoded_digest:
        raise ProviderEnvironmentError("RECORD contains a non-canonical sha256 digest")
    return raw_digest.hex()


def _is_runtime_cache(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path).parts
    return (
        len(parts) >= 2
        and parts[-2] == "__pycache__"
        and _CACHE_FILE_RE.fullmatch(parts[-1]) is not None
    )


def _parse_metadata(content: bytes) -> tuple[str, str]:
    try:
        metadata = BytesHeaderParser().parsebytes(content)
    except (UnicodeError, ValueError) as exc:
        raise ProviderEnvironmentError("distribution METADATA is invalid") from exc
    names = metadata.get_all("Name", [])
    versions = metadata.get_all("Version", [])
    if len(names) != 1 or len(versions) != 1:
        raise ProviderEnvironmentError("distribution METADATA requires one Name and Version")
    raw_name = names[0]
    version = versions[0]
    if not isinstance(raw_name, str) or not isinstance(version, str):
        raise ProviderEnvironmentError("distribution METADATA identity is invalid")
    if _VERSION_RE.fullmatch(version) is None:
        raise ProviderEnvironmentError("distribution METADATA contains an invalid Version")
    return _canonical_name(raw_name), version


def _reject_editable_install(content: bytes) -> None:
    try:
        value = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderEnvironmentError("distribution direct_url.json is invalid") from exc
    if not isinstance(value, Mapping):
        raise ProviderEnvironmentError("distribution direct_url.json must be an object")
    directory = value.get("dir_info")
    if isinstance(directory, Mapping) and directory.get("editable") is True:
        raise ProviderEnvironmentError("editable distributions are outside provider identity")


def _validate_path_extension(
    content: bytes,
    *,
    site_packages: str,
) -> None:
    try:
        text = content.decode("utf-8")
    except UnicodeError as exc:
        raise ProviderEnvironmentError("path-extension file is not UTF-8") from exc
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("import ") or line.startswith("import\t"):
            raise ProviderEnvironmentError(
                "executable path-extension files are outside provider identity"
            )
        target = _resolve_record_target(site_packages, line)
        if not target.startswith(f"{site_packages}/"):
            raise ProviderEnvironmentError(
                "path-extension file points outside its site-packages root"
            )


def _parse_record(
    root_fd: int,
    *,
    site_packages: str,
    dist_info_path: str,
    ownership: dict[str, str],
    fingerprints: dict[str, _StatFingerprint],
) -> ProviderDistributionIdentity:
    record_path = posixpath.join(dist_info_path, "RECORD")
    record_observation = _observe_regular_file(
        root_fd,
        record_path,
        collect_content=True,
        maximum_bytes=_MAX_RECORD_BYTES,
    )
    assert record_observation.content is not None
    try:
        record_text = record_observation.content.decode("utf-8")
    except UnicodeError as exc:
        raise ProviderEnvironmentError("distribution RECORD is not UTF-8") from exc

    observed_files: list[ProviderFileIdentity] = []
    metadata_content: bytes | None = None
    direct_url_content: bytes | None = None
    path_extensions: list[bytes] = []
    saw_record = False
    seen_targets: set[str] = set()
    reader = csv.reader(io.StringIO(record_text, newline=""))
    try:
        rows = list(reader)
    except csv.Error as exc:
        raise ProviderEnvironmentError("distribution RECORD is malformed") from exc
    if not rows:
        raise ProviderEnvironmentError("distribution RECORD is empty")

    for row in rows:
        if len(row) != 3:
            raise ProviderEnvironmentError("distribution RECORD row must have three fields")
        raw_path, encoded_hash, encoded_size = row
        target_path = _resolve_record_target(site_packages, raw_path)
        if target_path in seen_targets:
            raise ProviderEnvironmentError("distribution RECORD contains a duplicate path")
        seen_targets.add(target_path)

        if target_path == record_path:
            if encoded_hash or encoded_size:
                raise ProviderEnvironmentError("RECORD self-row must not claim a circular digest")
            saw_record = True
            continue
        if target_path.startswith(f"{site_packages}/") and _is_runtime_cache(
            target_path
        ):
            continue
        expected_sha256 = _decode_record_sha256(encoded_hash)
        if _DECIMAL_SIZE_RE.fullmatch(encoded_size) is None:
            raise ProviderEnvironmentError("RECORD requires a canonical byte size")
        expected_size = int(encoded_size)
        collect_content = (
            target_path == posixpath.join(dist_info_path, "METADATA")
            or target_path == posixpath.join(dist_info_path, "direct_url.json")
            or target_path.endswith(".pth")
        )
        maximum_bytes = (
            _MAX_METADATA_BYTES
            if target_path == posixpath.join(dist_info_path, "METADATA")
            else _MAX_SMALL_POLICY_FILE_BYTES if collect_content else None
        )
        observed = _observe_regular_file(
            root_fd,
            target_path,
            collect_content=collect_content,
            maximum_bytes=maximum_bytes,
        )
        if observed.identity.byte_size != expected_size:
            raise ProviderEnvironmentError("RECORD byte size does not match provider file")
        if not hmac.compare_digest(observed.identity.sha256, expected_sha256):
            raise ProviderEnvironmentError("RECORD sha256 does not match provider file")
        observed_files.append(observed.identity)
        fingerprints[target_path] = observed.fingerprint
        if target_path == posixpath.join(dist_info_path, "METADATA"):
            metadata_content = observed.content
        elif target_path == posixpath.join(dist_info_path, "direct_url.json"):
            direct_url_content = observed.content
        elif target_path.endswith(".pth"):
            assert observed.content is not None
            path_extensions.append(observed.content)

    if not saw_record:
        raise ProviderEnvironmentError("distribution RECORD does not contain its self-row")
    if metadata_content is None:
        raise ProviderEnvironmentError("distribution METADATA is absent from RECORD")
    canonical_name, version = _parse_metadata(metadata_content)
    owner_key = f"{canonical_name}=={version}"

    for target_path in (*seen_targets,):
        if target_path.startswith(f"{site_packages}/") and _is_runtime_cache(
            target_path
        ):
            continue
        previous_owner = ownership.setdefault(target_path, owner_key)
        if previous_owner != owner_key:
            raise ProviderEnvironmentError("provider file is claimed by multiple distributions")
    fingerprints[record_path] = record_observation.fingerprint
    if direct_url_content is not None:
        _reject_editable_install(direct_url_content)
    for path_extension in path_extensions:
        _validate_path_extension(path_extension, site_packages=site_packages)

    return ProviderDistributionIdentity(
        canonical_name=canonical_name,
        version=version,
        record=record_observation.identity,
        files=tuple(sorted(observed_files, key=lambda item: item.relative_path)),
    )


def _is_non_distribution_bin_entry(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path).parts
    if len(parts) != 2 or parts[0] not in {"bin", "Scripts"}:
        return False
    return (
        parts[1] in _ACTIVATION_HELPERS
        or _PYTHON_EXECUTABLE_RE.fullmatch(parts[1]) is not None
    )


def _scan_managed_tree(
    root_fd: int,
    relative_directory: str,
    *,
    expected_files: set[str],
    allow_bin_infrastructure: bool,
) -> None:
    directory_fd = _open_directory_fd(root_fd, relative_directory)
    try:
        with os.scandir(directory_fd) as entries:
            sorted_entries = sorted(
                (
                    (entry.name, entry.stat(follow_symlinks=False))
                    for entry in entries
                ),
                key=lambda item: item[0],
            )
        for entry_name, entry_stat in sorted_entries:
            relative_path = posixpath.join(relative_directory, entry_name)
            if stat.S_ISLNK(entry_stat.st_mode):
                if allow_bin_infrastructure and _is_non_distribution_bin_entry(
                    relative_path
                ):
                    continue
                raise ProviderEnvironmentError("provider managed tree contains a symbolic link")
            if stat.S_ISDIR(entry_stat.st_mode):
                _scan_managed_tree(
                    root_fd,
                    relative_path,
                    expected_files=expected_files,
                    allow_bin_infrastructure=allow_bin_infrastructure,
                )
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise ProviderEnvironmentError("provider managed tree contains a special file")
            if relative_path in expected_files or (
                not allow_bin_infrastructure and _is_runtime_cache(relative_path)
            ):
                continue
            if allow_bin_infrastructure and _is_non_distribution_bin_entry(relative_path):
                continue
            raise ProviderEnvironmentError(
                f"provider managed tree contains unrecorded file: {relative_path}"
            )
    finally:
        os.close(directory_fd)


def _scan_for_unrecorded_files(
    root_fd: int,
    *,
    site_package_paths: Sequence[str],
    expected_files: set[str],
) -> None:
    for site_packages in site_package_paths:
        _scan_managed_tree(
            root_fd,
            site_packages,
            expected_files=expected_files,
            allow_bin_infrastructure=False,
        )
    for script_directory in ("bin", "Scripts"):
        script_fd = _try_open_directory_fd(root_fd, script_directory)
        if script_fd is None:
            continue
        os.close(script_fd)
        _scan_managed_tree(
            root_fd,
            script_directory,
            expected_files=expected_files,
            allow_bin_infrastructure=True,
        )


def compute_provider_environment_identity(
    provider_root: str | os.PathLike[str],
) -> ProviderEnvironmentIdentity:
    """Compute a closed identity after verifying every wheel RECORD entry.

    ``site-packages`` and provider ``bin``/``Scripts`` trees are closed: every
    regular file must be distribution-owned, except standard activation/Python
    helpers and canonical ``__pycache__`` bytecode cache files.  Cache content is
    deliberately excluded from the identity.
    """

    root = _validated_root(Path(provider_root))
    try:
        root_fd = os.open(
            root,
            os.O_RDONLY
            | os.O_CLOEXEC
            | os.O_DIRECTORY
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise ProviderEnvironmentError("provider environment root is unavailable") from exc
    try:
        site_package_paths = _discover_site_packages(root_fd, root)
        dist_info_directories = _discover_dist_info_directories(
            root_fd, site_package_paths
        )
        ownership: dict[str, str] = {}
        fingerprints: dict[str, _StatFingerprint] = {}
        distributions = tuple(
            sorted(
                (
                    _parse_record(
                        root_fd,
                        site_packages=site_packages,
                        dist_info_path=dist_info_path,
                        ownership=ownership,
                        fingerprints=fingerprints,
                    )
                    for site_packages, dist_info_path in dist_info_directories
                ),
                key=lambda distribution: (
                    distribution.canonical_name,
                    distribution.version,
                ),
            )
        )
        canonical_names = [distribution.canonical_name for distribution in distributions]
        if len(canonical_names) != len(set(canonical_names)):
            raise ProviderEnvironmentError(
                "provider environment contains duplicate distribution names"
            )

        expected_files = set(fingerprints)
        _scan_for_unrecorded_files(
            root_fd,
            site_package_paths=site_package_paths,
            expected_files=expected_files,
        )
        for relative_path, original_fingerprint in sorted(fingerprints.items()):
            if _stat_regular_file(root_fd, relative_path) != original_fingerprint:
                raise ProviderEnvironmentError(
                    "provider file changed during environment verification"
                )
    finally:
        os.close(root_fd)

    provisional = ProviderEnvironmentIdentity(
        distributions=distributions,
        semantic_sha256="0" * 64,
    )
    semantic_sha256 = hashlib.sha256(
        _canonical_json_bytes(provisional.semantic_payload())
    ).hexdigest()
    return ProviderEnvironmentIdentity(
        distributions=distributions,
        semantic_sha256=semantic_sha256,
    )


def verify_provider_environment_identity(
    provider_root: str | os.PathLike[str],
    *,
    expected_sha256: str,
    expected_distribution_count: int | None = None,
    expected_file_count: int | None = None,
    expected_distributions: Mapping[str, str] | None = None,
) -> ProviderEnvironmentIdentity:
    """Recompute and compare a provider identity against policy-bound values."""

    if _SHA256_RE.fullmatch(expected_sha256) is None:
        raise ProviderEnvironmentError("expected provider identity sha256 is invalid")
    identity = compute_provider_environment_identity(provider_root)
    if not hmac.compare_digest(identity.semantic_sha256, expected_sha256):
        raise ProviderEnvironmentError("provider environment identity does not match policy")
    if (
        expected_distribution_count is not None
        and identity.distribution_count != expected_distribution_count
    ):
        raise ProviderEnvironmentError("provider distribution count does not match policy")
    if expected_file_count is not None and identity.file_count != expected_file_count:
        raise ProviderEnvironmentError("provider file count does not match policy")
    if expected_distributions is not None:
        canonical_expected: dict[str, str] = {}
        for raw_name, version in expected_distributions.items():
            if not isinstance(raw_name, str) or not isinstance(version, str):
                raise ProviderEnvironmentError("expected distribution identity is invalid")
            name = _canonical_name(raw_name)
            if name in canonical_expected:
                raise ProviderEnvironmentError("expected distribution names are duplicated")
            canonical_expected[name] = version
        if identity.distribution_versions != dict(sorted(canonical_expected.items())):
            raise ProviderEnvironmentError("provider distribution versions do not match policy")
    return identity


def _validated_compact_manifest(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProviderEnvironmentError("provider environment manifest must be an object")
    expected_keys = {
        "distributions",
        "identity_schema_version",
        "manifest_schema_version",
        "manifest_sha256",
        "provider_environment_distribution_count",
        "provider_environment_file_count",
        "provider_environment_sha256",
    }
    if set(value) != expected_keys:
        raise ProviderEnvironmentError("provider environment manifest fields are invalid")
    if value.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ProviderEnvironmentError("provider environment manifest schema is unsupported")
    if value.get("identity_schema_version") != IDENTITY_SCHEMA_VERSION:
        raise ProviderEnvironmentError("provider environment identity schema is unsupported")
    for field in ("manifest_sha256", "provider_environment_sha256"):
        if not isinstance(value.get(field), str) or _SHA256_RE.fullmatch(value[field]) is None:
            raise ProviderEnvironmentError("provider environment manifest checksum is invalid")
    for field in (
        "provider_environment_distribution_count",
        "provider_environment_file_count",
    ):
        _positive_manifest_count(value.get(field))

    raw_distributions = value.get("distributions")
    if not isinstance(raw_distributions, list) or not raw_distributions:
        raise ProviderEnvironmentError("provider environment distribution list is invalid")
    normalized_distributions: list[dict[str, object]] = []
    for raw_distribution in raw_distributions:
        if not isinstance(raw_distribution, dict) or set(raw_distribution) != {
            "canonical_name",
            "file_count",
            "record_sha256",
            "version",
        }:
            raise ProviderEnvironmentError("provider distribution manifest entry is invalid")
        name = raw_distribution.get("canonical_name")
        version = raw_distribution.get("version")
        file_count = raw_distribution.get("file_count")
        record_sha256 = raw_distribution.get("record_sha256")
        if (
            not isinstance(name, str)
            or _canonical_name(name) != name
            or not isinstance(version, str)
            or _VERSION_RE.fullmatch(version) is None
            or not isinstance(file_count, int)
            or isinstance(file_count, bool)
            or file_count <= 0
            or not isinstance(record_sha256, str)
            or _SHA256_RE.fullmatch(record_sha256) is None
        ):
            raise ProviderEnvironmentError("provider distribution manifest entry is invalid")
        normalized_distributions.append(dict(raw_distribution))
    if normalized_distributions != sorted(
        normalized_distributions,
        key=lambda item: (str(item["canonical_name"]), str(item["version"])),
    ):
        raise ProviderEnvironmentError("provider distribution manifest order is invalid")
    names = [str(item["canonical_name"]) for item in normalized_distributions]
    if len(names) != len(set(names)):
        raise ProviderEnvironmentError("provider distribution manifest names are duplicated")
    if len(normalized_distributions) != value["provider_environment_distribution_count"]:
        raise ProviderEnvironmentError("provider distribution manifest count does not match")
    if (
        sum(
            _positive_manifest_count(item["file_count"])
            for item in normalized_distributions
        )
        != value["provider_environment_file_count"]
    ):
        raise ProviderEnvironmentError("provider file manifest count does not match")

    manifest_payload = dict(value)
    observed_manifest_sha256 = str(manifest_payload.pop("manifest_sha256"))
    expected_manifest_sha256 = hashlib.sha256(
        _canonical_json_bytes(manifest_payload)
    ).hexdigest()
    if not hmac.compare_digest(observed_manifest_sha256, expected_manifest_sha256):
        raise ProviderEnvironmentError("provider environment manifest self-hash does not match")
    return value


def load_provider_environment_manifest(
    manifest_path: str | os.PathLike[str],
) -> dict[str, object]:
    """Load a no-symlink compact manifest and verify its canonical self-hash."""

    path = Path(manifest_path)
    try:
        path_stat = path.lstat()
    except OSError as exc:
        raise ProviderEnvironmentError("provider environment manifest is unavailable") from exc
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        raise ProviderEnvironmentError("provider environment manifest must be a regular file")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProviderEnvironmentError("provider environment manifest is unavailable") from exc
    try:
        opened_stat = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_size > _MAX_SMALL_POLICY_FILE_BYTES
        ):
            raise ProviderEnvironmentError("provider environment manifest is invalid")
        chunks = bytearray()
        while chunk := os.read(descriptor, _READ_CHUNK_SIZE):
            chunks.extend(chunk)
        final_stat = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _fingerprint(opened_stat) != _fingerprint(final_stat):
        raise ProviderEnvironmentError("provider environment manifest changed while read")
    try:
        value = json.loads(chunks)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProviderEnvironmentError("provider environment manifest is invalid JSON") from exc
    return _validated_compact_manifest(value)


def verify_provider_environment_manifest(
    provider_root: str | os.PathLike[str],
    manifest: Mapping[str, object] | str | os.PathLike[str],
    *,
    expected_manifest_sha256: str | None = None,
) -> ProviderEnvironmentIdentity:
    """Verify a compact manifest and recompute its complete environment identity."""

    if isinstance(manifest, Mapping):
        compact_manifest = _validated_compact_manifest(dict(manifest))
    else:
        compact_manifest = load_provider_environment_manifest(manifest)
    manifest_sha256 = compact_manifest["manifest_sha256"]
    assert isinstance(manifest_sha256, str)
    if expected_manifest_sha256 is not None:
        if _SHA256_RE.fullmatch(expected_manifest_sha256) is None:
            raise ProviderEnvironmentError("expected environment manifest sha256 is invalid")
        if not hmac.compare_digest(manifest_sha256, expected_manifest_sha256):
            raise ProviderEnvironmentError(
                "provider environment manifest is not the approved manifest"
            )

    identity = verify_provider_environment_identity(
        provider_root,
        expected_sha256=str(compact_manifest["provider_environment_sha256"]),
        expected_distribution_count=_positive_manifest_count(
            compact_manifest["provider_environment_distribution_count"]
        ),
        expected_file_count=_positive_manifest_count(
            compact_manifest["provider_environment_file_count"]
        ),
    )
    if identity.manifest() != compact_manifest:
        raise ProviderEnvironmentError(
            "provider environment does not reproduce its compact manifest"
        )
    return identity


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compute the closed RECORD identity of a V0 provider environment."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    identity_parser = subparsers.add_parser(
        "identity", help="verify the environment and emit its compact identity"
    )
    identity_parser.add_argument(
        "--provider-root",
        required=True,
        type=Path,
        help="provider virtual-environment root",
    )
    identity_parser.add_argument(
        "--output",
        type=Path,
        help="write the compact manifest to a new file instead of stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.command != "identity":
        parser.error("unsupported command")
    try:
        identity = compute_provider_environment_identity(arguments.provider_root)
    except ProviderEnvironmentError as exc:
        parser.exit(2, f"error: {exc}\n")
    payload = _canonical_json_bytes(identity.summary()) + b"\n"
    if arguments.output is None:
        sys.stdout.buffer.write(payload)
    else:
        try:
            _write_new(arguments.output, payload)
        except ProviderEnvironmentError as exc:
            parser.exit(2, f"error: {exc}\n")
        manifest_sha256 = identity.manifest()["manifest_sha256"]
        sys.stdout.write(f"manifest_sha256={manifest_sha256}\n")
        sys.stdout.write(f"file_sha256={hashlib.sha256(payload).hexdigest()}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
