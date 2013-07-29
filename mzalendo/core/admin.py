from django.contrib import admin
from mzalendo.core import models
from mzalendo.scorecards import models as scorecard_models
from django.contrib.gis import db
from django.core.urlresolvers import reverse
from django.contrib.contenttypes.generic import GenericTabularInline
from django import forms

from ajax_select import make_ajax_form
from ajax_select.admin import AjaxSelectAdmin

from images.admin import ImageAdminInline

def create_admin_link_for(obj, link_text):
    return u'<a href="%s">%s</a>' % ( obj.get_admin_url(), link_text )


class ContactKindAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ["name"]}
    search_fields = [ 'name' ]


class AlternativePersonNameInlineAdmin(admin.TabularInline):
    model = models.AlternativePersonName
    extra = 0


class InformationSourceAdmin(admin.ModelAdmin):
    list_display  = [ 'source', 'show_foreign', 'entered', ]
    list_filter   = [ 'entered', ]
    search_fields = [ 'source', ]

    def show_foreign(self, obj):
        return create_admin_link_for( obj.content_object, str(obj.content_object) )
    show_foreign.allow_tags = True


class InformationSourceInlineAdmin(GenericTabularInline):
    model      = models.InformationSource
    extra      = 0
    can_delete = False
    fields     = [ 'source', 'note', 'entered', ]
    formfield_overrides = {
        db.models.TextField: {'widget': forms.Textarea(attrs={'rows':2, 'cols':40})},
    }


class ContactAdmin(admin.ModelAdmin):
    list_display  = [ 'kind', 'value', 'show_foreign' ]
    search_fields = ['value', ]
    inlines       = [ InformationSourceInlineAdmin, ]
    
    def show_foreign(self, obj):
        return create_admin_link_for( obj.content_object, str(obj.content_object) )
    show_foreign.allow_tags = True
    

class ContactInlineAdmin(GenericTabularInline):
    model      = models.Contact
    extra      = 0
    can_delete = False
    fields     = [ 'kind', 'value', 'source', 'note', ]
    formfield_overrides = {
        db.models.TextField: {'widget': forms.Textarea(attrs={'rows':2, 'cols':20})},
    }


class PositionAdmin(AjaxSelectAdmin):
    list_display  = [ 'id', 'show_person', 'show_organisation', 'show_place', 'show_title', 'start_date', 'end_date' ]
    search_fields = [ 'person__legal_name', 'organisation__name', 'title__name' ]
    list_filter   = [ 'title__name' ]    
    inlines       = [ InformationSourceInlineAdmin, ]
    readonly_fields = ['sorting_start_date','sorting_end_date']

    form = make_ajax_form(
        models.Position,
        {
            'organisation': 'organisation_name',
            'place':        'place_name',
            'person':       'person_name',
            'title':        'title_name',
        }
    )    
    
    def show_person(self, obj):
        return create_admin_link_for( obj.person, obj.person.name )
    show_person.allow_tags = True
    
    def show_organisation(self, obj):
        return create_admin_link_for(obj.organisation, obj.organisation.name)
    show_organisation.allow_tags = True

    def show_place(self, obj):
        return create_admin_link_for(obj.place, obj.place.name)
    show_place.allow_tags = True

    def show_title(self, obj):
        return create_admin_link_for(obj.title, obj.title.name)
    show_title.allow_tags = True


class PositionInlineAdmin(admin.TabularInline):
    model      = models.Position
    extra      = 3    # do not set to zero as the autocomplete does not work in inlines
    can_delete = True
    fields     = [ 'person', 'organisation', 'place', 'title', 'subtitle', 'category', 'start_date', 'end_date' ]

    form = make_ajax_form(
        models.Position,
        {
            'organisation': 'organisation_name',
            'place':        'place_name',
            'person':       'person_name',
            'title':        'title_name',
        }
    )    

class ScorecardInlineAdmin(GenericTabularInline):
    model = scorecard_models.Entry
    fields = ('date', 'score', 'disabled')
    readonly_fields = ('date', 'score')
    extra = 0
    can_delete = False

class PersonAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ["legal_name"]}
    inlines = [AlternativePersonNameInlineAdmin, PositionInlineAdmin, ContactInlineAdmin, InformationSourceInlineAdmin, ImageAdminInline, ScorecardInlineAdmin]
    list_display = ['slug', 'name', 'date_of_birth']
    list_filter   = [ 'can_be_featured', ]
    search_fields = ['legal_name']

class PlaceAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    list_display = ('slug', 'name', 'kind', 'show_organisation')
    list_filter = ('kind',)
    search_fields = ('name', 'organisation__name')
    inlines = (InformationSourceInlineAdmin, ScorecardInlineAdmin)

    def show_organisation(self, obj):
        if obj.organisation:
            return create_admin_link_for(obj.organisation, obj.organisation.name)
        else:
            return '-'
    show_organisation.allow_tags = True


class PlaceInlineAdmin(admin.TabularInline):
    model      = models.Place
    extra      = 0
    can_delete = False
    fields     = [ 'name', 'slug', 'kind' ]


class OrganisationAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    inlines       = [ PlaceInlineAdmin, PositionInlineAdmin, ContactInlineAdmin, InformationSourceInlineAdmin, ]
    list_display  = [ 'slug', 'name', 'kind', ]
    list_filter   = [ 'kind', ]
    search_fields = [ 'name' ]

class OrganisationKindAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    search_fields = [ 'name' ]

class PlaceKindAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    list_display = [ 'slug', 'name' ]
    search_fields = [ 'name' ]

class PositionTitleAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    search_fields = [ 'name' ]


# Add these to the admin
admin.site.register( models.Contact,              ContactAdmin               )
admin.site.register( models.ContactKind,          ContactKindAdmin           )
admin.site.register( models.InformationSource,    InformationSourceAdmin     )
admin.site.register( models.Organisation,         OrganisationAdmin          )
admin.site.register( models.OrganisationKind,     OrganisationKindAdmin      )
admin.site.register( models.Person,               PersonAdmin                )
admin.site.register( models.Place,                PlaceAdmin                 )
admin.site.register( models.PlaceKind,            PlaceKindAdmin             )
admin.site.register( models.Position,             PositionAdmin              )
admin.site.register( models.PositionTitle,        PositionTitleAdmin         )



class LogAdmin(admin.ModelAdmin):
    """Create an admin view of the history/log table"""
    list_display = ('action_time','user','content_type','change_message','is_addition','is_change','is_deletion')
    list_filter = ['action_time','user','content_type']
    ordering = ('-action_time',)
    readonly_fields = [ 'user','content_type','object_id','object_repr','action_flag','change_message']
    date_hierarchy = 'action_time'
    
    #We don't want people changing this historical record:
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        #returning false causes table to not show up in admin page :-(
        #I guess we have to allow changing for now
        return True
    def has_delete_permission(self, request, obj=None):
        return False

# the line below caused issues on one server and not on others. Possibly an
# import order issue?? The uncommented lines don't have the problem
#
# buggy --> admin.site.register( admin.models.LogEntry, LogAdmin )
from django.contrib.admin.models import LogEntry
admin.site.register( LogEntry, LogAdmin )
