from onb_orchestrator import PROJECT


def test_package_imports_and_names_its_project() -> None:
    assert PROJECT == "orchestrator"
