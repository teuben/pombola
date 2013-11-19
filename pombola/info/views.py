from django.views.generic import DetailView, ListView
from django.contrib.syndication.views import Feed
from django.core.urlresolvers import reverse_lazy

from models import InfoPage, Category


class BlogMixin(object):

    def get_context_data(self, **kwargs):
        context = super(BlogMixin, self).get_context_data(**kwargs)

        context['all_categories'] = Category.objects.all().order_by('name')

        context['recent_posts'] = InfoPage.objects \
            .filter(kind=InfoPage.KIND_BLOG) \
            .order_by("-publication_date") \
            [:5]

        return context



class InfoBlogList(BlogMixin, ListView):
    """Show list of blog posts"""
    model = InfoPage
    queryset = InfoPage.objects.filter(kind=InfoPage.KIND_BLOG).order_by("-publication_date")
    paginate_by = 10
    template_name = 'info/blog_list.html'


class InfoBlogCategory(InfoBlogList):

    def get_queryset(self):
        category_slug = self.kwargs['slug']
        queryset = super(InfoBlogCategory, self).get_queryset()
        return queryset.filter(categories__slug=category_slug)


class InfoBlogView(BlogMixin, DetailView):
    """Show the blog post for the given slug"""
    model = InfoPage
    queryset = InfoPage.objects.filter(kind=InfoPage.KIND_BLOG)
    template_name = 'info/blog_post.html'


class InfoBlogFeed(Feed):
    """Create a feed with the latest 10 blog entries in"""
    title = "Recent blog posts"
    link = reverse_lazy('info_blog_list')
    description = "Recent blog posts"

    def items(self):
        return InfoPage.objects.filter(kind=InfoPage.KIND_BLOG).order_by("-publication_date")[:10]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.content


class InfoPageView(DetailView):
    """Show the page for the given slug"""
    model = InfoPage
    queryset = InfoPage.objects.filter(kind=InfoPage.KIND_PAGE)


