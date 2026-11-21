#!/usr/bin/env python3
"""Audit the attribute/directive reference against a local Odin source checkout.

This checks inventory and local links, not the correctness of prose or target
support. It needs compiler sources, not just an installed Odin executable.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
REFERENCE = PROJECT / "docs" / "attributes-and-directives.md"
PIN = "8169255b97b85d6a08691d99764f51fb01f92b5a"


def function(source: str, name: str) -> str:
    """Native functions end with an unindented closing brace."""
    match = re.search(
        r"(?ms)^[^\n{}]*\b" + re.escape(name)
        + r"\([^\n]*\)\s*\{.*?^\}", source
    )
    if match is None:
        raise ValueError(f"Cannot locate native function {name}")
    return match.group()


def compared_names(source: str, expression: str = "name") -> set[str]:
    return set(re.findall(
        r"\b" + expression + r'\s*==\s*"([a-zA-Z0-9_:+/.-]+)"', source
    ))


def inventory(root: Path) -> dict[str, set[str]]:
    native = root / "src"
    checker = (native / "checker.cpp").read_text()
    parser = (native / "parser.cpp").read_text()
    settings = (native / "build_settings.cpp").read_text()
    attributes: set[str] = set()
    handlers = re.findall(
        r"gb_internal DECL_ATTRIBUTE_PROC\((\w+)\)", checker
    )
    if not handlers:
        raise ValueError("No native attribute handlers found")
    for handler in handlers:
        # DECL_ATTRIBUTE_PROC is a function-generating macro, not a normal name.
        match = re.search(
            r"(?ms)^gb_internal DECL_ATTRIBUTE_PROC\(" + re.escape(handler)
            + r"\)\s*\{.*?^\}", checker
        )
        if match is None:
            raise ValueError(f"Cannot locate attribute handler {handler}")
        body = match.group()
        attributes.update(compared_names(body))
        if "ATTRIBUTE_USER_TAG_NAME" in body:
            attributes.add("tag")
    # These are handled outside the declaration-kind-specific native handlers.
    attributes.add("builtin")
    thor_unused = (PROJECT / "rust/src/unused_attributes.rs").read_text()
    if '"unused"' not in thor_unused:
        raise ValueError("Thor unused-attribute handler changed; review inventory")
    attributes.update({"unused", "vet_ignore", "no_alloc"})

    directives = compared_names(parser, r"(?:name\.string|tag\.string|tag)")
    directives.discard("_")  # A package-name check, not a directive.
    for filename, name in [
        ("check_expr.cpp", "check_basic_directive_expr"),
        ("check_builtin.cpp", "check_builtin_procedure_directive"),
    ]:
        directives.update(compared_names(function((native / filename).read_text(), name)))
    # Native/tooling parameter flags and contextual parser cases include tags
    # represented as enums rather than the BasicDirective AST node.
    ast = (root / "core/odin/ast/ast.odin").read_text()
    flags = re.search(r"(?ms)^field_hash_flag_strings := .*?^\}", ast)
    if flags is None:
        raise ValueError("Cannot locate native parameter directive table")
    directives.update(re.findall(r'\{"(\w+)"', flags.group()))
    thor_parser = (PROJECT / "frontend/parser/parser.odin").read_text()
    for case in re.findall(r"(?m)^\s*case ((?:\"[^\"]+\"[, ]*)+):", thor_parser):
        directives.update(re.findall(r'"(\w+)"', case))
    directives.difference_update({"builtin", "intrinsics"})  # Reserved package names.

    asm = (native / "check_asm.cpp").read_text()
    asm_names = compared_names(asm)
    # These branches dispatch assembly instruction/register/operand names rather
    # than hash directives. Keep exclusions explicit so new branches get reviewed.
    asm_names.difference_update({"li", "la", "lla", "esp", "rsp", "runtime"})
    directives.update(asm_names)
    directives.update(compared_names(asm, r"clobber->name\.string"))
    # Thor extensions and the historical hash-conditional family documented as
    # removed. Their exact implementation is not an Odin BasicDirective handler.
    directives.update({"run", "runtime", "switch", "mut", "ro"})
    directives.update({"if", "else", "else_if", "elif", "end", "endif"})

    file_body = function(parser, "parse_file_tag")
    tags = set(re.findall(r'string_starts_with\(lc, str_lit\("([^" ]+)"\)\)', file_body))
    tags.update(compared_names(file_body, "lc"))
    return {
        "attr": attributes,
        "dir": directives,
        "file": tags,
        "feature": compared_names(function(settings, "get_feature_flag_from_name")),
        "vet": compared_names(function(settings, "get_vet_flag_from_name")) | {"no_alloc"},
        "analysis": compared_names(function(settings, "get_analysis_flag_from_name")),
    }


def local_link_errors(document: str) -> list[str]:
    errors = []
    for target in re.findall(r"\]\(([^\s)]+)\)", document):
        if "://" in target or target.startswith("#"):
            continue
        path = (REFERENCE.parent / target.split("#", 1)[0]).resolve()
        if not path.is_file():
            errors.append(f"Missing local link: {target}")
    anchors = re.findall(r'<a id="([^"]+)"></a>', document)
    if len(set(anchors)) != len(anchors):
        errors.append("Duplicate explicit entry anchors")
    # GitHub-style heading anchors used by the table of contents.
    heading_anchors = set()
    for heading in re.findall(r"(?m)^#{1,6} (.+)$", document):
        heading = re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-")
        heading_anchors.add(heading)
    all_anchors = set(anchors) | heading_anchors
    for target in re.findall(r"\]\(#([^\s)]+)\)", document):
        if target not in all_anchors:
            errors.append(f"Missing internal anchor: {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odin-root", type=Path, help="Odin checkout containing src/")
    args = parser.parse_args()
    root = args.odin_root
    if root is None:
        result = subprocess.run(["odin", "root"], check=True, capture_output=True, text=True)
        root = Path(result.stdout.strip())
    document = REFERENCE.read_text()
    names = inventory(root)
    anchors = set(re.findall(r'<a id="([^"]+)"></a>', document))
    errors = local_link_errors(document)
    for category, entries in names.items():
        missing = sorted(name for name in entries if f"{category}-{name}" not in anchors)
        if missing:
            errors.append(f"Undocumented {category}: {', '.join(missing)}")
        print(f"{category}: {len(entries)} source names checked")
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    if revision.returncode == 0 and revision.stdout.strip() != PIN:
        errors.append("Odin source revision differs from the reference pin; review descriptions and update pin")
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Reference inventory and local links are complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
