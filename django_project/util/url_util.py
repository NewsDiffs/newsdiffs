def get_url_domain(url):
    return url.split('/')[2]


def remove_query_params(url):
    return url.split('?')[0]


def get_url_authority(url):
    """The authority is the scheme plus the domain"""
    return '/'.join(url.split('/')[:3])


def remove_parameters(url):
    """Removes query params and anchor"""
    return url.split('?')[0].split('#')[0].strip()
