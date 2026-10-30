"""The two check names of the tests workflow, read from the setting `required_checks` default (`testruns/defaults.json`,
read by `testruns/config.py`), and the OS words of a test run's record, read from `OSES` there. A test names a check
or an OS by these constants, never by a quoted literal, so a settings change or a second machine's setup changes one
value here and nowhere else."""

from orch_it.testruns.config import DEFAULT_REQUIRED_CHECKS, OSES

LINUX_CHECK, WINDOWS_CHECK = DEFAULT_REQUIRED_CHECKS
LINUX_OS, WINDOWS_OS, MACOS_OS = OSES[0], OSES[1], OSES[2]  # the OS words of a test run's record, from `OSES`
# the foreign jobs of the tests workflow: their names are built from the required names and the OS words
FOREIGN_PREFIX = "foreign-"
FOREIGN_LINUX, FOREIGN_WINDOWS, FOREIGN_MACOS = FOREIGN_PREFIX + LINUX_CHECK, FOREIGN_PREFIX + WINDOWS_CHECK, FOREIGN_PREFIX + MACOS_OS
