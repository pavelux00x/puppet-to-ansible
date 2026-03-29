"""p2a CLI — Puppet to Ansible Converter.

Usage examples:
    p2a convert site.pp -o output/
    p2a convert site.pp --module-paths /etc/puppet/modules --hiera /etc/puppet/hiera.yaml -o output/
    p2a convert site.pp --report migration.md -o output/
    p2a convert-module /etc/puppet/modules/nginx/ -o roles/nginx/
    p2a convert-all /etc/puppet/ -o ansible-project/
    p2a convert-all /etc/puppet/ --module-paths /etc/puppet/modules --hiera /etc/puppet/hiera.yaml -o ansible-project/ --report report.md
    p2a convert-erb templates/nginx.conf.erb -o roles/nginx/templates/
    p2a convert-hiera /etc/puppet/hieradata/ -o inventory/
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
from rich import box
from rich.console import Console
from rich.table import Table

from src.converters.manifest_converter import ConversionResult, ManifestConverter
from src.generators.playbook import InventoryGenerator, PlaybookGenerator, RoleGenerator
from src.parser.parser import ParseError, parse_file
from src.parser.preprocessor import ManifestPreprocessor
from src.reporters.migration_report import (
    MigrationReportBuilder,
    write_report,
)
from src.templates.erb_to_jinja import ErbConverter
from src.utils.hiera_resolver import HieraResolver, build_hiera_resolver

console = Console()
err_console = Console(stderr=True, style="bold red")


# ── Logging setup ──────────────────────────────────────────────────────────────

def _setup_logging(verbosity: int) -> None:
    level = {0: logging.WARNING, 1: logging.WARNING, 2: logging.INFO, 3: logging.DEBUG}.get(
        verbosity, logging.DEBUG
    )
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")


# ── CLI group ──────────────────────────────────────────────────────────────────

@click.group()
@click.version_option(version="0.1.0", prog_name="p2a")
def main() -> None:
    """p2a — Puppet to Ansible Converter.

    Convert Puppet manifests, modules, and entire codebases into idiomatic Ansible.
    """


# ── Shared options ─────────────────────────────────────────────────────────────

_puppet_version_option = click.option(
    "--puppet-version", "-p",
    type=click.Choice(["3", "4"]),
    default="4",
    show_default=True,
    help="Puppet language version (3=legacy, 4=modern).",
)

_output_mode_option = click.option(
    "--output-mode", "-m",
    type=click.Choice(["auto", "playbook", "role"]),
    default="auto",
    show_default=True,
    help="Output format: playbook, role structure, or auto-detect.",
)

_output_option = click.option(
    "--output", "-o",
    type=click.Path(),
    default="output",
    show_default=True,
    help="Output directory.",
)

_hosts_option = click.option(
    "--hosts",
    default="all",
    show_default=True,
    help="Ansible hosts pattern for playbooks.",
)

_dry_run_option = click.option(
    "--dry-run", is_flag=True,
    help="Parse and convert but do not write any files.",
)

_verbose_option = click.option(
    "-v", "--verbose", count=True,
    help="Increase verbosity (-v=warnings, -vv=info, -vvv=debug).",
)

_report_option = click.option(
    "--report", "-r",
    type=click.Path(),
    default=None,
    help="Write a detailed migration report to this file (.md or .json). "
         "Extension determines format: .json → JSON, anything else → Markdown.",
)

_module_paths_option = click.option(
    "--module-paths", "-M",
    multiple=True,
    type=click.Path(exists=True, file_okay=False),
    help="Puppet modulepath directories (can be repeated). "
         "Used to follow include/require across files.",
)

_hiera_option = click.option(
    "--hiera",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Path to hiera.yaml. If omitted, p2a searches upward from the manifest.",
)


# ── convert ────────────────────────────────────────────────────────────────────

@main.command("convert")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@_puppet_version_option
@_output_mode_option
@_output_option
@_hosts_option
@_dry_run_option
@_verbose_option
@_module_paths_option
@_hiera_option
@_report_option
def convert_cmd(
    manifest: str,
    puppet_version: str,
    output_mode: str,
    output: str,
    hosts: str,
    dry_run: bool,
    verbose: int,
    module_paths: tuple[str, ...],
    hiera: str | None,
    report: str | None,
) -> None:
    """Convert a single Puppet manifest (.pp) file.

    When --module-paths is provided, follows include/require chains across
    module files and merges all discovered classes into the output.
    """
    _setup_logging(verbose)
    pv = int(puppet_version)
    mpath = list(module_paths)

    console.print(f"[bold]p2a[/bold] Converting [cyan]{manifest}[/cyan] (Puppet {pv})")

    # --- Hiera resolver ---
    hiera_res = _build_hiera(hiera, manifest, mpath)
    if hiera_res and verbose >= 2:
        console.print(f"  [dim]Hiera: {len(hiera_res._data_layers)} data layer(s) loaded[/dim]")

    # --- Preprocessor (cross-file resolution) ---
    manifest_paths = _resolve_includes(manifest, mpath, pv, verbose)

    # --- Parse + convert all resolved files ---
    rb = MigrationReportBuilder(
        source_root=manifest, output_dir=output,
        puppet_version=pv, output_mode=output_mode,
        hiera_config=hiera or "", module_paths=mpath,
    )
    results: list[ConversionResult] = []
    for mp in manifest_paths:
        try:
            ast = parse_file(mp, puppet_version=pv)
            conv = ManifestConverter(puppet_version=pv, hiera_resolver=hiera_res, module_paths=mpath)
            r = conv.convert(ast)
            results.append(r)
            rb.add_file_result(str(mp), r)
        except ParseError as e:
            console.print(f"  [yellow]⚠[/yellow] Parse error in {Path(mp).name}: {e}")
            rb.add_parse_error(str(mp), str(e))

    if not results:
        err_console.print("Nothing to convert.")
        sys.exit(1)

    result = _merge_results(results)
    result.source_file = manifest
    rb.set_total_result(result)

    mode = output_mode if output_mode != "auto" else result.suggested_output_mode
    console.print(f"[dim]Output mode: {mode}[/dim]")

    if not dry_run:
        written = _write_output(result, Path(output), mode, manifest, hosts)
        for f in written:
            console.print(f"  [green]✓[/green] {f}")

    _print_report(result)
    _write_report_if_requested(rb, report)


# ── convert-module ─────────────────────────────────────────────────────────────

@main.command("convert-module")
@click.argument("module_dir", type=click.Path(exists=True, file_okay=False))
@_puppet_version_option
@_output_option
@_dry_run_option
@_verbose_option
@_module_paths_option
@_hiera_option
@_report_option
def convert_module_cmd(
    module_dir: str,
    puppet_version: str,
    output: str,
    dry_run: bool,
    verbose: int,
    module_paths: tuple[str, ...],
    hiera: str | None,
    report: str | None,
) -> None:
    """Convert an entire Puppet module directory to an Ansible role."""
    _setup_logging(verbose)
    pv = int(puppet_version)
    mod_path = Path(module_dir)
    module_name = mod_path.name
    mpath = list(module_paths)

    console.print(f"[bold]p2a[/bold] Converting module [cyan]{module_name}[/cyan] (Puppet {pv})")

    hiera_res = _build_hiera(hiera, str(mod_path / "manifests" / "init.pp"), mpath)

    # Use preprocessor to get ordered file list (init.pp first)
    try:
        pre = ManifestPreprocessor(module_paths=mpath, puppet_version=pv)
        pre_result = pre.resolve_module(mod_path)
        pp_files = pre_result.manifest_paths
        for w in pre_result.warnings:
            console.print(f"  [dim]preprocessor:[/dim] {w}")
    except (NotADirectoryError, FileNotFoundError):
        # Fallback: just glob all .pp files
        pp_files = sorted(mod_path.rglob("*.pp"))

    if not pp_files:
        console.print("[yellow]No .pp files found in module directory.[/yellow]")
        return

    rb = MigrationReportBuilder(
        source_root=str(mod_path), output_dir=output,
        puppet_version=pv, hiera_config=hiera or "", module_paths=mpath,
    )
    results: list[ConversionResult] = []
    for pp_file in pp_files:
        try:
            ast = parse_file(pp_file, puppet_version=pv)
            conv = ManifestConverter(puppet_version=pv, hiera_resolver=hiera_res, module_paths=mpath)
            r = conv.convert(ast)
            results.append(r)
            rb.add_file_result(str(pp_file), r)
        except ParseError as e:
            console.print(f"  [yellow]⚠[/yellow] Parse error in {Path(pp_file).name}: {e}")
            rb.add_parse_error(str(pp_file), str(e))

    if not results:
        err_console.print("All files failed to parse.")
        sys.exit(1)

    merged = _merge_results(results)
    merged.source_file = str(mod_path / "manifests" / "init.pp")
    rb.set_total_result(merged)

    if not dry_run:
        out_dir = Path(output) / module_name
        gen = RoleGenerator()
        role_dir = gen.generate(merged, out_dir, role_name=module_name)
        console.print(f"  [green]✓[/green] Role: {role_dir}")

        # ERB templates → Jinja2
        templates_dir = mod_path / "templates"
        if templates_dir.exists():
            _convert_templates_tracked(templates_dir, out_dir / "templates", verbose, rb)

        # requirements.yml
        if merged.collections:
            req_file = Path(output) / "requirements.yml"
            gen.write_requirements(merged, req_file)
            console.print(f"  [green]✓[/green] requirements.yml ({', '.join(sorted(merged.collections))})")

    _print_report(merged)
    _write_report_if_requested(rb, report)


# ── convert-all ────────────────────────────────────────────────────────────────

@main.command("convert-all")
@click.argument("puppet_dir", type=click.Path(exists=True, file_okay=False))
@_puppet_version_option
@_output_option
@_dry_run_option
@_verbose_option
@_module_paths_option
@_hiera_option
@_report_option
def convert_all_cmd(
    puppet_dir: str,
    puppet_version: str,
    output: str,
    dry_run: bool,
    verbose: int,
    module_paths: tuple[str, ...],
    hiera: str | None,
    report: str | None,
) -> None:
    """Convert an entire Puppet codebase (control repo + modules).

    Automatically discovers the structure:

    \b
    puppet_dir/
      hiera.yaml        ← auto-detected (or use --hiera)
      manifests/
        site.pp         ← converted to site.yml + inventory
      modules/          ← auto-detected (or use --module-paths)
        nginx/          ← converted to roles/nginx/
        apache/         ← converted to roles/apache/
      hieradata/        ← converted to group_vars / host_vars
    """
    _setup_logging(verbose)
    pv = int(puppet_version)
    base = Path(puppet_dir).resolve()
    out  = Path(output)

    console.print(f"[bold]p2a[/bold] Converting Puppet codebase: [cyan]{puppet_dir}[/cyan]")
    console.print()

    # ── Auto-discover structure ──────────────────────────────────────────────
    modules_dirs = list(module_paths) or _autodiscover_modules(base, verbose)
    hiera_file   = Path(hiera) if hiera else _autodiscover_hiera(base, verbose)
    hieradata_dir = _autodiscover_hieradata(base, hiera_file, verbose)
    site_pp       = _autodiscover_site_pp(base, verbose)

    # Build Hiera resolver once (shared across all conversions)
    hiera_res: HieraResolver | None = None
    if hiera_file:
        try:
            hiera_res = HieraResolver(hiera_config=hiera_file)
            console.print(f"  [green]✓[/green] Hiera: {hiera_file} ({len(hiera_res._data_layers)} layers)")
        except Exception as e:
            console.print(f"  [yellow]⚠[/yellow] Hiera load failed: {e}")

    total = ConversionResult()
    all_collections: set[str] = set()
    rb = MigrationReportBuilder(
        source_root=puppet_dir, output_dir=output,
        puppet_version=pv, output_mode="role",
        hiera_config=str(hiera_file) if hiera_file else "",
        module_paths=list(modules_dirs),
    )

    # ── 1. Convert each module → role ────────────────────────────────────────
    if modules_dirs:
        console.print(f"\n[bold]Modules → Roles[/bold]")
        for mod_root in modules_dirs:
            mod_root_path = Path(mod_root)
            mod_list = [d for d in sorted(mod_root_path.iterdir()) if d.is_dir() and d.name not in _SKIP_DIRS]
            console.print(f"  Found [cyan]{len(mod_list)}[/cyan] modules in {mod_root_path.name}/")

            for mod_dir in mod_list:
                r = _convert_one_module(
                    mod_dir, pv, out / "roles", hiera_res, list(modules_dirs), dry_run, verbose, rb
                )
                if r:
                    _accumulate(total, r)
                    all_collections.update(r.collections)

    # ── 2. Convert site.pp → site.yml + inventory ────────────────────────────
    if site_pp:
        console.print(f"\n[bold]site.pp → site.yml + inventory[/bold]")
        manifest_paths = _resolve_includes(str(site_pp), list(modules_dirs), pv, verbose)

        results: list[ConversionResult] = []
        for mp in manifest_paths:
            try:
                ast = parse_file(mp, puppet_version=pv)
                conv = ManifestConverter(puppet_version=pv, hiera_resolver=hiera_res, module_paths=list(modules_dirs))
                r = conv.convert(ast)
                results.append(r)
                rb.add_file_result(str(mp), r)
            except ParseError as e:
                console.print(f"  [yellow]⚠[/yellow] {Path(mp).name}: {e}")
                rb.add_parse_error(str(mp), str(e))

        if results:
            site_result = _merge_results(results)
            site_result.source_file = str(site_pp)
            _accumulate(total, site_result)
            all_collections.update(site_result.collections)

            if not dry_run:
                _write_site_playbook(site_result, out)
                if site_result.node_definitions:
                    _write_inventory(site_result, out / "inventory")
                    console.print(f"  [green]✓[/green] inventory/hosts.yml ({len(site_result.node_definitions)} nodes)")

    # ── 3. Convert hieradata → group_vars / host_vars ────────────────────────
    if hieradata_dir and not dry_run:
        console.print(f"\n[bold]Hiera data → group_vars / host_vars[/bold]")
        from src.templates.hiera_to_vars import HieraConverter
        hc = HieraConverter()
        converted = hc.convert_dir(hieradata_dir, out / "inventory")
        console.print(f"  [green]✓[/green] {len(converted)} Hiera file(s) converted")

    # ── 4. Global requirements.yml ───────────────────────────────────────────
    if all_collections and not dry_run:
        total.collections = all_collections
        req_file = out / "requirements.yml"
        RoleGenerator().write_requirements(total, req_file)
        console.print(f"\n[green]✓[/green] requirements.yml ({', '.join(sorted(all_collections))})")
        console.print("  Install with: ansible-galaxy collection install -r requirements.yml")

    rb.set_total_result(total)
    console.print()
    _print_report(total)
    _write_report_if_requested(rb, report)


# ── convert-erb ────────────────────────────────────────────────────────────────

@main.command("convert-erb")
@click.argument("template", type=click.Path(exists=True))
@click.option("--output", "-o", type=click.Path(), default=None)
@_verbose_option
def convert_erb_cmd(template: str, output: str | None, verbose: int) -> None:
    """Convert a single ERB template to Jinja2 (.j2)."""
    _setup_logging(verbose)
    erb_path = Path(template)
    j2_path  = Path(output) if output else erb_path.with_suffix(".j2")

    result = ErbConverter().convert_file(str(erb_path), str(j2_path))
    console.print(f"  [green]✓[/green] {j2_path}")
    for w in result.warnings:
        console.print(f"  [yellow]⚠[/yellow] {w}")


# ── convert-hiera ──────────────────────────────────────────────────────────────

@main.command("convert-hiera")
@click.argument("hiera_dir", type=click.Path(exists=True, file_okay=False))
@_output_option
@_verbose_option
def convert_hiera_cmd(hiera_dir: str, output: str, verbose: int) -> None:
    """Convert Hiera YAML data to Ansible group_vars/host_vars."""
    _setup_logging(verbose)
    from src.templates.hiera_to_vars import HieraConverter
    converted = HieraConverter().convert_dir(Path(hiera_dir), Path(output))
    for path in converted:
        console.print(f"  [green]✓[/green] {path}")


# ── Internal helpers ───────────────────────────────────────────────────────────

# Module directories to skip when scanning for Puppet modules
_SKIP_DIRS = {
    ".git", ".svn", "__pycache__", "node_modules",
    "spec", "tests", "examples", ".fixtures",
}


def _build_hiera(
    hiera_path: str | None,
    manifest: str | None,
    module_paths: list[str],
) -> HieraResolver | None:
    """Build a HieraResolver from explicit path or auto-discovery."""
    if hiera_path:
        try:
            return HieraResolver(hiera_config=Path(hiera_path))
        except Exception as e:
            console.print(f"  [yellow]⚠[/yellow] Could not load hiera.yaml: {e}")
            return None
    return build_hiera_resolver(manifest_path=manifest, module_paths=module_paths)


def _resolve_includes(
    manifest: str,
    module_paths: list[str],
    pv: int,
    verbose: int,
) -> list[Path]:
    """Return ordered list of manifests to parse (with dependencies first)."""
    if not module_paths:
        return [Path(manifest)]

    try:
        pre = ManifestPreprocessor(module_paths=module_paths, puppet_version=pv)
        result = pre.resolve(manifest)
        if verbose >= 2 and len(result.manifest_paths) > 1:
            console.print(f"  [dim]Resolved {len(result.manifest_paths)} file(s) via include chains[/dim]")
        for w in result.warnings:
            console.print(f"  [dim]preprocessor: {w}[/dim]")
        return result.manifest_paths or [Path(manifest)]
    except Exception as e:
        console.print(f"  [yellow]⚠[/yellow] Preprocessor error: {e}")
        return [Path(manifest)]


def _convert_one_module(
    mod_dir: Path,
    pv: int,
    roles_out: Path,
    hiera_res: HieraResolver | None,
    module_paths: list[str],
    dry_run: bool,
    verbose: int,
    rb: MigrationReportBuilder | None = None,
) -> ConversionResult | None:
    """Convert a single module directory. Returns merged ConversionResult or None."""
    manifests_dir = mod_dir / "manifests"
    if not manifests_dir.is_dir():
        return None

    pp_files = sorted(manifests_dir.rglob("*.pp"))
    if not pp_files:
        return None

    results: list[ConversionResult] = []
    for pp_file in pp_files:
        try:
            ast = parse_file(pp_file, puppet_version=pv)
            conv = ManifestConverter(puppet_version=pv, hiera_resolver=hiera_res, module_paths=module_paths)
            r = conv.convert(ast)
            results.append(r)
            if rb:
                rb.add_file_result(str(pp_file), r)
        except ParseError as e:
            if verbose >= 1:
                console.print(f"    [yellow]⚠[/yellow] {mod_dir.name}/{pp_file.name}: {e}")
            if rb:
                rb.add_parse_error(str(pp_file), str(e))

    if not results:
        return None

    merged = _merge_results(results)
    merged.source_file = str(mod_dir / "manifests" / "init.pp")

    if not dry_run:
        role_dir = roles_out / mod_dir.name
        RoleGenerator().generate(merged, role_dir, role_name=mod_dir.name)

        # ERB templates → Jinja2
        templates_dir = mod_dir / "templates"
        if templates_dir.exists():
            if rb:
                _convert_templates_tracked(templates_dir, role_dir / "templates", verbose, rb)
            else:
                _convert_templates(templates_dir, role_dir / "templates", verbose)

        # Copy static files
        files_dir = mod_dir / "files"
        if files_dir.exists():
            _copy_files(files_dir, role_dir / "files")

        console.print(f"    [green]✓[/green] roles/{mod_dir.name}/  "
                      f"(conv={merged.total_converted} warns={len(merged.warnings)} unconv={len(merged.unconverted)})")

    return merged


def _write_report_if_requested(
    rb: MigrationReportBuilder,
    report_path: str | None,
) -> None:
    """Write migration report to file if --report was specified."""
    if not report_path:
        return
    path = Path(report_path)
    fmt = "json" if path.suffix.lower() == ".json" else "markdown"
    written = write_report(rb.build(), path, fmt=fmt)
    console.print(f"\n[bold green]Migration report:[/bold green] {written}")


def _convert_templates_tracked(
    src_dir: Path,
    out_dir: Path,
    verbose: int,
    rb: MigrationReportBuilder,
) -> None:
    """Convert ERB templates and track them in the report builder."""
    erb_conv = ErbConverter()
    out_dir.mkdir(parents=True, exist_ok=True)
    for erb_file in sorted(src_dir.rglob("*.erb")):
        rel = erb_file.relative_to(src_dir)
        j2_path = out_dir / rel.with_suffix(".j2")
        j2_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = erb_conv.convert_file(str(erb_file), str(j2_path))
            rb.add_template(str(erb_file), str(j2_path), result.warnings)
            if verbose >= 2:
                console.print(f"      [dim]template: {rel.with_suffix('.j2')}[/dim]")
            if verbose >= 1:
                for w in result.warnings:
                    console.print(f"      [yellow]⚠[/yellow] template {rel}: {w}")
        except Exception as e:
            if verbose >= 1:
                console.print(f"      [yellow]⚠[/yellow] template {rel}: {e}")


def _convert_templates(src_dir: Path, out_dir: Path, verbose: int) -> None:
    """Convert all ERB templates in src_dir to Jinja2 in out_dir."""
    erb_conv = ErbConverter()
    out_dir.mkdir(parents=True, exist_ok=True)
    for erb_file in sorted(src_dir.rglob("*.erb")):
        rel = erb_file.relative_to(src_dir)
        j2_path = out_dir / rel.with_suffix(".j2")
        j2_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = erb_conv.convert_file(str(erb_file), str(j2_path))
            if verbose >= 2:
                console.print(f"      [dim]template: {rel.with_suffix('.j2')}[/dim]")
            if verbose >= 1:
                for w in result.warnings:
                    console.print(f"      [yellow]⚠[/yellow] template {rel}: {w}")
        except Exception as e:
            if verbose >= 1:
                console.print(f"      [yellow]⚠[/yellow] template {rel}: {e}")


def _copy_files(src_dir: Path, out_dir: Path) -> None:
    """Copy static files directory (no conversion needed)."""
    import shutil
    if src_dir.exists():
        out_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(src_dir.rglob("*")):
            if f.is_file():
                dest = out_dir / f.relative_to(src_dir)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)


def _write_site_playbook(result: ConversionResult, out_dir: Path) -> None:
    """Write site.yml from node definitions or top-level tasks."""
    out_dir.mkdir(parents=True, exist_ok=True)

    if result.node_definitions:
        # Generate one play per node group
        lines = [
            "# Generated by p2a — site.yml",
            "# Review before deploying",
            "---",
        ]
        for node in result.node_definitions:
            hosts = node.get("hosts", "all")
            roles  = node.get("roles", [])
            tasks  = node.get("tasks", [])
            play: dict = {"hosts": hosts, "become": True}
            if roles:
                play["roles"] = roles
            if tasks:
                play["tasks"] = tasks
            import yaml
            lines.append(yaml.dump([play], default_flow_style=False, allow_unicode=True).rstrip())
        site_file = out_dir / "site.yml"
        site_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        console.print(f"  [green]✓[/green] site.yml ({len(result.node_definitions)} plays)")
    elif result.tasks:
        pg = PlaybookGenerator()
        site_file = out_dir / "site.yml"
        pg.write(result, site_file)
        console.print(f"  [green]✓[/green] site.yml")


def _write_inventory(result: ConversionResult, inv_dir: Path) -> None:
    inv_dir.mkdir(parents=True, exist_ok=True)
    inv_yaml = InventoryGenerator().generate(result)
    if inv_yaml:
        (inv_dir / "hosts.yml").write_text(inv_yaml, encoding="utf-8")


# ── Auto-discovery helpers ─────────────────────────────────────────────────────

def _autodiscover_modules(base: Path, verbose: int) -> list[str]:
    """Find module directories in a Puppet codebase."""
    candidates = ["modules", "site-modules", "dist", "site"]
    found = []
    for name in candidates:
        d = base / name
        if d.is_dir():
            found.append(str(d))
            if verbose >= 2:
                console.print(f"  [dim]Auto-detected modules dir: {d}[/dim]")
    return found


def _autodiscover_hiera(base: Path, verbose: int) -> Path | None:
    """Find hiera.yaml in or near the codebase root."""
    for name in ("hiera.yaml", "hiera.yml"):
        f = base / name
        if f.exists():
            if verbose >= 2:
                console.print(f"  [dim]Auto-detected hiera.yaml: {f}[/dim]")
            return f
    return None


def _autodiscover_hieradata(base: Path, hiera_file: Path | None, verbose: int) -> Path | None:
    """Find the hieradata directory."""
    # Try to read datadir from hiera.yaml
    if hiera_file and hiera_file.exists():
        try:
            import yaml
            raw = yaml.safe_load(hiera_file.read_text()) or {}
            datadir = raw.get(":datadir") or raw.get("datadir") or raw.get("defaults", {}).get("datadir")
            if datadir:
                d = Path(datadir) if Path(datadir).is_absolute() else hiera_file.parent / datadir
                if d.is_dir():
                    return d
        except Exception:
            pass

    # Fallback: common names
    for name in ("hieradata", "hiera", "data"):
        d = base / name
        if d.is_dir():
            if verbose >= 2:
                console.print(f"  [dim]Auto-detected hieradata: {d}[/dim]")
            return d
    return None


def _autodiscover_site_pp(base: Path, verbose: int) -> Path | None:
    """Find the main site.pp entry point."""
    candidates = [
        base / "manifests" / "site.pp",
        base / "site.pp",
        base / "manifests" / "init.pp",
    ]
    for f in candidates:
        if f.exists():
            if verbose >= 2:
                console.print(f"  [dim]Auto-detected site.pp: {f}[/dim]")
            return f
    return None


# ── Output helpers ─────────────────────────────────────────────────────────────

def _write_output(
    result: ConversionResult,
    out_dir: Path,
    mode: str,
    source_file: str,
    hosts: str,
) -> list[Path]:
    written = []
    stem = Path(source_file).stem

    if mode == "playbook":
        gen      = PlaybookGenerator()
        out_file = out_dir / f"{stem}.yml"
        out_dir.mkdir(parents=True, exist_ok=True)
        gen.write(result, out_file, hosts=hosts)
        written.append(out_file)
    else:
        role_dir = out_dir / stem
        RoleGenerator().generate(result, role_dir, role_name=stem)
        written.append(role_dir)

    if result.node_definitions:
        _write_inventory(result, out_dir / "inventory")
        written.append(out_dir / "inventory" / "hosts.yml")

    if result.collections:
        req_file = out_dir / "requirements.yml"
        RoleGenerator().write_requirements(result, req_file)
        written.append(req_file)

    return written


def _merge_results(results: list[ConversionResult]) -> ConversionResult:
    merged = ConversionResult()
    for r in results:
        merged.tasks.extend(r.tasks)
        merged.handlers.extend(r.handlers)
        merged.variables.update(r.variables)
        merged.classes.extend(r.classes)
        merged.defined_types.extend(r.defined_types)
        merged.node_definitions.extend(r.node_definitions)
        merged.collections.update(r.collections)
        merged.warnings.extend(r.warnings)
        merged.unconverted.extend(r.unconverted)
        for k, v in r.converted_counts.items():
            merged.converted_counts[k] = merged.converted_counts.get(k, 0) + v
    return merged


def _accumulate(total: ConversionResult, r: ConversionResult) -> None:
    total.warnings.extend(r.warnings)
    total.unconverted.extend(r.unconverted)
    total.collections.update(r.collections)
    for k, v in r.converted_counts.items():
        total.converted_counts[k] = total.converted_counts.get(k, 0) + v


def _print_report(result: ConversionResult) -> None:
    console.print()

    if result.converted_counts:
        table = Table(title="Converted Resources", box=box.SIMPLE)
        table.add_column("Resource type", style="cyan")
        table.add_column("Count", justify="right", style="green")
        total = 0
        for rtype, count in sorted(result.converted_counts.items()):
            table.add_row(rtype, str(count))
            total += count
        table.add_row("[bold]TOTAL[/bold]", f"[bold]{total}[/bold]")
        console.print(table)

    if result.warnings:
        console.print(f"\n[yellow]⚠  {len(result.warnings)} warning(s):[/yellow]")
        for w in result.warnings[:10]:
            console.print(f"  [yellow]•[/yellow] {w}")
        if len(result.warnings) > 10:
            console.print(f"  [dim]... and {len(result.warnings) - 10} more[/dim]")

    if result.unconverted:
        console.print(f"\n[red]✗  {len(result.unconverted)} resource(s) not converted:[/red]")
        for item in result.unconverted[:10]:
            console.print(f"  [red]•[/red] {item['type']}['{item['title']}'] — {item['reason']}")
        if len(result.unconverted) > 10:
            console.print(f"  [dim]... and {len(result.unconverted) - 10} more[/dim]")

    if result.collections:
        console.print(f"\n[bold]Collections required:[/bold] {', '.join(sorted(result.collections))}")
        console.print("  Install with: ansible-galaxy collection install -r requirements.yml")
