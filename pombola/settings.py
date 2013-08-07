# Django settings for pombola project.

import os
import shutil
import sys
import logging
import yaml

# We need to work out if we are in test mode so that the various directories can
# be changed so that the tests do not clobber the dev environment (eg media
# files, xapian search index, cached hansard downloads).

# Tried various suggestions but most did not work. Eg detecting the 'outbox'
# attribute on django.core.mail does not work as it is created after settings.py
# is read.

# This is absolutely horrid code - assumes that you only ever run tests via
# './manage.py test'. It does work though...
# TODO - replace this with something much more robust
IN_TEST_MODE = sys.argv[1:2] == ['test']

# Work out where we are to set up the paths correctly and load config
base_dir = os.path.abspath( os.path.join( os.path.split(__file__)[0], '..' ) )
root_dir = os.path.abspath( os.path.join( base_dir, '..' ) )

# print "base_dir: " + base_dir
# print "root_dir: " + root_dir

# Change the root dir in testing, and delete it to ensure that we have a clean
# slate. Also rint out a little warning - adds clutter to the test output but
# better than letting a site go live and not notice that the test mode has been
# detected by mistake
if IN_TEST_MODE:
    root_dir += '/testing'
    if os.path.exists( root_dir ):
        shutil.rmtree( root_dir )
    print "Running in test mode! (testing root_dir is '%s')" % root_dir


# load the mySociety config
config_file = os.path.join( base_dir, 'conf', 'general.yml' )
config = yaml.load( open(config_file, 'r') )

if int(config.get('STAGING')):
    STAGING = True
else:
    STAGING = False

# switch on all debug when staging
DEBUG          = STAGING
TEMPLATE_DEBUG = STAGING

# TODO - should we delegate this to web server (issues with admin css etc)?
SERVE_STATIC_FILES = STAGING

ADMINS = (
    (config.get('ERRORS_NAME'), config.get('ERRORS_EMAIL')),
)

DEFAULT_FROM_EMAIL = config.get('FROM_EMAIL')

# This is the From: address used for error emails to ADMINS
SERVER_EMAIL = DEFAULT_FROM_EMAIL

MANAGERS = (
    (config.get('MANAGERS_NAME'), config.get('MANAGERS_EMAIL')),
)

DATABASES = {
    'default': {
        'ENGINE':   'django.contrib.gis.db.backends.postgis',
        'NAME':     config.get('POMBOLA_DB_NAME'),
        'USER':     config.get('POMBOLA_DB_USER'),
        'PASSWORD': config.get('POMBOLA_DB_PASS'),
        'HOST':     config.get('POMBOLA_DB_HOST'),
        'PORT':     config.get('POMBOLA_DB_PORT'),
    }
}

# Local time zone for this installation. Choices can be found here:
# http://en.wikipedia.org/wiki/List_of_tz_zones_by_name
# although not all choices may be available on all operating systems.
# On Unix systems, a value of None will cause Django to use the same
# timezone as the operating system.
# If running in a Windows environment this must be set to the same as your
# system time zone.
TIME_ZONE = config.get('TIME_ZONE')

# Language code for this installation. All choices can be found here:
# http://www.i18nguy.com/unicode/language-identifiers.html
LANGUAGE_CODE = 'en-GB'

SITE_ID = 1

# If you set this to False, Django will make some optimizations so as not
# to load the internationalization machinery.
USE_I18N = True

# If you set this to False, Django will not format dates, numbers and
# calendars according to the current locale
USE_L10N = True

# Absolute filesystem path to the directory that will hold user-uploaded files.
# Example: "/home/media/media.lawrence.com/media/"
MEDIA_ROOT = os.path.normpath( os.path.join( root_dir, "media_root/") )

# URL that handles the media served from MEDIA_ROOT. Make sure to use a
# trailing slash.
# Examples: "http://media.lawrence.com/media/", "http://example.com/media/"
MEDIA_URL = '/media_root/'

# Absolute path to the directory static files should be collected to.
# Don't put anything in this directory yourself; store your static files
# in apps' "static/" subdirectories and in STATICFILES_DIRS.
# Example: "/home/media/media.lawrence.com/static/"
STATIC_ROOT = os.path.normpath( os.path.join( root_dir, "collected_static/") )

# URL prefix for static files.
# Example: "http://media.lawrence.com/static/"
STATIC_URL = '/static/'

# integer which when updated causes the caches to fetch new content. See note in
# 'base.html' for a better alternative in Django 1.4
STATIC_GENERATION_NUMBER = 36

# Additional locations of static files
STATICFILES_DIRS = (
    # Put strings here, like "/home/html/static" or "C:/www/django/static".
    # Always use forward slashes, even on Windows.
    # Don't forget to use absolute paths, not relative paths.
    os.path.join( base_dir, "web/static/" ),
)

# List of finder classes that know how to find static files in
# various locations.
STATICFILES_FINDERS = (
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
    # 'django.contrib.staticfiles.finders.DefaultStorageFinder',
)

# Make this unique, and don't share it with anybody.
SECRET_KEY = config.get('DJANGO_SECRET_KEY')

CACHES = {

    # by default use memcached locally. This is what get used by
    # django.core.cache.cache
    'default': {
        'BACKEND':    'django.core.cache.backends.memcached.MemcachedCache',
        'LOCATION':   '127.0.0.1:11211',
        'KEY_PREFIX': config.get('POMBOLA_DB_NAME'),
    },

    # we also have a dummy cache that is used for all the page requests - we want
    # the cache framework to auto-add all the caching headers, but we don't actually
    # want to do the caching ourselves - rather we leave that to Varnish on the
    # servers.
    'dummy': {
        'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
    },
}

CACHE_MIDDLEWARE_ALIAS='dummy'
if DEBUG:
    CACHE_MIDDLEWARE_SECONDS = 0
else:
    CACHE_MIDDLEWARE_SECONDS = 60 * 20 # twenty minutes
CACHE_MIDDLEWARE_KEY_PREFIX = config.get('POMBOLA_DB_NAME')
CACHE_MIDDLEWARE_ANONYMOUS_ONLY = True

# Always use the TemporaryFileUploadHandler as it allows us to access the
# uploaded file on disk more easily. Currently used by the CSV upload in
# scorecards admin.
FILE_UPLOAD_HANDLERS = (
    # "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
)


# List of callables that know how to import templates from various sources.
TEMPLATE_LOADERS = (
    'django.template.loaders.app_directories.Loader',
    'django.template.loaders.filesystem.Loader',
    # 'django.template.loaders.eggs.Loader',
)


MIDDLEWARE_CLASSES = (
    'django.middleware.gzip.GZipMiddleware', # first in list so it is able to act last on response
    'django.middleware.cache.UpdateCacheMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.middleware.cache.FetchFromCacheMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'pagination.middleware.PaginationMiddleware',
    'pombola.middleware.FakeInstanceMiddleware',
)

ROOT_URLCONF = 'pombola.urls'

TEMPLATE_DIRS = (
    # Put strings here, like "/home/html/django_templates" or "C:/www/django/templates".
    # Always use forward slashes, even on Windows.
    # Don't forget to use absolute paths, not relative paths.
    os.path.join( base_dir, "pombola/templates" ),
)

TEMPLATE_CONTEXT_PROCESSORS = (
    "django.contrib.auth.context_processors.auth",
    "django.core.context_processors.debug",
    "django.core.context_processors.i18n",
    "django.core.context_processors.media",
    "django.core.context_processors.static",
    "django.core.context_processors.request",
    "django.contrib.messages.context_processors.messages",
    "pombola.core.context_processors.add_settings",    
)

COUNTRY_APP = config.get('COUNTRY_APP')
if not COUNTRY_APP:
    raise Exception("You need to set 'COUNTRY_APP' in your config")



INSTALLED_APPS = (
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.sites',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',
    'django.contrib.gis',

    'pombola.admin_additions',
    'django.contrib.admin',
    'django.contrib.admindocs',

    'south',
    'pagination',
    'ajax_select',
    'markitup',

    # SayIt
    'django_select2',
    #'tastypie',
    'django_bleach',
    'popit',
    'instances',
    'speeches',

    'pombola.' + COUNTRY_APP,

    'mapit',

    'pombola.images',
    'sorl.thumbnail',

    'haystack',

    'pombola.helpers',
    'pombola.info',
    'pombola.tasks',
    'pombola.core',
    'pombola.feedback',
    'pombola.scorecards',
    'pombola.search',
    'pombola.file_archive',
    'pombola.map',
)

# add the optional apps
ALL_OPTIONAL_APPS = ( 'hansard', 'projects', 'place_data', 'votematch' )
OPTIONAL_APPS = tuple( [ 'pombola.' + a for a in config.get( 'OPTIONAL_APPS', [] ) ] )
INSTALLED_APPS += OPTIONAL_APPS

# mapit related settings
MAPIT_AREA_SRID = 4326
MAPIT_COUNTRY = 'KE'
MAPIT_RATE_LIMIT = ['127.0.0.1']


# A sample logging configuration. The only tangible logging
# performed by this configuration is to send an email to
# the site admins on every HTTP 500 error.
# See http://docs.djangoproject.com/en/dev/topics/logging for
# more details on how to customize your logging configuration.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'filters': {
         'require_debug_false': {
             '()': 'django.utils.log.RequireDebugFalse'
         }
     },
    'handlers': {
        'mail_admins': {
            'filters': ['require_debug_false'],
            'level': 'ERROR',
            'class': 'django.utils.log.AdminEmailHandler'
        },
        'stream_to_stderr': {
            'level': 'WARN',
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['stream_to_stderr'],
            'level': 'INFO',
            'propagate': True,
        },
        'django.request': {
            'handlers': ['mail_admins'],
            'level': 'ERROR',
            'propagate': True,
        },
    }
}

# Configure the Hansard app
HANSARD_CACHE = os.path.join( base_dir, "../hansard_cache" )
KENYA_PARSER_PDF_TO_HTML_HOST = config.get('KENYA_PARSER_PDF_TO_HTML_HOST')

# The name of a Twitter account related to this website. This will be used to
# pull in the latest tweets on the homepage and in the share on twitter links.
TWITTER_USERNAME = config.get('TWITTER_USERNAME')
# The widget ID is used for displaying tweets on the homepage.
TWITTER_WIDGET_ID = config.get('TWITTER_WIDGET_ID')

# pagination related settings
PAGINATION_DEFAULT_PAGINATION      = 10
PAGINATION_DEFAULT_WINDOW          = 2
PAGINATION_DEFAULT_ORPHANS         = 2
PAGINATION_INVALID_PAGE_RAISES_404 = True


# haystack config - interface to search engine
HAYSTACK_CONNECTIONS = {
    'default': {
        'ENGINE': 'haystack.backends.elasticsearch_backend.ElasticsearchSearchEngine',
        'URL': 'http://127.0.0.1:9200/',
        'INDEX_NAME': 'haystack',
    },
}
HAYSTACK_SIGNAL_PROCESSOR = 'haystack.signals.RealtimeSignalProcessor'

# Admin autocomplete
AJAX_LOOKUP_CHANNELS = {
    'person_name'       : dict(model='core.person',        search_field='legal_name'),
    'organisation_name' : dict(model='core.organisation',  search_field='name'),
    'place_name'        : dict(model='core.place',         search_field='name'),
    'title_name'        : dict(model='core.positiontitle', search_field='name'),
}
AJAX_SELECT_BOOTSTRAP = False
AJAX_SELECT_INLINES   = None # we add the js and css ourselves in the header

# misc settings
HTTPLIB2_CACHE_DIR = os.path.join( root_dir, 'httplib2_cache' )
GOOGLE_ANALYTICS_ACCOUNT = config.get('GOOGLE_ANALYTICS_ACCOUNT')

IEBC_API_ID = config.get('IEBC_API_ID')
IEBC_API_SECRET = config.get('IEBC_API_SECRET')

# Markitup settings
MARKITUP_FILTER = ('markdown.markdown', {'safe_mode': True, 'extensions':['tables']})
MARKITUP_SET = 'markitup/sets/markdown'


# There are some models that are just for testing, so they are not included in
# the South migrations.
SOUTH_TESTS_MIGRATE = False


# Settings for the selenium tests
TEST_RUNNER   = 'django_selenium.selenium_runner.SeleniumTestRunner'
SELENIUM_DRIVER = 'Firefox'

# For the disqus comments
DISQUS_SHORTNAME       = config.get( 'DISQUS_SHORTNAME', None )
# At some point we should deprecate this. For now it defaults to true so that
# no entry in the config does the right thing.
DISQUS_USE_IDENTIFIERS = config.get( 'DISQUS_USE_IDENTIFIERS', True )


# Polldaddy widget ID - from http://polldaddy.com/
# Use the widget rather than embedding a poll direct as it will allow the poll
# to be changed without having to alter the settings or HTML. If left blank
# then no poll will be shown.
POLLDADDY_WIDGET_ID = config.get( 'POLLDADDY_WIDGET_ID', None );


# RSS feed to the blog related to this site. If present will cause the 'Latest
# News' to appear on the homepage.
BLOG_RSS_FEED = config.get( 'BLOG_RSS_FEED', None )


# create the ENABLED_FEATURES hash that is used to toggle features on and off.
ENABLED_FEATURES = {}
for key in ALL_OPTIONAL_APPS: # add in the optional apps
    ENABLED_FEATURES[key] = 'pombola.' + key in INSTALLED_APPS



# list of all the position title slugs considered to be politicians
POLITICIAN_TITLE_SLUGS = [
    'mp',
    'nominated-member-parliament',
    'member-national-assembly',
    'representative',
    'senator',
    'president',
    'governor',
    'ward-representative',
]

# map boundaries
MAP_BOUNDING_BOX_NORTH = config.get('MAP_BOUNDING_BOX_NORTH')
MAP_BOUNDING_BOX_EAST  = config.get('MAP_BOUNDING_BOX_EAST' )
MAP_BOUNDING_BOX_SOUTH = config.get('MAP_BOUNDING_BOX_SOUTH')
MAP_BOUNDING_BOX_WEST  = config.get('MAP_BOUNDING_BOX_WEST' )

THUMBNAIL_DEBUG = True
