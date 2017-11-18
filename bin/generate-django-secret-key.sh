# Just for reference:
# Requires a higher version of django than we currently use, so need to install that in a different virtual env
from django.core.management import utils
utils.get_random_secret_key()