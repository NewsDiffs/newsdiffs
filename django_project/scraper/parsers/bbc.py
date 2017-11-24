from baseparser import BaseParser
import BeautifulSoup as bs3


class BBCParser(BaseParser):
    SUFFIX = '?print=true'
    domains = ['www.bbc.co.uk']

    feeder_pat   = '^http://www.bbc.co.uk/news/'
    feeder_pages = ['http://www.bbc.co.uk/news/']

    def _parse(self, html):
        soup = bs3.BeautifulSoup(html,
                                 convertEntities=bs3.BeautifulSoup.HTML_ENTITIES,
                                 fromEncoding='utf-8')

        self.meta = soup.findAll('meta')
        elt = soup.find('h1', 'story-header')
        if elt is None:
            self.real_article = False
            return
        self.title = elt.getText()
        self.byline = ''
        self.date = soup.find('span', 'date').getText()

        div = soup.find('div', 'story-body')
        if div is None:
            # Hack for video articles
            div = soup.find('div', 'emp-decription')
        if div is None:
            self.real_article = False
            return
        self.body = '\n'+'\n\n'.join([x.getText() for x in div.childGenerator()
                                      if isinstance(x, bs3.Tag) and x.name == 'p'])
