from onb_mcp_mongo import PROJECT


def test_package_imports_and_names_its_project() -> None:
    assert PROJECT == "mcp-mongo"
