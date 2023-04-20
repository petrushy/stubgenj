# stubgenj
PEP-484 python stub generator for Java modules using the JPype import system. Originally based on mypy stubgenc.

## Usage through cmmnbuild-dep-manager
```
$ python -m cmmnbuild_dep_manager stubgen
```
Every package registered in cmmnbuild_dep_manager can request stubs to be generated for particular 
prefixes by defining `__stubgen_packages__` next to `__cmmnbuild_deps__`.

For more details, consult the cmmnbuild_dep_manager docs. 

## CLI usage
```
$ python -m stubgenj --help
usage: __main__.py [-h] [--jvmpath JVMPATH] [--classpath CLASSPATH]
                   [--output-dir OUTPUT_DIR] [--convert-strings]
                   [--no-stubs-suffix]
                   prefixes [prefixes ...]

Generate Python Type Stubs for Java classes.

positional arguments:
  prefixes              package prefixes to generate stubs for (e.g.
                        org.myproject)

optional arguments:
  -h, --help            show this help message and exit
  --jvmpath JVMPATH     path to the JVM ("libjvm.so", "jvm.dll", ...)
                        (default: use system default JVM)
  --classpath CLASSPATH
                        java class path to use, separated by ":". glob-like
                        expressions (e.g. dir/*.jar) are supported (default:
                        .)
  --output-dir OUTPUT_DIR
                        path to write stubs to (default: .)
  --convert-strings     convert java.lang.String to python str in return
                        types. consult the JPype documentation on the
                        convertStrings flag for details
  --no-stubs-suffix     do not use PEP-561 "-stubs" suffix for top-level
                        packages
  --no-javadoc          do not generate docstrings from JavaDoc where 
                        available
```


E.g. generate stubs for java packages `cern.lsa.*` and `java.*` using the JARs from cmmnbuild-dep-manager:
```
$ python -m stubgenj --convert-strings --classpath "/path/to/cmmnbuild_dep_manager/lib/*.jar" cern.lsa java
```
The stubs will be put in the working directory as stub-only packages (PEP-561).

## Python API usage
Start up the JVM, enable the JPype import system, then invoke `stubgenj.generateJavaStubs`.

With plain JPype:
```python
import jpype
import stubgenj

jpype.startJVM(None, convertStrings=True)  # noqa
import jpype.imports  # noqa
import java.util  # noqa

stubgenj.generateJavaStubs([java.util], useStubsSuffix=True)
```

Through cmmnbuild-dep-manager:
```python
import cmmnbuild_dep_manager
import stubgenj

with cmmnbuild_dep_manager.Manager().imports():
    import java.util  # noqa
    stubgenj.generateJavaStubs([java.util], useStubsSuffix=True)
```
