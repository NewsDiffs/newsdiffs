import datetime
import json
import logging
import os
import re

from django.shortcuts import render_to_response, get_object_or_404, redirect
from django.http import HttpResponse, HttpResponseRedirect, Http404
from django.core.urlresolvers import reverse
from django.template import RequestContext
from django.views.decorators.cache import cache_page

from frontend import models
from frontend.models import Article, Version
from util import url_util

logger = logging.getLogger(__name__)

OUT_FORMAT = '%B %d, %Y at %l:%M %p UTC'

SEARCH_ENGINES = """
http://www.ask.com
http://www.google
https://www.google
search.yahoo.com
http://www.bing.com
""".split()

SOURCES = '''
nytimes.com
cnn.com
politico.com
washingtonpost.com
bbc.co.uk
'''.split()


def came_from_search_engine(request):
    return any(x in request.META.get('HTTP_REFERER', '')
               for x in SEARCH_ENGINES)


def get_first_update(source):
    if source is None:
        source = ''
    updates = (
        models.Article.objects
            .order_by('last_update')
            .filter(last_update__isnull=False, url__contains=source)
    )
    try:
        return updates[0].last_update
    except IndexError:
        return datetime.datetime.utcnow()


def get_last_update(source):
    if source is None:
        source = ''
    updates = (
        models.Article.objects
            .order_by('-last_update')
            .filter(last_update__isnull=False, url__contains=source)
    )
    try:
        return updates[0].last_update
    except IndexError:
        return datetime.datetime.utcnow()


def get_articles(source=None, distance=0):

    source_regex = re.compile(r'^https?://(?:[^/]*\.)%s/' % source) if source else None

    page_length = datetime.timedelta(days=1)
    end_date = datetime.datetime.utcnow() - distance * page_length
    start_date = end_date - page_length

    version_query = '''
    SELECT
      version.id, 
      version.article_id, 
      version.v, 
      version.title,
      version.byline, 
      version.date, 
      version.boring, 
      version.diff_json,
      T.age as age,
      Articles.url as a_url, 
      Articles.initial_date as a_initial_date,
      Articles.last_update as a_last_update, 
      Articles.last_check as a_last_check
    FROM version,
     (
       SELECT 
         Articles.id as article_id, 
         MAX(version_3.date) AS age, 
         COUNT(version_3.id) AS num_vs
      FROM Articles LEFT OUTER JOIN version version_3 ON (Articles.id = version_3.article_id)
      WHERE version_3.boring = 0 
      GROUP BY Articles.id 
      -- isn't 'age' here actually latest_version_date?
      HAVING (age > %s  AND age < %s  AND num_vs > 1 )
      ) T, 
      Articles
    WHERE (version.article_id = Articles.id) and
          (version.article_id = T.article_id) and
          NOT version.boring
    ORDER BY date
    '''

    all_versions = models.Version.objects.raw(version_query,
                                              (start_date, end_date))
    article_dict = {}
    for v in all_versions:
        a = models.Article(id=v.article_id, url=v.a_url,
                           initial_date=v.a_initial_date,
                           last_update=v.a_last_update,
                           last_check=v.a_last_check)
        v.article = a
        article_dict.setdefault(v.article, []).append(v)

    articles = []
    for article, versions in article_dict.items():
        url = article.url
        if source and not source_regex.match(url):
            logger.info('URL did not pass filter: %s', url)
            continue
        if 'blogs.nytimes.com' in url:  # XXX temporary
            logger.info('Skipping blogs.nytimes.com URL: %s', url)
            continue
        if len(versions) < 2:
            continue
        version_info = get_version_info(article, versions)
        articles.append((article, versions[-1], version_info))
    articles.sort(key=lambda x: x[-1][0][1].date, reverse=True)
    return articles


def is_valid_domain(domain):
    """Cheap method to tell whether a domain is being tracked."""
    return any(domain.endswith(source) for source in SOURCES)


@cache_page(os.environ.get('CACHE_DURATION_MINUTES', 5))
def browse(request, source=''):
    if source not in SOURCES + ['']:
        raise Http404
    pagestr = request.REQUEST.get('page', '1')
    try:
        page = int(pagestr)
    except ValueError:
        page = 1

    # Temporarily disable browsing past the first page, since it was
    # overloading the server.
    if page != 1:
        return HttpResponseRedirect(reverse(browse))

    first_update = get_first_update(source)
    num_pages = (datetime.datetime.utcnow() - first_update).days + 1
    page_list = range(1, 1 + num_pages)
    page_list = []

    articles = get_articles(source=source, distance=page-1)
    return render_to_response('browse.html', {
        'source': source,
        'articles': articles,
        'first_update': first_update,
        'page': page,
        'page_list': page_list,
        'sources': SOURCES
    })


@cache_page(os.environ.get('CACHE_DURATION_MINUTES', 5))
def feed(request, source=''):
    if source not in SOURCES + ['']:
        raise Http404
    pagestr=request.REQUEST.get('page', '1')
    try:
        page = int(pagestr)
    except ValueError:
        page = 1

    first_update = get_first_update(source)
    num_pages = (datetime.datetime.utcnow() - first_update).days + 1
    page_list = range(1, 1+num_pages)

    articles = get_articles(source=source, distance=page-1)
    last_update = get_last_update(source)
    return render_to_response('feed.xml', {
            'source': source, 'articles': articles,
            'page':page,
            'request':request,
            'page_list': page_list,
            'last_update': last_update,
            'sources': SOURCES
            },
            context_instance=RequestContext(request),
            mimetype='application/atom+xml')


def old_diff_view(request):
    """Support for legacy diff urls"""
    url = request.REQUEST.get('url')
    v1tag = request.REQUEST.get('v1')
    v2tag = request.REQUEST.get('v2')
    if url is None or v1tag is None or v2tag is None:
        return HttpResponseRedirect(reverse(front))

    try:
        v1 = Version.objects.get(v=v1tag)
        v2 = Version.objects.get(v=v2tag)
    except Version.DoesNotExist:
        raise Http404

    try:
        article = Article.objects.get(url=url)
    except Article.DoesNotExist:
        raise Http404

    view_kwargs = dict(vid1=v1.id,
                       vid2=v2.id,
                       diff_url=article.url)
    return redirect(reverse(diff_view, kwargs=view_kwargs), permanent=True)


def diff_view(request, vid1, vid2, diff_url):
    try:
        v1 = Version.objects.get(id=int(vid1))
        v2 = Version.objects.get(id=int(vid2))
    except Version.DoesNotExist:
        raise Http404

    if v1.article != v2.article:
        raise Exception('versions %s and %s have different articles' %
                        (vid1, vid2))

    article = v1.article

    title = article.latest_version().title

    versions = dict(enumerate(article.versions()))

    adjacent_versions = []
    dates = []
    texts = []

    for v in (v1, v2):
        texts.append(v.text())
        dates.append(v.date.strftime(OUT_FORMAT))

        indices = [i for i, x in versions.items() if x == v]
        index = indices[0]
        adjacent_versions.append([versions.get(index+offset)
                                  for offset in (-1, 1)])

    if any(x is None for x in texts):
        raise Exception('missing text for some version')

    diff_hrefs = []
    for i in range(2):
        if all(x[i] for x in adjacent_versions):
            kwargs = dict(vid1=adjacent_versions[0][i].id,
                          vid2=adjacent_versions[1][i].id,
                          diff_url=diff_url)
            diff_href = reverse('diff_view', kwargs=kwargs)
            diff_hrefs.append(diff_href)
        else:
            diff_hrefs.append('')

    return render_to_response('diff_view.html', {
        'title': title,
        'date1': dates[0],
        'date2': dates[1],
        'text1': texts[0],
        'text2': texts[1],
        'prev': diff_hrefs[0],
        'next': diff_hrefs[1],
        'article_url': article.url,
        'v1': v1,
        'v2': v2,
        'display_search_banner': came_from_search_engine(request),
    })


def get_version_info(article, versions=None):
    if versions is None:
        versions = article.versions()
    version_info = []
    last_version = None
    for version in versions:
        if last_version is None:
            diff_href = ''
        else:
            kwargs = dict(vid1=last_version.id,
                          vid2=version.id,
                          diff_url=article.url)
            diff_href = reverse('diff_view', kwargs=kwargs)
        version_info.append((diff_href, version))
        last_version = version
    version_info.reverse()
    return version_info


def prepend_http(url):
    """Return a version of the url that starts with the proper scheme.

    url may look like

    www.nytimes.com
    https:/www.nytimes.com  <- because Apache collapses adjacent slashes
    http://www.nytimes.com
    """
    components = url.split('/', 2)
    if len(components) <= 2 or '.' in components[0]:
        components = ['http:', '']+components
    elif components[1]:
        components[1:1] = ['']
    return '/'.join(components)


def swap_http_https(url):
    """Get the url with the other of http/https to start"""
    for (one, other) in [("https:", "http:"),
                         ("http:", "https:")]:
        if url.startswith(one):
            return other+url[len(one):]
    raise ValueError("URL doesn't start with http: or https: ({0})".format(url))


def decode_scheme_colon(url):
    # Sometimes the colon of http: or https: is URL-encoded.
    # Sometimes the encoding percent sign is itself encoded (multiple times!)
    # So replace as many %25 as necessary to get to the colon
    return re.sub('http(s?)%(25)*3A', 'http\g<1>:', url)


def article_history(request, history_url):

    normal_url = url_util.remove_query_params(history_url)  # For if user copy-pastes from news site
    normal_url = prepend_http(normal_url)

    # Give an error on urls with the wrong hostname without hitting the
    # database.  These queries are usually spam.
    domain = url_util.get_url_domain(normal_url)
    if not is_valid_domain(domain):
        logger.debug('Unsupported domain for URL: %s', history_url)
        return render_to_response('article_history_missing.html', {
            'history_url': normal_url,
            'message': 'NewsDiffs currently does not support this domain: %s' % domain
        })

    decoded_url = decode_scheme_colon(normal_url)
    try:
        try:
            article = Article.objects.get(url=decoded_url)
        except Article.DoesNotExist:
            article = Article.objects.get(url=swap_http_https(decoded_url))
    except Article.DoesNotExist:
        return render_to_response('article_history_missing.html',
                                  {'url': decoded_url})

    version_info = get_version_info(article)
    return render_to_response('article_history.html', {
        'article': article,
        'versions': version_info,
        'display_search_banner': came_from_search_engine(request),
    })


def article_history_feed(request, url):
    url = prepend_http(url)
    article = get_object_or_404(Article, url=url)
    version_info = get_version_info(article)
    args = {
        'article': article,
        'versions': version_info,
        'request': request,
    }
    return render_to_response('article_history.xml', args=args,
                              context_instance=RequestContext(request),
                              mimetype='application/atom+xml')


def article_history_by_url(request):
    url = request.REQUEST.get('url')
    if not url:
        return redirect(reverse(browse))
    else:
        view_kwargs = dict(history_url=url)
        return redirect(reverse(article_history, kwargs=view_kwargs))


def json_view(request, vid):
    version = get_object_or_404(Version, id=int(vid))
    data = dict(
        title=version.title,
        byline=version.byline,
        date=version.date.isoformat(),
        text=version.text(),
        )
    return HttpResponse(json.dumps(data), mimetype="application/json")


def upvote(request):
    article_url = request.REQUEST.get('article_url')
    diff_v1 = request.REQUEST.get('diff_v1')
    diff_v2 = request.REQUEST.get('diff_v2')
    remote_ip = request.META.get('REMOTE_ADDR')
    article_id = Article.objects.get(url=article_url).id
    models.Upvote(article_id=article_id, diff_v1=diff_v1, diff_v2=diff_v2,
                  creation_time=datetime.datetime.utcnow(),
                  upvoter_ip=remote_ip).save()
    return render_to_response('upvote.html')


def about(request):
    return render_to_response('about.html', {})


def examples(request):
    return render_to_response('examples.html', {})


def contact(request):
    return render_to_response('contact.html', {})


def front(request):
    return render_to_response('front.html', {'sources': SOURCES})


def subscribe(request):
    return render_to_response('subscribe.html', {})


def press(request):
    return render_to_response('press.html', {})
