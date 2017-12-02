#! /bin/sh

source /opt/python/run/venv/bin/activate
source /opt/python/current/env
export LOG_FILE_PATH=/home/ec2-user/mit_migrate.log
python /opt/python/current/app/django_project/manage.py mit_migrate