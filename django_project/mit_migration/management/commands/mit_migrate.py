import errno
import logging
import subprocess
import textwrap
import os

from django.core.management.base import BaseCommand
import mysql.connector
from optparse import make_option
import pytz

from frontend import models
from frontend.models import article_url_to_filename
from frontend.views import swap_http_https
from util.Bag import Bag

logger = logging.getLogger(__name__)
eastern_timezone = pytz.timezone("US/Eastern")

# Put the migrated article versions into a separate directory so that Git
# operations don't conflict
MIGRATED_VERSIONS_GIT_SUBDIR = 'mit_migration'
# This is where to find the Git dirs of the articles versions to migrate
MIGRATION_VERSIONS_DIR = '/newsdiffs-efs/mit_migration/dump'


class Command(BaseCommand):
    # option_list = BaseCommand.option_list + (
    #     make_option('--all',
    #                 action='store_true',
    #                 default=False,
    #                 help='Update _all_ stored articles'),
    # )
    help = textwrap.dedent('''Migrate data from MIT data dump to AWS''').strip()

    def handle(self, *args, **options):
        migration_connection = None
        migration_cursor = None
        connection = None
        cursor = None
        try:
            migration_connection = mysql.connector.connect(
                host=os.environ['DB_HOST'],
                database='mit_migration',
                user=os.environ['DB_USER'],
                password=os.environ['DB_PASSWORD'],
            )
            migration_cursor = migration_connection.cursor(named_tuple=True)

            cutoff = get_migrate_cutoff(migration_cursor)
            logger.info('Using cutoff: %s', cutoff)

            connection = mysql.connector.connect(
                host=os.environ['DB_HOST'],
                database=os.environ['DB_NAME'],
                user=os.environ['DB_USER'],
                password=os.environ['DB_PASSWORD'],
            )
            cursor = migration_connection.cursor(named_tuple=True)

            article_query = """
                select 
                  a.id as article_id
                  a.url
                  a.initial_date
                  a.last_update
                  a.last_check
                  a.git_dir
                  v.id as version_id
                  v.v
                  v.title
                  v.byline
                  v.date
                  v.boring
                  v.diff_json
                from Articles a join versions v on a.id = v.article_id
                  where a.initial_date < %(cutoff)s
                order by a.id, v.date
            """
            migration_cursor.execute(article_query, {'cutoff': cutoff})
            current_article = None
            current_versions = []
            for row in migration_cursor:
                if not current_article:
                    current_article = make_article(row)
                    current_versions.append(make_version(row))
                elif current_article.id == row.article_id:
                    current_versions.append(make_version(row))
                else:
                    process_article_versions(cursor, current_article,
                                             current_versions)
                    connection.commit()

                    current_article = make_article(row)
                    current_versions[:] = [make_version(row)]
        finally:
            if migration_cursor:
                cursor.close()
            if migration_connection:
                connection.close()
            if cursor:
                cursor.close()
            if connection:
                connection.close()


def make_article(row):
    return Bag(
        id=row.article_id,
        url=row.url,
        initial_date=row.initial_date,
        last_update=row.initial_date,
        last_check=row.last_check,
        git_dir=row.git_dir,
    )


def make_version(row):
    return Bag(
        id=row.version_id,
        v=row.v,
        title=row.title,
        byline=row.byline,
        date=row.date,
        boring=row.boring,
        diff_json=row.diff_json
    )


def fix_date(naive_eastern_datetime):
    eastern_aware_datetime = \
        eastern_timezone.localize(naive_eastern_datetime, is_dst=None)
    return eastern_aware_datetime.astimezone(pytz.utc).replace(tzinfo=None)


def process_article_versions(cursor, migration_article, versions):
    # Figure out if the article exists in the new DB.
    # If so, exclude versions that overlap.
    # Also exclude the most-recent non-overlapping version if it's equal to the
    # oldest one in the new DB.
    # Also need to copy over the file and commit it.
    try:
        extant_article = models.Article.objects.get(url=migration_article.url)
    except models.Article.DoesNotExist:
        try:
            extant_article = models.Article.objects.get(url=swap_http_https(migration_article.url))
        except models.Article.DoesNotExist:
            migrate_article(cursor, migration_article)
            migrate_versions(cursor, migration_article, versions)
        else:
            migrate_non_overlapping_article_versions(cursor, migration_article, extant_article, versions)
    else:
        migrate_non_overlapping_article_versions(cursor, migration_article, extant_article, versions)


def migrate_article(cursor, article):
    article_query = """
        insert into Articles (id, url, initial_date, last_update, last_check, git_dir, is_migrated) 
        values (%(id)s, %(url)s, %(initial_date)s, %(last_update)s, %(last_check)s, %(git_dir)s, TRUE)
    """
    article_data = {
        'id': article.id,
        'url': article.url,
        'initial_date': fix_date(article.initial_date),
        'last_update': fix_date(article.last_update),
        'last_check': fix_date(article.last_check),
        'git_dir': os.path.join(MIGRATED_VERSIONS_GIT_SUBDIR, article.git_dir),
    }
    cursor.execute(article_query, article_data)


def migrate_versions(cursor, migration_article, migration_versions, filename_article):

    git_dir = os.path.join(os.environ['ARTICLES_DIR_ROOT'], MIGRATED_VERSIONS_GIT_SUBDIR, migration_article.git_dir)
    if not os.path.exists(git_dir):
        logger.debug('Initializing Git repo at: %s', git_dir)
        make_git_repo(git_dir)

    for migration_version in migration_versions:

        migrate_version_text = get_version_text(
            migration_version,
            # Use the migrate article in case the URLs differ by scheme
            article_url_to_filename(migration_article),
            os.path.join(MIGRATION_VERSIONS_DIR, migration_article.git_dir)
        )

        version_path = os.path.join(git_dir, filename_article.filename())
        write_version_file(migrate_version_text, version_path)
        commit_version_file(version_path, git_dir, migration_article.url, migration_version.date)

        version_query = """
            insert into version (id, v, title, byline, date, boring, diff_json, is_migrated)
            values (%(id)s, %(v)s, %(title)s, %(byline)s, %(date)s, %(boring)s, %(diff_json)s, TRUE)
        """
        version_data = {
            'id': migration_version.id,
            'v': migration_version.v,
            'title': migration_version.title,
            'byline': migration_version.byline,
            'date': fix_date(migration_version.date),
            'boring': migration_version.boring,
            'diff_json': migration_version.diff_json,
        }
        cursor.execute(version_query, version_data)


def make_dirs(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST:
            pass
        else:
            raise


def make_git_repo(path):
    make_dirs(path)

    subprocess.check_output(['git', 'init'], cwd=path)

    # Create a file so that there is something to commit
    initial_commit_file = os.path.join(path, 'initial-commit-file')
    open(initial_commit_file, 'w').close()

    configure_git(path)

    subprocess.check_output(['git', 'add', initial_commit_file], cwd=path)
    subprocess.check_output(['git', 'commit', '-m', 'Initial commit'], cwd=path)


def configure_git(git_dir):
    subprocess.check_output(['git', 'config', 'user.email',
                             'migration@newsdiffs.org'], cwd=git_dir)
    subprocess.check_output(['git', 'config', 'user.name',
                             'NewsDiffs Migration'], cwd=git_dir)


def write_version_file(file_text, path):
    with open(path, 'w') as dest:
        dest.write(file_text)
    # Write the files as world-readable to avoid permissions errors between
    # the web and scraper
    os.chmod(path, 0o777)


def commit_version_file(version_path, git_dir, url, date):
    command_parts = ['git', 'add', version_path]
    output = subprocess.check_output(command_parts, cwd=git_dir)
    logging.info('%s: %s', ' '.join(command_parts), output)

    command_parts = ['git', 'commit', '-m', 'Migrating %s from %s' % (url, date)]
    output = subprocess.check_output(command_parts, cwd=git_dir)
    logging.info('%s: %s', ' '.join(command_parts), output)


def migrate_non_overlapping_article_versions(
        cursor,
        migration_article,
        extant_article,
        versions
):
    oldest_extant_version = get_oldest_extant_version(extant_article)
    last_non_overlapping_version_index = -1
    # The versions should be sorted by date
    for i, version in enumerate(versions):
        if version.date > oldest_extant_version.date:
            last_non_overlapping_version_index = i - 1
            break
    if last_non_overlapping_version_index > -1:
        last_non_overlapping_version = versions[last_non_overlapping_version_index]
        migrate_version_text = get_version_text(
            last_non_overlapping_version,
            # Use the migrate article in case the URLs differ by scheme
            article_url_to_filename(migration_article),
            os.path.join(MIGRATION_VERSIONS_DIR, migration_article.git_dir)
        )
        extant_version_text = oldest_extant_version.text()
        # If the most recent non-overlapping version is equal to the
        # one we already have, take the one before it
        if migrate_version_text == extant_version_text:
            last_non_overlapping_version_index -= 1
        # Check again that such a version actually exists since we updated the
        # index
        if last_non_overlapping_version_index > -1:
            non_overlapping_versions = \
                versions[:last_non_overlapping_version_index+1]
            migrate_versions(cursor, migration_article, non_overlapping_versions)
    else:
        migrate_versions(cursor, migration_article, versions)


def get_version_text(version, filename, git_dir):
    revision = version.v + ':' + filename
    return subprocess.check_output(['git', 'show', revision], cwd=git_dir)


def get_oldest_extant_version(extant_article):
    return models.Version.objects.filter(article_id=extant_article.id).order_by('date').first()


def get_migrate_cutoff(cursor):
    cursor.execute('select min(initial_date) as cutoff from Articles')
    return cursor.fetchone().cutoff
