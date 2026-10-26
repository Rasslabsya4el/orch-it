import os


def test_red_on_a_push():
    assert os.environ.get('GITHUB_EVENT_NAME') not in ('push', 'workflow_dispatch'), 'the trial: red only on the base'
