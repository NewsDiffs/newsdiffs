#!/usr/bin/python

from datetime import datetime, timedelta
import errno
import httplib
import logging
import os
import subprocess
import textwrap
import time
import traceback
import urllib2

from django.core.management.base import BaseCommand
from django.db.models import Q
from optparse import make_option

from website.frontend import models
from website.scraper import parsers
from website.scraper import diff_match_patch
from website.scraper.parsers.baseparser import canonicalize

GIT_PROGRAM = 'git'

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    option_list = BaseCommand.option_list + (
        make_option('--update',
                    action='store_true',
                    default=False,
                    help='DEPRECATED; this is the default'),
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
        logger.info('Starting scraping')

        logger.debug('Beginning to clean all Git repos')
        for repo in all_git_repos():
            logger.debug('About to clean Git repo %s', repo)
            cleanup_git_repo(repo)
        logger.debug('Done cleaning all Git repos')

        todays_repo = get_and_make_git_repo()
        logger.debug("Today's Git repo: %s", todays_repo)

        git_dir = os.path.join(models.ARTICLES_DIR_ROOT, todays_repo)
        configure_git(git_dir)

        logger.debug('Beginning updating articles')
        update_articles(todays_repo)
        logger.debug('Done updating articles')

        logger.debug('Beginning updating versions')
        update_versions(todays_repo, options['all'])
        logger.debug('Done updating versions')

        logger.info('Done scraping.')

# Begin utility functions


def configure_git(git_dir):
    subprocess.check_output([GIT_PROGRAM, 'config', 'user.email', 
                             'scraper@newsdiffs.org'], cwd=git_dir)
    subprocess.check_output([GIT_PROGRAM, 'config', 'user.name', 
                             'NewsDiffs Scraper'], cwd=git_dir)


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST:
            pass
        else:
            raise


def canonicalize_url(url):
    return url.split('?')[0].split('#')[0].strip()


class IndexLockError(OSError):
    pass


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
    ans = set()
    for parser in parsers.parsers:
        logger.info('Looking up %s' % parser.domains)
        urls = parser.feed_urls()
        ans = ans.union(map(canonicalize_url, urls))
    return ans


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


def is_boring(old, new):
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
    chars_added   = sum(len(text) for (sign, text) in diff if sign == 1)
    chars_removed = sum(len(text) for (sign, text) in diff if sign == -1)
    return dict(chars_added=chars_added, chars_removed=chars_removed)


def add_to_git_repo(data, filename, article):
    start_time = time.time()

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

    boring = False
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
            logger.debug('Article matches current version in repo')
            return None, None, None

        #Now check how many times this same version has appeared before
        my_hash = run_git_command(['hash-object', filename],
                                  article.full_git_dir).strip()

        # This is going to look this up separately each time
        commits = [v.v for v in article.versions()]
        # Why > 2?  Why not > 1?
        if len(commits) > 2:
            logger.debug('Checking for duplicates among %s commits',
                         len(commits))
            def get_hash(version):
                """Return the SHA1 hash of filename in a given version"""
                output = run_git_command(['ls-tree', '-r', version, filename],
                                         article.full_git_dir)
                return output.split()[2]
            # What's the difference between `hashes` and `commits`?
            # `hashes` is the file hash, `commits` are the commit hashes
            hashes = map(get_hash, commits)

            number_equal = sum(1 for h in hashes if h == my_hash)

            logger.debug('Got %s previous version files have an identical hash', number_equal)

            # So if the version has reverted to a previous version, the system might not show it...
            if number_equal >= 2: #Refuse to list a version more than twice

                # Overwrite the file
                run_git_command(['checkout', filename], article.full_git_dir)
                return None, None, None

        if is_boring(previous, data):
            boring = True
        else:
            diff_info = get_diff_info(previous, data)

    run_git_command(['add', filename], article.full_git_dir)
    if not already_exists:
        commit_message = 'Adding file %s' % filename
    else:
        commit_message = 'Change to %s' % filename
    logger.debug('Running git commit... %s', time.time()-start_time)
    run_git_command(['commit', filename, '-m', commit_message],
                    article.full_git_dir)
    logger.debug('git revlist... %s', time.time()-start_time)

    # Now figure out what the commit ID was.
    # I would like this to be "git rev-list HEAD -n1 filename"
    # unfortunately, this command is slow: it doesn't abort after the
    # first line is output.  Without filename, it does abort; therefore
    # we do this and hope no intervening commit occurs.
    # (looks like the slowness is fixed in git HEAD)
    v = run_git_command(['rev-list', 'HEAD', '-n1'],
                        article.full_git_dir).strip()
    logger.debug('done %s', time.time()-start_time)
    return v, boring, diff_info


def load_article(url):
    try:
        parser = parsers.get_parser(url)
    except KeyError:
        logger.info('Unable to parse domain, skipping')
        return
    try:
        parsed_article = parser(url)
    except (AttributeError, urllib2.HTTPError, httplib.HTTPException), e:
        if isinstance(e, urllib2.HTTPError) and e.msg == 'Gone':
            return
        logger.error('Exception when parsing %s', url)
        logger.error(traceback.format_exc())
        logger.error('Continuing')
        return
    if not parsed_article.real_article:
        return
    return parsed_article


#Update url in git
#Return whether it changed
def update_article(article):
    parsed_article = load_article(article.url)
    if parsed_article is None:
        return
    to_store = unicode(parsed_article).encode('utf8')
    t = datetime.now()
    logger.debug('Article parsed; trying to store')
    v, boring, diff_info = add_to_git_repo(to_store,
                                           article.filename(),
                                           article)
    if v:
        logger.info('Modifying! new blob: %s', v)
        v_row = models.Version(v=v,
                               boring=boring,
                               title=parsed_article.title,
                               byline=parsed_article.byline,
                               date=t,
                               article=article,
                               )
        v_row.diff_info = diff_info
        v_row.save()
        if not boring:
            article.last_update = t
            article.save()


def update_articles(todays_git_dir):
    logger.info('Starting scraper; looking for new URLs')
    all_urls = get_all_article_urls()
    logger.info('Got all %s urls; storing to database' % len(all_urls))
    for i, url in enumerate(all_urls):
        logger.debug('Woo: %d/%d is %s' % (i+1, len(all_urls), url))
        # Looks like it skips URLs longer than 255?
        if len(url) > 255:  #Icky hack, but otherwise they're truncated in DB.
            continue
        # Is there an index on this column?
        if not models.Article.objects.filter(url=url).count():
            logger.debug('Adding Article {0}'.format(url))
            models.Article(url=url, git_dir=todays_git_dir).save()
    logger.info('Done storing to database')


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
    logger.info('Looking for articles to check')
    # For memory issues, restrict to the last year of articles
    threshold = datetime.now() - timedelta(days=366)
    article_query = models.Article.objects.exclude(git_dir='old').filter(Q(last_update__gt=threshold) |
                                                                         Q(initial_date__gt=threshold))
    articles = list(article_query)
    total_articles = len(articles)

    update_priority = lambda x: x.minutes_since_check() * 1. / get_update_delay(x.minutes_since_update())
    articles = sorted([a for a in articles if update_priority(a) > 1 or do_all],
                      key=update_priority, reverse=True)

    logger.info('Checking %s of %s articles', len(articles), total_articles)

    # Do git gc at the beginning, so if we're falling behind and killed
    # it still happens and I don't run out of quota. =)
    logger.info('Starting with gc:')
    try:
        output = run_git_command(['gc'], os.path.join(models.ARTICLES_DIR_ROOT, todays_repo))
        logger.debug(output)
    except subprocess.CalledProcessError as e:
        logger.error('Error on initial gc!  Output:')
        logging.error(e.output)
        raise

    logger.info('Done with gc!')

    for i, article in enumerate(articles):
        logger.debug('Woo: %s %s %s (%s/%s)',
                     article.minutes_since_update(),
                     article.minutes_since_check(),
                     update_priority(article), i+1, len(articles))
        delay = get_update_delay(article.minutes_since_update())
        # isn't this inherent in update_priority being > 1 above?
        if article.minutes_since_check() < delay and not do_all:
            continue
        logger.info('Considering %s', article.url)

        article.last_check = datetime.now()
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
        #logger.info('Ending with gc:')
        #run_git_command(['gc'])


#Remove index.lock if 5 minutes old
def cleanup_git_repo(git_dir):
    for name in ['.git/index.lock', '.git/refs/heads/master.lock', '.git/gc.pid.lock']:
        fname = os.path.join(git_dir, name)
        try:
            stat = os.stat(fname)
        except OSError:
            return
        age = time.time() - stat.st_ctime
        if age > 60*5:
            os.remove(fname)
