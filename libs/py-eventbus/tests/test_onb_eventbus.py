from onb_eventbus import PROJECT


def test_package_imports_and_names_its_project() -> None:
    assert PROJECT == "py-eventbus"
