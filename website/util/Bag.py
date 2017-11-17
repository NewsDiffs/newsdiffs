class Bag(object):
    """A simple container for named properties"""
    def __init__(self, **kwargs):
        self.__dict__.update(**kwargs)
