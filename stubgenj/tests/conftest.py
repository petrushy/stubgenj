import os
import pathlib

import pytest

pytest_plugins = [
    'mypy.test.data',
]
os.environ['MYPY_TEST_PREFIX'] = str(pathlib.Path(__file__).parent / 'stubtest')


@pytest.fixture(autouse=True, scope="session")
def jvm():
    import jpype
    if not jpype.isJVMStarted(): jpype.startJVM(None, convertStrings=True)  # noqa
    import jpype.imports  # noqa
    yield jpype
    from java.lang import Runtime  # noqa
    Runtime.getRuntime().halt(0)