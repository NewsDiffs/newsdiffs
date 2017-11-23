#!/usr/bin/python

from datetime import datetime, timedelta
import errno
import httplib
import logging
import os
import subprocess
import sys
import textwrap
import time
import traceback
import urllib2

from django.core.management.base import BaseCommand
from django.db.models import Q
from optparse import make_option

from frontend import models
from scraper import parsers
from scraper import diff_match_patch
from scraper.parsers.baseparser import canonicalize
from util import url_util

GIT_PROGRAM = 'git'

logger = logging.getLogger(__name__)


class IndexLockError(OSError):
    pass


class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--all',
                    action='store_true',
                    default=False,
                    help='Update _all_ stored articles'),
    )
    help = textwrap.dedent('''Scrape websites.

    By default, scan front pages for new articles, and scan
    existing and new articles to archive their current contents.
    
    Articles that haven't changed in a while are skipped if we've
    scanned them recently, unless --all is passed.
    ''').strip()

    def handle(self, *args, **options):
        articles_dir_root = models.ARTICLES_DIR_ROOT
        logger.info('Starting scraping into %s', articles_dir_root)

        logger.debug('Beginning to clean all Git repos')
        for repo in all_git_repos():
            logger.debug('About to clean Git repo %s', repo)
            cleanup_git_repo(repo)
        logger.debug('Done cleaning all Git repos')

        todays_repo = get_and_make_git_repo()
        logger.debug("Today's Git repo: %s", todays_repo)

        git_dir = os.path.join(articles_dir_root, todays_repo)
        configure_git(git_dir)

        logger.debug('Beginning updating articles')
        update_articles(todays_repo)
        logger.debug('Done updating articles')

        logger.debug('Beginning updating versions')
        update_versions(todays_repo, options['all'])
        logger.debug('Done updating versions')

        logger.info('Done scraping.')


def cleanup_git_repo(git_dir):
    #Remove index.lock if 5 minutes old
    for name in ['.git/index.lock', '.git/refs/heads/master.lock', '.git/gc.pid.lock']:
        fname = os.path.join(git_dir, name)
        try:
            stat = os.stat(fname)
        except OSError:
            return
        age = time.time() - stat.st_ctime
        if age > 60*5:
            os.remove(fname)


def configure_git(git_dir):
    subprocess.check_output([GIT_PROGRAM, 'config', 'user.email', 
                             'scraper@newsdiffs.org'], cwd=git_dir)
    subprocess.check_output([GIT_PROGRAM, 'config', 'user.name', 
                             'NewsDiffs Scraper'], cwd=git_dir)


def get_and_make_git_repo():
    result = time.strftime('%Y-%m', time.localtime())
    full_path = os.path.join(models.ARTICLES_DIR_ROOT, result)
    if not os.path.exists(full_path+'/.git'):
        logger.debug('Creating Git repo at: %s', full_path)
        make_new_git_repo(full_path)

        # Make directories world-readable to avoid permissions problems
        # between the scraper and web app
        os.chmod(full_path, 0o777)

    return result


def make_new_git_repo(full_dir):
    mkdir_p(full_dir)

    subprocess.check_output([GIT_PROGRAM, 'init'], cwd=full_dir)

    # Create a file so that there is something to commit
    initial_commit_file = os.path.join(full_dir, 'initial-commit-file')
    open(initial_commit_file, 'w').close()

    configure_git(full_dir)

    subprocess.check_output([GIT_PROGRAM, 'add', initial_commit_file],
                            cwd=full_dir)
    subprocess.check_output([GIT_PROGRAM, 'commit', '-m', 'Initial commit'],
                            cwd=full_dir)


def all_git_repos():
    import glob
    return glob.glob(os.path.join(models.ARTICLES_DIR_ROOT, '*'))


def run_git_command(command, git_dir, max_timeout=15):
    """Run a git command like ['show', filename] and return the output.

    First, wait up to max_timeout seconds for the index.lock file to go away.
    If the index.lock file remains, raise an IndexLockError.

    Still have a race condition if two programs run this at the same time.
    """
    end_time = time.time() + max_timeout
    delay = 0.1
    lock_file = os.path.join(git_dir, '.git/index.lock')
    while os.path.exists(lock_file):
        if time.time() < end_time - delay:
            time.sleep(delay)
        else:
            raise IndexLockError('Git index.lock file exists for %s seconds'
                                 % max_timeout)
    output = subprocess.check_output([GIT_PROGRAM] + command,
                                     cwd=git_dir,
                                     stderr=subprocess.STDOUT)
    return output


def get_all_article_urls():
    all_urls = set()
    for parser in parsers.parsers:
        logger.info('Extracting URLs from: %s' % parser.domains)
        urls = parser.feed_urls()
        all_urls = all_urls.union(map(canonicalize_url, urls))
    return all_urls


CHARSET_LIST = """
EUC-JP 
GB2312 
EUC-KR 
Big5 
SHIFT_JIS 
windows-1252
IBM855
IBM866
ISO-8859-2
ISO-8859-5
ISO-8859-7
KOI8-R
MacCyrillic
TIS-620
windows-1250
windows-1251
windows-1253
windows-1255
""".split()


def is_difference_boring(old, new):
    oldu = canonicalize(old.decode('utf8'))
    newu = canonicalize(new.decode('utf8'))

    def extra_canonical(s):
        """Ignore changes in whitespace or the date line"""
        # This is fragile: depending on the text looking a particular way!
        nondate_portion = s.split('\n', 1)[1]
        return nondate_portion.split()

    if extra_canonical(oldu) == extra_canonical(newu):
        return True

    # This seems kind of fragile.  Are we 100% sure that differences between
    # these encodings are unimportant?  Also, how does this relate to non-latin
    # text?
    for charset in CHARSET_LIST:
        try:
            if oldu.encode(charset) == new:
                logger.debug('Boring!')
                return True
        except UnicodeEncodeError:
            pass
    return False


def get_diff_info(old, new):
    dmp = diff_match_patch.diff_match_patch()
    dmp.Diff_Timeout = 3 # seconds; default of 1 is too little
    diff = dmp.diff_main(old, new)
    dmp.diff_cleanupSemantic(diff)
    chars_added = sum(len(text) for (sign, text) in diff if sign == 1)
    chars_removed = sum(len(text) for (sign, text) in diff if sign == -1)
    return dict(chars_added=chars_added, chars_removed=chars_removed)


def add_to_git_repo(data, filename, article):
    # Don't use full path because it can exceed the maximum filename length
    # full_path = os.path.join(models.ARTICLES_DIR_ROOT, filename)
    os.chdir(article.full_git_dir)
    article_dirname = os.path.dirname(filename)
    mkdir_p(article_dirname)

    # Make directories world-readable to avoid permissions problems
    # between the scraper and web app
    curr_dir = article.full_git_dir
    for part in article_dirname.split(os.path.sep):
        curr_dir = os.path.join(curr_dir, part)
        os.chmod(curr_dir, 0o777)

    is_boring = False
    diff_info = None

    try:
        previous = run_git_command(['show', 'HEAD:'+filename], article.full_git_dir)
    except subprocess.CalledProcessError as e:
        if (e.output.endswith("does not exist in 'HEAD'\n") or
                e.output.endswith("exists on disk, but not in 'HEAD'.\n")):
            already_exists = False
        else:
            raise
    else:
        already_exists = True

    with open(filename, 'w') as article_file:
        article_file.write(data)
    # Write the files as world-readable to avoid permissions errors between
    # the web and scraper
    os.chmod(filename, 0o777)

    if already_exists:
        if previous == data:
            logger.debug("Article %d data hasn't changed", article.id)
            return None, None, None

        # Now check how many times this same version has appeared before
        # my_hash = run_git_command(['hash-object', filename],
        #                           article.full_git_dir).strip()
        #
        # commits = [v.v for v in article.versions()]
        # if len(commits) > 2:
        #     logger.debug('Checking for duplicates among %s commits',
        #                  len(commits))
        #
        #     def get_hash(version):
        #         """Return the SHA1 hash of filename in a given version"""
        #         output = run_git_command(['ls-tree', '-r', version, filename],
        #                                  article.full_git_dir)
        #         return output.split()[2]
        #     # What's the difference between `hashes` and `commits`?
        #     # `hashes` is the file hash, `commits` are the commit hashes
        #     hashes = map(get_hash, commits)
        #
        #     number_equal = sum(1 for h in hashes if h == my_hash)
        #
        #     logger.debug('Got %s previous version files have an identical hash', number_equal)
        #
        #     # TODO if the version has reverted to a previous version, the
        #     # system might not show it...
        #     if number_equal >= 2:  # Refuse to list a version more than twice
        #
        #         # Overwrite the file
        #         run_git_command(['checkout', filename], article.full_git_dir)
        #         return None, None, None

        if is_difference_boring(previous, data):
            is_boring = True
        else:
            diff_info = get_diff_info(previous, data)

    run_git_command(['add', filename], article.full_git_dir)
    if not already_exists:
        commit_message = 'Adding file %s' % filename
    else:
        commit_message = 'Change to %s' % filename
    command_args = ['commit', filename, '-m', commit_message]
    logger.debug('Committing article %d version data to Git with command: %s',
                 article.id, ' '.join(command_args))
    run_git_command(command_args, article.full_git_dir)
    logger.debug('Done committing article %d version data to Git.', article.id)

    # Now figure out what the commit ID was.
    # I would like this to be "git rev-list HEAD -n1 filename"
    # unfortunately, this command is slow: it doesn't abort after the
    # first line is output.  Without filename, it does abort; therefore
    # we do this and hope no intervening commit occurs.
    # (looks like the slowness is fixed in git HEAD)
    commit_hash = run_git_command(['rev-list', 'HEAD', '-n1'], article.full_git_dir).strip()
    logger.debug('New article %d version commit name: %s', article.id,
                 commit_hash)
    return commit_hash, is_boring, diff_info


def load_article(url):
    try:
        parser_class = parsers.get_parser(url)
    except KeyError:
        domain = url_util.get_url_domain(url)
        logger.error('No parser configured for domain: %s', domain)
        return None

    logger.debug('Initializing parser for URL: %s', url)
    try:
        parser = parser_class(url)
    except (AttributeError, urllib2.HTTPError, httplib.HTTPException), ex:
        if isinstance(ex, urllib2.HTTPError) and ex.msg == 'Gone':
            logger.warn('Article missing')
            return None
        logger.exception(ex)
        return None
    if not parser.real_article:
        logger.debug('Not a real article')
        return None
    return parser


def update_article(article):
    parser = load_article(article.url)
    if parser is None:
        logger.debug('parser is None, cannot update article')
        return
    string_representation = unicode(parser).encode('utf8')
    commit_hash, is_boring, diff_info = \
        add_to_git_repo(string_representation, article.filename(), article)
    if commit_hash:
        version = models.Version(v=commit_hash,
                                 boring=is_boring,
                                 title=parser.title,
                                 byline=parser.byline,
                                 date=parser.parse_time,
                                 article=article,
                                 )
        version.diff_info = diff_info
        version.save()
        logger.debug('Saved new version %d', version.id)
        if not is_boring:
            article.last_update = parser.parse_time


def update_articles(todays_git_dir):
    all_urls = get_all_article_urls()
    logger.info('Processing %d found articles', len(all_urls))
    skipped_url_count = 0
    new_article_count = 0
    for i, url in enumerate(all_urls):
        if len(url) > models.Article._meta.get_field('url').max_length:
            skipped_url_count += 1
            logger.debug('Skipping URL because it is too long: %s', url)
            continue
        if not models.Article.objects.filter(url=url).count():
            new_article_count += 1
            logger.debug('Adding article %s', url)
            article = models.Article(url=url, git_dir=todays_git_dir)
            article.save()
    logger.info('Added %d new URLs', new_article_count)
    logger.info('Skipped %d URLs that were too long', skipped_url_count)


def get_update_delay(minutes_since_update):
    days_since_update = minutes_since_update // (24 * 60)
    if minutes_since_update < 60*3:
        return 15
    elif days_since_update < 1:
        return 60
    elif days_since_update < 7:
        return 180
    elif days_since_update < 30:
        return 60*24*3
    elif days_since_update < 360:
        return 60*24*30
    else:
        return 60*24*365*1e5  #ignore old articles


def update_versions(todays_repo, do_all=False):
    # For memory issues, restrict to the last year of articles
    threshold = datetime.utcnow() - timedelta(days=366)
    article_query = (
        models.Article.objects
            .exclude(git_dir='old')
            .filter(Q(last_update__gt=threshold) |
                    Q(initial_date__gt=threshold))
    )
    full_articles = list(article_query)

    def update_priority(article):
        if not article.last_check:
            return sys.float_info.max
        return (
            article.minutes_since_check() * 1. /
            get_update_delay(article.minutes_since_update())
        )
    if not do_all:
        filtered_articles = [a for a in full_articles if update_priority(a) > 1]
    else:
        filtered_articles = full_articles

    logger.info('%d out of %d articles passed the filter',
                len(filtered_articles), len(full_articles))

    sorted_articles = sorted(filtered_articles, key=update_priority,
                             reverse=True)

    # Do git gc at the beginning, so if we're falling behind and killed
    # it still happens and I don't run out of quota. =)
    logger.info('Starting Git garbage collection')
    try:
        output = run_git_command(['gc'], os.path.join(models.ARTICLES_DIR_ROOT,
                                                      todays_repo))
        if output:
            logger.debug('Git garbage collection output: %s', output)
    except subprocess.CalledProcessError as e:
        logger.error('Git garbage collection error: %s', e.output)
        raise
    logger.info('Finished Git garbage collection')

    for i, article in enumerate(sorted_articles):
        logger.debug('Checking article %d (%d/%d): %s', article.id, i + 1,
                     len(sorted_articles), article.url)
        if article.last_check:
            last_check = article.last_check.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            last_check_ago = datetime.utcnow() - article.last_check
            logger.debug('Article %d last checked %s (%s ago)', article.id,
                         last_check, last_check_ago)
        else:
            logger.debug('Article %d last checked never', article.id)
        if article.last_update:
            last_update = article.last_update.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            last_update_ago = datetime.utcnow() - article.last_update
            logger.debug('Article %d last updated %s (%s ago)', article.id,
                         last_update, last_update_ago)
        else:
            logger.debug('Article %d last updated never', article.id)

        article.last_check = datetime.utcnow()
        try:
            update_article(article)
        except Exception, e:
            if isinstance(e, subprocess.CalledProcessError):
                logger.error('CalledProcessError when updating %s', article.url)
                logger.error(repr(e.output))
            else:
                logger.error('Unknown exception when updating %s', article.url)

            logger.error(traceback.format_exc())
        article.save()


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST:
            pass
        else:
            raise


def canonicalize_url(url):
    return url_util.remove_parameters(url)
