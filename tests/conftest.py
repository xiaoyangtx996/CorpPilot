"""Run legacy unittest suites against a disposable project data tree."""
import importlib
import shutil
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_project_tree(tmp_path_factory):
    source = Path(__file__).resolve().parents[1]
    root = tmp_path_factory.mktemp("corppilot")
    for directory in ("agents", "flows", "skills"):
        shutil.copytree(source / directory, root / directory,
                        ignore=shutil.ignore_patterns(".history", "__pycache__"))

    modules = (
        "core", "bootstrap_skills", "flow_engine", "flow_io", "checklist",
        "design_artifacts", "visual_diff", "postcondition", "demo_greenfield",
        "project_close", "finance_agent", "cost_report", "traffic_seed",
        "skill_evolution", "runtime_bridge", "runtime.execution_backends",
        "runtime.agent_loop", "runtime.agent_manager", "runtime.message_bus",
        "runtime.model_router", "runtime.traffic_monitor", "runtime.tools",
    )
    # Load lazy artifact writers before redirecting their module constants.
    for name in modules:
        importlib.import_module(name)
    legacy_tests = {
        "test_board_flow", "test_e2e_workflow", "test_extended_modules",
        "test_flow_engine", "test_greenfield_e2e", "test_traffic_skills",
    }

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CORPPILOT_DATA_DIR", str(root / "data"))
        patch.setenv("CORPPILOT_AUTO_RUNTIME", "0")
        patch.setenv("CORPPILOT_CLAUDE_DRY_RUN", "0")
        # Includes test modules so their setup, assertions and cleanup use
        # the same tree as production code, without changing import paths.
        for module in tuple(sys.modules.values()):
            module_name = getattr(module, "__name__", "")
            if module_name not in modules and module_name.split(".")[-1] not in legacy_tests:
                continue
            filename = getattr(module, "__file__", None)
            if not filename or not Path(filename).is_relative_to(source):
                continue
            for name, value in tuple(vars(module).items()):
                if (isinstance(value, Path) and name.isupper()
                        and name not in {"SCRIPTS_DIR", "DASHBOARD_DIR"}
                        and value.is_relative_to(source)):
                    patch.setattr(module, name, root / value.relative_to(source))
        yield root
