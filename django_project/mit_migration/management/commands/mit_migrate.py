from datetime import datetime, timedelta
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

# If the migration fails in less than this amount of time, don't repeat it.
# It may indicate that some error is occurring that will just continue forever.
migrate_repeat_required_elapsed_time = timedelta(seconds=10)

# Put the migrated article versions into a separate directory so that Git
# operations don't conflict
MIGRATED_VERSIONS_GIT_SUBDIR = 'mit_migration'
# This is where to find the Git dirs of the articles versions to migrate
MIGRATION_VERSIONS_DIR = '/newsdiffs-efs/mit_migration/dump'

# Help keep the migration moving forward by not re-reading migrated articles
last_migrated_article_id = -1


class Command(BaseCommand):
    # option_list = BaseCommand.option_list + (
    #     make_option('--all',
    #                 action='store_true',
    #                 default=False,
    #                 help='Update _all_ stored articles'),
    # )
    help = textwrap.dedent('''Migrate data from MIT data dump to AWS''').strip()

    def handle(self, *args, **options):
        migrate_until_done()


def migrate_until_done():
    is_done = False
    attempt_number = 0
    last_exception_datetime = datetime.now()
    while not is_done:
        attempt_number += 1
        logger.info('Beginning migration attempt number %s', attempt_number)
        try:
            is_done = connect_and_migrate()
        except Exception as ex:
            logger.warn('Caught exception while migrating until done')
            logger.exception(ex)
            elapsed_time = datetime.now() - last_exception_datetime
            if elapsed_time < migrate_repeat_required_elapsed_time:
                raise Exception('last exception was only %s ago, which is less '
                             'than the required %s.  Exiting' % (elapsed_time,
                                                                 migrate_repeat_required_elapsed_time))
            last_exception_datetime = datetime.now()


def connect_and_migrate():
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
        return True
    finally:
        if from_cursor:
            try:
                from_cursor.close()
            except:
                pass
        if hasattr(from_connection, 'close'):
            try:
                from_connection.close()
            except:
                pass
        if to_cursor:
            try:
                to_cursor.close()
            except:
                pass
        if hasattr(to_connection, 'close'):
            try:
                to_connection.close()
            except:
                pass


def migrate(from_cursor, to_connection, to_cursor):
    global last_migrated_article_id

    cutoff = get_migrate_cutoff(to_cursor)
    logger.info('Using cutoff: %s', cutoff)

    if last_migrated_article_id == -1:
        last_migrated_article_id = get_last_migrated_article_id(to_cursor)
    logger.info('Using last_migrated_article_id: %s', last_migrated_article_id)

    current_article = None
    current_versions = []
    row_count = query_migration_article_versions_count(from_cursor, cutoff)
    logger.info('Starting migrating %s article/version rows', row_count)
    query_migration_article_versions(from_cursor, cutoff)
    curr_row_number = 0
    for row in from_cursor:
        curr_row_number += 1
        logger.debug('Processing article/version row %s / %s (%.2f%%)', curr_row_number, row_count, 100. * curr_row_number / row_count)
        if not current_article:
            current_article = make_article(row)
            logger.debug('Started reading article %s', current_article.id)

            current_versions.append(make_version(row))
            logger.debug('Read article %s version %s', current_article.id, current_versions[-1].id)
        elif current_article.id == row['article_id']:
            current_versions.append(make_version(row))
            logger.debug('Read article %s version %s', current_article.id, current_versions[-1].id)
        else:
            logger.debug('Processing article %s with %s versions', current_article.id, len(current_versions))
            process_article_versions(to_cursor, current_article,
                                     current_versions)
            last_migrated_article_id = current_article.id
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


def get_last_migrated_article_id(to_cursor):
    execute_query(to_cursor, 'select max(migrated_article_id) as max_id from Articles')
    row = to_cursor.fetchone()
    to_cursor.fetchall()
    max_id = row['max_id']
    if max_id is None:
        max_id = -1
    return max_id


def query_migration_article_versions_count(from_cursor, cutoff):
    article_query = """
        select count(*) as count
        from Articles a join version v on a.id = v.article_id
          where 
                a.initial_date < %(cutoff)s
            and a.id >= %(last_migrated_article_id)s
    """
    execute_query(from_cursor, article_query, dict(cutoff=cutoff, last_migrated_article_id=last_migrated_article_id))
    count = from_cursor.fetchone()['count']
    # Try to avoid MySQL error 2014 "Commands out of sync; you can't run this command now"
    from_cursor.fetchall()
    return count


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
          where 
                a.initial_date < %(cutoff)s
            -- use >= so that we retry all the versions, in case only some versions were migrated
            and a.id >= %(last_migrated_article_id)s
        order by a.id, v.date
    """
    execute_query(from_cursor, article_query, dict(cutoff=cutoff, last_migrated_article_id=last_migrated_article_id))


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


def eastern_to_utc(naive_eastern_datetime, entity_type, entity_id, time_description):
    try:
        eastern_aware_datetime = \
            eastern_timezone.localize(naive_eastern_datetime, is_dst=None)
    except pytz.exceptions.AmbiguousTimeError:
        # Since the original times were stored as Eastern w/o timezone info,
        # we can't tell what to do with ambiguous times.  Just assume they are
        # DST for simplicity.
        # See http://pytz.sourceforge.net/#problems-with-localtime
        logger.warn('Ambiguous time for %s %s %s: %s', entity_type, entity_id, time_description, naive_eastern_datetime)
        eastern_aware_datetime = \
            eastern_timezone.localize(naive_eastern_datetime, is_dst=True)
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
    migrated_article_data = get_migrated_article_data(to_cursor, from_article_data)
    if not migrated_article_data:
        article_query = """
            insert into Articles (url, initial_date, last_update, last_check, git_dir, is_migrated, migrated_article_id) 
            values (%(url)s, %(initial_date)s, %(last_update)s, %(last_check)s, %(git_dir)s, TRUE, %(migrated_article_id)s)
        """
        article_data = dict(
            url=from_article_data.url,
            initial_date=eastern_to_utc(from_article_data.initial_date, 'article', from_article_data.id, 'initial_date'),
            last_update=eastern_to_utc(from_article_data.last_update, 'article', from_article_data.id, 'last_update'),
            last_check=eastern_to_utc(from_article_data.last_check, 'article', from_article_data.id, 'last_check'),
            git_dir=os.path.join(MIGRATED_VERSIONS_GIT_SUBDIR, from_article_data.git_dir),
            migrated_article_id=from_article_data.id,
        )
        execute_query(to_cursor, article_query, article_data)

        execute_query(to_cursor, 'select last_insert_id() as article_id')
        article_id = to_cursor.fetchone()['article_id']
        # Try to avoid MySQL error 2014 "Commands out of sync; you can't run this command now"
        to_cursor.fetchall()

        # Can we just use this property?
        logger.debug('last_insert_id: %s; to_cursor.lastrowid: %s', article_id, to_cursor.lastrowid)

        migrated_article_data = Bag(id=article_id, url=from_article_data.url)
    return migrated_article_data


def get_migrated_article_data(to_cursor, from_article_data):
    query = """
        select id, url from Articles where migrated_article_id = %(id)s
    """
    execute_query(to_cursor, query, dict(id=from_article_data.id))
    row = to_cursor.fetchone()
    to_cursor.fetchall()
    if not row:
        return None
    return Bag(id=row['id'], url=row['url'])


def migrate_versions(to_cursor, from_article_data, from_version_datas, to_article_data):
    git_dir = os.path.join(os.environ['ARTICLES_DIR_ROOT'], MIGRATED_VERSIONS_GIT_SUBDIR, from_article_data.git_dir)
    if not os.path.exists(git_dir):
        logger.debug('Initializing Git repo at: %s', git_dir)
        make_git_repo(git_dir)

    for from_version_data in from_version_datas:

        migrate_version_text = get_version_text(
            from_version_data,
            article_url_to_filename(from_article_data.url),
            os.path.join(MIGRATION_VERSIONS_DIR, from_article_data.git_dir)
        )

        filename = article_url_to_filename(to_article_data.url)
        version_path = os.path.join(git_dir, filename)
        logger.debug('Writing version %s file to %s', from_version_data.id, version_path)
        write_version_file(migrate_version_text, version_path)
        did_change = commit_version_file(version_path, git_dir, from_article_data.url, from_version_data.date)

        if did_change:
            if has_version_id_been_migrated(to_cursor, from_version_data.id):
                # Hopefully this doesn't happen if we always process the versions in the same order
                raise Exception('version %s changed in Git but is already migrated (%s / %s) ' % (from_version_data.id, from_article_data.url, to_article_data.url))
                # commit_hash = get_most_recent_commit_hash_that_modified_file(git_dir, filename)

            commit_hash = get_commit_hash(git_dir)

            version_query = """
                insert into version (article_id, v, title, byline, date, boring, diff_json, is_migrated)
                values (%(article_id)s, %(v)s, %(title)s, %(byline)s, %(date)s, %(boring)s, %(diff_json)s, TRUE)
            """
            version_data = dict(
                article_id=to_article_data.id,
                v=commit_hash,
                migrated_commit_hash=from_version_data.v,
                migrated_version_id=from_version_data.id,
                title=from_version_data.title,
                byline=from_version_data.byline,
                date=eastern_to_utc(from_version_data.date, 'version', from_version_data.id, 'date'),
                boring=from_version_data.boring,
                diff_json=from_version_data.diff_json,
            )
            execute_query(to_cursor, version_query, version_data)
        else:
            # Check if it has been migrated.  Hopefully it has
            if not has_version_id_been_migrated(to_cursor, from_version_data.id):
                raise Exception('version %s exists in Git but is not migrated (%s / %s) ' % (from_version_data.id, from_article_data.url, to_article_data.url))


def get_most_recent_commit_hash_that_modified_file(git_dir, filename):
    command_parts = ['git', 'log', '-n', '1', '--pretty=format:%h', filename]
    return run_command(command_parts, cwd=git_dir)


def has_version_id_been_migrated(to_cursor, version_id):
    query = """
        select count(*) as count from version where migrated_version_id = %(version_id)s
    """
    execute_query(to_cursor, query, dict(version_id=version_id))
    count = to_cursor.fetchone()['count']
    to_cursor.fetchall()
    return count > 0


def get_commit_hash(git_dir):
    return run_command(['git', 'rev-list', 'HEAD', '-n1'], cwd=git_dir).strip()


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
    run_command(['git', 'config', 'user.email', 'migration@newsdiffs.org'],
                cwd=git_dir)
    run_command(['git', 'config', 'user.name', 'NewsDiffs Migration'],
                cwd=git_dir)


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
            return False
        else:
            raise
    else:
        return True


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
    return models.Version.objects.filter(article_id=extant_article.id).order_by('date')[0]


def get_migrate_cutoff(to_cursor):
    execute_query(to_cursor, 'select min(initial_date) as cutoff from Articles where not is_migrated')
    cutoff = to_cursor.fetchone()['cutoff']
    to_cursor.fetchall()
    return cutoff
