"""Closed-inventory tests for the standalone V0 provider environment verifier."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from scripts import v0_provider_environment as environment


def _record_digest(content: bytes) -> str:
    digest = hashlib.sha256(content).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _write_distribution(
    root: Path,
    *,
    name: str,
    version: str,
    script_name: str | None = None,
    direct_url: dict[str, object] | None = None,
) -> Path:
    site_packages = root / "lib" / "python3.12" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    package_name = re.sub(r"[-_.]+", "_", name).lower()
    package_root = site_packages / package_name
    package_root.mkdir()
    dist_info = site_packages / f"{package_name}-{version}.dist-info"
    dist_info.mkdir()

    files: dict[str, bytes] = {
        f"{package_name}/__init__.py": f'VERSION = "{version}"\n'.encode(),
        f"{dist_info.name}/METADATA": (
            f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n\n"
        ).encode(),
        f"{dist_info.name}/WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\n",
    }
    if direct_url is not None:
        files[f"{dist_info.name}/direct_url.json"] = json.dumps(
            direct_url, sort_keys=True
        ).encode()
    if script_name is not None:
        files[f"../../../bin/{script_name}"] = b"#!/bin/sh\nexit 0\n"

    for record_path, content in files.items():
        target = site_packages.joinpath(*record_path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if record_path.startswith("../../../bin/"):
            target.chmod(0o755)

    rows = [
        (record_path, _record_digest(content), str(len(content)))
        for record_path, content in sorted(files.items())
    ]
    rows.append((f"{dist_info.name}/RECORD", "", ""))
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerows(rows)
    (dist_info / "RECORD").write_text(output.getvalue(), encoding="utf-8")
    return dist_info


def _provider_environment(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "provider-env"
    root.mkdir()
    alpha = _write_distribution(
        root,
        name="Alpha_Package",
        version="1.2.3",
        script_name="alpha-cli",
    )
    beta = _write_distribution(root, name="beta.package", version="2.0")
    bin_root = root / "bin"
    (bin_root / "activate").write_text("# activation helper\n", encoding="utf-8")
    (bin_root / "python3").symlink_to(Path(sys.executable))
    return root, alpha, beta


def _rewrite_record(
    record_path: Path,
    transform: Callable[[list[list[str]]], list[list[str]]],
) -> None:
    rows = list(csv.reader(io.StringIO(record_path.read_text(encoding="utf-8"))))
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(transform(rows))
    record_path.write_text(output.getvalue(), encoding="utf-8")


def test_identity_covers_sorted_distributions_records_files_and_bin_scripts(
    tmp_path: Path,
) -> None:
    root, alpha, beta = _provider_environment(tmp_path)

    identity = environment.compute_provider_environment_identity(root)

    assert identity.distribution_count == 2
    assert identity.file_count == 9
    assert identity.distribution_versions == {
        "alpha-package": "1.2.3",
        "beta-package": "2.0",
    }
    assert re.fullmatch(r"[0-9a-f]{64}", identity.semantic_sha256)
    assert [item.canonical_name for item in identity.distributions] == [
        "alpha-package",
        "beta-package",
    ]
    assert identity.distributions[0].record.sha256 == hashlib.sha256(
        (alpha / "RECORD").read_bytes()
    ).hexdigest()
    assert identity.distributions[1].record.sha256 == hashlib.sha256(
        (beta / "RECORD").read_bytes()
    ).hexdigest()
    assert any(
        file.relative_path == "bin/alpha-cli"
        for file in identity.distributions[0].files
    )


def test_identity_is_stable_across_runtime_cache_changes(tmp_path: Path) -> None:
    root, _, _ = _provider_environment(tmp_path)
    baseline = environment.compute_provider_environment_identity(root)
    cache = (
        root
        / "lib"
        / "python3.12"
        / "site-packages"
        / "alpha_package"
        / "__pycache__"
        / "__init__.cpython-312.pyc"
    )
    cache.parent.mkdir()
    cache.write_bytes(b"first-runtime-cache")

    with_cache = environment.compute_provider_environment_identity(root)
    cache.write_bytes(b"different-runtime-cache")
    changed_cache = environment.compute_provider_environment_identity(root)

    assert with_cache.semantic_sha256 == baseline.semantic_sha256
    assert changed_cache.semantic_sha256 == baseline.semantic_sha256
    assert changed_cache.file_count == baseline.file_count


def test_identity_changes_when_only_record_physical_bytes_change(tmp_path: Path) -> None:
    root, alpha, _ = _provider_environment(tmp_path)
    baseline = environment.compute_provider_environment_identity(root)
    _rewrite_record(
        alpha / "RECORD",
        lambda rows: [*reversed(rows[:-1]), rows[-1]],
    )

    reordered = environment.compute_provider_environment_identity(root)

    assert reordered.semantic_sha256 != baseline.semantic_sha256
    assert reordered.file_count == baseline.file_count


@pytest.mark.parametrize("tamper", ("content", "size", "hash"))
def test_record_verification_rejects_file_drift(tmp_path: Path, tamper: str) -> None:
    root, alpha, _ = _provider_environment(tmp_path)
    module = root / "lib" / "python3.12" / "site-packages" / "alpha_package" / "__init__.py"
    if tamper == "content":
        module.write_bytes(b"X" * module.stat().st_size)
    else:
        def mutate(rows: list[list[str]]) -> list[list[str]]:
            row = next(item for item in rows if item[0] == "alpha_package/__init__.py")
            if tamper == "size":
                row[2] = str(int(row[2]) + 1)
            else:
                row[1] = "sha256=" + "A" * 43
            return rows

        _rewrite_record(alpha / "RECORD", mutate)

    with pytest.raises(environment.ProviderEnvironmentError, match="RECORD"):
        environment.compute_provider_environment_identity(root)


@pytest.mark.parametrize("field_index", (1, 2))
def test_record_requires_sha256_and_size_for_non_cache_files(
    tmp_path: Path, field_index: int
) -> None:
    root, alpha, _ = _provider_environment(tmp_path)

    def clear_field(rows: list[list[str]]) -> list[list[str]]:
        row = next(item for item in rows if item[0] == "alpha_package/__init__.py")
        row[field_index] = ""
        return rows

    _rewrite_record(alpha / "RECORD", clear_field)

    with pytest.raises(environment.ProviderEnvironmentError, match="RECORD"):
        environment.compute_provider_environment_identity(root)


def test_record_rejects_paths_outside_provider_root(tmp_path: Path) -> None:
    root, alpha, _ = _provider_environment(tmp_path)

    def append_escape(rows: list[list[str]]) -> list[list[str]]:
        rows.insert(-1, ["../../../../escape.py", _record_digest(b"escape"), "6"])
        return rows

    _rewrite_record(alpha / "RECORD", append_escape)

    with pytest.raises(environment.ProviderEnvironmentError, match="escapes"):
        environment.compute_provider_environment_identity(root)


def test_recorded_symlink_is_rejected_without_following_it(tmp_path: Path) -> None:
    root, _, _ = _provider_environment(tmp_path)
    module = root / "lib" / "python3.12" / "site-packages" / "alpha_package" / "__init__.py"
    outside = tmp_path / "outside.py"
    outside.write_bytes(module.read_bytes())
    module.unlink()
    module.symlink_to(outside)

    with pytest.raises(environment.ProviderEnvironmentError, match="symbolic"):
        environment.compute_provider_environment_identity(root)


def test_unrecorded_symlink_directory_is_rejected_without_following_it(
    tmp_path: Path,
) -> None:
    root, _, _ = _provider_environment(tmp_path)
    outside = tmp_path / "outside-package"
    outside.mkdir()
    (outside / "module.py").write_text("EXTERNAL = True\n", encoding="utf-8")
    site_packages = root / "lib" / "python3.12" / "site-packages"
    (site_packages / "external_package").symlink_to(outside, target_is_directory=True)

    with pytest.raises(environment.ProviderEnvironmentError, match="symbolic"):
        environment.compute_provider_environment_identity(root)


@pytest.mark.parametrize(
    "relative_path",
    (
        "lib/python3.12/site-packages/rogue.py",
        "lib/python3.12/site-packages/extra.data",
        "bin/unrecorded-command",
    ),
)
def test_closed_managed_trees_reject_extra_regular_files(
    tmp_path: Path, relative_path: str
) -> None:
    root, _, _ = _provider_environment(tmp_path)
    extra = root / relative_path
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"unrecorded")

    with pytest.raises(environment.ProviderEnvironmentError, match="unrecorded"):
        environment.compute_provider_environment_identity(root)


def test_noncanonical_cache_file_is_not_exempt(tmp_path: Path) -> None:
    root, _, _ = _provider_environment(tmp_path)
    cache = (
        root
        / "lib"
        / "python3.12"
        / "site-packages"
        / "alpha_package"
        / "__pycache__"
        / "arbitrary.pyc"
    )
    cache.parent.mkdir()
    cache.write_bytes(b"not-a-canonical-cache-name")

    with pytest.raises(environment.ProviderEnvironmentError, match="unrecorded"):
        environment.compute_provider_environment_identity(root)


def test_canonical_cache_name_is_exempt_only_inside_site_packages(tmp_path: Path) -> None:
    root, _, _ = _provider_environment(tmp_path)
    cache = root / "bin" / "__pycache__" / "rogue.cpython-312.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"not-a-site-packages-runtime-cache")

    with pytest.raises(environment.ProviderEnvironmentError, match="unrecorded"):
        environment.compute_provider_environment_identity(root)


def test_editable_distribution_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "provider-env"
    root.mkdir()
    _write_distribution(
        root,
        name="editable-package",
        version="1.0",
        direct_url={"dir_info": {"editable": True}, "url": "file:///tmp/source"},
    )

    with pytest.raises(environment.ProviderEnvironmentError, match="editable"):
        environment.compute_provider_environment_identity(root)


def test_compact_manifest_self_hash_load_and_live_verification(tmp_path: Path) -> None:
    root, _, _ = _provider_environment(tmp_path)
    identity = environment.compute_provider_environment_identity(root)
    manifest = identity.manifest()
    manifest_path = tmp_path / "provider-environment-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )

    loaded = environment.load_provider_environment_manifest(manifest_path)
    verified = environment.verify_provider_environment_manifest(
        root,
        manifest_path,
        expected_manifest_sha256=str(manifest["manifest_sha256"]),
    )

    assert loaded == manifest
    assert verified == identity
    assert manifest["provider_environment_sha256"] == identity.semantic_sha256
    assert manifest["provider_environment_distribution_count"] == 2
    assert manifest["provider_environment_file_count"] == 9


def test_manifest_and_identity_verifiers_reject_unapproved_values(tmp_path: Path) -> None:
    root, _, _ = _provider_environment(tmp_path)
    identity = environment.compute_provider_environment_identity(root)

    with pytest.raises(environment.ProviderEnvironmentError, match="identity"):
        environment.verify_provider_environment_identity(
            root,
            expected_sha256="0" * 64,
        )
    with pytest.raises(environment.ProviderEnvironmentError, match="approved"):
        environment.verify_provider_environment_manifest(
            root,
            identity.manifest(),
            expected_manifest_sha256="0" * 64,
        )


def test_identity_cli_emits_one_canonical_compact_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _, _ = _provider_environment(tmp_path)

    assert environment.main(["identity", "--provider-root", str(root)]) == 0

    output = capsys.readouterr().out
    parsed = json.loads(output)
    assert output == json.dumps(
        parsed, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ) + "\n"
    assert parsed["manifest_schema_version"] == environment.MANIFEST_SCHEMA_VERSION
    assert re.fullmatch(r"[0-9a-f]{64}", parsed["manifest_sha256"])


def test_identity_cli_writes_only_one_new_manifest(tmp_path: Path) -> None:
    root, _, _ = _provider_environment(tmp_path)
    output = tmp_path / "environment-manifest.json"

    assert (
        environment.main(
            [
                "identity",
                "--provider-root",
                str(root),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    manifest = environment.load_provider_environment_manifest(output)
    assert manifest["provider_environment_distribution_count"] == 2
    with pytest.raises(SystemExit) as raised:
        environment.main(
            [
                "identity",
                "--provider-root",
                str(root),
                "--output",
                str(output),
            ]
        )
    assert raised.value.code == 2
