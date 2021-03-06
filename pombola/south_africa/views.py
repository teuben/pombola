from __future__ import division

from collections import defaultdict
import datetime
import dateutil
import json
import logging
import re
import string
import urllib
from urlparse import urlsplit
import warnings

import requests

from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.http import Http404
from django.db.models import Count, Min, Max
from django.core.cache import caches
from django.core.exceptions import ObjectDoesNotExist
from django.shortcuts import redirect
from django.core.urlresolvers import reverse
from django.views.generic import RedirectView, TemplateView
from django.shortcuts import get_object_or_404
from django import forms
from django.utils.http import urlquote
from django.utils.translation import ugettext_lazy as _
from django.db.models import Q
from django.contrib.contenttypes.models import ContentType

import mapit
from haystack.query import RelatedSearchQuerySet, SQ
from haystack.inputs import AutoQuery
from haystack.forms import SearchForm

from info.views import InfoBlogView

from speeches.models import Section, Speech, Speaker, Tag
from speeches.views import NamespaceMixin, SpeechView, SectionView

from info.models import (
    InfoPage, Category as BlogCategory, Tag as BlogTag
)
from info.views import InfoPageView

from slug_helpers.views import SlugRedirect

from pombola.core import models
from pombola.core.views import (HomeView, BasePlaceDetailView, PlaceDetailView,
    PlaceDetailSub, OrganisationDetailView, PersonDetail, PlaceDetailView,
    OrganisationDetailSub, CommentArchiveMixin, PersonSpeakerMappingsMixin)
from pombola.search.views import GeocoderView, SearchBaseView

from pombola.south_africa.models import ZAPlace

from pombola.interests_register.models import Release, Category, Entry
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django_date_extensions.fields import ApproximateDate


# For requests to external APIs, timeout after 3 seconds:
API_REQUESTS_TIMEOUT = 3.05


logger = logging.getLogger('django.request')


class AttendanceAPIDown(Exception):
    pass


class WardCouncillorAPIDown(Exception):
    pass


# In the short term, until we have a list of constituency offices and
# addresses from DA, let's bundle these together.
CONSTITUENCY_OFFICE_PLACE_KIND_SLUGS = (
    'constituency-office',
    'constituency-area', # specific to DA party
)


class SAHomeView(HomeView):

    def get_context_data(self, **kwargs):
        context = super(SAHomeView, self).get_context_data(**kwargs)

        articles = InfoPage.objects.filter(
            kind=InfoPage.KIND_BLOG).order_by("-publication_date")

        articles_for_front_page = \
            InfoPage.objects.filter(
                categories__slug__in=(
                    'week-parliament',
                    'impressions'
                )
            ).order_by('-publication_date')

        context['news_articles'] = articles_for_front_page[:2]

        try:
            c = BlogCategory.objects.get(slug='mp-corner')
            context['mp_corner'] = articles.filter(categories=c)[0]
        except (BlogCategory.DoesNotExist, IndexError):
            context['mp_corner'] = None

        try:
            context['infographics'] = BlogTag.objects.get(name='infographic'). \
                entries.order_by('-created')[0:4]
        except BlogTag.DoesNotExist:
            context['infographics'] = []

        return context

class SAGeocoderView(GeocoderView):

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        results = context.get('geocoder_results')
        if results is not None and len(results) == 1:
            result = results[0]
            redirect_url = reverse('latlon', kwargs={
                'lat': result['latitude'],
                'lon': result['longitude']})
            redirect_url += '?q=' + urlquote(request.GET['q'])
            return redirect(redirect_url)
        else:
            return self.render_to_response(context)

class LocationSearchForm(SearchForm):
    q = forms.CharField(required=False, label=_('Search'), widget=forms.TextInput(attrs={'placeholder': 'Your address'}))

class LatLonDetailBaseView(BasePlaceDetailView):

    # Using 25km as the default, as that's what's used on MyReps.
    constituency_office_search_radius = 25

    # The codes used here should match the party slugs, and the names of the
    # icon files in .../static/images/party-map-icons/
    party_slugs_that_have_logos = set((
        'adcp', 'anc', 'apc', 'azapo', 'cope', 'da', 'ff', 'id', 'ifp', 'mf',
        'pac', 'sacp', 'ucdp', 'udm', 'agang', 'aic', 'eff'
    ));

    def get_object(self):
        # FIXME - handle bad args better.
        lat = float(self.kwargs['lat'])
        lon = float(self.kwargs['lon'])

        self.location = Point(lon, lat)

        areas = mapit.models.Area.objects.by_location(self.location)

        try:
            # FIXME - Handle getting more than one province.
            province = models.Place.objects.get(mapit_area__in=areas, kind__slug='province')
        except models.Place.DoesNotExist:
            raise Http404

        return province

    def get_ward_councillors(self, location):
        # Look up the ward on MapIt:
        url = 'http://mapit.code4sa.org/point/4326/{lon},{lat}?type=WD'.format(
            lon=location.x, lat=location.y
        )
        try:
            r = requests.get(url, timeout=API_REQUESTS_TIMEOUT)
        except requests.exceptions.RequestException as e:
            raise WardCouncillorAPIDown(u"MapIt request failed: {0}".format(e))
        mapit_json = r.json()
        if not mapit_json:
            return []
        ward_id = mapit_json.values()[0]['name']
        # Then find the ward councillor from that ward ID. There
        # should only be one at the moment, but make it a list in case
        # we support broader lookups in the future:
        url_fmt = 'http://nearby.code4sa.org/councillor/ward-{ward_id}.json'
        try:
            r = requests.get(
                url_fmt.format(ward_id=ward_id),
                timeout=API_REQUESTS_TIMEOUT)
            r.raise_for_status()
            ward_result = r.json()
        except (requests.exceptions.RequestException, ValueError) as e:
            raise WardCouncillorAPIDown(unicode(e))
        councillor_data = ward_result['councillor']
        party = models.Organisation.objects.filter(
            name__icontains=councillor_data['PartyDetail']['Name'].lower(),
            kind__slug='party'
        ).first()
        has_party_logo = party and (party.slug in self.party_slugs_that_have_logos)

        return [
            {
                'name': councillor_data['Name'],
                'person': None,
                'email': councillor_data['custom_contact_details'].get('email', ''),
                'phone': councillor_data['custom_contact_details'].get('phone', ''),
                'postal_addresses': [],
                'party': party,
                'has_party_logo': has_party_logo,
                'ward_data': ward_result,
                'ward_mapit_area_id': mapit_json.values()[0]['id'],
                'positions': [
                    {
                        'title': {'name': 'Ward Councillor'}
                    }
                ],
                'element_id': 'ward-councillor-{ward_id}-0'.format(
                    ward_id=ward_id
                ),
            }
        ]

    def get_context_data(self, **kwargs):
        context = super(LatLonDetailBaseView, self).get_context_data(**kwargs)

        try:
            context['ward_data'] = self.get_ward_councillors(self.location)
        except WardCouncillorAPIDown as e:
            context['ward_data'] = []
            context['ward_data_not_available'] = u"The error was: {0}".format(e)

        context['location'] = self.location
        context['office_search_radius'] = self.constituency_office_search_radius

        nearest_office_places = (
            ZAPlace.objects
            .filter(kind__slug__in=CONSTITUENCY_OFFICE_PLACE_KIND_SLUGS)
            .distance(self.location)
            .filter(location__distance_lte=(self.location, D(km=self.constituency_office_search_radius)))
            .order_by('distance')
            .select_related('organisation')
            .prefetch_related('organisation__org_rels_as_b')
            .prefetch_related('organisation__position_set')
        )

        context['mp_data'] = mp_data = []
        context['mpl_data'] = mpl_data = []

        for office_place in nearest_office_places:
            organisation = office_place.organisation
            if not organisation.is_ongoing():
                continue
            # Get the party and party logo:
            party = None
            has_party_logo = False
            for org_rel in organisation.org_rels_as_b.all():
                if org_rel.kind.name == 'has_office':
                    party = org_rel.organisation_a
                    if party.slug in self.party_slugs_that_have_logos:
                       has_party_logo = True
            # Find all the constituency contacts:
            for i, position in enumerate(
                    organisation.position_set.filter(
                        title__slug='constituency-contact'
                    ).currently_active()
            ):
                person = position.person
                element_id = 'constituency-contact-{office_id}-{i}'.format(
                    office_id=position.organisation.id, i=i
                )
                mp_positions = person.position_set \
                    .filter(
                        organisation__slug='national-assembly',
                        title__slug='member'
                    ) \
                    .currently_active()
                mpl_positions = person.position_set \
                    .filter(
                        organisation__kind__slug='provincial-legislature',
                        title__slug='member'
                    ) \
                    .currently_active()
                email, phone = None, None
                email_contact = person.contacts.filter(kind__slug='email').first()
                if email_contact:
                    email = email_contact.value
                phone_contact = person.contacts.filter(kind__slug='voice').first()
                if phone_contact:
                    phone = phone_contact.value
                person_data = {
                    'name': person.legal_name,
                    'person': person,
                    'email': email,
                    'phone': phone,
                    'postal_addresses': [
                        pa.value for pa in office_place.postal_addresses()
                        ],
                    'party': party,
                    'has_party_logo': has_party_logo,
                    'office_place': office_place,
                    'element_id': element_id,
                    }

                if mp_positions:
                    person_data['positions'] = mp_positions
                    person_data['is_mp'] = True
                    mp_data.append(person_data)
                if mpl_positions:
                    person_data['positions'] = mpl_positions
                    person_data['is_mpl'] = True
                    mpl_data.append(person_data)

        context['form'] = LocationSearchForm(
            initial={'q': self.request.GET.get('q')}
        )
        return context


class LatLonDetailLocalView(LatLonDetailBaseView):
    template_name = 'south_africa/latlon_local_view.html'



class SAPlaceDetailView(PlaceDetailView):

    def get_context_data(self, **kwargs):
        """
        Get back the people for this place in separate lists so they can
        be displayed separately on the place detail page.
        """
        context = super(SAPlaceDetailView, self).get_context_data(**kwargs)

        for context_string, position_filter in (
                ('national_assembly_people',
                 {'organisation__slug': 'national-assembly',
                  'title__slug': 'member'}),
                ('ncop_people',
                 {'organisation__slug': 'ncop',
                  'title__slug': 'delegate'}),
                ('legislature_people',
                 {'organisation__kind__slug': 'provincial-legislature',
                  'title__slug': 'member'}),
        ):
            all_member_positions = self.object.all_related_positions(). \
                filter(**position_filter).select_related('person')
            current_positions = all_member_positions.currently_active()
            current_people = models.Person.objects.filter(position__in=current_positions).distinct()
            former_positions = all_member_positions.currently_inactive()

            context[context_string + '_count'] = current_people.count()
            context[context_string] = current_people
            context['former_' + context_string] = models.Person.objects.filter(
                position__in=former_positions
            ).distinct()

        context['other_people'] = (
            models.Person.objects
              .filter(position__place=self.object)
              .exclude(id__in=context['national_assembly_people'])
              .exclude(id__in=context['former_national_assembly_people'])
              .exclude(id__in=context['ncop_people'])
              .exclude(id__in=context['former_ncop_people'])
              .exclude(id__in=context['legislature_people'])
              .exclude(id__in=context['former_legislature_people'])
        )
        return context


class SAPlaceDetailSub(PlaceDetailSub):
    child_place_template = "south_africa/constituency_office_list_item.html"
    child_place_list_template = "south_africa/constituency_office_list.html"

    def get_context_data(self, **kwargs):
        context = super(SAPlaceDetailSub, self).get_context_data(**kwargs)

        context['child_place_template'] = self.child_place_template
        context['child_place_list_template'] = self.child_place_list_template
        context['subcontent_title'] = 'Constituency Offices'

        if self.object.kind.slug == 'province':
            context['child_places'] = (
                ZAPlace.objects
                .filter(kind__slug__in=CONSTITUENCY_OFFICE_PLACE_KIND_SLUGS)
                .filter(location__coveredby=self.object.mapit_area.polygons.collect())
                )

        return context

def key_position_sort_last_name(position):
    """Take a position and return a tuple for sorting it with.

    This is intended for use as the key attribute of .sort() or
    sorted() when sorting positions, so that the positions are sorted
    by the appropriate name of the associated person. It also needs to sort by
    the person's id so as to avoid mixing up people with the similar names,
    and additionally, it puts positions which are membership
    of a parliament at the top of the group of positions for a single
    person.
    """

    org_kind_slug = position.organisation.kind.slug
    title_slug = position.title and position.title.slug
    is_parliamentary = org_kind_slug == 'parliament'
    is_member = title_slug in ('member', 'delegate')

    return (
        position.person.sort_name,
        position.person.id,
        # False if the position is member or delegate to a parliament
        # (False sorts to before True)
        not(is_parliamentary and is_member),
        )

class SAOrganisationDetailView(CommentArchiveMixin, OrganisationDetailView):

    def get_context_data(self, **kwargs):
        context = super(SAOrganisationDetailView, self).get_context_data(**kwargs)

        if self.object.kind.slug == 'parliament':
            self.add_parliament_counts_to_context_data(context)

        # Sort the list of positions in an organisation by an approximation
        # of their holder's last name.
        context['positions'] = sorted(
            context['positions'].currently_active(),
            key=key_position_sort_last_name,
        )

        paginator = Paginator(context['positions'], 20)
        page = self.request.GET.get('page')
        try:
            context['positions'] = paginator.page(page)
        except PageNotAnInteger:
            context['positions'] = paginator.page(1)
        except EmptyPage:
            context['positions'] = paginator.page(paginator.num_pages)

        return context

    def add_parliament_counts_to_context_data(self, context):
        # Get all the currently active positions in the house:
        positions_in_house = models.Position.objects.filter(
            organisation=self.object). \
            select_related('person').currently_active()
        # Then find the distinct people who have those positions:
        people_in_house = set(p.person for p in positions_in_house)
        # Now find all the active party memberships for those people:
        party_counts = defaultdict(int)
        for current_party_position in models.Position.objects.filter(
            title__slug='member',
            organisation__kind__slug='party',
            person__in=people_in_house).currently_active(). \
            select_related('organisation'):
            party_counts[current_party_position.organisation] += 1
        parties = sorted(party_counts.keys(), key=lambda o: o.name)
        total_people = len(people_in_house)

        # Calculate the % of the house each party occupies.
        context['parties_counts_and_percentages'] = sorted(
            [
                (party,
                 party_counts[party],
                 (float(party_counts[party]) * 100) / total_people)
                for party in parties
            ],
            key=lambda x: x[1],
            reverse=True,
        )

        context['total_people'] =  total_people

    def get_template_names(self):
        if self.object.kind.slug == 'parliament':
            return [ 'south_africa/organisation_house.html' ]
        else:
            return super(SAOrganisationDetailView, self).get_template_names()


class SAOrganisationDetailSub(OrganisationDetailSub):
    sub_page = None

    def add_sub_page_context(self, context):
        pass

    def get_context_data(self, *args, **kwargs):
        context = super(SAOrganisationDetailSub, self).get_context_data(*args, **kwargs)

        self.add_sub_page_context(context)

        context['sorted_positions'] = sorted(
                context['sorted_positions'],
                key=key_position_sort_last_name)

        return context


class SAOrganisationDetailSubParty(SAOrganisationDetailSub):
    sub_page = 'party'

    def add_sub_page_context(self, context):

        context['party'] = get_object_or_404(models.Organisation,slug=self.kwargs['sub_page_identifier'])

        # We need to find any position where someone was a member
        # of the organisation and that membership overlapped with
        # their membership of the party, and mark whether that
        # time includes the current date.
        current_date = str(datetime.date.today())
        params = [current_date, current_date, current_date, current_date,
            self.object.id, context['party'].id]
        # n.b. "hp" = "house position", "pp" = "party position"
        all_positions = models.raw_query_with_prefetch(
            models.Position,
            '''
SELECT
    hp.*,
    (hp.sorting_start_date <= %s AND
     hp.sorting_end_date_high >= %s AND
     pp.sorting_start_date <= %s AND
     pp.sorting_end_date_high >= %s) AS current
FROM core_position hp, core_position pp
  WHERE hp.person_id = pp.person_id
    AND hp.organisation_id = %s
    AND pp.organisation_id = %s
    AND hp.sorting_start_date <= pp.sorting_end_date_high
    AND pp.sorting_start_date <= hp.sorting_end_date_high
''',
            params,
            (('person', ('alternative_names', 'images')),
             ('place', ()),
             ('organisation', ()),
             ('title', ())))

        current_person_ids = set(p.person_id for p in all_positions if p.current)

        if self.request.GET.get('all'):
            context['sorted_positions'] = all_positions

        elif self.request.GET.get('historic'):
            context['historic'] = True
            context['sorted_positions'] = [
                p for p in all_positions if p.person_id not in current_person_ids
            ]
        else:
            # Otherwise we're looking current positions:
            context['historic'] = True
            context['sorted_positions'] = [
                p for p in all_positions if p.current
            ]


class SAOrganisationDetailSubPeople(SAOrganisationDetailSub):
    sub_page = 'people'

    def add_sub_page_context(self, context):
        all_positions = self.object.position_set.all()
        context['historic_filter'] = False
        context['all_filter'] = False
        context['current_filter'] = False

        if self.request.GET.get('all'):
            context['all_filter'] = True
            context['sorted_positions'] = all_positions
        elif self.request.GET.get('historic'):
            context['historic_filter'] = True
            #FIXME - limited to members and delegates so that current members who are no longer officials are not displayed, but this
            #means that if a former member was an official this is not shown
            context['sorted_positions'] = all_positions.filter(Q(title__slug='member') | Q(title__slug='delegate')).currently_inactive()
        elif self.request.GET.get('historic'):
            context['historic_filter'] = True
            context['sorted_positions'] = all_positions.currently_inactive()
        else:
            context['current_filter'] = True
            context['sorted_positions'] = all_positions.currently_active()

        # Counts of positions relating to people whose name starts with each letter of the alphabet
        context['count_by_prefix'] = [
            (letter,
             context['sorted_positions']
             .filter(person__sort_name__istartswith=letter).count())
            for letter in string.ascii_uppercase
            ]

        person_name_prefix = self.kwargs.get('person_prefix')
        if person_name_prefix:
            context['sorted_positions'] = (
                context['sorted_positions']
                .filter(person__sort_name__istartswith=person_name_prefix)
                )

        context['current_name_prefix'] = person_name_prefix

        if self.object.slug == 'ncop':
            context['membertitle'] = 'delegate'
        else:
            context['membertitle'] = 'member'

class SAPersonDetail(PersonSpeakerMappingsMixin, PersonDetail):
    important_org_kind_slugs = ('national-executive', 'parliament', 'provincial-legislature')

    def get_recent_speeches_for_section(self, tags, limit=5):
        pombola_person = self.object
        sayit_speaker = self.pombola_person_to_sayit_speaker(pombola_person)

        if not sayit_speaker:
            # Without a speaker we can't find any speeches
            return Speech.objects.none()

        speeches = Speech.objects \
            .filter(tags__name__in=tags, speaker=sayit_speaker) \
            .order_by('-start_date', '-start_time')

        if limit:
            speeches = speeches[:limit]

        return speeches


    def get_tabulated_interests(self):
        interests = self.object.interests_register_entries.all()
        tabulated = {}

        release_content_type = ContentType.objects.get_for_model(Release)

        for entry in interests:
            release = entry.release
            category = entry.category

            if release.id not in tabulated:
                sources = models.InformationSource.objects.filter(
                    content_type=release_content_type,
                    object_id=release.id
                )

                tabulated[release.id] = {
                    'name': release.name,
                    'categories': {},
                    'informationsource': sources
                }

            if category.id not in tabulated[release.id]['categories']:
                tabulated[release.id]['categories'][category.id] = {
                    'name': category.name,
                    'headings': [],
                    'headingindex': {},
                    'headingcount': 1,
                    'entries': []
                }

            #create row list
            tabulated[release.id]['categories'][category.id]['entries'].append(
                ['']*(tabulated[entry.release.id]['categories'][entry.category.id]['headingcount']-1)
            )

            #loop through each 'cell' in the row
            for entrylistitem in entry.line_items.all():
                #if the heading for the column does not yet exist, create it
                if entrylistitem.key not in tabulated[entry.release.id]['categories'][entry.category.id]['headingindex']:
                    tabulated[release.id]['categories'][category.id]['headingindex'][entrylistitem.key] = tabulated[entry.release.id]['categories'][entry.category.id]['headingcount']-1
                    tabulated[release.id]['categories'][category.id]['headingcount']+=1
                    tabulated[release.id]['categories'][category.id]['headings'].append(entrylistitem.key)

                    #loop through each row that already exists to ensure lists are the same size
                    for (key, line) in enumerate(tabulated[release.id]['categories'][category.id]['entries']):
                        tabulated[entry.release.id]['categories'][entry.category.id]['entries'][key].append('')

                #record the 'cell' in the correct position in the row list
                tabulated[release.id]['categories'][category.id]['entries'][-1][tabulated[release.id]['categories'][category.id]['headingindex'][entrylistitem.key]] = entrylistitem.value

        ret = []

        for release_id, release_data in tabulated.items():
            release = Release.objects.get(pk=release_id)

            ret.append((release_data, release.date))

        ret.sort(key=lambda x: x[1], reverse=True)

        return ret

    def list_contacts(self, kind_slugs):
        return self.object.contacts.filter(kind__slug__in=kind_slugs).values_list(
            'value', flat=True)

    def get_former_parties(self, person):
        former_party_memberships = (
            person
            .position_set
            .all()
            .filter(
              # select the political party memberships
              ( Q(title__slug='member') & Q(organisation__kind__slug='party') )
            )
            .order_by('-start_date')
        )
        return models.Organisation.objects.filter(position__in=former_party_memberships).distinct()

    def store_or_get_pmg_member_id(self, scheme='za.org.pmg.api/member'):
        identifier = self.object.get_identifier(scheme)

        if not identifier:
            # First find the id of the person
            pa_link = urllib.quote(
                "http://www.pa.org.za/person/{}/".format(self.object.slug))

            url_fmt = "https://api.pmg.org.za/member/?filter[pa_link]={}"
            api_search_url = url_fmt.format(pa_link)
            try:
                search_resp = requests.get(
                    api_search_url, timeout=API_REQUESTS_TIMEOUT)
            except requests.exceptions.RequestException:
                raise AttendanceAPIDown
            search_data = json.loads(search_resp.text)

            if not search_data.get('count'):
                return None

            if search_data['count'] > 1:
                logger.error(
                    'Duplicate members at PMG with slug {} - SKIPPING'.format(self.object.slug))
                return None

            identifier = search_data['results'][0]['id']

            models.Identifier.objects.create(
                scheme=scheme,
                identifier=identifier,
                content_object=self.object,
                )

        return identifier

    def get_attendance_data_url(self, attendance_url_template="http://api.pmg.org.za/member/{}/attendance/"):
        identifier = self.store_or_get_pmg_member_id()

        if identifier:
            return attendance_url_template.format(identifier)

    def download_attendance_data(self):
        attendance_url = next_url = self.get_attendance_data_url()

        cache = caches['pmg_api']
        results = cache.get(attendance_url)

        if results is None:
            results = []
            while next_url:
                try:
                    resp = requests.get(next_url, timeout=API_REQUESTS_TIMEOUT)
                except requests.exceptions.RequestException:
                    raise AttendanceAPIDown

                data = json.loads(resp.text)
                results.extend(data.get('results'))

                next_url = data.get('next')

            cache.set(attendance_url, results)

        # Results are returned from the API most recent first, which
        # is convenient for us.
        return results

    def get_attendance_stats_raw(self, data):
        if not data:
            return {}

        attendance_by_year = {}

        for x in data:
            attendance = x['attendance']
            year = dateutil.parser.parse(x['meeting']['date']).year

            year_dict = attendance_by_year.setdefault(year, {})

            year_dict.setdefault(attendance, 0)
            year_dict[attendance] += 1

        return attendance_by_year

    def get_latest_meeting_urls(self, data):
        api_url_re = r'/committee-meeting/(\d+)/'
        meeting_url_template = 'https://pmg.org.za/committee-meeting/{}/'

        meetings = [x['meeting'] for x in data[:5]]

        results = []

        for meeting in meetings:
            api_url = meeting['url']
            path = urlsplit(api_url).path
            meeting_id = re.match(api_url_re, path).group(1)

            results.append(
                {'url': meeting_url_template.format(meeting_id),
                 'title': meeting['title'],
                 'committee_name': meeting['committee']['name'],
                 }
                )

        return results

    # Meanings of attendance field

    #  A:   Absent
    #  AP:  Absent with Apologies
    #  DE:  Departed Early
    #  L:   Arrived Late
    #  LDE: Arrived Late and Departed Early
    #  P:   Present

    def get_attendance_stats(self, attendance_by_year):
        present_values = set(('P', 'DE', 'L', 'LDE'))

        sorted_keys = sorted(attendance_by_year.keys(), reverse=True)

        return_data = []
        # year, attended, total, percentage
        for year in sorted_keys:
            year_dict = attendance_by_year[year]

            attendance = sum((year_dict[x] for x in year_dict if x in present_values))
            meeting_count = sum((year_dict[x] for x in year_dict))
            percentage = 100 * attendance / meeting_count
            return_data.append(
                {'year': year,
                 'attended': attendance,
                 'total': meeting_count,
                 'percentage': percentage,
                 }
                )

        return return_data

    def get_attendance_data_for_display(self):
        raw_data = self.download_attendance_data()
        attendance_by_year = self.get_attendance_stats_raw(raw_data)
        attendance_stats = self.get_attendance_stats(attendance_by_year)
        meeting_urls = self.get_latest_meeting_urls(raw_data)

        return attendance_stats, meeting_urls

    def get_context_data(self, **kwargs):
        context = super(SAPersonDetail, self).get_context_data(**kwargs)
        context['twitter_contacts'] = self.list_contacts(('twitter',))
        context['facebook_contacts'] = self.list_contacts(('facebook',))
        context['linkedin_contacts'] = self.list_contacts(('linkedin',))
        context['youtube_contacts'] = self.list_contacts(('youtube',))
        context['whoswhosa_contacts'] = self.list_contacts(('whos-who-sa',))
        # The email attribute of the person might also be duplicated
        # in a contact of type email, so create a set of email
        # addresses:
        context['email_contacts'] = set(self.list_contacts(('email',)))
        if self.object.email:
            context['email_contacts'].add(self.object.email)
        context['phone_contacts'] = self.list_contacts(('cell', 'voice'))
        context['fax_contacts'] = self.list_contacts(('fax',))
        context['address_contacts'] = self.list_contacts(('address',))

        orgs_from_important_positions = models.Organisation.objects.filter(
            kind__slug__in=self.important_org_kind_slugs,
            position__in=self.object.politician_positions(),
            )

        former_important_positions = (
            self.object.position_set
            .all()
            .political()
            .previous()
            .filter(organisation__kind__slug__in=self.important_org_kind_slugs)
            )

        former_orgs_from_important_positions = models.Organisation.objects.filter(
            position__in=former_important_positions,
            ).exclude(id__in=orgs_from_important_positions)

        context['organizations_from_important_positions'] = \
            orgs_from_important_positions.distinct()

        context['organizations_from_former_important_positions'] = \
            former_orgs_from_important_positions.distinct()

        # FIXME - the titles used here will need to be checked and fixed.
        context['hansard']   = self.get_recent_speeches_for_section(('hansard',), limit=2)
        context['committee'] = self.get_recent_speeches_for_section(('committee',), limit=5)
        context['question']  = self.get_recent_speeches_for_section(('question', 'answer'), limit=3)

        context['interests'] = self.get_tabulated_interests()
        if self.object.date_of_death != None:
            context['former_parties'] = self.get_former_parties(self.object)

        try:
            context['attendance'], context['latest_meetings_attended'] = self.get_attendance_data_for_display()
        except AttendanceAPIDown:
            context['attendance'] = context['latest_meetings_attended'] = 'UNAVAILABLE'

        return context


class SASearchView(SearchBaseView):

    def __init__(self, *args, **kwargs):
        super(SASearchView, self).__init__(*args, **kwargs)
        del self.search_sections['speeches']
        self.search_sections['questions'] = {
            'model': Speech,
            'title': 'Questions and Answers',
            'filter': {
                'args': [SQ(tags='question') | SQ(tags='answer')],
            }
        }
        self.search_sections['committee'] = {
            'model': Speech,
            'title': 'Committee Appearance',
            'filter': {
                'kwargs': {
                    'tags': 'committee'
                }
            }
        }
        self.search_sections['hansard'] = {
            'model': Speech,
            'title': 'Hansard',
            'filter': {
                'kwargs': {
                    'tags': 'hansard'
                }
            }
        }
        self.section_ordering.remove('speeches')
        self.section_ordering += [
            'questions', 'committee', 'hansard'
        ]


class SANewsletterPage(InfoPageView):
    template_name = 'south_africa/info_newsletter.html'

class SASpeakerRedirectView(RedirectView):

    permanent = False

    # see also SAPersonDetail for mapping in opposite direction
    def get_redirect_url(self, *args, **kwargs):
        try:
            speaker = Speaker.objects.get(pk=kwargs['pk'])
            return reverse(
                'person',
                args=(speaker.pombola_link.pombola_person.slug,)
            )
        except ObjectDoesNotExist:
            raise Http404

class SASpeechesIndex(NamespaceMixin, TemplateView):
    template_name = 'south_africa/hansard_index.html'
    top_section_name='Hansard'
    sections_to_show = 25
    section_parent_field = 'parent__parent__parent__parent'

    def get_context_data(self, **kwargs):
        context = super(SASpeechesIndex, self).get_context_data(**kwargs)
        self.page = self.request.GET.get('page')

        # Get the top level section, or 404
        top_section = get_object_or_404(Section, heading=self.top_section_name, parent=None)
        context['show_lateness_warning'] = (self.top_section_name == 'Hansard')

        # As we know that the hansard section structure is
        # "Hansard" -> yyyy -> mm -> dd -> section -> subsection -> [speeches]
        # we can create very specific queries to fetch the sections
        # then the subsections containing the speeches themselves.
        #
        # The parent_sections form the headings which are expanded by javascript
        # to reveal the debate_sections
        #
        # FIXME ideally we'd have start_date for sections rather than
        # having to get MAX('start_date') from the speeches table

        # exclude sections without subsections and
        # with subsections that have no speeches
        section_filter = {
            self.section_parent_field : top_section,
            'children__speech__id__isnull' : False,
            'children__id__isnull' : False
        }

        # get a list of all the section headings
        all_parent_section_headings = Section \
              .objects \
              .filter(**section_filter) \
              .values('heading') \
              .distinct() \
              .annotate(latest_start_date=Max('children__speech__start_date')) \
              .order_by('-latest_start_date')

        # use Paginator to cut this down to the sections for the current page
        paginator = Paginator(all_parent_section_headings, self.sections_to_show)
        try:
            parent_section_headings = paginator.page(self.page)
        except PageNotAnInteger:
            parent_section_headings = paginator.page(1)
        except EmptyPage:
            parent_section_headings = paginator.page(paginator.num_pages)

        # get the sections for the current page in date order
        headings = list(section['heading'] for section in parent_section_headings)
        section_filter['heading__in'] = headings
        parent_sections = Section \
              .objects \
              .values('id', 'heading') \
              .filter(**section_filter) \
              .annotate(latest_start_date=Max('children__speech__start_date')) \
              .order_by('-latest_start_date', 'heading')

        # get the subsections based on the relevant section ids
        # exclude those with blank headings as we have no way of linking to them
        parent_ids = list(section['id'] for section in parent_sections)
        debate_sections = Section \
            .objects \
            .filter(parent_id__in=parent_ids, speech__id__isnull=False) \
            .annotate(start_order=Min('speech__id'), speech_start_date=Max('speech__start_date'), speech_count=Count('speech__id')) \
            .exclude(heading='') \
            .order_by('-speech_start_date', 'parent__heading', 'start_order')

        context['entries'] = debate_sections
        context['page_obj'] = parent_section_headings
        return context

class SAHansardIndex(SASpeechesIndex):
    template_name = 'south_africa/hansard_index.html'
    top_section_name='Hansard'
    section_parent_field = 'parent__parent__parent__parent'
    sections_to_show = 15

class SACommitteeIndex(SASpeechesIndex):
    template_name = 'south_africa/hansard_index.html'
    top_section_name='Committee Minutes'
    section_parent_field = 'parent__parent'
    sections_to_show = 25

def questions_section_sort_key(section):
    """This function helps to sort question sections

    The intention is to have questions for the President first, then
    Deputy President, then offices associated with the presidency,
    then questions to ministers sorted by the name of the ministry,
    and finally anything else sorted just on the heading.

    >>> questions_section_sort_key(Section(heading="for the President"))
    'AAAfor the President'
    >>> questions_section_sort_key(Section(heading="ask the deputy president"))
    'BBBask the deputy president'
    >>> questions_section_sort_key(Section(heading="about the Presidency"))
    'CCCabout the Presidency'
    >>> questions_section_sort_key(Section(heading="Questions asked to Minister for Foo"))
    'DDDFoo'
    >>> questions_section_sort_key(Section(heading="Questions asked to the Minister for Bar"))
    'DDDBar'
    >>> questions_section_sort_key(Section(heading="Questions asked to Minister of Baz"))
    'DDDBaz'
    >>> questions_section_sort_key(Section(heading="Minister of Quux"))
    'DDDQuux'
    >>> questions_section_sort_key(Section(heading="Random"))
    'MMMRandom'
    """
    heading = section.heading
    if re.search(r'(?i)Deputy\s+President', heading):
        return "BBB" + heading
    if re.search(r'(?i)President', heading):
        return "AAA" + heading
    if re.search(r'(?i)Presidency', heading):
        return "CCC" + heading
    stripped_heading = heading
    for regexp in (r'^(?i)Questions\s+asked\s+to\s+(the\s+)?Minister\s+(of|for(\s+the)?)\s+',
                   r'^(?i)Minister\s+(of|for(\s+the)?)\s+'):
        stripped_heading = re.sub(regexp, '', stripped_heading)
    if stripped_heading == heading:
        # i.e. it wasn't questions for a minister
        return "MMM" + heading
    return "DDD" + stripped_heading

class SAQuestionIndex(TemplateView):
    template_name = 'south_africa/question_index.html'

    def get_context_data(self, **kwargs):
        context = super(SAQuestionIndex, self).get_context_data(**kwargs)

        context['ministers'] = []
        ministers = Section.objects.filter(parent__heading='Questions')
        ministers = sorted(ministers, key=questions_section_sort_key)
        for minister in ministers:
            context['ministers'].append({
                'title': minister.title.replace('Questions asked to the ',''),
                'slug': minister.slug
                })

        context['orderby'] = 'recentquestions'
        context['minister'] = 'all'
        context['q'] = ''

        #set filter values
        for key in ('orderby', 'minister', 'q'):
            if key in self.request.GET:
                context[key] = self.request.GET[key]

        if not context['orderby'] in ['recentquestions', 'recentanswers']:
            context['orderby'] = 'recentquestions'

        search_result_sections = []

        if context['q'] != '':
            #using a RelatedSearchQuerySet seems to result in fewer
            #queries, although the same results can be achieved with a
            #SearchQuerySet
            query = RelatedSearchQuerySet().models(Speech)
            query = query.filter(
                tags__name__in = ['question', 'answer'],
                content=AutoQuery(context['q']),
                ).load_all()

            all_speeches = Speech.objects.all().filter(
                tags__name__in = ['question', 'answer'])

            if context['minister'] != 'all':
                all_speeches = all_speeches.filter(
                    section__parent__slug=context['minister']
                    )

            query = query.load_all_queryset(Speech, all_speeches)

            for result in query:
                search_result_sections.append(result.object.section.id)

        sections = Section \
            .objects \
            .filter(
                parent__parent__heading='Questions'
            ) \
            .select_related('parent__heading') \
            .prefetch_related('speech_set') \
            .annotate(
                earliest_date=Min('speech__start_date'),
                smallest_id=Min('speech__id'),
                number_speeches=Count('speech'),
                latest_date=Max('speech__start_date'),
            )

        if context['minister'] != 'all':
            sections = sections.filter(parent__slug=context['minister'])

        if len(search_result_sections)>0:
            sections = sections.filter(id__in=search_result_sections)

        if context['orderby'] == 'recentanswers':
            sections = sections.filter(number_speeches__gt=1).order_by(
                '-latest_date',
                '-smallest_id'
            )
        else:
            sections = sections.order_by(
                '-earliest_date',
                '-smallest_id'
            )

        paginator = Paginator(sections, 10)
        page = self.request.GET.get('page')

        try:
            sections = paginator.page(page)
        except PageNotAnInteger:
            sections = paginator.page(1)
        except EmptyPage:
            sections = paginator.page(paginator.num_pages)

        context['paginator'] = sections

        #format questions and answers for the template
        questions = []
        for section in sections:
            question = section.speech_set.all()[0]
            question.questionto = section.parent.heading.replace('Questions asked to the ','')
            question.questionto_slug = section.parent.slug

            #assume if there is more than one speech that the question is answered
            if len(section.speech_set.all())>1:
                question.answer = section \
                    .speech_set \
                    .all()[len(section.speech_set.all())-1]

                #extract the actual reply from the reply text (replies
                #often include the original question and other text,
                #such as a letterhead)
                text = question.answer.text.split('REPLY')
                if len(text)>1:
                    question.answer.text = text[len(text)-1]
                    if question.answer.text[0]==':':
                        question.answer.text = question.answer.text[1:]

            questions.append(question)

        context['speeches'] = questions

        return context


class OldSpeechRedirect(RedirectView):

    """Redirects from an old speech URL to the current one"""

    permanent = True

    def get_redirect_url(self, *args, **kwargs):
        try:
            speech_id = int(kwargs['pk'])
            speech = Speech.objects.get(pk=speech_id)
            return reverse(
                'speeches:speech-view',
                args=[speech_id])
        except (ValueError, Speech.DoesNotExist):
            raise Http404


class OldSectionRedirect(RedirectView):

    """Redirects from an old section URL to the current one"""

    permanent = True

    def get_redirect_url(self, *args, **kwargs):
        try:
            section_id = int(kwargs['pk'])
            section = Section.objects.get(pk=section_id)
            return reverse(
                'speeches:section-view',
                args=[section.get_path])
        except (ValueError, Section.DoesNotExist):
            raise Http404


class SAPersonAppearanceView(PersonSpeakerMappingsMixin, TemplateView):

    template_name = 'south_africa/person_appearances.html'

    def get_context_data(self, **kwargs):
        context = super(SAPersonAppearanceView, self).get_context_data(**kwargs)

        # Extract slug and tag provided on url
        person_slug = self.kwargs['person_slug']
        speech_tag  = self.kwargs['speech_tag']

        # Find (or 404) matching objects
        person = get_object_or_404(models.Person, slug=person_slug)
        tag    = get_object_or_404(Tag, name=speech_tag)

        # SayIt speaker is different to core.Person, Load the speaker
        speaker = self.pombola_person_to_sayit_speaker(person)

        # Load the speeches. Pagination is done in the template
        speeches = Speech.objects \
            .filter(tags=tag, speaker=speaker) \
            .order_by('-start_date', '-start_time')

        # Store person as 'object' for the person_base.html template
        context['object']  = person
        context['speeches'] = speeches

        # Add a hardcoded section-view url name to use for the speeches. Would
        # rather this was not hardcoded here but seems hard to avoid.
        # speech_tag not known. Use 'None' for template default instead
        context['section_url'] = None

        return context


def should_redirect_to_source(section):
    # If this committee is descended from the Committee Minutes
    # section, redirect to the source transcript on the PMG website:
    root_section = section.get_ancestors[0]
    return root_section.slug == 'committee-minutes'


class SASpeechView(SpeechView):

    def get(self, request, *args, **kwargs):
        speech = self.object = self.get_object()
        try_redirect = should_redirect_to_source(speech.section)
        if try_redirect and speech.source_url:
            return redirect(speech.source_url)
        context = self.get_context_data(object=speech)
        return self.render_to_response(context)

    def get_queryset(self):
        return Speech.objects.all()

class SASectionView(SectionView):

    def get(self, request, *args, **kwargs):
        try:
            self.object = self.get_object()
        except Http404:
            #if not found check if any sections have been redirected by
            #considering the deepest section first
            full_slug = self.kwargs.get('full_slug', None)
            slugs = full_slug.split('/')
            for i in range(len(slugs), 0, -1):
                try:
                    check_slug = '/'.join(slugs[:i])
                    sr = SlugRedirect.objects.get(
                        content_type=ContentType.objects.get_for_model(Section),
                        old_object_slug=check_slug
                    )
                    new_url = '/' + full_slug.replace(
                        check_slug,
                        sr.new_object.get_path,
                        1)
                    break
                except SlugRedirect.DoesNotExist:
                    pass

            try:
                return redirect(new_url)
            except NameError:
                raise Http404

        if should_redirect_to_source(self.object):
            # Find a URL to redirect to; try to get any speech in the
            # section with a non-blank source URL. FIXME: after
            # switching to Django 1.6, .first() will make this simpler.
            speeches = self.object.speech_set.exclude(source_url='')[:1]
            if speeches:
                speech = speeches[0]
                redirect_url = speech.source_url
                if redirect_url:
                    return redirect(redirect_url)

        if self.object.get_ancestors[0].slug == 'questions':
            self.template_name = 'south_africa/question_section_detail.html'

            if len(self.object.get_ancestors) == 2:
                #level two question sections are displayed on the
                #landing page
                redirect_url = '/question/?minister=%s&orderby=recentquestions' % \
                    (self.object.slug)
                return redirect(redirect_url)

        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)

class SAElectionOverviewMixin(TemplateView):
    election_type = ''

    def get_context_data(self, **kwargs):
        context = super(SAElectionOverviewMixin, self).get_context_data(**kwargs)

        # XXX Does this need to only be parties standing in the election?
        party_kind = models.OrganisationKind.objects.get(slug='party')
        context['party_list'] = models.Organisation.objects.filter(
            kind=party_kind).order_by('name')

        province_kind = models.PlaceKind.objects.get(slug='province')
        context['province_list'] = models.Place.objects.filter(
            kind=province_kind).order_by('name')

        election_year = self.kwargs['election_year']

        # All lists of candidates share this kind
        election_list = models.OrganisationKind.objects.get(slug='election-list')

        context['election_year'] = election_year

        # Build the right election list names
        election_list_suffix = '-national-election-list-' + election_year

        # All the running parties live in this list
        running_parties = []

        # Find the slugs of all parties taking part in the national election
        national_running_party_lists = models.Organisation.objects.filter(
            kind=election_list,
            slug__endswith=election_list_suffix
        ).order_by('name')

        # Loop through national sets and extract slugs
        for l in national_running_party_lists:
            party_slug = l.slug.replace(election_list_suffix, '')
            if not party_slug in running_parties:
                running_parties.append(party_slug)

        # I am so sorry for this.
        context['running_party_list'] = []
        for running_party in running_parties:
            context['running_party_list'].append(models.Organisation.objects.get(slug=running_party))

        return context

class SAElectionOverviewView(SAElectionOverviewMixin):
    template_name = 'south_africa/election/overview.html'

class SAElectionStatisticsView(SAElectionOverviewMixin):
    template_name = 'south_africa/election/statistics.html'
    election_type = 'statistics'

    def get_context_data(self, **kwargs):
        context = super(SAElectionStatisticsView, self).get_context_data(**kwargs)

        context['current_mps'] = {'all': {}, 'byparty': []}

        election_2014_query = (
            Q(person__position__organisation__slug__contains='national-election-list-2014') |
            (Q(person__position__organisation__slug__contains='election-list-2014') &
             Q(person__position__organisation__slug__contains='regional'))
            )

        current_na_positions = (
            models.Organisation.objects
            .get(slug='national-assembly')
            .position_set.all()
            .currently_active()
            )

        context['current_mps']['all']['current'] = (
            current_na_positions
            .order_by('person__id')
            .distinct('person__id')
            .count()
            )
        context['current_mps']['all']['rerunning'] = (
            current_na_positions
            .filter(election_2014_query)
            .order_by('person__id')
            .distinct('person__id')
            .count()
            )
        context['current_mps']['all']['percent_rerunning'] = 100 * context['current_mps']['all']['rerunning'] / context['current_mps']['all']['current']

        #find the number of current MPs running for office per party
        for p in context['party_list']:
            party = models.Organisation.objects.get(slug=p.slug)
            current = (
                current_na_positions
                .filter(person__position__organisation__slug=p.slug)
                .order_by('person__id')
                .distinct('person__id')
                .count()
                )
            rerunning = (
                current_na_positions
                .filter(person__position__organisation__slug=p.slug)
                .filter(election_2014_query)
                .order_by('person__id')
                .distinct('person__id')
                .count()
                )
            if current:
                percent = 100 * rerunning / current
                context['current_mps']['byparty'].append({'party': party, 'current': current, 'rerunning': rerunning, 'percent_rerunning': percent})


        #find individuals who appear to have switched party
        context['people_new_party'] = []
        people = models.Person.objects.filter(position__organisation__kind__slug='party', position__title__slug='member').annotate(num_parties=Count('position')).filter(num_parties__gt=1)

        for person in people:
            #check whether the person is a candidate - there is probably be a cleaner way to do this in the initial query
            person_list = person.position_set.all().filter(organisation__slug__contains='election-list-2014')
            if person_list:
                context['people_new_party'].append({
                    'person': person,
                    'current_positions': person.position_set.all().filter(organisation__kind__slug='party').currently_active(),
                    'former_positions': person.position_set.all().filter(organisation__kind__slug='party').currently_inactive(),
                    'person_list': person_list
                })

        return context

class SAElectionNationalView(SAElectionOverviewMixin):
    template_name = 'south_africa/election/national.html'
    election_type = 'national'

class SAElectionProvincialView(SAElectionOverviewMixin):
    template_name = 'south_africa/election/provincial.html'
    election_type = 'provincial'

class SAElectionPartyCandidatesView(TemplateView):

    template_name = 'south_africa/election_candidates_party.html'

    election_type = 'national'

    def get_context_data(self, **kwargs):
        context = super(SAElectionPartyCandidatesView, self).get_context_data(**kwargs)

        # These are common bits
        election_year = self.kwargs['election_year']
        election_type = self.election_type

        context['election_year'] = election_year
        context['election_type'] = election_type

        # Details from the URI
        if 'party_name' in self.kwargs:
            party_name = self.kwargs['party_name']
        else:
            party_name = None

        if 'province_name' in self.kwargs:
            province_name = self.kwargs['province_name']
            # Also get the province object, so we can use its details
            province_kind = models.PlaceKind.objects.get(slug='province')
            context['province'] = models.Place.objects.get(
                kind=province_kind,
                slug=province_name)
        else:
            province_name = None

        # All lists of candidates share this kind
        election_list = models.OrganisationKind.objects.get(slug='election-list')

        # Build the right election list name
        election_list_name = party_name

        if election_type == 'provincial':
            election_list_name += '-provincial'
            election_list_name += '-' + province_name
        elif election_type == 'national' and province_name is not None:
            election_list_name += '-regional'
            election_list_name += '-' + province_name
        else:
            election_list_name += '-national'

        election_list_name += '-election-list'
        election_list_name += '-' + election_year

        # This is a party template, so get the party
        context['party'] = get_object_or_404(models.Organisation, slug=party_name)

        # Now go get the party's election list (assuming it exists)
        election_list = get_object_or_404(models.Organisation,
            slug=election_list_name
        )

        candidates = election_list.position_set.select_related('title').all()

        context['party_election_list'] = sorted(candidates,key=lambda x:
            int(re.match('\d+', x.title.name).group())
        )

        # Grab a list of provinces in which the party is actually running
        # Only relevant for national election non-province party lists
        if election_type == 'national' and province_name is None:

            # Find the lists of regional candidates for this party
            party_election_lists_startwith = party_name + '-regional-'
            party_election_lists_endwith = '-election-list-' + election_year

            party_election_lists = models.Organisation.objects.filter(
                slug__startswith=party_election_lists_startwith,
                slug__endswith=party_election_lists_endwith
            ).order_by('name')

            # Loop through lists and extract province slugs
            party_provinces = []
            for l in party_election_lists:
                province_slug = l.slug.replace(party_election_lists_startwith, '')
                province_slug = province_slug.replace(party_election_lists_endwith, '')
                party_provinces.append(province_slug)

            context['province_list'] = models.Place.objects.filter(
                kind__slug='province',
                slug__in=party_provinces
            )

        return context

class SAElectionProvinceCandidatesView(TemplateView):

    template_name = 'south_africa/election_candidates_province.html'

    election_type = 'national'

    def get_context_data(self, **kwargs):
        context = super(SAElectionProvinceCandidatesView, self).get_context_data(**kwargs)

        # These are common bits
        election_year = self.kwargs['election_year']
        election_type = self.election_type

        context['election_year'] = election_year
        context['election_type'] = election_type

        # Details from the URI
        if 'party_name' in self.kwargs:
            party_name = self.kwargs['party_name']
        else:
            party_name = None

        # The province name should always exist
        province_name = self.kwargs['province_name']

        # All lists of candidates share this kind
        election_list = models.OrganisationKind.objects.get(slug='election-list')

        # Build the right election list name
        election_list_name = ''

        if election_type == 'provincial':
            election_list_name += '-provincial'
            election_list_name += '-' + province_name
        elif election_type == 'national' and province_name is not None:
            election_list_name += '-regional'
            election_list_name += '-' + province_name

        election_list_name += '-election-list'
        election_list_name += '-' + election_year

        # Go get all the election lists!
        election_lists = models.Organisation.objects.filter(
            slug__endswith=election_list_name
        ).order_by('name')

        # Loop round each election list so we can go do other necessary queries
        context['province_election_lists'] = []

        for election_list in election_lists:

            # Get the party data, finding the raw party slug from the list name
            party = models.Organisation.objects.get(
                slug=election_list.slug.replace(election_list_name, '')
            )

            # Get the candidates data for that list
            candidate_list = election_list.position_set.select_related('title').all()

            candidates = sorted(candidate_list, key=lambda x:
                int(re.match('\d+', x.title.name).group())
            )

            context['province_election_lists'].append({
                'party': party,
                'candidates': candidates
            })

        # Get the province object, so we can use its details
        province_kind = models.PlaceKind.objects.get(slug='province')
        context['province'] = models.Place.objects.get(
            kind=province_kind,
            slug=province_name)

        return context

class SAMembersInterestsIndex(TemplateView):
    template_name = "interests_register/index.html"

    def get_context_data(self, **kwargs):
        context = {}

        #get the data for the form
        kind_slugs = (
            'parliament',
            'executive',
            'joint-committees',
            'ncop-committees',
            'ad-hoc-committees',
            'national-assembly-committees'
        )
        context['categories'] = Category.objects.all()
        context['parties'] = models.Organisation.objects.filter(kind__slug='party')
        context['organisations'] = models.Organisation.objects \
            .filter(kind__slug__in=kind_slugs) \
            .order_by('kind__id','name')
        context['releases'] = Release.objects.all()

        #set filter values
        for key in ('display', 'category', 'party', 'organisation', 'release'):
            context[key] = 'all'
            if key in self.request.GET:
                context[key] = self.request.GET[key]

        try:
            if context['release']!='all':
                release = Release.objects.get(slug=context['release'])
                context['release_id'] = release.id

            if context['category']!='all':
                categorylookup = Category.objects.get(slug=self.request.GET['category'])
                context['category_id'] = categorylookup.id
        except ObjectDoesNotExist:
            return context

        #complete view - declarations for multiple people in multiple categories
        if context['display']=='all' and context['category']=='all':
            context = self.get_complete_view(context)

        #section view - data for multiple people in different categories
        elif context['display']=='all' and context['category']!='all':
            context = self.get_section_view(context)

        #numberbyrepresentative view - number of declarations per person per category
        elif context['display']=='numberbyrepresentative':
            context = self.get_number_by_representative_view(context)

        #numberbysource view - number of declarations by source per category
        elif context['display']=='numberbysource':
            context = self.get_numberbysource_view(context)

        return context

    def get_complete_view(self, context):

        release_content_type = ContentType.objects.get_for_model(Release)

        #complete view - declarations for multiple people in multiple categories
        context['layout'] = 'complete'

        when = datetime.date.today()
        now_approx = repr(ApproximateDate(year=when.year, month=when.month, day=when.day))

        people = Entry.objects.order_by(
            'person__legal_name',
            'release__date'
        ).distinct(
            'person__legal_name',
            'release__date'
        )

        if context['release']!='all':
            people = people.filter(release=context['release_id'])

        if context['party']!='all':
            people = people.filter(
                Q(person__position__end_date__gte=now_approx) | Q(person__position__end_date=''),
                person__position__organisation__slug=context['party'],
                person__position__start_date__lte=now_approx)

        if context['organisation']!='all':
            people = people.filter(
                Q(person__position__end_date__gte=now_approx) | Q(person__position__end_date=''),
                person__position__organisation__slug=context['organisation'],
                person__position__start_date__lte=now_approx)

        paginator = Paginator(people, 10)
        page = self.request.GET.get('page')

        try:
            people_paginated = paginator.page(page)
        except PageNotAnInteger:
            people_paginated = paginator.page(1)
        except EmptyPage:
            people_paginated = paginator.page(paginator.num_pages)

        context['paginator'] = people_paginated

        #tabulate the data
        data = []
        for entry_person in people_paginated:
            person = entry_person.person
            year = entry_person.release.date.year
            person_data = []
            person_categories = person.interests_register_entries.filter(
                release=entry_person.release
            ).order_by(
                'category__id'
            ).distinct(
                'category__id'
            )

            sources = models.InformationSource.objects.filter(
                content_type=release_content_type,
                object_id=entry_person.release.id
            )
            if sources:
                source_url = sources[0].source
            else:
                source_url = ''

            for cat in person_categories:
                entries = person.interests_register_entries.filter(
                    category=cat.category,
                    release=entry_person.release)

                headers = []
                headers_index = {}
                cat_data = []
                for entry in entries:
                    row = ['']*len(headers)

                    for entrylineitem in entry.line_items.all():
                        if entrylineitem.key not in headers:
                            headers_index[entrylineitem.key] = len(headers)
                            headers.append(entrylineitem.key)
                            row.append('')

                        row[headers_index[entrylineitem.key]] = entrylineitem.value

                    cat_data.append(row)

                person_data.append({
                    'category': cat.category,
                    'headers': headers,
                    'data': cat_data})
            data.append({
                'person': person,
                'data': person_data,
                'year': year,
                'source_url': source_url})

        context['data'] = data

        return context

    def get_section_view(self, context):
        release_content_type = ContentType.objects.get_for_model(Release)

        #section view - data for multiple people in different categories
        context['layout'] = 'section'

        when = datetime.date.today()
        now_approx = repr(ApproximateDate(year=when.year, month=when.month, day=when.day))

        entries = Entry.objects.select_related(
            'person',
            'category'
        ).all().filter(
            category__id=context['category_id']
        ).order_by(
            'person',
            'category'
        )

        if context['release']!='all':
            entries = entries.filter(release=context['release_id'])

        if context['party']!='all':
            entries = entries.filter(
                Q(person__position__end_date__gte=now_approx) | Q(person__position__end_date=''),
                person__position__organisation__slug=context['party'],
                person__position__start_date__lte=now_approx)

        if context['organisation']!='all':
            entries = entries.filter(
                Q(person__position__end_date__gte=now_approx) | Q(person__position__end_date=''),
                person__position__organisation__slug=context['organisation'],
                person__position__start_date__lte=now_approx)

        paginator = Paginator(entries, 25)
        page = self.request.GET.get('page')

        try:
            entries_paginated = paginator.page(page)
        except PageNotAnInteger:
            entries_paginated = paginator.page(1)
        except EmptyPage:
            entries_paginated = paginator.page(paginator.num_pages)

        headers = ['Year', 'Person', 'Type']
        headers_index = {'Year': 0, 'Person': 1, 'Type': 2}
        data = []
        for entry in entries_paginated:
            sources = models.InformationSource.objects.filter(
                content_type=release_content_type,
                object_id=entry.release.id
            )
            if sources:
                source_url = sources[0].source
            else:
                source_url = ''

            entry.release.source_url = source_url

            row = ['']*len(headers)
            row[0] = entry.release
            row[1] = entry.person
            row[2] = entry.category.name

            for entrylineitem in entry.line_items.all():
                if entrylineitem.key not in headers:
                    headers_index[entrylineitem.key] = len(headers)
                    headers.append(entrylineitem.key)
                    row.append('')

                row[headers_index[entrylineitem.key]] = entrylineitem.value

            data.append(row)

        context['data'] = data
        context['headers'] = headers
        context['paginator'] = entries_paginated

        return context

    def get_number_by_representative_view(self, context):
        release_content_type = ContentType.objects.get_for_model(Release)

        #numberbyrepresentative view - number of declarations per person per category
        context['layout'] = 'numberbyrepresentative'

        #custom sql used as
        #Entry.objects.values('category', 'release', 'person').annotate(c=Count('id')).order_by('-c')
        #returns a ValueQuerySet with foreign keys, not model instances
        if context['category']=='all' and context['release']=='all':
            data = Entry.objects.raw(
                '''SELECT max(id) as id, category_id, release_id, person_id,
                count(*) as c FROM "interests_register_entry"
                GROUP BY category_id, release_id, person_id ORDER BY c DESC''',
                [])
        elif context['category'] == 'all':
            data = Entry.objects.raw(
                '''SELECT max(id) as id, category_id, release_id, person_id,
                count(*) as c FROM "interests_register_entry"
                WHERE "interests_register_entry"."release_id" = %s
                GROUP BY category_id, release_id, person_id ORDER BY c DESC''',
                [context['release_id']])
        elif context['category'] != 'all' and context['release']=='all':
            data = Entry.objects.raw(
                '''SELECT max(id) as id, category_id, release_id, person_id,
                count(*) as c FROM "interests_register_entry"
                WHERE "interests_register_entry"."category_id" = %s
                GROUP BY category_id, release_id, person_id ORDER BY c DESC''',
                [context['category_id']])
        else:
            data = Entry.objects.raw(
                '''SELECT max(id) as id, category_id, release_id, person_id,
                count(*) as c FROM "interests_register_entry"
                WHERE "interests_register_entry"."category_id" = %s
                AND "interests_register_entry"."release_id" = %s
                GROUP BY category_id, release_id, person_id ORDER BY c DESC''',
                [context['category_id'], context['release_id']])

        paginator = Paginator(data, 20)
        paginator._count = len(list(data))
        page = self.request.GET.get('page')

        try:
            data_paginated = paginator.page(page)
        except PageNotAnInteger:
            data_paginated = paginator.page(1)
        except EmptyPage:
            data_paginated = paginator.page(paginator.num_pages)

        for row in data_paginated:
            sources = models.InformationSource.objects.filter(
                content_type=release_content_type,
                object_id=row.release.id
            )
            if sources:
                source_url = sources[0].source
            else:
                source_url = ''

            row.release.source_url = source_url

        context['data'] = data_paginated

        return context

    def get_numberbysource_view(self, context):
        release_content_type = ContentType.objects.get_for_model(Release)

        #numberbysource view - number of declarations by source per category
        context['layout'] = 'numberbysource'
        if context['category']=='all' and context['release']=='all':
            data = Entry.objects.raw(
                '''SELECT max("interests_register_entrylineitem"."id") as id,
                value, count(*) as c, release_id, category_id
                FROM "interests_register_entrylineitem"
                INNER JOIN "interests_register_entry"
                ON ("interests_register_entrylineitem"."entry_id"
                = "interests_register_entry"."id")
                WHERE ("interests_register_entrylineitem"."key" = 'Source')
                GROUP BY value, release_id, category_id ORDER BY c DESC''',
                [])
        elif context['category'] == 'all':
            data = Entry.objects.raw(
                '''SELECT max("interests_register_entrylineitem"."id") as id,
                value, count(*) as c, release_id, category_id
                FROM "interests_register_entrylineitem"
                INNER JOIN "interests_register_entry"
                ON ("interests_register_entrylineitem"."entry_id"
                = "interests_register_entry"."id")
                WHERE ("interests_register_entrylineitem"."key" = 'Source'
                AND "interests_register_entry"."release_id" = %s)
                GROUP BY value, release_id, category_id ORDER BY c DESC''',
                [context['release_id']])
        elif context['release']=='all':
            data = Entry.objects.raw(
                '''SELECT max("interests_register_entrylineitem"."id") as id,
                value, count(*) as c, release_id, category_id
                FROM "interests_register_entrylineitem"
                INNER JOIN "interests_register_entry"
                ON ("interests_register_entrylineitem"."entry_id"
                = "interests_register_entry"."id")
                WHERE ("interests_register_entry"."category_id" = %s
                AND "interests_register_entrylineitem"."key" = 'Source')
                GROUP BY value, release_id, category_id ORDER BY c DESC''',
                [context['category_id']])
        else:
            data = Entry.objects.raw(
                '''SELECT max("interests_register_entrylineitem"."id") as id,
                value, count(*) as c, release_id, category_id
                FROM "interests_register_entrylineitem"
                INNER JOIN "interests_register_entry"
                ON ("interests_register_entrylineitem"."entry_id"
                = "interests_register_entry"."id")
                WHERE ("interests_register_entry"."category_id" = %s
                AND "interests_register_entrylineitem"."key" = 'Source'
                AND "interests_register_entry"."release_id" = %s)
                GROUP BY value, release_id, category_id ORDER BY c DESC''',
                [context['category_id'], context['release_id']])

        paginator = Paginator(data, 20)
        paginator._count = len(list(data))
        page = self.request.GET.get('page')

        try:
            data_paginated = paginator.page(page)
        except PageNotAnInteger:
            data_paginated = paginator.page(1)
        except EmptyPage:
            data_paginated = paginator.page(paginator.num_pages)

        for row in data_paginated:
            sources = models.InformationSource.objects.filter(
                content_type=release_content_type,
                object_id=row.release.id
            )
            if sources:
                source_url = sources[0].source
            else:
                source_url = ''

            row.release.source_url = source_url

        context['data'] = data_paginated

        return context

class SAMembersInterestsSource(TemplateView):
    template_name = "interests_register/source.html"

    def get_context_data(self, **kwargs):
        context = {}

        context['categories'] = Category.objects.filter(
            slug__in = ['sponsorships',
                        'gifts-and-hospitality',
                        'benefits',
                        'pensions'])
        context['releases'] = Release.objects.all()

        context['source'] = ''
        context['match'] = 'absolute'
        context['category'] = context['categories'][0].slug
        context['release'] = 'all'

        for key in ('source', 'match', 'category', 'release'):
            if key in self.request.GET:
                context[key] = self.request.GET[key]

        if not context['match'] in ('absolute', 'contains'):
            context['match'] = 'absolute'

        #if a source is specified perform the search
        if context['source']!='':
            if context['match']=='absolute':
                entries = Entry.objects.filter(line_items__key='Source',
                    line_items__value=context['source'],
                    category__slug=context['category'])
            else:
                entries = Entry.objects.filter(line_items__key='Source',
                    line_items__value__contains=context['source'],
                    category__slug=context['category'])

            if context['release']!='all':
                entries = entries.filter(release__slug=context['release'])

            paginator = Paginator(entries, 25)
            page = self.request.GET.get('page')

            try:
                entries_paginated = paginator.page(page)
            except PageNotAnInteger:
                entries_paginated = paginator.page(1)
            except EmptyPage:
                entries_paginated = paginator.page(paginator.num_pages)

            #tabulate the data
            headers = ['Year', 'Person', 'Type']
            headers_index = {'Year': 0, 'Person': 1, 'Type': 2}
            data = []
            for entry in entries_paginated:
                row = ['']*len(headers)
                row[0] = entry.release.date.year
                row[1] = entry.person
                row[2] = entry.category.name

                for entrylineitem in entry.line_items.all():
                    if entrylineitem.key not in headers:
                        headers_index[entrylineitem.key] = len(headers)
                        headers.append(entrylineitem.key)
                        row.append('')

                    row[headers_index[entrylineitem.key]] = entrylineitem.value

                data.append(row)

            context['data'] = data
            context['headers'] = headers
            context['paginator'] = entries_paginated

        else:
            context['data'] = None

        return context

class SAMpAttendanceView(TemplateView):
    template_name = "south_africa/mp_attendance.html"

    def calculate_abs_percenatge(self, num, total):
        """
        Return the truncated value of num, as a percentage of total.
        """
        return int("{:.0f}".format(num / total * 100))

    def download_attendance_data(self):
        attendance_url = next_url = 'https://api.pmg.org.za/committee-meeting-attendance/summary/'

        cache = caches['pmg_api']
        results = cache.get(attendance_url)

        if results is None:
            results = []
            while next_url:
                resp = requests.get(next_url, timeout=API_REQUESTS_TIMEOUT)
                data = json.loads(resp.text)
                results.extend(data.get('results'))

                next_url = data.get('next')

            cache.set(attendance_url, results)

        # Results are returned from the API most recent first, which
        # is convenient for us.
        return results

    def get_context_data(self, **kwargs):
        data = self.download_attendance_data()

        #  A:   Absent
        #  AP:  Absent with Apologies
        #  DE:  Departed Early
        #  L:   Arrived Late
        #  LDE: Arrived Late and Departed Early
        #  P:   Present

        present_codes = ['P', 'L', 'LDE', 'DE']
        arrive_late_codes = ['L', 'LDE']
        depart_early_codes = ['DE', 'LDE']

        context = {}
        context['year'] = str(
            dateutil.parser.parse(data[0]['end_date']).year)
        context['party'] = ''

        for key in ('year', 'party'):
            if key in self.request.GET:
                context[key] = self.request.GET[key]

        context['attendance_data'] = []
        context['years'] = []
        context['download_url'] = 'http://api.pmg.org.za/committee-meeting-attendance/data.xlsx'

        for annual_attendance in data:
            year = str(dateutil.parser.parse(annual_attendance['end_date']).year)
            context['years'].append(year)

            if year == context['year']:
                parties = set(ma['member']['party_name'] for
                    ma in annual_attendance['attendance_summary'])
                parties.discard(None)
                context['parties'] = sorted(parties)

                attendance_summary = annual_attendance['attendance_summary']
                if context['party']:
                    attendance_summary = [mbr_att for mbr_att in attendance_summary if
                        mbr_att['member']['party_name'] == context['party']]

                aggregate_total = aggregate_present = 0

                for summary in attendance_summary:
                    total = sum(v for v in summary['attendance'].itervalues())

                    present = sum(
                        v for k, v in summary['attendance'].iteritems()
                        if k in present_codes)

                    arrive_late = sum(
                        v for k, v in summary['attendance'].iteritems()
                        if k in arrive_late_codes)

                    depart_early = sum(
                        v for k, v in summary['attendance'].iteritems()
                        if k in depart_early_codes)

                    aggregate_total += total
                    aggregate_present += present

                    present_perc = self.calculate_abs_percenatge(present, total)
                    arrive_late_perc = self.calculate_abs_percenatge(arrive_late, total)
                    depart_early_perc = self.calculate_abs_percenatge(depart_early, total)

                    context['attendance_data'].append({
                        "name": summary['member']['name'],
                        "pa_url": summary['member']['pa_url'],
                        "party_name": summary['member']['party_name'],
                        "present": present_perc,
                        "absent": 100 - present_perc,
                        "arrive_late": arrive_late_perc,
                        "depart_early": depart_early_perc,
                        "total": total,
                    })

                if aggregate_total == 0:
                    # To avoid a division by zero if there's no data...
                    aggregate_attendance = -1
                else:
                    aggregate_attendance = self.calculate_abs_percenatge(aggregate_present, aggregate_total)
                context['aggregate_attendance'] = aggregate_attendance

        return context


class SAInfoBlogView(CommentArchiveMixin, InfoBlogView):
    pass
