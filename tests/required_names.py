"""The two check names of the tests workflow, read from the setting `required_checks` default (`testruns/defaults.json`,
read by `testruns/config.py`). A test names a check by these constants, never by a quoted literal, so a settings change
or a second machine's setup changes one value here and nowhere else."""

from orch_it.testruns.config import DEFAULT_REQUIRED_CHECKS

LINUX_CHECK, WINDOWS_CHECK = DEFAULT_REQUIRED_CHECKS
