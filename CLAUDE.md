# stubgenj — Project Notes for Claude

## Project Purpose

PEP-484 Python stub generator for Java modules using the JPype import system. Walks Java package trees and generates `.pyi` files with proper type annotations, enabling type checking in Python when using Java libraries.

## Architecture

- **Entry point (CLI):** `stubgenj/__main__.py`
- **Core engine:** `stubgenj/_stubgenj.py`
- **Public API:** `stubgenj/__init__.py` exports `generateJavaStubs`
- **Tests:** `stubgenj/tests/test_stubtest.py` (integration-only via mypy)
- **Build:** setuptools + setuptools_scm (version from git tags)

### Key Workflow
1. Start JVM via JPype, enable import system
2. Walk Java package tree recursively
3. For each Java class, generate Python stub with type annotations
4. Handle type translation (Java → Python), generics, overloads, javadoc
5. Generate module protocols for `JPackage` introspection

## Docstring Pipeline

Javadoc is extracted by JPype's `JavadocExtractor` and flows through two sanitization stages:

### Stage 1 — HTML sanitization (`sanitizeJavadocHtml`)

Unescapes HTML entities (`&lt;`, `&gt;`, `&nbsp;`, non-breaking spaces). Applied to all javadoc content before it is stored in `Javadoc`.

### Stage 2 — RST cleanup (`sanitizeJavadocRst`)

Applied **after** `splitMethodOverloadJavadoc` for methods/constructors, and directly for class descriptions and field docstrings. Does two things:

1. **Strips the leading Java declaration line** — the first non-empty line containing `public ` (e.g. `public class Foo extends Bar`) is redundant given the Python class signature and is removed.
2. **Simplifies Sphinx cross-references** — `` :class:`~org.orekit.time.UTCScale` `` → `UTCScale`, `` :meth:`~pkg.Class.method` `` → `method`, broken Oracle javadoc URL refs → cleaned up.

### Critical ordering constraint for methods

`splitMethodOverloadJavadoc` uses the `public ReturnType methodName(args)` lines as **delimiters** to assign javadoc sections to overloads. **Do not apply `sanitizeJavadocRst` to `jDoc.methods` or `jDoc.ctors` before splitting** — stripping those delimiter lines first causes all overloads to get empty docstrings. The RST cleanup is applied per-overload only after the split.

### Parameter names from javadoc (`extractParamNamesFromJavadoc`)

Parses the `Parameters:` section of a sanitized overload docstring to extract names in order. If the count matches the number of non-`self` arguments in the signature, the javadoc names replace the bytecode-inferred names (e.g. `extendedPositionProvider` → `sun`). Falls back to inferred names when counts differ or no javadoc is present.

## Known Issues

### Critical / High

- **No error handling in `__main__.py` (~line 39-49):** `jpype.startJVM()` has no try/catch; JVM halt is called unconditionally regardless of success/failure.
- **Potential infinite loop in `generateJavaClassStub()` (~line 1220-1240):** `while True:` depends on side-effects modifying `classesDoneNested`; no iteration limit — certain Java class hierarchies could cause hangs.
- **Bare `except Exception` (~lines 103, 255 of `_stubgenj.py`):** Catches `KeyboardInterrupt`/`SystemExit`, masks real bugs and prevents clean shutdown.
- **Empty string crash in `inferArgName()` (~line 843):** `typename[:1].lower() + typename[1:]` crashes on empty string from unusual Java type names.

### Security

- **Path traversal:** `outputDir` from CLI is not resolved/validated before use — could write outside intended directory. Fix: `Path(outputDir).resolve()`.
- **Classpath glob injection:** `glob(c_in)` in `__main__.py:37` processes user-supplied patterns without safety checks — could load malicious JARs.
- **ReDoS in `splitMethodOverloadJavadoc()` (~line 909):** Complex regex compiled per-method from unvalidated javadoc input; crafted input could cause CPU exhaustion.

### Fragile Private API Access

- `_stubgenj.py:38` — `from jpype._pykeywords import pysafe`
- `_stubgenj.py:595` — `classHints = jpClass._hints`
- Both access private JPype internals with no version guards — silently breaks on JPype upgrades.

### Dependencies (setup.py)

- `python_requires='~=3.6'` — Python 3.6 is EOL (Dec 2021); should be `~=3.7`.
- `mypy>=0.931,<0.971` — mypy is at 1.x; this constraint blocks all current versions.
- `JPype1<2.0.dev0` — excludes dev/RC releases of JPype 2.0.
- `typing_extensions` only declared for Python <3.8, but `Literal` is used at runtime — could fail on 3.7 without it.

### Test Coverage

- ~2.5% test-to-source ratio (65 lines tests vs 1,295 lines source).
- Only integration tests (run mypy on generated stubs) — no unit tests.
- Completely untested: `filterClassNamesInPackage()`, `translateTypeName()`, `inferArgName()`, `pythonType()`, `splitMethodOverloadJavadoc()`.
- No error condition tests (JPype exceptions, circular deps, empty packages).

### CI/CD (.gitlab-ci.yml)

- No mypy type checking or static analysis (flake8/bandit) in CI.
- Only Python 3.7 and 3.9 tested — 3.8, 3.10, 3.11+ not covered.
- JPype 1.2 and 1.3 not tested despite being in version requirements.
- `-p no:faulthandler` in `pyproject.toml:13` suppresses useful crash diagnostics.

### Performance

- `@functools.lru_cache(maxsize=None)` on `convertStrings()` — unbounded cache; memory leak in batch processing.
- Dependency resolution while-loop in `generateStubsForJavaPackage()` (~line 283-338) has no iteration counter — potentially quadratic for large package trees.
- Regex compiled per-method in `splitMethodOverloadJavadoc()` — should be pre-compiled.
- `pythonType()` called recursively without memoization.

## Recommended Fixes (Priority Order)

1. Fix path traversal — `Path(outputDir).resolve()`
2. Wrap `__main__.py` in try/finally — ensure JVM halted even on failure
3. Add iteration limit to `while True:` in `generateJavaClassStub()`
4. Fix empty string in `inferArgName()` — guard before slicing
5. Update mypy and Python version constraints in `setup.py`
6. Add mypy + flake8 to CI pipeline
7. Write unit tests for core functions (target ≥60% coverage)
8. Add version guards around private JPype API accesses
9. Validate javadoc input size before regex to prevent ReDoS
10. Bound the `lru_cache` on `convertStrings()`
