# Note that this conftest file exists at the package level as it is needed to configure
# the JVM for docstrings at the package level.
import os
import pathlib

import pytest


pytest_plugins = [
    'mypy.test.data',
]
os.environ['MYPY_TEST_PREFIX'] = str(pathlib.Path(__file__).parent / 'tests' / 'stubtest')


@pytest.fixture(autouse=True, scope="session")
def jvm():
    import jpype
    if not jpype.isJVMStarted(): jpype.startJVM(None, convertStrings=True)  # noqa
    import jpype.imports  # noqa
    yield jpype
