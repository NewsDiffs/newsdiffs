from util import env_util


def test_read_sh_env_vars():
    config = """
        export VAR_1=VAL_1
        export VAR_2=VAL_2
        export VAR_3='something (that) might require quotes'
        """
    expected = {
        'VAR_1': 'VAL_1',
        'VAR_2': 'VAL_2',
        'VAR_3': 'something (that) might require quotes',
    }

    assert env_util.read_sh_env_vars(config) == expected
