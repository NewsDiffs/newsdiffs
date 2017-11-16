NewsDiffs
==========

A website and framework that tracks changes in online news articles over time.

Original installation at newsdiffs.org.
A product of the Knight Mozilla MIT news hackathon in June 2012.
Authors: Eric Price (ecprice@mit.edu), Greg Price (gnprice@gmail.com),
 and Jennifer 8. Lee (jenny@jennifer8lee.com)

This is free software under the MIT/Expat license; see LICENSE.
The project's source code lives at http://github.com/ecprice/newsdiffs .


Requirements
------------

You need to have installed on your local machine
* Git
* Python 2.6 or later
* Django and other Python libraries

On a Debian- or Ubuntu-based system, it may suffice (untested) to run

```
$ sudo apt-get install git-core python-django python-django-south
```

On Mac OS, the easiest way may be to install pip:
[http://www.pip-installer.org/en/latest/installing.html](http://www.pip-installer.org/en/latest/installing.html)
and then

```
$ pip install Django
```

### Installing MySQL-python

* https://stackoverflow.com/a/40592989/39396
* https://github.com/PyMySQL/mysqlclient-python/issues/169

Do something equivalent to the following (have the latest Homebrew mysql and 
then follow the instructions from `brew info openssl` for installation): 

```
brew upgrade mysql
brew upgrade openssl
LDFLAGS=-L/usr/local/opt/openssl/lib CPPFLAGS=-I/usr/local/opt/openssl/include pip install MySQL-python
```

Initial setup
-------------

newsdiffs depends upon several environment variables.
`config/local-example.sh` contains example parameters to run the application locally.
You can use these values as-is for a SQLite database or modify for your local 
environment if you desire.  If you want to modify the values, please copy the
contents to a new file rather than change the example.

```
$ source config/local-example.sh
$ python website/manage.py syncdb
$ python website/manage.py migrate
$ mkdir articles
```

## Environment variables
| Environment Variable| Description                              |
| --------------------| -----------------------------------------|
| `DJANGO_SECRET_KEY` | the secret key for Django operations     |
| `ARTICLES_DIR_ROOT` | the directory where articles are stored. If not absolute, it will be relative to the Django project directory. | 
| `DB_ENGINE`         | the Django database engine               |
| `DB_HOST`           | the Django database Host                 |
| `DB_PORT`           | the Django database port                 |
| `DB_NAME`           | the Django database name                 |
| `DB_USER`           | the Django database username             |
| `DB_PASSWORD`       | the Django database password             |
| `LOG_FILE_PATH`     | WSGI-only: the path to which to log      |
| `LOG_FILE_LEVEL`    | WSGI-only: the level at which to log to file |

Running NewsDiffs Locally
-------------------------

Do the initial setup above.  Then to start the webserver for testing:

```
$ source config/local-example.sh && python website/manage.py runserver
```

and visit [http://localhost:8000/](http://localhost:8000/)

Running the scraper
-------------------

Do the initial setup above.  You will also need additional Python
libraries; on a Debian- or Ubuntu-based system, it may suffice
(untested) to run

```
$ sudo apt-get install python-bs4 python-beautifulsoup
```

on a Mac, you will want something like

```
$ pip install beautifulsoup4
$ pip install beautifulsoup
$ pip install html5lib
```

Note that we need two versions of BeautifulSoup, both 3.2 and 4.0;
some websites are parsed correctly in only one version.

Then run
  
```
$ source config/local-example.sh && python website/manage.py scraper
```

This will populate the articles repository with a list of current news
articles.  This is a snapshot at a single time, so the website will
not yet have any changes. To get changes, wait some time (say, 3
hours) and run 'python website/manage.py scraper' again.  If any of
the articles have changed in the intervening time, the website should
display the associated changes.

To run the scraper every hour, run something like:

```
$ while true; do python website/manage.py scraper; sleep 60m; done
```

or make a cron job.

There is also a file `run_continuously.py` showing how you might continuously
scrape the news.

Adding new sites to the scraper
-------------------------------

The procedure for adding new sites to the scraper is outlined in
`parsers/__init__.py` .  You need to

1. Create a new parser module in parsers/ .  This should be a
   subclass of BaseParser (in parsers/baseparser.py).  Model it off
   the other parsers in that directory.  You can test the parser
   with by running, e.g.,

    ```$ python parsers/test_parser.py bbc.BBCParser```
    
    which will output a list of URLs to track, and
    
    ```$ python parsers/test_parser.py bbc.BBCParser http://www.bbc.co.uk/news/uk-21649494```
    
    which will output the text that NewsDiffs would store.

2. Add the parser to 'parsers' in `parsers/__init__.py`

   This should cause the scraper to start tracking the site.

3. To make the source display properly on the website, you will need
   minor edits to two other files: website/frontend/models.py and
   website/frontend/views.py (to define the display name and create a tab
   for the source, respectively).  Search for 'bbc' to find the locations
   to edit.
