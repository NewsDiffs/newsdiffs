from datetime import datetime
import logging
import subprocess
import os

from django.db import models
import json

from util import path_util

logger = logging.getLogger(__name__)

ARTICLES_DIR_ROOT = os.environ['ARTICLES_DIR_ROOT']
if not os.path.isabs(ARTICLES_DIR_ROOT):
    ARTICLES_DIR_ROOT = \
        path_util.prepend_project_dir(os.path.pardir, ARTICLES_DIR_ROOT)

GIT_PROGRAM = 'git'


def strip_prefix(string, prefix):
    if string.startswith(prefix):
        string = string[len(prefix):]
    return string


PublicationDict = {
    'www.nytimes.com': 'NYT',
    'edition.cnn.com': 'CNN',
    'www.bbc.co.uk': 'BBC',
    'www.politico.com': 'Politico',
    'www.washingtonpost.com': 'Washington Post',
}


class Article(models.Model):
    class Meta:
        db_table = 'Articles'

    url = models.CharField(max_length=2048, blank=False, unique=True,
                           db_index=True)
    initial_date = models.DateTimeField(auto_now_add=True)
    last_update = models.DateTimeField(null=True)
    last_check = models.DateTimeField(null=True)
    git_dir = models.CharField(max_length=4096, blank=False)

    @property
    def full_git_dir(self):
        return os.path.join(ARTICLES_DIR_ROOT, self.git_dir)

    def filename(self):
        ans = self.url.rstrip('/')
        if ans.startswith('http://'):
            return ans[len('http://'):]
        elif ans.startswith('https://'):
            # Terrible hack for backwards compatibility from when https was stored incorrectly,
            # perpetuating the problem
            return 'https:/' + ans[len('https://'):]
        raise ValueError("Unknown file type '%s'" % self.url)

    def publication(self):
        return PublicationDict.get(self.url.split('/')[2])

    def versions(self):
        return self.version_set.filter(boring=False).order_by('date')

    def latest_version(self):
        return self.versions().latest()

    def first_version(self):
        return self.versions()[0]

    def minutes_since_update(self):
        if not self.last_update:
            effective_update = self.initial_date
        else:
            effective_update = self.last_update
        delta = datetime.utcnow() - effective_update
        return delta.seconds // 60 + 24*60*delta.days

    def minutes_since_check(self):
        delta = datetime.utcnow() - self.last_check
        return delta.seconds // 60 + 24*60*delta.days


class Version(models.Model):
    class Meta:
        db_table = 'version'
        get_latest_by = 'date'

    article = models.ForeignKey('Article', null=False)
    v = models.CharField(max_length=255, blank=False, unique=True)
    title = models.CharField(max_length=255, blank=False)
    byline = models.CharField(max_length=255,blank=False)
    date = models.DateTimeField(blank=False)
    boring = models.BooleanField(blank=False, default=False)
    diff_json = models.CharField(max_length=255, null=True)

    def text(self):
        revision = self.v + ':' + self.article.filename()
        try:
            return subprocess.check_output([GIT_PROGRAM, 'show', revision],
                                           cwd=self.article.full_git_dir)
        except subprocess.CalledProcessError as ex:
            logger.error('Failed to get version text')
            logger.exception(ex)
            return None

    def get_diff_info(self):
        if self.diff_json is None:
            return {}
        return json.loads(self.diff_json)

    def set_diff_info(self, val=None):
        if val is None:
            self.diff_json = None
        else:
            self.diff_json = json.dumps(val)
    diff_info = property(get_diff_info, set_diff_info)


class Upvote(models.Model):
    class Meta:
        db_table = 'upvotes'

    article_id = models.IntegerField(blank=False)
    diff_v1 = models.CharField(max_length=255, blank=False)
    diff_v2 = models.CharField(max_length=255, blank=False)
    creation_time = models.DateTimeField(blank=False)
    upvoter_ip = models.CharField(max_length=255)
