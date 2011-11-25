from django.conf.urls.defaults import patterns, include, url

urlpatterns = patterns('search.views',
    url( r'^location/',     'location_search', name="location_search" ),
    url( r'^autocomplete/', 'autocomplete',    name="autocomplete"    ),
    url( r'^',              include('haystack.urls')                  ),
)
