from django.conf import settings

from haystack import indexes

from pombola.core import models as core_models


# TODO - currently I'm using the realtime search index - which is possibly a bad
# idea if we get heavy load. Switch to a queue as suggested in the docs:
#
#   http://docs.haystacksearch.org/dev/best_practices.html#use-of-a-queue-for-a-better-user-experience

# TODO - all the search result html could be cached to save db access when
# displaying results: Not done initially as the templates will keep changing
# until the design is stable.
#
#   http://docs.haystacksearch.org/dev/best_practices.html#avoid-hitting-the-database


# Note - these indexes could be specified in the individual apps, which might
# well be cleaner. They are all in one place as I believe there is a good chance
# that they'll be heavily edited when moving to Haystack 2, or some other search
# index abstraction.


class BaseIndex(indexes.SearchIndex):
    text = indexes.CharField(document=True, use_template=True)


class PersonIndex(BaseIndex, indexes.Indexable):
    name_auto = indexes.EdgeNgramField(model_attr='name')

    def get_model(self):
        return core_models.Person

class PlaceIndex(BaseIndex, indexes.Indexable):
    name_auto = indexes.EdgeNgramField(model_attr='name')

    def get_model(self):
        return core_models.Place

class OrganisationIndex(BaseIndex, indexes.Indexable):
    name_auto = indexes.EdgeNgramField(model_attr='name')

    def get_model(self):
        return core_models.Organisation

class PositionTitleIndex(BaseIndex, indexes.Indexable):
    name_auto = indexes.EdgeNgramField(model_attr='name')

    def get_model(self):
        return core_models.PositionTitle


if settings.ENABLED_FEATURES['hansard']:
    from pombola.hansard import models as hansard_models

    class HansardEntryIndex(BaseIndex, indexes.Indexable):
        def get_model(self):
            return hansard_models.Entry
        pass
