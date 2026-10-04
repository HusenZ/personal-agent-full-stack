import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

pytestmark = pytest.mark.integration

ALEMBIC_CONFIG_PATH = "alembic.ini"


def _script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(ALEMBIC_CONFIG_PATH))


def test_migration_history_has_a_single_head():
    """Two heads mean concurrent branches were merged without a merge revision,
    which makes `alembic upgrade head` ambiguous."""
    assert len(_script_directory().get_heads()) == 1


def test_every_revision_is_reachable_from_the_base():
    script_directory = _script_directory()
    head = script_directory.get_current_head()
    assert head is not None

    reachable = {revision.revision for revision in script_directory.walk_revisions("base", head)}
    all_revisions = {revision.revision for revision in script_directory.walk_revisions()}

    assert reachable == all_revisions
