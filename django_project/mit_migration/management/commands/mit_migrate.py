import errno
import logging
import subprocess
import textwrap
import os

from django.core.management.base import BaseCommand
import MySQLdb
import MySQLdb.cursors
from optparse import make_option
import pytz

from frontend import models
from frontend.models import article_url_to_filename
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
        from_connection = None
        from_cursor = None
        to_connection = None
        to_cursor = None
        try:
            to_connection = MySQLdb.connect(
                host=os.environ['DB_HOST'],
                db=os.environ['DB_NAME'],
                user=os.environ['DB_USER'],
                passwd=os.environ['DB_PASSWORD'],
                cursorclass=MySQLdb.cursors.DictCursor,
            )
            to_cursor = to_connection.cursor()

            from_connection = MySQLdb.connect(
                host=os.environ['DB_HOST'],
                db='mit_migration',
                user=os.environ['DB_USER'],
                passwd=os.environ['DB_PASSWORD'],
                cursorclass=MySQLdb.cursors.SSDictCursor,
            )
            from_cursor = from_connection.cursor()

            migrate(from_cursor, to_connection, to_cursor)
        finally:
            if from_cursor:
                to_cursor.close()
            if hasattr(from_connection, 'close'):
                from_connection.close()
            if to_cursor:
                to_cursor.close()
            if hasattr(to_connection, 'close'):
                to_connection.close()


def migrate(from_cursor, to_connection, to_cursor):
    cutoff = get_migrate_cutoff(to_cursor)
    logger.info('Using cutoff: %s', cutoff)

    current_article = None
    current_versions = []
    row_count = query_migration_article_versions(from_cursor, cutoff)
    logger.info('Starting migrating %s article/version rows', row_count)
    for row in from_cursor:
        if not current_article:
            current_article = make_article(row)
            logger.debug('Started reading article %s', current_article.id)

            current_versions.append(make_version(row))
            logger.debug('Read article %s version %s', current_article.id, current_versions[-1].id)
        elif current_article.id == row['article_id']:
            current_versions.append(make_version(row))
            logger.debug('Read article %s version %s', current_article.id, current_versions[-1].id)
        else:
            logger.debug('Processing article %s', current_article.id)
            process_article_versions(to_cursor, current_article,
                                     current_versions)
            logger.debug('Processed article %s', current_article.id)

            logger.debug('Committing article %s', current_article.id)
            to_connection.commit()
            logger.debug('Committed article %s', current_article.id)

            current_article = make_article(row)
            logger.debug('Started reading article %s', current_article.id)

            current_versions[:] = [make_version(row)]
            logger.debug('Read article %s version %s', current_article.id, current_versions[-1].id)

    logger.debug('Processing article %s', current_article.id)
    process_article_versions(to_cursor, current_article, current_versions)
    logger.debug('Processed article %s', current_article.id)

    logger.debug('Committing article %s', current_article.id)
    to_connection.commit()
    logger.debug('Committed article %s', current_article.id)


def query_migration_article_versions(from_cursor, cutoff):
    article_query = """
        select 
            a.id as article_id
          , a.url
          , a.initial_date
          , a.last_update
          , a.last_check
          , a.git_dir
          , v.id as version_id
          , v.v
          , v.title
          , v.byline
          , v.date
          , v.boring
          , v.diff_json
        from Articles a join version v on a.id = v.article_id
          where a.initial_date < %(cutoff)s
        order by a.id, v.date
    """
    return execute_query(from_cursor, article_query, dict(cutoff=cutoff))


def execute_query(cursor, query, *args):
    logger.debug('Executing query: %s', query)
    if len(args) > 0:
        params = args[0]
        logger.debug('Query params: %s', params)
    return cursor.execute(query, *args)


def make_article(row):
    return Bag(
        id=row['article_id'],
        url=row['url'],
        initial_date=row['initial_date'],
        last_update=row['last_update'],
        last_check=row['last_check'],
        git_dir=row['git_dir'],
    )


def make_version(row):
    return Bag(
        id=row['version_id'],
        v=row['v'],
        title=row['title'],
        byline=row['byline'],
        date=row['date'],
        boring=row['boring'],
        diff_json=row['diff_json'],
    )


def fix_date(naive_eastern_datetime):
    eastern_aware_datetime = \
        eastern_timezone.localize(naive_eastern_datetime, is_dst=None)
    return eastern_aware_datetime.astimezone(pytz.utc).replace(tzinfo=None)


def process_article_versions(to_cursor, from_article_data, from_version_datas):
    # Figure out if the article exists in the new DB.
    # If so, exclude versions that overlap.
    # Also exclude the most-recent non-overlapping version if it's equal to the
    # oldest one in the new DB.
    # Also need to copy over the file and commit it.
    try:
        extant_to_article = models.Article.objects.get(url=from_article_data.url)
    except models.Article.DoesNotExist:
        to_article_data = migrate_article(to_cursor, from_article_data)
        migrate_versions(to_cursor, from_article_data, from_version_datas, to_article_data)
    else:
        migrate_non_overlapping_article_versions(to_cursor, from_article_data, extant_to_article, from_version_datas)


def migrate_article(to_cursor, from_article_data):
    article_query = """
        insert into Articles (url, initial_date, last_update, last_check, git_dir, is_migrated) 
        values (%(url)s, %(initial_date)s, %(last_update)s, %(last_check)s, %(git_dir)s, TRUE)
    """
    article_data = dict(
        url=from_article_data.url,
        initial_date=fix_date(from_article_data.initial_date),
        last_update=fix_date(from_article_data.last_update),
        last_check=fix_date(from_article_data.last_check),
        git_dir=os.path.join(MIGRATED_VERSIONS_GIT_SUBDIR, from_article_data.git_dir),
    )
    execute_query(to_cursor, article_query, article_data)

    execute_query(to_cursor, 'select last_insert_id() as article_id')
    article_id = to_cursor.fetchone()['article_id']

    return Bag(id=article_id, url=from_article_data.url)


def migrate_versions(to_cursor, from_article_data, from_version_datas, to_article_data):
    git_dir = os.path.join(os.environ['ARTICLES_DIR_ROOT'], MIGRATED_VERSIONS_GIT_SUBDIR, from_article_data.git_dir)
    if not os.path.exists(git_dir):
        logger.debug('Initializing Git repo at: %s', git_dir)
        make_git_repo(git_dir)

    for from_version_data in from_version_datas:

        migrate_version_text = get_version_text(
            from_version_data,
            # Use the migrate article in case the URLs differ by scheme
            article_url_to_filename(from_article_data.url),
            os.path.join(MIGRATION_VERSIONS_DIR, from_article_data.git_dir)
        )

        version_path = os.path.join(git_dir, article_url_to_filename(to_article_data.url))
        logger.debug('Writing version %s file to %s', from_version_data.id, version_path)
        write_version_file(migrate_version_text, version_path)
        commit_version_file(version_path, git_dir, from_article_data.url, from_version_data.date)

        version_query = """
            insert into version (article_id, v, title, byline, date, boring, diff_json, is_migrated)
            values (%(article_id)s, %(v)s, %(title)s, %(byline)s, %(date)s, %(boring)s, %(diff_json)s, TRUE)
        """
        version_data = dict(
            article_id=to_article_data.id,
            v=from_version_data.v,
            title=from_version_data.title,
            byline=from_version_data.byline,
            date=fix_date(from_version_data.date),
            boring=from_version_data.boring,
            diff_json=from_version_data.diff_json,
        )
        execute_query(to_cursor, version_query, version_data)


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

    run_command(['git', 'init'], cwd=path)

    # Create a file so that there is something to commit
    initial_commit_file = os.path.join(path, 'initial-commit-file')
    open(initial_commit_file, 'w').close()

    configure_git(path)

    run_command(['git', 'add', initial_commit_file], cwd=path)
    run_command(['git', 'commit', '-m', 'Initial commit'], cwd=path)


def configure_git(git_dir):
    run_command(['git', 'config', 'user.email',
                             'migration@newsdiffs.org'], cwd=git_dir)
    run_command(['git', 'config', 'user.name',
                             'NewsDiffs Migration'], cwd=git_dir)


def run_command(*args, **kwargs):
    try:
        return subprocess.check_output(*args, **kwargs)
    except subprocess.CalledProcessError as ex:
        logger.warn(ex.output)
        raise


def write_version_file(file_text, path):
    dir_path = os.path.dirname(path)
    if not os.path.exists(dir_path):
        make_dirs(dir_path)
    with open(path, 'w') as dest:
        dest.write(file_text)
    # Write the files as world-readable to avoid permissions errors between
    # the web and scraper
    os.chmod(path, 0o777)


def commit_version_file(version_path, git_dir, url, date):
    command_parts = ['git', 'add', version_path]
    output = run_command(command_parts, cwd=git_dir)
    logger.info('%s: %s', ' '.join(command_parts), output)

    command_parts = ['git', 'commit', '-m', 'Migrating %s from %s' % (url, date)]
    try:
        output = run_command(command_parts, cwd=git_dir)
        logger.info('%s: %s', ' '.join(command_parts), output)
    except subprocess.CalledProcessError as ex:
        # If the file already had identical contents, don't worry.
        # Assume it was a previous run of the migration
        if 'nothing to commit, working tree clean' in ex.output:
            logger.info('%s: %s', ' '.join(command_parts), ex.output)
        else:
            raise


def migrate_non_overlapping_article_versions(
        to_cursor,
        from_article_data,
        extant_to_article,
        from_version_datas
):
    oldest_extant_version = get_oldest_extant_version(extant_to_article)
    last_non_overlapping_version_index = -1
    # The versions should be sorted by date
    for i, version in enumerate(from_version_datas):
        if version.date > oldest_extant_version.date:
            last_non_overlapping_version_index = i - 1
            break
    if last_non_overlapping_version_index > -1:
        last_non_overlapping_version = from_version_datas[last_non_overlapping_version_index]
        migrate_version_text = get_version_text(
            last_non_overlapping_version,
            # Use the migrate article in case the URLs differ by scheme
            article_url_to_filename(from_article_data.url),
            os.path.join(MIGRATION_VERSIONS_DIR, from_article_data.git_dir)
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
                from_version_datas[:last_non_overlapping_version_index + 1]
            migrate_versions(to_cursor, from_article_data, non_overlapping_versions, extant_to_article)
    else:
        migrate_versions(to_cursor, from_article_data, from_version_datas, extant_to_article)


def get_version_text(version, filename, git_dir):
    revision = version.v + ':' + filename
    return run_command(['git', 'show', revision], cwd=git_dir)


def get_oldest_extant_version(extant_article):
    return models.Version.objects.filter(article_id=extant_article.id).order_by('date').first()


def get_migrate_cutoff(to_cursor):
    execute_query(to_cursor, 'select min(initial_date) as cutoff from Articles')
    return to_cursor.fetchone()['cutoff']
