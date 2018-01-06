from datetime import datetime, timedelta
import errno
import logging
import re
import resource
import subprocess
import sys
import textwrap
import time
import os

from django.core.management.base import BaseCommand
from mem_top import mem_top
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
migrate_repeat_required_elapsed_time = timedelta(minutes=2)

# Put the migrated article versions into a separate directory so that Git
# operations don't conflict
MIGRATED_VERSIONS_GIT_SUBDIR = 'mit_migration'
# This is where to find the Git dirs of the articles versions to migrate
MIGRATION_VERSIONS_DIR = '/newsdiffs-efs/mit_migration/dump'

# Help keep the migration moving forward by not re-reading migrated articles
last_migrated_article_id = -1

max_git_attempts = 5
git_lock_error_sleep_seconds = 5

create_mit_migrate_issues = """
create table mit_migrate_issues (
    issue_id
  , when
  , message
  , from_article_id
  , to_article_id
  , from_version_id
  , to_version_id
)
"""


class Command(BaseCommand):
    # option_list = BaseCommand.option_list + (
    #     make_option('--all',
    #                 action='store_true',
    #                 default=False,
    #                 help='Update _all_ stored articles'),
    # )
    help = textwrap.dedent('''Migrate data from MIT data dump to AWS''').strip()

    def handle(self, *args, **options):
        set_limits()
        migrate_until_done()


class MigrationException(Exception):
    pass


def set_limits():
    # Limit the process to 1GiB of memory.  Some of Popens are growing the
    # RAM usage unbounded and I don't know why
    one_mibibyte = 1024
    # resource.setrlimit(resource.RLIMIT_RSS, (one_mibibyte, one_mibibyte))
    # Heap size
    # resource.setrlimit(resource.RLIMIT_DATA, (one_mibibyte, one_mibibyte))
    # Stack size
    # resource.setrlimit(resource.RLIMIT_STACK, (one_mibibyte, one_mibibyte))


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
            if isinstance(ex, MigrationException):
                logger.exception('Caught MigrationException, aborting')
                raise
            logger.warn('Caught exception while migrating until done')
            logger.exception(ex)
            elapsed_time = datetime.now() - last_exception_datetime
            if elapsed_time < migrate_repeat_required_elapsed_time:
                logger.error('last exception was only %s ago, which is less '
                             'than the required %s.  Exiting', elapsed_time,
                             migrate_repeat_required_elapsed_time)
                sys.exit(1)
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
    curr_row_number = 0
    while row_count > 0:
        logger.info('Starting migrating %s article/version rows', row_count)
        query_migration_article_versions(from_cursor, cutoff)
        for row in from_cursor:
            curr_row_number += 1
            logger.debug('Processing article/version row %s / %s (%.2f%%)', curr_row_number, row_count, 100. * curr_row_number / row_count)
            if not current_article:
                current_article = make_article(row)
                logger.debug('Started reading first article %s', current_article.id)

                current_versions.append(make_version(row))
                logger.debug('Read first article %s version %s', current_article.id, current_versions[-1].id)
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

                previous_article_git_dir = current_article.git_dir

                current_article = make_article(row)
                logger.debug('Started reading article %s', current_article.id)

                # logging.debug(mem_top(width=200))
                if curr_row_number % 20 == 0 or current_article.git_dir != previous_article_git_dir:
                    git_gc(previous_article_git_dir)

                current_versions[:] = [make_version(row)]
                logger.debug('Read article %s version %s', current_article.id, current_versions[-1].id)
            logger.debug('Reading next row...')

        logger.debug('Processing article %s with %s versions', current_article.id, len(current_versions))
        process_article_versions(to_cursor, current_article, current_versions)
        last_migrated_article_id = current_article.id
        logger.debug('Processed article %s', current_article.id)

        logger.debug('Committing article %s', current_article.id)
        to_connection.commit()
        logger.debug('Committed article %s', current_article.id)

        row_count = query_migration_article_versions_count(from_cursor, cutoff)


def git_gc(git_dir):
    git_dir = os.path.join(os.environ['ARTICLES_DIR_ROOT'], MIGRATED_VERSIONS_GIT_SUBDIR, git_dir)
    logger.debug('starting git garbage collection in %s', git_dir)
    # without --quiet, there is a lot of dynamic (curses?) output, and I think it's causing a hang
    # I think this is relevant: https://thraxil.org/users/anders/posts/2008/03/13/Subprocess-Hanging-PIPE-is-your-enemy/
    output = run_git_command(['git', 'gc', '--quiet'], cwd=git_dir)
    logger.debug('done with git garbage collection: %s', output)


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
        limit 1000
    """
    execute_query(from_cursor, article_query, dict(cutoff=cutoff, last_migrated_article_id=last_migrated_article_id))


def execute_query(cursor, query, *args):
    logger.debug('Executing query: %s', query)
    if len(args) > 0:
        params = args[0]
        logger.debug('Query params: %s', params)
    return cursor.execute(query, *args)


def make_article(row):
    old_git_dir = row['git_dir']
    initial_date = row['initial_date']

    # Initially all articles where stored in a single Git repo.  For performance
    # reasons, they were later split into repos by month.  But the initial
    # articles were put into a repo under 'old'.  This repo is large and
    # git gc is failing in it during migration.  So let's break up the old repo
    # into months as part of the migration
    if old_git_dir == 'old':
        git_dir = initial_date.strftime('%Y-%m')
    else:
        git_dir = old_git_dir
    return Bag(
        id=row['article_id'],
        url=row['url'],
        initial_date=initial_date,
        last_update=row['last_update'],
        last_check=row['last_check'],
        git_dir=git_dir,
        old_git_dir=old_git_dir,
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
        logger.debug('looking for extant article')
        extant_to_article = models.Article.objects.get(url=from_article_data.url)
    except models.Article.DoesNotExist:
        logger.debug('no extant article')

        logger.debug('migrating article')
        to_article_data = migrate_article(to_cursor, from_article_data)
        logger.debug('done migrating article')

        logger.debug('migrating versions')
        migrate_versions(to_cursor, from_article_data, from_version_datas, to_article_data)
        logger.debug('done migrating versions')
    else:
        logger.debug('found extant article')

        logger.debug('migrating non-overlapping versions')
        migrate_non_overlapping_article_versions(to_cursor, from_article_data, extant_to_article, from_version_datas)
        logger.debug('done migrating non-overlapping versions')


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

        article_id = to_cursor.lastrowid
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

        migrate_version_text = try_get_version_text(
            from_version_data,
            article_url_to_filename(from_article_data.url),
            os.path.join(MIGRATION_VERSIONS_DIR, from_article_data.old_git_dir)
        )

        if migrate_version_text is None:
            logger.warn('Could not get version text for %s.  Skipping migrating this version', (from_version_data,))
            continue

        filename = article_url_to_filename(to_article_data.url)
        version_path = os.path.join(git_dir, filename)
        logger.debug('Writing version %s file to %s', from_version_data.id, version_path)
        write_version_file(migrate_version_text, version_path)
        did_change = commit_version_file(version_path, git_dir, from_article_data.url, from_version_data.date)

        if did_change:
            # Check if the version has already been migrated
            migrated_commit_hash = get_migrated_commit_hash(to_cursor, from_version_data.id)
            if migrated_commit_hash:
                # Check that the git commit from the migrated version exists
                if not git_commit_exists(migrated_commit_hash, git_dir):
                    raise MigrationException("version %s has been migrated, but its Git commit %s doesn't exist" % (from_version_data.id, migrated_commit_hash))
                logger.warn("encountered file change while trying to migrate "
                            "version %s, which has been migrated, but since its Git "
                            "commit already %s exists we are resetting the change and "
                            "continuing" % (from_version_data.id, migrated_commit_hash))
                run_git_command(['git', 'reset', '--hard'], cwd=git_dir)
            else:
                commit_hash = get_commit_hash(git_dir)
                migrate_version_with_commit_hash(to_cursor, from_version_data, to_article_data, commit_hash)
        else:
            # If the file did not change, check if the version has been migrated
            migrated_commit_hash = get_migrated_commit_hash(to_cursor, from_version_data.id)
            if migrated_commit_hash:
                logger.info("encountered version %s where file didn't change and it is already migrated. continuing." % (from_version_data.id,))
            else:
                # If the file didn't change and the version hasn't been migrated
                # then maybe we can emulate the change by finding the commit
                # when the file last changed
                previous_commit_hash = get_most_recent_commit_hash_that_modified_file(git_dir, filename)
                version_migrating_previous_commit_hash = get_version_for_commit_hash(to_cursor, previous_commit_hash)
                if version_migrating_previous_commit_hash:
                    logger.warn("while trying to migrate version %s, found that "
                                "it's file contents are already on disk AND the "
                                "commit that did so %s is already migrated as "
                                "version %s.  Skipping migrating this version" % (
                                    from_version_data.id, previous_commit_hash,
                                    version_migrating_previous_commit_hash.id))
                else:
                    # Create version using the previous commit hash
                    migrate_version_with_commit_hash(to_cursor, from_version_data, to_article_data, previous_commit_hash)


def git_commit_exists(commit_hash, git_dir):
    try:
        # https://stackoverflow.com/a/31780867/39396
        run_git_command(['git', 'cat-file', '-e', commit_hash + '^{commit}'], cwd=git_dir)
    except subprocess.CalledProcessError:
        return False
    else:
        return True


def get_version_for_commit_hash(to_cursor, git_commit):
    query = """
        select *, id as version_id from version where v = %(git_commit)s
    """
    execute_query(to_cursor, query, dict(git_commit=git_commit))
    version_rows = to_cursor.fetchall()
    if len(version_rows) > 1:
        raise MigrationException('git commit %s has been migrated %s times: %s' % (git_commit, len(version_rows), version_rows))
    if len(version_rows) < 1:
        return None
    row = version_rows[0]
    return make_version(row)


def migrate_version_with_commit_hash(to_cursor, from_version_data, to_article_data, commit_hash):
    version_query = """
        insert into version (article_id, v, title, byline, date, boring, diff_json, is_migrated, migrated_commit_hash, migrated_version_id)
        values (%(article_id)s, %(v)s, %(title)s, %(byline)s, %(date)s, %(boring)s, %(diff_json)s, TRUE, %(migrated_commit_hash)s, %(migrated_version_id)s)
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


def get_most_recent_commit_hash_that_modified_file(git_dir, filename):
    command_parts = ['git', 'log', '-n', '1', '--pretty=format:%H', filename]
    return run_git_command(command_parts, cwd=git_dir)


def get_migrated_commit_hash(to_cursor, version_id):
    query = """
        select id, v from version where migrated_version_id = %(version_id)s
    """
    execute_query(to_cursor, query, dict(version_id=version_id))
    commit_hashes = to_cursor.fetchall()
    if len(commit_hashes) > 1:
        raise MigrationException('version ID has been migrated %s times: %s' % (len(commit_hashes), commit_hashes))
    if len(commit_hashes) < 1:
        return None
    return commit_hashes[0]['v']


def get_commit_hash(git_dir):
    return run_git_command(['git', 'rev-list', 'HEAD', '-n1'], cwd=git_dir).strip()


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

    run_git_command(['git', 'init'], cwd=path)

    # Create a file so that there is something to commit
    initial_commit_file = os.path.join(path, 'initial-commit-file')
    open(initial_commit_file, 'w').close()

    configure_git(path)

    run_git_command(['git', 'add', initial_commit_file], cwd=path)
    run_git_command(['git', 'commit', '-m', 'Initial commit'], cwd=path)


def configure_git(git_dir):
    run_git_command(['git', 'config', 'user.email', 'migration@newsdiffs.org'],
                    cwd=git_dir)
    run_git_command(['git', 'config', 'user.name', 'NewsDiffs Migration'],
                    cwd=git_dir)


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
    output = run_git_command(command_parts, cwd=git_dir)
    logger.info('%s: %s', ' '.join(command_parts), output)

    command_parts = ['git', 'commit', '-m', 'Migrating %s from %s' % (url, date)]
    try:
        output = run_git_command(command_parts, cwd=git_dir)
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
    earliest_extant_version = get_earliest_extant_version(extant_to_article)
    if not earliest_extant_version:
        logger.info('despite extant article new article %s for old article ID %s, no extant versions. migrating all versions', extant_to_article.id, from_article_data.id)
        migrate_versions(to_cursor, from_article_data, from_version_datas, extant_to_article)
    else:
        last_non_overlapping_version_index = -1

        # (The version_datas must be sorted by date)
        for i, version in reversed(list(enumerate(from_version_datas))):
            if version.date < earliest_extant_version.date:
                last_non_overlapping_version_index = i
                break
        if last_non_overlapping_version_index > -1:

            # Get the latest version having text
            migrate_version_text = None
            while migrate_version_text is None and last_non_overlapping_version_index > -1:
                last_non_overlapping_version = from_version_datas[last_non_overlapping_version_index]
                migrate_version_text = try_get_version_text(
                    last_non_overlapping_version,
                    # Use the migrate article in case the URLs differ by scheme
                    article_url_to_filename(from_article_data.url),
                    os.path.join(MIGRATION_VERSIONS_DIR, from_article_data.old_git_dir)
                )
                if migrate_version_text is None:
                    last_non_overlapping_version_index -= 1

            if migrate_version_text is None:
                logger.warn('Despite version overlap time-wise, we were unable '
                            'to get previous version text for any version of'
                            ' old article %s (new article %s).  Migrating all versions',
                            from_article_data.id, extant_to_article.id)
                migrate_versions(to_cursor, from_article_data, from_version_datas, extant_to_article)
            else:
                extant_version_text = earliest_extant_version.text()
                # We have text for a previous version.  Now try and find the first
                # previous version where the text differs.  If no previous versions
                # have differing text, migrate the first version only
                while (
                    migrate_version_text == extant_version_text or
                    migrate_version_text is None
                ) and last_non_overlapping_version_index > -1:
                    last_non_overlapping_version_index -= 1
                    if last_non_overlapping_version_index > -1:
                        last_non_overlapping_version = from_version_datas[last_non_overlapping_version_index]
                        migrate_version_text = try_get_version_text(
                            last_non_overlapping_version,
                            # Use the migrate article in case the URLs differ by scheme
                            article_url_to_filename(from_article_data.url),
                            os.path.join(MIGRATION_VERSIONS_DIR, from_article_data.old_git_dir)
                        )
                if migrate_version_text == extant_version_text or last_non_overlapping_version_index == -1:
                    first_version = from_version_datas[0]
                    logger.info('No previous versions with text differing from '
                                'extant versions was found.  Migrating first version %s '
                                'only for old article ID %s (new article ID %s).',
                                first_version.id, from_article_data.id, extant_to_article.id)
                    migrate_versions(to_cursor, from_article_data, [first_version], extant_to_article)
                else:
                    non_overlapping_versions = \
                        from_version_datas[:last_non_overlapping_version_index + 1]
                    logger.info('Found %s non-overlapping versions (out of %s total).  Beginning migration.', (len(non_overlapping_versions), len(from_version_datas)))
                    migrate_versions(to_cursor, from_article_data, non_overlapping_versions, extant_to_article)

        else:
            logger.info('No overlap with extant versions, migrating all versions')
            migrate_versions(to_cursor, from_article_data, from_version_datas, extant_to_article)


def try_get_version_text(version, filename, git_dir):
    try:
        return get_version_text(version, filename, git_dir)
    except subprocess.CalledProcessError as ex:
        if 'does not exist' in ex.output:
            return None
        raise


def get_version_text(version, filename, git_dir):
    revision = version.v + ':' + filename
    return run_git_command(['git', 'show', revision], cwd=git_dir)


def get_earliest_extant_version(extant_article):
    extant_versions = models.Version.objects.filter(article_id=extant_article.id).order_by('date')
    if len(extant_versions) > 0:
        return extant_versions[0]
    return None


def get_migrate_cutoff(to_cursor):
    execute_query(to_cursor, 'select min(initial_date) as cutoff from Articles where not is_migrated')
    cutoff = to_cursor.fetchone()['cutoff']
    to_cursor.fetchall()
    return cutoff


def run_git_command(*args, **kwargs):
    attempt_count = 0
    while attempt_count < max_git_attempts:
        try:
            return run_command(*args, **kwargs)
        except subprocess.CalledProcessError as ex:
            # For some unknown reason occasionally there is a git index file hanging
            # around. Try to wait for it to go away.
            is_git_index_error = 'Another git process seems to be running in this repository' in ex.output
            if is_git_index_error:
                logger.info('Reporting Git lock conflict information')
                report_git_lock_conflict(ex.output)
                attempt_count += 1
                if attempt_count < max_git_attempts:
                    logger.debug('Git lock error during attempt number %s.  Sleeping %s seconds', attempt_count, git_lock_error_sleep_seconds)
                    time.sleep(git_lock_error_sleep_seconds)
                else:
                    logger.debug('After %s attempts, deleting git index file and retrying one last time.', attempt_count)
                    delete_git_index_files(kwargs['cwd'])
                    return run_command(*args, **kwargs)
            else:
                raise


def delete_git_index_files(git_dir):
    git_internal_dir = os.path.join(git_dir, '.git')
    git_index_lock_file_path = os.path.join(git_internal_dir, 'index.lock')
    run_command(['rm', '-f', git_index_lock_file_path])

    git_refs_dir = os.path.join(git_internal_dir, 'refs')
    git_master_ref_lock_file_path = os.path.join(git_refs_dir, 'heads', 'master.lock')
    run_command(['rm', '-f', git_master_ref_lock_file_path])

    # run_command(['find', git_refs_dir, '-type', 'f', '-name', '*.lock', '-delete'])


def report_git_lock_conflict(err_output):
    match = re.search(r"Unable to create '(.*)': File exists", err_output)
    if match:
        file_path = match.group(1)

        fuser_output = run_command(['fuser', file_path])
        pid_matches = re.findall('.*: (\d+).?', fuser_output)
        pids = map(lambda m: m.group(1), pid_matches)

        ps_output = run_command(['ps', ' '.join(pids)])

        logger.warn('Processes using %s:', file_path)
        logger.warn(ps_output)
    else:
        logger.warn('No file path match found.')



def run_command(*args, **kwargs):
    command = args[0]
    command_str = command if isinstance(command, basestring) else ' '.join(command)
    cwd = kwargs.get('cwd', os.getcwd())
    logger.debug('Running %s in %s' % (command_str, cwd))
    try:
        return subprocess.check_output(*args, stderr=subprocess.STDOUT, **kwargs)
    except subprocess.CalledProcessError as ex:
        logger.warn(ex.output)
        raise
