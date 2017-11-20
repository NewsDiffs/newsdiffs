# This only needs to be run once per database
cd /opt/python/ondeck/app/
source /opt/python/run/venv/bin/activate
source /opt/python/current/env
python django_project/manage.py createcachetable cache_table
