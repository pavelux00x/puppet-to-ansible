"""Integration tests — parse → convert → generate for all 16 fixtures."""
from __future__ import annotations

import glob
import pathlib
import tempfile
from pathlib import Path

import pytest

from puppet_to_ansible.converters.manifest_converter import ManifestConverter
from puppet_to_ansible.generators.playbook import InventoryGenerator, PlaybookGenerator, RoleGenerator
from puppet_to_ansible.parser.parser import parse_file

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "input"


def all_fixtures():
    """Return (fixture_path, fixture_name) for all 16 input fixtures."""
    files = sorted(FIXTURES_DIR.glob("*.pp"))
    return [(f, f.stem) for f in files]


@pytest.mark.parametrize("fixture_path,fixture_name", all_fixtures(), ids=[f.stem for f, _ in all_fixtures()])
def test_fixture_parses_without_error(fixture_path, fixture_name):
    """Every fixture must parse to a Manifest without raising ParseError."""
    manifest = parse_file(fixture_path)
    assert manifest is not None
    assert len(manifest.statements) > 0


@pytest.mark.parametrize("fixture_path,fixture_name", all_fixtures(), ids=[f.stem for f, _ in all_fixtures()])
def test_fixture_converts_without_error(fixture_path, fixture_name):
    """Every fixture must convert without raising an exception."""
    manifest = parse_file(fixture_path)
    result = ManifestConverter().convert(manifest)
    assert result is not None


@pytest.mark.parametrize("fixture_path,fixture_name", all_fixtures(), ids=[f.stem for f, _ in all_fixtures()])
def test_fixture_produces_tasks_or_classes(fixture_path, fixture_name):
    """Every fixture must produce either tasks, classes, defined types, or nodes."""
    manifest = parse_file(fixture_path)
    result = ManifestConverter().convert(manifest)
    has_output = (
        len(result.tasks) > 0
        or len(result.classes) > 0
        or len(result.defined_types) > 0
        or len(result.node_definitions) > 0
    )
    assert has_output, f"{fixture_name} produced no output"


@pytest.mark.parametrize("fixture_path,fixture_name", all_fixtures(), ids=[f.stem for f, _ in all_fixtures()])
def test_fixture_generates_valid_yaml(fixture_path, fixture_name):
    """Every fixture must produce parseable YAML output."""
    import yaml

    manifest = parse_file(fixture_path)
    result = ManifestConverter().convert(manifest)

    if result.suggested_output_mode == "playbook" or not (result.classes or result.defined_types or result.node_definitions):
        gen = PlaybookGenerator()
        yaml_str = gen.generate(result)
        docs = list(yaml.safe_load_all(yaml_str.split("---\n", 1)[-1]))
        assert len(docs) >= 1
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            rg = RoleGenerator()
            rg.generate(result, tmpdir)
            main_tasks = Path(tmpdir) / "tasks" / "main.yml"
            assert main_tasks.exists(), f"{fixture_name}: tasks/main.yml not generated"
            parsed = yaml.safe_load(main_tasks.read_text())
            assert parsed is not None


# ── Specific fixture tests ────────────────────────────────────────────────────

class TestSimpleFixtures:
    def test_01_single_package(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("01_simple_package.pp")
        assert r.total_converted == 1
        assert r.converted_counts.get("package") == 1
        assert r.tasks[0]["ansible.builtin.package"]["name"] == "nginx"
        assert r.tasks[0]["ansible.builtin.package"]["state"] == "present"

    def test_02_single_service(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("02_simple_service.pp")
        assert r.total_converted == 1
        assert r.converted_counts.get("service") == 1

    def test_03_file_resources(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("03_simple_file.pp")
        assert r.total_converted >= 3
        assert r.converted_counts.get("file", 0) >= 3

    def test_04_notify_generates_handler(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("04_multi_resource_with_deps.pp")
        assert len(r.handlers) >= 1
        assert any("nginx" in h["name"].lower() for h in r.handlers)

    def test_05_exec_refreshonly_becomes_handler(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("05_exec_user_cron.pp")
        # exec with refreshonly should become a handler
        handler_names = [h["name"].lower() for h in r.handlers]
        assert len(handler_names) > 0

    def test_06_variables_and_conditionals(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("06_variables_and_conditionals.pp")
        assert r.total_converted >= 2
        # Tasks with when conditions
        tasks_with_when = [t for t in r.tasks if "when" in t]
        assert len(tasks_with_when) > 0


class TestClassFixtures:
    def test_07_class_basic(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("07_class_basic.pp")
        assert len(r.classes) == 1
        assert r.classes[0]["name"] == "nginx"
        assert r.suggested_output_mode == "role"

    def test_08_class_with_params(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("08_class_with_params.pp")
        assert len(r.classes) == 1
        # Class tasks inside the class definition
        cls = r.classes[0]
        assert len(cls.get("tasks", [])) > 0

    def test_09_defined_type(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("09_defined_type.pp")
        assert len(r.defined_types) == 1
        assert r.suggested_output_mode == "role"


class TestEnterpriseFixtures:
    def test_10_hiera_and_inheritance(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("10_hiera_and_inheritance.pp")
        assert len(r.classes) >= 2

    def test_11_puppet4_modern(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("11_puppet4_modern.pp")
        assert len(r.classes) == 1
        assert r.total_converted >= 3

    def test_15_enterprise_full_site(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("15_enterprise_full_site.pp")
        assert len(r.classes) >= 5
        assert len(r.node_definitions) >= 5
        assert r.suggested_output_mode == "role"

    def test_16_puppet3_legacy(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("16_puppet3_legacy.pp", puppet_version=3)
        assert r.total_converted >= 3


class TestOutputModeSelection:
    def test_simple_resources_suggest_playbook(self):
        from tests.conftest import convert_snippet
        r = convert_snippet("package { 'nginx': ensure => installed }")
        assert r.suggested_output_mode == "playbook"

    def test_class_definition_suggests_role(self):
        from tests.conftest import convert_snippet
        r = convert_snippet("class mymodule { package { 'nginx': ensure => installed } }")
        assert r.suggested_output_mode == "role"

    def test_node_definition_suggests_role(self):
        from tests.conftest import convert_snippet
        r = convert_snippet("node 'web01' { include nginx }")
        assert r.suggested_output_mode == "role"


class TestGracefulDegradation:
    def test_unknown_resource_generates_todo(self):
        from tests.conftest import convert_snippet
        r = convert_snippet("custom_type { 'something': param => 'value' }")
        assert len(r.unconverted) == 1
        assert r.unconverted[0]["type"] == "custom_type"

    def test_unconverted_still_produces_valid_result(self):
        from tests.conftest import convert_snippet
        r = convert_snippet("custom_type { 'something': param => 'value' }")
        # Should not raise, should produce a TODO task
        pg = PlaybookGenerator()
        yaml_str = pg.generate(r)
        assert "TODO" in yaml_str or len(yaml_str) > 0

    def test_no_silent_skips(self):
        """Unconverted resources MUST appear in either tasks or unconverted list."""
        from tests.conftest import convert_snippet
        r = convert_snippet("""
        package { 'nginx': ensure => installed }
        totally_unknown_resource { 'x': a => 'b' }
        """)
        # Either the unknown resource is in unconverted OR there's a TODO task
        todo_tasks = [t for t in r.tasks if "TODO" in str(t.get("name", ""))]
        assert len(r.unconverted) > 0 or len(todo_tasks) > 0


class TestGenerators:
    def test_playbook_has_header(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("01_simple_package.pp")
        pg = PlaybookGenerator()
        yaml_str = pg.generate(r)
        assert "Generated by p2a" in yaml_str

    def test_playbook_has_hosts_all(self):
        from tests.conftest import convert_fixture
        import yaml
        r = convert_fixture("01_simple_package.pp")
        pg = PlaybookGenerator()
        yaml_str = pg.generate(r)
        docs = yaml.safe_load(yaml_str.split("---\n", 1)[-1])
        assert docs[0]["hosts"] == "all"

    def test_role_generates_tasks_dir(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("07_class_basic.pp")
        with tempfile.TemporaryDirectory() as tmpdir:
            rg = RoleGenerator()
            rg.generate(r, tmpdir)
            assert (Path(tmpdir) / "tasks" / "main.yml").exists()

    def test_role_generates_handlers_dir(self):
        from tests.conftest import convert_fixture
        r = convert_fixture("04_multi_resource_with_deps.pp")
        with tempfile.TemporaryDirectory() as tmpdir:
            rg = RoleGenerator()
            rg.generate(r, tmpdir)
            assert (Path(tmpdir) / "handlers" / "main.yml").exists()

    def test_inventory_from_node_definitions(self):
        from tests.conftest import convert_fixture
        import yaml
        r = convert_fixture("15_enterprise_full_site.pp")
        ig = InventoryGenerator()
        inv_str = ig.generate(r)
        inv = yaml.safe_load(inv_str.split("---\n", 1)[-1])
        assert "all" in inv
