import cookielib
from datetime import datetime
import logging
import re
import socket
import ssl
import sys
import time
import urllib2

import BeautifulSoup as bs3
import bs4

from util import url_util

logger = logging.getLogger(__name__)

# Utility functions


def grab_url(url, max_depth=5, opener=None):
    timeout = 5
    if opener is None:
        cj = cookielib.CookieJar()
        opener = urllib2.build_opener(urllib2.HTTPCookieProcessor(cj))
    retry = False
    try:
        text = opener.open(url, timeout=timeout).read()
        if '<title>NY Times Advertisement</title>' in text:
            retry = True
    except socket.timeout:
        logger.warn('Timed out while requesting {0} (timeout: {1})'.format(url, timeout))
        retry = True
    except ssl.SSLError as ex:
        if ex.message == 'The read operation timed out':
            retry = True
        else:
            raise
    except urllib2.HTTPError as ex:
        # Service unavailable
        if ex.code == 503:
            retry = True
        else:
            raise
    if retry:
        if max_depth == 0:
            raise Exception('Too many attempts to download %s' % url)
        time.sleep(0.5)
        return grab_url(url, max_depth-1, opener)
    return text


# Begin hot patch for https://bugs.launchpad.net/bugs/788986
# Ick.
def bs_fixed_getText(self, separator=u""):
    bsmod = sys.modules[bs3.BeautifulSoup.__module__]
    if not len(self.contents):
        return u""
    stopNode = self._lastRecursiveChild().next
    strings = []
    current = self.contents[0]
    while current is not stopNode:
        if isinstance(current, bsmod.NavigableString):
            strings.append(current)
        current = current.next
    return separator.join(strings)
sys.modules[bs3.BeautifulSoup.__module__].Tag.getText = bs_fixed_getText
# End fix

def strip_whitespace(text):
    lines = text.split('\n')
    return '\n'.join(x.strip().rstrip(u'\xa0') for x in lines).strip() + '\n'

# from http://stackoverflow.com/questions/5842115/converting-a-string-which-contains-both-utf-8-encoded-bytestrings-and-codepoints
# Translate a unicode string containing utf8
def parse_double_utf8(txt):
    def parse(m):
        try:
            return m.group(0).encode('latin1').decode('utf8')
        except UnicodeDecodeError:
            return m.group(0)
    return re.sub(ur'[\xc2-\xf4][\x80-\xbf]+', parse, txt)

def canonicalize(text):
    return strip_whitespace(parse_double_utf8(text))

def concat(domain, url):
    return domain + url if url.startswith('/') else domain + '/' + url

# End utility functions

# Base Parser
# To create a new parser, subclass and define _parse(html).
class BaseParser(object):
    url = None
    domains = [] # List of domains this should parse

    # These should be filled in by self._parse(html)
    date = None
    title = None
    byline = None
    body = None

    real_article = True # If set to False, ignore this article
    SUFFIX = ''         # append suffix, like '?fullpage=yes', to urls

    meta = []  # Currently unused.

    # Used when finding articles to parse
    feeder_pat = None  # Look for links matching this regular expression
    feeder_pages = []   # on these pages

    feeder_soup_version = 'bs3'  # use this version of BeautifulSoup for feed

    def __init__(self, url):
        self.url = url
        try:
            self.html = grab_url(self._printableurl())
        except urllib2.HTTPError as e:
            if e.code == 404:
                self.real_article = False
                return
            raise
        self._parse(self.html)
        self.parse_time = datetime.utcnow()

    def _printableurl(self):
        return self.url + self.SUFFIX

    def _parse(self, html):
        """Should take html and populate self.(date, title, byline, body)

        If the article isn't valid, set self.real_article to False and return.
        """
        raise NotImplementedError()

    def __unicode__(self):
        return canonicalize(u'\n'.join((self.date, self.title, self.byline,
                                        self.body,)))

    @classmethod
    def feed_urls(cls):
        all_urls = []
        for feeder_url in cls.feeder_pages:
            html = grab_url(feeder_url)
            if cls.feeder_soup_version == 'bs3':
                soup = bs3.BeautifulSoup(html)
            elif cls.feeder_soup_version == 'bs4':
                soup = bs4.BeautifulSoup(html, 'html5lib')
            else:
                raise Exception('Invalid feeder_soup_version.  Must be from '
                                '{bs3,bs4}: %s', cls.feeder_soup_version)

            urls = [a_tag.get('href', '') for a_tag in soup.findAll('a')]

            # If there's no scheme, assume it needs the whole authority
            # prepended
            feeder_authority = url_util.get_url_authority(feeder_url)
            urls = [url if '://' in url else concat(feeder_authority, url) for url in urls]

            all_urls = all_urls + [url for url in urls if
                                   re.search(cls.feeder_pat, url)]
        return all_urls
