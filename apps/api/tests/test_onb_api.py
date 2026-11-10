from onb_api import PROJECT


def test_package_imports_and_names_its_project() -> None:
    assert PROJECT == "api"
