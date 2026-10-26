# JUnit XML fixtures: tools and commands

Date: 2026-10-25. Platform: Windows 10 (win32), amd64.

The reports in `pytest/`, `jest/` and `go/` are tool output: each was written by the tool named below and not touched by hand afterwards (not even whitespace or timestamps), with one exception: the machine's host name in every `hostname="..."` attribute of the `go/` and `pytest/` reports was replaced with `fixture-host`, and the machine's home-folder prefix in the paths of `jest/red.xml`, `pytest/red.xml` and `pytest/green.xml` (stack frames and skip locations) was replaced with `C:\fixtures\` (#395: the first commit of these files carried both). Nothing else was edited. Their other post-processing is git's text normalisation to LF on commit.

The sources the reports were produced from are in `sources/<tool>/<scenario>/`. Each scenario has the same three results:

- `green`: `adds` passes, `subtracts` passes, `skips` is skipped.
- `red`: `adds` passes, `subtracts` fails (1 - 1 == 1), `skips` is skipped.
- `empty`: a run with zero tests.

## Versions

| Tool | Version |
| --- | --- |
| node | v24.11.1 |
| npm | 11.6.2 |
| jest | 30.5.2 |
| jest-junit | 17.0.0 |
| go | go1.24.13 windows/amd64 (official zip from go.dev/dl) |
| go-junit-report | v2.1.0 (`go install github.com/jstemmer/go-junit-report/v2@v2.1.0`; the binary itself prints `go-junit-report v2.1.0-dev  (HEAD)`, which is what `go install` of a tagged version stamps) |
| python | 3.12.4 |
| pytest | 9.0.3 (plugins loaded: anyio 4.12.1, testmon 2.2.0, xdist 3.8.0; none of them adds anything to the report) |

## Commands

Every command ran with the scenario's sources copied to an empty folder outside the repository, with that folder as the working directory. That way the pytest rootdir is that folder (so the classname is `test_math`, not a path through this repository), and jest finds `jest` and `jest-junit` in an installation next to it. `<out>` below is this folder.

### pytest (`pytest/<scenario>.xml`)

```
python -m pytest -p no:cacheprovider --junitxml=<out>/pytest/<scenario>.xml .
```

Exit codes: green 0, red 1, empty 5 (pytest's "no tests ran"; the report was still written).

### jest (`jest/<scenario>.xml`)

```
JEST_JUNIT_OUTPUT_DIR=<out>/jest JEST_JUNIT_OUTPUT_NAME=<scenario>.xml npx jest --ci --reporters=default --reporters=jest-junit
```

For `empty` the same command plus `--passWithNoTests` (the folder holds only `package.json`, no test file). No other jest-junit option is set; every other jest-junit setting is its default. Exit codes: green 0, red 1, empty 0.

### go (`go/<scenario>.xml`)

```
go test -v ./... 2>&1 | go-junit-report -set-exit-code > <out>/go/<scenario>.xml
```

Exit codes of the pipeline: green 0, red 1, empty 0. For `empty` the package holds a `_test.go` file with no test functions.
