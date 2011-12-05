import datetime
from haystack import indexes

from core    import models as core_models
from hansard import models as hansard_models


# TODO - currently I'm using the realtime search index - which is possibly a bad
# idea if we get heavy load. Switch to a queue as suggested in the docs:
#
#   http://docs.haystacksearch.org/dev/best_practices.html#use-of-a-queue-for-a-better-user-experience

# TODO - all the search result html could be cached to save db access when
# displaying results: Not done initially as the templates will keep changing
# until the design is stable.
#
#   http://docs.haystacksearch.org/dev/best_practices.html#avoid-hitting-the-database




class PersonIndex(indexes.RealTimeSearchIndex, indexes.Indexable):
    text = indexes.CharField(document=True, use_template=True)
    name_auto = indexes.EdgeNgramField(model_attr='name')

    def get_model(self):
        return core_models.Person


class PlaceIndex(indexes.RealTimeSearchIndex, indexes.Indexable):
    text = indexes.CharField(document=True, use_template=True)

    def get_model(self):
        return core_models.Place


class OrganisationIndex(indexes.RealTimeSearchIndex, indexes.Indexable):
    text = indexes.CharField(document=True, use_template=True)

    def get_model(self):
        return core_models.Organisation


class PositionTitleIndex(indexes.RealTimeSearchIndex, indexes.Indexable):
    text = indexes.CharField(document=True, use_template=True)

    def get_model(self):
        return core_models.PositionTitle


class HansardEntryIndex(indexes.RealTimeSearchIndex, indexes.Indexable):
    text = indexes.CharField(document=True, use_template=True)

    def get_model(self):
        return hansard_models.Entry


